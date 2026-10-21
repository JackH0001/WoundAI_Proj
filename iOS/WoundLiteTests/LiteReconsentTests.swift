import XCTest
@testable import WoundLite

@MainActor
final class LiteReconsentTests: XCTestCase {
    private var folder: URL!
    private var journal: LiteWithdrawalJournal!
    private let owner = String(repeating: "a", count: 32)
    private let server = "https://lite.invalid"
    override func setUp() {
        folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        journal = LiteWithdrawalJournal(url: folder.appendingPathComponent("request.json"))
    }
    override func tearDown() { try? FileManager.default.removeItem(at: folder) }
    private func complete() throws -> LiteWithdrawalRecord {
        let r = LiteWithdrawalRecord(server: server, owner: owner, phase: .liveMediaRemoved)
        try journal.save(r); return r
    }
    private func model() -> LiteWithdrawalModel {
        LiteWithdrawalModel(journal: journal, stopUploads: { XCTFail("No implicit consent changes") },
            owner: { _ in self.owner }, send: { _,_ in XCTFail("No deletion or registration"); return .pending })
    }
    private func receipt(_ r: LiteReconsentRequest, owner: String? = nil, restored: Bool = false) -> LiteReconsentReceipt {
        LiteReconsentReceipt(status: "reconsented", anon_id: owner ?? r.anon_id,
            consent_epoch: r.new_epoch, previous_epoch: r.previous_epoch,
            consent_version: r.consent_version, disclosure_version: r.disclosure_version,
            audit_sha256: String(repeating: "b", count: 64), scope: "future_captures_only", historical_media_restored: restored)
    }
    func testSuccessKeepsIdentityExactWithdrawalAuditAndBlocksOldBindings() async throws {
        _ = try complete(); let bytes = try Data(contentsOf: journal.url)
        let m = model(); XCTAssertTrue(m.blocksUploads)
        let ok = await m.reconsentCompletedWithdrawal(server: server) { intent in
            XCTAssertEqual(intent.previousWithdrawal, bytes)
            XCTAssertEqual(intent.request.anon_id, self.owner)
            XCTAssertEqual(intent.request.previous_epoch, "legacy")
            XCTAssertTrue(self.journal.blocksUploads)
            return self.receipt(intent.request)
        }
        XCTAssertTrue(ok); XCTAssertTrue(m.renewed); XCTAssertFalse(m.blocksUploads)
        let current = try XCTUnwrap(journal.load())
        let path = folder.appendingPathComponent("history/" + current.reconsentAudit! + ".json")
        let audit = try JSONDecoder().decode(LiteReconsentAudit.self, from: Data(contentsOf: path))
        XCTAssertEqual(audit.intent.previousWithdrawal, bytes)
        XCTAssertEqual(current.owner, owner)
        let old = LiteCloudBinding(server: server, anonID: owner, imageID: "old", imageW: 10, imageH: 10)
        XCTAssertFalse(journal.permits(old))
        var new = old; new.researchEpoch = current.researchEpoch
        XCTAssertTrue(journal.permits(new)); XCTAssertNil(journal.status(for: new))
        XCTAssertTrue(journal.status(for: old)?.contains("不會自動重傳") == true)
        XCTAssertTrue(model().renewed); XCTAssertFalse(model().blocksUploads)
    }
    func testLostReplyRetainsSameIntentAcrossRestartAndKeepsUploadsOff() async throws {
        _ = try complete(); let original = try Data(contentsOf: journal.url)
        var first: LiteReconsentIntent?
        let failed = await model().reconsentCompletedWithdrawal(server: server) {
            first = $0; throw URLError(.timedOut)
        }
        XCTAssertFalse(failed); XCTAssertEqual(try Data(contentsOf: journal.url), original)
        XCTAssertTrue(model().blocksUploads)
        let ok = await model().reconsentCompletedWithdrawal(server: server) {
            XCTAssertEqual($0, first); return self.receipt($0.request)
        }
        XCTAssertTrue(ok)
    }
    func testWrongReceiptOwnerOrRestorationClaimCannotEnableUpload() async throws {
        _ = try complete(); let original = try Data(contentsOf: journal.url)
        for wrongOwner in [true, false] {
            let m = model()
            let ok = await m.reconsentCompletedWithdrawal(server: server) {
                self.receipt($0.request, owner: wrongOwner ? String(repeating: "c", count: 32) : nil, restored: !wrongOwner)
            }
            XCTAssertFalse(ok); XCTAssertTrue(m.blocksUploads)
            XCTAssertEqual(try Data(contentsOf: journal.url), original)
        }
    }
    func testIncompleteWithdrawalWrongOriginAndLostIdentityNeverSendRenewal() async throws {
        try journal.save(LiteWithdrawalRecord(server: server, owner: owner))
        var sent = 0
        let pending = await model().reconsentCompletedWithdrawal(server: server) { sent += 1; return self.receipt($0.request) }
        XCTAssertFalse(pending)
        _ = try complete()
        let wrong = await model().reconsentCompletedWithdrawal(server: "https://other.invalid") { sent += 1; return self.receipt($0.request) }
        XCTAssertFalse(wrong)
        let missing = LiteWithdrawalModel(journal: journal, stopUploads: { XCTFail() }, owner: { _ in nil })
        let absent = await missing.reconsentCompletedWithdrawal(server: server) { sent += 1; return self.receipt($0.request) }
        XCTAssertFalse(absent); XCTAssertEqual(sent, 0)
    }
    func testNewWithdrawalTargetsNewEpochAndKeepsRenewalAudit() async throws {
        _ = try complete()
        let ok = await model().reconsentCompletedWithdrawal(server: server) { self.receipt($0.request) }
        XCTAssertTrue(ok)
        let prior = try XCTUnwrap(journal.load())
        let audit = folder.appendingPathComponent("history/" + prior.reconsentAudit! + ".json")
        let auditBytes = try Data(contentsOf: audit)
        var calls = 0
        let m = LiteWithdrawalModel(journal: journal, stopUploads: {}, owner: { _ in XCTFail(); return nil },
            send: { _,_ in XCTFail("Must not target legacy scope"); return .pending }, sendScoped: { origin, owner, epoch in
                calls += 1; XCTAssertEqual(origin, self.server); XCTAssertEqual(owner, self.owner)
                XCTAssertEqual(epoch, prior.researchEpoch); return .liveMediaRemoved
            })
        await m.request(server: server)
        XCTAssertTrue(m.finished); XCTAssertTrue(m.blocksUploads); XCTAssertEqual(calls, 1)
        XCTAssertEqual(try Data(contentsOf: audit), auditBytes)
        XCTAssertEqual(try journal.load()?.researchEpoch, prior.researchEpoch)
    }
    func testMissingAuditFailsClosedAndDoesNotChangeIdentity() async throws {
        _ = try complete()
        let ok = await model().reconsentCompletedWithdrawal(server: server) { self.receipt($0.request) }
        XCTAssertTrue(ok)
        let current = try XCTUnwrap(journal.load())
        try FileManager.default.removeItem(at: folder.appendingPathComponent("history/" + current.reconsentAudit! + ".json"))
        XCTAssertTrue(journal.blocksUploads); XCTAssertThrowsError(try journal.load())
    }
    func testJournalChangeDuringNetworkRequestCannotReenableUpload() async throws {
        _ = try complete()
        let m = model()
        let ok = await m.reconsentCompletedWithdrawal(server: server) { intent in
            try self.journal.save(LiteWithdrawalRecord(server: self.server, owner: self.owner))
            return self.receipt(intent.request)
        }
        XCTAssertFalse(ok); XCTAssertTrue(m.blocksUploads)
    }
    func testConcurrentTapSendsOnlyOneRenewal() async throws {
        _ = try complete(); let m = model(); var calls = 0
        let send: (LiteReconsentIntent) async throws -> LiteReconsentReceipt = {
            calls += 1; try await Task.sleep(nanoseconds: 20_000_000); return self.receipt($0.request)
        }
        async let one = m.reconsentCompletedWithdrawal(server: server, sendRenewal: send)
        async let two = m.reconsentCompletedWithdrawal(server: server, sendRenewal: send)
        _ = await (one, two); XCTAssertEqual(calls, 1)
    }
    func testUnavailableStoragePreventsAnyRenewalRequest() async throws {
        _ = try complete()
        try Data([1]).write(to: folder.appendingPathComponent("renewal"))
        let ok = await model().reconsentCompletedWithdrawal(server: server) { _ in XCTFail(); throw URLError(.badURL) }
        XCTAssertFalse(ok); XCTAssertTrue(journal.blocksUploads)
    }
    func testRetiredPresentationDoesNotClaimCurrentCloudSyncOrOfferRetry() {
        var r = LiteRecord(id: "old", dateISO: "2026-10-21", surfaceCm2: 3, projectedCm2: 3,
                           quality: "ok", imageName: "local", source: "manual")
        r.cloudSync = LiteCloudSyncState(binding: LiteCloudBinding(server: server, anonID: owner,
            imageID: "old", imageW: 10, imageH: 10), acknowledgedRevision: 5)
        let state = LiteRevisionPresentation.make(record: r, syncing: false, consent: true,
            withdrawalBlocked: false, retiredResearch: true)
        XCTAssertEqual(state.phase, .localOnly); XCTAssertNil(state.retryTitle)
        XCTAssertTrue(state.title.contains("已撤回"))
    }
}
