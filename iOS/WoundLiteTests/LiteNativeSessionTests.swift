import XCTest
import SwiftUI
import PDFKit
@testable import WoundLite

private let sessionTime = 1_800_000_000
private let sessionToken = "10000000-0000-4000-8000-000000000001"
private let sessionHash = String(repeating: "a", count: 64)

private actor SessionJournal: LiteServiceJournal {
    var rows = [String: Data]()
    var fails = false
    func load(_ id: String) -> Data? { rows[id] }
    func replace(_ id: String, expected: Data?, with data: Data) throws {
        guard !fails, rows[id] == expected else { throw LiteServiceFailure.conflict }
        rows[id] = data
    }
    func fail(_ value: Bool) { fails = value }
    func snapshot() -> [String: Data] { rows }
    func corruptRecognition() throws {
        guard let key = rows.first(where: { $0.value.range(of: Data("captureID".utf8)) != nil })?.key else {
            throw LiteServiceFailure.invalidState
        }
        rows[key] = Data("broken-existing-record".utf8)
    }
}

@MainActor private final class SessionApple: LiteAppleIdentityProvider {
    var calls = 0, allowed = true
    func signIn(_ c: LiteAccountChallenge) async throws -> LiteAppleIdentity {
        calls += 1
        return LiteAppleIdentity(identityToken: "synthetic-jwt", state: c.state, user: "synthetic-user", authorizationCode: "one-use-code")
    }
    func authorized(user: String) async throws -> Bool { allowed }
}

@MainActor private final class SessionStoreKit: LitePurchaseProvider {
    let event = LitePurchaseEvent(transaction: "123", original: "100", token: sessionToken,
        product: "com.woundai.lite.plus.monthly", environment: "Sandbox", signed: "synthetic-jws")
    var buys = 0, finishes = 0, restores = 0, pending = false
    func monthlyOffer() async throws -> LiteMonthlyOffer { nativeTestOffer }
    func knownPurchases() async throws -> [LitePurchaseEvent] { pending ? [event] : [] }
    func purchase(offer: LiteMonthlyOffer, token: UUID) async throws -> LitePurchaseOutcome {
        XCTAssertEqual(token.uuidString.lowercased(), sessionToken)
        XCTAssertEqual(offer, nativeTestOffer); buys += 1; pending = true
        return .completed(event)
    }
    func restore() async throws -> [LitePurchaseEvent] { restores += 1; return [event] }
    func unfinished() async throws -> [LitePurchaseEvent] { pending ? [event] : [] }
    func updates() -> AsyncStream<LitePurchaseUpdate> { AsyncStream { $0.finish() } }
    func finish(_ event: LitePurchaseEvent) async throws { finishes += 1; pending = false }
}

private actor SessionWire: LiteAuthenticatedTransport {
    var owner = "synthetic-native-service", registrations = 0
    var paths = [String](), session: [String: Any]?, ended = false, closed = false
    var production = false, lostConfirmation = false, lostRun = false
    var afterContext: (@Sendable () async -> Void)?
    var activeWounds = [String](), woundRevision = 0
    let golden: [String: Any]
    init() throws {
        let file = try XCTUnwrap(Bundle(for: LiteNativeSessionTests.self).url(forResource: "lite_service_operation", withExtension: "json"))
        golden = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(contentsOf: file)) as? [String: Any])
    }
    func installation() -> String { owner }
    func register() -> String { registrations += 1; return owner }
    func counts() -> [String: Int] {
        var r = Dictionary(paths.map { ($0, 1) }, uniquingKeysWith: +); r["registration"] = registrations; return r
    }
    func setProduction() { production = true }
    func loseConfirmation() { lostConfirmation = true }
    func loseRun() { lostRun = true }
    func onContext(_ closure: @escaping @Sendable () async -> Void) { afterContext = closure }
    func changeOwner() { owner = "other-installation" }
    func send(_ request: URLRequest, installation: String) async throws -> (Data, HTTPURLResponse) {
        XCTAssertEqual(installation, owner)
        let path = request.url!.path.replacingOccurrences(of: "/api/v1/lite/", with: "")
        paths.append(path)
        let body = (try? JSONSerialization.jsonObject(with: request.httpBody ?? Data())) as? [String: Any] ?? [:]
        let operation = body["operation"] as? String ?? ""
        var result = [String: Any](), code = 200
        switch path {
        case "account/challenge":
            ended = false
            result = ["v": 1, "installation": owner, "operation": operation, "nonce": sessionHash,
                "state": String(repeating: "b", count: 64), "created_at": sessionTime,
                "expires_at": sessionTime + 300, "audience": "com.woundai.lite", "authorization_code_required": true]
        case "account/confirm":
            session = ["v": 1, "state": "signed_in", "installation": owner, "operation": operation,
                "account_sha256": sessionHash, "app_account_token": sessionToken, "expires_at": sessionTime + 900]
            result = session!
        case "account/status":
            guard !ended, !closed, let current = session else { throw LiteAccountFailure.unavailable }
            result = current
        case "account/logout": ended = true; result = ["v": 1, "state": "signed_out", "installation": owner, "operation": operation]
        case "service/wounds":
            XCTAssertEqual(body["account_sha256"] as? String, sessionHash)
            let action = body["action"] as? String
            var decision: String?
            if action != "status" {
                XCTAssertEqual(body["revision"] as? Int, woundRevision)
                let wound = try XCTUnwrap(body["wound_id"] as? String)
                if action == "open", !activeWounds.contains(wound) { activeWounds.append(wound); woundRevision += 1 }
                if action == "finish", activeWounds.contains(wound) { activeWounds.removeAll { $0 == wound }; woundRevision += 1 }
                decision = "confirmed"
            }
            result = ["schema":"lite.wounds/1", "account_sha256":sessionHash,"revision":woundRevision,
                      "limit":5,"active":activeWounds]
            if let decision { result["decision"] = decision }
        case "purchase/context":
            result = ["v": 1, "operation": operation, "installation": owner, "account_sha256": sessionHash,
                "app_account_token": sessionToken, "registration_sha256": sessionHash, "scope_sha256": sessionHash,
                "product_id": "com.woundai.lite.plus.monthly", "environment": production ? "Production" : "Sandbox",
                "created_at": sessionTime, "expires_at": sessionTime + 900]
            let callback = afterContext; afterContext = nil; await callback?()
        case "purchase/confirm":
            if lostConfirmation { lostConfirmation = false; throw LitePurchaseFailure.unavailable }
            result = ["v": 1, "state": "associated", "account_sha256": sessionHash, "app_account_token": sessionToken,
                "transaction_id": "123", "original_transaction_id": "100", "product_id": "com.woundai.lite.plus.monthly",
                "environment": "Sandbox", "association_sha256": sessionHash]
        case "service/prepare": result = golden["prepare"] as! [String: Any]
        case "service/run":
            if lostRun { lostRun = false; throw LiteServiceFailure.invalidResponse }
            result = golden["completed"] as! [String: Any]
        case "service/status": result = golden["completed"] as! [String: Any]
        case "account/deletion/request", "account/deletion/status":
            if path == "account/deletion/request" {
                XCTAssertEqual(body["confirmation"] as? String, LiteAccountDeletionCheckpoint.confirmation)
            }
            closed = true; code = 202
            result = ["v": 1, "state": "account_deletion_pending", "operation": operation, "installation": owner,
                "requested_at": sessionTime, "inventory_sha256": sessionHash, "grant_total": 1, "revocation_scan": 1,
                "grants_examined": 0, "grants_revoked": 0, "apple_revocation": "not_started", "data_disposition": "not_started",
                "account_deleted": false, "subscription_cancelled": false, "local_records_preserved": true, "research_withdrawal":"requested"]
        default: XCTFail("Unexpected route " + path); throw LiteAccountFailure.invalid
        }
        return (try JSONSerialization.data(withJSONObject: result), HTTPURLResponse(url: request.url!, statusCode: code, httpVersion: nil, headerFields: nil)!)
    }
    func image() -> Data { Data(base64Encoded: golden["jpeg"] as! String)! }
    func capture() -> String { golden["capture_id"] as! String }
}

@MainActor final class LiteNativeSessionTests: XCTestCase {
    private let origin = URL(string: "https://synthetic.invalid")!
    private func runtime(_ wire: SessionWire, _ journal: any LiteServiceJournal,
                         apple: SessionApple? = nil, storeKit: SessionStoreKit? = nil,
                         origin: URL? = nil, time: Int = sessionTime, captures: LiteCaptureDraftStore? = nil) throws -> LiteNativeSession {
        try LiteNativeSession(origin: origin ?? self.origin, transport: wire, identityProvider: apple ?? SessionApple(),
            purchaseProvider: storeKit ?? SessionStoreKit(), journal: journal, captures: captures, register: { await wire.register() },
            now: { Date(timeIntervalSince1970: Double(time)) })
    }
    func testConstructionAndDefaultRootDoNotRegisterOrSend() async throws {
        let wire = try SessionWire(), journal = SessionJournal(), kit = SessionStoreKit()
        let r = try runtime(wire, journal, storeKit: kit)
        let before = await wire.counts(), rows = await journal.snapshot()
        XCTAssertEqual(before, ["registration": 0]); XCTAssertTrue(rows.isEmpty); XCTAssertEqual(kit.buys, 0)
        XCTAssertNil(LiteRootView(lidarAvailable: false).nativeSession)
        XCTAssertTrue(LiteRootView(lidarAvailable: false, nativeSession: r).nativeSession === r)
    }

    #if WOUND_LITE_NATIVE_SANDBOX
    func testCandidateAcceptsOnlyItsExactSignedBundleAndDedicatedOrigin() throws {
        let binding = LiteNativeCandidateBinding.self
        XCTAssertTrue(LiteNativeBuild.includesService)
        XCTAssertEqual(try binding.origin(bundleID: "com.woundai.lite", configured: binding.address).absoluteString,
                       binding.address)
        XCTAssertNotEqual(binding.audience, Bundle.main.object(forInfoDictionaryKey: "LiteAttestAudience") as? String)
        for value: String? in [nil, "", LiteBackendConfiguration.dedicatedOrigin, binding.address + "/",
                               binding.address + "?env=Sandbox", binding.address + ".example.org",
                               binding.address.replacingOccurrences(of: "https:", with: "http:")] {
            XCTAssertThrowsError(try binding.origin(bundleID: "com.woundai.lite", configured: value))
        }
        XCTAssertThrowsError(try binding.origin(bundleID: "com.woundai.app", configured: binding.address))
    }
    #else
    func testOrdinaryBuildOmitsNativeCandidateEntry() {
        XCTAssertFalse(LiteNativeBuild.includesService)
    }
    #endif
    func testTrackingUsesOriginalAccountAndDoesNotSilentlyReopenRemoteEnd() async throws {
        let wire = try SessionWire()
        let session = try runtime(wire, SessionJournal())
        let id = try LiteWoundRoster.newID(for: UUID().uuidString)
        let local = try await session.woundPermit(id:id,owner:nil)
        XCTAssertNil(local)
        await session.identity.signIn()
        let first = try await session.woundPermit(id:id, owner:nil)
        XCTAssertEqual(first?.account, sessionHash)
        let roster = try await session.woundList()
        XCTAssertEqual(roster?.remoteGroups(excluding:[]).first?.id, id)
        let group = LiteWoundGroup(id:id,name:"合成傷口",account:sessionHash)
        _ = try await session.finishTracking(group)
        do {
            _ = try await session.woundPermit(id:id,owner:sessionHash)
            XCTFail("stale phone reopened a finished wound")
        } catch LiteWoundTrackingError.ended {}
        let restored = try await session.woundPermit(id:id,owner:sessionHash,resume:true)
        XCTAssertEqual(restored?.id,id)
        await session.identity.signOut()
        do {
            _ = try await session.woundPermit(id:id,owner:sessionHash)
            XCTFail("signed out account wrote to a bound wound")
        } catch LiteWoundTrackingError.otherAccount {}
        let counts = await wire.counts()
        XCTAssertNil(counts["service/run"])
        XCTAssertNil(counts["purchase/confirm"])
    }

