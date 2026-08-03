package com.woundmeasurement.app.data.store

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import com.woundmeasurement.app.data.crypto.PhiCrypto
import java.io.File
import java.util.UUID

/**
 * 本機傷口影像儲存（**加密**）。
 *
 * 為什麼要存：先前 `MeasurementEntity.imagePath` 一直是空字串，等於影像完全沒落地 →
 *  - 時間軸沒有縮圖，一列只有數字；
 *  - **回頭重新修邊做不到**（修邊需要與 polygon 同一座標空間的那張圖）；
 *  - 重簽同意後要補送標註，只能整個重測一遍。
 *
 * 存哪一張：**送給後端 classify 的那張 work 影像（長邊 ≤2048）**，不是相機原圖。
 * 因為 `gt_polygon` 與 `image_w/h` 的座標空間就是它；存原圖反而對不上。
 *
 * 為什麼加密：傷口影像是 PHI。app 私有目錄雖然其他 App 讀不到，但
 *  (a) root/備份萃取拿得走，(b) `filesDir` 不在 `backup_rules.xml` 排除的 `database` domain 內。
 * 用 [PhiCrypto.encryptBytes]（Keystore AES-GCM，金鑰不可匯出）→ 檔案外流也是密文。
 * 代價：不能直接餵給 Glide/ImageDecoder，必須先解密到記憶體（見 [loadThumbnail]）。
 *
 * 縮圖策略：**不另存縮圖檔**，一律從同一份加密原檔用 `inSampleSize` 解出來。
 * 兩份檔案遲早會不同步（刪一個忘了刪另一個、修邊後只更新一個），單一來源比較省心。
 */
class LocalImageStore(context: Context) {

    private val dir = File(context.filesDir, "wound_images").apply { if (!exists()) mkdirs() }

    /** 存檔並回傳相對檔名（寫進 `MeasurementEntity.imagePath`）。失敗回 null，呼叫端須容許沒有影像。 */
    fun save(jpeg: ByteArray): String? = try {
        val name = "img_${UUID.randomUUID()}.enc"
        val enc = PhiCrypto.encryptBytes(jpeg) ?: return null
        File(dir, name).writeBytes(enc)
        name
    } catch (e: Exception) {
        null
    }

    /** 存 Bitmap（內部先壓成 JPEG q90；work 影像本來就是從 JPEG 來的，再壓一次差異可忽略）。 */
    fun save(bitmap: Bitmap): String? = try {
        val out = java.io.ByteArrayOutputStream()
        bitmap.compress(Bitmap.CompressFormat.JPEG, 90, out)
        save(out.toByteArray())
    } catch (e: Exception) {
        null
    }

    private fun readDecrypted(name: String?): ByteArray? {
        if (name.isNullOrBlank()) return null
        val f = File(dir, name)
        if (!f.exists()) return null
        return try { PhiCrypto.decryptBytes(f.readBytes()) } catch (e: Exception) { null }
    }

    /**
     * 解出縮圖。`reqPx` 是期望的長邊像素，用 `inSampleSize` 降採樣
     * （2048 的圖直接整張解進記憶體是 16MB ARGB，清單捲個幾列就 OOM）。
     */
    fun loadThumbnail(name: String?, reqPx: Int = 256): Bitmap? {
        val raw = readDecrypted(name) ?: return null
        return try {
            val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            BitmapFactory.decodeByteArray(raw, 0, raw.size, bounds)
            var scale = 1
            val longEdge = maxOf(bounds.outWidth, bounds.outHeight)
            while (longEdge / (scale * 2) >= reqPx) scale *= 2
            BitmapFactory.decodeByteArray(raw, 0, raw.size,
                BitmapFactory.Options().apply { inSampleSize = scale })
        } catch (e: Exception) {
            null
        }
    }

    /** 解出完整影像（回頭修邊用：座標空間必須與 gt_polygon 一致，所以這裡**不可**降採樣）。 */
    fun loadFull(name: String?): Bitmap? {
        val raw = readDecrypted(name) ?: return null
        return try { BitmapFactory.decodeByteArray(raw, 0, raw.size) } catch (e: Exception) { null }
    }

    fun exists(name: String?): Boolean =
        !name.isNullOrBlank() && File(dir, name).exists()

    /** 刪除單一影像（保存期限清理／撤回同意時用）。回 true 表示有刪到。 */
    fun delete(name: String?): Boolean =
        !name.isNullOrBlank() && File(dir, name).let { it.exists() && it.delete() }

    /** 目前佔用位元組數（設定頁顯示用）。 */
    fun totalBytes(): Long = dir.listFiles()?.sumOf { it.length() } ?: 0L

    fun fileCount(): Int = dir.listFiles()?.size ?: 0
}
