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

    private static let suite = "woundai_settings"

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

    /**
     預設後端位址。

     Release 走 Cloud Run（彰化區）；Debug 走 localhost，讓開發時不必動設定。
     ⚠ iOS 模擬器的 `localhost` 就是 Mac 本機，**不是** Android 的 `10.0.2.2`——
     兩邊的預設值不能互抄。
     */
    static var defaultURL: String {
        #if DEBUG
        return "http://localhost:5000"
        #else
        return "https://wound-ai-867037876992.asia-east1.run.app"
        #endif
    }

    // MARK: - 後端連線

    static func backendURL() -> String {
        let s = (d.string(forKey: K.baseUrl) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        return s.isEmpty ? defaultURL : normalize(s)
    }

    static func setBackendURL(_ raw: String) {
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

    static func backendUser() -> String { return d.string(forKey: K.user) ?? "" }

    /// 解不開就回空字串——回舊值或 nil 會讓呼叫端拿著壞掉的憑證反覆重試。
    static func backendPassword() -> String {
        guard let enc = d.string(forKey: K.passEnc), !enc.isEmpty else { return "" }
        return PhiCrypto.decrypt(enc) ?? ""
    }

    /// - Returns: 加密失敗回 `false`，且**不寫入明文**。
    @discardableResult
    static func setCredentials(user: String, password: String) -> Bool {
        do {
            let enc = try PhiCrypto.encrypt(password)
            d.set(user, forKey: K.user)
            d.set(enc ?? "", forKey: K.passEnc)
            return true
        } catch {
            return false
        }
    }

    static func clearCredentials() {
        d.removeObject(forKey: K.user)
        d.removeObject(forKey: K.passEnc)
    }

    // MARK: - 待重試佇列
    //
    // ⚠ **撤回與重新取得必須是兩個獨立佇列。**
    //
    // 合成一個的話，「先撤回後重簽」與「先重簽後撤回」會得到同樣的待辦清單，
    // 而那兩者的正確結果剛好相反——重播時無從得知病患最後的意思是什麼。

    static func pendingWithdrawals() -> Set<String> { return readSet(K.pendingWithdraw) }
    static func pendingRestores() -> Set<String>    { return readSet(K.pendingRestore) }

    static func addPendingWithdrawals(_ codes: [String]) { addToSet(K.pendingWithdraw, codes) }
    static func addPendingRestores(_ codes: [String])    { addToSet(K.pendingRestore, codes) }

    static func clearPendingWithdrawal(_ code: String) { removeFromSet(K.pendingWithdraw, code) }
    static func clearPendingRestore(_ code: String)    { removeFromSet(K.pendingRestore, code) }

    private static func readSet(_ key: String) -> Set<String> {
        return Set(d.stringArray(forKey: key) ?? [])
    }

    private static func addToSet(_ key: String, _ codes: [String]) {
        var s = readSet(key)
        for c in codes where !c.trimmingCharacters(in: .whitespaces).isEmpty { s.insert(c) }
        d.set(Array(s).sorted(), forKey: key)
    }

    private static func removeFromSet(_ key: String, _ code: String) {
        var s = readSet(key)
        s.remove(code)
        d.set(Array(s).sorted(), forKey: key)
    }

    /// 測試用。
    static func resetAllForTesting() {
        for k in [K.baseUrl, K.user, K.passEnc, K.pendingWithdraw, K.pendingRestore] {
            d.removeObject(forKey: k)
        }
    }
}
