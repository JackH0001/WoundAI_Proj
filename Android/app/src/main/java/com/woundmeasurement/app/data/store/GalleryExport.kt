package com.woundmeasurement.app.data.store

import android.content.ContentValues
import android.content.Context
import android.graphics.Bitmap
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import com.woundmeasurement.app.BuildConfig
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

    /**
     * 內測臨床影像用的**另一個**資料夾。刻意跟驗證圖分開，理由是harm reduction：
     * 內測結束時測試者要能「整個資料夾刪掉」，而不必在一堆範例圖裡逐張辨認哪些是病人的。
     */
    private const val ALBUM_CLINICAL = "WoundAI_內測臨床"

    /** 這兩種來源不含任何人的個資，任何建置都可以寫進共用相簿。 */
    private val ALLOWED_SOURCES = setOf("sample", "phantom")

    /**
     * 這個建置是否允許把**臨床**影像寫進共用相簿。
     *
     * 只有 `internalTest` build type 會是 true（見 app/build.gradle）。
     * 刻意不做成執行期開關或設定頁選項——執行期開關意味著「正式版裡存在一條
     * 可以被打開的路」，而這條路一旦被打開就不可逆（影像同步上雲後，病患撤回
     * 同意時我們刪不掉）。編譯期決定讓正式版**根本不含**這段行為。
     */
    private val clinicalExportAllowed: Boolean
        get() = BuildConfig.ALLOW_CLINICAL_GALLERY_EXPORT

    /**
     * 統一的匯出入口與**唯一的**政策判斷處。
     *
     * 呼叫端不需要（也不應該）自己判斷能不能匯出——所有 `if (!clinicalMode)` 之類的
     * UI 層判斷都只是為了給使用者可讀的理由，真正的拒絕一律在這裡。
     *
     * @return 顯示用相對路徑；被政策拒絕或寫入失敗皆回 null。
     */
    fun saveCapture(ctx: Context, bitmap: Bitmap, source: String?): String? {
        // fail-closed：來源不明（null）一律拒絕。預設放行才是危險的那一邊。
        val album = when {
            source == null -> return null
            source in ALLOWED_SOURCES -> ALBUM
            source == "clinical" && clinicalExportAllowed -> ALBUM_CLINICAL
            else -> return null
        }
        return write(ctx, bitmap, source, album)
    }

    /**
     * 舊名稱，保留給既有呼叫端（MeasureValidationEntry 的「存入時間軸」）。
     * 政策與 [saveCapture] 完全相同——不要在這裡重複判斷，否則兩處遲早會分岔。
     */
    fun saveForQuickMeasure(ctx: Context, bitmap: Bitmap, source: String?): String? =
        saveCapture(ctx, bitmap, source)

    /**
     * 匯出**量測結果疊圖**（原圖＋醫師確認的組織分區＋輪廓＋校正框＋結果標註帶）。
     *
     * 政策與 [saveCapture] 完全共用——疊圖含有與原圖相同的臨床影像內容，
     * 不能因為它「多畫了幾條線」就套比較寬的規則。檔名加 `_overlay` 後綴，
     * 讓測試者在相簿裡一眼分得出哪張是原圖、哪張是判讀結果。
     *
     * 疊圖本身由 [com.woundmeasurement.app.pipeline.WoundOverlayRenderer] 產生；
     * 這裡只負責政策與 I/O。
     */
    fun saveOverlay(ctx: Context, bitmap: Bitmap, source: String?): String? {
        val album = when {
            source == null -> return null
            source in ALLOWED_SOURCES -> ALBUM
            source == "clinical" && clinicalExportAllowed -> ALBUM_CLINICAL
            else -> return null
        }
        return write(ctx, bitmap, "${source}_overlay", album)
    }

    /** 實際寫入。政策已在 [saveCapture] 判完，這裡只負責 I/O。 */
    private fun write(ctx: Context, bitmap: Bitmap, source: String, album: String): String? {
        return try {
            val stamp = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(Date())
            val name = "woundai_${source}_$stamp.jpg"
            val rel = "${Environment.DIRECTORY_PICTURES}/$album"
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
