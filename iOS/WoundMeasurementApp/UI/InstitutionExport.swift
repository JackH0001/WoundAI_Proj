import Foundation
import UIKit
import CryptoKit

/// Medical-only, explicit institutional build. Exports are review copies, not cloud receipts.
enum InstitutionExport {
    static let institution = "MMHPS20261007"
    static var enabled: Bool {
        #if WOUND_INSTITUTION
        return true
        #else
        return false
        #endif
    }
    enum Failure: Error { case invalidGeometry, incompleteRaster, imageEncoding, inconsistentExistingExport }

    static func validate(image: UIImage, raster: EditRaster) throws {
        guard let cg = image.cgImage, image.imageOrientation == .up,
              raster.canvasW == cg.width, raster.canvasH == cg.height,
              (1...4096).contains(cg.width), (1...4096).contains(cg.height),
              (1...4096).contains(raster.mw), (1...4096).contains(raster.mh),
              raster.mScale.isFinite, raster.mScale > 0,
              raster.rx0.isFinite, raster.ry0.isFinite, raster.rx0 >= 0, raster.ry0 >= 0,
              raster.imageRect.maxX <= Double(cg.width) + 0.01,
              raster.imageRect.maxY <= Double(cg.height) + 0.01 else { throw Failure.invalidGeometry }
        guard raster.mask.count == raster.mw * raster.mh,
              raster.tissue.count == raster.mask.count,
              raster.mask.allSatisfy({ $0 <= 1 }),
              zip(raster.mask, raster.tissue).allSatisfy({ $0.0 == 0 || (1...5).contains($0.1) })
        else { throw Failure.incompleteRaster }
    }

