// Included ONLY by run_lite_native_wire.py in an isolated simulator test target.
// No real Apple sign-in, StoreKit purchase, research data, or Cloud credentials.
import XCTest
@testable import WoundLite

private struct WireConfig: Decodable { let url: URL, token: String }
private struct WireSeed: Decodable { let owner: String, now: Int, jpeg: String; let research_owner: String? }
private struct ResearchReadback: Decodable {
    let image_id, bundle_sha256, rgb_sha256, depth_sha256, metadata_sha256, media_generation: String
    let index_rows: Int
    let ai_polygons: [[[Double]]]
    struct Label: Decodable {
        let revision: Int, polygons: [[[Int]]], surface_cm2: Double
    }
    let labels: [Label]
}
private final class WireClock: @unchecked Sendable {
    private let lock = NSLock()
    private var value: Int
    init(_ value: Int) { self.value = value }
    func date() -> Date {
        lock.lock(); defer { lock.unlock() }
        return Date(timeIntervalSince1970: Double(value))
    }
    func advance(_ seconds: Int) {
        lock.lock(); defer { lock.unlock() }
        value += seconds
    }
}
private struct WireSnapshot: Decodable {
    let model_calls, used, attempts: Int
    let registered_installations: Int
    let research_unchanged, image_unchanged: Bool
    struct Route: Decodable { let path: String, status: Int }
    let routes: [Route]
}

private actor WirePairBarrier {
    var waiting: CheckedContinuation<Void, Never>?
    func arrive() async {
        if let first = waiting { waiting = nil; first.resume(); return }
        await withCheckedContinuation { waiting = $0 }
    }
}

private actor NativeWire: LiteAuthenticatedTransport {
    let config: WireConfig, owner: String
    let research: Bool
    var dropRun = false
    var dropCapture = false
    var dropRevision = false
    var woundStatusBarrier: WirePairBarrier?
    init(_ config: WireConfig, owner: String, research: Bool = false) {
        self.config = config; self.owner = owner; self.research = research
    }
    func installation() -> String { owner }
    func loseRunResponse() { dropRun = true }
    func loseCaptureResponse() { dropCapture = true }
    func loseRevisionResponse() { dropRevision = true }
    func holdNextWoundStatus(_ barrier: WirePairBarrier) { woundStatusBarrier = barrier }
    static func control(_ config: WireConfig, _ path: String, _ body: Data = Data("{}".utf8)) async throws -> Data {
        var request = URLRequest(url: config.url.appendingPathComponent("fixture/" + path))
        request.httpMethod = "POST"; request.httpBody = body
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue(config.token, forHTTPHeaderField: "X-Synthetic-Wire")
        let (data, response) = try await URLSession.shared.data(for: request)
        guard (response as? HTTPURLResponse)?.statusCode == 200 else { throw LiteAccountFailure.unavailable }
        return data
    }
    func send(_ original: URLRequest, installation: String) async throws -> (Data, HTTPURLResponse) {
        guard installation == owner, original.url?.host == (research ? "research-wire.invalid" : "native-wire.invalid"),
              original.url?.path.hasPrefix("/api/v1/lite/") == true else { throw LiteAccountFailure.invalid }
        var request = original
        request.url = config.url.appendingPathComponent(String(original.url!.path.dropFirst()))
        request.setValue(config.token, forHTTPHeaderField: "X-Synthetic-Wire")
        request.setValue(owner, forHTTPHeaderField: "X-Synthetic-Installation")
        request.setValue(research ? "research" : "native", forHTTPHeaderField: "X-Synthetic-Service")
        let (data, response) = try await URLSession.shared.data(for: request)
        let http = try XCTUnwrap(response as? HTTPURLResponse)
        if original.url?.lastPathComponent == "wounds",
           let raw = original.httpBody, let body = try? JSONSerialization.jsonObject(with: raw) as? [String:Any],
           body["action"] as? String == "status", let barrier = woundStatusBarrier {
            woundStatusBarrier = nil
            await barrier.arrive()
        }
        if dropRun && original.url?.lastPathComponent == "run" {
            dropRun = false; throw URLError(.networkConnectionLost)
        }
        if dropCapture && original.url?.path == "/api/v1/lite/research/capture" && http.statusCode == 200 {
            dropCapture = false; throw URLError(.networkConnectionLost)
        }
        if dropRevision && original.url?.path == "/api/v1/lite/annotation/revision" && http.statusCode == 200 {
            dropRevision = false; throw URLError(.networkConnectionLost)
        }
        // Only the QA transport maps HTTPS origin to loopback; production origin
        // checks and all request/response parsing remain unchanged.
        return (data, try XCTUnwrap(HTTPURLResponse(url: original.url!, statusCode: http.statusCode,
            httpVersion: nil, headerFields: http.allHeaderFields as? [String: String])))
    }
}

