import Foundation

/// Every production Lite media request goes through the same per-origin actor,
/// including revision retries from another BackendClient instance.
struct LiteSignedCloudTransport: LiteAuthenticatedTransport {
    let resolve: @Sendable () async throws -> LiteAttestClient
    private let consent: @Sendable () async -> Bool

    init(baseURL: String) {
        resolve = {
            guard let origin = URL(string: baseURL),
                  let audience = Bundle.main.object(forInfoDictionaryKey: "LiteAttestAudience") as? String,
                  LiteAttestClient.matches(audience, "[a-z0-9-]{1,64}") else {
                throw LiteAttestFailure.invalidConfiguration
            }
            return try await LiteAttestClients.shared.client(origin: origin, audience: audience)
        }
        consent = { await MainActor.run { LitePrefs.researchConsent == true } }
    }

    /// Dependency injection exercises the real signing flow without a real Apple key.
    init(client: LiteAttestClient, consent: @escaping @Sendable () async -> Bool) {
        resolve = { client }; self.consent = consent
    }

    func installation() async throws -> String {
        guard await consent() else { throw LiteAttestFailure.consentRequired }
        let client = try await resolve()
        let owner = try await client.registeredInstallation()
        guard await consent() else { throw LiteAttestFailure.consentRequired }
        return owner
    }

    func send(_ request: URLRequest, installation: String) async throws -> (Data, HTTPURLResponse) {
        guard await consent() else { throw LiteAttestFailure.consentRequired }
        let client = try await resolve()
        guard try await client.existingInstallation() == installation else {
            throw LiteAttestFailure.identityMismatch
        }
        return try await client.send(request, installation: installation, requestID: UUID(), allowSending: consent)
    }
}
