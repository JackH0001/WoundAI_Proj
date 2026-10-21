import Foundation
import SwiftUI
import CryptoKit

/// This receipt covers only current live media, never backups or model unlearning.
enum LiteWithdrawalOutcome: Equatable { case pending, pendingUntil(Date), liveMediaRemoved }

enum LiteWithdrawalFailure: LocalizedError {
    case noIdentity, invalidReceipt, unavailable, invalidState, cancellationRequiresServer
    var errorDescription: String? {
        switch self {
        case .noIdentity: return "找不到可驗證的原安裝身分，尚未送出雲端撤回。舊版資料或遺失身分的資料需聯絡支援處理。"
        case .invalidReceipt: return "尚未取得符合範圍的雲端刪除確認，請稍後重試。"
        case .unavailable: return "雲端尚未確認撤回，請檢查網路後重試。"
        case .invalidState: return "無法讀取或保存撤回狀態；研究上傳已停用，請聯絡支援。"
        case .cancellationRequiresServer: return "此服務已有上傳身分或綁定紀錄，不能在手機直接取消撤回；需先取得伺服器的重新同意確認。原紀錄與撤回要求保留。"
        }
    }
}

struct LiteWithdrawalRecord: Codable, Equatable {
    enum Phase: String, Codable { case pending, liveMediaRemoved, cancelledBeforeSubmission }
    var server: String
    var owner: String?
    var phase: Phase = .pending
    var retryAt: Date? = nil
    var cancellationAudit: String? = nil
    // Older journals have no timestamp; never invent one during decoding.
    var requestedAt: Date? = Date()
}

private struct LiteUnsentWithdrawalCancellation: Codable {
    let action: String
    let at: Date
    let consentVersion: String
    let renewalDisclosureVersion: String
    let previousRequest: Data
    let previousSHA256: String
    let noRegisteredOwner: Bool
    let noBoundRecordsForService: Bool
}

