import XCTest
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
    private func captureDepth() -> DepthCapture {
        DepthCapture(map: Array(repeating: 0.3, count: 64), width: 8, height: 8,
            fx: 50, fy: 50, cx: 32, cy: 32, refWidth: 64, refHeight: 64,
            accuracy: "absolute", filtered: false, rgbWidth: 64, rgbHeight: 64, sourceExifOrientation: 1)
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
}
