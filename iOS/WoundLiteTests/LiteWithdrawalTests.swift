import XCTest
import CryptoKit
@testable import WoundLite

final class LiteWithdrawalSigningTests: XCTestCase {
    let server = "https://lite.invalid"
    func setup() throws -> (LiteSignedCloudTransport, LiteAttestClient, AttestHTTPDouble, AttestDeviceDouble, AttestPermission) {
        let http = AttestHTTPDouble(), device = AttestDeviceDouble(), permission = AttestPermission()
        let client = try LiteAttestClient(origin: URL(string: server)!, audience: "lite-test", device: device,
            persistence: AttestMemory(), transport: http, now: { Date(timeIntervalSince1970: 1000) })
        return (LiteSignedCloudTransport(client: client, consent: { await permission.read() }), client, http, device, permission)
    }
    func testWithdrawWithConsentOffSignsEmptyDeleteAndDoesNotReenroll() async throws {
        let (transport, client, http, device, permission) = try setup()
        let owner = try await client.registeredInstallation(); await permission.deny()
        await http.businessReply(["status":"deleted", "anon_id":owner, "deletion_scope":"live_media", "writers_drained":true])
        let result = try await transport.withdraw(server: server, owner: owner)
        XCTAssertEqual(result, .liveMediaRemoved)
        let snapshot = await http.snapshot(), request = try XCTUnwrap(snapshot.1.first)
        XCTAssertEqual(request.httpMethod, "DELETE"); XCTAssertEqual(request.url?.path, "/api/v1/lite/data/" + owner)
        XCTAssertTrue((request.httpBody ?? Data()).isEmpty)
        XCTAssertNil(request.value(forHTTPHeaderField: "Authorization")); XCTAssertEqual(snapshot.0.count, 1)
        let bytes = try LiteAttestRequest.clientData(audience: "lite-test", installation: owner,
            keyID: Data(base64Encoded: device.key)!, challenge: snapshot.4,
            requestID: XCTUnwrap(request.value(forHTTPHeaderField: "X-Lite-Request-ID")), method: "DELETE",
            path: request.url!.path, contentType: "", body: Data())
        let hashes = await device.hashes(); XCTAssertEqual(hashes.last, Data(SHA256.hash(data: bytes)))
    }
    func testFencedReceiptConfirmsPayloadRemovalWithoutClaimingWritersDrained() async throws {
        let (transport, client, http, _, permission) = try setup()
        let owner = try await client.registeredInstallation(); await permission.deny()
        await http.businessReply(["status":"deleted", "anon_id":owner, "deletion_scope":"live_media",
                                  "writers_drained":false, "write_fence":"gcs-generation-v1",
                                  "live_payloads_remaining":0, "retained_markers":5,
                                  "research_ledger_cleanup":"live_rows_removed"])
        let result = try await transport.withdraw(server:server, owner:owner)
        XCTAssertEqual(result, .liveMediaRemoved)
    }
    func testIncompleteOrUnknownFenceCannotDowngradeToLegacySuccess() async throws {
        let (transport, client, http, _, _) = try setup()
        let owner = try await client.registeredInstallation()
        let base: [String:Any] = ["status":"deleted", "anon_id":owner, "deletion_scope":"live_media",
                                 "writers_drained":false, "write_fence":"gcs-generation-v1",
                                 "live_payloads_remaining":0, "retained_markers":5,
                                 "research_ledger_cleanup":"live_rows_removed"]
        var replies: [[String:Any]] = []
        for key in ["write_fence", "live_payloads_remaining", "retained_markers", "research_ledger_cleanup", "writers_drained"] {
            var value = base; value.removeValue(forKey:key); replies.append(value)
        }
        for (key, value) in [("write_fence", "unknown" as Any), ("live_payloads_remaining", 1 as Any),
                             ("retained_markers", -1 as Any), ("writers_drained", true as Any),
                             ("research_ledger_cleanup", "not_performed" as Any)] {
            var reply = base; reply[key] = value; replies.append(reply)
        }
        for reply in replies {
            await http.businessReply(reply)
            do {
                _ = try await transport.withdraw(server:server, owner:owner)
                XCTFail("Incomplete or contradictory fence receipt accepted: \(reply)")
            } catch LiteWithdrawalFailure.invalidReceipt {} catch { XCTFail("Unexpected failure: \(error)") }
        }
    }
    func testPendingRequiresActual202ReceiptNotSuccess() async throws {
        let (transport, client, http, _, _) = try setup(); let owner = try await client.registeredInstallation()
        await http.options(status: 202)
        await http.businessReply(["status":"withdrawal_pending", "anon_id":owner, "reason":"writers_in_flight"])
        let result = try await transport.withdraw(server: server, owner: owner); XCTAssertEqual(result, .pending)
    }
    func testMissingDrainedWrongScopeWrongOwnerAndOldReplyCannotReportDeletion() async throws {
        let (transport, client, http, _, _) = try setup(); let owner = try await client.registeredInstallation()
        let replies: [[String:Any]] = [
            ["status":"deleted", "anon_id":owner],
            ["status":"deleted", "anon_id":owner, "deletion_scope":"live_media", "writers_drained":false],
            ["status":"deleted", "anon_id":owner, "deletion_scope":"all", "writers_drained":true],
            ["status":"deleted", "anon_id":"other", "deletion_scope":"live_media", "writers_drained":true],
            ["status":"deleted", "anon_id":owner, "deletion_scope":"live_media", "writers_drained":"true"]]
        for reply in replies {
            await http.businessReply(reply)
            do { _ = try await transport.withdraw(server: server, owner: owner); XCTFail("Accepted \(reply)") }
            catch is LiteWithdrawalFailure { }
        }
    }
    func testNoIdentityAndWrongOwnerHaveNoEnrollmentOrNetwork() async throws {
        let (transport, _, http, device, _) = try setup()
        let existing = try await transport.withdrawalOwner(); XCTAssertNil(existing)
        do { _ = try await transport.withdraw(server: server, owner: String(repeating:"a", count:32)); XCTFail() }
        catch is LiteWithdrawalFailure { }
        let calls = await http.snapshot(); XCTAssertEqual(calls.2, 0); XCTAssertTrue(calls.1.isEmpty)
        let counts = await device.counts(); XCTAssertEqual(counts.0, 0)
    }
    func testDifferentOriginCannotReceiveSignedDeletion() async throws {
        let (transport, client, http, _, _) = try setup(); let owner = try await client.registeredInstallation()
        do { _ = try await transport.withdraw(server:"https://elsewhere.invalid", owner:owner); XCTFail() }
        catch { XCTAssertEqual(error as? LiteAttestFailure, .invalidRequest) }
        let calls = await http.snapshot(); XCTAssertTrue(calls.1.isEmpty)
    }
    func testHTTPFailureOrContradictoryStatusNeverMeansDeleted() async throws {
        let (transport, client, http, _, _) = try setup(); let owner = try await client.registeredInstallation()
        for status in [202, 401, 429, 503] {
            await http.options(status: status)
            await http.businessReply(["status":"deleted", "anon_id":owner, "deletion_scope":"live_media", "writers_drained":true])
            do { _ = try await transport.withdraw(server: server, owner: owner); XCTFail() }
            catch is LiteWithdrawalFailure { }
        }
    }
    func testCanonicalOriginAcceptsExplicitHTTPSPortWithoutChangingOwner() async throws {
        let (transport, client, http, _, _) = try setup(); let owner = try await client.registeredInstallation()
        await http.businessReply(["status":"deleted", "anon_id":owner, "deletion_scope":"live_media", "writers_drained":true])
        let result = try await transport.withdraw(server:"https://LITE.invalid:443/", owner:owner)
        XCTAssertEqual(result,.liveMediaRemoved)
    }
    func testTimeoutHasNoAutomaticRetryAndKeepsOriginalIdentity() async throws {
        let (transport, client, http, _, _) = try setup(); let owner = try await client.registeredInstallation()
        await http.businessReply([:], fail:true)
        do { _ = try await transport.withdraw(server:server, owner:owner); XCTFail() } catch is URLError { }
        let calls = await http.snapshot(); XCTAssertEqual(calls.1.count, 1)
        let existing = try await transport.withdrawalOwner(); XCTAssertEqual(existing, owner)
    }
}