    func testLegacyGroupRetainsItsTimelineIDWithoutDuplicatingRemoteFolder() throws {
        let group = LiteWoundGroup(id:"old human-readable ID",name:"原傷口")
        let opaque = try LiteWoundRoster.wireID(group.id)
        XCTAssertTrue(LiteServiceBytes.hex(opaque,32))
        let remote = LiteWoundRoster(schema:"lite.wounds/1",account_sha256:sessionHash,revision:1,active:[opaque],limit:1,decision:nil)
        XCTAssertTrue(remote.remoteGroups(excluding:[group]).isEmpty)
        let remoteGroup = try XCTUnwrap(remote.remoteGroups(excluding:[]).first)
        XCTAssertEqual(try LiteWoundRoster.wireID(remoteGroup.id),opaque)
    }

    func testMalformedRosterRejectsDuplicateIDsAndUnexpectedGrant() throws {
        for (ids, limit) in [([String(repeating:"a",count:32),String(repeating:"a",count:32)],5), ([],6), (["invalid"],1)] {
            let roster = LiteWoundRoster(schema:"lite.wounds/1",account_sha256:sessionHash,revision:1,active:ids,limit:limit,decision:nil)
            XCTAssertThrowsError(try roster.validate(account:sessionHash))
        }
    }

    func testRegistrationIsExplicitAndDoesNotSignInOrBuy() async throws {
        let wire = try SessionWire(), apple = SessionApple(), kit = SessionStoreKit()
        let r = try runtime(wire, SessionJournal(), apple: apple, storeKit: kit)
        await r.registerDevice(); let counts = await wire.counts()
        XCTAssertEqual(counts, ["registration": 1]); XCTAssertEqual(apple.calls, 0); XCTAssertEqual(kit.buys, 0)
        XCTAssertEqual(r.registration, .confirmed)
    }

    func testCheckingExistingRegistrationAfterRestartNeverRegistersOrSends() async throws {
        let wire = try SessionWire(), journal = SessionJournal()
        for _ in 0..<2 {
            let session = try runtime(wire, journal)
            await session.checkExistingRegistration()
            XCTAssertEqual(session.registration, .confirmed)
        }
        let counts = await wire.counts(), rows = await journal.snapshot()
        XCTAssertEqual(counts, ["registration": 0])
        XCTAssertTrue(rows.isEmpty)
    }

    func testMissingAndUnreadableRegistrationCannotBeConfused() async throws {
        let wire = try SessionWire(), journal = SessionJournal()
        for locked in [false, true] {
            let session = try LiteNativeSession(origin: origin, transport: wire, identityProvider: SessionApple(),
                purchaseProvider: SessionStoreKit(), journal: journal, register: { await wire.register() },
                existing: { if locked { throw LiteAttestFailure.unavailable }; return nil })
            await session.checkExistingRegistration()
            XCTAssertEqual(session.registration, locked ? .unreadable : .required)
            if locked { await session.registerDevice() }
        }
        let counts = await wire.counts(), rows = await journal.snapshot()
        XCTAssertEqual(counts, ["registration": 0])
        XCTAssertTrue(rows.isEmpty)
    }
    func testBeforeSignInNeitherPurchaseNorRecognitionIsDispatched() async throws {
        let wire = try SessionWire(), r = try runtime(wire, SessionJournal())
        await r.purchases.purchaseDisplayedTestOffer()
        let id = await wire.capture(), image = await wire.image()
        _ = try await r.service.enqueue(captureID: id, jpeg: image)
        let pending = try await r.service.resume(id) { _ in image }
        XCTAssertEqual(pending.note, "unavailable")
        let counts = await wire.counts(); XCTAssertEqual(counts, ["registration": 0])
    }
    func testSignInRecognitionPurchaseRestoreShareAccountAndNoResearchRoutes() async throws {
        let wire = try SessionWire(), journal = SessionJournal(), kit = SessionStoreKit()
        let r = try runtime(wire, journal, storeKit: kit)
        await r.identity.signIn()
        let id = await wire.capture(), image = await wire.image()
        _ = try await r.service.enqueue(captureID: id, jpeg: image)
        let done = try await r.service.resume(id) { _ in image }; XCTAssertEqual(done.note, "confirmed")
        await r.purchases.purchaseDisplayedTestOffer(); await r.purchases.restore()
        XCTAssertEqual(kit.buys, 1); XCTAssertEqual(kit.restores, 1); XCTAssertEqual(kit.finishes, 1)
        let restarted = try runtime(wire, journal, storeKit: kit)
        let old = try await restarted.service.resume(id) { _ in XCTFail("Must not reload JPEG"); return nil }
        XCTAssertEqual(old, done)
        let counts = await wire.counts()
        XCTAssertEqual(counts["service/run"], 1); XCTAssertEqual(counts["purchase/confirm"], 1)
        XCTAssertTrue(counts.keys.allSatisfy { !$0.contains("research") && !$0.contains("annotation") && $0 != "segment" })
    }
    func testLostPurchaseConfirmationRecoversWithoutSecondPurchase() async throws {
        let wire = try SessionWire(), journal = SessionJournal(), kit = SessionStoreKit()
        let r = try runtime(wire, journal, storeKit: kit); await r.identity.signIn(); await wire.loseConfirmation()
        await r.purchases.purchaseDisplayedTestOffer(); XCTAssertEqual(kit.finishes, 0)
        let restarted = try runtime(wire, journal, storeKit: kit); await restarted.purchases.reconcile()
        XCTAssertEqual(kit.buys, 1); XCTAssertEqual(kit.finishes, 1)
        await restarted.purchases.reconcile(); XCTAssertEqual(kit.finishes, 1)
    }
    func testLogoutStopsPurchasesAndPreservesJournal() async throws {
        let wire = try SessionWire(), journal = SessionJournal(), kit = SessionStoreKit()
        let r = try runtime(wire, journal, storeKit: kit); await r.identity.signIn(); await r.identity.signOut()
        let before = await journal.snapshot(); await r.purchases.purchaseDisplayedTestOffer()
        XCTAssertEqual(kit.buys, 0); let after = await journal.snapshot(); XCTAssertEqual(before, after)
        let counts = await wire.counts(); XCTAssertNil(counts["purchase/context"])
    }
    func testLogoutDuringContextResponseCannotOpenStoreKit() async throws {
        let wire = try SessionWire(), kit = SessionStoreKit(), r = try runtime(wire, SessionJournal(), storeKit: kit)
        await r.identity.signIn(); await wire.onContext { await r.identity.signOut() }
        await r.purchases.purchaseDisplayedTestOffer(); XCTAssertEqual(kit.buys, 0)
        let counts = await wire.counts(); XCTAssertEqual(counts["purchase/context"], 1); XCTAssertNil(counts["purchase/confirm"])
    }
    func testDeletionIntentFencesAllOrdinaryClientsAcrossRestartButProgressWorks() async throws {
        let wire = try SessionWire(), journal = SessionJournal(), kit = SessionStoreKit()
        let r = try runtime(wire, journal, storeKit: kit); await r.identity.signIn()
        let d = r.deletion(pauseResearch:{}); await d.requestConfirmed()
        XCTAssertNotNil(d.record?.receipt); XCTAssertFalse(d.record?.receipt?.account_deleted ?? true)
        let operation = d.record?.operation
        let restarted = try runtime(wire, journal, storeKit: kit)
        await restarted.purchases.purchaseDisplayedTestOffer(); XCTAssertEqual(kit.buys, 0)
        let id = await wire.capture(), image = await wire.image()
        _ = try await restarted.service.enqueue(captureID: id, jpeg: image)
        _ = try await restarted.service.resume(id) { _ in image }
        let status = restarted.deletion(pauseResearch:{}); await status.retry()
        XCTAssertEqual(status.record?.operation, operation); XCTAssertNotNil(status.record?.receipt)
        let counts = await wire.counts(); XCTAssertNil(counts["service/prepare"]); XCTAssertNil(counts["purchase/context"])
        XCTAssertEqual(counts["account/deletion/request"], 1); XCTAssertGreaterThan(counts["account/deletion/status"] ?? 0, 0)
    }
    func testFailedIdentityJournalPreventsAppleAndServiceCalls() async throws {
        let wire = try SessionWire(), journal = SessionJournal(), apple = SessionApple(), kit = SessionStoreKit()
        let r = try runtime(wire, journal, apple: apple, storeKit: kit); await journal.fail(true)
        await r.identity.signIn(); await r.purchases.purchaseDisplayedTestOffer()
        XCTAssertEqual(apple.calls, 0); XCTAssertEqual(kit.buys, 0)
        let counts = await wire.counts(); XCTAssertNil(counts["account/confirm"]); XCTAssertNil(counts["purchase/context"])
    }
    func testProductionContextNeverReachesSandboxStoreKit() async throws {
        let wire = try SessionWire(), kit = SessionStoreKit(), r = try runtime(wire, SessionJournal(), storeKit: kit)
        await r.identity.signIn(); await wire.setProduction(); await r.purchases.purchaseDisplayedTestOffer()
        XCTAssertEqual(kit.buys, 0)
    }
    func testOriginChangeCannotReuseSavedIdentity() async throws {
        let wire = try SessionWire(), journal = SessionJournal(), kit = SessionStoreKit()
        let first = try runtime(wire, journal); await first.identity.signIn()
        let second = try runtime(wire, journal, storeKit: kit, origin: URL(string: "https://other.invalid")!)
        await second.purchases.purchaseDisplayedTestOffer(); XCTAssertEqual(kit.buys, 0)
        let counts = await wire.counts(); XCTAssertNil(counts["purchase/context"])
    }
    func testChangedInstallationDoesNotAdoptOldSession() async throws {
        let wire = try SessionWire(), kit = SessionStoreKit(), r = try runtime(wire, SessionJournal(), storeKit: kit)
        await r.identity.signIn(); await wire.changeOwner(); await r.purchases.purchaseDisplayedTestOffer()
        XCTAssertEqual(kit.buys, 0)
    }
    func testPurposeTransportRejectsResearchAndForeignURLWithoutDispatch() async throws {
        let wire = try SessionWire(), r = try runtime(wire, SessionJournal())
        await r.identity.signIn()
        let transport = try XCTUnwrap(r.purchases.service as? LitePurchaseHTTPService).transport
        for text in ["https://foreign.invalid/api/v1/lite/purchase/context", "https://synthetic.invalid/api/v1/lite/segment",
                     "https://synthetic.invalid/api/v1/lite/purchase/context?override=1"] {
            var request = URLRequest(url: URL(string: text)!); request.httpMethod = "POST"
            do { _ = try await transport.send(request, installation: "synthetic-native-service"); XCTFail(text) } catch { }
        }
        let counts = await wire.counts(); XCTAssertNil(counts["purchase/context"]); XCTAssertNil(counts["segment"])
    }
    func testInvalidOriginsFailBeforeAnySideEffects() async throws {
        let wire = try SessionWire(), journal = SessionJournal()
        for text in ["http://synthetic.invalid", "https://synthetic.invalid/", "https://synthetic.invalid:443",
                     "https://user@synthetic.invalid", "https://synthetic.invalid?x=1"] {
            XCTAssertThrowsError(try runtime(wire, journal, origin: URL(string: text)!))
        }
        let counts = await wire.counts(), rows = await journal.snapshot()
        XCTAssertEqual(counts, ["registration": 0]); XCTAssertTrue(rows.isEmpty)
    }

