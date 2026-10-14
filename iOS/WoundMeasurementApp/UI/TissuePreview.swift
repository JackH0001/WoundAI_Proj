import SwiftUI
import UIKit

/// Read-only rendering; never replaces the stored clinician raster or measurement.
enum TissuePreviewRenderer {
    static func render(image: UIImage, raster: EditRaster) -> UIImage? {
        guard (try? InstitutionExport.validate(image: image, raster: raster)) != nil else { return nil }
        var bytes = [UInt8](repeating: 0, count: raster.mask.count * 4)
        for i in raster.mask.indices where raster.mask[i] != 0 {
            let c = EditPalette.rgb[Int(raster.tissue[i])]
            bytes[i*4] = c.0; bytes[i*4+1] = c.1; bytes[i*4+2] = c.2; bytes[i*4+3] = EditPalette.alpha
        }
        guard let provider = CGDataProvider(data: Data(bytes) as CFData),
              let layer = CGImage(width: raster.mw, height: raster.mh, bitsPerComponent: 8,
                bitsPerPixel: 32, bytesPerRow: raster.mw * 4, space: CGColorSpaceCreateDeviceRGB(),
                bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.last.rawValue), provider: provider,
                decode: nil, shouldInterpolate: false, intent: .defaultIntent) else { return nil }
        let scale = min(1, 900 / Double(max(raster.canvasW, raster.canvasH)))
        let format = UIGraphicsImageRendererFormat(); format.scale = 1
        return UIGraphicsImageRenderer(size: CGSize(width: Double(raster.canvasW)*scale,
            height: Double(raster.canvasH)*scale), format: format).image { context in
            context.cgContext.interpolationQuality = .none
            UIImage(cgImage: layer).draw(in: CGRect(x: raster.rx0*scale, y: raster.ry0*scale,
                width: Double(raster.mw)/raster.mScale*scale, height: Double(raster.mh)/raster.mScale*scale))
        }
    }

    static func automatic(image: UIImage, polygons: [[[Int]]], wbGains: [Double]?) -> UIImage? {
        guard let src = image.cgImage, image.imageOrientation == .up,
              src.width > 1, src.height > 1 else { return nil }
        let polys = polygons.filter { $0.count >= 3 && $0.allSatisfy { $0.count == 2 } }
        let points = polys.flatMap { $0 }
        guard !points.isEmpty else { return nil }
        let x0 = max(0, min(src.width-2, points.map { $0[0] }.min()!))
        let y0 = max(0, min(src.height-2, points.map { $0[1] }.min()!))
        let x1 = max(x0+2, min(src.width, points.map { $0[0] }.max()!))
        let y1 = max(y0+2, min(src.height, points.map { $0[1] }.max()!))
        // Use uniform sampling so ROI pixel geometry remains exact.
        let factor = min(1.0, 512.0 / Double(max(x1-x0, y1-y0)))
        let gw = max(1, Int(Double(x1-x0)*factor)), gh = max(1, Int(Double(y1-y0)*factor))
        var mask = [UInt8](repeating: 0, count: gw*gh)
        for polygon in polys {
            let part = TissueSeg.rasterizePolygon(polygon, x0: x0, y0: y0,
                bw: x1-x0, bh: y1-y0, gw: gw, gh: gh)
            for i in mask.indices where part[i] != 0 { mask[i] = 1 }
        }
        guard let tissue = TissueSeg.classify(src, x0: x0, y0: y0, x1: x1, y1: y1,
            gw: gw, gh: gh, inside: mask, wbGains: wbGains) else { return nil }
        let raster = EditRaster(mask: mask, tissue: tissue, origMask: mask,
            rx0: Double(x0), ry0: Double(y0), mw: gw, mh: gh, mScale: factor,
            cm2PerPx: nil, canvasW: src.width, canvasH: src.height)
        return render(image: image, raster: raster)
    }
}

/// Value identity prevents toggles/zoom from rerunning tissue classification.
private struct TissuePreviewInput: Equatable {
    let image: UIImage
    let polygons: [[[Int]]]
    let raster: EditRaster?
    let wbGains: [Double]?
    static func == (a: Self, b: Self) -> Bool {
        guard a.image === b.image, a.polygons == b.polygons, a.wbGains == b.wbGains else { return false }
        switch (a.raster, b.raster) {
        case (nil, nil): return true
        case let (a?, b?): return a.mask == b.mask && a.tissue == b.tissue && a.rx0 == b.rx0 && a.ry0 == b.ry0
            && a.mw == b.mw && a.mh == b.mh && a.mScale == b.mScale && a.canvasW == b.canvasW && a.canvasH == b.canvasH
        default: return false
        }
    }
}

struct MedicalImagePreview: View {
    let image: UIImage
    let polygons: [[[Int]]]
    let imageW: Int
    let imageH: Int
    var markerPolygons: [[[Int]]] = []
    var raster: EditRaster? = nil
    var wbGains: [Double]? = nil
    var allowAutomatic = false
    var showOutline = true
    @State private var overlay: UIImage?
    @State private var showTissue = true
    @State private var loading = false
    var body: some View {
        VStack(alignment: .leading) {
            WoundImagePreview(image: image, polygons: showOutline ? polygons : [], imageW: imageW, imageH: imageH,
                markerPolygons: markerPolygons, tissueOverlay: showTissue ? overlay : nil)
            Button { showTissue.toggle() } label: {
                Label(showTissue ? "隱藏組織遮罩" : "顯示組織遮罩", systemImage: showTissue ? "eye.fill" : "eye.slash")
            }.buttonStyle(.bordered).disabled(overlay == nil)
            if loading { ProgressView("準備組織預覽…") }
            else if overlay != nil {
                Text(raster == nil ? "自動組織預覽（非醫師確認，僅輔助檢視）" : "已儲存修邊組織遮罩")
                    .font(.caption).foregroundStyle(.secondary)
                Text("肉芽：綠 · 腐肉：琥珀 · 壞死：紫 · 上皮：粉 · 其他：灰").font(.caption)
            } else { Text("組織遮罩未提供或座標不符；未推測既有標註。 ").font(.caption).foregroundStyle(.secondary) }
        }
        .task(id: TissuePreviewInput(image: image, polygons: polygons, raster: raster, wbGains: wbGains)) {
            overlay = nil; loading = true
            let valid = image.cgImage?.width == imageW && image.cgImage?.height == imageH
            let job = Task.detached(priority: .userInitiated) { () -> UIImage? in
                guard valid else { return nil }
                // An invalid saved raster must never fall back to an invented automatic annotation.
                if let raster { return TissuePreviewRenderer.render(image: image, raster: raster) }
                return allowAutomatic ? TissuePreviewRenderer.automatic(image: image, polygons: polygons, wbGains: wbGains) : nil
            }
            let rendered = await job.value
            guard !Task.isCancelled else { return }
            overlay = rendered; loading = false
        }
    }
}
