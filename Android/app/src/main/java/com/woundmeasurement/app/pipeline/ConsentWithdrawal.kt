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
    private val processing = kotlinx.coroutines.sync.Mutex()

    /** 單筆結果。`ok=false` 時代碼已經進入待重試佇列。 */
    data class Result(val done: List<String>, val pending: List<String>) {
        val allOk: Boolean get() = pending.isEmpty()
    }

    /** 同步方向。撤回與重新取得必須分開兩個佇列，兩者的正確結果剛好相反。 */
    private enum class Dir { WITHDRAW, RESTORE }

    /**
     * 對後端撤回一批代碼。失敗的會記進待重試佇列。
     *
     * ⚠ 不丟例外。呼叫端是「病患剛剛撤回同意」的當下，
     * 那一刻拋例外只會讓畫面顯示一個技術訊息，而該做的事一件都沒做。
     */
    suspend fun pushToBackend(ctx: Context, codes: Collection<String>, runtime: MedicalRuntime = MedicalRuntime(ctx)): Result =
        sync(ctx, codes, Dir.WITHDRAW, runtime)

    /**
     * 重新取得同意 → 解除雲端的撤回封鎖。
     *
     * ⚠ 沒有這一步，撤回就是**死局**：病患改變主意重新簽署後，App 顯示「訓練同意✓」，
     * 而雲端仍以「已撤回訓練同意」擋下每一次送出——錯誤訊息還會把內部端點路徑
     * `/api/v1/consent/restore` 直接印給醫師看，而他沒有辦法自己呼叫它。
     *
     * 2026-08-07 實測到這個狀態：test001 撤回後重新簽署，重新修邊都成功，
     * 但補送標註一律被擋，畫面上完全看不出該怎麼辦。
     */
    suspend fun restoreOnBackend(ctx: Context, codes: Collection<String>, runtime: MedicalRuntime = MedicalRuntime(ctx)): Result =
        sync(ctx, codes, Dir.RESTORE, runtime)

    private suspend fun sync(ctx: Context, codes: Collection<String>, dir: Dir, runtime: MedicalRuntime, newIntent:Boolean=true): Result {
        val list = codes.filter { it.isNotBlank() }.distinct()
        val queue = if (dir == Dir.WITHDRAW) com.woundmeasurement.app.data.store.MedicalAccountPreferences.ConsentQueue.WITHDRAWAL
            else com.woundmeasurement.app.data.store.MedicalAccountPreferences.ConsentQueue.RESTORE
        val current = runtime.operation()
        // Persist before the first suspension; a closed environment must keep its own pending work.
        if(newIntent) runtime.accounts.add(list,queue)
        val tokens=list.mapNotNull { code -> runtime.accounts.token(code,queue)?.let {code to it} }.toMap()
        return withContext(Dispatchers.IO) {
            if(runtime.accounts.retryAfter(queue)>System.currentTimeMillis() || !processing.tryLock()) return@withContext Result(emptyList(),list)
            try {
            runtime.accounts.deferRetry(queue,System.currentTimeMillis()+60_000)
            if (list.isEmpty()) return@withContext Result(emptyList(), emptyList())
            if (!current()) return@withContext Result(emptyList(), list)
            val backend = runCatching { runtime.acquire() }.onFailure { failure ->
                if(failure is ResearchCapacityException) runtime.accounts.deferRetry(queue,failure.notBefore)
            }.getOrNull()
                ?: return@withContext Result(emptyList(), list)
            val done = ArrayList<String>(); val fail = ArrayList<String>()
            var capacityWaiting=false
            for (code in list) {
                val token=tokens[code] ?: continue
                if(runtime.accounts.token(code,queue)!=token) continue
                val ok = current() && !capacityWaiting && runCatching {
                    if(dir==Dir.RESTORE && newIntent && runtime.accounts.consentRevision(code,token)==null) {
                        val revision=backend.consentRevision(code)
                        if(!current() || !runtime.accounts.pinConsentRevision(revision,code,token)) return@runCatching false
                    }
                    if(runtime.accounts.token(code,queue)!=token) return@runCatching false
                    if (dir == Dir.WITHDRAW) backend.withdrawConsent(code,token)
                    else backend.restoreConsent(code,token,runtime.accounts.consentRevision(code,token))
                }.onFailure { failure ->
                    if(failure is ResearchCapacityException) {
                        capacityWaiting=true;runtime.accounts.deferRetry(queue,failure.notBefore)
                    }
                }.getOrDefault(false)
                if(runtime.accounts.token(code,queue)!=token) continue
                if (ok && current()) { done.add(code); runtime.accounts.remove(code, queue, token) }
                else fail.add(code)
            }
            if(fail.isEmpty()) runtime.accounts.deferRetry(queue,0)
            Result(done, fail)
            } finally { processing.unlock() }
        }
    }

    /**
     * 重試佇列裡所有未完成的撤回。連得上就做，連不上就維持原狀。
     *
     * 呼叫時機：進入個案管理頁、設定頁連線測試成功之後。
     * 這兩處都是「使用者剛好有網路而且在等畫面」的時刻，補做不會被感知到。
     */
    suspend fun retryPending(ctx: Context, runtime: MedicalRuntime = MedicalRuntime(ctx)): Result {
        // 兩個方向都要補做。只補撤回的話，重新簽署的病患會永遠卡在「雲端說已撤回」。
        val wd = runtime.accounts.pending(com.woundmeasurement.app.data.store.MedicalAccountPreferences.ConsentQueue.WITHDRAWAL)
        val a = if (wd.isEmpty()) Result(emptyList(), emptyList()) else sync(ctx, wd, Dir.WITHDRAW, runtime, false)
        val rs = runtime.accounts.pending(com.woundmeasurement.app.data.store.MedicalAccountPreferences.ConsentQueue.RESTORE)
        val b = if (rs.isEmpty()) Result(emptyList(), emptyList()) else sync(ctx, rs, Dir.RESTORE, runtime, false)
        return Result(a.done + b.done, a.pending + b.pending)
    }

    /** 給畫面用的提示。沒有待辦時回 null。 */
    fun pendingBanner(ctx: Context, runtime: MedicalRuntime = MedicalRuntime(ctx)): String? {
        val wd = runtime.accounts.pending(com.woundmeasurement.app.data.store.MedicalAccountPreferences.ConsentQueue.WITHDRAWAL)
        val rs = runtime.accounts.pending(com.woundmeasurement.app.data.store.MedicalAccountPreferences.ConsentQueue.RESTORE)
        if (wd.isEmpty() && rs.isEmpty()) return null
        // 兩種待辦的後果完全不同，訊息必須分開講：
        //   撤回沒同步 → 病患已撤回但資料還會進訓練集（違反同意書承諾）
        //   重簽沒同步 → 病患已同意但送不出去（醫師會以為系統壞了）
        val parts = ArrayList<String>()
        if (wd.isNotEmpty()) parts.add(
            "⚠ 有 ${wd.size} 筆撤回尚未同步到雲端：${wd.sorted().joinToString("、")}\n" +
            "尚未取得雲端停止使用的確認，請優先完成撤回核對。")
        if (rs.isNotEmpty()) parts.add(
            "⚠ 有 ${rs.size} 筆重新簽署尚未同步到雲端：${rs.sorted().joinToString("、")}\n" +
            "雲端尚未確認恢復。若同意版次已變動或先前離線，請重新開啟同意表單核對並確認；重試不會自動解除新的撤回。")
        parts.add("請確認網路與後端帳密後重試；若持續失敗，請由管理者到主控台處理。")
        return parts.joinToString("\n")
    }
}
