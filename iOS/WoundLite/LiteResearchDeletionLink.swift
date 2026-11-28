import Foundation

/// Only used from the internal account-deletion screen. Each origin uses its own
/// existing App Attest registration. No photo, depth, account token or new enrollment.
enum LiteResearchLinkPath {
    static let offer = "/api/v1/lite/account/deletion/research-offer"
    static let accept = "/api/v1/lite/research/deletion-proof"
    static let confirmation = "withdraw_this_research_installation_for_this_account_deletion"
}

private struct LinkPair: Codable {
    let action, scope, bucket, person, native, research, operation, registration: String
}
private struct LinkPacket: Codable { let pair: LinkPair; let native: String }
private struct LinkTicket: Codable {
    let packet: LinkPacket
    let issued, expires: Int
    let signature: String
}
private struct LinkOffer: Encodable { let anon_id, operation, research, confirmation: String }
private struct LinkAccept: Encodable { let anon_id, confirmation: String; let ticket: LinkTicket }
private struct LinkReply: Decodable {
    let state, operation, native, research, receipt_sha256: String
    let cleanup_confirmed: Bool
}

private struct LinkIntent: Codable {
    let version: Int
    let context: LiteDeletionContext
    let operation, researchOrigin, research: String
    var receipt: String?

    static func key(_ checkpoint: LiteAccountDeletionCheckpoint) -> String {
        let fields = [checkpoint.context.origin, checkpoint.context.installation,
                      checkpoint.operation, "research-withdrawal-link/1"]
        return String(LiteServiceBytes.hash(Data(fields.joined(separator: "\0").utf8)).prefix(32))
    }

    func validate(_ original: LiteAccountDeletionCheckpoint, origin: URL) throws {
        guard version == 1, context == original.context, operation == original.operation,
              researchOrigin == origin.absoluteString, LiteServiceBytes.hex(research, 32),
              receipt.map({ LiteServiceBytes.hex($0) }) ?? true else { throw LiteDeletionFailure.conflict }
    }
}

@MainActor final class LiteResearchDeletionLink: ObservableObject {
    let nativeOrigin, researchOrigin: URL
    let native: any LiteAuthenticatedTransport
    let research: any LiteAuthenticatedTransport
    let journal: any LiteServiceJournal
    @Published private(set) var busy = false
    @Published private(set) var confirmed = false
    @Published private(set) var message = "尚未核對這部手機在研究服務的原登記。"

    init(nativeOrigin: URL, researchOrigin: URL, native: any LiteAuthenticatedTransport,
         research: any LiteAuthenticatedTransport, journal: any LiteServiceJournal) throws {
        for u in [nativeOrigin, researchOrigin] {
            guard LiteBackendConfiguration.canonicalOrigin(u.absoluteString) == u.absoluteString,
                  u.scheme == "https" else { throw LiteDeletionFailure.invalid }
        }
        guard nativeOrigin != researchOrigin else { throw LiteDeletionFailure.conflict }
        self.nativeOrigin = nativeOrigin
        self.researchOrigin = researchOrigin
        self.native = native
        self.research = research
        self.journal = journal
    }

    private func original(_ record: LiteAccountDeletionCheckpoint) async throws {
        try record.validate()
        let c = record.context
        guard c.origin == nativeOrigin.absoluteString, record.receipt?.research_withdrawal == "requested",
              try await native.installation() == c.installation,
              let bytes = try await journal.load(LiteAccountDeletionCheckpoint.key(origin: c.origin, owner: c.installation))
        else { throw LiteDeletionFailure.identity }
        let stored = try JSONDecoder().decode(LiteAccountDeletionCheckpoint.self, from: bytes)
        try stored.validate()
        guard stored.context == c, stored.operation == record.operation, stored.receipt?.research_withdrawal == "requested"
        else { throw LiteDeletionFailure.conflict }
    }

    private func read(_ record: LiteAccountDeletionCheckpoint) async throws -> (Data?, LinkIntent?) {
        guard let raw = try await journal.load(LinkIntent.key(record)) else { return (nil, nil) }
        let intent = try JSONDecoder().decode(LinkIntent.self, from: raw)
        try intent.validate(record, origin: researchOrigin)
        return (raw, intent)
    }

    private func save(_ intent: LinkIntent, for record: LiteAccountDeletionCheckpoint, previous: Data?) async throws -> Data {
        let data = try JSONEncoder().encode(intent)
        try await journal.replace(LinkIntent.key(record), expected: previous, with: data)
        return data
    }

    private func show(_ intent: LinkIntent?) {
        confirmed = intent?.receipt != nil
        if confirmed { message = "兩個服務已核對本機原登記；研究資料清理仍待確認。" }
        else if intent != nil { message = "已保存本機核對要求，可用原裝置繼續。" }
        else { message = "尚未核對這部手機在研究服務的原登記。" }
    }

