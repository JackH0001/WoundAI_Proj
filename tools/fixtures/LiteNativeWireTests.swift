// Included ONLY by run_lite_native_wire.py in an isolated simulator test target.
// No real Apple sign-in, StoreKit purchase, research data, or Cloud credentials.
import XCTest
@testable import WoundLite

private struct WireConfig: Decodable { let url: URL, token: String }
private struct WireSeed: Decodable { let owner: String, now: Int, jpeg: String }
private struct WireSnapshot: Decodable {
    let model_calls, used, attempts: Int
    let research_unchanged, image_unchanged: Bool
    struct Route: Decodable { let path: String, status: Int }
    let routes: [Route]
}

private actor NativeWire: LiteAuthenticatedTransport {
    let config: WireConfig, owner: String
    var dropRun = false
    init(_ config: WireConfig, owner: String) { self.config = config; self.owner = owner }
    func installation() -> String { owner }
    func loseRunResponse() { dropRun = true }
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
        guard installation == owner, original.url?.host == "native-wire.invalid",
              original.url?.path.hasPrefix("/api/v1/lite/") == true else { throw LiteAccountFailure.invalid }
        var request = original
        request.url = config.url.appendingPathComponent(String(original.url!.path.dropFirst()))
        request.setValue(config.token, forHTTPHeaderField: "X-Synthetic-Wire")
        let (data, response) = try await URLSession.shared.data(for: request)
        let http = try XCTUnwrap(response as? HTTPURLResponse)
        if dropRun && original.url?.lastPathComponent == "run" {
            dropRun = false; throw URLError(.networkConnectionLost)
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
                         _ directory: URL, _ time: Int) throws -> LiteNativeSession {
        try LiteNativeSession(origin: URL(string: "https://native-wire.invalid")!, transport: wire,
            identityProvider: apple, purchaseProvider: kit,
            journal: LiteServiceFileJournal(directory: directory.appendingPathComponent("journal")),
            captures: LiteCaptureDraftStore(directory: directory.appendingPathComponent("captures")),
            register: { await wire.installation() }, now: { Date(timeIntervalSince1970: Double(time)) })
    }
    private func finish(_ config: WireConfig, _ name: String) async throws -> WireSnapshot {
        try JSONDecoder().decode(WireSnapshot.self, from: await NativeWire.control(config, "finish",
            JSONEncoder().encode(["name":name])))
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
}
