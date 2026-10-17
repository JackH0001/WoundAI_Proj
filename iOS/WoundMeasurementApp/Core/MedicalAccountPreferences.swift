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
        (defaults.object(forKey: queue.rawValue + "_retry_at") as? Double).map(Date.init(timeIntervalSince1970:))
    } }
    func deferRetry(_ queue: ConsentQueue, until: Date) { locked {
        defaults.set(until.timeIntervalSince1970, forKey: queue.rawValue + "_retry_at")
    } }
    func pending(_ queue: ConsentQueue) -> Set<String> { locked {
        Set(defaults.stringArray(forKey: queue.rawValue) ?? [])
    } }
    func add(_ codes: [String], to queue: ConsentQueue) { locked {
        var values = pending(queue)
        values.formUnion(codes.filter { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty })
        defaults.set(Array(values).sorted(), forKey: queue.rawValue)
    } }
    func remove(_ code: String, from queue: ConsentQueue) { locked {
        var values = pending(queue); values.remove(code)
        defaults.set(Array(values).sorted(), forKey: queue.rawValue)
    } }
}
