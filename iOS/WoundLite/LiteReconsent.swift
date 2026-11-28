import Foundation
import CryptoKit

/// Rejoining is a separate privacy action, not device registration or media upload.
/// Keep the durable intent intact on every failure; only a matching receipt can
/// activate the new epoch. HTTP errors must not look like a successful toggle.
enum LiteReconsentFailure: LocalizedError, Equatable {
    case endpointUnavailable, authorizationRejected, connectionUnavailable, identityUnavailable, capacityUnavailable
    var errorDescription: String? {
        switch self {
        case .endpointUnavailable:
            return "目前研究服務無法提供重新加入功能。研究上傳仍關閉；需待服務修復後再核對。原撤回紀錄與這次同意要求已保留，不必重拍或重新登記裝置。"
        case .authorizationRejected:
            return "研究服務未接受這次重新加入的驗證。研究上傳仍關閉，原身分與同意要求保留。請聯絡支援核對 App 版本與服務設定，不要刪除 App 或重建身分。"
        case .connectionUnavailable:
            return "連線中斷，尚未取得重新加入確認。研究上傳仍關閉，這次要求已保留；網路恢復後可核對同一次要求，不必重拍。"
        case .identityUnavailable:
            return "找不到先前撤回使用的安裝身分，無法核對重新加入。原紀錄保留，研究上傳仍關閉；請聯絡支援，不要重新登記裝置。"
        case .capacityUnavailable:
            return "研究服務暫忙，尚未確認重新加入。研究上傳仍關閉，這次要求已保留；請稍後核對，不必重拍或重新登記裝置。"
        }
    }

    static func explanation(_ error: Error) -> String {
        if let failure = error as? Self { return failure.localizedDescription }
        if error is ResearchCapacityWait { return Self.capacityUnavailable.localizedDescription }
        if error is URLError { return Self.connectionUnavailable.localizedDescription }
        if let failure = error as? LiteAttestFailure {
            switch failure {
            case .http(401), .http(403), .registrationRejected:
                return Self.authorizationRejected.localizedDescription
            default: break
            }
        }
        if let failure = error as? LiteWithdrawalFailure {
            if case .noIdentity = failure { return Self.identityUnavailable.localizedDescription }
            return failure.localizedDescription
        }
        return LiteWithdrawalFailure.renewalUnconfirmed.localizedDescription
    }
}

/// Pin participation when the camera result arrives, before asynchronous image
/// processing. Renewed consent cannot reclassify an already-processing capture.
struct LiteCaptureResearchScope: Codable, Equatable, Sendable {
    let origin: String
    let epoch: String?
    @MainActor
    static func capture(server: String, consent: Bool, journal: LiteWithdrawalJournal = .standard) throws -> Self? {
        guard consent, !journal.blocksUploads else { return nil }
        let origin = try LiteWithdrawalJournal.origin(server)
        let record = try journal.load()
        if let record {
            guard try LiteWithdrawalJournal.origin(record.server) == origin else { return nil }
        }
        return Self(origin: origin, epoch: record?.researchEpoch)
    }
    @MainActor
    func permits(server: String, consent: Bool, journal: LiteWithdrawalJournal = .standard) -> Bool {
        guard let current = try? Self.capture(server: server, consent: consent, journal: journal) else { return false }
        return current == self
    }
}

/// A new participation epoch keeps the SAME attested installation. It never
/// restores deleted media or changes any saved photo/mask/revision binding.
struct LiteReconsentRequest: Codable, Equatable, Sendable {
    static let disclosure = "2026-10-21.rejoin.1"
    // Historical evidence is checked against its own receipt, not today's text.
    // Current consent for future uploads is separately enforced by LitePrefs.
    static func validVersion(_ value: String) -> Bool {
        LiteAttestClient.matches(value, "[A-Za-z0-9][A-Za-z0-9._-]{0,95}")
    }
    let anon_id: String
    let research_consent: Bool
    let previous_epoch: String
    let new_epoch: String
    let consent_version: String
    let disclosure_version: String
}

struct LiteReconsentReceipt: Codable, Equatable, Sendable {
    let status: String
    let anon_id: String
    let consent_epoch: String
    let previous_epoch: String
    let consent_version: String
    let disclosure_version: String
    let audit_sha256: String
    let scope: String
    let historical_media_restored: Bool
    func validates(_ request: LiteReconsentRequest) -> Bool {
        LiteReconsentRequest.validVersion(request.consent_version) &&
        LiteReconsentRequest.validVersion(request.disclosure_version) &&
        status == "reconsented" && anon_id == request.anon_id && consent_epoch == request.new_epoch &&
        previous_epoch == request.previous_epoch && consent_version == request.consent_version &&
        disclosure_version == request.disclosure_version && scope == "future_captures_only" &&
        !historical_media_restored && LiteAttestClient.matches(audit_sha256, "[0-9a-f]{64}")
    }
}

struct LiteReconsentIntent: Codable, Equatable {
    let server: String
    let previousWithdrawal: Data
    let requestedAt: Date
    let request: LiteReconsentRequest
}

