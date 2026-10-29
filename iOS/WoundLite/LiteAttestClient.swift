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
    case identityMismatch, serviceChanged
    case registrationRecoveryNotAllowed, registrationRecoveryNotReady
    case http(Int)

    /// Authentication happens before sending image/depth bytes. Do not describe
    /// its rejection as an empty segmentation result or ask for another capture.
    var captureFailureMessage: String? {
        switch self {
        case .registrationRejected:
            return "裝置身分驗證未通過，雲端辨識與上傳尚未開始。可手動圈選並存入手機；請保留原紀錄，先不要重拍或反覆送出，待驗證問題修復後再處理。"
        case .http(403):
            return "雲端服務入口目前無法使用，辨識與上傳尚未開始。可手動圈選並存入手機；請保留原紀錄，待入口恢復後再處理。"
        case .unsupported:
            return "此裝置目前不支援所需的身分驗證，未開始雲端辨識或上傳。仍可手動圈選並存入手機。"
        case .invalidState, .identityMismatch, .serviceChanged:
            return "裝置身分或服務設定需要核對，未開始雲端辨識或上傳。請保留原紀錄，不要刪除 App 或重新建立帳號。"
        default: return nil
        }
    }
}

/// One instance per origin/audience/install scope. Lock spans the final HTTP
/// response, so concurrent UI tasks cannot deliver assertion counters backwards.
actor LiteAttestClient {
    struct RegistrationRecovery: Codable, Equatable {
        let oldKey: String
        let oldChallengeID: String
        let oldAttestation: Data
        let invalidatingChallengeID: String
        let confirmedAt: Date
        let reason: String
    }
    struct Identity: Codable, Equatable {
        var version = 1
        var origin: String
        var audience: String
        var key: String
        var phase: String
        var challengeID: String?
        var attestation: Data?
        var installation: String?
        // Preserved with the replacement identity, including after registration.
        // Contains public proofs only; never a Secure Enclave private key.
        var registrationRecoveries: [RegistrationRecovery]?
    }
    private struct Challenge: Decodable {
        let challenge_id: String; let challenge: String; let expires_at: Int; let audience: String
        let replaces_challenge_id: String?
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
        if let audits = value.registrationRecoveries {
            guard !audits.isEmpty, audits.count <= 4 else { throw LiteAttestFailure.invalidState }
            var previousKeys = Set<String>()
            for audit in audits {
                _ = try keyData(audit.oldKey)
                guard audit.oldKey != value.key, previousKeys.insert(audit.oldKey).inserted,
                      Self.matches(audit.oldChallengeID, "[0-9a-f]{32}"),
                      Self.matches(audit.invalidatingChallengeID, "[0-9a-f]{32}"),
                      audit.oldChallengeID != audit.invalidatingChallengeID,
                      (1...65536).contains(audit.oldAttestation.count),
                      audit.confirmedAt.timeIntervalSince1970.isFinite,
                      audit.reason == "server_pending_challenge_replaced_no_bound_records" else {
                    throw LiteAttestFailure.invalidState
                }
            }
        }
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
    private func persist(_ value: Identity) async throws {
        let bytes = try JSONEncoder().encode(value)
        guard bytes.count <= 100_000 else { throw LiteAttestFailure.invalidState }
        try await persistence.save(bytes)
    }
    private func control(_ path: String, body: [String: String]) async throws -> Data {
        var request = URLRequest(url: origin.appendingPathComponent(path))
        request.httpMethod = "POST"; request.timeoutInterval = 45
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: body, options: [.sortedKeys])
        let (data, response) = try await transport.send(request)
        guard response.url == request.url, data.count <= 100_000 else { throw LiteAttestFailure.invalidResponse }
        try ResearchCapacityWait.check(response, data: data)
        guard response.statusCode == 200 else {
            if response.statusCode == 401 { throw LiteAttestFailure.registrationRejected }
            throw LiteAttestFailure.http(response.statusCode)
        }
        return data
    }
    private func challenge(key: String, purpose: String, replacing: String? = nil) async throws -> Challenge {
        var body = ["key_id": key, "purpose": purpose]
        if let replacing { body["previous_challenge_id"] = replacing }
        let bytes = try await control("api/v1/lite/attest/challenge", body: body)
        guard let value = try? JSONDecoder().decode(Challenge.self, from: bytes), value.audience == audience,
              Self.matches(value.challenge_id, "[0-9a-f]{32}"), let nonce = Data(base64Encoded: value.challenge),
              nonce.count == 32, nonce.base64EncodedString() == value.challenge,
              Double(value.expires_at) > now().timeIntervalSince1970,
              Double(value.expires_at) <= now().timeIntervalSince1970 + 180,
              replacing == value.replaces_challenge_id else { throw LiteAttestFailure.invalidResponse }
        return value
    }
    private func enroll() async throws -> Identity {
        guard await device.supported() else { throw LiteAttestFailure.unsupported }
        var identity = try await stored()
        if let interrupted = identity, interrupted.phase == "attesting" {
            // Interrupted before proof persistence; no registration HTTP was sent.
            // Atomically replace the unusable key while retaining recovery audit.
            // Removing the Keychain item first could lose the audit on a crash.
            let key = try await device.generateKey(); _ = try keyData(key)
            guard key != interrupted.key,
                  !(interrupted.registrationRecoveries ?? []).contains(where: { $0.oldKey == key }) else {
                throw LiteAttestFailure.invalidState
            }
            identity = Identity(origin: interrupted.origin, audience: interrupted.audience, key: key,
                                phase: "generated", registrationRecoveries: interrupted.registrationRecoveries)
            try await persist(identity!)
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
            } catch {
                // Keep attesting state and audit; the next explicit attempt can
                // replace this never-registered key without erasing history.
                throw error
            }
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

    enum RegistrationRecoveryResult { case restoredExistingRegistration, preparedFreshRegistration }

    /// Explicit user recovery only. The recover_register contract requires the
    /// exact original expired pending challenge. Missing/active/revoked/corrupt
    /// states are rejected. Its echoed lineage and DIFFERENT challenge prove
    /// old proof invalidation, closing a race with a delayed registration.
    /// The caller must re-read consent, withdrawal and records on every check.
    /// No media is sent and no record is rebound by this operation.
    func recoverUnregisteredIdentity(
        allowRecovery: @Sendable () async throws -> Bool
    ) async throws -> RegistrationRecoveryResult {
        try await acquire(); defer { release() }; try Task.checkCancellation()
        guard try await allowRecovery(), let original = try await stored(),
              original.phase == "pending", original.installation == nil,
              (original.registrationRecoveries?.count ?? 0) < 4 else {
            throw LiteAttestFailure.registrationRecoveryNotAllowed
        }
        // A lost registration acknowledgement is not permission to rotate.
        // Recover the original installation first, using the exact old proof.
        do {
            let bytes = try await control("api/v1/lite/attest/register", body: ["key_id": original.key,
                "challenge_id": original.challengeID!, "attestation": original.attestation!.base64EncodedString()])
            guard let result = try? JSONDecoder().decode(Registration.self, from: bytes), result.key_id == original.key,
                  Self.matches(result.installation, "[0-9a-f]{32}"), try await stored() == original,
                  try await allowRecovery() else { throw LiteAttestFailure.invalidState }
            var restored = original
            restored.phase = "registered"; restored.installation = result.installation
            restored.challengeID = nil; restored.attestation = nil
            try await persist(restored)
            return .restoredExistingRegistration
        } catch LiteAttestFailure.registrationRejected {
            // An explicit rejection alone is NOT sufficient to rotate a key.
        }
        guard try await stored() == original, try await allowRecovery() else {
            throw LiteAttestFailure.registrationRecoveryNotAllowed
        }
        let pending = try await challenge(key: original.key, purpose: "recover_register", replacing: original.challengeID!)
        guard pending.challenge_id != original.challengeID else {
            throw LiteAttestFailure.registrationRecoveryNotReady
        }
        guard try await stored() == original, try await allowRecovery() else {
            throw LiteAttestFailure.registrationRecoveryNotAllowed
        }
        try Task.checkCancellation()
        let replacement = try await device.generateKey(); _ = try keyData(replacement)
        guard replacement != original.key,
              !(original.registrationRecoveries ?? []).contains(where: { $0.oldKey == replacement }),
              try await stored() == original, try await allowRecovery() else {
            throw LiteAttestFailure.registrationRecoveryNotAllowed
        }
        var audits = original.registrationRecoveries ?? []
        audits.append(RegistrationRecovery(oldKey: original.key, oldChallengeID: original.challengeID!,
            oldAttestation: original.attestation!, invalidatingChallengeID: pending.challenge_id,
            confirmedAt: now(), reason: "server_pending_challenge_replaced_no_bound_records"))
        let fresh = Identity(origin: original.origin, audience: original.audience, key: replacement,
                             phase: "generated", registrationRecoveries: audits)
        // One Keychain update preserves old proof/audit together with new state.
        try await persist(fresh)
        return .preparedFreshRegistration
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
        // Only the two reviewed deletion POSTs share the privacy budget.
        // Other POSTs must never bypass ordinary service admission.
        let privacy = method == "DELETE" || (method == "POST" &&
            ["/api/v1/lite/account/deletion/request", "/api/v1/lite/account/deletion/status"].contains(url.path))
        let c = try await challenge(key: value.key, purpose: privacy ? "withdraw" : "assert")
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