    static func overlay(image: UIImage, raster: EditRaster, polygons: [[[Int]]],
                        marker: [[Int]]?, caption: String) throws -> UIImage {
        try validate(image: image, raster: raster)
        let w = CGFloat(raster.canvasW), h = CGFloat(raster.canvasH)
        let font = UIFont.systemFont(ofSize: max(12, w / 42))
        let attributes: [NSAttributedString.Key: Any] = [.font: font, .foregroundColor: UIColor.white]
        let textHeight = (caption as NSString).boundingRect(with: CGSize(width: w - 24, height: 10000),
            options: [.usesLineFragmentOrigin, .usesFontLeading], attributes: attributes, context: nil).height
        let format = UIGraphicsImageRendererFormat(); format.scale = 1; format.opaque = true
        let colours: [[UInt8]] = [[0,0,0,0],[0,153,102,115],[204,128,0,115],[51,26,153,115],
                                 [191,102,128,115],[102,102,102,115]]
        var rgba = [UInt8](repeating: 0, count: raster.mask.count * 4)
        for i in raster.mask.indices where raster.mask[i] != 0 {
            let c = colours[Int(raster.tissue[i])]
            for j in 0..<4 { rgba[i * 4 + j] = c[j] }
        }
        guard let provider = CGDataProvider(data: Data(rgba) as CFData),
              let layer = CGImage(width: raster.mw, height: raster.mh, bitsPerComponent: 8,
                bitsPerPixel: 32, bytesPerRow: raster.mw * 4, space: CGColorSpaceCreateDeviceRGB(),
                bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.last.rawValue), provider: provider,
                decode: nil, shouldInterpolate: false, intent: .defaultIntent) else { throw Failure.imageEncoding }
        return UIGraphicsImageRenderer(size: CGSize(width: w, height: h + ceil(textHeight) + 24), format: format).image { context in
            UIColor.black.setFill(); context.fill(CGRect(x: 0, y: 0, width: w, height: h + textHeight + 24))
            image.draw(in: CGRect(x: 0, y: 0, width: w, height: h))
            context.cgContext.saveGState()
            context.cgContext.clip(to: CGRect(x: 0, y: 0, width: w, height: h))
            UIImage(cgImage: layer).draw(in: raster.imageRect)
            func draw(_ points: [[Int]], colour: UIColor) {
                guard points.count >= 3, points.allSatisfy({ $0.count == 2 }) else { return }
                let path = UIBezierPath(); path.move(to: CGPoint(x: points[0][0], y: points[0][1]))
                for p in points.dropFirst() { path.addLine(to: CGPoint(x: p[0], y: p[1])) }
                path.close(); path.lineWidth = max(2, w / 350); colour.setStroke(); path.stroke()
            }
            for p in polygons { draw(p, colour: .cyan) }
            if let marker { draw(marker, colour: .green) }
            context.cgContext.restoreGState()
            (caption as NSString).draw(in: CGRect(x: 12, y: h + 12, width: w - 24, height: textHeight + 1), withAttributes: attributes)
        }
    }

    /// Revision hash avoids duplicate output on an identical retry. No patient names or IDs in file names.
    static func write(image: UIImage, raster: EditRaster, polygons: [[[Int]]], marker: [[Int]]?,
                      measurementID: Int64, doctorVerified: Bool, exudate: Int?, directory: URL) throws -> URL {
        try validate(image: image, raster: raster)
        let fractions = raster.tissueFrac()
        let push = WoundPipeline.push(cm2: raster.areaCm2, frac: fractions, exudate: exudate)
        let caption = "\(institution) · 機構驗證副本\n面積 \(raster.areaCm2.map { String(format: "%.2f", $0) } ?? "未校正") cm² · PUSH \(push.full.map(String.init) ?? "未完整")（部分分數 \(push.partial.map(String.init) ?? "—")）\n醫師確認：\(doctorVerified ? "是" : "否") · 組織人工修改：\(raster.tissueEdited ? "是" : "否")\n肉芽／腐肉／壞死／上皮／其他：" + ["granulation","slough","necrosis","epithelial","other"].map { String(format: "%.1f%%", (fractions[$0] ?? 0) * 100) }.joined(separator: " / ") + "\n限機構驗證；不代表已上傳或已核准研究用途。"
        guard measurementID > 0, let original = image.pngData(),
              let combined = try overlay(image: image, raster: raster, polygons: polygons, marker: marker, caption: caption).pngData(),
              let encoded = EditRasterCodec.encode(raster) else { throw Failure.imageEncoding }
        var files: [String: Data] = ["image.png": original, "overlay.png": combined,
                                   "mask-tissue.png": encoded.0, "raster.json": try JSONSerialization.data(withJSONObject: JSONSerialization.jsonObject(with: Data(encoded.1.utf8)), options: [.sortedKeys])]
        let metadata: [String: Any] = ["schema": "woundai.institution-export/1", "institution": institution,
            "measurement_id": measurementID, "doctor_verified": doctorVerified,
            "tissue_edited": raster.tissueEdited, "tissue_fractions": fractions,
            "area_cm2": raster.areaCm2 as Any? ?? NSNull(), "exudate": exudate as Any? ?? NSNull(),
            "push_full": push.full as Any? ?? NSNull(), "push_partial": push.partial as Any? ?? NSNull(),
            "polygons": polygons, "marker_quad": marker as Any? ?? NSNull(),
            "cloud_uploaded": false, "depth_included": false]
        files["measurement.json"] = try JSONSerialization.data(withJSONObject: metadata, options: [.sortedKeys])
        let hashes = files.mapValues { SHA256.hash(data: $0).map { String(format: "%02x", $0) }.joined() }
        let manifest = try JSONSerialization.data(withJSONObject: hashes, options: [.sortedKeys])
        files["sha256.json"] = manifest
        let revision = SHA256.hash(data: manifest).map { String(format: "%02x", $0) }.joined()
        try HealthDataFiles.prepareDirectory(directory)
        let destination = directory.appendingPathComponent("measurement-\(measurementID)-\(revision)", isDirectory: true)
        if FileManager.default.fileExists(atPath: destination.path) {
            for (name, bytes) in files where (try? Data(contentsOf: destination.appendingPathComponent(name))) != bytes {
                _ = name; throw Failure.inconsistentExistingExport
            }
            return destination
        }
        let temporary = directory.appendingPathComponent(".export-\(UUID().uuidString)", isDirectory: true)
        try HealthDataFiles.prepareDirectory(temporary)
        defer { try? FileManager.default.removeItem(at: temporary) }
        for (name, data) in files { try HealthDataFiles.write(data, to: temporary.appendingPathComponent(name)) }
        try FileManager.default.moveItem(at: temporary, to: destination)
        return destination
    }
}
