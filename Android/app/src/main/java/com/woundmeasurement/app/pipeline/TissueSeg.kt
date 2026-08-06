package com.woundmeasurement.app.pipeline

import android.graphics.Bitmap
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

/**
 * 逐像素組織分區。**參照圖與修邊畫面共用這一份實作。**
 *
 * ## 為什麼要抽出來
 *
 * 兩個畫面各寫一份的話，某天只改了其中一邊，醫師就會看到「結果頁說這裡是腐肉、
 * 修邊頁說是肉芽」——而他無從判斷該相信哪一個。分區是要拿去當 GT 的，不能有兩種說法。
 *
 * ## ⚠ 兩套組織碼，必須明確轉換
 *
 * | 來源 | 1 | 2 | 3 | 4 | 5 |
 * |---|---|---|---|---|---|
 * | [TissueClassifierV2]（與後端一致） | 壞死 | 腐肉 | 肉芽 | 上皮 | 其他 |
 * | 修邊畫面／[T_NAMES]                | 肉芽 | 腐肉 | 壞死 | 上皮 | 其他 |
 *
 * **1 和 3 是相反的。** 直接把分類器的輸出塞進 tissue 陣列，肉芽與壞死會整批互換——
 * 而畫面看起來完全正常（都是合理的顏色分布），只有送去訓練的 GT 是錯的。
 * 這種錯誤不會有任何徵兆，所以轉換表寫成具名常數並在此說明。
 */
internal object TissueSeg {

    /** 分類器碼 → 修邊畫面碼。索引 0 不用。 */
    val CLS_TO_EDIT = byteArrayOf(0, 3, 2, 1, 4, 5)

    /** 取樣網格長邊上限。全解析度逐像素跑 HSV 規則會得到椒鹽狀雜點，且成本與像素數成正比。 */
    const val GRID_MAX = 512

    /**
     * 對 [src] 的矩形區域做逐像素分類，回傳 gw×gh 的**修邊畫面碼**（0＝區域外）。
     *
     * @param inside gw*gh 的遮罩（非 0 才分類）。白平衡增益只用遮罩內的像素計算——
     *   用整張圖算會被背景膚色拉偏，而膚色恰好落在肉芽的色相附近。
     */
    fun classify(
        src: Bitmap, x0: Int, y0: Int, x1: Int, y1: Int,
        gw: Int, gh: Int, inside: ByteArray
    ): ByteArray? = runCatching {
        val bw = x1 - x0; val bh = y1 - y0
        if (bw < 2 || bh < 2 || gw < 2 || gh < 2) return null

        val roi = Bitmap.createBitmap(src, x0, y0, bw, bh)
        val small = Bitmap.createScaledBitmap(roi, gw, gh, true)
        if (roi !== small) roi.recycle()
        val px = IntArray(gw * gh)
        small.getPixels(px, 0, gw, 0, 0, gw, gh)
        if (small !== src) small.recycle()

        var sr = 0.0; var sg = 0.0; var sb = 0.0; var n = 0
        for (i in px.indices) if (inside[i].toInt() != 0) {
            sr += (px[i] shr 16) and 0xFF; sg += (px[i] shr 8) and 0xFF; sb += px[i] and 0xFF; n++
        }
        if (n == 0) return null
        val g = TissueClassifierV2.wbGains(sr / n, sg / n, sb / n)

        val raw = ByteArray(gw * gh)
        for (i in px.indices) if (inside[i].toInt() != 0) {
            val r = TissueClassifierV2.applyGain((px[i] shr 16) and 0xFF, g[0])
            val gg = TissueClassifierV2.applyGain((px[i] shr 8) and 0xFF, g[1])
            val b = TissueClassifierV2.applyGain(px[i] and 0xFF, g[2])
            raw[i] = CLS_TO_EDIT[TissueClassifierV2.classifyPixel(r, gg, b)]
        }

        // 3×3 多數決。少了這一步畫面是一片椒鹽，醫師無法從中判斷任何東西，
        // 而「看不懂的分區」比「沒有分區」更糟——他會關掉圖層，等於這個功能不存在。
        val out = raw.copyOf()
        val cnt = IntArray(T_MAX + 1)
        for (y in 1 until gh - 1) for (x in 1 until gw - 1) {
            val i = y * gw + x
            if (inside[i].toInt() == 0) continue
            java.util.Arrays.fill(cnt, 0)
            for (dy in -1..1) for (dx in -1..1) {
                val c = raw[(y + dy) * gw + (x + dx)].toInt()
                if (c in 1..T_MAX) cnt[c]++
            }
            var best = raw[i].toInt(); var bn = -1
            for (c in 1..T_MAX) if (cnt[c] > bn) { bn = cnt[c]; best = c }
            out[i] = best.toByte()
        }
        out
    }.getOrNull()

    /** 依區域大小挑取樣網格（長邊 ≤ [GRID_MAX]）。 */
    fun grid(bw: Int, bh: Int): Pair<Int, Int> {
        val s = min(1f, GRID_MAX.toFloat() / max(bw, bh))
        return Pair(max(2, (bw * s).roundToInt()), max(2, (bh * s).roundToInt()))
    }

    /** 多邊形掃描線填充成 gw×gh 遮罩（座標先線性映射到取樣網格）。 */
    fun rasterizePolygon(
        polygon: List<List<Int>>, x0: Int, y0: Int, bw: Int, bh: Int, gw: Int, gh: Int
    ): ByteArray {
        val inside = ByteArray(gw * gh)
        val sx = gw.toFloat() / bw; val sy = gh.toFloat() / bh
        val poly = polygon.map { floatArrayOf((it[0] - x0) * sx, (it[1] - y0) * sy) }
        for (y in 0 until gh) {
            val yc = y + 0.5f
            var j = poly.size - 1
            val xs = ArrayList<Float>(8)
            for (i in poly.indices) {
                val a = poly[i]; val b = poly[j]
                if ((a[1] > yc) != (b[1] > yc)) xs.add(a[0] + (yc - a[1]) / (b[1] - a[1]) * (b[0] - a[0]))
                j = i
            }
            xs.sort()
            var k = 0
            while (k + 1 < xs.size) {
                val xa = max(0, xs[k].roundToInt()); val xb = min(gw - 1, xs[k + 1].roundToInt())
                for (x in xa..xb) inside[y * gw + x] = 1
                k += 2
            }
        }
        return inside
    }
}
