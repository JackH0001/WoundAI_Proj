import Foundation

/**
 App 設定與待重試佇列（對等 Android `data/store/AppSettings.kt`）。

 存放位置刻意分開：
 - **後端位址、帳號、待重試佇列** → `UserDefaults`
 - **密碼** → `UserDefaults`，但值以 `PhiCrypto.encrypt` 加密

 密碼加密後仍放 `UserDefaults` 而不是直接進 Keychain，是為了與 Android 端保持同構
 （Android 用 SharedPreferences + Keystore 加密）。金鑰本身在 Keychain 裡，
 且標記 `ThisDeviceOnly`——`UserDefaults` 會進 iTunes/iCloud 備份，金鑰不會，
 所以備份檔裡的密文在別台機器上解不開。
 */
enum AppSettings {

    private static let suite = MedicalDataScope.mmh.settingsSuite
    private static let accounts = MedicalAccountPreferences(scope: .mmh,
        invalidate: { MedicalSessionEpoch.shared.invalidate() })

    private static var d: UserDefaults {
        return UserDefaults(suiteName: suite) ?? UserDefaults.standard
    }

    private enum K {
        static let baseUrl         = "backend_base_url"
        static let user            = "backend_user"
        static let passEnc         = "backend_pass_enc"
        static let pendingWithdraw = "pending_withdrawals"
        static let pendingRestore  = "pending_restores"
    }

    // 醫療 TestFlight 使用隔離示範服務；Lite 的雲端啟用另有發布閘門。
    static let demoURL = "https://woundai-backend-demo-z4kgfkob4a-de.a.run.app"
    private static let legacyURL = "https://woundai-backend-421209514056.asia-east1.run.app"

    static var defaultURL: String {
        #if DEBUG && targetEnvironment(simulator)
        return "http://localhost:5000"
        #elseif WOUND_LITE
        return legacyURL
        #else
        return demoURL
        #endif
    }

    // MARK: - 冷啟動追蹤（對等 Android BackendWarmup.sinceLastOkMs）

    /// 最近一次後端成功回應的時刻。>15 分鐘沒成功過＝Cloud Run 幾乎已縮到零，
    /// 下一次呼叫要吃 10–30 秒冷啟動——提示要在**喚醒前**就講，不是等逾時。
    static func markBackendOk() { d.set(Date().timeIntervalSince1970, forKey: "last_backend_ok") }
    static func backendLikelyCold() -> Bool {
        let t = d.double(forKey: "last_backend_ok")
        return Date().timeIntervalSince1970 - t > 15 * 60
    }

    // MARK: - 後端連線

    static func backendURL() -> String {
        #if WOUND_INSTITUTION
        // Dedicated product cannot reuse a saved production/demo endpoint.
        return (Bundle.main.object(forInfoDictionaryKey: "WoundAIInstitutionBackendURL") as? String).flatMap { value in
            guard let url = URL(string: value), url.scheme == "https",
                  url.host?.hasSuffix(".run.app") == true, url.user == nil, url.password == nil,
                  value != demoURL, value != legacyURL else { return nil as String? }
            return value
        } ?? "https://mmhps20261007.invalid"
        #else
        let s = (d.string(forKey: K.baseUrl) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        if !s.isEmpty { return normalize(s) }
        // 升級不能把既有帳密默默送往另一個平台。舊版若留下帳號但沒有網址，
        // 保留原服務，讓使用者在設定中明確登出並切換。
        if defaultURL == demoURL && !backendUser().isEmpty { return legacyURL }
        return defaultURL
        #endif
    }

    static func setBackendURL(_ raw: String) {
        if normalize(raw) != normalize(backendURL()) { MedicalSessionEpoch.shared.invalidate() }
        d.set(normalize(raw), forKey: K.baseUrl)
    }

    static func normalize(_ raw: String) -> String {
        var s = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if s.isEmpty { return s }
        if !s.lowercased().hasPrefix("http://") && !s.lowercased().hasPrefix("https://") {
            s = "https://" + s
        }
        while s.hasSuffix("/") { s.removeLast() }
        return s
    }

    static func backendUser() -> String { accounts.user() }
    static func backendPassword() -> String { accounts.password() }
    @discardableResult
    static func setCredentials(user: String, password: String) -> Bool {
        accounts.setCredentials(user: user, password: password)
    }
    static func clearCredentials() { accounts.clearCredentials() }

    // MARK: - 待重試佇列
    //
    // ⚠ **撤回與重新取得必須是兩個獨立佇列。**
    //
    // 合成一個的話，「先撤回後重簽」與「先重簽後撤回」會得到同樣的待辦清單，
    // 而那兩者的正確結果剛好相反——重播時無從得知病患最後的意思是什麼。

    static func pendingWithdrawals() -> Set<String> { accounts.pending(.withdrawal) }
    static func pendingRestores() -> Set<String> { accounts.pending(.restore) }
    static func addPendingWithdrawals(_ codes: [String]) { accounts.add(codes, to: .withdrawal) }
    static func addPendingRestores(_ codes: [String]) { accounts.add(codes, to: .restore) }
    static func clearPendingWithdrawal(_ code: String) { accounts.remove(code, from: .withdrawal) }
    static func clearPendingRestore(_ code: String) { accounts.remove(code, from: .restore) }

    /// 測試用。
    static func resetAllForTesting() {
        for k in [K.baseUrl, K.user, K.passEnc, K.pendingWithdraw, K.pendingRestore] {
            d.removeObject(forKey: k)
        }
    }
}