@MainActor private final class WireApple: LiteAppleIdentityProvider {
    let config: WireConfig
    var signIns = 0
    init(_ config: WireConfig) { self.config = config }
    func signIn(_ challenge: LiteAccountChallenge) async throws -> LiteAppleIdentity {
        signIns += 1
        struct Body: Encodable { let challenge: LiteAccountChallenge }
        return try JSONDecoder().decode(LiteAppleIdentity.self,
            from: await NativeWire.control(config, "identity", JSONEncoder().encode(Body(challenge: challenge))))
    }
    func authorized(user: String) async throws -> Bool { user == "apple-human-1" }
}

@MainActor private final class WireStoreKit: LitePurchaseProvider {
    let config: WireConfig
    var buys = 0, finishes = 0, restores = 0
    var current: LitePurchaseEvent?
    let quote = LiteMonthlyOffer(name: "Synthetic Plus", displayPrice: "$1.99", price: 1.99,
                                currency: "USD", storefront: "143441")
    init(_ config: WireConfig) { self.config = config }
    func monthlyOffer() async throws -> LiteMonthlyOffer { quote }
    func knownPurchases() async throws -> [LitePurchaseEvent] { current.map { [$0] } ?? [] }
    func purchase(offer: LiteMonthlyOffer, token: UUID) async throws -> LitePurchaseOutcome {
        buys += 1
        let data = try await NativeWire.control(config, "purchase",
            JSONEncoder().encode(["token":token.uuidString.lowercased()]))
        let event = try JSONDecoder().decode(LitePurchaseEvent.self, from: data)
        current = event; return .completed(event)
    }
    func restore() async throws -> [LitePurchaseEvent] { restores += 1; return current.map { [$0] } ?? [] }
    func unfinished() async throws -> [LitePurchaseEvent] { [] }
    func updates() -> AsyncStream<LitePurchaseUpdate> { AsyncStream { $0.finish() } }
    func finish(_ event: LitePurchaseEvent) async throws { finishes += 1 }
}

