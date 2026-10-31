import SwiftUI
import UIKit

/// A read-only snapshot of one saved record. No account, inference, quota or sync dependencies.
enum LitePDFReport {
    static let disclaimer = "個人影像紀錄，供就醫時溝通參照。圈選與面積為估算，不提供診斷、感染風險或治療建議，也不以數值變化判定癒合。"

    static func polygons(_ record: LiteRecord, image: UIImage?) -> [[[Int]]]? {
        guard let image, image.imageOrientation == .up, let cg = image.cgImage,
              let data = record.polysJson?.data(using: .utf8), data.count <= 1_000_000,
              let ps = try? JSONDecoder().decode([[[Int]]].self, from: data),
              !ps.isEmpty, ps.count <= 100,
              ps.reduce(0, { $0 + $1.count }) <= 20_000,
              ps.allSatisfy({ p in p.count >= 3 && p.allSatisfy { q in
                  q.count == 2 && (0..<cg.width).contains(q[0]) && (0..<cg.height).contains(q[1])
              } }) else { return nil }
        if let c = record.measurementContext,
           c.imageWidth != cg.width || c.imageHeight != cg.height { return nil }
        return ps
    }

    static func areaLines(_ record: LiteRecord, image: UIImage?) -> [String] {
        guard let c = record.measurementContext, c.isKnownSurface,
              c.orientation == "normalized", c.depthWidth > 0, c.depthHeight > 0,
              polygons(record, image: image) != nil else {
            return ["未提供面積：本筆的尺度、影像或圈選來源無法完整核對；原紀錄保留於 App。"]
        }
        let result = DepthAreaResult(surfaceAreaCm2: record.surfaceCm2,
            projectedAreaCm2: record.projectedCm2, coverage: c.coverage,
            medianDistanceM: c.distanceM, tiltDeg: record.tiltDeg)
        let verdict = liteVerdict(result)
        guard verdict.usable else { return ["未提供面積：" + (verdict.blocker ?? "品質資訊不足。") ] }
        var lines = [String(format: "LiDAR 表面積估算：%.2f cm²", record.surfaceCm2),
                     String(format: "相機方向投影估算：%.2f cm²", record.projectedCm2),
                     "來源：免貼紙 LiDAR 深度估算；不同於貼紙校正面積。",
                     "資料對應檢查不代表實物精度驗證；尚無固定誤差保證。"]
        for warning in c.warnings + verdict.warnings where !lines.contains(warning) { lines.append(warning) }
        if record.quality != "ok", c.warnings.isEmpty, verdict.warnings.isEmpty {
            lines.append("原紀錄有拍攝品質注意事項，請回 App 查看。")
        }
        return lines
    }

