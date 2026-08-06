package com.woundmeasurement.app.data.store

import android.content.Context
import com.woundmeasurement.app.data.crypto.PhiCrypto

/**
 * 後端連線設定（本機、可設定、憑證加密）。
 *
 * ## 為什麼需要這一層
 *
 * 在此之前後端位址寫死成 `http://10.0.2.2:5000`，帳密寫死成 `admin` / `woundai-admin`。
 * 兩者都只在模擬器上成立，而 n=20 臨床收案**必須在真機上做**（模擬器沒有相機，也拍不到 ArUco 標記）：
 *
 *  - `10.0.2.2` 是 **Android 模擬器專用**的 loopback 別名，對映到開發機的 `127.0.0.1`。
 *    真機上這個位址什麼都不是 → classify 與補送標註全部失敗，而且錯誤訊息只會說「後端未連線」。
 *  - 明文帳密編進 APK 等於把後端鑰匙印在 App 上。APK 可以反編譯，帶去醫院就是把憑證一起帶出門。
 *
 * ## 憑證怎麼存
 *
 * 帳號存明文（它本身不是秘密，且要顯示在設定頁讓人確認打對了）；
 * **密碼以 [PhiCrypto] 加密**後才寫進 SharedPreferences，和病患姓名同一把 Keystore 金鑰、
 * 同樣不可匯出。SharedPreferences 的 XML 在 root 或備份萃取下是讀得到的，明文存等於沒存。
 *
 * ⚠ **誠實邊界**：這解決的是「憑證不隨 APK 散佈、不以明文落地」。它不是完整的身分驗證方案——
 * 真正的正解是後端改走 OIDC/院內 SSO 並發短效 token，讓 App 完全不碰密碼。列為後續。
 *
 * ## 為什麼用 SharedPreferences 而不是 DataStore
 *
 * DataStore 要新增相依。這裡只有四個純量欄位、讀取都在畫面啟動時做一次，
 * SharedPreferences 完全夠用，而他們的 AGP/Kotlin 組合剛穩定下來，不值得為此再動 Gradle。
 */
object AppSettings {

    private const val PREF = "woundai_settings"
    private const val K_URL = "backend_base_url"
    private const val K_USER = "backend_user"
    private const val K_PASS_ENC = "backend_pass_enc"

    /**
     * 尚未成功送到後端的「撤回訓練同意」代碼。
     *
     * ## 為什麼需要這個佇列
     *
     * 病患撤回同意是**立即生效的權利**，不能因為手機當下沒網路就拒絕他。
     * 所以本機撤回一定要先成功。但雲端那一邊沒撤回的話，他的資料照樣會進訓練集——
     * 那正是同意書承諾不會發生的事。
     *
     * 兩者的解法是：本機立刻生效、雲端撤回失敗就記在這裡，之後每次連上就重試，
     * 而且**在撤回成功之前一直顯示出來**。
     *
     * ⚠ 只存 `WD-` 代碼，不含任何個資——代碼本身就是去識別後的識別子。
     * 這是唯一可以放進 SharedPreferences 的原因。
     */
    private const val K_PENDING_WD = "pending_withdrawals"

    fun pendingWithdrawals(ctx: Context): Set<String> =
        prefs(ctx).getStringSet(K_PENDING_WD, emptySet()) ?: emptySet()

    fun addPendingWithdrawals(ctx: Context, codes: Collection<String>) {
        if (codes.isEmpty()) return
        // getStringSet 回的可能是內部共用實例，直接改它的行為未定義——一定要複製一份。
        val next = pendingWithdrawals(ctx).toMutableSet().apply { addAll(codes) }
        prefs(ctx).edit().putStringSet(K_PENDING_WD, next).apply()
    }

    fun clearPendingWithdrawal(ctx: Context, code: String) {
        val next = pendingWithdrawals(ctx).toMutableSet().apply { remove(code) }
        prefs(ctx).edit().putStringSet(K_PENDING_WD, next).apply()
    }

