import XCTest
@testable import WoundLite

private actor LinkTestJournal: LiteServiceJournal {
    private var data: [String: Data] = [:]
    func load(_ id: String) -> Data? { data[id] }
    func replace(_ id: String, expected: Data?, with value: Data) throws {
        guard data[id] == expected else { throw LiteDeletionFailure.conflict }
        data[id] = value
    }
}

private actor LinkTestTransport: LiteAuthenticatedTransport {
    var owner: String
    private var replies: [Data] = []
    private var requests: [URLRequest] = []
    init(owner: String) { self.owner = owner }
    func installation() -> String { owner }
    func switchOwner(_ next: String) { owner = next }
    func enqueue(_ reply: Data) { replies.append(reply) }
    func bodies() -> [Data] { requests.compactMap(\.httpBody) }
    func send(_ request: URLRequest, installation: String) throws -> (Data, HTTPURLResponse) {
        guard owner == installation, let url = request.url else { throw LiteDeletionFailure.identity }
        requests.append(request)
        guard !replies.isEmpty else { throw LiteDeletionFailure.unavailable }
        let response = HTTPURLResponse(url: url, statusCode: 200, httpVersion: nil, headerFields: nil)!
        return (replies.removeFirst(), response)
    }
}

@MainActor final class LiteResearchDeletionLinkTests: XCTestCase {
    let serviceURL = URL(string: "https://native.example.com")!
    let researchURL = URL(string: "https://research.example.com")!
    let nativeOwner = String(repeating: "1", count: 32)
    let researchOwner = String(repeating: "2", count: 32)
    let person = String(repeating: "3", count: 64)
    let op = String(repeating: "4", count: 32)

    private func checkpoint(_ journal: LinkTestJournal) async throws -> LiteAccountDeletionCheckpoint {
        let account = LiteServiceBytes.hash(try JSONEncoder().encode("apple-" + person))
        let c = LiteDeletionContext(origin: serviceURL.absoluteString, installation: nativeOwner, account: account)
        let receipt = LiteDeletionReceipt(v: 1, state: "account_deletion_pending", operation: op,
            installation: nativeOwner, requested_at: 10, inventory_sha256: person, grant_total: 1,
            revocation_scan: 1, grants_examined: 0, grants_revoked: 0, apple_revocation: "not_started",
            data_disposition: "not_started", account_deleted: false, subscription_cancelled: false,
            local_records_preserved: true, research_withdrawal: "requested")
        let saved = LiteAccountDeletionCheckpoint(version: 1, context: c, operation: op, createdAt: 10,
            receipt: receipt, researchWithdrawalConfirmed: true)
        try await journal.replace(LiteAccountDeletionCheckpoint.key(origin: c.origin, owner: c.installation),
                                  expected: nil, with: JSONEncoder().encode(saved))
        return saved
    }
    private func make(_ n: LinkTestTransport, _ r: LinkTestTransport, _ j: LinkTestJournal) throws -> LiteResearchDeletionLink {
        try LiteResearchDeletionLink(nativeOrigin: serviceURL, researchOrigin: researchURL, native: n, research: r, journal: j)
    }
    private func offer(_ person: String) throws -> Data {
        let pair = ["action":"withdraw-linked-research/1","scope":self.person,"bucket":"synthetic-test",
                    "person":person,"native":nativeOwner,"research":researchOwner,"operation":op,"registration":self.person]
        return try JSONSerialization.data(withJSONObject: ["issued":1,"expires":601,"signature":"test",
            "packet":["pair":pair,"native":"test"]])
    }
    private func reply(_ done: Bool = false) throws -> Data {
        try JSONSerialization.data(withJSONObject: ["state":"association_recorded","operation":op,"native":nativeOwner,
                                                   "research":researchOwner,"receipt_sha256":person,"cleanup_confirmed":done])
    }

    func testLostResponseAndRestartUseOriginalPairAndLocalReceipt() async throws {
        let n = LinkTestTransport(owner: nativeOwner), r = LinkTestTransport(owner: researchOwner), j = LinkTestJournal()
        let c = try await checkpoint(j)
        let first = try make(n,r,j)
        await first.reload(c)
        let reads = await n.bodies()
        XCTAssertTrue(reads.isEmpty)
        await n.enqueue(try offer(person))
        await first.confirm(c)
        XCTAssertFalse(first.confirmed)
        let second = try make(n,r,j)
        await second.reload(c)
        XCTAssertTrue(second.message.contains("已保存"))
        await n.enqueue(try offer(person))
        await r.enqueue(try reply())
        await second.confirm(c)
        XCTAssertTrue(second.confirmed)
        XCTAssertTrue(second.message.contains("清理仍待確認"))
        let replay = try make(n,r,j)
        await replay.reload(c)
        await replay.confirm(c)
        XCTAssertTrue(replay.confirmed)
        let nativeBodies = await n.bodies(), researchBodies = await r.bodies()
        XCTAssertEqual(nativeBodies.count, 2)
        XCTAssertEqual(researchBodies.count, 2)
        for body in nativeBodies + researchBodies {
            let json = try XCTUnwrap(JSONSerialization.jsonObject(with: body) as? [String:Any])
            XCTAssertEqual(json["confirmation"] as? String, LiteResearchLinkPath.confirmation)
            XCTAssertTrue(Set(json.keys).isSubset(of: ["anon_id","confirmation","operation","research","ticket"]))
        }
    }

    func testResearchIdentityChangeAfterFailureNeverRetargetsTheRequest() async throws {
        let n = LinkTestTransport(owner: nativeOwner), r = LinkTestTransport(owner: researchOwner), j = LinkTestJournal()
        let c = try await checkpoint(j)
        await (try make(n,r,j)).confirm(c)
        await r.switchOwner(String(repeating: "7", count: 32))
        let resumed = try make(n,r,j)
        await resumed.confirm(c)
        XCTAssertFalse(resumed.confirmed)
        let ns = await n.bodies(), rs = await r.bodies()
        XCTAssertEqual(ns.count,1)
        XCTAssertTrue(rs.isEmpty)
    }

    func testTicketForDifferentAccountCannotReachResearchAndCleanupClaimRejected() async throws {
        let n = LinkTestTransport(owner: nativeOwner), r = LinkTestTransport(owner: researchOwner), j = LinkTestJournal()
        let c = try await checkpoint(j), link = try make(n,r,j)
        await n.enqueue(try offer(String(repeating:"8",count:64)))
        await link.confirm(c)
        let before = await r.bodies()
        XCTAssertTrue(before.isEmpty)
        await n.enqueue(try offer(person)); await r.enqueue(try reply(true))
        await link.confirm(c)
        XCTAssertFalse(link.confirmed)
    }

    func testMissingOriginalResearchRegistrationDoesNotSend() async throws {
        let n = LinkTestTransport(owner: nativeOwner), r = LinkTestTransport(owner: ""), j = LinkTestJournal()
        let c = try await checkpoint(j), link = try make(n,r,j)
        await link.confirm(c)
        let ns = await n.bodies(), rs = await r.bodies()
        XCTAssertTrue(ns.isEmpty && rs.isEmpty)
        XCTAssertTrue(link.message.contains("無須建立登記"))
    }
}