    func testSavedRecordAndOriginalFilesRemainByteIdenticalAfterServiceRetry() async throws {
        let wire = try SessionWire(), journal = SessionJournal(), r = try runtime(wire, journal)
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let index = dir.appendingPathComponent("records.json"), image = await wire.image()
        let rgb = dir.appendingPathComponent("original.jpg"), depth = dir.appendingPathComponent("depth.fixture")
        try image.write(to: rgb); try Data("synthetic-depth-preserved".utf8).write(to: depth)
        let store = LiteStore(fileURL: index)
        let record = LiteRecord(id: "00000000-0000-0000-0000-000000000001", dateISO: "2026-10-29T00:00:00Z",
            surfaceCm2: 10.04, projectedCm2: 10.01, quality: "synthetic", imageName: "original.jpg",
            source: "manual", polysJson: "[[[1,1],[12,1],[12,12]]]", depthName: "depth.fixture")
        XCTAssertTrue(store.add(record))
        let before = try [index, rgb, depth].map { try Data(contentsOf: $0) }
        await r.identity.signIn()
        let done = try await r.recognizeSaved(recordID: record.id, jpeg: image)
        let again = try await r.recognizeSaved(recordID: record.id, jpeg: image)
        XCTAssertEqual(done, again); XCTAssertEqual(done.note, "confirmed")
        XCTAssertEqual(try [index, rgb, depth].map { try Data(contentsOf: $0) }, before)
        let counts = await wire.counts()
        XCTAssertEqual(counts["service/prepare"], 1); XCTAssertEqual(counts["service/run"], 1)
        XCTAssertFalse(counts.keys.contains { $0.contains("research") || $0.contains("annotation") })
    }

    func testSavedRecognitionLostReplyUsesStatusAfterRestartWithoutSecondRun() async throws {
        let wire = try SessionWire(), journal = SessionJournal(), r = try runtime(wire, journal)
        let image = await wire.image(), id = "00000000-0000-0000-0000-000000000001"
        await r.identity.signIn(); await wire.loseRun()
        let waiting = try await r.recognizeSaved(recordID: id, jpeg: image)
        XCTAssertEqual(waiting.note, "unavailable")
        let restarted = try runtime(wire, journal, time: sessionTime + 10)
        let done = try await restarted.recognizeSaved(recordID: id, jpeg: image)
        XCTAssertEqual(done.note, "confirmed"); XCTAssertEqual(done.operation, waiting.operation)
        let counts = await wire.counts(); XCTAssertEqual(counts["service/run"], 1); XCTAssertEqual(counts["service/status"], 1)
    }

    func testSavedRecognitionCannotReuseRecordIDWithDifferentJPEG() async throws {
        let wire = try SessionWire(), journal = SessionJournal(), r = try runtime(wire, journal)
        let image = await wire.image(), id = "00000000-0000-0000-0000-000000000001"
        await r.identity.signIn(); _ = try await r.recognizeSaved(recordID: id, jpeg: image)
        let before = await journal.snapshot(), requests = await wire.counts()
        do { _ = try await r.recognizeSaved(recordID: id, jpeg: image + Data([0])); XCTFail() } catch { }
        let after = await journal.snapshot(), requestsAfter = await wire.counts()
        XCTAssertEqual(before, after); XCTAssertEqual(requests, requestsAfter)
    }

    func testMissingJournalIsOptionalButCorruptionStopsWithoutNewOperation() async throws {
        let wire = try SessionWire(), journal = SessionJournal(), r = try runtime(wire, journal)
        let id = await wire.capture(), image = await wire.image()
        let missing = try await r.service.storedIfPresent(id); XCTAssertNil(missing)
        _ = try await r.service.enqueue(captureID: id, jpeg: image); try await journal.corruptRecognition()
        let before = await journal.snapshot()
        do { _ = try await r.service.storedIfPresent(id); XCTFail() } catch { }
        do { _ = try await r.recognizeSaved(recordID: "00000000-0000-0000-0000-000000000001", jpeg: image); XCTFail() } catch { }
        let after = await journal.snapshot(), counts = await wire.counts()
        XCTAssertEqual(before, after); XCTAssertEqual(counts, ["registration": 0])
    }

    func testProtectedFileJournalReopensAllPurposesWithoutNetworkOrDuplicatePurchase() async throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: dir) }
        let wire = try SessionWire(), kit = SessionStoreKit()
        let first = try runtime(wire, LiteServiceFileJournal(directory: dir), storeKit: kit)
        let image = await wire.image(), id = "00000000-0000-0000-0000-000000000001"
        await first.identity.signIn(); await first.purchases.purchaseDisplayedTestOffer()
        let done = try await first.recognizeSaved(recordID: id, jpeg: image), before = await wire.counts()
        let reopened = try runtime(wire, LiteServiceFileJournal(directory: dir), storeKit: kit)
        let saved = try await reopened.service.storedIfPresent(LiteNativeSession.captureID(recordID: id))
        XCTAssertEqual(saved, done); let noReadHTTP = await wire.counts(); XCTAssertEqual(before, noReadHTTP)
        await reopened.purchases.reconcile(); XCTAssertEqual(kit.buys, 1); XCTAssertEqual(kit.finishes, 1)
        let files = try FileManager.default.contentsOfDirectory(at: dir, includingPropertiesForKeys: [.isExcludedFromBackupKey])
        XCTAssertGreaterThanOrEqual(files.filter { $0.pathExtension == "json" }.count, 3)
        for file in files { XCTAssertEqual(try file.resourceValues(forKeys: [.isExcludedFromBackupKey]).isExcludedFromBackup, true) }
    }

    func testInvalidSavedRecordIdentifierCannotCreateAnOperation() async throws {
        let wire = try SessionWire(), journal = SessionJournal(), r = try runtime(wire, journal), image = await wire.image()
        do { _ = try await r.recognizeSaved(recordID: "not-a-record", jpeg: image); XCTFail() } catch { }
        let counts = await wire.counts(), rows = await journal.snapshot()
        XCTAssertEqual(counts, ["registration": 0]); XCTAssertTrue(rows.isEmpty)
    }
}


extension LiteNativeSessionTests {
    /// As in the candidate root, SwiftUI retains the measurement tab while the
    /// service session changes from nil to ready. Observe recovery on disk.
    func testMountedMeasurementAttachesLateSessionAndRecoversCommittedDraft() async throws {
        final class Entry: ObservableObject { @Published var session: LiteNativeSession? }
        struct Screen: View {
            @ObservedObject var entry: Entry
            let store: LiteStore
            let mounted: XCTestExpectation
            var body: some View {
                LiteMeasureView(store: store, nativeSession: entry.session)
                    .onAppear { mounted.fulfill() }
            }
        }
        let path = captureFiles(); defer { try? FileManager.default.removeItem(at: path) }
        let files = LiteCaptureDraftStore(directory: path), wire = try SessionWire()
        let session = try runtime(wire, SessionJournal(), captures: files)
        let draft = try await captureFixture(wire, in: files)
        let store = LiteStore(fileURL: path.appendingPathComponent("index.json"))
        let original = LiteMeasureVM(native: session)
        await original.restoreDraft(draft.id, in: store)
        await original.applyManual([[[5,5],[5,55],[55,55],[55,5]]], correctionIoU: 0.7)
        let record = try XCTUnwrap(original.saveCapture(in: store, selectedWoundID: "",
            location: .init(side: "right", site: "heel")))
        defer { store.images.delete(record.imageName); if let d = record.depthName { store.images.delete(d) } }
        let committed = Set([record.id]), entry = Entry()
        let mounted = expectation(description: "Measurement mounted before the session is ready")
        let window = UIWindow(frame: CGRect(x: 0, y: 0, width: 430, height: 932))
        let host = UIHostingController(rootView: Screen(entry: entry, store: store, mounted: mounted))
        window.rootViewController = host; window.makeKeyAndVisible()
        defer { window.isHidden = true; window.rootViewController = nil }
        await fulfillment(of: [mounted], timeout: 5)
        let before = try await files.unfinalized(savedIDs: committed)
        XCTAssertEqual(before.map(\.id), [record.id])
        entry.session = session
        var pending = before
        for _ in 0..<50 {
            pending = try await files.unfinalized(savedIDs: committed)
            if pending.isEmpty { break }
            try await Task.sleep(for: .milliseconds(50))
        }
        XCTAssertTrue(pending.isEmpty, "The already-mounted tab must resume its original draft after setup")
        XCTAssertEqual(store.images.rawBytes(record.imageName), draft.jpeg)
        XCTAssertEqual(store.records.map(\.id), [record.id])
        let counts = await wire.counts()
        XCTAssertEqual(counts, ["registration":0])
    }

    private func captureDepth() -> DepthCapture {
        DepthCapture(map: Array(repeating: 0.3, count: 64), width: 8, height: 8,
            fx: 50, fy: 50, cx: 32, cy: 32, refWidth: 64, refHeight: 64,
            accuracy: "absolute", filtered: false, rgbWidth: 64, rgbHeight: 64, sourceExifOrientation: 1)
    }
    func testLateSessionPreservesOfflineEditsAndAppliesToTheNextCapture() async throws {
        let path = captureFiles(); defer { try? FileManager.default.removeItem(at: path) }
        let files = LiteCaptureDraftStore(directory: path), wire = try SessionWire()
        let session = try runtime(wire, SessionJournal(), captures: files)
        let vm = LiteMeasureVM(captureParticipation: { nil })
        let store = LiteStore(fileURL: path.appendingPathComponent("index.json"))
        let photoBytes = await wire.image(), photo = try XCTUnwrap(UIImage(data: photoBytes))
        _ = await vm.ingest(photo, depth: captureDepth())
        let jpeg = try XCTUnwrap(vm.captureJPEG)
        vm.attachNativeSession(session)
        XCTAssertFalse(vm.usesServiceDraft)
        let contour = [[[5,5],[5,55],[55,55],[55,5]]]
        await vm.applyManual(contour, correctionIoU: 0.7)
        XCTAssertEqual(vm.polys, contour)
        let saved = try XCTUnwrap(vm.saveCapture(in: store, selectedWoundID: "",
            location: .init(side: "right", site: "heel")))
        defer { store.images.delete(saved.imageName); if let d = saved.depthName { store.images.delete(d) } }
        XCTAssertEqual(store.images.rawBytes(saved.imageName), jpeg)
        XCTAssertNil(saved.cloudSync); XCTAssertNil(saved.researchCapture)
        XCTAssertEqual(vm.finishSavedCapture(in: store), saved.id)
        _ = await vm.ingest(photo, depth: captureDepth())
        let next = try XCTUnwrap(vm.draft)
        XCTAssertTrue(vm.usesServiceDraft); XCTAssertNil(next.researchPin)
        XCTAssertEqual(next.jpeg, jpeg)
        XCTAssertEqual(store.records.map(\.id), [saved.id])
        let counts = await wire.counts()
        XCTAssertEqual(counts, ["registration":0])
    }