@MainActor final class LiteNativeWireTests: XCTestCase {
    private func config() throws -> WireConfig {
        let url = try XCTUnwrap(Bundle(for: Self.self).url(forResource: "native-wire", withExtension: "json"))
        return try JSONDecoder().decode(WireConfig.self, from: Data(contentsOf: url))
    }
    private func session(_ wire: NativeWire, _ apple: WireApple, _ kit: WireStoreKit,
                         _ directory: URL, _ time: Int,
                         clock: (@Sendable () -> Date)? = nil) throws -> LiteNativeSession {
        try LiteNativeSession(origin: URL(string: "https://native-wire.invalid")!, transport: wire,
            identityProvider: apple, purchaseProvider: kit,
            journal: LiteServiceFileJournal(directory: directory.appendingPathComponent("journal")),
            captures: LiteCaptureDraftStore(directory: directory.appendingPathComponent("captures")),
            register: { await wire.installation() }, now: clock ?? { Date(timeIntervalSince1970: Double(time)) })
    }
    private func finish(_ config: WireConfig, _ name: String) async throws -> WireSnapshot {
        try JSONDecoder().decode(WireSnapshot.self, from: await NativeWire.control(config, "finish",
            JSONEncoder().encode(["name":name])))
    }
    func testResearchRGBDLostRepliesPreserveMediaAndTwoRevisionOrder() async throws {
        let cfg = try config()
        let seed = try JSONDecoder().decode(WireSeed.self, from: await NativeWire.control(cfg,"reset-research"))
        let owner = try XCTUnwrap(seed.research_owner), clock = WireClock(seed.now)
        XCTAssertNotEqual(owner,seed.owner)
        let wire = NativeWire(cfg,owner:seed.owner), research = NativeWire(cfg,owner:owner,research:true)
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let native = try session(wire,WireApple(cfg),WireStoreKit(cfg),directory,seed.now,clock:{clock.date()})
        await native.identity.signIn()
        let image = try XCTUnwrap(Data(base64Encoded:seed.jpeg)), recordID = UUID().uuidString
        let recognized = try await native.recognizeSaved(recordID:recordID,jpeg:image)
        XCTAssertEqual(recognized.note,"confirmed",recognized.message)
        let result = try XCTUnwrap(recognized.reply?.result)
        let context = try await native.identity.activeServiceContext()
        let delivery = LiteResearchCaptureDelivery(source:wire,
            currentAccount:{try await native.identity.activeServiceContext()},
            researchOwner:{_ in owner}, permits:{_ in true}, researchTransport:{_,_ in research})
        let pinValue = await delivery.pin(.init(origin:"https://research-wire.invalid",epoch:nil))
        let pin = try XCTUnwrap(pinValue)
        XCTAssertEqual(pin.account,context)
        var depth = DepthCapture(map:(0..<64).map {0.3 + Float($0)*0.0001},width:8,height:8,
            fx:50,fy:50,cx:32,cy:32,refWidth:64,refHeight:64,
            accuracy:"absolute",filtered:false,rgbWidth:64,rgbHeight:64,sourceExifOrientation:1)
        depth.captureProvenance = .init(id:UUID(),observedAt:clock.date())
        let packet = try LiteRawDepthPacket.make(depth:depth,jpeg:image)
        let store = LiteStore(fileURL:directory.appendingPathComponent("index.json"),now:{clock.date()})
        let job = LitePendingResearchCapture(pin:pin,captureID:depth.captureProvenance!.id.uuidString.lowercased(),
            imageHash:LiteServiceBytes.hash(image),operation:recognized.operation,
            resultHash:recognized.reply?.receipt?.result_sha256,width:64,height:64)
        let row = LiteRecord(id:recordID,dateISO:"2026-11-13T01:00:00Z",surfaceCm2:4,projectedCm2:4,
            quality:"synthetic",imageName:try XCTUnwrap(store.images.save(jpeg:image)),source:"manual",
            polysJson:"[[[5,5],[5,50],[50,50]]]",depthName:try XCTUnwrap(store.saveDepth(depth,matchingJPEG:image)),
            researchCapture:job,manuallyConfirmed:true)
        defer {
            store.images.delete(row.imageName)
            if let name = row.depthName { store.images.delete(name) }
            try? FileManager.default.removeItem(at:directory)
        }
        let saved = try XCTUnwrap(store.saveMeasurement(row,selectedWoundID:"",
            enteredLocation:.init(side:"right",site:"heel"),queueResearchSync:true))
        let frozen = try XCTUnwrap(saved.researchCapture?.firstRevision)
        await research.loseCaptureResponse()
        await delivery.resume(recordID,store:store)
        XCTAssertNil(store.records.first?.cloudSync)
        XCTAssertNotNil(store.records.first?.researchCapture?.encryptedTicket)
        func readback() async throws -> ResearchReadback {
            try JSONDecoder().decode(ResearchReadback.self,from:await NativeWire.control(cfg,"research-readback"))
        }
        let uploaded = try await readback()
        XCTAssertEqual(uploaded.rgb_sha256,LiteServiceBytes.hash(image))
        XCTAssertEqual(uploaded.depth_sha256,LiteServiceBytes.hash(packet.rawDepth))
        XCTAssertEqual(uploaded.bundle_sha256,packet.bundleDigest)
        XCTAssertEqual(uploaded.ai_polygons,result.wound_polygons)
        XCTAssertEqual(uploaded.labels.count,0)
        var edit = try XCTUnwrap(store.records.first)
        edit.surfaceCm2 = 5; edit.polysJson = "[[[5,5],[6,45],[50,49]]]"
        XCTAssertTrue(store.update(edit))
        _ = try await NativeWire.control(cfg,"advance",JSONEncoder().encode(["seconds":65]))
        clock.advance(65)
        let reopened = LiteStore(fileURL:directory.appendingPathComponent("index.json"),now:{clock.date()})
        await delivery.resume(recordID,store:reopened)
        let sync = try XCTUnwrap(reopened.records.first?.cloudSync,
            reopened.records.first?.researchCapture?.notice ?? "no research receipt")
        XCTAssertEqual(sync.pending?.payloadJSON,frozen)
        XCTAssertEqual(sync.binding.imageID,uploaded.image_id)
        XCTAssertEqual(sync.binding.storageReceipt?.rawDepthReceipt?.depth_sha256,uploaded.depth_sha256)
        let client = BackendClient(baseUrl:pin.scope.origin,liteTransport:research)
        func send(_ binding:LiteCloudBinding,_ pending:LitePendingRevision) async throws {
            try await client.liteRevision(bindingAnonID:binding.anonID,imageID:binding.imageID,
                revision:pending.revision,payloadJSON:pending.payloadJSON,digest:pending.digest,
                researchEpoch:binding.researchEpoch)
        }
        await research.loseRevisionResponse()
        await reopened.syncRevision(recordID:recordID,consent:{true},bindingAllowed:{_ in true},send:send)
        XCTAssertEqual(reopened.records.first?.cloudSync?.pending?.revision,1)
        let first = try await readback(); XCTAssertEqual(first.labels.map(\.revision),[1])
        let restarted = LiteStore(fileURL:directory.appendingPathComponent("index.json"),now:{clock.date()})
        await restarted.syncRevision(recordID:recordID,consent:{true},bindingAllowed:{_ in true},send:send)
        XCTAssertEqual(restarted.records.first?.cloudSync?.acknowledgedRevision,2)
        XCTAssertNil(restarted.records.first?.cloudSync?.pending)
        let finalMedia = try await readback()
        XCTAssertEqual(finalMedia.labels.map(\.revision),[1,2])
        XCTAssertEqual(finalMedia.labels.map(\.surface_cm2),[4,5])
        XCTAssertEqual(finalMedia.labels.map(\.polygons),[[[[5,5],[5,50],[50,50]]],[[[5,5],[6,45],[50,49]]]])
        XCTAssertEqual(finalMedia.index_rows,1); XCTAssertEqual(finalMedia.media_generation,uploaded.media_generation)
        XCTAssertEqual(finalMedia.bundle_sha256,uploaded.bundle_sha256)
        XCTAssertEqual(finalMedia.metadata_sha256,uploaded.metadata_sha256)
        XCTAssertEqual(finalMedia.ai_polygons,result.wound_polygons)
        let before = try JSONDecoder().decode(WireSnapshot.self,from:await NativeWire.control(cfg,"snapshot"))
        await restarted.syncRevision(recordID:recordID,consent:{true},bindingAllowed:{_ in true},send:send)
        XCTAssertEqual(restarted.images.rawBytes(row.imageName),image)
        XCTAssertEqual(restarted.loadDepth(try XCTUnwrap(row.depthName),matchingImage:row.imageName)?.map.map(\.bitPattern),depth.map.map(\.bitPattern))
        let final = try await finish(cfg,"research-media-revisions")
        XCTAssertEqual(final.routes.count,before.routes.count)
        XCTAssertEqual(final.model_calls,1); XCTAssertEqual(final.used,1); XCTAssertEqual(final.attempts,1)
        XCTAssertEqual(final.routes.filter {$0.path.hasSuffix("service/research-source")}.count,1)
        XCTAssertEqual(final.routes.filter {$0.path.hasSuffix("research/capture/status")}.count,2)
        XCTAssertEqual(final.routes.filter {$0.path.hasSuffix("research/capture")}.count,1)
        XCTAssertEqual(final.routes.filter {$0.path.hasSuffix("annotation/revision")}.count,3)
        XCTAssertTrue(final.research_unchanged); XCTAssertTrue(final.image_unchanged)
    }
    func testRealHTTPFreePlusRestoreAndWrongAccountDoNotMixQuota() async throws {
        let cfg = try config()
        let seed = try JSONDecoder().decode(WireSeed.self, from: await NativeWire.control(cfg, "reset"))
        let image = try XCTUnwrap(Data(base64Encoded: seed.jpeg)), originalHash = LiteServiceBytes.hash(image)
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let wire = NativeWire(cfg, owner: seed.owner), apple = WireApple(cfg), kit = WireStoreKit(cfg)
        let r = try session(wire, apple, kit, directory, seed.now)
        await r.identity.signIn()
        let active = await r.identity.canUseService(); XCTAssertTrue(active, r.identity.message)
        let initial = try await r.personalBalance(); XCTAssertEqual(initial.limit, 2); XCTAssertEqual(initial.used, 0)
        for n in 1...2 {
            let result = try await r.recognizeSaved(recordID: String(format: "00000000-0000-0000-0000-%012x", n), jpeg: image)
            XCTAssertEqual(result.note, "confirmed", result.message)
        }
        let denied = try await r.recognizeSaved(recordID: String(format: "00000000-0000-0000-0000-%012x", 3), jpeg: image)
        XCTAssertEqual(denied.note, "quota")
        await r.purchases.loadOffer(); await r.purchases.purchase(accepting: kit.quote)
        let upgraded = try await r.personalBalance()
        XCTAssertEqual(upgraded.limit, 10, r.purchases.message); XCTAssertEqual(upgraded.used, 2)
        await r.purchases.restore(); await r.purchases.restore()
        let restored = try await r.personalBalance(); XCTAssertEqual(restored, upgraded)
        XCTAssertEqual(kit.buys, 1); XCTAssertEqual(kit.restores, 2)
        let saved = try await r.recognizeSaved(recordID: String(format: "00000000-0000-0000-0000-%012x", 4), jpeg: image)
        XCTAssertEqual(saved.note, "confirmed")
        let beforeWrongAccount = try JSONDecoder().decode(WireSnapshot.self,
            from: await NativeWire.control(cfg, "snapshot"))
        let e = try XCTUnwrap(kit.current)
        kit.current = LitePurchaseEvent(transaction: e.transaction, original: e.original,
            token: "99999999-9999-4999-8999-999999999999", product: e.product, environment: e.environment, signed: e.signed)
        await r.purchases.restore()
        let afterWrongAccount = try await r.personalBalance()
        XCTAssertEqual(afterWrongAccount.used, 3); XCTAssertEqual(afterWrongAccount.limit, 10)
        XCTAssertEqual(kit.buys, 1)
        await r.identity.signOut()
        let allowed = await r.identity.canUseService(); XCTAssertFalse(allowed)
        let local = try await r.service.stored(saved.captureID); XCTAssertEqual(local, saved)
        XCTAssertEqual(LiteServiceBytes.hash(image), originalHash)
        let final = try await finish(cfg, "free-plus-restore")
        XCTAssertEqual(final.model_calls, 3); XCTAssertEqual(final.used, 3); XCTAssertEqual(final.attempts, 3)
        XCTAssertEqual(final.routes.filter { $0.path.hasSuffix("purchase/confirm") }.count,
                       beforeWrongAccount.routes.filter { $0.path.hasSuffix("purchase/confirm") }.count)
        XCTAssertEqual(final.routes.filter { $0.path.hasSuffix("account/status") }.count, 25)
        XCTAssertTrue(final.research_unchanged); XCTAssertTrue(final.image_unchanged)
    }

