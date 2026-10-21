import XCTest
import CryptoKit
@testable import WoundLite

final class LiteWithdrawalSigningTests: XCTestCase {
    let server = "https://lite.invalid"
    func testRenewalUsesSameKeyAndSignsExactConsentAndEpochWhileMediaOff() async throws {
        let (transport, client, http, device, permission) = try setup()
        let owner = try await client.registeredInstallation(); await permission.deny()
        let epoch = String(repeating: "e", count: 32)
        let payload = LiteReconsentRequest(anon_id: owner, research_consent: true, previous_epoch: "legacy",
            new_epoch: epoch, consent_version: LitePrefs.consentVersion, disclosure_version: LiteReconsentRequest.disclosure)
        let intent = LiteReconsentIntent(server: server, previousWithdrawal: Data(), requestedAt: Date(), request: payload)
        await http.businessReply(["status":"reconsented", "anon_id":owner, "consent_epoch":epoch,
            "previous_epoch":"legacy", "consent_version":LitePrefs.consentVersion,
            "disclosure_version":LiteReconsentRequest.disclosure, "audit_sha256":String(repeating:"b",count:64),
            "scope":"future_captures_only", "historical_media_restored":false])
        let receipt = try await transport.rejoin(intent)
        XCTAssertTrue(receipt.validates(payload))
        let snapshot = await http.snapshot(), request = try XCTUnwrap(snapshot.1.last)
        XCTAssertEqual(request.url?.path, "/api/v1/lite/research/rejoin")
        XCTAssertEqual(try JSONDecoder().decode(LiteReconsentRequest.self, from: XCTUnwrap(request.httpBody)), payload)
        let bytes = try LiteAttestRequest.clientData(audience: "lite-test", installation: owner,
            keyID: Data(base64Encoded: device.key)!, challenge: snapshot.4,
            requestID: XCTUnwrap(request.value(forHTTPHeaderField: "X-Lite-Request-ID")), method: "POST",
            path: request.url!.path, contentType: "application/json", body: XCTUnwrap(request.httpBody))
        let hashes = await device.hashes(); XCTAssertEqual(hashes.last, Data(SHA256.hash(data: bytes)))
        XCTAssertEqual(snapshot.0.count, 1)
        let counts = await device.counts(); XCTAssertEqual(counts.0, 1); XCTAssertEqual(counts.1, 1)
        let allowed = await permission.read(); XCTAssertFalse(allowed)
    }
    func testRenewedWithdrawalSignsEpochAndRejectsLegacyReceipt() async throws {
        let (transport, client, http, device, permission) = try setup()
        let owner = try await client.registeredInstallation(); await permission.deny()
        let epoch = String(repeating: "e", count: 32)
        var reply: [String:Any] = ["status":"deleted", "anon_id":owner, "deletion_scope":"live_media", "writers_drained":true]
        await http.businessReply(reply)
        do { _ = try await transport.withdraw(server: server, owner: owner, epoch: epoch); XCTFail() }
        catch LiteWithdrawalFailure.invalidReceipt { }
        reply["consent_epoch"] = epoch; await http.businessReply(reply)
        let result = try await transport.withdraw(server: server, owner: owner, epoch: epoch)
        XCTAssertEqual(result, .liveMediaRemoved)
        let snapshot = await http.snapshot(), request = try XCTUnwrap(snapshot.1.last)
        XCTAssertEqual(request.url?.path, "/api/v1/lite/data/"+owner+"/"+epoch)
        let bytes = try LiteAttestRequest.clientData(audience: "lite-test", installation: owner,
            keyID: Data(base64Encoded: device.key)!, challenge: snapshot.4,
            requestID: XCTUnwrap(request.value(forHTTPHeaderField: "X-Lite-Request-ID")), method: "DELETE",
            path: request.url!.path, contentType: "", body: Data())
        let hashes = await device.hashes(); XCTAssertEqual(hashes.last, Data(SHA256.hash(data: bytes)))
        XCTAssertEqual(snapshot.0.count, 1)
    }
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
        let before = Date()
        let result = try await transport.withdraw(server: server, owner: owner)
        guard case .pendingUntil(let deadline) = result else { return XCTFail("Missing durable wait") }
        XCTAssertGreaterThanOrEqual(deadline.timeIntervalSince(before), 60)
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
            catch is ResearchCapacityWait { XCTAssertTrue([429,503].contains(status)) }
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
        var pending = try XCTUnwrap(journal.load()); pending.retryAt = .distantPast; try journal.save(pending)
        let second = LiteWithdrawalModel(journal:journal, stopUploads:{}, owner:{ _ in XCTFail("Must not replace owner"); return nil }, send:{ origin, owner in
            XCTAssertEqual(origin, self.server); XCTAssertEqual(owner, self.owner); return .liveMediaRemoved
        })
        await second.request(server:"https://changed.invalid")
        XCTAssertTrue(second.finished); XCTAssertTrue(journal.blocksUploads)
        XCTAssertEqual(try journal.load()?.phase, .liveMediaRemoved)
    }
    func testCapacityWaitKeepsWithdrawalFenceAndPreventsEarlyRetry() async throws {
        let due = Date().addingTimeInterval(300)
        let first = LiteWithdrawalModel(journal: journal, stopUploads: {}, owner: { _ in self.owner }, send: { _, _ in
            throw ResearchCapacityWait(notBefore: due)
        })
        await first.request(server: server)
        XCTAssertEqual(try journal.load()?.retryAt, due); XCTAssertTrue(journal.blocksUploads)
        let reopened = LiteWithdrawalModel(journal: journal, stopUploads: {}, send: { _, _ in XCTFail("too early"); return .liveMediaRemoved })
        await reopened.request(server: "https://changed.invalid")
        XCTAssertFalse(reopened.finished); XCTAssertEqual(reopened.record?.server,server)
    }
    func test202DeadlineSurvivesRestartAndBlocksEarlyRetry() async throws {
        let until = Date().addingTimeInterval(600)
        let model = LiteWithdrawalModel(journal:journal, stopUploads:{}, owner:{ _ in self.owner }, send:{ _,_ in .pendingUntil(until) })
        await model.request(server:server)
        XCTAssertEqual(try journal.load()?.retryAt, until)
        let restarted = LiteWithdrawalModel(journal:journal, stopUploads:{}, owner:{ _ in XCTFail(); return nil }, send:{ _,_ in XCTFail(); return .liveMediaRemoved })
        await restarted.request(server:server)
        XCTAssertFalse(restarted.finished); XCTAssertTrue(restarted.blocksUploads)
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
    func testMissingIdentityExplanationSurvivesRestartWithoutChangingWithdrawal() async throws {
        let model = LiteWithdrawalModel(journal:journal, stopUploads:{}, owner:{ _ in nil }, send:{ _,_ in XCTFail(); return .liveMediaRemoved })
        await model.request(server:server)
        let bytes = try Data(contentsOf:journal.url)
        let reopened = LiteWithdrawalModel(journal:journal, stopUploads:{ XCTFail() },
            owner:{ _ in XCTFail(); return nil }, send:{ _,_ in XCTFail(); return .liveMediaRemoved })
        XCTAssertEqual(reopened.statusTitle, "撤回對象尚未確認")
        XCTAssertTrue(reopened.message?.contains("未取得雲端撤回收據") == true)
        XCTAssertTrue(reopened.blocksUploads); XCTAssertFalse(reopened.finished)
        XCTAssertNil(reopened.record?.owner)
        XCTAssertEqual(try Data(contentsOf:journal.url), bytes)
    }
    private func cancellationModel(existingOwner: String? = nil) -> LiteWithdrawalModel {
        LiteWithdrawalModel(journal: journal, stopUploads: { XCTFail("Must not change consent implicitly") },
            owner: { origin in XCTAssertEqual(origin, self.server); return existingOwner },
            send: { _,_ in XCTFail("Cancellation must never send deletion"); return .pending })
    }
    func testExplicitUnsentCancellationPreservesExactLegacyRequestAndConsentAudit() async throws {
        // A legacy request has no time. Preserve the fact that it was not recorded.
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let original = Data(#"{"phase":"pending","server":"https://lite.invalid"}"#.utf8)
        try original.write(to: journal.url)
        let model = cancellationModel()
        let before = Date()
        let success = await model.reconsentUnsentWithdrawal(server: server,
            consentVersion: LitePrefs.consentVersion, hasBoundRecords: { false })
        XCTAssertTrue(success); XCTAssertFalse(model.blocksUploads)
        let current = try XCTUnwrap(journal.load())
        XCTAssertNil(current.owner); XCTAssertNil(current.requestedAt)
        let auditPath = try folder.appendingPathComponent("history/" + XCTUnwrap(current.cancellationAudit) + ".json")
        let auditBytes = try Data(contentsOf: auditPath)
        let audit = try XCTUnwrap(JSONSerialization.jsonObject(with: auditBytes) as? [String:Any])
        XCTAssertEqual(Data(base64Encoded: try XCTUnwrap(audit["previousRequest"] as? String)), original)
        XCTAssertEqual(audit["consentVersion"] as? String, LitePrefs.consentVersion)
        XCTAssertEqual(audit["renewalDisclosureVersion"] as? String, LiteWithdrawalJournal.renewalDisclosureVersion)
        let date = Date(timeIntervalSinceReferenceDate: try XCTUnwrap(audit["at"] as? Double))
        XCTAssertGreaterThanOrEqual(date, before)
        let restarted = cancellationModel()
        XCTAssertTrue(restarted.cancelledBeforeSubmission); XCTAssertFalse(restarted.blocksUploads)
        XCTAssertFalse(restarted.mayReviewUnsentCancellation)
        XCTAssertEqual(try Data(contentsOf: auditPath), auditBytes)
    }
    func testCancellationRejectsKnownOwnerBoundDataWrongServiceAndUnreadableIndex() async throws {
        for scenario in 0..<5 {
            let original = LiteWithdrawalRecord(server: server, owner: scenario == 0 ? owner : nil)
            try journal.save(original)
            let bytes = try Data(contentsOf: journal.url)
            let model = cancellationModel(existingOwner: scenario == 1 ? owner : nil)
            let success = await model.reconsentUnsentWithdrawal(server: scenario == 3 ? "https://other.invalid" : server,
                consentVersion: LitePrefs.consentVersion, hasBoundRecords: {
                    if scenario == 4 { throw LiteWithdrawalFailure.invalidState }
                    return scenario == 2
                })
            XCTAssertFalse(success); XCTAssertTrue(model.blocksUploads)
            XCTAssertEqual(try Data(contentsOf: journal.url), bytes)
        }
    }
    func testIdentityReadFailureKeepsWithdrawalAndDoesNotEnroll() async throws {
        try journal.save(LiteWithdrawalRecord(server: server))
        let bytes = try Data(contentsOf: journal.url)
        let model = LiteWithdrawalModel(journal: journal, stopUploads: { XCTFail() },
            owner: { _ in throw LiteAttestFailure.invalidRequest }, send: { _,_ in XCTFail(); return .pending })
        let success = await model.reconsentUnsentWithdrawal(server: server,
            consentVersion: LitePrefs.consentVersion, hasBoundRecords: { false })
        XCTAssertFalse(success); XCTAssertTrue(model.blocksUploads)
        XCTAssertEqual(try Data(contentsOf: journal.url), bytes)
    }
    func testCancellationRechecksJournalAndBindingsAfterIdentityLookup() async throws {
        for changeJournal in [false,true] {
            try journal.save(LiteWithdrawalRecord(server: server))
            var identityChecked = false
            let model = LiteWithdrawalModel(journal: journal, stopUploads: { XCTFail() }, owner: { _ in
                identityChecked = true
                if changeJournal { try self.journal.save(LiteWithdrawalRecord(server: self.server, owner: self.owner)) }
                return nil
            }, send: { _,_ in XCTFail(); return .pending })
            let success = await model.reconsentUnsentWithdrawal(server: server,
                consentVersion: LitePrefs.consentVersion, hasBoundRecords: { !changeJournal && identityChecked })
            XCTAssertFalse(success); XCTAssertTrue(model.blocksUploads)
            XCTAssertEqual(try journal.load()?.phase, .pending)
        }
    }
    func testCompletedWithdrawalCannotBeCancelledLocally() async throws {
        try journal.save(LiteWithdrawalRecord(server: server, owner: owner, phase: .liveMediaRemoved))
        let bytes = try Data(contentsOf: journal.url)
        let model = cancellationModel()
        let success = await model.reconsentUnsentWithdrawal(server: server,
            consentVersion: LitePrefs.consentVersion, hasBoundRecords: { false })
        XCTAssertFalse(success); XCTAssertTrue(model.blocksUploads); XCTAssertTrue(model.finished)
        XCTAssertEqual(try Data(contentsOf: journal.url), bytes)
    }
    func testMissingOrCorruptedCancellationAuditBlocksUploads() async throws {
        try journal.save(LiteWithdrawalRecord(server: server))
        let model = cancellationModel()
        let success = await model.reconsentUnsentWithdrawal(server: server,
            consentVersion: LitePrefs.consentVersion, hasBoundRecords: { false })
        XCTAssertTrue(success)
        let current = try XCTUnwrap(journal.load())
        let path = try folder.appendingPathComponent("history/" + XCTUnwrap(current.cancellationAudit) + ".json")
        let original = try Data(contentsOf: path)
        var data = try XCTUnwrap(JSONSerialization.jsonObject(with: original) as? [String:Any])
        data["previousSHA256"] = "wrong"
        try JSONSerialization.data(withJSONObject: data).write(to: path)
        XCTAssertTrue(cancellationModel().blocksUploads)
        try original.write(to: path)
        XCTAssertFalse(cancellationModel().blocksUploads)
        try FileManager.default.removeItem(at: path)
        XCTAssertTrue(cancellationModel().blocksUploads)
    }
    func testUnsentCancellationRejectsOldConsentVersionAndArchiveWriteFailure() async throws {
        try journal.save(LiteWithdrawalRecord(server: server))
        let bytes = try Data(contentsOf: journal.url)
        let model = cancellationModel()
        let obsolete = await model.reconsentUnsentWithdrawal(server: server,
            consentVersion: "old", hasBoundRecords: { false })
        XCTAssertFalse(obsolete); XCTAssertEqual(try Data(contentsOf: journal.url), bytes)
        try Data([0]).write(to: folder.appendingPathComponent("history"))
        let failed = await model.reconsentUnsentWithdrawal(server: server,
            consentVersion: LitePrefs.consentVersion, hasBoundRecords: { false })
        XCTAssertFalse(failed); XCTAssertTrue(model.blocksUploads)
        XCTAssertEqual(try Data(contentsOf: journal.url), bytes)
    }
    func testNewExplicitWithdrawalRetainsCancelledRequestAudit() async throws {
        try journal.save(LiteWithdrawalRecord(server: server))
        let model = cancellationModel()
        let success = await model.reconsentUnsentWithdrawal(server: server,
            consentVersion: LitePrefs.consentVersion, hasBoundRecords: { false })
        XCTAssertTrue(success)
        let current = try XCTUnwrap(journal.load())
        let path = try folder.appendingPathComponent("history/" + XCTUnwrap(current.cancellationAudit) + ".json")
        let bytes = try Data(contentsOf: path)
        var stopped = false
        let fresh = LiteWithdrawalModel(journal: journal, stopUploads: { stopped = true },
            owner: { _ in self.owner }, send: { _,_ in
                XCTAssertTrue(stopped); XCTAssertTrue(self.journal.blocksUploads); return .liveMediaRemoved
            })
        await fresh.request(server: server)
        XCTAssertTrue(fresh.finished); XCTAssertTrue(fresh.blocksUploads)
        XCTAssertNil(fresh.record?.cancellationAudit); XCTAssertNotNil(fresh.record?.requestedAt)
        XCTAssertEqual(try Data(contentsOf: path), bytes)
    }
    func testBindingCheckPreservesLegacyServiceIsolationAndFailsOnMalformedOrigin() throws {
        var r = LiteRecord(id: "test", dateISO: "2026-10-21", surfaceCm2: 1, projectedCm2: 1,
            quality: "ok", imageName: "unused", source: "cloud")
        XCTAssertFalse(try LiteWithdrawalModel.hasBoundRecords([r], server: server))
        r.cloudSync = LiteCloudSyncState(binding: LiteCloudBinding(server: "https://old.invalid",
            anonID: owner, imageID: "unused", imageW: 10, imageH: 10))
        XCTAssertFalse(try LiteWithdrawalModel.hasBoundRecords([r], server: server))
        r.cloudSync?.binding.server = "https://LITE.invalid:443/"
        XCTAssertTrue(try LiteWithdrawalModel.hasBoundRecords([r], server: server))
        r.cloudSync?.binding.server = "not-an-origin"
        XCTAssertThrowsError(try LiteWithdrawalModel.hasBoundRecords([r], server: server))
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