    func testPersonalRecognitionWithoutResearchKeepsTwoSeparateLocalTimelines() async throws {
        let path = captureFiles(); defer { try? FileManager.default.removeItem(at: path) }
        let files = LiteCaptureDraftStore(directory: path), wire = try SessionWire()
        let session = try runtime(wire, SessionJournal(), captures: files)
        await session.identity.signIn()
        let vm = LiteMeasureVM(native: session, captureParticipation: { nil })
        let store = LiteStore(fileURL: path.appendingPathComponent("index.json"))
        defer { for row in store.records {
            store.images.delete(row.imageName); if let d = row.depthName { store.images.delete(d) }
        } }
        let firstDraft = try await captureFixture(wire, in: files)
        await vm.restoreDraft(firstDraft.id, in: store)
        await vm.recognizeNative()
        XCTAssertEqual(vm.serviceResult?.note, "confirmed")
        let firstPermit = try await session.woundPermit(id: LiteWoundRoster.newID(for: firstDraft.id), owner: nil)
        let first = try XCTUnwrap(vm.saveCapture(in: store, selectedWoundID: "",
            location: .init(side: "right", site: "heel"), permit: firstPermit))
        XCTAssertEqual(vm.finishSavedCapture(in: store), first.id)
        _ = await vm.ingest(try XCTUnwrap(UIImage(data: firstDraft.jpeg)), depth: captureDepth())
        let secondDraft = try XCTUnwrap(vm.draft)
        await vm.applyManual([[[5,5],[5,55],[55,55],[55,5]]], correctionIoU: 0.7)
        let secondPermit = try await session.woundPermit(id: LiteWoundRoster.newID(for: secondDraft.id), owner: nil)
        let second = try XCTUnwrap(vm.saveCapture(in: store, selectedWoundID: "",
            location: .init(side: "left", site: "palm"), permit: secondPermit))
        XCTAssertNotEqual(first.woundID, second.woundID)
        let counts = await wire.counts()
        let group = try XCTUnwrap(store.wounds.first { $0.id == first.woundID })
        let selection = try LiteTimelineSelection(group: group, records: store.records)
        let pdf = try LiteTimelinePDF.export(selection, photos: true, image: { row in
            XCTAssertEqual(row.id, first.id)
            return store.images.rawBytes(row.imageName).flatMap { UIImage(data: $0) }
        }, directory: path.appendingPathComponent("share"))
        defer { pdf.remove() }
        let text = try XCTUnwrap(PDFDocument(url: pdf.url)?.string)
        XCTAssertTrue(text.contains("足跟")); XCTAssertFalse(text.contains("手掌"))
        XCTAssertEqual(selection.rows.map(\.id), [first.id])
        XCTAssertTrue(store.records.allSatisfy { $0.researchCapture == nil && $0.cloudSync == nil })
        XCTAssertEqual(counts["service/run"], 1); XCTAssertEqual(counts["service/prepare"], 1)
        XCTAssertFalse(counts.keys.contains { $0.contains("research") || $0 == "segment" })
        let after = await wire.counts()
        XCTAssertEqual(counts, after)
    }
    private func captureFiles() -> URL {
        FileManager.default.temporaryDirectory.appendingPathComponent("capture-test-" + UUID().uuidString)
    }
    private func captureFixture(_ wire: SessionWire, in captures: LiteCaptureDraftStore) async throws -> LiteCaptureDraft {
        try await captures.create(jpeg: wire.image(), depth: captureDepth(),
            id: UUID(uuidString: "00000000-0000-0000-0000-000000000001")!, now: Date(timeIntervalSince1970: 1_800_000_000))
    }
    func testCameraDraftPersistsExactPairBeforeAnyServiceOrResearchTransmission() async throws {
        let path = captureFiles(); defer { try? FileManager.default.removeItem(at: path) }
        let wire = try SessionWire(), files = LiteCaptureDraftStore(directory: path)
        let r = try runtime(wire, SessionJournal(), captures: files)
        let vm = LiteMeasureVM(native: r), image = await wire.image()
        let consent = LitePrefs.researchConsent
        _ = await vm.ingest(try XCTUnwrap(UIImage(data: image)), depth: captureDepth())
        let draft = try XCTUnwrap(vm.draft), reloaded = try await files.load(draft.id)
        XCTAssertEqual(reloaded.jpeg, vm.captureJPEG)
        XCTAssertEqual(try reloaded.depth().map.map(\.bitPattern), captureDepth().map.map(\.bitPattern))
        let counts = await wire.counts(); XCTAssertEqual(counts, ["registration": 0])
        XCTAssertEqual(LitePrefs.researchConsent, consent); XCTAssertNil(vm.cloudBinding)
        let encrypted = try Data(contentsOf: path.appendingPathComponent(draft.id + ".enc"))
        XCTAssertNil(encrypted.range(of: draft.jpeg)); XCTAssertNil(encrypted.range(of: Data("polygons".utf8)))
    }
    func testDraftDiskFailureNeverEnqueuesOrTransmitsAndCanRetryLocally() async throws {
        let path = captureFiles(); defer { try? FileManager.default.removeItem(at: path) }
        let wire = try SessionWire(), files = LiteCaptureDraftStore(directory: path, write: { _, _ in throw LiteServiceFailure.conflict })
        let r = try runtime(wire, SessionJournal(), captures: files), vm = LiteMeasureVM(native: r)
        let jpeg = await wire.image()
        _ = await vm.ingest(try XCTUnwrap(UIImage(data: jpeg)), depth: captureDepth())
        XCTAssertNil(vm.draft); XCTAssertNotNil(vm.captureJPEG); XCTAssertNotNil(vm.depth)
        await vm.recognizeNative(); await vm.retryDraftSave()
        let counts = await wire.counts(); XCTAssertEqual(counts, ["registration": 0])
        XCTAssertNil(vm.draft); XCTAssertNotNil(vm.captureJPEG)
    }
    func testNativeCaptureRecoversAndReusesCompletedResultWithNoSecondRun() async throws {
        let path = captureFiles(); defer { try? FileManager.default.removeItem(at: path) }
        let wire = try SessionWire(), journal = SessionJournal(), files = LiteCaptureDraftStore(directory: path)
        let draft = try await captureFixture(wire, in: files)
        let r = try runtime(wire, journal, captures: files); await r.identity.signIn()
        let store = LiteStore(fileURL: path.appendingPathComponent("index.json")), vm = LiteMeasureVM(native: r)
        let countsBefore = await wire.counts()
        await vm.restoreDraft(draft.id, in: store)
        let countsRestored = await wire.counts(); XCTAssertEqual(countsBefore, countsRestored)
        await vm.recognizeNative()
        XCTAssertEqual(vm.serviceResult?.note, "confirmed"); XCTAssertFalse(vm.polys.isEmpty)
        XCTAssertNotNil(vm.estimate); XCTAssertFalse(vm.hasManualContour); XCTAssertNil(vm.cloudBinding)
        let saved = try await files.load(draft.id)
        let r2 = try runtime(wire, journal, captures: files), vm2 = LiteMeasureVM(native: r2)
        await vm2.restoreDraft(draft.id, in: store)
        XCTAssertEqual(vm2.polys, saved.polygons); XCTAssertEqual(vm2.captureJPEG, draft.jpeg)
        await vm2.recognizeNative()
        let counts = await wire.counts()
        XCTAssertEqual(counts["service/run"], 1); XCTAssertEqual(counts["service/prepare"], 1)
        XCTAssertFalse(counts.keys.contains { $0.contains("research") || $0 == "segment" })
        XCTAssertEqual(try XCTUnwrap(vm2.depth).map.map(\.bitPattern), captureDepth().map.map(\.bitPattern))
    }
    func testLostRunReplyResumesFromSameDraftWithStatusOnly() async throws {
        let path = captureFiles(); defer { try? FileManager.default.removeItem(at: path) }
        let wire = try SessionWire(), journal = SessionJournal(), files = LiteCaptureDraftStore(directory: path)
        let draft = try await captureFixture(wire, in: files)
        let r = try runtime(wire, journal, captures: files); await r.identity.signIn(); await wire.loseRun()
        let store = LiteStore(fileURL: path.appendingPathComponent("index.json")), vm = LiteMeasureVM(native: r)
        await vm.restoreDraft(draft.id, in: store); await vm.recognizeNative()
        XCTAssertEqual(vm.serviceResult?.note, "unavailable")
        let restart = try runtime(wire, journal, time: sessionTime + 6, captures: files)
        let vm2 = LiteMeasureVM(native: restart)
        await vm2.restoreDraft(draft.id, in: store); await vm2.recognizeNative()
        XCTAssertEqual(vm2.serviceResult?.note, "confirmed")
        let counts = await wire.counts(); XCTAssertEqual(counts["service/run"], 1)
        XCTAssertEqual(counts["service/prepare"], 1); XCTAssertEqual(counts["service/status"], 1)
    }
    func testManualContourSurvivesRecognitionUntilExplicitReplacement() async throws {
        let path = captureFiles(); defer { try? FileManager.default.removeItem(at: path) }
        let wire = try SessionWire(), files = LiteCaptureDraftStore(directory: path)
        let draft = try await captureFixture(wire, in: files)
        let r = try runtime(wire, SessionJournal(), captures: files); await r.identity.signIn()
        let store = LiteStore(fileURL: path.appendingPathComponent("index.json")), vm = LiteMeasureVM(native: r)
        await vm.restoreDraft(draft.id, in: store)
        let manual = [[[5,5],[5,55],[55,55],[55,5]]]
        await vm.applyManual(manual, correctionIoU: 0.7)
        let beforeArea = try XCTUnwrap(vm.estimate).surfaceAreaCm2
        await vm.recognizeNative()
        XCTAssertEqual(vm.polys, manual); XCTAssertTrue(vm.hasManualContour)
        XCTAssertEqual(vm.estimate?.surfaceAreaCm2, beforeArea)
        await vm.replaceWithNativeResult()
        XCTAssertNotEqual(vm.polys, manual); XCTAssertFalse(vm.hasManualContour)
        XCTAssertLessThan(try XCTUnwrap(vm.estimate).surfaceAreaCm2, beforeArea)
        let counts = await wire.counts(); XCTAssertEqual(counts["service/run"], 1)
    }
    func testRetakeKeepsBothDraftsAndRestoresManualEditExactly() async throws {
        let path = captureFiles(); defer { try? FileManager.default.removeItem(at: path) }
        let wire = try SessionWire(), files = LiteCaptureDraftStore(directory: path)
        let r = try runtime(wire, SessionJournal(), captures: files), vm = LiteMeasureVM(native: r)
        let jpeg = await wire.image()
        let image = try XCTUnwrap(UIImage(data: jpeg))
        let store = LiteStore(fileURL: path.appendingPathComponent("index.json"))
        _ = await vm.ingest(image, depth: captureDepth())
        let first = try XCTUnwrap(vm.draft)
        let manual = [[[5,5],[5,55],[55,55],[55,5]]]
        await vm.applyManual(manual, correctionIoU: 0.7)
        let before = try XCTUnwrap(vm.estimate)
        _ = await vm.ingest(image, depth: captureDepth())
        XCTAssertNotEqual(vm.draft?.id, first.id)
        await vm.reloadDrafts(in: store); XCTAssertEqual(vm.pendingCaptures.count, 2)
        await vm.restoreDraft(first.id, in: store)
        XCTAssertEqual(vm.polys, manual); XCTAssertTrue(vm.hasManualContour)
        XCTAssertEqual(vm.estimate?.surfaceAreaCm2, before.surfaceAreaCm2)
        let counts = await wire.counts(); XCTAssertEqual(counts, ["registration": 0])
    }
    func testDraftStaleEditsAndSameIDReplacementFailWithoutChangingOriginal() async throws {
        let path = captureFiles(); defer { try? FileManager.default.removeItem(at: path) }
        let wire = try SessionWire(), files = LiteCaptureDraftStore(directory: path)
        let draft = try await captureFixture(wire, in: files)
        var edited = draft; edited.polygons = [[[5,5],[5,55],[55,55],[55,5]]]
        let saved = try await files.update(edited, previous: draft)
        do { _ = try await files.update(draft, previous: draft); XCTFail("stale editor must fail") } catch { }
        do { _ = try await captureFixture(wire, in: files); XCTFail("duplicate ID must fail") } catch { }
        let current = try await files.load(draft.id); XCTAssertEqual(current, saved)
    }
    func testUnreadableDraftLibraryDoesNotPretendEmptyOrTransmit() async throws {
        let path = captureFiles(); defer { try? FileManager.default.removeItem(at: path) }
        let wire = try SessionWire(), files = LiteCaptureDraftStore(directory: path)
        let draft = try await captureFixture(wire, in: files)
        try Data("corrupt".utf8).write(to: path.appendingPathComponent(draft.id + ".enc"))
        let r = try runtime(wire, SessionJournal(), captures: files), vm = LiteMeasureVM(native: r)
        let store = LiteStore(fileURL: path.appendingPathComponent("index.json"))
        await vm.reloadDrafts(in: store); XCTAssertNotNil(vm.draftNotice)
        await vm.restoreDraft(draft.id, in: store); await vm.recognizeNative()
        XCTAssertNil(vm.draft)
        let counts = await wire.counts(); XCTAssertEqual(counts, ["registration": 0])
    }

