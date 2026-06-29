import Foundation
import CoreGraphics

/**
 iOS 端到端協調器（對等 Android WoundAnalyzer）：端上分割→與 wsm 分歧度→ArUco 面積→組織v2→PUSH；
 難例(分歧度<門檻)上雲(雙軌)。對齊 engineering/phase2/dual_track_router.py。
 以 [WoundSegmenter] 協定解耦 CoreML 細節（由 SegmentationEngineCoreML 實作該協定即可接入）。
 計分常數取自 SSOT `Preproc`（透過 [WoundPipeline]）。輔助、非診斷、需醫師確認。
 */

/// 端上分割器協定：回傳二值遮罩(row-major)。SegmentationEngineCoreML 可 extension 實作。
public protocol WoundSegmenter {
    func segment(_ image: CGImage) async -> [Bool]
}

public final class WoundAnalyzer {
    private let student: WoundSegmenter
    private let wsm: WoundSegmenter?
    public init(student: WoundSegmenter, wsm: WoundSegmenter? = nil) { self.student = student; self.wsm = wsm }

    /// IoU(分歧度)
    private func iou(_ a: [Bool], _ b: [Bool]) -> Double {
        var inter = 0, uni = 0; let n = min(a.count, b.count)
        for i in 0..<n { let x = a[i], y = b[i]; if x || y { uni += 1 }; if x && y { inter += 1 } }
        return uni == 0 ? 1.0 : Double(inter) / Double(uni)
    }
    /// marker 四角像素面積(Shoelace);corners=[x0,y0,...,x3,y3]
    private func quadPxArea(_ c: [CGFloat]) -> Double {
        guard c.count >= 8 else { return 0 }
        var s = 0.0
        for i in 0..<4 { let j = (i + 1) % 4
            s += Double((c[2*j] + c[2*i]) * (c[2*j+1] - c[2*i+1])) }
        return abs(s / 2.0)
    }

    /// 端到端分析；cloudEscalate：難例上雲回 A∪U 二值遮罩。
    public func run(image: CGImage,
                    markerCorners: [CGFloat]?,
                    tissueFrac: [String: Double],
                    exudate: Int?,
                    cloudEscalate: ((CGImage) async -> [Bool])? = nil) async -> MeasureResult {
        var mask = await student.segment(image)
        let dis = (wsm != nil) ? iou(mask, await wsm!.segment(image)) : 1.0
        if dis < 0.50, let esc = cloudEscalate { mask = await esc(image) }
        let woundPx = mask.filter { $0 }.count
        let markerPxArea = markerCorners.map { quadPxArea($0) }
        let cap = CaptureContainer(rgb: Data(), timestamp: ISO8601DateFormatter().string(from: Date()))
        return WoundPipeline.analyze(cap: cap, woundPx: woundPx, markerPxArea: markerPxArea,
                                     tissueFrac: tissueFrac, disagreementIou: dis, exudate: exudate)
    }
}
