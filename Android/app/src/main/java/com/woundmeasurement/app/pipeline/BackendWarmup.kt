package com.woundmeasurement.app.pipeline

import android.content.Context
import android.util.Log
import com.woundmeasurement.app.data.store.AppSettings
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import java.util.concurrent.TimeUnit

/**
 * 後端暖機。
 *
 * ## 為什麼需要
 *
 * Cloud Run 設 `min-instances=0`（閒置不計費），代表**閒置一段時間後第一個請求要等容器啟動**——
 * 載入 82 MB 的 ONNX 模型，實測 10–30 秒。醫師按下拍照之後盯著轉圈 30 秒，
 * 在病房裡就是「這個 App 很慢」，而且他不會知道下一次其實只要 1 秒。
 *
 * ## 為什麼是「App 啟動時打，而不是量測前檢查」
 *
 * 量測前才檢查等於把等待搬到最不能等的那一刻。而使用者從開 App 到真的按下拍照，
 * 中間要選病患、確認同意、選傷口——那段時間足夠容器完成啟動。
 * 所以這裡是**背景先喚醒**，不是先確認再讓人等。
 *
 * 用 `/api/health`：它不需要 JWT（暖機不該依賴登入狀態），而且回應輕。
 * 打不通完全沒關係——暖機失敗不影響任何功能，真正的錯誤處理在各自的呼叫點。
 *
 * ## 更好的解法（不在 App 這一側）
 *
 * 門診時段用 Cloud Scheduler 每 10 分鐘打一次 `/api/health`（免費額度內），
 * 容器就幾乎不會被回收。見 `docs/deploy_cloudrun.md` §4。
 * App 端的暖機是那之外的第二層保險，不是替代品。
 */
object BackendWarmup {

    private const val TAG = "BackendWarmup"

    // 獨立的短逾時 client：暖機失敗不該讓任何東西等。
    private val http = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(45, TimeUnit.SECONDS)   // 冷啟動本來就慢，讀取要給夠
        .build()

    @Volatile private var lastOkAt: Long = 0L

    /**
     * 最近一次 `/api/health` 回報的降級原因；服務正常時為 null。
     *
     * ⚠ 這與下面 `ping` 註解裡「暖機失敗不在這裡跳警告」是**兩件不同的事**，
     * 不要一起處理：
     *
     *  · 連不上 → 使用者按下量測時自然會看到錯誤，開 App 時先嚇一次沒有幫助。
     *  · **降級 → 服務會照樣回 200、面積照樣有數字**，而分割模型或色準模組沒載到。
     *    沒有任何後續步驟會報錯，醫師會拿到一個看起來正常的錯答案。
     *    這種故障不講出來就永遠不會被發現。
     */
    @Volatile private var degradedReason: String? = null

    /** 給畫面用的降級橫幅；服務正常或問不到時回 null。 */
    fun degradedBanner(): String? = degradedReason?.let {
        "⚠ 後端服務降級：$it\n" +
        "此狀態下的面積或組織判讀不具參考價值，請先不要用於臨床決策，並通知管理者。"
    }

    /** 距離最近一次成功接觸後端過了多久（毫秒）。未曾成功則回 Long.MAX_VALUE。 */
    fun sinceLastOkMs(): Long =
        if (lastOkAt == 0L) Long.MAX_VALUE else System.currentTimeMillis() - lastOkAt

    /**
     * 背景喚醒後端。**永不拋例外、永不阻塞使用者**。
     *
     * @return true 表示後端已回應（容器是熱的或剛被喚醒）
     */
    suspend fun ping(ctx: Context): Boolean = withContext(Dispatchers.IO) {
        val base = AppSettings.backendUrl(ctx)
        if (base.isBlank()) return@withContext false
        try {
            val req = Request.Builder().url("$base/api/health").get().build()
            http.newCall(req).execute().use { resp ->
                val ok = resp.isSuccessful
                if (ok) {
                    lastOkAt = System.currentTimeMillis()
                    // 反正回應已經在手上，順手把降級狀態解出來——不必為此多打一次。
                    degradedReason = runCatching {
                        val j = org.json.JSONObject(resp.body!!.string())
                        if (j.optString("status") == "degraded")
                            j.optString("degraded_reason").ifBlank { "原因未提供" }
                        else null
                    }.getOrNull()
                }
                Log.d(TAG, "warmup $base → ${resp.code}${if (degradedReason != null) " (degraded)" else ""}")
                ok
            }
        } catch (e: Exception) {
            // 沒網路、位址沒設好、後端沒起來——全都不在這裡處理。
            // 暖機是最佳努力，真正的錯誤訊息要出現在使用者實際操作的那一步，
            // 在這裡跳警告只會在開 App 時嚇人一次然後被忽略。
            Log.d(TAG, "warmup 失敗（忽略）: ${e.message}")
            false
        }
    }
}