    /**
     * 預設後端位址由建置型別決定（`build.gradle` 的 `buildConfigField`）：
     * release ＝ 雲端網址，debug ＝ 模擬器 loopback。
     *
     * 之所以編進 APK：測試者裝完 App 的第一件事若是手打一串 40 字元的網址，
     * 就會有人打錯而看到「後端未連線」，然後把它回報成「App 壞了」。
     * 網址不是機密——沒有 token 進不去任何端點。
     *
     * 設定頁仍可個別覆寫（存 SharedPreferences），且**已存過的值優先於預設值**：
     * 升級 App 不會把使用者自己設的位址蓋掉。
     */
    val DEFAULT_URL: String = com.woundmeasurement.app.BuildConfig.DEFAULT_BACKEND_URL

    private fun prefs(ctx: Context) =
        ctx.applicationContext.getSharedPreferences(PREF, Context.MODE_PRIVATE)

    fun backendUrl(ctx: Context): String =
        prefs(ctx).getString(K_URL, null)?.takeIf { it.isNotBlank() } ?: DEFAULT_URL

    fun setBackendUrl(ctx: Context, url: String) {
        prefs(ctx).edit().putString(K_URL, normalizeUrl(url)).apply()
    }

    fun backendUser(ctx: Context): String = prefs(ctx).getString(K_USER, "") ?: ""

    /** 解不開就回空字串（金鑰換新＝App 重裝／還原備份）→ 呼叫端會走到「請重新輸入密碼」。 */
    fun backendPassword(ctx: Context): String {
        val enc = prefs(ctx).getString(K_PASS_ENC, null) ?: return ""
        return runCatching { PhiCrypto.decrypt(enc) }.getOrNull() ?: ""
    }

    /**
     * 存憑證。加密失敗時**寧可不存也不存明文**——存明文會讓後續每一次讀取都以為它是安全的。
     * @return true 表示真的存進去了
     */
    fun setCredentials(ctx: Context, user: String, password: String): Boolean {
        val enc = runCatching { PhiCrypto.encrypt(password) }.getOrNull() ?: return false
        prefs(ctx).edit().putString(K_USER, user.trim()).putString(K_PASS_ENC, enc).apply()
        return true
    }

    fun hasCredentials(ctx: Context): Boolean =
        backendUser(ctx).isNotBlank() && prefs(ctx).getString(K_PASS_ENC, null) != null

    fun clearCredentials(ctx: Context) {
        prefs(ctx).edit().remove(K_USER).remove(K_PASS_ENC).apply()
    }

    /**
     * `10.0.2.2` 只在 Android 模擬器裡有意義。真機上留著它，使用者會看到「後端未連線」
     * 卻完全猜不到原因——這正是設定頁要主動點出來的事。
     */
    fun isEmulatorLoopback(url: String): Boolean = url.contains("10.0.2.2")

    /**
     * 判斷目前是否跑在模擬器上。用來決定要不要對 `10.0.2.2` 提出警告。
     *
     * 這是啟發式判斷（Build 指紋），不是保證正確；但它只影響「要不要顯示一句提示」，
     * 判斷錯了不會擋住任何操作，所以這個精確度是足夠的。
     */
    fun looksLikeEmulator(): Boolean {
        val fp = android.os.Build.FINGERPRINT ?: ""
        return fp.contains("generic") || fp.contains("emulator") || fp.contains("sdk") ||
               (android.os.Build.MODEL ?: "").contains("sdk_gphone")
    }

    /**
     * 補 scheme、去尾斜線。
     *
     * 使用者在醫院現場多半只會打 `192.168.1.50:5000`——少了 `http://` 的話 OkHttp 會直接拋
     * `IllegalArgumentException: Expected URL scheme`，而畫面只會說「連線錯誤」。
     * 尾斜線則會讓 `"$baseUrl/api/v1/classify"` 變成 `//api/v1/classify`，有些反向代理會 404。
     */
    fun normalizeUrl(raw: String): String {
        var u = raw.trim()
        if (u.isEmpty()) return u
        if (!u.startsWith("http://") && !u.startsWith("https://")) u = "http://$u"
        return u.trimEnd('/')
    }
}
