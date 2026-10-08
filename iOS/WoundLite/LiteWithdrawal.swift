import Foundation
import SwiftUI

/// This receipt covers only current live media, never backups or model unlearning.
enum LiteWithdrawalOutcome: Equatable { case pending, liveMediaRemoved }

enum LiteWithdrawalFailure: LocalizedError {
    case noIdentity, invalidReceipt, unavailable, invalidState
    var errorDescription: String? {
        switch self {
        case .noIdentity: return "找不到可驗證的原安裝身分，尚未送出雲端撤回。舊版資料或遺失身分的資料需聯絡支援處理。"
        case .invalidReceipt: return "尚未取得符合範圍的雲端刪除確認，請稍後重試。"
        case .unavailable: return "雲端尚未確認撤回，請檢查網路後重試。"
        case .invalidState: return "無法讀取或保存撤回狀態；研究上傳已停用，請聯絡支援。"
        }
    }
}

struct LiteWithdrawalRecord: Codable, Equatable {
    enum Phase: String, Codable { case pending, liveMediaRemoved }
    var server: String
    var owner: String?
    var phase: Phase = .pending
}

/// Atomic, backup-excluded journal. Corruption/read failures block uploads instead of resetting consent.
struct LiteWithdrawalJournal: Sendable {
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
        do { return try load() != nil } catch { return true }
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
            return .pending
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
        do { record = try journal.load() } catch { message = error.localizedDescription }
    }
    var blocksUploads: Bool { journal.blocksUploads }
    var finished: Bool { record?.phase == .liveMediaRemoved }
    func request(server: String) async {
        guard !busy, !finished else { return }
        busy = true; defer { busy = false }
        stopUploads()
        do {
            // A retry always retains its ORIGINAL origin/owner, even after settings change.
            var value = try journal.load() ?? LiteWithdrawalRecord(server: try LiteWithdrawalJournal.origin(server))
            try journal.save(value); record = value
            if value.owner == nil {
                guard let existing = try await owner(value.server) else { throw LiteWithdrawalFailure.noIdentity }
                value.owner = existing
                try journal.save(value); record = value
            }
            let result = try await send(value.server, value.owner!)
            if result == .liveMediaRemoved { value.phase = .liveMediaRemoved }
            try journal.save(value); record = value
            message = result == .pending
                ? "雲端已受理撤回，正在等待先前的上傳結束。請稍後按「重試／查核撤回」。"
                : "雲端確認目前可見的影像與深度媒體已清除，該安裝的研究索引已停用。"
        } catch let error as LiteWithdrawalFailure {
            message = error.localizedDescription
        } catch {
            message = LiteWithdrawalFailure.unavailable.localizedDescription
        }
    }
}
