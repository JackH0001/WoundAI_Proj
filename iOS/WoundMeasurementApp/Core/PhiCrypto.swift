import Foundation
import CryptoKit
import Security

/**
 本機 PHI 欄位加密（對等 Android `data/crypto/PhiCrypto.kt`）。

 兩把金鑰，用途刻意分開：
 - `woundai_phi_v1`      AES-256-GCM，加密姓名／病歷號／簽名 PNG
 - `woundai_mrn_hmac_v1` HMAC-SHA256，產生病歷號指紋供查重

 ## 為什麼病歷號指紋用 HMAC 而不是加鹽 SHA-256

 病歷號的熵極低（多半是院內流水號），而鹽若寫在原始碼裡，對拿到 App 的人就是公開的
 ——字典攻擊幾秒鐘就能把指紋反查回明文。HMAC 的金鑰在 Keychain 裡、不隨 App 二進位
 外流，才真的擋得住反查。

 ## 誠實邊界

 這擋的是「裝置遺失後的靜態外洩」與「備份/同步時的意外散布」。它擋不了在已越獄裝置上
 讀取行程記憶體的攻擊者，也**不等於** HIPAA／GDPR 要求的完整技術保護措施。
 整庫加密（SQLCipher 等）列為後續工作。

 ## 與 Android 的差異（刻意）

 Android 用 AndroidKeyStore（金鑰不可匯出）；iOS 這裡把 256-bit 金鑰以
 `kSecAttrAccessibleWhenUnlockedThisDeviceOnly` 存進 Keychain。`ThisDeviceOnly` 是關鍵：
 少了它，金鑰會隨 iCloud Keychain 同步到使用者的其他裝置，而病歷資料庫不會——
 於是金鑰散布得比它保護的資料還廣。
 */
enum PhiCrypto {

    // MARK: - 常數（與 Android 對齊）

    static let keyAlias = "woundai_phi_v1"
    static let macAlias = "woundai_mrn_hmac_v1"
    /// 密文前綴。用來分辨「v1 時代存進去的明文」與「已加密的值」，讓升級不必搬資料。
    static let prefix = "enc1:"

    enum CryptoError: Error, LocalizedError {
        case keychainFailure(OSStatus)
        case encryptFailed(String)

        var errorDescription: String? {
            switch self {
            case .keychainFailure(let s): return "Keychain 操作失敗（OSStatus \(s)）"
            case .encryptFailed(let m):   return "加密失敗：\(m)"
            }
        }
    }

    // MARK: - 金鑰快取
    //
    // Keychain 每次查詢都是一次跨行程往返。清單畫面解密上百個欄位時，不快取會直接卡住
    // 主執行緒。用鎖保護，因為 Repository 會在背景佇列上併發解密。

    private static let lock = NSLock()
    private static var cachedKey: SymmetricKey?
    private static var cachedMac: SymmetricKey?

    private static func key() throws -> SymmetricKey {
        lock.lock(); defer { lock.unlock() }
        if let k = cachedKey { return k }
        let k = try loadOrCreateKey(tag: keyAlias)
        cachedKey = k
        return k
    }

    private static func macKey() throws -> SymmetricKey {
        lock.lock(); defer { lock.unlock() }
        if let k = cachedMac { return k }
        let k = try loadOrCreateKey(tag: macAlias)
        cachedMac = k
        return k
    }

    /// 測試用：清掉快取，強制下次重讀 Keychain。
    static func resetCacheForTesting() {
        lock.lock(); defer { lock.unlock() }
        cachedKey = nil; cachedMac = nil
    }

    private static func loadOrCreateKey(tag: String) throws -> SymmetricKey {
        let query: [String: Any] = [
            kSecClass as String:       kSecClassGenericPassword,
            kSecAttrService as String: "com.woundmeasurement.app.phi",
            kSecAttrAccount as String: tag,
            kSecReturnData as String:  true,
            kSecMatchLimit as String:  kSecMatchLimitOne
        ]
        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        if status == errSecSuccess, let data = item as? Data, data.count == 32 {
            return SymmetricKey(data: data)
        }
        guard status == errSecItemNotFound else { throw CryptoError.keychainFailure(status) }

        // 不存在 → 產生新金鑰並寫入。
        var bytes = [UInt8](repeating: 0, count: 32)
        let rc = SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes)
        guard rc == errSecSuccess else { throw CryptoError.keychainFailure(rc) }
        let data = Data(bytes)

