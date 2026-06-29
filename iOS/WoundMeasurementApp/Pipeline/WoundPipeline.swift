import Foundation

/**
 端上量測/分型/嚴重度 管線骨架（依 docs/mobile_technical_spec）。
 計分常數取自 SSOT 產生的 `Preproc`（Preprocessing.generated.swift），禁硬編碼。
 與後端 clinical_rules.push_score / tissue_proxy_v2 一致；金標 engineering/generated/push_golden.json。
 輔助、非診斷、需醫師確認。
 */

struct CaptureContainer {
    let rgb: Data
    var depthMm: [Float]? = nil       // LiDAR 選配(Float32 mm)
    var intrinsicsK: [Float]? = nil   // fx,fy,cx,cy
    var stickerPose: [Float]? = nil
    let timestamp: String
    var deidentified: Bool = false
}
struct ConsentRecord {
    let care: Bool                    // ①必填
    let train: Bool                   // ②選填(可撤回)
    let signaturePNG: Data?           // 電子簽名
    let signedAt: String
}
struct PushScore { let area: Int?; let tissue: Int; let exudate: Int?; let partial: Int?; let full: Int? }
struct MeasureResult {
    let areaCm2: Double?; let tissueFrac: [String: Double]
    let push: PushScore; let route: String; let confidence: Double
    let disclaimer = "輔助、非診斷、需醫師確認；滲液須醫師輸入"
}

enum WoundPipeline {
    private static let tissueScore = ["necrosis": 4, "slough": 3, "granulation": 2, "epithelial": 1]

    /// PUSH 面積子分；帶值取自 SSOT `Preproc.pushAreaBands`。
    static func areaSubscore(_ cm2: Double?) -> Int? {
        guard let v = cm2 else { return nil }
        if v <= 0 { return 0 }
        for b in Preproc.pushAreaBands { if v <= b.0 { return b.1 } }
        return 10
    }
    /// 組織子分：取最差存在組織(門檻5%)，順序取自 `Preproc.tissueWorstOrder`。
    static func tissueSubscore(_ frac: [String: Double], present: Double = 0.05) -> Int {
        for k in Preproc.tissueWorstOrder { if (frac[k] ?? 0) >= present { return tissueScore[k] ?? 0 } }
        return 0
    }
    static func push(_ cm2: Double?, _ frac: [String: Double], _ exudate: Int?) -> PushScore {
        let a = areaSubscore(cm2); let t = tissueSubscore(frac)
        let partial = a != nil ? a! + t : nil
        let full = (partial != nil && exudate != nil) ? partial! + exudate! : nil
        return PushScore(area: a, tissue: t, exudate: exudate, partial: partial, full: full)
    }
    /// 面積比例法：wound_px × markerMm² / markerPxArea / 100；markerMm 取自 SSOT。
    static func areaCm2ByRatio(woundPx: Int, markerPxArea: Double, markerMm: Double = Preproc.markerMmActive) -> Double? {
        guard markerPxArea > 0 else { return nil }
        return Double(woundPx) * markerMm * markerMm / markerPxArea / 100.0
    }
    /// 端上分析骨架；難例(分歧度<門檻)上雲(雙軌)。TODO：接 SegmentationEngineCoreML / ArUco / tissue v2。
    static func analyze(cap: CaptureContainer, woundPx: Int, markerPxArea: Double?,
                        tissueFrac: [String: Double], disagreementIou: Double,
                        exudate: Int?, escalateIou: Double = 0.50) -> MeasureResult {
        let area = markerPxArea != nil ? areaCm2ByRatio(woundPx: woundPx, markerPxArea: markerPxArea!) : nil
        let route = disagreementIou < escalateIou ? "cloud" : "ondevice"
        let conf = route == "cloud" ? 0.95 : (1.0 - disagreementIou)
        return MeasureResult(areaCm2: area, tissueFrac: tissueFrac, push: push(area, tissueFrac, exudate), route: route, confidence: conf)
    }
}