/// Atomic, backup-excluded journal. Corruption/read failures block uploads instead of resetting consent.
struct LiteWithdrawalJournal: Sendable {
    static let renewalDisclosureVersion = "2026-10-21.1"
    let url: URL
    static let standard = LiteWithdrawalJournal(url: FileManager.default.urls(for: .applicationSupportDirectory,
        in: .userDomainMask)[0].appendingPathComponent("lite-withdrawal/request.json"))
    static func origin(_ server: String) throws -> String {
        guard var c = URLComponents(string: server), c.scheme == "https", c.host != nil,
              c.user == nil, c.password == nil, c.query == nil, c.fragment == nil,
              c.percentEncodedPath.isEmpty || c.percentEncodedPath == "/" else { throw LiteWithdrawalFailure.invalidState }
        c.host = c.host?.lowercased(); c.path = ""
        if c.port == 443 { c.port = nil }
        guard let url = c.url else { throw LiteWithdrawalFailure.invalidState }
        return url.absoluteString
    }
    func status(for binding: LiteCloudBinding) -> String? {
        do {
            guard let record = try load(), record.owner == binding.anonID,
                  try Self.origin(record.server) == Self.origin(binding.server) else { return nil }
            return record.phase == .liveMediaRemoved
                ? "此安裝已撤回；雲端確認目前可見媒體已清除。本機紀錄保留，歷史版本與研究衍生資料的刪除尚未確認。"
                : "此安裝已要求撤回，已停止同步；雲端清除尚待確認，可到設定重試查核。"
        } catch { return "撤回狀態無法核對，已停止雲端同步。請到設定查看。" }
    }
    func load() throws -> LiteWithdrawalRecord? {
        let bytes: Data
        do { bytes = try Data(contentsOf: url) }
        catch let error as NSError {
            if error.domain == NSCocoaErrorDomain && error.code == NSFileReadNoSuchFileError { return nil }
            throw LiteWithdrawalFailure.invalidState
        }
        guard let value = try? JSONDecoder().decode(LiteWithdrawalRecord.self, from: bytes),
              let origin = URL(string: value.server), origin.scheme == "https", origin.host != nil,
              origin.user == nil, origin.password == nil, origin.query == nil, origin.fragment == nil,
              origin.path.isEmpty || origin.path == "/",
              value.owner == nil || LiteAttestClient.matches(value.owner!, "[0-9a-f]{32}"),
              value.phase != .liveMediaRemoved || value.owner != nil else { throw LiteWithdrawalFailure.invalidState }
        if value.phase == .cancelledBeforeSubmission {
            guard value.owner == nil, value.retryAt == nil, let name = value.cancellationAudit,
                  UUID(uuidString: name) != nil else { throw LiteWithdrawalFailure.invalidState }
            let auditURL = url.deletingLastPathComponent().appendingPathComponent("history/" + name + ".json")
            guard let data = try? Data(contentsOf: auditURL),
                  let audit = try? JSONDecoder().decode(LiteUnsentWithdrawalCancellation.self, from: data),
                  audit.action == "cancel_unsent_withdrawal_and_reconsent",
                  audit.at.timeIntervalSince1970.isFinite, !audit.consentVersion.isEmpty,
                  audit.renewalDisclosureVersion == Self.renewalDisclosureVersion,
                  audit.noRegisteredOwner, audit.noBoundRecordsForService,
                  SHA256.hash(data: audit.previousRequest).map({ String(format: "%02x", $0) }).joined() == audit.previousSHA256,
                  let prior = try? JSONDecoder().decode(LiteWithdrawalRecord.self, from: audit.previousRequest),
                  prior.phase == .pending, prior.owner == nil, prior.cancellationAudit == nil,
                  try Self.origin(prior.server) == Self.origin(value.server) else { throw LiteWithdrawalFailure.invalidState }
        } else if value.cancellationAudit != nil { throw LiteWithdrawalFailure.invalidState }
        return value
    }
    func save(_ record: LiteWithdrawalRecord) throws {
        do {
            var folder = url.deletingLastPathComponent()
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            var values = URLResourceValues(); values.isExcludedFromBackup = true
            try folder.setResourceValues(values)
            try JSONEncoder().encode(record).write(to: url, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
            guard try load() == record else { throw LiteWithdrawalFailure.invalidState }
        } catch { throw LiteWithdrawalFailure.invalidState }
    }
    var blocksUploads: Bool {
        do { return try load().map { $0.phase != .cancelledBeforeSubmission } ?? false } catch { return true }
    }

    /// Called only after explicit renewed consent and read-only identity/record checks.
    /// The original request is retained byte-for-byte before the active gate changes.
    func cancelUnsent(_ expected: LiteWithdrawalRecord, consentVersion: String) throws -> LiteWithdrawalRecord {
        guard expected.phase == .pending, expected.owner == nil, expected.cancellationAudit == nil,
              consentVersion == LitePrefs.consentVersion, try load() == expected else { throw LiteWithdrawalFailure.invalidState }
        let original = try Data(contentsOf: url)
        let name = UUID().uuidString
        let audit = LiteUnsentWithdrawalCancellation(action: "cancel_unsent_withdrawal_and_reconsent", at: Date(),
            consentVersion: consentVersion, renewalDisclosureVersion: Self.renewalDisclosureVersion, previousRequest: original,
            previousSHA256: SHA256.hash(data: original).map { String(format: "%02x", $0) }.joined(),
            noRegisteredOwner: true, noBoundRecordsForService: true)
        let file = url.deletingLastPathComponent().appendingPathComponent("history/" + name + ".json")
        let bytes = try JSONEncoder().encode(audit)
        try HealthDataFiles.write(bytes, to: file)
        guard try Data(contentsOf: file) == bytes, try load() == expected else { throw LiteWithdrawalFailure.invalidState }
        var next = expected
        next.phase = .cancelledBeforeSubmission; next.retryAt = nil; next.cancellationAudit = name
        try save(next)
        return next
    }
}

extension LiteSignedCloudTransport {
    /// No consent is required for deletion, and no replacement key/owner is enrolled.
    func withdrawalOwner() async throws -> String? {
        let client = try await resolve()
        return try await client.existingInstallation()
    }
    func withdraw(server: String, owner: String) async throws -> LiteWithdrawalOutcome {
        guard LiteAttestClient.matches(owner, "[0-9a-f]{32}"), let origin = URL(string: try LiteWithdrawalJournal.origin(server)) else {
            throw LiteWithdrawalFailure.noIdentity
        }
        let client = try await resolve()
        guard try await client.existingInstallation() == owner else { throw LiteWithdrawalFailure.noIdentity }
        var request = URLRequest(url: origin.appendingPathComponent("api/v1/lite/data/" + owner))
        request.httpMethod = "DELETE"
        let (data, response) = try await client.send(request, installation: owner, requestID: UUID(), allowSending: { true })
        try ResearchCapacityWait.check(response, data: data)
        struct Receipt: Decodable {
            let status: String
            let anon_id: String
            var reason: String?
            var deletion_scope: String?
            var writers_drained: Bool?
            var write_fence: String?
            var live_payloads_remaining: Int?
            var retained_markers: Int?
            var research_ledger_cleanup: String?
        }
        guard response.statusCode == 200 || response.statusCode == 202 else { throw LiteWithdrawalFailure.unavailable }
        guard let receipt = try? JSONDecoder().decode(Receipt.self, from: data), receipt.anon_id == owner else {
            throw LiteWithdrawalFailure.invalidReceipt
        }
        if response.statusCode == 202, receipt.status == "withdrawal_pending", receipt.reason == "writers_in_flight" {
            return .pendingUntil(ResearchCapacityWait.deadline(retryAfter: response.value(forHTTPHeaderField: "Retry-After"),
                serverDate: response.value(forHTTPHeaderField: "Date"), data: data, now: Date()))
        }
        guard response.statusCode == 200, receipt.status == "deleted", receipt.deletion_scope == "live_media" else {
            throw LiteWithdrawalFailure.invalidReceipt
        }
        if receipt.write_fence != nil || receipt.live_payloads_remaining != nil || receipt.retained_markers != nil {
            // Sealed writes are not drained writers. Require the complete new
            // receipt; never downgrade an incomplete/unknown fence to legacy.
            guard receipt.write_fence == "gcs-generation-v1", receipt.writers_drained == false,
                  receipt.live_payloads_remaining == 0, let markers = receipt.retained_markers, markers >= 0,
                  receipt.research_ledger_cleanup == "live_rows_removed" else { throw LiteWithdrawalFailure.invalidReceipt }
        } else {
            guard receipt.writers_drained == true else { throw LiteWithdrawalFailure.invalidReceipt }
        }
        return .liveMediaRemoved
    }
}

@MainActor
final class LiteWithdrawalModel: ObservableObject {
    static let shared = LiteWithdrawalModel()
    @Published private(set) var record: LiteWithdrawalRecord?
    @Published private(set) var busy = false
    @Published private(set) var message: String?
    private let journal: LiteWithdrawalJournal
    private let stopUploads: () -> Void
    private let owner: (String) async throws -> String?
    private let send: (String, String) async throws -> LiteWithdrawalOutcome
    init(journal: LiteWithdrawalJournal = .standard,
         stopUploads: @escaping () -> Void = { LitePrefs.researchConsent = false },
         owner: @escaping (String) async throws -> String? = { try await LiteSignedCloudTransport(baseURL: $0).withdrawalOwner() },
         send: @escaping (String, String) async throws -> LiteWithdrawalOutcome = {
             try await LiteSignedCloudTransport(baseURL: $0).withdraw(server: $0, owner: $1)
         }) {
        self.journal = journal; self.stopUploads = stopUploads; self.owner = owner; self.send = send
        do {
            record = try journal.load()
            if record?.phase == .pending, record?.owner == nil {
                message = "尚未確認此服務的原上傳身分，未取得雲端撤回收據；本機紀錄保留，不能視為雲端資料已刪除。"
            } else if record?.phase == .cancelledBeforeSubmission {
                message = "已保留原撤回與重新同意紀錄。舊服務資料未轉移，也未因本次操作刪除。"
            }
        } catch { message = error.localizedDescription }
    }
    var blocksUploads: Bool { journal.blocksUploads }
    var finished: Bool { record?.phase == .liveMediaRemoved }
    var cancelledBeforeSubmission: Bool { record?.phase == .cancelledBeforeSubmission }
    var mayReviewUnsentCancellation: Bool { record?.phase == .pending && record?.owner == nil }
    static func hasBoundRecords(_ records: [LiteRecord], server: String) throws -> Bool {
        let target = try LiteWithdrawalJournal.origin(server)
        return try records.contains { record in
            guard let binding = record.cloudSync?.binding else { return false }
            return try LiteWithdrawalJournal.origin(binding.server) == target
        }
    }
    var statusTitle: String {
        if finished { return "已確認清除目前可見媒體" }
        if cancelledBeforeSubmission { return "已取消尚未送出的撤回要求" }
        if record?.owner == nil { return "撤回對象尚未確認" }
        return "撤回尚待完成確認"
    }
    /// This does not undo a server withdrawal, enroll a replacement identity,
    /// migrate old records, or claim that old media was deleted.
    func reconsentUnsentWithdrawal(server: String, consentVersion: String,
                                  hasBoundRecords: () throws -> Bool) async -> Bool {
        guard !busy else { return false }
        busy = true; defer { busy = false }
        do {
            guard let value = try journal.load(), value.phase == .pending, value.owner == nil,
                  try LiteWithdrawalJournal.origin(server) == LiteWithdrawalJournal.origin(value.server),
                  try !hasBoundRecords() else { throw LiteWithdrawalFailure.cancellationRequiresServer }
            guard try await owner(value.server) == nil else { throw LiteWithdrawalFailure.cancellationRequiresServer }
            guard try journal.load() == value, try !hasBoundRecords() else { throw LiteWithdrawalFailure.invalidState }
            record = try journal.cancelUnsent(value, consentVersion: consentVersion)
            message = "已保留原撤回與本次重新同意紀錄。這只恢復今後的研究上傳，不代表舊服務資料已刪除或轉移。"
            return true
        } catch {
            message = error.localizedDescription
            return false
        }
    }
    func request(server: String) async {
        guard !busy, !finished, !(record?.retryAt.map { $0 > Date() } ?? false) else { return }
        busy = true; defer { busy = false }
        stopUploads()
        do {
            // A retry always retains its ORIGINAL origin/owner, even after settings change.
            let previous = try journal.load()
            // A newly confirmed withdrawal after reconsent is a new request.
            // The cancelled request and consent event remain in the history file.
            var value: LiteWithdrawalRecord
            if let previous, previous.phase != .cancelledBeforeSubmission {
                value = previous
            } else {
                value = LiteWithdrawalRecord(server: try LiteWithdrawalJournal.origin(server))
            }
            value.retryAt = Date().addingTimeInterval(60)
            try journal.save(value); record = value
            if value.owner == nil {
                guard let existing = try await owner(value.server) else { throw LiteWithdrawalFailure.noIdentity }
                value.owner = existing
                try journal.save(value); record = value
            }
            value.retryAt = Date().addingTimeInterval(60)
            try journal.save(value); record = value
            let result = try await send(value.server, value.owner!)
            if result == .liveMediaRemoved { value.phase = .liveMediaRemoved; value.retryAt = nil }
            if case .pendingUntil(let deadline) = result { value.retryAt = deadline }
            try journal.save(value); record = value
            message = result == .liveMediaRemoved
                ? "雲端確認目前可見的影像與深度媒體已清除，該安裝的研究索引已停用。"
                : "雲端已受理撤回，正在等待先前的上傳結束。本機已保留撤回要求，App 開啟時會自動接續查核。"
        } catch let wait as ResearchCapacityWait {
            if var value = record {
                value.retryAt = wait.notBefore
                do { try journal.save(value); record = value } catch { message = LiteWithdrawalFailure.invalidState.localizedDescription; return }
            }
            message = "後端忙碌；撤回要求已保留，等待後自動查核。研究上傳保持停用。"
        } catch let error as LiteWithdrawalFailure {
            if var value = record { value.retryAt = nil; do { try journal.save(value); record = value } catch { message = LiteWithdrawalFailure.invalidState.localizedDescription; return } }
            message = error.localizedDescription
        } catch {
            message = LiteWithdrawalFailure.unavailable.localizedDescription
        }
    }
}
