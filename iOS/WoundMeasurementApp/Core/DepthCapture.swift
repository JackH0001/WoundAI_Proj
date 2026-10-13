import Foundation
import AVFoundation
import CoreGraphics

/**
 WoundAI3D 深度資料擷取與落地（LiDAR RGB-D）。

 ## 為什麼現在就收

 3D 重建研究需要的是「拍攝當下」的深度圖＋相機內參——事後補不回來。影像本身 90 天
 會依保存政策清除，但深度圖與內參仍可與影像及個案連結，
 應視為敏感資料並與影像同等加密保存。現在每一筆臨床收案順手收下，WoundAI3D 開案時就有資料集。

 ## 設計邊界（刻意的）

 1. **不動 `measurements` schema**。DB v6 與 Android Room v6 鏡像；深度是 iOS 獨有能力，
    加欄位會讓兩端 schema 分岔。改用 **sidecar 註冊表**：`depth_index.json` 把
    `Measurement.imagePath` 映射到兩個加密檔（深度圖＋meta）。索引裡只有檔名，無 PHI。
 2. **`depth_source` 誠實三值**（隨標註上傳，後端欄位已存在）：
    `none`＝沒拍深度；`lidar_local`＝拍了、加密存於本機、尚未上傳；
    `lidar`＝已隨標註上傳（後端深度端點上線後才用）。
    「沒拍」與「拍了但沒存/沒傳」必須分得出來，否則日後分析資料集會把缺席當成不支援。
 3. 深度圖以 **Float32（公尺）** 原樣保存，不壓縮不量化——量化損失對曲率計算是不可逆的。
    一張 320×240 f32 ≈ 300KB，加密後同量級，儲存壓力可忽略。
 */
struct DepthCapture {
    /// Float32 深度（公尺），row-major，w×h。LiDAR 為絕對深度。
    var map: [Float]
    var width: Int
    var height: Int
    /// 內參（相對 `refWidth × refHeight` 的像素座標）。3D 反投影必需：
    /// X=(u-cx)·Z/fx。少了它深度圖只是灰階畫，建不了模。
    var fx: Double
    var fy: Double
    var cx: Double
    var cy: Double
    var refWidth: Double
    var refHeight: Double
    /// `AVDepthData.depthDataAccuracy`：absolute（LiDAR）／relative（雙鏡頭視差）。
    var accuracy: String
    /// 是否經過 Apple 的時域濾波（filtered 平滑但會抹掉小凹陷；照實記錄供研究端取捨）。
    var filtered: Bool
    /// 對應的 RGB 影像尺寸（上傳後端前的 work 影像座標空間）。
    var rgbWidth: Int = 0
    var rgbHeight: Int = 0
    /// EXIF transform applied to both map and calibration before RGB normalization.
    /// nil means legacy/unknown, not proven upright.
    var sourceExifOrientation: Int? = nil

    /// Fixed at the RGB-D photo callback, not when a sidecar is serialized.
    /// This is a host wall-clock observation, not a sensor exposure timestamp.
    struct CaptureProvenance {
        let id: UUID
        let observedAt: Date
        init(id: UUID = UUID(), observedAt: Date) {
            self.id = id
            self.observedAt = observedAt
        }
        var metadata: [String: Any] {
            let formatter = ISO8601DateFormatter()
            formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            return ["capture_id": id.uuidString.lowercased(),
                    "captured_at": formatter.string(from: observedAt),
                    "capture_time_source": "photo_callback_wall_clock"]
        }
    }
    var captureProvenance: CaptureProvenance? = nil

    /// 摘要統計（進 meta，供不解檔快篩：例如「深度覆蓋率過低的樣本先排除」）。
    func coverage() -> (valid: Double, minM: Double, maxM: Double) {
        var n = 0
        var mn = Double.infinity, mx = 0.0
        for v in map where v.isFinite && v > 0 {
            n += 1
            let d = Double(v)
            if d < mn { mn = d }
            if d > mx { mx = d }
        }
        let tot = max(1, map.count)
        return (Double(n) / Double(tot), n > 0 ? mn : 0, mx)
    }

    var metaJson: [String: Any] {
        let c = coverage()
        var meta: [String: Any] = [
            "version": 3,
            "format": "f32_le_meters",
            "width": width, "height": height,
            "intrinsics": ["fx": fx, "fy": fy, "cx": cx, "cy": cy,
                           "ref_w": refWidth, "ref_h": refHeight],
            "accuracy": accuracy,
            "filtered": filtered,
            "rgb_w": rgbWidth, "rgb_h": rgbHeight,
            "coverage": c.valid, "min_m": c.minM, "max_m": c.maxM,
            "device": BackendClient.deviceModelString(),
            // Transformation provenance is not a registration/accuracy certificate.
            "registration": "not_verified",
            "orientation_status": "unknown"
        ]
        meta["capture_time_source"] = "unknown"
        captureProvenance?.metadata.forEach { meta[$0.key] = $0.value }
        if let exif = sourceExifOrientation, (1...8).contains(exif) {
            meta["source_exif_orientation"] = exif
            meta["normalized_exif_orientation"] = 1
            meta["orientation_status"] = "normalized"
        }
        return meta
    }

