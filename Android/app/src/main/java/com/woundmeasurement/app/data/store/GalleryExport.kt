package com.woundmeasurement.app.data.store

import android.content.ContentValues
import android.content.Context
import android.graphics.Bitmap
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * 把影像寫進**手機共用相簿**（快速量測專用）。
 *
 * ## 為什麼要有
 *
 * 快速量測（範例／模擬圖驗證）拍完之後，原始影像只以密文存在 App 私有目錄，
 * 使用者拿不到、也無法事後比對或匯入個案。驗證工作需要看得到原圖。
 *
 * ## ⚠ 為什麼**只有**快速量測可以用（這是硬性限制，不是慣例）
 *
 * 寫進共用相簿等於把影像丟出這個 App 的控制範圍。具體後果：
 *
 * | 後果 | 為什麼致命 |
 * |---|---|
 * | 任何有相片權限的 App 都讀得到 | 傷口影像屬特種個資，這是最直接的外洩路徑 |
 * | 會被 Google 相簿等自動同步上雲 | 目的地不可控，且多半在境外——違反「醫療雲端儲存以境內為原則」 |
 * | App 移除後仍留在裝置 | 逃出 `LocalImageStore` 的 Keystore 加密與生命週期 |
 * | 不受 90 天保存期限清理 | `CaseRepository.purgeExpiredImages` 碰不到它 |
 * | 不受撤回同意約束 | 病患撤回時我們刪得掉飛輪與本機副本，刪不掉相簿裡的 |
 *
 * 所以 [saveForQuickMeasure] 明確要求 `source`，且**只接受 sample／phantom**。
 * 臨床影像傳進來會直接回 null 並拒絕寫入——不是靠呼叫端記得別呼叫。
 *
 * ## 誠實邊界
 *
 * 程式擋得住「臨床模式呼叫它」，擋不住「有人在快速量測裡拍真實病人的傷口」。
 * 那是流程與教育訓練的問題（已列入 `docs/clinical_pilot_20_SOP.md`），
 * 而這個限制本身就是為什麼快速量測入口刻意隱藏「臨床」來源選項。
 */
object GalleryExport {

    /** 相簿子資料夾。集中一處，使用者要清理時找得到、刪得掉。 */
    private const val ALBUM = "WoundAI_驗證"

    /** 只有這兩種來源可以寫進共用相簿——它們不含任何人的個資。 */
    private val ALLOWED_SOURCES = setOf("sample", "phantom")

    /**
     * 存入共用相簿。回傳顯示用的相對路徑，失敗或來源不允許回 null。
     *
     * @param source 樣本來源。**必須**是 `sample` 或 `phantom`；其餘一律拒絕。
     */
    fun saveForQuickMeasure(ctx: Context, bitmap: Bitmap, source: String?): String? {
        // fail-closed：來源不明（null）也拒絕。預設放行才是危險的那一邊。
        if (source == null || source !in ALLOWED_SOURCES) return null
        return try {
            val stamp = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(Date())
            val name = "woundai_${source}_$stamp.jpg"
            val rel = "${Environment.DIRECTORY_PICTURES}/$ALBUM"
            val values = ContentValues().apply {
                put(MediaStore.Images.Media.DISPLAY_NAME, name)
                put(MediaStore.Images.Media.MIME_TYPE, "image/jpeg")
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                    put(MediaStore.Images.Media.RELATIVE_PATH, rel)
                    // IS_PENDING：寫入期間其他 App 看不到半成品。寫完才翻成可見，
                    // 否則相簿可能掃到一個 0 byte 的檔案。
                    put(MediaStore.Images.Media.IS_PENDING, 1)
                }
            }
            val resolver = ctx.contentResolver
            val uri = resolver.insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, values)
                ?: return null
            resolver.openOutputStream(uri)?.use { out ->
                bitmap.compress(Bitmap.CompressFormat.JPEG, 92, out)
            } ?: return null
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                values.clear()
                values.put(MediaStore.Images.Media.IS_PENDING, 0)
                resolver.update(uri, values, null, null)
            }
            "$rel/$name"
        } catch (e: Exception) {
            // 寫相簿失敗不該中斷量測流程——它是輔助功能，不是必要路徑。
            null
        }
    }
}