    func reload(_ record: LiteAccountDeletionCheckpoint) async {
        guard !busy else { return }
        busy = true
        defer { busy = false }
        do {
            try await original(record)
            let (_, intent) = try await read(record)
            show(intent)
        } catch {
            confirmed = false
            message = "無法讀取原刪帳要求。請解鎖裝置後重新核對。"
        }
    }

    private func post<Body: Encodable, Reply: Decodable>(_ body: Body, path: String, to origin: URL,
        owner: String, via transport: any LiteAuthenticatedTransport, reply: Reply.Type) async throws -> Reply {
        let url = origin.appendingPathComponent(String(path.dropFirst()))
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(body)
        let (raw, response) = try await transport.send(request, installation: owner)
        guard response.url == url, response.statusCode == 200, raw.count <= 4096 else { throw LiteDeletionFailure.unavailable }
        return try JSONDecoder().decode(Reply.self, from: raw)
    }

    /// Enter only from the user's confirmation. Bind the original local pair before
    /// any network request; losing either response can only replay the same intent.
    func confirm(_ record: LiteAccountDeletionCheckpoint) async {
        guard !busy else { return }
        busy = true
        defer { busy = false }
        do {
            try await original(record)
            var (previous, stored) = try await read(record)
            if stored?.receipt != nil { show(stored); return }
            guard let owner = try? await research.installation(), LiteServiceBytes.hex(owner, 32) else {
                message = "無法讀取這部手機原有的研究登記。請先解鎖；若從未參與研究，無須建立登記。"
                return
            }
            var intent = stored ?? LinkIntent(version: 1, context: record.context, operation: record.operation,
                                               researchOrigin: researchOrigin.absoluteString, research: owner)
            try intent.validate(record, origin: researchOrigin)
            guard owner == intent.research else { throw LiteDeletionFailure.identity }
            if stored == nil { previous = try await save(intent, for: record, previous: nil) }
            let accountOwner = record.context.installation
            let offer = LinkOffer(anon_id: accountOwner, operation: record.operation, research: owner,
                                  confirmation: LiteResearchLinkPath.confirmation)
            let ticket = try await post(offer, path: LiteResearchLinkPath.offer, to: nativeOrigin,
                                        owner: accountOwner, via: native, reply: LinkTicket.self)
            let pair = ticket.packet.pair
            let subject = try JSONEncoder().encode("apple-" + pair.person)
            guard pair.action == "withdraw-linked-research/1", pair.native == accountOwner, pair.research == owner,
                  pair.operation == record.operation, LiteServiceBytes.hex(pair.person),
                  LiteServiceBytes.hash(subject) == record.context.account else { throw LiteDeletionFailure.conflict }
            try await original(record)
            guard try await research.installation() == owner else { throw LiteDeletionFailure.identity }
            let result = try await post(LinkAccept(anon_id: owner, confirmation: LiteResearchLinkPath.confirmation, ticket: ticket),
                                        path: LiteResearchLinkPath.accept, to: researchOrigin, owner: owner,
                                        via: research, reply: LinkReply.self)
            guard result.state == "association_recorded", !result.cleanup_confirmed,
                  result.operation == record.operation, result.native == accountOwner, result.research == owner,
                  LiteServiceBytes.hex(result.receipt_sha256) else { throw LiteDeletionFailure.conflict }
            try await original(record)
            guard try await research.installation() == owner else { throw LiteDeletionFailure.identity }
            intent.receipt = result.receipt_sha256
            _ = try await save(intent, for: record, previous: previous)
            show(intent)
        } catch {
            confirmed = false
            message = "本次尚未確認。請保留原紀錄，使用原裝置重試；不會重新上傳照片或深度。"
        }
    }
}


struct LiteExistingResearchTransport: LiteAuthenticatedTransport {
    let origin: URL

    private func allowed() async -> Bool {
        await MainActor.run {
            origin.absoluteString == LiteBackendConfiguration.dedicatedOrigin &&
                LiteBackendConfiguration.sameService(AppSettings.backendURL(), origin.absoluteString)
        }
    }

    func installation() async throws -> String {
        guard await allowed() else { throw LiteDeletionFailure.identity }
        let client = try await LiteSignedCloudTransport(baseURL: origin.absoluteString).resolve()
        guard let owner = try await client.existingInstallation(), await allowed() else {
            throw LiteDeletionFailure.identity
        }
        return owner
    }

    func send(_ request: URLRequest, installation owner: String) async throws -> (Data, HTTPURLResponse) {
        guard request.httpMethod == "POST",
              request.url == origin.appendingPathComponent(String(LiteResearchLinkPath.accept.dropFirst())),
              try await installation() == owner else { throw LiteDeletionFailure.identity }
        let client = try await LiteSignedCloudTransport(baseURL: origin.absoluteString).resolve()
        return try await client.send(request, installation: owner, requestID: UUID(), allowSending: { await allowed() })
    }
}
