import Foundation
#if canImport(UIKit)
import UIKit
#endif

/**
 本機影像儲存。**檔案內容一律加密**（對等 Android `data/store/LocalImageStore.kt`）。

 傷口照片是 PHI。放在 App 沙箱裡看似安全，但沙箱內容會進 iTunes／iCloud 備份，
 而備份可能落在使用者的電腦上。加密之後，備份裡的是密文，而金鑰標記
 `ThisDeviceOnly` 不隨 iCloud Keychain 同步——所以備份檔搬到別台機器也解不開。
 */
final class LocalImageStore {

    private let dir: URL

    init() {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        dir = base.appendingPathComponent("wound_images", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true,
            attributes: [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication])
    }

    private func url(_ name: String) -> URL { return dir.appendingPathComponent(name) }

    /// - Returns: 檔名（存進 `Measurement.imagePath`），失敗回 nil。
    func save(jpeg: Data) -> String? {
        guard let enc = try? PhiCrypto.encryptBytes(jpeg), let enc = enc else { return nil }
        let name = "img_\(UUID().uuidString).enc"
        do {
            try enc.write(to: url(name), options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
            return name
        } catch {
            return nil
        }
    }

    #if canImport(UIKit)
    func save(image: UIImage, quality: CGFloat = 0.9) -> String? {
        guard let d = image.jpegData(compressionQuality: quality) else { return nil }
        return save(jpeg: d)
    }

    /**
     載入完整影像。

     ⚠ **不可降採樣。** `gtPolygon` 的座標空間是原圖尺寸；縮過的圖再配上原座標，
     醫師的修邊會畫在錯的位置上，而畫面看起來完全正常。
     */
    func loadFull(_ name: String) -> UIImage? {
        guard let raw = rawBytes(name) else { return nil }
        return UIImage(data: raw)
    }

    /// 清單縮圖。這裡**可以**降採樣——縮圖不參與座標運算。
    func loadThumbnail(_ name: String, maxPixel: Int = 256) -> UIImage? {
        guard let raw = rawBytes(name) else { return nil }
        let opts: [CFString: Any] = [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceThumbnailMaxPixelSize: maxPixel,
            kCGImageSourceCreateThumbnailWithTransform: true
        ]
        guard let src = CGImageSourceCreateWithData(raw as CFData, nil),
              let cg = CGImageSourceCreateThumbnailAtIndex(src, 0, opts as CFDictionary) else { return nil }
        return UIImage(cgImage: cg)
    }
    #endif

    /// 解密後的原始位元組。柵格 PNG 用這個——走 `UIImage` 會經過色彩管理，
    /// 而我們存在 RGB 通道裡的是**類別碼**，被轉換過就再也還原不回來。
    func rawBytes(_ name: String) -> Data? {
        guard !name.isEmpty, let enc = try? Data(contentsOf: url(name)) else { return nil }
        return PhiCrypto.decryptBytes(enc)
    }

    func saveRaw(_ bytes: Data) -> String? {
        guard let enc = try? PhiCrypto.encryptBytes(bytes), let enc = enc else { return nil }
        let name = "ras_\(UUID().uuidString).enc"
        do {
            try enc.write(to: url(name), options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
            return name
        } catch {
            return nil
        }
    }

    func exists(_ name: String) -> Bool {
        return !name.isEmpty && FileManager.default.fileExists(atPath: url(name).path)
    }

    func delete(_ name: String) {
        guard !name.isEmpty else { return }
        try? FileManager.default.removeItem(at: url(name))
    }

    func totalBytes() -> Int64 {
        guard let items = try? FileManager.default.contentsOfDirectory(
            at: dir, includingPropertiesForKeys: [.fileSizeKey]) else { return 0 }
        return items.reduce(Int64(0)) { acc, u in
            acc + Int64((try? u.resourceValues(forKeys: [.fileSizeKey]).fileSize) as? Int ?? 0)
        }
    }
}
