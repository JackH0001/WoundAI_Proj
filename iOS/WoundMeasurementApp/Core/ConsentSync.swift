import Foundation

/**
 撤回／重新取得訓練同意的**雲端側**執行與重試（對等 Android `ConsentWithdrawal.kt`）。

 ## 這裡守住的是一個病患權利層級的缺口

 Android 端 2026-08-06 以前：本機標記了撤回，畫面顯示「尚須對後端撤回這些代碼」，
 然後**就沒有然後了**——`withdrawConsent()` 存在，但沒有任何呼叫端。結果是病患撤回了同意，
 資料仍留在雲端的訓練佇列裡。同意書上寫的是「撤回後不再納入後續訓練」，那句話當時做不到。

 隔一天又發現對稱的另一半：`restore` 也沒有呼叫端，於是病患改變主意重新簽署後，
 App 顯示「訓練同意 ✓」而雲端持續擋下每一次送出，錯誤訊息還把內部端點路徑印給醫師看。
 **撤回若不能還原，撤回本身就是死局。**

 iOS 一次把兩個方向都接上。

 ## 設計：本機立即生效、雲端盡力而為、失敗要看得見

 三者的優先序不能顛倒：

 1. **本機撤回一定先成功。** 病患的撤回是立即生效的權利，不能因為手機沒訊號就拒絕。
 2. **雲端撤回失敗不可以假裝成功。** 顯示「已撤回」而雲端還留著，比誠實地說
    「本機已撤回，雲端尚未完成」糟得多——後者至少有人會去處理。
 3. **待辦要持久化並反覆出現。** 一次性的錯誤訊息會被滑掉；沒有完成的撤回必須一直
    看得見，直到它真的完成。
 */
enum ConsentSync {

    /// 單次同步結果。`pending` 非空代表那些代碼已進入待重試佇列。
    struct Result {
        let done: [String]
        let pending: [String]
        var allOK: Bool { return pending.isEmpty }

        static let empty = Result(done: [], pending: [])
    }

    /// 同步方向。**兩個方向必須用兩個佇列**，理由見 `AppSettings`。
    private enum Dir {
        case withdraw
        case restore
    }

    /**
     對後端撤回一批代碼。失敗的會記進待重試佇列。

     ⚠ 不丟例外。呼叫端是「病患剛剛撤回同意」的當下，那一刻拋例外只會讓畫面顯示一個
     技術訊息，而該做的事一件都沒做。
     */
    static func pushToBackend(codes: [String]) async -> Result {
        return await sync(codes, .withdraw)
    }

    /// 重新取得同意 → 解除雲端的撤回封鎖。沒有這一步，撤回就是死局。
    static func restoreOnBackend(codes: [String]) async -> Result {
        return await sync(codes, .restore)
    }

    private static func sync(_ codes: [String], _ dir: Dir) async -> Result {
        var seen = Set<String>()
        let list = codes
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty && seen.insert($0).inserted }
        if list.isEmpty { return .empty }

        func remember(_ cs: [String]) {
            dir == .withdraw ? AppSettings.addPendingWithdrawals(cs)
                             : AppSettings.addPendingRestores(cs)
        }
        func forget(_ c: String) {
            dir == .withdraw ? AppSettings.clearPendingWithdrawal(c)
                             : AppSettings.clearPendingRestore(c)
        }

        let user = AppSettings.backendUser()
        let pass = AppSettings.backendPassword()
        guard !user.isEmpty, !pass.isEmpty else {
            remember(list)
            return Result(done: [], pending: list)
        }

        let backend = BackendClient(baseUrl: AppSettings.backendURL())
        let loggedIn = (try? await backend.login(username: user, password: pass)) ?? false
        guard loggedIn else {
            remember(list)
            return Result(done: [], pending: list)
        }

        var done: [String] = []
        var fail: [String] = []
        for c in list {
            // 逐筆處理而非整批：一個代碼失敗不該讓其他的也留在佇列裡，
            // 而重試時只重試真正沒完成的那些。
            let ok = dir == .withdraw
                ? await backend.withdrawConsent(code: c)
                : await backend.restoreConsent(code: c)
            if ok { done.append(c); forget(c) } else { fail.append(c) }
        }
        if !fail.isEmpty { remember(fail) }
        return Result(done: done, pending: fail)
    }

    /**
     重試佇列裡所有未完成的同步。連得上就做，連不上就維持原狀。

     呼叫時機：進入個案管理頁、設定頁連線測試成功之後。這兩處都是「使用者剛好有網路
     而且在等畫面」的時刻，補做不會被感知到。
     */
    static func retryPending() async -> Result {
        // 兩個方向都要補做。只補撤回的話，重新簽署的病患會永遠卡在「雲端說已撤回」。
        let wd = Array(AppSettings.pendingWithdrawals())
        let rs = Array(AppSettings.pendingRestores())
        if wd.isEmpty && rs.isEmpty { return .empty }
        let a = wd.isEmpty ? Result.empty : await sync(wd, .withdraw)
        let b = rs.isEmpty ? Result.empty : await sync(rs, .restore)
        return Result(done: a.done + b.done, pending: a.pending + b.pending)
    }

    /// 給畫面用的常駐提示。沒有待辦時回 nil。
    static func pendingBanner() -> String? {
        let wd = AppSettings.pendingWithdrawals().sorted()
        let rs = AppSettings.pendingRestores().sorted()
        if wd.isEmpty && rs.isEmpty { return nil }

        // 兩種待辦的後果完全不同，訊息必須分開講：
        //   撤回沒同步 → 病患已撤回但資料還會進訓練集（違反同意書承諾）
        //   重簽沒同步 → 病患已同意但送不出去（醫師會以為系統壞了）
        var parts: [String] = []
        if !wd.isEmpty {
            parts.append("""
            ⚠ 有 \(wd.count) 筆撤回尚未同步到雲端：\(wd.joined(separator: "、"))
            這些代碼的資料目前仍在雲端訓練佇列中。
            """)
        }
        if !rs.isEmpty {
            parts.append("""
            ⚠ 有 \(rs.count) 筆重新簽署尚未同步到雲端：\(rs.joined(separator: "、"))
            這些代碼在雲端仍被標記為已撤回，補送訓練標註會被擋下。
            """)
        }
        parts.append("請確認網路與後端帳密後重試；若持續失敗，請由管理者到主控台處理。")
        return parts.joined(separator: "\n")
    }
}
