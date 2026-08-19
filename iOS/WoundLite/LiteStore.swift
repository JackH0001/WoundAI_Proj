import Foundation
import UIKit

/**
 民眾版本地紀錄——輕量 JSON＋加密影像。

 **刻意不用醫療版的 `CaseRepository`／SQLite schema**：那份 schema 是 Android Room v6
 的鏡像，受 `tools/parity_check.py` 管制；民眾版沒有病患／個案／同意書概念，
 硬塞進去只會把兩件事互相綁死。這裡一個 JSON 檔就夠（單人、百筆級）。
 影像沿用 `LocalImageStore`（AES 加密落地，與醫療版同一套實作、不同沙盒）。
 */
struct LiteRecord: Codable, Identifiable {
    var id: String
    var dateISO: String
    /// 主數字：三角化 3D 表面積（cm²）。民眾版的「傷口面積」。
    var surfaceCm2: Double
    var projectedCm2: Double
    var tiltDeg: Double?
    var volumeMl: Double?
    var maxDepthMm: Double?
    /// 拍攝品質摘要（"ok" 或警告短語），列表徽章用。
    var quality: String
    /// LocalImageStore 內的加密檔名。
    var imageName: String
    /// 輪廓來源："manual"（離線手動）／"cloud"（雲端辨識）／"local"（地端模型）。
    var source: String
    /// 輪廓（work 影像座標，[[[Int]]] 的 JSON）。詳情頁重繪與「重新圈選」預載用。
    /// Optional：舊紀錄沒有 → 只能檢視。
    var polysJson: String?
    /// 深度側檔（加密）檔名。重新圈選後要重算面積，靠它把 DepthCapture 讀回來。
    /// Optional：舊紀錄沒有 → 無法重新量測。
    var depthName: String?
}

/// 深度側檔的序列化格式（**Lite 本地專用**，與上傳契約的 png16_mm wire format 無關
/// ——這裡要無損往返 Float32，直接存原始位元組最不會出錯）。
private struct LiteDepthFile: Codable {
    var w: Int; var h: Int
    var fx: Double; var fy: Double; var cx: Double; var cy: Double
    var refW: Double; var refH: Double
    var accuracy: String; var filtered: Bool
    var rgbW: Int; var rgbH: Int
    /// Float32 原始位元組（機器序）的 base64。
    var mapB64: String
}

@MainActor
final class LiteStore: ObservableObject {
    @Published private(set) var records: [LiteRecord] = []
    let images = LocalImageStore()

    private var fileURL: URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory,
                                            in: .userDomainMask)[0]
        try? FileManager.default.createDirectory(at: base, withIntermediateDirectories: true)
        return base.appendingPathComponent("lite_records.json")
    }

    init() { load() }

    func add(_ r: LiteRecord) {
        records.insert(r, at: 0)
        persist()
    }

    func delete(_ r: LiteRecord) {
        records.removeAll { $0.id == r.id }
        images.delete(r.imageName)
        if let d = r.depthName { images.delete(d) }   // 深度側檔一併清，不留孤兒
        persist()
    }

    /// 重新量測後覆寫同 id 的那一筆（詳情頁「重新圈選」用）。
    func update(_ r: LiteRecord) {
        guard let i = records.firstIndex(where: { $0.id == r.id }) else { return }
        records[i] = r
        persist()
    }

    // MARK: 深度側檔

    func saveDepth(_ d: DepthCapture) -> String? {
        let raw = d.map.withUnsafeBufferPointer { Data(buffer: $0) }
        let f = LiteDepthFile(w: d.width, h: d.height, fx: d.fx, fy: d.fy,
                              cx: d.cx, cy: d.cy,
                              refW: d.refWidth, refH: d.refHeight,
                              accuracy: d.accuracy, filtered: d.filtered,
                              rgbW: d.rgbWidth, rgbH: d.rgbHeight,
                              mapB64: raw.base64EncodedString())
        guard let j = try? JSONEncoder().encode(f) else { return nil }
        return images.saveRaw(j)   // AES 加密落地，與影像同一套
    }

    func loadDepth(_ name: String) -> DepthCapture? {
        guard let j = images.rawBytes(name),
              let f = try? JSONDecoder().decode(LiteDepthFile.self, from: j),
              let raw = Data(base64Encoded: f.mapB64) else { return nil }
        let n = raw.count / MemoryLayout<Float>.size
        guard n == f.w * f.h, n > 0 else { return nil }
        var map = [Float](repeating: 0, count: n)
        _ = map.withUnsafeMutableBytes { raw.copyBytes(to: $0) }
        return DepthCapture(map: map, width: f.w, height: f.h,
                            fx: f.fx, fy: f.fy, cx: f.cx, cy: f.cy,
                            refWidth: f.refW, refHeight: f.refH,
                            accuracy: f.accuracy, filtered: f.filtered,
                            rgbWidth: f.rgbW, rgbHeight: f.rgbH)
    }

    private func load() {
        guard let d = try? Data(contentsOf: fileURL),
              let rs = try? JSONDecoder().decode([LiteRecord].self, from: d) else { return }
        records = rs
    }

    private func persist() {
        guard let d = try? JSONEncoder().encode(records) else { return }
        // 原子寫入：中途被殺不會留半個 JSON（下次 load 解不開＝整份紀錄消失）。
        try? d.write(to: fileURL, options: .atomic)
    }
}
