import Foundation

/// SDK adapter must refresh through Google Sign-In before returning this value.
/// Local checks prevent routing mistakes; IAP remains the signature/allowlist verifier.
struct InstitutionIDToken: Sendable {
    let value: String
    let subject: String
    let audience: String
    let expiresAt: Date
}

protocol InstitutionIDTokenProvider: Sendable {
    func freshToken() async throws -> InstitutionIDToken
}

protocol InstitutionAuthenticatedTransport: Sendable {
    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse)
    func invalidate() async
}

enum InstitutionEntryError: Error, LocalizedError {
    case unconfigured, invalidDestination, signInRequired, redirected, badResponse
    case entryProbeFailed(Int)
    var errorDescription: String? {
        switch self {
        case .unconfigured: return "機構入口尚未設定，請聯絡管理者。"
        case .invalidDestination: return "請求位址與機構入口不符，已停止連線。"
        case .signInRequired: return "請先登入機構 Google 帳號，再登入 WoundAI 帳號。"
        case .redirected: return "機構入口回傳重新導向，已停止傳送登入資料。"
        case .badResponse: return "機構入口回應無法確認，請稍後再試。"
        case .entryProbeFailed(let code) where code == 429 || code == 503:
            return "Google 帳號已驗證，但機構服務目前忙碌或啟動中（HTTP \(code)）。請稍後重試；這不是 WoundAI 帳密錯誤，尚未傳送帳密。"
        case .entryProbeFailed(let code): return "Google 已登入，但機構入口尚未通過（HTTP \(code)）。請聯絡管理者；尚未傳送 WoundAI 帳密。"
        }
    }
}

enum InstitutionEntryHealth {
    static func validate(_ data: Data, response: HTTPURLResponse, service: String) throws {
        guard response.statusCode == 200 else { throw InstitutionEntryError.entryProbeFailed(response.statusCode) }
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let build = object["build"] as? [String: Any],
              build["service"] as? String == service,
              let revision = build["revision"] as? String, !revision.isEmpty else {
            throw InstitutionEntryError.badResponse
        }
    }
}

/// Never follow redirects with either identity, even to the same host.
final class InstitutionNoRedirect: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }
}

/// One instance belongs to one Google subject and one configured origin.
/// Sign-out invalidates it permanently; a new Google identity needs a new App login/client.
actor InstitutionIAPTransport: InstitutionAuthenticatedTransport {
    private let origin: URL
    private let audience: String
    private let subject: String
    private let provider: any InstitutionIDTokenProvider
    private let session: URLSession
    private let clock: @Sendable () -> Date
    private let redirectGuard = InstitutionNoRedirect()
    private var active = true
    private var sending = false
    private var waiting: [(UUID, CheckedContinuation<Void, Error>)] = []

    private func acquire() async throws {
        try Task.checkCancellation()
        guard active else { throw InstitutionEntryError.signInRequired }
        if !sending { sending = true; return }
        let id = UUID()
        try await withTaskCancellationHandler(operation: {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
                if Task.isCancelled { continuation.resume(throwing: CancellationError()) }
                else { waiting.append((id, continuation)) }
            }
        }, onCancel: { Task { await self.cancelWaiting(id) } })
    }
    private func cancelWaiting(_ id: UUID) {
        guard let index = waiting.firstIndex(where: { $0.0 == id }) else { return }
        waiting.remove(at: index).1.resume(throwing: CancellationError())
    }
    private func release() {
        if waiting.isEmpty { sending = false }
        else { waiting.removeFirst().1.resume() }
    }

    init(origin: URL, audience: String, subject: String,
         provider: any InstitutionIDTokenProvider, session: URLSession? = nil,
         clock: @escaping @Sendable () -> Date = { Date() }) throws {
        guard let c = URLComponents(url: origin, resolvingAgainstBaseURL: false),
              c.scheme == "https", let host = c.host, !host.isEmpty,
              c.user == nil, c.password == nil, c.port == nil,
              c.path.isEmpty || c.path == "/", c.query == nil, c.fragment == nil,
              !audience.isEmpty, !subject.isEmpty else { throw InstitutionEntryError.unconfigured }
        self.origin = origin; self.audience = audience; self.subject = subject
        self.provider = provider; self.clock = clock
        let config = URLSessionConfiguration.ephemeral
        config.httpCookieStorage = nil
        config.httpShouldSetCookies = false
        config.urlCache = nil
        config.timeoutIntervalForRequest = 90
        config.timeoutIntervalForResource = 120
        self.session = session ?? URLSession(configuration: config)
    }

    func invalidate() {
        active = false
        let pending = waiting; waiting.removeAll()
        for (_, continuation) in pending { continuation.resume(throwing: InstitutionEntryError.signInRequired) }
    }

    private func validDestination(_ url: URL?) -> Bool {
        guard let url, let c = URLComponents(url: url, resolvingAgainstBaseURL: false),
              c.scheme == "https", c.host == origin.host, c.port == nil,
              c.user == nil, c.password == nil, c.fragment == nil,
              c.path.hasPrefix("/api/"), !c.percentEncodedPath.contains("%"),
              !c.path.split(separator: "/").contains(where: { $0 == "." || $0 == ".." }) else { return false }
        return true
    }

    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        // All clients under this Google session share one queue, including research uploads.
        // Actor isolation alone does not serialize network awaits (actors are reentrant).
        try await acquire()
        defer { release() }
        guard active else { throw InstitutionEntryError.signInRequired }
        guard validDestination(request.url) else { throw InstitutionEntryError.invalidDestination }
        try Task.checkCancellation()
        let token: InstitutionIDToken
        do { token = try await provider.freshToken() }
        catch is CancellationError { throw CancellationError() }
        catch { throw InstitutionEntryError.signInRequired }
        try Task.checkCancellation()
        guard active, token.subject == subject, token.audience == audience,
              token.expiresAt.timeIntervalSince(clock()) > 30,
              !token.value.isEmpty, token.value.utf8.allSatisfy({ (33...126).contains($0) })
        else { throw InstitutionEntryError.signInRequired }
        var authorized = request
        // Caller cannot smuggle an alternative Cloud Run identity or browser cookie.
        authorized.setValue(nil, forHTTPHeaderField: "X-Serverless-Authorization")
        authorized.setValue(nil, forHTTPHeaderField: "Cookie")
        authorized.setValue("Bearer \(token.value)", forHTTPHeaderField: "Proxy-Authorization")
        // Authorization and the body are intentionally preserved for WoundAI RBAC.
        let data: Data
        let response: URLResponse
        do { (data, response) = try await session.data(for: authorized, delegate: redirectGuard) }
        catch is CancellationError { throw CancellationError() }
        catch { throw InstitutionEntryError.badResponse }
        try Task.checkCancellation()
        guard active else { throw InstitutionEntryError.signInRequired }
        guard let http = response as? HTTPURLResponse, http.url == authorized.url else {
            throw InstitutionEntryError.badResponse
        }
        guard !(300..<400).contains(http.statusCode) else { throw InstitutionEntryError.redirected }
        // Do not retry a write or mistake an App RBAC 403 for expired Google credentials.
        return (data, http)
    }
}