        let add: [String: Any] = [
            kSecClass as String:       kSecClassGenericPassword,
            kSecAttrService as String: "com.woundmeasurement.app.phi",
            kSecAttrAccount as String: tag,
            kSecValueData as String:   data,
            // ThisDeviceOnly：見檔頭說明，不可拿掉。
            kSecAttrAccessible as String: kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        ]
        let addStatus = SecItemAdd(add as CFDictionary, nil)
        guard addStatus == errSecSuccess || addStatus == errSecDuplicateItem else {
            throw CryptoError.keychainFailure(addStatus)
        }
        return SymmetricKey(data: data)
    }

    // MARK: - 字串加解密

    /**
     加密字串。null／空字串原樣回傳。

     ⚠ **失敗時拋錯，絕不默默回傳明文。** 這裡回傳明文會讓姓名與病歷號以明文寫進資料庫，
     而呼叫端完全看不出來——沒有例外、沒有記錄，只有一個看起來正常的欄位。
     */
    static func encrypt(_ plain: String?) throws -> String? {
        guard let plain = plain, !plain.isEmpty else { return plain }
        do {
            let sealed = try AES.GCM.seal(Data(plain.utf8), using: try key())
            // combined = nonce(12) ‖ ciphertext ‖ tag(16)，與 Android 的 IV‖ct+tag 同構
            guard let combined = sealed.combined else {
                throw CryptoError.encryptFailed("AES.GCM combined 為 nil")
            }
            return prefix + combined.base64EncodedString()
        } catch let e as CryptoError {
            throw e
        } catch {
            throw CryptoError.encryptFailed(String(describing: error))
        }
    }

    /**
     解密。無 `enc1:` 前綴＝v1 時代的明文，原樣回傳（相容既有資料）。

     失敗回 `nil` 而不是拋錯：一筆解不開的舊資料不該讓整個病患清單開不起來。
     呼叫端把 `nil` 顯示為「（無法解密）」，讓它看得見但不致命。
     */
    static func decrypt(_ stored: String?) -> String? {
        guard let stored = stored, !stored.isEmpty else { return stored }
        guard stored.hasPrefix(prefix) else { return stored }   // v1 明文
        let b64 = String(stored.dropFirst(prefix.count))
        guard let raw = Data(base64Encoded: b64), raw.count > 12 else { return nil }
        do {
            let box = try AES.GCM.SealedBox(combined: raw)
            let out = try AES.GCM.open(box, using: try key())
            return String(data: out, encoding: .utf8)
        } catch {
            return nil
        }
    }

    static func isEncrypted(_ stored: String?) -> Bool {
        return stored?.hasPrefix(prefix) ?? false
    }

    // MARK: - 位元組加解密（簽名 PNG）

    /// 加密位元組。格式 `nonce ‖ ct ‖ tag`，**無前綴**（直接存 BLOB）。
    static func encryptBytes(_ plain: Data?) throws -> Data? {
        guard let plain = plain, !plain.isEmpty else { return plain }
        do {
            let sealed = try AES.GCM.seal(plain, using: try key())
            guard let combined = sealed.combined else {
                throw CryptoError.encryptFailed("AES.GCM combined 為 nil")
            }
            return combined
        } catch let e as CryptoError {
            throw e
        } catch {
            throw CryptoError.encryptFailed(String(describing: error))
        }
    }

    static func decryptBytes(_ stored: Data?) -> Data? {
        guard let stored = stored, stored.count > 12 else { return nil }
        do {
            let box = try AES.GCM.SealedBox(combined: stored)
            return try AES.GCM.open(box, using: try key())
        } catch {
            return nil
        }
    }

    // MARK: - 病歷號指紋

    /// `HMAC-SHA256(macKey, mrn.trimmed)` → 小寫 hex。查重用，不可逆。
    static func hashMrn(_ mrn: String?) throws -> String? {
        guard let mrn = mrn else { return nil }
        let t = mrn.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty else { return nil }
        let mac = HMAC<SHA256>.authenticationCode(for: Data(t.utf8), using: try macKey())
        return mac.map { String(format: "%02x", $0) }.joined()
    }

    // MARK: - 顯示遮罩

    /**
     病歷號顯示遮罩。`A123456789` → `A12***789`。

     ⚠ 純顯示用途，**不是**去識別化。短號碼（≤4 碼）整串遮掉，因為留頭留尾等於沒遮。
     */
    static func maskMrn(_ mrn: String?) -> String {
        guard let mrn = mrn else { return "—" }
        let t = mrn.trimmingCharacters(in: .whitespacesAndNewlines)
        if t.isEmpty { return "—" }
        if t.count <= 4 { return String(repeating: "*", count: t.count) }
        return String(t.prefix(3)) + "***" + String(t.suffix(3))
    }
}