    func testCompletedDraftWriteWithLostAcknowledgementRetriesSameCapture() async throws {
        let path = captureFiles(); defer { try? FileManager.default.removeItem(at: path) }
        let wire = try SessionWire(), files = LiteCaptureDraftStore(directory: path, write: { data, url in
            try HealthDataFiles.write(data, to: url)
            throw LiteServiceFailure.conflict
        })
        let r = try runtime(wire, SessionJournal(), captures: files), vm = LiteMeasureVM(native: r)
        let jpeg = await wire.image()
        _ = await vm.ingest(try XCTUnwrap(UIImage(data: jpeg)), depth: captureDepth())
        XCTAssertNil(vm.draft)
        let before = try await files.pending(excluding: [])
        XCTAssertEqual(before.count, 1)
        await vm.retryDraftSave()
        XCTAssertEqual(vm.draft?.id, before.first?.id)
        let after = try await files.pending(excluding: [])
        XCTAssertEqual(after.count, 1)
        let counts = await wire.counts(); XCTAssertEqual(counts, ["registration": 0])
    }

    func testCorruptServiceReceiptStillAllowsLocalDraftButNeverNewRecognition() async throws {
        let path = captureFiles(); defer { try? FileManager.default.removeItem(at: path) }
        let wire = try SessionWire(), journal = SessionJournal(), files = LiteCaptureDraftStore(directory: path)
        let draft = try await captureFixture(wire, in: files)
        let r = try runtime(wire, journal, captures: files); await r.identity.signIn()
        _ = try await r.recognizeSaved(recordID: draft.id, jpeg: draft.jpeg)
        try await journal.corruptRecognition()
        let before = await wire.counts(), vm = LiteMeasureVM(native: r)
        let store = LiteStore(fileURL: path.appendingPathComponent("index.json"))
        await vm.restoreDraft(draft.id, in: store)
        XCTAssertEqual(vm.captureJPEG, draft.jpeg); XCTAssertTrue(vm.serviceUnreadable)
        await vm.applyManual([[[5,5],[5,55],[55,55],[55,5]]], correctionIoU: 0.7)
        await vm.recognizeNative(); await vm.resumePendingRecognition()
        XCTAssertNotNil(vm.estimate); XCTAssertTrue(vm.hasManualContour)
        let after = await wire.counts(); XCTAssertEqual(before, after)
        let readBack = try await files.load(draft.id); XCTAssertEqual(readBack.polygons, vm.polys)
    }

    func testUnreadableMeasurementIndexPreventsDraftRecovery() async throws {
        let path = captureFiles(); defer { try? FileManager.default.removeItem(at: path) }
        let wire = try SessionWire(), files = LiteCaptureDraftStore(directory: path)
        let draft = try await captureFixture(wire, in: files)
        let r = try runtime(wire, SessionJournal(), captures: files), vm = LiteMeasureVM(native: r)
        let index = path.appendingPathComponent("index.json")
        try Data("incomplete-index".utf8).write(to: index)
        let store = LiteStore(fileURL: index); XCTAssertTrue(store.needsReload)
        await vm.reloadDrafts(in: store); await vm.restoreDraft(draft.id, in: store)
        XCTAssertNotNil(vm.draftNotice); XCTAssertNil(vm.draft); XCTAssertNil(vm.image)
        let counts = await wire.counts(); XCTAssertEqual(counts, ["registration": 0])
        XCTAssertEqual(try Data(contentsOf: index), Data("incomplete-index".utf8))
    }

    func testConfirmedReceiptBeforeContourWriteRecoversWithoutAnotherRequest() async throws {
        let path = captureFiles(); defer { try? FileManager.default.removeItem(at: path) }
        let wire = try SessionWire(), journal = SessionJournal(), files = LiteCaptureDraftStore(directory: path)
        let draft = try await captureFixture(wire, in: files)
        let r = try runtime(wire, journal, captures: files); await r.identity.signIn()
        _ = try await r.recognizeSaved(recordID: draft.id, jpeg: draft.jpeg)
        let before = await wire.counts()
        let store = LiteStore(fileURL: path.appendingPathComponent("index.json")), vm = LiteMeasureVM(native: r)
        await vm.restoreDraft(draft.id, in: store)
        XCTAssertFalse(vm.polys.isEmpty); XCTAssertNotNil(vm.estimate)
        let applied = try await files.load(draft.id)
        await vm.restoreDraft(draft.id, in: store)
        let reapplied = try await files.load(draft.id)
        XCTAssertEqual(reapplied, applied, "Reopening a confirmed result must not invent a mask revision")
        let after = await wire.counts(); XCTAssertEqual(before, after)
    }

    func testForegroundContinuationHonorsWaitAndStopsWhenConfirmed() async throws {
        let path = captureFiles(); defer { try? FileManager.default.removeItem(at: path) }
        let wire = try SessionWire(), journal = SessionJournal(), files = LiteCaptureDraftStore(directory: path)
        let draft = try await captureFixture(wire, in: files)
        let r = try runtime(wire, journal, captures: files); await r.identity.signIn(); await wire.loseRun()
        let store = LiteStore(fileURL: path.appendingPathComponent("index.json")), vm = LiteMeasureVM(native: r)
        await vm.restoreDraft(draft.id, in: store)
        let before = await wire.counts()
        await vm.resumePendingRecognition()
        let idle = await wire.counts(); XCTAssertEqual(before, idle, "No initial cloud request without user action")
        await vm.recognizeNative(); await vm.resumePendingRecognition()
        let waiting = await wire.counts(); XCTAssertNil(waiting["service/status"])
        let resumed = try runtime(wire, journal, time: sessionTime + 6, captures: files)
        let vm2 = LiteMeasureVM(native: resumed)
        await vm2.restoreDraft(draft.id, in: store); await vm2.resumePendingRecognition()
        XCTAssertEqual(vm2.serviceResult?.note, "confirmed")
        let finished = await wire.counts()
        await vm2.resumePendingRecognition(); await vm2.resumePendingRecognition()
        let final = await wire.counts(); XCTAssertEqual(final, finished)
        XCTAssertEqual(final["service/prepare"], 1); XCTAssertEqual(final["service/run"], 1)
        XCTAssertEqual(final["service/status"], 1)
    }

    func testSavedCaptureReadbackKeepsExactPairAndInterruptedFinalizationCannotDuplicate() async throws {
        let path = captureFiles(); defer { try? FileManager.default.removeItem(at: path) }
        let wire = try SessionWire(), files = LiteCaptureDraftStore(directory: path)
        let draft = try await captureFixture(wire, in: files)
        let r = try runtime(wire, SessionJournal(), captures: files), vm = LiteMeasureVM(native: r)
        let index = path.appendingPathComponent("index.json"), store = LiteStore(fileURL: index)
        let location = LiteWoundLocation(side: "right", site: "heel")
        await vm.restoreDraft(draft.id, in: store)
        await vm.applyManual([[[5,5],[5,55],[55,55],[55,5]]], correctionIoU: 0.7)
        let saved = try XCTUnwrap(vm.saveCapture(in: store, selectedWoundID: "", location: location))
        defer { store.images.delete(saved.imageName); if let d = saved.depthName { store.images.delete(d) } }
        XCTAssertEqual(saved.id, draft.id); XCTAssertEqual(store.records.count, 1)
        XCTAssertNil(saved.cloudSync); XCTAssertTrue(saved.manuallyConfirmed == true)
        XCTAssertEqual(store.images.rawBytes(saved.imageName), draft.jpeg)
        let depth = try XCTUnwrap(store.loadDepth(try XCTUnwrap(saved.depthName), matchingImage: saved.imageName))
        XCTAssertEqual(depth.map.map(\.bitPattern), captureDepth().map.map(\.bitPattern))
        XCTAssertEqual(saved.surfaceCm2, try XCTUnwrap(vm.estimate).surfaceAreaCm2)
        XCTAssertNil(vm.saveCapture(in: store, selectedWoundID: "", location: location))
        XCTAssertEqual(vm.finishSavedCapture(in: store), draft.id); XCTAssertNil(vm.image)
        XCTAssertNil(vm.finishSavedCapture(in: store))
        let retained = try await files.load(draft.id)
        XCTAssertNil(retained.savedRecordID, "Simulated termination before draft-finalization write")
        XCTAssertEqual(retained.jpeg, draft.jpeg)
        let restartedStore = LiteStore(fileURL: index), restarted = LiteMeasureVM(native: r)
        await restarted.reloadDrafts(in: restartedStore); XCTAssertTrue(restarted.pendingCaptures.isEmpty)
        await restarted.restoreDraft(draft.id, in: restartedStore)
        XCTAssertNil(restarted.draft); XCTAssertEqual(restartedStore.records.count, 1)
        do { _ = try await files.load(draft.id); XCTFail("Recovery must finish retiring a committed capture copy") } catch { }
        XCTAssertEqual(store.images.rawBytes(saved.imageName), draft.jpeg)
    }

