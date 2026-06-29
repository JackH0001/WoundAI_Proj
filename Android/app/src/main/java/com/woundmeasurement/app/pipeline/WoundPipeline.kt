package com.woundmeasurement.app.pipeline

import com.woundmeasurement.app.generated.Preproc

/**
 * 端上量測/分型/嚴重度 管線骨架（依 docs/mobile_technical_spec）。
 * 計分常數(面積帶/組織順序/marker_mm)一律取自 SSOT 產生的 [Preproc]，禁硬編碼。
 * 與後端 clinical_rules.push_score / tissue_proxy_v2 規則一致；金標見 engineering/generated/push_golden.json。
 * 輔助、非診斷、需醫師確認。
 */

// ---- 資料模型（對應 capture_container / 同意 / 標註）----
data class CaptureContainer(
    val rgb: ByteArray,
    val depthMm: FloatArray? = null,      // LiDAR 選配（Float32 mm）
    val intrinsicsK: FloatArray? = null,  // fx,fy,cx,cy
    val stickerPose: FloatArray? = null,  // marker 四角影像座標
    val timestamp: String,
    val deidentified: Boolean = false
)
data class ConsentRecord(
    val care: Boolean,                    // ①必填
    val train: Boolean,                   // ②選填(可撤回)
    val signaturePng: ByteArray?,         // 電子簽名
    val signedAt: String
)
data class PushScore(
    val area: Int?, val tissue: Int, val exudate: Int?,
    val partial: Int?, val full: Int?
)
data class MeasureResult(
    val areaCm2: Double?, val tissueFrac: Map<String, Double>,
    val push: PushScore, val route: String, val confidence: Double,
    val disclaimer: String = "輔助、非診斷、需醫師確認；滲液須醫師輸入"
)

object WoundPipeline {
    private val TISSUE_SCORE = mapOf("necrosis" to 4, "slough" to 3, "granulation" to 2, "epithelial" to 1)

    /** PUSH 面積子分（NPUAP3.0），帶值取自 SSOT [Preproc.pushAreaBands]。 */
    fun areaSubscore(cm2: Double?): Int? {
        if (cm2 == null) return null
        if (cm2 <= 0.0) return 0
        for (b in Preproc.pushAreaBands) if (cm2 <= b[0]) return b[1].toInt()
        return 10
    }
    /** 組織子分：取最差存在組織（門檻 5%），順序取自 [Preproc.tissueWorstOrder]。 */
    fun tissueSubscore(frac: Map<String, Double>, present: Double = 0.05): Int {
        for (k in Preproc.tissueWorstOrder) if ((frac[k] ?: 0.0) >= present) return TISSUE_SCORE[k] ?: 0
        return 0
    }
    /** PUSH = 面積 + 組織 (+ 滲液;醫師輸入)。 */
    fun push(cm2: Double?, frac: Map<String, Double>, exudate: Int?): PushScore {
        val a = areaSubscore(cm2); val t = tissueSubscore(frac)
        val partial = if (a != null) a + t else null
        val full = if (partial != null && exudate != null) partial + exudate else null
        return PushScore(a, t, exudate, partial, full)
    }

    /** 面積比例法：wound_px × markerMm² / markerPxArea / 100。markerMm 取自 SSOT。 */
    fun areaCm2ByRatio(woundPx: Int, markerPxArea: Double, markerMm: Double = Preproc.markerMmActive): Double? {
        if (markerPxArea <= 0.0) return null
        return woundPx * markerMm * markerMm / markerPxArea / 100.0
    }

    /**
     * 端上分析骨架：分割→面積→組織→PUSH；難例(分歧度)上雲(雙軌)。
     * seg：傳入端上分割器(回 mask 與信心);cloudEscalate：難例時呼叫雲端 A∪U。
     * TODO(原生實作)：接 OnnxSegmentationModule(student/wsm)、ArUco 偵測、tissue v2(WB+HSV)。
     */
    fun analyze(
        cap: CaptureContainer,
        woundPx: Int,
        markerPxArea: Double?,
        tissueFrac: Map<String, Double>,
        disagreementIou: Double,
        exudate: Int?,
        escalateIou: Double = 0.50
    ): MeasureResult {
        val area = if (markerPxArea != null) areaCm2ByRatio(woundPx, markerPxArea) else null
        val route = if (disagreementIou < escalateIou) "cloud" else "ondevice"
        val conf = if (route == "cloud") 0.95 else (1.0 - disagreementIou)
        return MeasureResult(area, tissueFrac, push(area, tissueFrac, exudate), route, conf)
    }
}