    static func render(record: LiteRecord, image: UIImage?) -> Data {
        let page = CGRect(x: 0, y: 0, width: 595.28, height: 841.89)
        let format = UIGraphicsPDFRendererFormat()
        format.documentInfo = [kCGPDFContextTitle as String: "WoundAILite 個人影像紀錄",
                               kCGPDFContextCreator as String: "WoundAILite"]
        return UIGraphicsPDFRenderer(bounds: page, format: format).pdfData { ctx in
            var y: CGFloat = 0
            var number = 0
            func newPage() {
                ctx.beginPage(); number += 1; y = 36
                UIColor.white.setFill(); ctx.cgContext.fill(page)
                ("WoundAILite · 個人紀錄 · 第 \(number) 頁" as NSString).draw(
                    in: CGRect(x: 36, y: 807, width: 523, height: 20),
                    withAttributes: [.font: UIFont.systemFont(ofSize: 10), .foregroundColor: UIColor.darkGray])
            }
            func text(_ value: String, size: CGFloat = 13, bold: Bool = false) {
                // Bound corrupted/legacy free text without hiding normal warnings.
                let safe = value.count > 700 ? String(value.prefix(700)) + "…（完整內容請回 App 查看）" : value
                let style = NSMutableParagraphStyle(); style.lineSpacing = 3
                let attrs: [NSAttributedString.Key: Any] = [
                    .font: bold ? UIFont.boldSystemFont(ofSize: size) : UIFont.systemFont(ofSize: size),
                    .foregroundColor: UIColor.black, .paragraphStyle: style]
                let height = ceil((safe as NSString).boundingRect(with: CGSize(width: 523, height: 700),
                    options: [.usesLineFragmentOrigin, .usesFontLeading], attributes: attrs, context: nil).height) + 4
                if y + height > 780 { newPage() }
                (safe as NSString).draw(in: CGRect(x: 36, y: y, width: 523, height: height), withAttributes: attrs)
                y += height + 5
            }
            newPage()
            text("WoundAILite｜個人影像紀錄", size: 22, bold: true)
            text("拍攝時間：" + record.dateISO + "（原紀錄時間，Z 表示 UTC）", size: 11)
            text((record.woundName ?? "未分組紀錄") + " · " + (record.woundLocation?.label ?? "位置未填寫"))
            if let image, image.size.width > 0, image.size.height > 0 {
                if y + 350 > 780 { newPage() }
                let scale = min(523 / image.size.width, 330 / image.size.height)
                let size = CGSize(width: image.size.width * scale, height: image.size.height * scale)
                let frame = CGRect(x: 36 + (523-size.width)/2, y: y, width: size.width, height: size.height)
                // Render pixels, never embed original JPEG/EXIF or raw depth as PDF attachments.
                let rasterFormat = UIGraphicsImageRendererFormat(); rasterFormat.scale = 1
                rasterFormat.opaque = true; rasterFormat.preferredRange = .standard
                let factor = min(1, 1600 / max(image.size.width, image.size.height))
                let rasterSize = CGSize(width: image.size.width * factor, height: image.size.height * factor)
                let raster = UIGraphicsImageRenderer(size: rasterSize, format: rasterFormat).image { r in
                    UIColor.white.setFill(); r.fill(CGRect(origin: .zero, size: rasterSize))
                    image.draw(in: CGRect(origin: .zero, size: rasterSize))
                }
                raster.draw(in: frame)
                if let ps = polygons(record, image: image), let cg = image.cgImage {
                    ctx.cgContext.saveGState(); ctx.cgContext.clip(to: frame)
                    ctx.cgContext.setStrokeColor(UIColor(red: 0, green: 0.65, blue: 0.8, alpha: 1).cgColor)
                    ctx.cgContext.setLineWidth(1.5)
                    for p in ps {
                        let points = p.map { CGPoint(x: frame.minX + CGFloat($0[0])/CGFloat(cg.width)*frame.width,
                                                     y: frame.minY + CGFloat($0[1])/CGFloat(cg.height)*frame.height) }
                        ctx.cgContext.addLines(between: points); ctx.cgContext.closePath(); ctx.cgContext.strokePath()
                    }
                    ctx.cgContext.restoreGState()
                }
                y += size.height + 12
                text(polygons(record, image: image) == nil
                     ? "照片保留；圈選座標無法核對，未疊加輪廓。"
                     : "藍色輪廓為此筆已保存圈選；不是醫師確認或診斷。", size: 11)
            } else { text("本機照片已不存在或暫時無法讀取；未從雲端補下載。") }
            for line in areaLines(record, image: image) { text(line) }
            text(disclaimer, size: 12)
            text("本報告只含所選單筆紀錄，不含深度／體積、帳號或雲端識別碼。分享由您自行選擇接收對象。", size: 10)
        }
    }
}

/// Each share owns one protected, backup-excluded temporary copy. Never remove source media.
struct LitePDFShare: Identifiable {
    let id = UUID()
    let url: URL
    static var directory: URL {
        FileManager.default.temporaryDirectory.appendingPathComponent("woundailite-pdf-share", isDirectory: true)
    }
    init(data: Data, directory: URL = Self.directory) throws {
        guard data.starts(with: Data("%PDF-".utf8)) else { throw CocoaError(.fileWriteUnknown) }
        try HealthDataFiles.prepareDirectory(directory)
        url = directory.appendingPathComponent("WoundAILite-\(UUID().uuidString).pdf")
        try HealthDataFiles.write(data, to: url)
    }
    func remove() { try? FileManager.default.removeItem(at: url) }
    /// One process launch only; an active share must not be purged by a view redraw.
    static func purgeAfterLaunch(directory: URL = Self.directory) {
        guard let files = try? FileManager.default.contentsOfDirectory(at: directory,
            includingPropertiesForKeys: [.isRegularFileKey, .isSymbolicLinkKey]) else { return }
        for file in files where file.lastPathComponent.hasPrefix("WoundAILite-") && file.pathExtension == "pdf" {
            let v = try? file.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey])
            if v?.isRegularFile == true, v?.isSymbolicLink != true { try? FileManager.default.removeItem(at: file) }
        }
    }
}

struct LitePDFShareSheet: UIViewControllerRepresentable {
    let item: LitePDFShare
    let completed: () -> Void
    func makeUIViewController(context: Context) -> UIActivityViewController {
        let controller = UIActivityViewController(activityItems: [item.url], applicationActivities: nil)
        controller.completionWithItemsHandler = { _, _, _, _ in completed() }
        return controller
    }
    func updateUIViewController(_ uiViewController: UIActivityViewController, context: Context) { }
}
