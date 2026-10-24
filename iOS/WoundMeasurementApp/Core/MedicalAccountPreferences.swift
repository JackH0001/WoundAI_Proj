import Foundation

/// Immutable settings namespace. No global "active environment" or cross-scope fallback.
final class MedicalAccountPreferences {
    private let defaults: UserDefaults
    private static let lock = NSRecursiveLock()
    private let invalidate: () -> Void
    private let encrypt: (String) throws -> String?
    private let decrypt: (String) -> String?
    init(scope: MedicalDataScope, defaults: UserDefaults? = nil,
         invalidate: @escaping () -> Void = {},
         encrypt: @escaping (String) throws -> String? = { try PhiCrypto.encrypt($0) },
         decrypt: @escaping (String) -> String? = { PhiCrypto.decrypt($0) }) {
        guard let store = defaults ?? UserDefaults(suiteName: scope.settingsSuite) else {
            preconditionFailure("Cannot open scoped medical preferences")
        }
        self.defaults = store; self.invalidate = invalidate
        self.encrypt = encrypt; self.decrypt = decrypt
    }
    private func locked<T>(_ body: () -> T) -> T {
        Self.lock.lock(); defer { Self.lock.unlock() }; return body()
    }
    func user() -> String { locked { defaults.string(forKey: "backend_user") ?? "" } }
    func password() -> String { locked {
        guard let value = defaults.string(forKey: "backend_pass_enc"), PhiCrypto.isEncrypted(value) else { return "" }
        return decrypt(value) ?? ""
    } }
    @discardableResult
    func setCredentials(user: String, password: String) -> Bool { locked {
        guard let encrypted = try? encrypt(password), PhiCrypto.isEncrypted(encrypted) else { return false }
        invalidate()
        defaults.set(user, forKey: "backend_user")
        defaults.set(encrypted, forKey: "backend_pass_enc")
        return true
    } }
    func clearCredentials() { locked {
        invalidate()
        defaults.removeObject(forKey: "backend_user"); defaults.removeObject(forKey: "backend_pass_enc")
    } }
    enum ConsentQueue: String { case withdrawal = "pending_withdrawals", restore = "pending_restores" }
    func retryAfter(_ queue: ConsentQueue) -> Date? { locked {
        guard let raw = defaults.object(forKey: queue.rawValue + "_retry_at") as? Double else { return nil }
        let bounded = ResearchCapacityWait.privacyDeadline(Date(timeIntervalSince1970: raw))
        if bounded.timeIntervalSince1970 != raw { defaults.set(bounded.timeIntervalSince1970, forKey: queue.rawValue + "_retry_at") }
        return bounded
    } }
    func deferRetry(_ queue: ConsentQueue, until: Date) { locked {
        defaults.set(ResearchCapacityWait.privacyDeadline(until).timeIntervalSince1970, forKey: queue.rawValue + "_retry_at")
    } }
    private func queues() -> [String: [String: String]] {
        // One atomic object records direction AND generation. Keep legacy arrays
        // as migration evidence; ambiguous legacy pairs resolve to withdrawal.
        if let saved = defaults.dictionary(forKey: "consent_intents_v1") as? [String: [String: String]] { return saved }
        var result: [String: [String: String]] = [:]
        for queue in [ConsentQueue.restore, .withdrawal] {
            for code in defaults.stringArray(forKey: queue.rawValue) ?? [] {
                result[code] = ["queue": queue.rawValue, "token": UUID().uuidString]
            }
        }
        defaults.set(result, forKey: "consent_intents_v1")
        return result
    }
    func pending(_ queue: ConsentQueue) -> Set<String> { locked {
        Set(queues().filter { $0.value["queue"] == queue.rawValue }.keys)
    } }
    func token(_ code: String, in queue: ConsentQueue) -> String? { locked {
        let entry = queues()[code]
        return entry?["queue"] == queue.rawValue ? entry?["token"] : nil
    } }
    func add(_ codes: [String], to queue: ConsentQueue) { locked {
        var values = queues()
        for code in codes.map({ $0.trimmingCharacters(in: .whitespacesAndNewlines) }).filter({ !$0.isEmpty }) {
            values[code] = ["queue": queue.rawValue, "token": UUID().uuidString]
        }
        defaults.set(values, forKey: "consent_intents_v1")
    } }
    func consentRevision(_ code: String, token: String) -> String? { locked {
        guard queues()[code]?["token"] == token else { return nil }
        return queues()[code]?["consent_revision"]
    } }
    func pinConsentRevision(_ revision: String, code: String, token: String) -> Bool { locked {
        var values = queues()
        guard values[code]?["token"] == token, values[code]?["queue"] == ConsentQueue.restore.rawValue,
              values[code]?["consent_revision"] == nil else { return false }
        values[code]?["consent_revision"] = revision
        defaults.set(values, forKey: "consent_intents_v1")
        return true
    } }
    func remove(_ code: String, from queue: ConsentQueue, expectedToken: String? = nil) { locked {
        var values = queues()
        guard values[code]?["queue"] == queue.rawValue,
              expectedToken == nil || values[code]?["token"] == expectedToken else { return }
        values.removeValue(forKey: code)
        defaults.set(values, forKey: "consent_intents_v1")
    } }
}
