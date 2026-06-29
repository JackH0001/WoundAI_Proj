package com.woundmeasurement.app.pipeline

import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

/**
 * 組織分型 v2（白平衡 + HSV 飽和度感知,遮罩內互斥）。
 * 與後端 wound_classifier.tissue_classmap_v2 一致；HSV 採 OpenCV 8-bit 公式(H 0-180),
 * 已驗證與 cv2 抽樣 4096 點 0 差異。金標 engineering/generated/tissue_golden.json。
 * 組織碼：1 壞死 / 2 腐肉 / 3 肉芽 / 4 上皮 / 5 其他。輔助、非診斷、需醫師確認。
 */
object TissueClassifierV2 {
    data class HSV(val h: Int, val s: Int, val v: Int)

    /** OpenCV 8-bit RGB→HSV(H 0-180, S/V 0-255)。 */
    fun rgb2hsv(r: Int, g: Int, b: Int): HSV {
        val R = r.toDouble(); val G = g.toDouble(); val B = b.toDouble()
        val v = max(R, max(G, B)); val mn = min(R, min(G, B)); val d = v - mn
        val s = if (v == 0.0) 0.0 else d / v * 255.0
        var h = when {
            d == 0.0 -> 0.0
            v == R -> 60 * (G - B) / d
            v == G -> 120 + 60 * (B - R) / d
            else -> 240 + 60 * (R - G) / d
        }
        if (h < 0) h += 360
        h /= 2.0
        return HSV(h.roundToInt(), s.roundToInt(), v.roundToInt())
    }

    /** 單像素互斥分類(輸入應為已白平衡之 RGB)。 */
    fun classifyPixel(r: Int, g: Int, b: Int): Int {
        val (h, s, v) = rgb2hsv(r, g, b)
        if (v < 75 && s < 90) return 1                         // 壞死:暗且低飽和
        if (h in 18..45 && s >= 60 && v >= 60) return 2        // 腐肉:黃
        if (v >= 170 && s < 70 && r > 150) return 4            // 上皮:淡粉高明度
        if ((h < 15 || h > 160) && s >= 60) return 3           // 肉芽:紅/高飽和
        return 5                                               // 其他
    }

    /** 灰世界白平衡增益：gain_c = meanAll / mean_c。 */
    fun wbGains(meanR: Double, meanG: Double, meanB: Double): DoubleArray {
        val mu = (meanR + meanG + meanB) / 3.0
        return doubleArrayOf(mu / (meanR + 1e-6), mu / (meanG + 1e-6), mu / (meanB + 1e-6))
    }
    fun applyGain(v: Int, gain: Double): Int = min(255, max(0, (v * gain).roundToInt()))

    /** 遮罩內組織比例(necrosis/slough/granulation/epithelial/other)。pixels=遮罩內已白平衡 RGB 列表。 */
    fun proxy(pixels: List<IntArray>): Map<String, Double> {
        val key = arrayOf("necrosis", "slough", "granulation", "epithelial", "other")
        val cnt = IntArray(6)
        for (p in pixels) cnt[classifyPixel(p[0], p[1], p[2])]++
        val tot = pixels.size.coerceAtLeast(1)
        return (1..5).associate { key[it - 1] to cnt[it].toDouble() / tot }
    }
}