    /// 從 `AVDepthData` 轉出。一律轉成 DepthFloat32（LiDAR 原生常是 f16 視差／深度）。
    static func from(_ depthData: AVDepthData) -> DepthCapture? {
        let d = depthData.converting(toDepthDataType: kCVPixelFormatType_DepthFloat32)
        let buf = d.depthDataMap
        CVPixelBufferLockBaseAddress(buf, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(buf, .readOnly) }
        let w = CVPixelBufferGetWidth(buf), h = CVPixelBufferGetHeight(buf)
        guard w > 0, h > 0, let base = CVPixelBufferGetBaseAddress(buf) else { return nil }
        let rowBytes = CVPixelBufferGetBytesPerRow(buf)
        var map = [Float](repeating: 0, count: w * h)
        for y in 0..<h {
            let row = base.advanced(by: y * rowBytes).assumingMemoryBound(to: Float.self)
            for x in 0..<w { map[y * w + x] = row[x] }
        }
        var out = DepthCapture(map: map, width: w, height: h,
                               fx: 0, fy: 0, cx: 0, cy: 0, refWidth: 0, refHeight: 0,
                               accuracy: d.depthDataAccuracy == .absolute ? "absolute" : "relative",
                               filtered: d.isDepthDataFiltered)
        if let cal = d.cameraCalibrationData {
            let m = cal.intrinsicMatrix   // 行主序 simd_float3x3：columns.0.x=fx …
            out.fx = Double(m.columns.0.x)
            out.fy = Double(m.columns.1.y)
            out.cx = Double(m.columns.2.x)
            out.cy = Double(m.columns.2.y)
            out.refWidth = Double(cal.intrinsicMatrixReferenceDimensions.width)
            out.refHeight = Double(cal.intrinsicMatrixReferenceDimensions.height)
        }
        return out
    }
}

/**
 深度 sidecar 落地與索引。深度圖與 meta 都走 `LocalImageStore` 的 AES-GCM 加密；
 索引檔只存檔名對映（無 PHI），毀損時最壞情況是深度變成孤兒檔——量測本體不受影響。
 */
enum DepthStore {

    private static let lock = NSRecursiveLock()
    private static func loadIndex(store: LocalImageStore) throws -> [String: [String: String]] {
        guard FileManager.default.fileExists(atPath: store.depthIndexURL.path) else { return [:] }
        let data = try Data(contentsOf: store.depthIndexURL)
        guard let index = try JSONSerialization.jsonObject(with: data) as? [String: [String: String]] else {
            throw CocoaError(.fileReadCorruptFile)
        }
        return index
    }
    private static func saveIndex(_ idx: [String: [String: String]], store: LocalImageStore) -> Bool {
        do {
            let data = try JSONSerialization.data(withJSONObject: idx)
            try HealthDataFiles.write(data, to: store.depthIndexURL)
            return true
        } catch { return false }
    }

    /// 綁定深度到已落地的量測影像。**先寫檔再寫索引**——反過來的話索引會指向不存在的檔。
    @discardableResult
    static func attach(imagePath: String, capture: DepthCapture, store: LocalImageStore) -> Bool {
        lock.lock(); defer { lock.unlock() }
        guard !imagePath.isEmpty, var idx = try? loadIndex(store: store) else { return false }
        let raw = capture.map.withUnsafeBufferPointer { Data(buffer: $0) }
        guard let metaData = try? JSONSerialization.data(withJSONObject: capture.metaJson),
              let depthName = store.saveRaw(raw) else { return false }
        guard let metaName = store.saveRaw(metaData) else { store.delete(depthName); return false }
        idx[imagePath] = ["depth": depthName, "meta": metaName]
        guard saveIndex(idx, store: store) else {
            store.delete(depthName); store.delete(metaName); return false
        }
        return true
    }

    /// 有沒有深度（供 `depth_source` 真值與畫面標示）。
    static func lookup(imagePath: String, store: LocalImageStore) -> [String: String]? {
        guard !imagePath.isEmpty else { return nil }
        lock.lock(); defer { lock.unlock() }
        return (try? loadIndex(store: store))?[imagePath]
    }

    /// 讀回 sidecar 供補傳（`/api/v1/depth`）。回 (f32 原始位元組, 本機 metaJson)。
    static func load(imagePath: String, store: LocalImageStore) -> (raw: Data, meta: [String: Any])? {
        guard let e = lookup(imagePath: imagePath, store: store),
              let dn = e["depth"], let mn = e["meta"],
              let raw = store.rawBytes(dn),
              let md = store.rawBytes(mn),
              let meta = (try? JSONSerialization.jsonObject(with: md)) as? [String: Any]
        else { return nil }
        return (raw, meta)
    }

    /// 補傳成功後記旗標（避免重複補傳的 UI 噪音；後端本就冪等，這只是體驗）。
    @discardableResult
    static func markUploaded(imagePath: String, store: LocalImageStore,
                             receipt: BackendClient.DepthUploadReceipt? = nil) -> Bool {
        lock.lock(); defer { lock.unlock() }
        guard var idx = try? loadIndex(store: store) else { return false }
        guard var e = idx[imagePath] else { return false }
        e["uploaded"] = "1"
        if let receipt {
            e["annotation_receipt_id"] = receipt.annotationRevisionID
            e["depth_receipt_id"] = receipt.receiptID
            e["payload_sha256"] = receipt.payloadSHA256
            e["meta_sha256"] = receipt.metadataSHA256
        }
        idx[imagePath] = e
        return saveIndex(idx, store: store)
    }

    /// 影像被 90 天清除時，深度 sidecar 一併清（同一保存政策）。
    static func purge(imagePath: String, store: LocalImageStore) {
        lock.lock(); defer { lock.unlock() }
        guard var idx = try? loadIndex(store: store), let e = idx[imagePath] else { return }
        idx.removeValue(forKey: imagePath)
        // Preserve readable sidecars if the registry cannot be updated.
        guard saveIndex(idx, store: store) else { return }
        if let p = e["depth"] { store.delete(p) }
        if let p = e["meta"] { store.delete(p) }
    }
}
