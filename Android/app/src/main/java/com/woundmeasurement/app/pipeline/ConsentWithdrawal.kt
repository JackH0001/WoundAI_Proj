package com.woundmeasurement.app.pipeline

import android.content.Context
import com.woundmeasurement.app.data.store.AppSettings
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * 撤回訓練同意的**雲端側**執行與重試。
 *
 * ## 這裡修掉的是一個病患權利層級的缺口
 *
 * 2026-08-06 以前：`CaseRepository.withdrawTraining()` 只在本機標記，
 * 畫面顯示「⚠ 尚須對後端撤回這些代碼…（本機留痕只是一半）」，
 * 然後**就沒有然後了**——`BackendClient.withdrawConsent()` 存在，但沒有任何呼叫端。
 *
 * 結果是：病患撤回了同意，資料仍留在雲端的訓練佇列裡，除非剛好有人看到那行提示、
 * 把代碼抄下來、登入主控台手動處理。同意書上寫的是「撤回後不再納入後續訓練」——
 * 那句話當時做不到。
 *
 * ## 設計：本機立即生效、雲端盡力而為、失敗要看得見
 *
 * 三者的優先序不能顛倒：
 *
 *  1. **本機撤回一定先成功。** 病患的撤回是立即生效的權利，不能因為手機沒訊號就拒絕。
 *  2. **雲端撤回失敗不可以假裝成功。** 顯示「已撤回」而雲端還留著，比誠實地說
 *     「本機已撤回，雲端尚未完成」糟得多——後者至少有人會去處理。
 *  3. **待辦要持久化並反覆出現。** 一次性的錯誤訊息會被滑掉；
 *     沒有完成的撤回必須一直看得見，直到它真的完成。
 */
object ConsentWithdrawal {

    /** 單筆結果。`ok=false` 時代碼已經進入待重試佇列。 */
    data class Result(val done: List<String>, val pending: List<String>) {
        val allOk: Boolean get() = pending.isEmpty()
    }

    /**
     * 對後端撤回一批代碼。失敗的會記進待重試佇列。
     *
     * ⚠ 不丟例外。呼叫端是「病患剛剛撤回同意」的當下，
     * 那一刻拋例外只會讓畫面顯示一個技術訊息，而該做的事一件都沒做。
     */
    suspend fun pushToBackend(ctx: Context, codes: Collection<String>): Result =
        withContext(Dispatchers.IO) {
            val list = codes.filter { it.isNotBlank() }.distinct()
            if (list.isEmpty()) return@withContext Result(emptyList(), emptyList())

            val url = AppSettings.backendUrl(ctx)
            val user = AppSettings.backendUser(ctx)
            val pass = AppSettings.backendPassword(ctx)
            if (user.isBlank() || pass.isBlank()) {
                AppSettings.addPendingWithdrawals(ctx, list)
                return@withContext Result(emptyList(), list)
            }

            val backend = BackendClient(url)
            val loggedIn = runCatching { backend.login(user, pass) }.getOrDefault(false)
            if (!loggedIn) {
                AppSettings.addPendingWithdrawals(ctx, list)
                return@withContext Result(emptyList(), list)
            }

            val done = ArrayList<String>()
            val fail = ArrayList<String>()
            for (c in list) {
                // 逐筆處理而非整批：一個代碼失敗不該讓其他的也留在佇列裡，
                // 而重試時只重試真正沒完成的那些。
                val ok = runCatching { backend.withdrawConsent(c) }.getOrDefault(false)
                if (ok) {
                    done.add(c)
                    AppSettings.clearPendingWithdrawal(ctx, c)
                } else {
                    fail.add(c)
                }
            }
            if (fail.isNotEmpty()) AppSettings.addPendingWithdrawals(ctx, fail)
            Result(done, fail)
        }

    /**
     * 重試佇列裡所有未完成的撤回。連得上就做，連不上就維持原狀。
     *
     * 呼叫時機：進入個案管理頁、設定頁連線測試成功之後。
     * 這兩處都是「使用者剛好有網路而且在等畫面」的時刻，補做不會被感知到。
     */
    suspend fun retryPending(ctx: Context): Result {
        val pending = AppSettings.pendingWithdrawals(ctx)
        if (pending.isEmpty()) return Result(emptyList(), emptyList())
        return pushToBackend(ctx, pending)
    }

    /** 給畫面用的提示。沒有待辦時回 null。 */
    fun pendingBanner(ctx: Context): String? {
        val p = AppSettings.pendingWithdrawals(ctx)
        if (p.isEmpty()) return null
        return "⚠ 有 ${p.size} 筆撤回尚未同步到雲端：${p.sorted().joinToString("、")}\n" +
               "這些代碼的資料目前**仍在雲端訓練佇列中**。請確認網路與後端帳密後重試；" +
               "若持續失敗，請由管理者到主控台手動撤回。"
    }
}
