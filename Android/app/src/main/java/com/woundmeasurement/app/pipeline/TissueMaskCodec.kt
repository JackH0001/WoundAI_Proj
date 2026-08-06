package com.woundmeasurement.app.pipeline

import android.graphics.Bitmap
import android.util.Base64
import java.io.ByteArrayOutputStream

/**
 * 把修邊柵格的組織分類編成 PNG，供上傳成訓練用的組織分割 GT。
 *
 * ## 為什麼送柵格而不是影像尺寸的遮罩
 *
 * 柵格（長邊 ≤1024）**就是醫師實際畫的解析度**。放大成原圖尺寸再送，等於在資料裡
 * 加入我們自己插值出來的假精細度——訓練時模型會去學那些其實不存在的邊界細節。
 * 送小圖再附仿射參數，反而是無損的：它保留了「這是在什麼解析度上判斷的」這個事實。
 *
 * 順帶的好處是體積：1024² 的 5 類 PNG 約 10–40 KB，相對於 JPEG 可忽略。
 *
 * ## 編碼方式
 *
 * 值直接放在 R 通道（0＝遮罩外，1..5＝組織碼），G/B 補 0，A=255。
 * **不用調色盤**：Android 的 `Bitmap.compress` 不支援 8-bit 調色盤輸出，
 * 硬要做得自己寫 PNG 編碼器；而 ARGB 走 PNG 無損壓縮後，
 * 大面積同值區塊本來就會被壓得很小，省下的那點體積不值得多一個自寫的編碼器要維護。
 *
 * ⚠ **必須無損。** JPEG 會把類別邊界糊掉，而「3.7 類」這種值沒有意義——
 * 解碼端只能四捨五入，於是邊界像素被隨機指派到相鄰類別。PNG 是唯一選項。
 */
object TissueMaskCodec {

    /** 值域上限檢查用。與 [T_MAX] 同步；解碼端看到超出範圍的值應視為資料損毀。 */
    const val MAX_CODE = 5

    /**
     * @return base64 編碼的 PNG，或 null（尺寸不合、編碼失敗）。
     *   失敗時呼叫端應照常送出其餘欄位——遮罩缺席只是少一個訓練樣本，
     *   讓整筆標註失敗才是真的損失。
     */
    fun encode(tissue: ByteArray, mask: ByteArray, mw: Int, mh: Int): String? {
        if (mw < 2 || mh < 2 || tissue.size < mw * mh || mask.size < mw * mh) return null
        return runCatching {
            val px = IntArray(mw * mh)
            for (i in px.indices) {
                // 遮罩外一律 0。tissue 陣列在遮罩外可能留有舊值（擦除只清 mask 不清 tissue），
                // 直接編出去會讓訓練資料多出一圈傷口外的假標註。
                val v = if (mask[i].toInt() == 0) 0
                        else tissue[i].toInt().let { if (it in 1..MAX_CODE) it else 0 }
                px[i] = (0xFF shl 24) or (v shl 16)
            }
            val bmp = Bitmap.createBitmap(mw, mh, Bitmap.Config.ARGB_8888)
            bmp.setPixels(px, 0, mw, 0, 0, mw, mh)
            val out = ByteArrayOutputStream()
            val ok = bmp.compress(Bitmap.CompressFormat.PNG, 100, out)
            bmp.recycle()
            if (!ok) return null
            Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP)
        }.getOrNull()
    }
}
