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
    func purchase(product: String, token: UUID) async throws -> LitePurchaseOutcome {
        XCTAssertEqual(token.uuidString.lowercased(), sessionToken)
        XCTAssertEqual(product, event.product); buys += 1; pending = true
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
            closed = true; code = 202
            result = ["v": 1, "state": "account_deletion_pending", "operation": operation, "installation": owner,
                "requested_at": sessionTime, "inventory_sha256": sessionHash, "grant_total": 1, "revocation_scan": 1,
                "grants_examined": 0, "grants_revoked": 0, "apple_revocation": "not_started", "data_disposition": "not_started",
                "account_deleted": false, "subscription_cancelled": false, "local_records_preserved": true]
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
                         origin: URL? = nil, time: Int = sessionTime) throws -> LiteNativeSession {
        try LiteNativeSession(origin: origin ?? self.origin, transport: wire, identityProvider: apple ?? SessionApple(),
            purchaseProvider: storeKit ?? SessionStoreKit(), journal: journal, register: { await wire.register() },
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
    func testRegistrationIsExplicitAndDoesNotSignInOrBuy() async throws {
        let wire = try SessionWire(), apple = SessionApple(), kit = SessionStoreKit()
        let r = try runtime(wire, SessionJournal(), apple: apple, storeKit: kit)
        await r.registerDevice(); let counts = await wire.counts()
        XCTAssertEqual(counts, ["registration": 1]); XCTAssertEqual(apple.calls, 0); XCTAssertEqual(kit.buys, 0)
    }
    func testBeforeSignInNeitherPurchaseNorRecognitionIsDispatched() async throws {
        let wire = try SessionWire(), r = try runtime(wire, SessionJournal())
        await r.purchases.purchase()
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
        await r.purchases.purchase(); await r.purchases.restore()
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
        await r.purchases.purchase(); XCTAssertEqual(kit.finishes, 0)
        let restarted = try runtime(wire, journal, storeKit: kit); await restarted.purchases.reconcile()
        XCTAssertEqual(kit.buys, 1); XCTAssertEqual(kit.finishes, 1)
        await restarted.purchases.reconcile(); XCTAssertEqual(kit.finishes, 1)
    }
    func testLogoutStopsPurchasesAndPreservesJournal() async throws {
        let wire = try SessionWire(), journal = SessionJournal(), kit = SessionStoreKit()
        let r = try runtime(wire, journal, storeKit: kit); await r.identity.signIn(); await r.identity.signOut()
        let before = await journal.snapshot(); await r.purchases.purchase()
        XCTAssertEqual(kit.buys, 0); let after = await journal.snapshot(); XCTAssertEqual(before, after)
        let counts = await wire.counts(); XCTAssertNil(counts["purchase/context"])
    }
    func testLogoutDuringContextResponseCannotOpenStoreKit() async throws {
        let wire = try SessionWire(), kit = SessionStoreKit(), r = try runtime(wire, SessionJournal(), storeKit: kit)
        await r.identity.signIn(); await wire.onContext { await r.identity.signOut() }
        await r.purchases.purchase(); XCTAssertEqual(kit.buys, 0)
        let counts = await wire.counts(); XCTAssertEqual(counts["purchase/context"], 1); XCTAssertNil(counts["purchase/confirm"])
    }
    func testDeletionIntentFencesAllOrdinaryClientsAcrossRestartButProgressWorks() async throws {
        let wire = try SessionWire(), journal = SessionJournal(), kit = SessionStoreKit()
        let r = try runtime(wire, journal, storeKit: kit); await r.identity.signIn()
        let d = r.deletion(); await d.requestConfirmed()
        XCTAssertNotNil(d.record?.receipt); XCTAssertFalse(d.record?.receipt?.account_deleted ?? true)
        let operation = d.record?.operation
        let restarted = try runtime(wire, journal, storeKit: kit)
        await restarted.purchases.purchase(); XCTAssertEqual(kit.buys, 0)
        let id = await wire.capture(), image = await wire.image()
        _ = try await restarted.service.enqueue(captureID: id, jpeg: image)
        _ = try await restarted.service.resume(id) { _ in image }
        let status = restarted.deletion(); await status.retry()
        XCTAssertEqual(status.record?.operation, operation); XCTAssertNotNil(status.record?.receipt)
        let counts = await wire.counts(); XCTAssertNil(counts["service/prepare"]); XCTAssertNil(counts["purchase/context"])
        XCTAssertEqual(counts["account/deletion/request"], 1); XCTAssertGreaterThan(counts["account/deletion/status"] ?? 0, 0)
    }
    func testFailedIdentityJournalPreventsAppleAndServiceCalls() async throws {
        let wire = try SessionWire(), journal = SessionJournal(), apple = SessionApple(), kit = SessionStoreKit()
        let r = try runtime(wire, journal, apple: apple, storeKit: kit); await journal.fail(true)
        await r.identity.signIn(); await r.purchases.purchase()
        XCTAssertEqual(apple.calls, 0); XCTAssertEqual(kit.buys, 0)
        let counts = await wire.counts(); XCTAssertNil(counts["account/confirm"]); XCTAssertNil(counts["purchase/context"])
    }
    func testProductionContextNeverReachesSandboxStoreKit() async throws {
        let wire = try SessionWire(), kit = SessionStoreKit(), r = try runtime(wire, SessionJournal(), storeKit: kit)
        await r.identity.signIn(); await wire.setProduction(); await r.purchases.purchase()
        XCTAssertEqual(kit.buys, 0)
    }
    func testOriginChangeCannotReuseSavedIdentity() async throws {
        let wire = try SessionWire(), journal = SessionJournal(), kit = SessionStoreKit()
        let first = try runtime(wire, journal); await first.identity.signIn()
        let second = try runtime(wire, journal, storeKit: kit, origin: URL(string: "https://other.invalid")!)
        await second.purchases.purchase(); XCTAssertEqual(kit.buys, 0)
        let counts = await wire.counts(); XCTAssertNil(counts["purchase/context"])
    }
    func testChangedInstallationDoesNotAdoptOldSession() async throws {
        let wire = try SessionWire(), kit = SessionStoreKit(), r = try runtime(wire, SessionJournal(), storeKit: kit)
        await r.identity.signIn(); await wire.changeOwner(); await r.purchases.purchase()
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
        await first.identity.signIn(); await first.purchases.purchase()
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