@MainActor
final class LiteWithdrawalJournalTests: XCTestCase {
    private var folder: URL!
    private var journal: LiteWithdrawalJournal!
    private let server = "https://lite.invalid", owner = String(repeating:"a", count:32)
    override func setUp() {
        folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        journal = LiteWithdrawalJournal(url:folder.appendingPathComponent("request.json"))
    }
    override func tearDown() { try? FileManager.default.removeItem(at:folder) }
    func testRestartRetryPinsOriginalOwnerAndServerAndStopsUploadsBeforeSend() async throws {
        var stopped = false, calls = 0
        let first = LiteWithdrawalModel(journal:journal, stopUploads:{ stopped = true }, owner:{ _ in self.owner }, send:{ origin, owner in
            calls += 1; XCTAssertTrue(stopped); XCTAssertTrue(self.journal.blocksUploads)
            XCTAssertEqual(try self.journal.load()?.owner, self.owner)
            throw URLError(.timedOut)
        })
        await first.request(server:server)
        XCTAssertEqual(calls, 1); XCTAssertFalse(first.finished)
        let second = LiteWithdrawalModel(journal:journal, stopUploads:{}, owner:{ _ in XCTFail("Must not replace owner"); return nil }, send:{ origin, owner in
            XCTAssertEqual(origin, self.server); XCTAssertEqual(owner, self.owner); return .liveMediaRemoved
        })
        await second.request(server:"https://changed.invalid")
        XCTAssertTrue(second.finished); XCTAssertTrue(journal.blocksUploads)
        XCTAssertEqual(try journal.load()?.phase, .liveMediaRemoved)
    }
    func test202RemainsPendingAcrossRestart() async throws {
        let model = LiteWithdrawalModel(journal:journal, stopUploads:{}, owner:{ _ in self.owner }, send:{ _,_ in .pending })
        await model.request(server:server)
        let restored = LiteWithdrawalModel(journal:journal, stopUploads:{})
        XCTAssertFalse(restored.finished); XCTAssertTrue(restored.blocksUploads)
        XCTAssertEqual(restored.record?.owner, owner)
    }
    func testMissingIdentityDoesNotClaimCompletionAndKeepsOfflineJournal() async {
        let model = LiteWithdrawalModel(journal:journal, stopUploads:{}, owner:{ _ in nil }, send:{ _,_ in XCTFail(); return .liveMediaRemoved })
        await model.request(server:server)
        XCTAssertTrue(model.blocksUploads); XCTAssertFalse(model.finished); XCTAssertNotNil(model.message)
    }
    func testCorruptJournalFailsClosedAndCannotOverwriteOrSend() async throws {
        try FileManager.default.createDirectory(at:folder, withIntermediateDirectories:true)
        try Data("corrupt".utf8).write(to:journal.url)
        let model = LiteWithdrawalModel(journal:journal, stopUploads:{}, owner:{ _ in XCTFail(); return nil }, send:{ _,_ in XCTFail(); return .pending })
        await model.request(server:server)
        XCTAssertTrue(model.blocksUploads); XCTAssertFalse(model.finished)
        XCTAssertEqual(try String(contentsOf:journal.url, encoding:.utf8), "corrupt")
    }
    func testPersistenceFailurePreventsAnyRequest() async throws {
        try Data([1]).write(to:folder) // Parent is a file, so journal cannot be stored.
        var stopped = false
        let model = LiteWithdrawalModel(journal:journal, stopUploads:{ stopped = true }, owner:{ _ in XCTFail(); return nil }, send:{ _,_ in XCTFail(); return .pending })
        await model.request(server:server)
        XCTAssertTrue(stopped); XCTAssertFalse(model.finished); XCTAssertNotNil(model.message)
    }
    func testRepeatedTapDoesNotCreateConcurrentDeletion() async {
        var calls = 0
        let model = LiteWithdrawalModel(journal:journal, stopUploads:{}, owner:{ _ in self.owner }, send:{ _,_ in
            calls += 1; try await Task.sleep(nanoseconds:30_000_000); return .pending
        })
        async let a: Void = model.request(server:server)
        async let b: Void = model.request(server:server)
        _ = await (a,b)
        XCTAssertEqual(calls,1)
    }
    func testRecordStatusOnlyAppliesToMatchingOwnerAndOrigin() throws {
        try journal.save(LiteWithdrawalRecord(server:server, owner:owner, phase:.liveMediaRemoved))
        var binding = LiteCloudBinding(server:"https://LITE.invalid:443/", anonID:owner, imageID:"image", imageW:10, imageH:10)
        XCTAssertTrue(journal.status(for:binding)?.contains("目前可見媒體已清除") == true)
        binding.anonID = "legacy"; XCTAssertNil(journal.status(for:binding))
        binding.anonID = owner; binding.server = "https://different.invalid"; XCTAssertNil(journal.status(for:binding))
    }
    func testCompletedJournalHasNoNetworkOnFurtherTap() async throws {
        try journal.save(LiteWithdrawalRecord(server:server, owner:owner, phase:.liveMediaRemoved))
        let model = LiteWithdrawalModel(journal:journal, stopUploads:{}, owner:{ _ in XCTFail(); return nil }, send:{ _,_ in XCTFail(); return .pending })
        await model.request(server:server); XCTAssertTrue(model.finished)
    }
}