    func testRealHTTPLostRunReplySurvivesBothRestartsAndSessionRenewal() async throws {
        let cfg = try config()
        let seed = try JSONDecoder().decode(WireSeed.self, from: await NativeWire.control(cfg, "reset"))
        let image = try XCTUnwrap(Data(base64Encoded: seed.jpeg))
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let wire = NativeWire(cfg, owner: seed.owner), apple = WireApple(cfg), kit = WireStoreKit(cfg)
        let r = try session(wire, apple, kit, directory, seed.now)
        await r.identity.signIn(); await wire.loseRunResponse()
        let waiting = try await r.recognizeSaved(recordID: String(format: "00000000-0000-0000-0000-%012x", 1), jpeg: image)
        XCTAssertEqual(waiting.note, "unavailable")
        _ = try await NativeWire.control(cfg, "advance", JSONEncoder().encode(["seconds":1201]))
        _ = try await NativeWire.control(cfg, "restart")
        let reopened = try session(wire, apple, kit, directory, seed.now + 1201)
        let result = try await reopened.recognizeSaved(recordID: String(format: "00000000-0000-0000-0000-%012x", 1), jpeg: image)
        XCTAssertEqual(result.note, "confirmed", result.message); XCTAssertEqual(result.operation, waiting.operation)
        let again = try await reopened.recognizeSaved(recordID: String(format: "00000000-0000-0000-0000-%012x", 1), jpeg: image)
        XCTAssertEqual(again, result); XCTAssertEqual(apple.signIns, 1)
        let balance = try await reopened.personalBalance(); XCTAssertEqual(balance.used, 1)
        let final = try await finish(cfg, "lost-response-renewal")
        XCTAssertEqual(final.model_calls, 1); XCTAssertEqual(final.used, 1); XCTAssertEqual(final.attempts, 1)
        XCTAssertEqual(final.routes.filter { $0.path.hasSuffix("service/run") }.count, 1)
        XCTAssertEqual(final.routes.filter { $0.path.hasSuffix("service/status") }.count, 1)
        XCTAssertEqual(final.routes.filter { $0.path.hasSuffix("account/renew") && $0.status == 200 }.count, 1)
        // The lost run response never reached the old post-response check, so
        // this scenario eliminates three status calls (nine -> six), not four.
        XCTAssertEqual(final.routes.filter { $0.path.hasSuffix("account/status") }.count, 6)
        XCTAssertTrue(final.research_unchanged); XCTAssertTrue(final.image_unchanged)
    }

