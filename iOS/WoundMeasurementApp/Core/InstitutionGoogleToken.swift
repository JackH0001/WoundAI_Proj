import Foundation

/// Routing checks only; Google/IAP must verify the signature. Never log the JWT or decoded claims.
enum InstitutionGoogleClaims {
    static func token(_ raw: String, subject: String, audience: String, now: Date = Date()) throws -> InstitutionIDToken {
        let parts = raw.split(separator: ".", omittingEmptySubsequences: false)
        guard parts.count == 3, parts.allSatisfy({ !$0.isEmpty }), raw.utf8.count < 32768 else {
            throw InstitutionEntryError.signInRequired
        }
        var body = String(parts[1]).replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
        body += String(repeating: "=", count: (4 - body.count % 4) % 4)
        guard let data = Data(base64Encoded: body),
              let claims = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let sub = claims["sub"] as? String, !sub.isEmpty, sub == subject,
              let aud = claims["aud"] as? String, !aud.isEmpty, aud == audience,
              let issuer = claims["iss"] as? String,
              ["https://accounts.google.com", "accounts.google.com"].contains(issuer),
              let expiry = claims["exp"] as? Double, expiry.isFinite,
              expiry > now.timeIntervalSince1970 + 30 else { throw InstitutionEntryError.signInRequired }
        return InstitutionIDToken(value: raw, subject: sub, audience: aud, expiresAt: Date(timeIntervalSince1970: expiry))
    }
}

/// The SDK-specific closure checks currentUser then calls refreshTokensIfNeeded.
/// A provider is permanently revoked on sign-out, including a refresh already in flight.
@MainActor
final class InstitutionRefreshingTokenProvider: InstitutionIDTokenProvider {
    private var active = true
    private let subject: String
    private let audience: String
    private let refresh: @MainActor () async throws -> String
    init(subject: String, audience: String, refresh: @escaping @MainActor () async throws -> String) {
        self.subject = subject; self.audience = audience; self.refresh = refresh
    }
    func invalidate() { active = false }
    func freshToken() async throws -> InstitutionIDToken {
        guard active else { throw InstitutionEntryError.signInRequired }
        let raw = try await refresh()
        try Task.checkCancellation()
        guard active else { throw InstitutionEntryError.signInRequired }
        return try InstitutionGoogleClaims.token(raw, subject: subject, audience: audience)
    }
}

/// Synchronous snapshot is required by BackendClient's synchronous initializer.
/// Only the main-actor Google coordinator installs/removes transports in the app.
final class InstitutionTransportRegistry: @unchecked Sendable {
    static let shared = InstitutionTransportRegistry()
    private let lock = NSLock()
    private var transport: (any InstitutionAuthenticatedTransport)?
    func snapshot() -> (any InstitutionAuthenticatedTransport)? {
        lock.lock(); defer { lock.unlock() }; return transport
    }
    @discardableResult
    func replace(with next: (any InstitutionAuthenticatedTransport)?) -> (any InstitutionAuthenticatedTransport)? {
        lock.lock(); defer { lock.unlock() }
        let previous = transport; transport = next; return previous
    }
}
