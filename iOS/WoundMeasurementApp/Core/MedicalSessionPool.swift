import Foundation

/// Process-local generation; never stores a credential. Account/Google changes
/// invalidate old clients before dispatch and after any suspended response.
final class MedicalSessionEpoch: @unchecked Sendable {
    static let shared = MedicalSessionEpoch()
    private let lock = NSLock()
    private var value: UInt64 = 0
    func snapshot() -> UInt64 { lock.lock(); defer { lock.unlock() }; return value }
    func invalidate() { lock.lock(); defer { lock.unlock() }; value &+= 1 }
}

@MainActor
final class MedicalSessionPool {
    struct Input {
        let origin: String
        let user: String
        let password: String
        let epoch: UInt64
        var binding: Binding { Binding(origin: BackendClient.normalize(origin), user: user, epoch: epoch) }
    }
    struct Binding: Equatable { let origin: String; let user: String; let epoch: UInt64 }
    static let shared = MedicalSessionPool(input: {
        Input(origin: AppSettings.backendURL(), user: AppSettings.backendUser(),
              password: AppSettings.backendPassword(), epoch: MedicalSessionEpoch.shared.snapshot())
    }, factory: { BackendClient(baseUrl: $0) })

    private let input: () -> Input
    private let factory: (String) -> BackendClient
    private var cached: (Binding, BackendClient)?
    private var pending: (id: UUID, binding: Binding, task: Task<BackendClient, Error>)?
    init(input: @escaping () -> Input, factory: @escaping (String) -> BackendClient) {
        self.input = input; self.factory = factory
    }

    /// One in-flight authentication for concurrent callers. Every new acquisition
    /// performs a live read-only check; no cached UI flag authorizes an upload.
    func acquire() async throws -> BackendClient {
        let current = input(), binding = current.binding
        guard !current.origin.isEmpty, !current.user.isEmpty, !current.password.isEmpty else {
            throw BackendError.notLoggedIn
        }
        if let active = pending, active.binding == binding {
            let client = try await active.task.value
            try Task.checkCancellation()
            guard input().binding == binding else { throw CancellationError() }
            return client
        }
        let candidate = cached?.0 == binding ? cached?.1 : nil
        let client = candidate ?? factory(binding.origin)
        let ticket = UUID()
        let task = Task { @MainActor in
            if !(await client.isLoggedIn) {
                guard try await client.login(username: current.user, password: current.password) else {
                    throw BackendError.badResponse(await client.lastLoginFailure ?? "登入未完成")
                }
            }
            let identity = try await client.validateMedicalSession()
            guard identity.identity == current.user || identity.user == current.user else {
                throw BackendError.badResponse("登入身分與設定帳號不一致")
            }
            guard self.input().binding == binding else { throw CancellationError() }
            return client
        }
        pending = (ticket, binding, task)
        do {
            let verified = try await task.value
            // Do not let a late old-account response replace a newer session.
            guard input().binding == binding else { throw CancellationError() }
            if pending?.id == ticket { cached = (binding, verified); pending = nil }
            try Task.checkCancellation()
            return verified
        } catch {
            if pending?.id == ticket { pending = nil }
            // Keep an already valid client across transient read failures; the
            // next acquisition still must revalidate. No automatic write retry.
            throw error
        }
    }
}