    func testServerRevocationAfterPreflightRejectsRunWithoutDebit() async throws {
        let cfg = try config()
        let seed = try JSONDecoder().decode(WireSeed.self, from: await NativeWire.control(cfg, "reset"))
        let image = try XCTUnwrap(Data(base64Encoded: seed.jpeg))
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let wire = NativeWire(cfg, owner: seed.owner), apple = WireApple(cfg), kit = WireStoreKit(cfg)
        let r = try session(wire, apple, kit, directory, seed.now)
        await r.identity.signIn()
        _ = try await NativeWire.control(cfg, "revoke-before-service")
        let result = try await r.recognizeSaved(recordID: "00000000-0000-0000-0000-000000000001", jpeg: image)
        XCTAssertEqual(result.note, "unavailable")
        let final = try await finish(cfg, "revoked-after-preflight")
        XCTAssertEqual(final.model_calls, 0); XCTAssertEqual(final.used, 0); XCTAssertEqual(final.attempts, 0)
        XCTAssertEqual(final.routes.filter { $0.path.hasSuffix("service/prepare") && $0.status == 401 }.count, 1)
        XCTAssertEqual(final.routes.filter { $0.path.hasSuffix("service/run") }.count, 0)
        XCTAssertTrue(final.research_unchanged); XCTAssertTrue(final.image_unchanged)
    }

