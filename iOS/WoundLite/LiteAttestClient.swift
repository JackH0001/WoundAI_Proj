import Foundation
import CryptoKit

protocol LiteAttestDevice: Sendable {
    func supported() async -> Bool
    func generateKey() async throws -> String
    func attest(key: String, hash: Data) async throws -> Data
    func assertion(key: String, hash: Data) async throws -> Data
}
protocol LiteAttestPersistence: Sendable {
    func load() async throws -> Data?
    func save(_ data: Data) async throws
    func remove() async throws
}
protocol LiteAttestTransport: Sendable {
    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse)
}
enum LiteAttestFailure: Error, Equatable {
    case unsupported, invalidConfiguration, invalidState, invalidResponse, invalidRequest
    case appleTemporarilyUnavailable, registrationRejected, unavailable, busy, consentRequired
    case identityMismatch
    case http(Int)
}

/// One instance per origin/audience/install scope. Lock spans the final HTTP
/// response, so concurrent UI tasks cannot deliver assertion counters backwards.
actor LiteAttestClient {
    struct Identity: Codable, Equatable {
        var version = 1
        var origin: String
        var audience: String
        var key: String
        var phase: String
        var challengeID: String?
        var attestation: Data?
        var installation: String?
    }
    private struct Challenge: Decodable {
        let challenge_id: String; let challenge: String; let expires_at: Int; let audience: String
    }
    private struct Registration: Decodable { let installation: String; let key_id: String }
    let origin: URL
    let audience: String
    private let device: any LiteAttestDevice
    private let persistence: any LiteAttestPersistence
    private let transport: any LiteAttestTransport
    private let now: @Sendable () -> Date
    private var locked = false
    private var waiters: [CheckedContinuation<Void, Never>] = []

    init(origin: URL, audience: String, device: any LiteAttestDevice, persistence: any LiteAttestPersistence,
         transport: any LiteAttestTransport, now: @escaping @Sendable () -> Date = { Date() }) throws {
        guard origin.scheme == "https", origin.host != nil, origin.user == nil, origin.password == nil,
              origin.query == nil, origin.fragment == nil, !origin.absoluteString.contains("%"), origin.path.isEmpty || origin.path == "/",
              Self.matches(audience, "[a-z0-9-]{1,64}") else { throw LiteAttestFailure.invalidConfiguration }
        self.origin = origin; self.audience = audience; self.device = device
        self.persistence = persistence; self.transport = transport; self.now = now
    }
    static func matches(_ value: String, _ pattern: String) -> Bool {
        guard let re = try? NSRegularExpression(pattern: pattern) else { return false }
        let range = NSRange(value.startIndex..., in: value)
        return re.firstMatch(in: value, range: range)?.range == range
    }
    private func acquire() async throws {
        if !locked { locked = true; return }
        guard waiters.count < 8 else { throw LiteAttestFailure.busy }
        await withCheckedContinuation { waiters.append($0) }
    }
    private func release() { if waiters.isEmpty { locked = false } else { waiters.removeFirst().resume() } }
    private func keyData(_ key: String) throws -> Data {
        guard let data = Data(base64Encoded: key), data.count == 32, data.base64EncodedString() == key else { throw LiteAttestFailure.invalidState }
        return data
    }
    private func stored() async throws -> Identity? {
        guard let bytes = try await persistence.load() else { return nil }
        guard bytes.count <= 100_000, let value = try? JSONDecoder().decode(Identity.self, from: bytes),
              value.version == 1, value.origin == origin.absoluteString, value.audience == audience else { throw LiteAttestFailure.invalidState }
        _ = try keyData(value.key)
        switch value.phase {
        case "generated", "attesting":
            guard value.installation == nil, value.attestation == nil, value.challengeID == nil else { throw LiteAttestFailure.invalidState }
        case "pending":
            guard value.installation == nil, let proof = value.attestation, (1...65536).contains(proof.count),
                  let id = value.challengeID, Self.matches(id, "[0-9a-f]{32}") else { throw LiteAttestFailure.invalidState }
        case "registered":
            guard value.attestation == nil, value.challengeID == nil, let id = value.installation,
                  Self.matches(id, "[0-9a-f]{32}") else { throw LiteAttestFailure.invalidState }
        default: throw LiteAttestFailure.invalidState
        }
        return value
    }
    private func persist(_ value: Identity) async throws { try await persistence.save(JSONEncoder().encode(value)) }
    private func control(_ path: String, body: [String: String]) async throws -> Data {
        var request = URLRequest(url: origin.appendingPathComponent(path))
        request.httpMethod = "POST"; request.timeoutInterval = 45
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: body, options: [.sortedKeys])
        let (data, response) = try await transport.send(request)
        guard response.url == request.url, data.count <= 100_000 else { throw LiteAttestFailure.invalidResponse }
        guard response.statusCode == 200 else {
            if response.statusCode == 401 { throw LiteAttestFailure.registrationRejected }
            throw LiteAttestFailure.http(response.statusCode)
        }
        return data
    }
    private func challenge(key: String, purpose: String) async throws -> Challenge {
        let bytes = try await control("api/v1/lite/attest/challenge", body: ["key_id": key, "purpose": purpose])
        guard let value = try? JSONDecoder().decode(Challenge.self, from: bytes), value.audience == audience,
              Self.matches(value.challenge_id, "[0-9a-f]{32}"), let nonce = Data(base64Encoded: value.challenge),
              nonce.count == 32, nonce.base64EncodedString() == value.challenge,
              Double(value.expires_at) > now().timeIntervalSince1970,
              Double(value.expires_at) <= now().timeIntervalSince1970 + 180 else { throw LiteAttestFailure.invalidResponse }
        return value
    }
    private func enroll() async throws -> Identity {
        guard await device.supported() else { throw LiteAttestFailure.unsupported }
        var identity = try await stored()
        if identity?.phase == "attesting" {
            // Interrupted before proof persistence; no registration HTTP was sent.
            try await persistence.remove(); identity = nil
        }
        if identity == nil {
            let key = try await device.generateKey(); _ = try keyData(key)
            identity = Identity(origin: origin.absoluteString, audience: audience, key: key, phase: "generated")
            try await persist(identity!)
        }
        var value = identity!
        if value.phase == "generated" {
            let c = try await challenge(key: value.key, purpose: "register")
            try Task.checkCancellation()
            value.phase = "attesting"; try await persist(value)
            let proof: Data
            do { proof = try await device.attest(key: value.key, hash: Data(SHA256.hash(data: Data(base64Encoded: c.challenge)!))) }
            catch LiteAttestFailure.appleTemporarilyUnavailable {
                value.phase = "generated"; try await persist(value)
                throw LiteAttestFailure.appleTemporarilyUnavailable
            } catch { try await persistence.remove(); throw error }
            guard (1...65536).contains(proof.count) else { throw LiteAttestFailure.invalidResponse }
            value.phase = "pending"; value.challengeID = c.challenge_id; value.attestation = proof
            // Persist exact bytes BEFORE HTTP so lost responses do not re-attest.
            try await persist(value)
        }
        if value.phase == "pending" {
            try Task.checkCancellation()
            let data = try await control("api/v1/lite/attest/register", body: ["key_id": value.key,
                "challenge_id": value.challengeID!, "attestation": value.attestation!.base64EncodedString()])
            guard let result = try? JSONDecoder().decode(Registration.self, from: data), result.key_id == value.key,
                  Self.matches(result.installation, "[0-9a-f]{32}") else { throw LiteAttestFailure.invalidResponse }
            value.phase = "registered"; value.installation = result.installation
            value.challengeID = nil; value.attestation = nil; try await persist(value)
        }
        return value
    }
    /// Read only: revision/withdrawal may not enroll a replacement owner for old data.
    func existingInstallation() async throws -> String? {
        try await acquire(); defer { release() }; try Task.checkCancellation()
        guard let identity = try await stored(), identity.phase == "registered" else { return nil }
        return identity.installation
    }
    func registeredInstallation() async throws -> String {
        try await acquire(); defer { release() }; try Task.checkCancellation()
        return try await enroll().installation!
    }
    func send(_ original: URLRequest, installation: String, requestID: UUID,
              allowSending: @Sendable () async -> Bool) async throws -> (Data, HTTPURLResponse) {
        try await acquire(); defer { release() }; try Task.checkCancellation()
        guard await allowSending() else { throw LiteAttestFailure.consentRequired }
        guard let value = try await stored(), value.phase == "registered", value.installation == installation,
              let url = original.url, url.scheme == origin.scheme, url.host == origin.host, url.port == origin.port,
              url.user == nil, url.password == nil, url.query == nil, url.fragment == nil,
              !url.absoluteString.contains("%"), original.httpBodyStream == nil,
              original.value(forHTTPHeaderField: "Authorization") == nil, original.value(forHTTPHeaderField: "Cookie") == nil,
              let method = original.httpMethod else { throw LiteAttestFailure.invalidRequest }
        let body = original.httpBody ?? Data(), type = original.value(forHTTPHeaderField: "Content-Type") ?? ""
        let c = try await challenge(key: value.key, purpose: method == "DELETE" ? "withdraw" : "assert")
        let bytes = try LiteAttestRequest.clientData(audience: audience, installation: installation,
            keyID: keyData(value.key), challenge: Data(base64Encoded: c.challenge)!, requestID: requestID.uuidString.lowercased(),
            method: method, path: url.path, contentType: type, body: body)
        let assertion = try await device.assertion(key: value.key, hash: Data(SHA256.hash(data: bytes)))
        guard (1...4096).contains(assertion.count), Double(c.expires_at) > now().timeIntervalSince1970 else { throw LiteAttestFailure.invalidResponse }
        try Task.checkCancellation()
        guard await allowSending() else { throw LiteAttestFailure.consentRequired }
        var request = original
        request.setValue(value.key, forHTTPHeaderField: "X-Lite-Key-ID")
        request.setValue(c.challenge_id, forHTTPHeaderField: "X-Lite-Challenge-ID")
        request.setValue(assertion.base64EncodedString(), forHTTPHeaderField: "X-Lite-Assertion")
        request.setValue(requestID.uuidString.lowercased(), forHTTPHeaderField: "X-Lite-Request-ID")
        request.setValue(String(body.count), forHTTPHeaderField: "Content-Length")
        // No automatic media retry: a timeout may follow a committed write.
        let result = try await transport.send(request)
        guard result.1.url == request.url else { throw LiteAttestFailure.invalidResponse }
        return result
    }
}