    func testNoChangeManualCompletionDoesNotCreateNewDraftRevision() async throws {
        let path = captureFiles(); defer { try? FileManager.default.removeItem(at: path) }
        let wire = try SessionWire(), files = LiteCaptureDraftStore(directory: path)
        let draft = try await captureFixture(wire, in: files)
        let r = try runtime(wire, SessionJournal(), captures: files), vm = LiteMeasureVM(native: r)
        let store = LiteStore(fileURL: path.appendingPathComponent("index.json"))
        let contour = [[[5,5],[5,55],[55,55],[55,5]]]
        await vm.restoreDraft(draft.id, in: store); await vm.applyManual(contour, correctionIoU: 0.7)
        let before = try await files.load(draft.id), area = vm.estimate?.surfaceAreaCm2
        await vm.applyManual(contour, correctionIoU: 1)
        let after = try await files.load(draft.id)
        XCTAssertEqual(before, after); XCTAssertEqual(vm.estimate?.surfaceAreaCm2, area)
        let counts = await wire.counts(); XCTAssertEqual(counts, ["registration": 0])
    }

    func testCommittedIndexWithLostAcknowledgementIsReadBackWithoutLosingMedia() async throws {
        let path = captureFiles(); defer { try? FileManager.default.removeItem(at: path) }
        let wire = try SessionWire(), files = LiteCaptureDraftStore(directory: path)
        let draft = try await captureFixture(wire, in: files)
        let r = try runtime(wire, SessionJournal(), captures: files), vm = LiteMeasureVM(native: r)
        let index = path.appendingPathComponent("index.json")
        let store = LiteStore(fileURL: index, writeIndex: { data, url in
            try HealthDataFiles.write(data, to: url)
            throw LiteServiceFailure.conflict
        })
        await vm.restoreDraft(draft.id, in: store)
        await vm.applyManual([[[5,5],[5,55],[55,55],[55,5]]], correctionIoU: 0.7)
        let saved = try XCTUnwrap(vm.saveCapture(in: store, selectedWoundID: "",
            location: LiteWoundLocation(side: "right", site: "heel")))
        defer { store.images.delete(saved.imageName); if let d = saved.depthName { store.images.delete(d) } }
        XCTAssertEqual(store.records.count, 1); XCTAssertEqual(vm.savedId, saved.id)
        XCTAssertEqual(store.images.rawBytes(saved.imageName), draft.jpeg)
        let sidecar = try XCTUnwrap(saved.depthName)
        XCTAssertNotNil(store.loadDepth(sidecar, matchingImage: saved.imageName))
        let finalDraft = try XCTUnwrap(vm.draft)
        await vm.markDraftSaved(finalDraft, in: store)
        let pending = try await files.pending(excluding: [])
        XCTAssertTrue(pending.isEmpty)
        let encrypted = try Data(contentsOf: path.appendingPathComponent(draft.id + ".enc"))
        let receipt = try XCTUnwrap(PhiCrypto.decryptBytes(encrypted))
        XCTAssertLessThan(receipt.count, 512)
        XCTAssertNil(receipt.range(of: Data(draft.jpeg.base64EncodedString().utf8)))
        do { _ = try await files.load(draft.id); XCTFail("A saved draft is only a receipt, not a hidden photo copy") } catch { }
        await vm.markDraftSaved(finalDraft, in: store) // Idempotent finalization.
        XCTAssertEqual(store.records.count, 1)
        // End the injected write failure before exercising the normal deletion path.
        let healthyStore = LiteStore(fileURL: index)
        XCTAssertTrue(healthyStore.delete(saved))
        XCTAssertFalse(store.images.exists(saved.imageName)); XCTAssertFalse(store.images.exists(sidecar))
        let afterDeletion = try await files.pending(excluding: [])
        XCTAssertTrue(afterDeletion.isEmpty, "Deleting a saved record must not resurrect its old capture")
    }

    func testFailedIndexWriteKeepsDraftAndMeasurementScreen() async throws {
        let path = captureFiles(); defer { try? FileManager.default.removeItem(at: path) }
        let wire = try SessionWire(), files = LiteCaptureDraftStore(directory: path)
        let draft = try await captureFixture(wire, in: files)
        let r = try runtime(wire, SessionJournal(), captures: files), vm = LiteMeasureVM(native: r)
        let store = LiteStore(fileURL: path.appendingPathComponent("index.json"),
                              writeIndex: { _, _ in throw LiteServiceFailure.conflict })
        await vm.restoreDraft(draft.id, in: store)
        await vm.applyManual([[[5,5],[5,55],[55,55],[55,5]]], correctionIoU: 0.7)
        XCTAssertNil(vm.saveCapture(in: store, selectedWoundID: "",
            location: LiteWoundLocation(side: "right", site: "heel")))
        XCTAssertNil(vm.finishSavedCapture(in: store)); XCTAssertTrue(store.records.isEmpty)
        XCTAssertNotNil(vm.image); XCTAssertEqual(vm.captureJPEG, draft.jpeg); XCTAssertNotNil(vm.note)
        let retained = try await files.load(draft.id)
        XCTAssertEqual(retained.jpeg, draft.jpeg); XCTAssertEqual(retained.polygons, vm.polys)
        await vm.markDraftSaved(retained, in: store)
        let stillOriginal = try await files.load(draft.id); XCTAssertEqual(stillOriginal, retained)
    }

    func testDeleteBeforeDraftFinalizationNeverResurrectsAfterRestart() async throws {
        let path = captureFiles(); defer { try? FileManager.default.removeItem(at: path) }
        let wire = try SessionWire(), files = LiteCaptureDraftStore(directory: path)
        let draft = try await captureFixture(wire, in: files), r = try runtime(wire, SessionJournal(), captures: files)
        let index = path.appendingPathComponent("index.json"), store = LiteStore(fileURL: index)
        let vm = LiteMeasureVM(native: r)
        await vm.restoreDraft(draft.id, in: store)
        await vm.applyManual([[[5,5],[5,55],[55,55],[55,5]]], correctionIoU: 0.7)
        let saved = try XCTUnwrap(vm.saveCapture(in: store, selectedWoundID: "",
            location: LiteWoundLocation(side: "right", site: "heel")))
        // Simulate termination before capture finalization, then an ordinary
        // library deletion without constructing the native session.
        XCTAssertTrue(store.delete(saved))
        let nextStore = LiteStore(fileURL: index), restarted = LiteMeasureVM(native: r)
        await restarted.reloadDrafts(in: nextStore)
        XCTAssertTrue(restarted.pendingCaptures.isEmpty)
        await restarted.restoreDraft(draft.id, in: nextStore)
        XCTAssertNil(restarted.image); XCTAssertTrue(nextStore.records.isEmpty)
        do { _ = try await files.load(draft.id); XCTFail("A deleted capture must not remain recoverable") } catch { }
        XCTAssertFalse(nextStore.add(saved))
        let counts = await wire.counts(); XCTAssertEqual(counts, ["registration": 0])
    }

    func testNativeDeleteWaitsForDraftCleanupAndRetriesWithoutCloudCalls() async throws {
        let path = captureFiles(); defer { try? FileManager.default.removeItem(at: path) }
        let wire = try SessionWire(), healthy = LiteCaptureDraftStore(directory: path)
        let draft = try await captureFixture(wire, in: healthy)
        let r = try runtime(wire, SessionJournal(), captures: healthy), vm = LiteMeasureVM(native: r)
        let store = LiteStore(fileURL: path.appendingPathComponent("index.json"))
        await vm.restoreDraft(draft.id, in: store)
        await vm.applyManual([[[5,5],[5,55],[55,55],[55,5]]], correctionIoU: 0.7)
        let saved = try XCTUnwrap(vm.saveCapture(in: store, selectedWoundID: "",
            location: LiteWoundLocation(side: "right", site: "heel")))
        defer { store.images.delete(saved.imageName); if let d = saved.depthName { store.images.delete(d) } }
        let badFiles = LiteCaptureDraftStore(directory: path, write: { _, _ in throw LiteServiceFailure.conflict })
        let failing = try runtime(wire, SessionJournal(), captures: badFiles)
        let first = await failing.deleteLocalRecord(saved, in: store)
        XCTAssertFalse(first); XCTAssertEqual(store.records.count, 1)
        XCTAssertTrue(store.images.exists(saved.imageName)); XCTAssertNotNil(store.storageError)
        let second = await r.deleteLocalRecord(saved, in: store)
        XCTAssertTrue(second); XCTAssertTrue(store.records.isEmpty)
        XCTAssertFalse(store.images.exists(saved.imageName))
        do { _ = try await healthy.load(draft.id); XCTFail("No hidden raw draft after successful deletion") } catch { }
        let counts = await wire.counts(); XCTAssertEqual(counts, ["registration": 0])
    }

    func testDiscardKeepsOtherCaptureAndFencesAnAlreadyOpenEditor() async throws {
        let path = captureFiles(); defer { try? FileManager.default.removeItem(at: path) }
        let wire = try SessionWire(), files = LiteCaptureDraftStore(directory: path)
        let first = try await captureFixture(wire, in: files)
        let second = try await files.create(jpeg: first.jpeg, depth: captureDepth())
        let r = try runtime(wire, SessionJournal(), captures: files)
        let index = path.appendingPathComponent("index.json"), store = LiteStore(fileURL: index)
        let editor = LiteMeasureVM(native: r), other = LiteMeasureVM(native: r)
        await editor.restoreDraft(first.id, in: store); await other.restoreDraft(first.id, in: store)
        await other.discardDraft(first.id, in: store)
        XCTAssertNil(other.image); XCTAssertEqual(other.pendingCaptures.map(\.id), [second.id])
        await editor.recognizeNative()
        await editor.applyManual([[[5,5],[5,55],[55,55],[55,5]]], correctionIoU: 0.7)
        XCTAssertNil(editor.saveCapture(in: store, selectedWoundID: "", location: LiteWoundLocation(side: "right", site: "heel")))
        let next = LiteMeasureVM(native: r), newStore = LiteStore(fileURL: index)
        await next.reloadDrafts(in: newStore); await next.restoreDraft(first.id, in: newStore)
        XCTAssertNil(next.image); XCTAssertEqual(next.pendingCaptures.map(\.id), [second.id])
        let retained = try await files.load(second.id); XCTAssertEqual(retained.jpeg, second.jpeg)
        let counts = await wire.counts(); XCTAssertEqual(counts, ["registration": 0])
    }