    func testTwoInstallationsShareFiveSlotsAndKeepTimelineThroughDowngrade() async throws {
        let cfg = try config()
        let first = try JSONDecoder().decode(WireSeed.self, from: await NativeWire.control(cfg, "reset"))
        let second = try JSONDecoder().decode(WireSeed.self, from: await NativeWire.control(cfg, "second-installation"))
        XCTAssertNotEqual(first.owner, second.owner)
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let image = try XCTUnwrap(Data(base64Encoded: first.jpeg))
        let wa = NativeWire(cfg, owner: first.owner), wb = NativeWire(cfg, owner: second.owner)
        let aa = WireApple(cfg), ab = WireApple(cfg), ka = WireStoreKit(cfg), kb = WireStoreKit(cfg)
        let clock = WireClock(first.now)
        let a = try session(wa, aa, ka, directory.appendingPathComponent("a"), first.now, clock: { clock.date() })
        let b = try session(wb, ab, kb, directory.appendingPathComponent("b"), first.now, clock: { clock.date() })
        await a.identity.signIn(); await b.identity.signIn()
        let ca = try await a.identity.savedResearchContext(), cb = try await b.identity.savedResearchContext()
        XCTAssertEqual(ca.account, cb.account)
        func id(_ n: Int) -> String { String(format:"10000000-0000-0000-0000-%012x", n) }
        for (r,n) in [(a,1),(b,2)] {
            let result = try await r.recognizeSaved(recordID:id(100+n),jpeg:image)
            XCTAssertEqual(result.note,"confirmed",result.message)
        }
        let sharedFree = try await b.personalBalance()
        XCTAssertEqual(sharedFree.used,2); XCTAssertEqual(sharedFree.limit,2)
        _ = try await a.woundPermit(id:id(1),owner:nil)
        do { _ = try await b.woundPermit(id:id(2),owner:nil); XCTFail("second free slot accepted") }
        catch LiteWoundTrackingError.full(let n) { XCTAssertEqual(n,1) }
        await a.purchases.loadOffer(); await a.purchases.purchase(accepting:ka.quote)
        let plus = try await b.personalBalance()
        XCTAssertEqual(plus.limit,10); XCTAssertEqual(plus.used,2)
        for n in 2...4 { _ = try await (n % 2 == 0 ? b : a).woundPermit(id:id(n),owner:nil) }
        let barrier = WirePairBarrier()
        await wa.holdNextWoundStatus(barrier); await wb.holdNextWoundStatus(barrier)
        func compete(_ session: LiteNativeSession, _ n: Int) async throws -> Bool {
            do { _ = try await session.woundPermit(id:id(n),owner:nil); return true }
            catch LiteWoundTrackingError.changed { return false }
        }
        async let left = compete(a,5)
        async let right = compete(b,6)
        let won = try await (left,right)
        XCTAssertNotEqual(won.0,won.1)
        let rosterValue = try await a.woundList()
        let roster = try XCTUnwrap(rosterValue)
        XCTAssertEqual(roster.active.count,5); XCTAssertEqual(roster.revision,5)
        let rowsURL = directory.appendingPathComponent("records.json")
        let store = LiteStore(fileURL:rowsURL)
        for (index,wireID) in roster.active.enumerated() {
            let woundID = try XCTUnwrap(LiteWoundRoster.localID(wireID))
            let row = LiteRecord(id:id(200+index),dateISO:"2026-11-13T01:00:00Z",surfaceCm2:3,
                projectedCm2:3,tiltDeg:0,volumeMl:nil,maxDepthMm:nil,quality:"synthetic",
                imageName:"preserved-synthetic.jpg",source:"manual",polysJson:"[]",depthName:"preserved-depth.bin",
                woundID:woundID,woundName:"Synthetic \(index)",woundAccount:ca.account,woundEnded:false)
            XCTAssertTrue(store.add(row))
        }
        func stableRows(_ rows:[LiteRecord]) throws -> Data {
            let encoder=JSONEncoder();encoder.outputFormatting = [.sortedKeys]
            return try encoder.encode(rows.map { row in var copy=row;copy.woundEnded=nil;return copy })
        }
        let originalRows = try stableRows(store.records)
        let ended = try XCTUnwrap(store.wounds.first { $0.id == id(1) })
        _ = try await b.finishTracking(ended)
        _ = try await store.refreshTracking(from:a)
        XCTAssertTrue(try XCTUnwrap(store.wounds.first { $0.id == id(1) }).ended)
        do { _ = try await a.woundPermit(id:id(1),owner:ca.account); XCTFail("stale device reopened ended wound") }
        catch LiteWoundTrackingError.ended { }
        _ = try await b.woundPermit(id:id(7),owner:nil)
        let swappedValue = try await a.woundList()
        let swapped = try XCTUnwrap(swappedValue)
        XCTAssertEqual(swapped.active.count,5); XCTAssertEqual(swapped.revision,7)
        _ = try await NativeWire.control(cfg,"subscription-status",JSONEncoder().encode(["status":5]))
        _ = try await NativeWire.control(cfg,"advance",JSONEncoder().encode(["seconds":31]))
        clock.advance(31)
        let downgradedValue = try await a.woundList()
        let downgraded = try XCTUnwrap(downgradedValue)
        XCTAssertEqual(downgraded.limit,1); XCTAssertEqual(downgraded.active,swapped.active)
        XCTAssertEqual(downgraded.selectableGroups(in:store.wounds).count,1)
        let pausedID = try XCTUnwrap(LiteWoundRoster.localID(downgraded.active[1]))
        do { _ = try await b.woundPermit(id:pausedID,owner:ca.account); XCTFail("downgraded extra slot accepted") }
        catch LiteWoundTrackingError.full(let n) { XCTAssertEqual(n,1) }
        _ = try await store.refreshTracking(from:a)
        XCTAssertEqual(try stableRows(store.records),originalRows)
        let timeline = try LiteTimelineSelection(group:ended,records:store.records)
        XCTAssertEqual(timeline.rows.count,1); XCTAssertEqual(timeline.rows.first?.woundID,id(1))
        _ = try await NativeWire.control(cfg,"subscription-status",JSONEncoder().encode(["status":1]))
        _ = try await NativeWire.control(cfg,"advance",JSONEncoder().encode(["seconds":31]))
        clock.advance(31)
        let restoredValue = try await b.woundList()
        let restored = try XCTUnwrap(restoredValue)
        XCTAssertEqual(restored.limit,5); XCTAssertEqual(restored.active,swapped.active)
        let balance = try await b.personalBalance(); XCTAssertEqual(balance.used,2)
        let reopened = LiteStore(fileURL:rowsURL)
        XCTAssertEqual(try stableRows(reopened.records),originalRows)
        let final = try await finish(cfg,"two-installation-wound-tracking")
        XCTAssertEqual(final.registered_installations,2)
        XCTAssertEqual(final.model_calls,2); XCTAssertEqual(final.used,2); XCTAssertEqual(final.attempts,2)
        XCTAssertTrue(final.research_unchanged); XCTAssertTrue(final.image_unchanged)
        XCTAssertEqual(ka.buys,1); XCTAssertEqual(kb.buys,0)
    }
}