struct LiteReconsentAudit: Codable {
    let intent: LiteReconsentIntent
    let receipt: LiteReconsentReceipt
    let confirmedAt: Date
}

extension LiteWithdrawalJournal {
    func prepareRenewal(_ expected: LiteWithdrawalRecord) throws -> LiteReconsentIntent {
        guard try load() == expected, expected.phase == .liveMediaRemoved,
              let owner = expected.owner else { throw LiteWithdrawalFailure.invalidState }
        let original = try Data(contentsOf: url)
        // One immutable intent per withdrawal. Retry uses exactly the same epoch.
        let digest = SHA256.hash(data: original).map { String(format: "%02x", $0) }.joined()
        let file = url.deletingLastPathComponent().appendingPathComponent("renewal/" + digest + ".json")
        if FileManager.default.fileExists(atPath: file.path) {
            let old = try JSONDecoder().decode(LiteReconsentIntent.self, from: Data(contentsOf: file))
            guard old.previousWithdrawal == original, old.server == expected.server,
                  old.request.anon_id == owner, old.request.previous_epoch == (expected.researchEpoch ?? "legacy"),
                  old.request.research_consent, old.request.consent_version == LitePrefs.consentVersion,
                  old.request.disclosure_version == LiteReconsentRequest.disclosure,
                  LiteAttestClient.matches(old.request.new_epoch, "[0-9a-f]{32}") else { throw LiteWithdrawalFailure.invalidState }
            return old
        }
        let intent = LiteReconsentIntent(server: expected.server, previousWithdrawal: original,
            requestedAt: Date(), request: LiteReconsentRequest(anon_id: owner, research_consent: true,
                previous_epoch: expected.researchEpoch ?? "legacy",
                new_epoch: UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased(),
                consent_version: LitePrefs.consentVersion, disclosure_version: LiteReconsentRequest.disclosure))
        let bytes = try JSONEncoder().encode(intent)
        try HealthDataFiles.write(bytes, to: file)
        guard try Data(contentsOf: file) == bytes, try load() == expected else { throw LiteWithdrawalFailure.invalidState }
        return intent
    }

    func finishRenewal(_ intent: LiteReconsentIntent, receipt: LiteReconsentReceipt) throws -> LiteWithdrawalRecord {
        guard receipt.validates(intent.request), try Data(contentsOf: url) == intent.previousWithdrawal,
              let previous = try load(), previous.phase == .liveMediaRemoved,
              previous.owner == receipt.anon_id, previous.server == intent.server else { throw LiteWithdrawalFailure.invalidReceipt }
        let name = UUID().uuidString
        let file = url.deletingLastPathComponent().appendingPathComponent("history/" + name + ".json")
        let bytes = try JSONEncoder().encode(LiteReconsentAudit(intent: intent, receipt: receipt, confirmedAt: Date()))
        try HealthDataFiles.write(bytes, to: file)
        guard try Data(contentsOf: file) == bytes else { throw LiteWithdrawalFailure.invalidState }
        var next = previous
        next.phase = .reconsented; next.researchEpoch = receipt.consent_epoch
        next.reconsentAudit = name; next.retryAt = nil
        try save(next)
        return next
    }

    func epoch(server: String, owner: String) throws -> String? {
        guard let record = try load(), record.owner == owner,
              try Self.origin(record.server) == Self.origin(server) else { return nil }
        return record.researchEpoch
    }

    func permits(_ binding: LiteCloudBinding) -> Bool {
        do {
            guard !blocksUploads else { return false }
            return try epoch(server: binding.server, owner: binding.anonID) == binding.researchEpoch
        } catch { return false }
    }
}

extension LiteSignedCloudTransport {
    static func rejoinReceipt(_ bytes: Data, response: HTTPURLResponse,
                              intent: LiteReconsentIntent) throws -> LiteReconsentReceipt {
        try ResearchCapacityWait.check(response, data: bytes)
        switch response.statusCode {
        case 404, 405: throw LiteReconsentFailure.endpointUnavailable
        case 401, 403: throw LiteReconsentFailure.authorizationRejected
        default: break
        }
        guard response.statusCode == 200,
              let receipt = try? JSONDecoder().decode(LiteReconsentReceipt.self, from: bytes),
              receipt.validates(intent.request) else { throw LiteWithdrawalFailure.renewalUnconfirmed }
        return receipt
    }

    func rejoin(_ intent: LiteReconsentIntent) async throws -> LiteReconsentReceipt {
        let client = try await resolve()
        guard try await client.existingInstallation() == intent.request.anon_id else { throw LiteWithdrawalFailure.noIdentity }
        let origin = try LiteWithdrawalJournal.origin(intent.server)
        var request = URLRequest(url: URL(string: origin)!.appendingPathComponent("api/v1/lite/research/rejoin"))
        request.httpMethod = "POST"; request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(intent.request)
        // This explicit privacy action is permitted while media consent is off.
        // Caller persists an intentional renewal before invoking this method.
        let (bytes, response) = try await client.send(request, installation: intent.request.anon_id,
            requestID: UUID(), allowSending: { true })
        return try Self.rejoinReceipt(bytes, response: response, intent: intent)
    }
}
