package com.woundmeasurement.app.pipeline

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import org.json.JSONObject
import java.io.ByteArrayOutputStream

/**
 * 修邊柵格的本機持久化。
 *
 * ## 為什麼一定要存
 *
 * 在此之前，離開量測畫面之後**只有多邊形被保存下來**（`MeasurementEntity.gtPolygon`）。
 * 從時間軸回頭修邊時 `resume = null`，柵格由多邊形重建。後果有兩個，都是實質的資料損失：
 *
 * ### 1. 醫師畫的組織分區整批消失
 *
 * 多邊形只描述**傷口外緣**，不含任何組織資訊。重建之後組織圖層退回色彩啟發式的預設值——
 * 醫師上一次逐塊修正的判斷全部不見，而畫面看起來完全正常（一片合理的顏色）。
 * 更糟的是它**看起來像是他沒做過**，於是他會再做一次，然後再消失一次。
 *
 * ### 2. 面積每進出一次就漂移一點
 *
 * 往返是有損的：
 *
 * ```
 * 遮罩 →(邊界追蹤)→ 輪廓 →(RDP ε=1.5 簡化)→ 多邊形 →(掃描線填充)→ 遮罩'
 * ```
 *
 * RDP 會把轉角切掉；重建時 ROI 外框依新多邊形重算，`mScale` 也可能不同，
 * 於是柵格化的像素數跟著變。實測 51.55 → 51.23 cm²（−0.62%），
 * 而醫師**什麼都沒改**。一個會自己緩慢變動的臨床數值比明顯的錯誤更難察覺。
 *
 * 存下柵格之後，續編是**原樣載回**：像素數不變 → 面積不變。
 *
 * ## 格式
 *
 * 單張無損 PNG，三個通道各放一份 8-bit 資料：
 *
 * | 通道 | 內容 | 值域 |
 * |---|---|---|
 * | R | `tissue` 組織碼 | 0..5 |
 * | G | `mask` 目前遮罩 | 0 / 1 |
 * | B | `origMask` AI 原始遮罩（算 correction_iou 用） | 0 / 1 |
 *
 * ⚠ **必須無損。** JPEG 會把 0/1 糊成 0.4，解碼端只能猜，而猜錯的那些像素
 * 正好都在邊界上。仿射參數（rx0/ry0/mScale…）放在另一個 JSON 欄位，
 * 因為它們是浮點數，塞進像素會失去精度。
 */
object EditRasterCodec {

    /** @return (PNG 位元組, meta JSON) 或 null。 */
    fun encode(r: EditRaster): Pair<ByteArray, String>? = runCatching {
        val n = r.mw * r.mh
        if (n <= 0 || r.mask.size < n || r.tissue.size < n) return null
        val px = IntArray(n)
        for (i in 0 until n) {
            val t = r.tissue[i].toInt().let { if (it in 0..T_MAX) it else 0 }
            val m = if (r.mask[i].toInt() != 0) 1 else 0
            val o = if (r.origMask[i].toInt() != 0) 1 else 0
            px[i] = (0xFF shl 24) or (t shl 16) or (m shl 8) or o
        }
        val bmp = Bitmap.createBitmap(r.mw, r.mh, Bitmap.Config.ARGB_8888)
        bmp.setPixels(px, 0, r.mw, 0, 0, r.mw, r.mh)
        val out = ByteArrayOutputStream()
        val ok = bmp.compress(Bitmap.CompressFormat.PNG, 100, out)
        bmp.recycle()
        if (!ok) return null
        val meta = JSONObject().apply {
            put("rx0", r.rx0.toDouble()); put("ry0", r.ry0.toDouble())
            put("mw", r.mw); put("mh", r.mh); put("m_scale", r.mScale.toDouble())
            put("cm2_per_px", r.cm2PerPx ?: JSONObject.NULL)
            put("tissue_edited_px", r.tissueEditedPx); put("mask_px", r.maskPx)
            // 畫布尺寸一起存：續編時若影像尺寸不同（例如換了後端縮圖策略），
            // 柵格座標就對不上——寧可放棄續編也不要用錯的座標畫在病人的傷口上。
            put("canvas_w", r.canvasW); put("canvas_h", r.canvasH)
        }.toString()
        Pair(out.toByteArray(), meta)
    }.getOrNull()

    /**
     * @param canvasW/H 目前畫布尺寸。與存檔時不同就回 null（呼叫端退回由多邊形重建）。
     */
    fun decode(png: ByteArray?, meta: String?, canvasW: Int, canvasH: Int): EditRaster? = runCatching {
        if (png == null || meta.isNullOrBlank()) return null
        val j = JSONObject(meta)
        val mw = j.getInt("mw"); val mh = j.getInt("mh")
        if (mw <= 0 || mh <= 0) return null
        // ⚠ 尺寸不符時**不要**縮放遷就。柵格座標是相對於當時那張畫布算的，
        // 縮放後每一個像素都對應到不同的位置，醫師的判斷會被搬到錯的地方。
        val cw = j.optInt("canvas_w", 0); val ch = j.optInt("canvas_h", 0)
        if (cw > 0 && ch > 0 && (cw != canvasW || ch != canvasH)) return null

        val bmp = BitmapFactory.decodeByteArray(png, 0, png.size) ?: return null
        if (bmp.width != mw || bmp.height != mh) { bmp.recycle(); return null }
        val px = IntArray(mw * mh)
        bmp.getPixels(px, 0, mw, 0, 0, mw, mh)
        bmp.recycle()
        val tissue = ByteArray(mw * mh); val mask = ByteArray(mw * mh); val orig = ByteArray(mw * mh)
        for (i in px.indices) {
            tissue[i] = ((px[i] shr 16) and 0xFF).toByte()
            mask[i] = ((px[i] shr 8) and 0xFF).toByte()
            orig[i] = (px[i] and 0xFF).toByte()
        }
        EditRaster(
            mask, tissue, orig,
            j.getDouble("rx0").toFloat(), j.getDouble("ry0").toFloat(), mw, mh,
            j.getDouble("m_scale").toFloat(),
            if (j.isNull("cm2_per_px")) null else j.getDouble("cm2_per_px"),
            tissueEditedPx = j.optInt("tissue_edited_px", 0),
            maskPx = j.optInt("mask_px", 0),
            canvasW = cw, canvasH = ch
        )
    }.getOrNull()
}
