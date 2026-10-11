import XCTest
#if WOUND_INSTITUTION_TESTS
@testable import WoundAIInstitution
#else
@testable import WoundMeasurementApp
#endif

@MainActor
final class InstitutionGoogleTokenTests: XCTestCase {
    private func jwt(_ overrides: [String: Any] = [:]) throws -> String {
        var claims: [String: Any] = ["sub": "person-a", "aud": "iap-web", "iss": "https://accounts.google.com", "exp": Date().addingTimeInterval(3600).timeIntervalSince1970]
        claims.merge(overrides) { _, new in new }
        let b64 = try JSONSerialization.data(withJSONObject: claims).base64EncodedString()
            .replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "")
        return "fixture.\(b64).signature"
    }
    func testReadsActualClaimsInsteadOfAssumingConfiguredAudience() throws {
        let t = try InstitutionGoogleClaims.token(jwt(), subject: "person-a", audience: "iap-web")
        XCTAssertEqual(t.subject, "person-a"); XCTAssertEqual(t.audience, "iap-web")
        for changes: [String: Any] in [["aud": "ios-client"], ["sub": "person-b"], ["iss": "https://attacker.invalid"], ["exp": Date().timeIntervalSince1970 + 15], ["aud": ["iap-web", "other"]], ["exp": "tomorrow"]] {
            XCTAssertThrowsError(try InstitutionGoogleClaims.token(jwt(changes), subject: "person-a", audience: "iap-web"))
        }
    }
    func testMalformedTokenFailsClosed() {
        for raw in ["", "a.b", "a.b.c", ".e30.c", "a.e30.", String(repeating: "x", count: 33000)] {
            XCTAssertThrowsError(try InstitutionGoogleClaims.token(raw, subject: "person-a", audience: "iap-web"))
        }
    }
    func testRefreshRunsOnEachUseAndRevocationStopsFurtherRefresh() async throws {
        let raw = try jwt(); var calls = 0
        let p = InstitutionRefreshingTokenProvider(subject: "person-a", audience: "iap-web") { calls += 1; return raw }
        _ = try await p.freshToken(); _ = try await p.freshToken()
        XCTAssertEqual(calls, 2)
        p.invalidate()
        do { _ = try await p.freshToken(); XCTFail("Revoked provider returned a token") } catch {}
        XCTAssertEqual(calls, 2)
    }
    func testSignOutDuringRefreshRejectsLateToken() async throws {
        let raw = try jwt()
        let gate = TokenRefreshGate()
        let p = InstitutionRefreshingTokenProvider(subject: "person-a", audience: "iap-web") { await gate.wait() }
        let task = Task { try await p.freshToken() }
        await gate.waitUntilStarted()
        p.invalidate(); gate.finish(raw)
        do { _ = try await task.value; XCTFail("Late refresh survived sign-out") } catch {}
    }
    func testRefreshFailureDoesNotReturnPreviousToken() async throws {
        let raw = try jwt(); var succeeds = true
        let p = InstitutionRefreshingTokenProvider(subject: "person-a", audience: "iap-web") {
            if succeeds { return raw }; throw InstitutionEntryError.signInRequired
        }
        _ = try await p.freshToken(); succeeds = false
        do { _ = try await p.freshToken(); XCTFail("Returned stale token") } catch {}
    }
    #if WOUND_INSTITUTION_TESTS
    func testActualMMHClientCapturesTransportAndFailsWithoutGoogleEntry() async throws {
        let registry = InstitutionTransportRegistry.shared
        let saved = registry.replace(with: nil)
        defer { registry.replace(with: saved) }
        let beforeSignIn = BackendClient(baseUrl: "https://test.invalid")
        let first = RegistryTestTransport()
        registry.replace(with: first)
        let signedIn = BackendClient(baseUrl: "https://test.invalid")
        do { _ = try await beforeSignIn.health(); XCTFail("Pre-login client rebound to a later identity") } catch {}
        let health = try await signedIn.health()
        XCTAssertEqual(health.status, "ok")
        let old = registry.replace(with: nil); await old?.invalidate()
        do { _ = try await signedIn.health(); XCTFail("Old identity survived sign-out") } catch {}
        let afterSignOut = BackendClient(baseUrl: "https://test.invalid")
        do { _ = try await afterSignOut.health(); XCTFail("Unauthenticated fallback") } catch {}
    }
    #endif
    func testRegistrySnapshotsDoNotRebindOldClientsToNewIdentity() async throws {
        let registry = InstitutionTransportRegistry()
        let first = RegistryTestTransport(); let second = RegistryTestTransport()
        XCTAssertNil(registry.snapshot())
        registry.replace(with: first)
        let captured = registry.snapshot()
        let removed = registry.replace(with: nil); await removed?.invalidate()
        registry.replace(with: second)
        let request = URLRequest(url: URL(string: "https://test.invalid/api/health")!)
        do { _ = try await captured?.send(request); XCTFail("Old snapshot was usable") } catch {}
        let reply = try await registry.snapshot()?.send(request)
        XCTAssertEqual(reply?.1.statusCode, 200)
    }
}

@MainActor
private final class TokenRefreshGate {
    private var continuation: CheckedContinuation<String, Never>?
    private var started: CheckedContinuation<Void, Never>?
    func wait() async -> String {
        await withCheckedContinuation { continuation = $0; started?.resume(); started = nil }
    }
    func waitUntilStarted() async {
        if continuation != nil { return }
        await withCheckedContinuation { started = $0 }
    }
    func finish(_ raw: String) { continuation?.resume(returning: raw); continuation = nil }
}
private actor RegistryTestTransport: InstitutionAuthenticatedTransport {
    private var active = true
    func invalidate() { active = false }
    func send(_ request: URLRequest) throws -> (Data, HTTPURLResponse) {
        guard active else { throw InstitutionEntryError.signInRequired }
        return (Data(#"{"status":"ok"}"#.utf8), HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!)
    }
}