    func testDiscardPreservesCompletedServiceReceiptAndDoesNotChangeResearchConsent() async throws {
        let path = captureFiles(); defer { try? FileManager.default.removeItem(at: path) }
        let wire = try SessionWire(), journal = SessionJournal(), files = LiteCaptureDraftStore(directory: path)
        let draft = try await captureFixture(wire, in: files)
        let r = try runtime(wire, journal, captures: files); await r.identity.signIn()
        let store = LiteStore(fileURL: path.appendingPathComponent("index.json")), vm = LiteMeasureVM(native: r)
        await vm.restoreDraft(draft.id, in: store); await vm.recognizeNative()
        let id = try LiteNativeSession.captureID(recordID: draft.id)
        let before = try await r.service.storedIfPresent(id), counts = await wire.counts()
        let consent = LitePrefs.researchConsent
        await vm.discardDraft(draft.id, in: store)
        let after = try await r.service.storedIfPresent(id), finalCounts = await wire.counts()
        XCTAssertEqual(before, after); XCTAssertEqual(counts, finalCounts)
        XCTAssertEqual(finalCounts["service/run"], 1); XCTAssertEqual(LitePrefs.researchConsent, consent)
        XCTAssertNil(vm.image); XCTAssertTrue(store.records.isEmpty)
    }

    func testDiscardAcknowledgementLossRemainsPendingUntilReadBackRetry() async throws {
        let path = captureFiles(); defer { try? FileManager.default.removeItem(at: path) }
        let wire = try SessionWire(), healthy = LiteCaptureDraftStore(directory: path)
        let draft = try await captureFixture(wire, in: healthy)
        let files = LiteCaptureDraftStore(directory: path, write: { data, url in
            try HealthDataFiles.write(data, to: url); throw LiteServiceFailure.conflict
        })
        let r = try runtime(wire, SessionJournal(), captures: files), vm = LiteMeasureVM(native: r)
        let store = LiteStore(fileURL: path.appendingPathComponent("index.json"))
        await vm.restoreDraft(draft.id, in: store); await vm.discardDraft(draft.id, in: store)
        XCTAssertNotNil(vm.image); XCTAssertTrue(vm.note?.contains("尚未確認") == true)
        await vm.recognizeNative()
        await vm.discardDraft(draft.id, in: store)
        XCTAssertNil(vm.image); XCTAssertTrue(vm.pendingCaptures.isEmpty)
        let counts = await wire.counts(); XCTAssertEqual(counts, ["registration": 0])
    }
}

/// Drops the response after accepting the single media POST. Status can then
/// return that receipt to a fresh LiteStore, using a small JSON request.
private actor CaptureWire: LiteAuthenticatedTransport {
    let job: LitePendingResearchCapture, packet: LiteRawDepthPacket
    let badLookup: Bool, wrongDepth: Bool, expireFirstLookup: Bool
    var mediaPosts = 0, statusPosts = 0, sourcePosts = 0
    init(_ job: LitePendingResearchCapture, _ packet: LiteRawDepthPacket, badLookup: Bool = false,
         wrongDepth: Bool = false, expireFirstLookup: Bool = false) {
        self.job = job; self.packet = packet; self.badLookup = badLookup; self.wrongDepth = wrongDepth
        self.expireFirstLookup = expireFirstLookup
    }
    func installation() async throws -> String { job.pin.owner }
    func counts() -> [Int] { [sourcePosts, statusPosts, mediaPosts] }
    func send(_ request: URLRequest, installation: String) async throws -> (Data, HTTPURLResponse) {
        let url = try XCTUnwrap(request.url)
        let data = try XCTUnwrap(request.httpBody)
        var response: [String: Any]
        switch url.path {
        case "/api/v1/lite/service/research-source":
            XCTAssertEqual(installation, job.pin.account.installation)
            sourcePosts += 1
            let args = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
            XCTAssertEqual(args["capture_id"] as? String, job.captureID)
            let claim: [String: Any] = ["schema":"lite.research-source/1","native_installation":installation,
                "account_sha256":job.pin.account.account,"research_installation":job.pin.owner,
                "consent_epoch":job.pin.epoch,"consent_version":job.pin.consentVersion,"capture_id":job.captureID,
                "image_sha256":job.imageHash,"source":"manual","operation":NSNull(),"result_sha256":NSNull()]
            response = ["claim":claim,"signature":Data(repeating: 97, count: 64).base64EncodedString()]
        case "/api/v1/lite/research/capture/status":
            XCTAssertEqual(installation, job.pin.owner)
            statusPosts += 1
            let args = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
            XCTAssertEqual(args["bundle_sha256"] as? String, packet.bundleDigest)
            XCTAssertNil(data.range(of: packet.jpeg))
            if expireFirstLookup && statusPosts == 1 {
                return (Data(#"{"error":"research_source_expired","renew_source_only":true}"#.utf8),
                        HTTPURLResponse(url:url,statusCode:409,httpVersion:nil,headerFields:nil)!)
            }
            if mediaPosts > 0 {
                let idJSON = try JSONSerialization.data(withJSONObject: ["lite.research-capture/1",job.pin.owner,job.pin.epoch,job.captureID],options:.withoutEscapingSlashes)
                let imageID = String(LiteServiceBytes.hash(idJSON).prefix(16))
                let raw: [String: Any] = ["schema":"lite.rgbd.receipt/1","status":"stored","registration":"not_verified",
                    "training_admission":"not_evaluated","installation_id":job.pin.owner,"capture_id":imageID,
                    "bundle_bytes":16 + packet.metadata.count + packet.jpeg.count + packet.rawDepth.count,
                    "metadata_sha256":LiteServiceBytes.hash(packet.metadata),"rgb_sha256":job.imageHash,
                    "depth_sha256":wrongDepth ? String(repeating:"0",count:64) : LiteServiceBytes.hash(packet.rawDepth),
                    "bundle_sha256":packet.bundleDigest]
                response = ["schema":"lite.research-capture.receipt/1","status":"stored","image_id":imageID,
                    "image_sha256":job.imageHash,"capture_id":job.captureID,"consent_epoch":job.pin.epoch,
                    "capture_intent_sha256":sessionHash,"raw_depth_receipt":raw,"annotation_status":"not_confirmed",
                    "registration":"not_verified","training_admission":"not_evaluated"]
            } else {
                response = ["schema":"lite.research-capture.lookup/1","status":badLookup ? "unknown" : "upload_required",
                    "capture_id":job.captureID,"image_sha256":job.imageHash,"consent_epoch":job.pin.epoch,"bundle_sha256":packet.bundleDigest]
            }
        case "/api/v1/lite/research/capture":
            XCTAssertEqual(installation, job.pin.owner)
            XCTAssertNotNil(data.range(of: packet.jpeg))
            XCTAssertNotNil(data.range(of: packet.rawDepth))
            mediaPosts += 1
            throw URLError(.networkConnectionLost)
        default: throw LiteServiceFailure.invalidState
        }
        return (try JSONSerialization.data(withJSONObject: response),
                HTTPURLResponse(url: url,statusCode: 200,httpVersion: nil,headerFields: nil)!)
    }
}

extension LiteNativeSessionTests {
    private func researchFixture() async throws -> (URL, LiteStore, LiteRecord, LiteRawDepthPacket) {
        let path = captureFiles(), clock = { Date(timeIntervalSince1970: 1_800_010_000) }
        let store = LiteStore(fileURL: path.appendingPathComponent("index.json"), now: clock)
        let image = await (try SessionWire()).image()
        var depth = captureDepth()
        depth.captureProvenance = .init(id: UUID(), observedAt: clock())
        let packet = try LiteRawDepthPacket.make(depth: depth, jpeg: image)
        let pin = LiteResearchCapturePin(scope: .init(origin: "https://study.example", epoch: nil),
            account: .init(origin:"https://native.example",installation:String(repeating:"b",count:32),account:sessionHash),
            owner:String(repeating:"a",count:32),consentVersion:LitePrefs.consentVersion)
        let job = LitePendingResearchCapture(pin:pin,captureID:depth.captureProvenance!.id.uuidString.lowercased(),
            imageHash:LiteServiceBytes.hash(image),operation:nil,resultHash:nil,width:64,height:64)
        let record = LiteRecord(id:UUID().uuidString,dateISO:"2027-01-01T00:00:00Z",surfaceCm2:4,projectedCm2:4,
            quality:"ok",imageName:try XCTUnwrap(store.images.save(jpeg:image)),source:"manual",
            polysJson:"[[[5,5],[5,50],[50,50]]]",depthName:try XCTUnwrap(store.saveDepth(depth,matchingJPEG:image)),
            researchCapture:job, manuallyConfirmed:true)
        let saved = try XCTUnwrap(store.saveMeasurement(record,selectedWoundID:"",enteredLocation:.init(side:"right",site:"heel"),queueResearchSync:true))
        return (path, store, saved, packet)
    }
    private func cleanCapture(_ path:URL, _ store: LiteStore, _ record:LiteRecord) {
        store.images.delete(record.imageName)
        if let depth = record.depthName { store.images.delete(depth) }
        try? FileManager.default.removeItem(at:path)
    }
    private func delivery(_ job: LitePendingResearchCapture, wire: CaptureWire,
                          consent: @escaping () -> Bool = { true }) -> LiteResearchCaptureDelivery {
        LiteResearchCaptureDelivery(source: wire, currentAccount: { job.pin.account },
            researchOwner: { _ in job.pin.owner }, permits: { _ in consent() }, researchTransport: { _,_ in wire })
    }

    func testResearchLostReplyRestartGetsReceiptWithoutReuploadAndOrdersFirstAndEditedRevisions() async throws {
        let (path,store,saved,packet) = try await researchFixture()
        defer { cleanCapture(path,store,saved) }
        let frozen = try XCTUnwrap(saved.researchCapture)
        XCTAssertNotEqual(frozen.captureID, saved.id.lowercased())
        let wire = CaptureWire(frozen,packet)
        await delivery(frozen,wire:wire).resume(saved.id,store:store)
        XCTAssertNil(store.records.first?.cloudSync)
        XCTAssertNotNil(store.records.first?.researchCapture?.encryptedTicket)
        var edit = try XCTUnwrap(store.records.first)
        edit.surfaceCm2 = 5
        edit.polysJson = "[[[5,5],[6,45],[50,49]]]"
        XCTAssertTrue(store.update(edit))
        let reopened = LiteStore(fileURL:path.appendingPathComponent("index.json"),now:{ store.revisionNow.addingTimeInterval(65) })
        await delivery(frozen,wire:wire).resume(saved.id,store:reopened)
        let sync = try XCTUnwrap(reopened.records.first?.cloudSync)
        XCTAssertEqual(sync.pending?.payloadJSON,frozen.firstRevision)
        XCTAssertEqual(sync.binding.storageReceipt?.rawDepthReceipt?.depth_sha256,LiteServiceBytes.hash(packet.rawDepth))
        let evidence = try XCTUnwrap(reopened.records.first?.researchCapture?.encryptedReceipt)
        let confirmed = try XCTUnwrap(PhiCrypto.decryptBytes(evidence))
        XCTAssertEqual((try JSONSerialization.jsonObject(with:confirmed) as? [String:Any])?["image_id"] as? String,sync.binding.imageID)
        var revisions: [LitePendingRevision] = []
        await reopened.syncRevision(recordID:saved.id,consent:{true},bindingAllowed:{_ in true},send:{ _,revision in revisions.append(revision) })
        XCTAssertEqual(revisions.map(\.revision),[1,2])
        XCTAssertEqual(revisions.first?.payloadJSON,frozen.firstRevision)
        XCTAssertNotEqual(revisions.first?.digest,revisions.last?.digest)
        let latest = try JSONDecoder().decode(LiteRevisionPayload.self,from:Data(try XCTUnwrap(revisions.last).payloadJSON.utf8))
        XCTAssertEqual(latest.polygons,[[[5,5],[6,45],[50,49]]])
        let counts = await wire.counts()
        XCTAssertEqual(counts,[1,2,1])
        XCTAssertEqual(store.images.rawBytes(saved.imageName),packet.jpeg)
    }

    func testResearchUnknownLookupNeverSendsMediaAndWrongDepthReceiptNeverCreatesBinding() async throws {
        for bad in [true,false] {
            let (path,store,saved,packet) = try await researchFixture()
            defer { cleanCapture(path,store,saved) }
            let job = try XCTUnwrap(saved.researchCapture), wire = CaptureWire(job,packet,badLookup:bad,wrongDepth:true)
            let client = delivery(job,wire:wire)
            await client.resume(saved.id,store:store)
            let later = LiteStore(fileURL:path.appendingPathComponent("index.json"),now:{ store.revisionNow.addingTimeInterval(180) })
            await client.resume(saved.id,store:later,explicit:true)
            let counts = await wire.counts()
            XCTAssertEqual(counts,[1,2,bad ? 0 : 1])
            XCTAssertNil(later.records.first?.cloudSync)
            XCTAssertNotNil(later.records.first?.researchCapture?.notice)
        }
    }

    func testResearchWithdrawalAndDifferentAccountNeverDispatchOrRebind() async throws {
        let (path,store,saved,packet) = try await researchFixture()
        defer { cleanCapture(path,store,saved) }
        let job = try XCTUnwrap(saved.researchCapture), wire = CaptureWire(job,packet)
        await delivery(job,wire:wire,consent:{false}).resume(saved.id,store:store,explicit:true)
        let other = LiteResearchCaptureDelivery(source:wire,currentAccount:{
            .init(origin:job.pin.account.origin,installation:job.pin.account.installation,account:String(repeating:"f",count:64))
        },researchOwner:{ _ in job.pin.owner },permits:{ _ in true },researchTransport:{ _,_ in wire })
        await other.resume(saved.id,store:store,explicit:true)
        let counts = await wire.counts()
        XCTAssertEqual(counts,[0,0,0])
        XCTAssertNil(store.records.first?.cloudSync)
        XCTAssertEqual(store.records.first?.researchCapture?.firstRevision,job.firstRevision)
    }

    func testExpiredSourceRenewsBeforeSendingAnyImageBytes() async throws {
        let (path,store,saved,packet) = try await researchFixture()
        defer { cleanCapture(path,store,saved) }
        let job = try XCTUnwrap(saved.researchCapture)
        let wire = CaptureWire(job,packet,expireFirstLookup:true)
        await delivery(job,wire:wire).resume(saved.id,store:store)
        let first = await wire.counts()
        XCTAssertEqual(first,[1,1,0])
        XCTAssertNil(store.records.first?.researchCapture?.encryptedTicket)
        XCTAssertNotNil(store.records.first?.researchCapture?.signature)
        for offset in [65.0,130.0] {
            let reopened = LiteStore(fileURL:path.appendingPathComponent("index.json"),now:{ store.revisionNow.addingTimeInterval(offset) })
            await delivery(job,wire:wire).resume(saved.id,store:reopened)
        }
        let final = await wire.counts()
        XCTAssertEqual(final,[2,3,1])
        XCTAssertTrue(store.reload())
        XCTAssertNotNil(store.records.first?.cloudSync)
    }
}

extension LiteNativeSessionTests {
    func testCameraPinsResearchConsentAndUsesDepthCallbackIdentityForFirstSavedAnnotation() async throws {
        let path = captureFiles()
        defer { try? FileManager.default.removeItem(at:path) }
        let files = LiteCaptureDraftStore(directory:path), wire = try SessionWire()
        let session = try runtime(wire, SessionJournal(), captures:files)
        let owner = String(repeating:"a", count:32)
        let person = LiteDeletionContext(origin:origin.absoluteString,installation:String(repeating:"b",count:32),account:sessionHash)
        var consenting = false
        let researchOrigin = "https://study.example"
        let journal = LiteWithdrawalJournal(url:path.appendingPathComponent("withdrawal.json"))
        let participation = {
            try? LiteCaptureResearchScope.capture(server:researchOrigin,consent:consenting,journal:journal)
        }
        session.researchDelivery = LiteResearchCaptureDelivery(source:wire,currentAccount:{person},researchOwner:{_ in owner},
            permits:{ $0.permits(server:researchOrigin,consent:consenting,journal:journal) },
            researchTransport:{ _,_ in wire })
        var depth = captureDepth()
        depth.captureProvenance = .init(observedAt:Date(timeIntervalSince1970:1_800_000_000))
        let imageBytes = await wire.image()
        let image = try XCTUnwrap(UIImage(data:imageBytes))
        let store = LiteStore(fileURL:path.appendingPathComponent("index.json"))
        for consentAtCapture in [false, true] {
            consenting = consentAtCapture
            let vm = LiteMeasureVM(native:session,captureParticipation:participation)
            _ = await vm.ingest(image,depth:depth)
            let draft = try XCTUnwrap(vm.draft)
            XCTAssertEqual(draft.researchPin?.owner, consentAtCapture ? owner : nil)
            // A later opt-in must never admit a photo made before consent.
            consenting = true
            await vm.applyManual([[[5,5],[5,55],[55,55],[55,5]]], correctionIoU:0.7)
            let saved = try XCTUnwrap(vm.saveCapture(in:store,selectedWoundID:store.localTrackingID ?? "",
                                                     location:.init(side:"right",site:"heel")))
            defer { store.images.delete(saved.imageName); if let d = saved.depthName { store.images.delete(d) } }
            XCTAssertEqual(saved.researchCapture?.captureID, consentAtCapture ? depth.captureProvenance?.id.uuidString.lowercased() : nil)
            XCTAssertEqual(saved.researchCapture?.firstRevision != nil, consentAtCapture)
            XCTAssertNotEqual(saved.id.lowercased(), saved.researchCapture?.captureID)
        }
        let counts = await wire.counts()
        XCTAssertEqual(counts, ["registration":0])
    }
}

private struct AttestedSessionServer: LiteAttestTransport {
    let registration: AttestHTTPDouble
    let business: SessionWire
    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        if request.url?.path.contains("/attest/") == true { return try await registration.send(request) }
        XCTAssertNotNil(request.value(forHTTPHeaderField:"X-Lite-Assertion"))
        return try await business.send(request, installation:registration.owner)
    }
}

