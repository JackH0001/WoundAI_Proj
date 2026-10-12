import XCTest
#if WOUND_INSTITUTION_TESTS
@testable import WoundAIInstitution
#else
@testable import WoundMeasurementApp
#endif

private actor MedicalSessionTransport: InstitutionAuthenticatedTransport {
    var logins = 0, validations = 0
    var status = 200
    var delay: UInt64 = 0
    var malformed = false
    var role = "physician"
    func configure(status: Int = 200, delay: UInt64 = 0, malformed: Bool = false, role: String = "physician") {
        self.status = status; self.delay = delay; self.malformed = malformed; self.role = role
    }
    func invalidate() {}
    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let login = request.url!.path == "/api/auth/login"
        if login { logins += 1 } else { validations += 1 }
        let code = login ? 200 : status
        let invalid = malformed
        let currentRole = role
        if delay > 0 { try await Task.sleep(nanoseconds: delay) }
        var body: [String: Any] = ["identity":"mmhps20261007:dr01", "org":"mmhps20261007",
            "user":"dr01", "role":currentRole, "role_zh":currentRole,
            "perms": currentRole == "physician" ? ["measure.sample", "gt.verify", "annotation.submit"] : ["measure.sample"]]
        if login { body["access_token"] = "synthetic-session-fixture" }
        else {
            body["session_contract"] = invalid ? "unknown" : "account-revision-v1"
            body["expires_at"] = Date().timeIntervalSince1970 + 300
        }
        return (try JSONSerialization.data(withJSONObject: body),
            HTTPURLResponse(url: request.url!, statusCode: code, httpVersion: nil, headerFields: nil)!)
    }
}

@MainActor
final class MedicalSessionPoolTests: XCTestCase {
    private var epoch: UInt64 = 0
    private var origin = "https://synthetic.invalid"
    private func pool(_ transport: MedicalSessionTransport) -> MedicalSessionPool {
        MedicalSessionPool(input: { [self] in
            MedicalSessionPool.Input(origin: origin, user: "dr01", password: UUID().uuidString, epoch: epoch)
        }, factory: { BackendClient(baseUrl: $0, institutionTransport: transport) })
    }
    private func rejected(_ task: Task<BackendClient, Error>) async {
        do { _ = try await task.value; XCTFail("Stale/invalid session accepted") } catch {}
    }
    func testTenAcquisitionsUseOneLoginAndTenLiveChecks() async throws {
        let transport = MedicalSessionTransport()
        let subject = self.pool(transport)
        for _ in 0..<10 { _ = try await subject.acquire() }
        let logins = await transport.logins, checks = await transport.validations
        XCTAssertEqual(logins, 1); XCTAssertEqual(checks, 10)
    }
    func testConcurrentAcquisitionsShareOneAuthentication() async throws {
        let transport = MedicalSessionTransport(); let pool = pool(transport)
        await transport.configure(delay: 30_000_000)
        let first = Task { try await pool.acquire() }; let second = Task { try await pool.acquire() }
        let a = try await first.value, b = try await second.value
        XCTAssertTrue(a === b)
        let logins = await transport.logins, checks = await transport.validations
        XCTAssertEqual(logins, 1); XCTAssertEqual(checks, 1)
    }
    func testCredentialGenerationAndOriginRequireNewClient() async throws {
        let transport = MedicalSessionTransport(); let pool = pool(transport)
        let a = try await pool.acquire(); epoch += 1
        let b = try await pool.acquire(); origin = "https://second.synthetic.invalid"
        let c = try await pool.acquire()
        XCTAssertFalse(a === b); XCTAssertFalse(b === c)
        let count = await transport.logins; XCTAssertEqual(count, 3)
    }
    func testLogoutDuringLoginRejectsLateResponse() async throws {
        let transport = MedicalSessionTransport(); let pool = pool(transport)
        await transport.configure(delay: 40_000_000)
        let task = Task { try await pool.acquire() }
        while await transport.logins == 0 { await Task.yield() }
        epoch += 1; MedicalSessionEpoch.shared.invalidate()
        await rejected(task)
        let count = await transport.validations; XCTAssertEqual(count, 0)
    }
    func testGoogleSwitchDuringValidationRejectsLateResponse() async throws {
        let transport = MedicalSessionTransport(); let pool = pool(transport)
        let previous = try await pool.acquire()
        await transport.configure(delay: 40_000_000)
        let task = Task { try await pool.acquire() }
        while await transport.validations < 2 { await Task.yield() }
        epoch += 1; MedicalSessionEpoch.shared.invalidate()
        await rejected(task)
        let identity = await previous.currentIdentity(); XCTAssertNil(identity)
    }
    func testTransientReadFailureDoesNotRetryLoginOrKeepVerifiedIdentity() async throws {
        let transport = MedicalSessionTransport(); let pool = pool(transport)
        let client = try await pool.acquire()
        await transport.configure(status: 503)
        await rejected(Task { try await pool.acquire() })
        let identity = await client.currentIdentity(); XCTAssertNil(identity)
        await transport.configure()
        _ = try await pool.acquire()
        let count = await transport.logins; XCTAssertEqual(count, 1)
    }
    func testRevokedCredentialIsNotAutomaticallyReloggedIn() async throws {
        let transport = MedicalSessionTransport(); let pool = pool(transport)
        _ = try await pool.acquire(); await transport.configure(status: 401)
        await rejected(Task { try await pool.acquire() })
        let count = await transport.logins; XCTAssertEqual(count, 1)
        // A later explicit acquisition may obtain the current account permissions.
        await transport.configure(role: "nurse")
        let client = try await pool.acquire(); let identity = await client.currentIdentity()
        XCTAssertFalse(identity!.canVerifyClinicalEdits)
    }
    func testMalformedSuccessDoesNotAuthorize() async throws {
        let transport = MedicalSessionTransport(); let pool = pool(transport)
        await transport.configure(malformed: true)
        await rejected(Task { try await pool.acquire() })
    }
    func testLegacyBackendDoesNotSilentlyBypassValidation() async throws {
        let transport = MedicalSessionTransport(); let pool = pool(transport)
        await transport.configure(status: 404)
        await rejected(Task { try await pool.acquire() })
        let count = await transport.logins; XCTAssertEqual(count, 1)
    }
    func testOldClientCannotDispatchAfterGlobalAccountChange() async throws {
        let transport = MedicalSessionTransport(); let pool = pool(transport)
        let client = try await pool.acquire()
        MedicalSessionEpoch.shared.invalidate()
        do { _ = try await client.validateMedicalSession(); XCTFail("Old client dispatched") } catch {}
        let checks = await transport.validations; XCTAssertEqual(checks, 1)
    }
}
