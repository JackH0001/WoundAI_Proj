package com.woundmeasurement.app.pipeline

import android.graphics.Bitmap
import com.woundmeasurement.app.processing.OnnxSegmentationModule

/**
 * 端到端協調器：端上分割(student) → 與 wsm 算分歧度 → ArUco 面積 → 組織 v2 → PUSH；
 * 難例(分歧度<門檻)以 cloudEscalate 上雲(雙軌)。對齊 engineering/phase2/dual_track_router.py。
 * 計分常數取自 SSOT [com.woundmeasurement.app.generated.Preproc]（透過 [WoundPipeline]）。
 *
 * @param student 端上主分割(OnnxSegmentationModule, student_fp16)
 * @param wsm     端上備援分割(可選;用於分歧度判難)
 */
class WoundAnalyzer(
    private val student: OnnxSegmentationModule,
    private val wsm: OnnxSegmentationModule? = null
) {
    /** IoU(分歧度)：兩端上遮罩一致度;低→難例。 */
    private fun iou(a: BooleanArray, b: BooleanArray): Double {
        var inter = 0; var uni = 0
        val n = minOf(a.size, b.size)
        for (i in 0 until n) { val x = a[i]; val y = b[i]; if (x || y) uni++; if (x && y) inter++ }
        return if (uni == 0) 1.0 else inter.toDouble() / uni
    }
    /** 多邊形(marker 四角)像素面積(Shoelace)。corners=[x0,y0,x1,y1,x2,y2,x3,y3]。 */
    private fun quadPxArea(c: FloatArray): Double {
        if (c.size < 8) return 0.0
        var s = 0.0
        for (i in 0 until 4) { val j = (i + 1) % 4
            s += (c[2*j] + c[2*i]) * (c[2*j+1] - c[2*i+1]) }
        return Math.abs(s / 2.0)
    }

    /**
     * @param bitmap 原圖(含校正貼紙)
     * @param markerCorners ArUco 四角(影像座標,8 值)或 null(未校正)
     * @param tissueFrac 組織 v2 比例(necrosis/slough/granulation/epithelial);TODO 接 tissue v2(WB+HSV)
     * @param exudate 滲液(醫師輸入,0–3)或 null
     * @param cloudEscalate 難例上雲:傳原圖回雲端 A∪U 二值遮罩(suspend);null 則維持端上
     */
    suspend fun run(
        bitmap: Bitmap,
        markerCorners: FloatArray?,
        exudate: Int?,
        tissueFracOverride: Map<String, Double>? = null,
        cloudEscalate: (suspend (Bitmap) -> BooleanArray)? = null
    ): MeasureResult {
        val s = student.analyze(bitmap)                 // SegmentationResult(probMap, binaryMask)
        var mask = s.binaryMask
        val dis = if (wsm != null) iou(mask, wsm.analyze(bitmap).binaryMask) else 1.0
        // 雙軌:難例上雲(以雲端遮罩取代端上)
        if (dis < 0.50 && cloudEscalate != null) mask = cloudEscalate(bitmap)
        val woundPx = mask.count { it }
        val markerPxArea = markerCorners?.let { quadPxArea(it) }
        // 組織 v2:遮罩內像素 → 灰世界白平衡 → 互斥分類 → 比例(可由 override 帶入)
        val frac = tissueFracOverride ?: computeTissueFrac(bitmap, mask)
        return WoundPipeline.analyze(
            cap = CaptureContainer(rgb = ByteArray(0), timestamp = nowIso()),
            woundPx = woundPx,
            markerPxArea = markerPxArea,
            tissueFrac = frac,
            disagreementIou = dis,
            exudate = exudate
        )
    }

    /** 由遮罩內像素計算組織比例(灰世界白平衡 + TissueClassifierV2 互斥分類)。 */
    private fun computeTissueFrac(bitmap: Bitmap, mask: BooleanArray): Map<String, Double> {
        val mw = Math.sqrt(mask.size.toDouble()).toInt()
        if (mw <= 0) return emptyMap()
        val scaled = Bitmap.createScaledBitmap(bitmap, mw, mw, true)
        val px = IntArray(mw * mw); scaled.getPixels(px, 0, mw, 0, 0, mw, mw)
        var sr = 0.0; var sg = 0.0; var sb = 0.0; var n = 0
        for (i in mask.indices) if (mask[i] && i < px.size) {
            val c = px[i]; sr += (c shr 16 and 0xff); sg += (c shr 8 and 0xff); sb += (c and 0xff); n++
        }
        if (n == 0) return mapOf("necrosis" to 0.0,"slough" to 0.0,"granulation" to 0.0,"epithelial" to 0.0,"other" to 0.0)
        val gains = TissueClassifierV2.wbGains(sr / n, sg / n, sb / n)
        val pixels = ArrayList<IntArray>(n)
        for (i in mask.indices) if (mask[i] && i < px.size) {
            val c = px[i]
            pixels.add(intArrayOf(
                TissueClassifierV2.applyGain(c shr 16 and 0xff, gains[0]),
                TissueClassifierV2.applyGain(c shr 8 and 0xff, gains[1]),
                TissueClassifierV2.applyGain(c and 0xff, gains[2])))
        }
        return TissueClassifierV2.proxy(pixels)
    }
    private fun nowIso(): String = java.time.OffsetDateTime.now().toString()
}