extension LiteNativeSessionTests {
    func testRealSigningLaneCanCompleteRecognitionAndStopsAfterLogout() async throws {
        let api = try SessionWire(), control = AttestHTTPDouble()
        await api.useRegistration(control.owner)
        let client = try LiteAttestClient(origin: origin, audience:"lite-test",device:AttestDeviceDouble(),
            persistence:AttestMemory(),transport:AttestedSessionServer(registration:control,business:api),
            now:{ Date(timeIntervalSince1970:1000) })
        _ = try await client.registeredInstallation()
        let access = LiteSandboxAccess()
        let signed = LiteSandboxSignedTransport(client:client,access:access)
        let session = try LiteNativeSession(origin:origin,transport:signed,identityProvider:SessionApple(),
            purchaseProvider:SessionStoreKit(),journal:SessionJournal(),register:{try await client.registeredInstallation()},
            now:{ Date(timeIntervalSince1970:Double(sessionTime)) })
        access.identity = session.identity
        let completed = expectation(description:"signed service request left the signing lane")
        Task { @MainActor in
            defer { completed.fulfill() }
            do {
                await session.identity.signIn()
                let id = await api.capture(), image = await api.image()
                _ = try await session.service.enqueue(captureID:id,jpeg:image)
                let done = try await session.service.resume(id) { _ in image }
                XCTAssertEqual(done.note,"confirmed")
                await session.identity.signOut()
                var req = URLRequest(url:origin.appendingPathComponent("api/v1/lite/service/balance"))
                req.httpMethod = "POST"; req.httpBody = Data("{}".utf8)
                do { _ = try await signed.send(req,installation:control.owner); XCTFail("logged out request dispatched") } catch {}
                let calls = await api.counts()
                XCTAssertEqual(calls["service/run"],1)
                XCTAssertNil(calls["service/balance"])
            } catch { XCTFail("signing flow: \(error)") }
        }
        await fulfillment(of:[completed],timeout:5)
    }
}

private extension SessionWire {
    func useRegistration(_ id:String) { owner = id }
}

private struct AttestedCaptureServer: LiteAttestTransport {
    let registration: AttestHTTPDouble
    let capture: CaptureWire
    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        if request.url?.path.contains("/attest/") == true { return try await registration.send(request) }
        XCTAssertNotNil(request.value(forHTTPHeaderField:"X-Lite-Assertion"))
        return try await capture.send(request,installation:registration.owner)
    }
}

extension LiteNativeSessionTests {
    func testResearchSigningChecksOriginalOwnerWithoutWaitingOnItsOwnLane() async throws {
        let (path,store,saved,packet) = try await researchFixture()
        defer { cleanCapture(path,store,saved) }
        let job = try XCTUnwrap(saved.researchCapture), wire = CaptureWire(job,packet)
        let client = try LiteAttestClient(origin:try XCTUnwrap(URL(string:job.pin.scope.origin)),audience:"lite-test",
            device:AttestDeviceDouble(),persistence:AttestMemory(),
            transport:AttestedCaptureServer(registration:AttestHTTPDouble(),capture:wire),
            now:{Date(timeIntervalSince1970:1000)})
        let registration = try await client.registeredInstallation()
        XCTAssertEqual(registration,job.pin.owner)
        let delivery = LiteResearchCaptureDelivery(source:wire,currentAccount:{job.pin.account},researchOwner:{ _ in
            guard let owner = try await client.existingInstallation() else { throw LiteServiceFailure.conflict }
            return owner
        },permits:{ _ in true },researchTransport:{ _,scope in LiteSignedCloudTransport(client:client,consent:scope) })
        let completed = expectation(description:"signed recovery leaves original research lane")
        Task { @MainActor in
            defer { completed.fulfill() }
            await delivery.resume(saved.id,store:store)
            let reopened = LiteStore(fileURL:path.appendingPathComponent("index.json"),now:{ store.revisionNow.addingTimeInterval(65) })
            await delivery.resume(saved.id,store:reopened)
            XCTAssertNotNil(reopened.records.first?.cloudSync)
            let counts = await wire.counts()
            XCTAssertEqual(counts,[1,2,1])
        }
        await fulfillment(of:[completed],timeout:5)
    }
}
