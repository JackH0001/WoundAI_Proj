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
        if let binding = record.cloudSync?.binding,
           binding.imageW != cg.width || binding.imageH != cg.height { return nil }
        return ps
    }

    static func areaSeries(_ record: LiteRecord, polygons: [[[Int]]]?) -> LiteAreaSeries? {
        guard polygons != nil else { return nil }
        if record.areaSeries == .legacy {
            guard record.surfaceCm2 > 0, record.projectedCm2 > 0,
                  record.depthName?.isEmpty == false || record.cloudSync?.binding != nil else { return nil }
        }
        return record.areaSeries
    }

    static func areaLines(_ record: LiteRecord, image: UIImage?, locale: Locale = LiteReportCopy.originalLocale) -> [String] {
        areaLines(record, polygons: polygons(record, image: image), locale: locale)
    }

    static func areaLines(_ record: LiteRecord, polygons: [[[Int]]]?, locale: Locale = LiteReportCopy.originalLocale) -> [String] {
        let copy = LiteReportCopy(locale: locale)
        let area = record.savedArea
        if case .unavailable(let reason) = area { return [copy.format("未提供面積：%@", copy.warning(reason))] }
        guard let series = areaSeries(record, polygons: polygons) else {
            return [copy.text("未提供面積：本筆的尺度、影像或圈選資料無法核對。")]
        }
        if series == .legacy {
            return [copy.format("舊版 LiDAR 表面積：%@ cm²", copy.decimal(record.surfaceCm2)),
                    copy.format("舊版相機方向投影：%@ cm²", copy.decimal(record.projectedCm2)),
                    copy.text("沿用拍攝時保存的數值，舊版拍攝品質資料不完整。")]
        }
        guard case .current(let verdict) = area, let c = record.measurementContext else {
            return [copy.text("未提供面積：保存的面積來源無法核對。")]
        }
        var lines = [copy.format("LiDAR 表面積估算：%@ cm²", copy.decimal(record.surfaceCm2)),
                     copy.format("相機方向投影估算：%@ cm²", copy.decimal(record.projectedCm2)),
                     copy.text("來源：免貼紙 LiDAR 深度估算；不同於貼紙校正面積。"),
                     copy.text("資料對應檢查不代表實物精度驗證；尚無固定誤差保證。")]
        for warning in c.warnings + verdict.warnings {
            let translated = copy.warning(warning)
            if !lines.contains(translated) { lines.append(translated) }
        }
        if record.quality != "ok", c.warnings.isEmpty, verdict.warnings.isEmpty {
            lines.append(copy.text("原紀錄有拍攝品質注意事項，請回 App 查看。"))
        }
        return lines
    }

    /// Draw the saved wound region in the photo's pixel coordinate system.
    static func drawMask(_ polygons: [[[Int]]], pixels: CGImage, in rect: CGRect, context: CGContext) {
        context.saveGState()
        defer { context.restoreGState() }
        context.clip(to: rect)
        context.setLineJoin(.round)
        let path = CGMutablePath()
        for polygon in polygons {
            let points = polygon.map {
                CGPoint(x: rect.minX + CGFloat($0[0]) / CGFloat(pixels.width) * rect.width,
                        y: rect.minY + CGFloat($0[1]) / CGFloat(pixels.height) * rect.height)
            }
            path.addLines(between: points)
            path.closeSubpath()
        }
        let teal = UIColor(red: 0, green: 0.58, blue: 0.66, alpha: 1)
        // An unfilled outline keeps the original tissue colour visible. A
        // narrow white edge separates the line from both dark and teal pixels.
        context.addPath(path)
        context.setStrokeColor(UIColor.white.withAlphaComponent(0.9).cgColor)
        context.setLineWidth(1.2)
        context.strokePath()
        context.addPath(path)
        context.setStrokeColor(teal.cgColor)
        context.setLineWidth(0.65)
        context.strokePath()
    }

    /// Render only pixels into the PDF, at a resolution independent of the printed frame.
    static func reportPhoto(_ image: UIImage) -> UIImage {
        let width = image.size.width * image.scale
        let height = image.size.height * image.scale
        let factor = min(1, 1_600 / max(width, height))
        let size = CGSize(width: max(1, (width * factor).rounded()), height: max(1, (height * factor).rounded()))
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        format.opaque = true
        format.preferredRange = .standard
        return UIGraphicsImageRenderer(size: size, format: format).image { context in
            UIColor.white.setFill()
            context.fill(CGRect(origin: .zero, size: size))
            image.draw(in: CGRect(origin: .zero, size: size))
        }
    }

    static func render(record: LiteRecord, image: UIImage?, locale: Locale = LiteReportCopy.originalLocale) -> Data {
        let copy = LiteReportCopy(locale: locale)
        let page = CGRect(x: 0, y: 0, width: 595.28, height: 841.89)
        let format = UIGraphicsPDFRendererFormat()
        format.documentInfo = [kCGPDFContextTitle as String: copy.text("WoundAILite 個人影像紀錄"),
                               kCGPDFContextCreator as String: "WoundAILite"]
        return UIGraphicsPDFRenderer(bounds: page, format: format).pdfData { ctx in
            var y: CGFloat = 0
            var number = 0
            func newPage() {
                ctx.beginPage(); number += 1; y = 36
                UIColor.white.setFill(); ctx.cgContext.fill(page)
                (copy.format("WoundAILite · 個人紀錄 · 第 %@ 頁", String(number)) as NSString).draw(
                    in: CGRect(x: 36, y: 807, width: 523, height: 20),
                    withAttributes: [.font: UIFont.systemFont(ofSize: 10), .foregroundColor: UIColor.darkGray])
            }
            func text(_ value: String, size: CGFloat = 13, bold: Bool = false) {
                // Bound corrupted/legacy free text without hiding normal warnings.
                let safe = value.count > 700 ? String(value.prefix(700)) + copy.text("…（完整內容請回 App 查看）") : value
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
            text(copy.text("WoundAILite｜個人影像紀錄"), size: 22, bold: true)
            text(copy.format("拍攝時間：%@（原紀錄時間，Z 表示 UTC）", record.dateISO), size: 11)
            text((record.woundName ?? copy.text("未分組紀錄")) + " · " + copy.location(record.woundLocation))
            if let image, image.size.width > 0, image.size.height > 0 {
                if y + 350 > 780 { newPage() }
                let scale = min(523 / image.size.width, 330 / image.size.height)
                let size = CGSize(width: image.size.width * scale, height: image.size.height * scale)
                let frame = CGRect(x: 36 + (523-size.width)/2, y: y, width: size.width, height: size.height)
                reportPhoto(image).draw(in: frame)
                if let ps = polygons(record, image: image), let cg = image.cgImage {
                    drawMask(ps, pixels: cg, in: frame, context: ctx.cgContext)
                }
                y += size.height + 12
                text(polygons(record, image: image) == nil
                     ? copy.text("照片保留；圈選座標無法核對，未疊加輪廓。")
                     : copy.text("青色細線標示本筆保存的傷口輪廓。"), size: 11)
            } else { text(copy.text("本機照片已不存在或暫時無法讀取；未從雲端補下載。")) }
            text(copy.format("原始圈選：%@", copy.text(record.contourOrigin?.label ?? "圈選版本未確認")))
            if let notice = record.contourUncertaintyNotice { text(copy.warning(notice), size: 11) }
            for line in areaLines(record, image: image, locale: locale) { text(line) }
            text(copy.text(disclaimer), size: 12)
            text(copy.text("本報告只含所選單筆紀錄，不含深度／體積、帳號或雲端識別碼。分享由您自行選擇接收對象。"), size: 10)
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
    init(directory: URL, render: (URL) throws -> Void) throws {
        try HealthDataFiles.prepareDirectory(directory)
        url = directory.appendingPathComponent("WoundAILite-timeline-" + id.uuidString + ".pdf")
        do {
            try HealthDataFiles.write(Data(), to:url)
            try render(url)
            try HealthDataFiles.excludeExistingFile(url)
            let file = try FileHandle(forReadingFrom:url)
            defer { try? file.close() }
            guard try file.read(upToCount:5) == Data("%PDF-".utf8) else { throw CocoaError(.fileWriteUnknown) }
        } catch {
            try? FileManager.default.removeItem(at:url)
            throw error
        }
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
