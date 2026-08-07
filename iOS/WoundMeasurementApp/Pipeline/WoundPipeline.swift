import Foundation

/**
 端上量測／分型／嚴重度計分。**逐條對齊 Android `WoundPipeline.kt` 與後端
 `vendor/clinical_rules.push_score`。**

 計分常數（面積帶、組織順序、marker_mm）一律取自 SSOT 產生的 `Preproc`，**禁硬編碼**——
 三個平台各抄一份的話，改了 SSOT 只會有一端跟上，而差異會表現成「同一張照片在
 iPhone 上是 PUSH 9、在 Android 上是 PUSH 8」。

 金標：`engineering/generated/push_golden.json`（對應測試 `WoundPipelineGoldenTests`）。

 輔助、非診斷、需醫師確認。
 */

// MARK: - 資料模型

struct PushScore: Equatable {
    let area: Int?
    let tissue: Int
    let exudate: Int?
    let partial: Int?
    let full: Int?
}

struct MeasureResult {
    var areaCm2: Double?
    var tissueFrac: [String: Double]
    var push: PushScore
    var route: String
    var confidence: Double
    var disclaimer: String = "輔助、非診斷、需醫師確認；滲液須醫師輸入"
}

// MARK: - 計分

enum WoundPipeline {

    /// 組織 → PUSH 組織子分。鍵為後端具名欄位。
    private static let tissueScore: [String: Int] = [
        "necrosis": 4, "slough": 3, "granulation": 2, "epithelial": 1
    ]

    /**
     PUSH 面積子分（NPUAP 3.0）。帶值取自 SSOT `Preproc.pushAreaBands`。

     `nil` 面積 → `nil` 子分（**不是 0**）：未校正時我們不知道面積，而 0 的意思是
     「已癒合」。把「量不出來」記成「已癒合」，癒合曲線上會出現一個假的終點。
     */
    static func areaSubscore(_ cm2: Double?) -> Int? {
        guard let cm2 = cm2 else { return nil }
        if cm2 <= 0.0 { return 0 }
        for b in Preproc.pushAreaBands where cm2 <= b.0 { return b.1 }
        return 10
    }

    /// 組織子分：取**最差的存在組織**（門檻 5%），順序取自 SSOT `Preproc.tissueWorstOrder`。
    static func tissueSubscore(_ frac: [String: Double], present: Double = 0.05) -> Int {
        for k in Preproc.tissueWorstOrder where (frac[k] ?? 0.0) >= present {
            return tissueScore[k] ?? 0
        }
        return 0
    }

    /**
     PUSH ＝ 面積 ＋ 組織（＋ 滲液；**須醫師輸入**）。

     `full` 只有在面積與滲液都有值時才成立。滲液無法由單張影像判定——後端 classify
     一律回 `exudate_subscore: null` 與 `total_full: null`，端上必須讓醫師輸入才補得齊。
     */
    static func push(cm2: Double?, frac: [String: Double], exudate: Int?) -> PushScore {
        let a = areaSubscore(cm2)
        let t = tissueSubscore(frac)
        let partial = a.map { $0 + t }
        let full: Int? = {
            guard let p = partial, let e = exudate else { return nil }
            return p + e
        }()
        return PushScore(area: a, tissue: t, exudate: exudate, partial: partial, full: full)
    }

    /**
     面積比例法：`woundPx × markerMm² / markerPxArea / 100`。`markerMm` 取自 SSOT。

     這是實測誤差最小的一條路（實印實拍 n=15：平均 |誤差| 1.9%、最大 4.3%、斜角 60° 無劣化），
     且對旋轉免疫。透視單應校正在遠距外推時會爆掉，不要拿它算面積。
     */
    static func areaCm2ByRatio(woundPx: Int, markerPxArea: Double,
                               markerMm: Double = Preproc.markerMmActive) -> Double? {
        guard markerPxArea > 0 else { return nil }
        return Double(woundPx) * markerMm * markerMm / markerPxArea / 100.0
    }

    /**
     由 `mm_per_px` 直接算面積。

     ⚠ **修邊後的面積一定要走這條**，不要用「AI 初始面積 × 修正比例」——後者會累積
     換算鏈偏差。後端把 `mm_per_px` 直傳給端上，設計意圖就是讓端上自己乘。
     */
    static func areaCm2(pixelCount: Int, mmPerPx: Double?) -> Double? {
        guard let m = mmPerPx, m > 0 else { return nil }
        return Double(pixelCount) * m * m / 100.0
    }

    /// 每像素對應的 cm²。存進 `EditRaster.cm2PerPx` 後鎖定，確保面積冪等。
    static func cm2PerPixel(mmPerPx: Double?, rasterScale: Double) -> Double? {
        guard let m = mmPerPx, m > 0, rasterScale > 0 else { return nil }
        // 柵格像素邊長 ＝ 影像像素邊長 / mScale
        let mmPerRasterPx = m / rasterScale
        return mmPerRasterPx * mmPerRasterPx / 100.0
    }

    /// 兩個遮罩的 IoU。用於雙軌分歧度與 `correction_iou`。
    static func iou(_ a: [UInt8], _ b: [UInt8]) -> Double {
        let n = min(a.count, b.count)
        guard n > 0 else { return 1.0 }
        var inter = 0, uni = 0
        for i in 0..<n {
            let x = a[i] != 0, y = b[i] != 0
            if x || y { uni += 1 }
            if x && y { inter += 1 }
        }
        return uni == 0 ? 1.0 : Double(inter) / Double(uni)
    }
}
