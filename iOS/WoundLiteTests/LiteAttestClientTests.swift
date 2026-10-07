import XCTest
import CryptoKit
@testable import WoundLite

actor AttestMemory: LiteAttestPersistence {
    var bytes: Data?
    var saves = 0
    var failSave = 0
    func load() -> Data? { bytes }
    func save(_ data: Data) throws {
        saves += 1
        if saves == failSave { throw LiteAttestFailure.unavailable }
        bytes = data
    }
    func remove() { bytes = nil }
    func fail(at count: Int) { failSave = count }
}
actor AttestPermission {
    var allowed = true
    func read() -> Bool { allowed }
    func deny() { allowed = false }
}
actor AttestDeviceDouble: LiteAttestDevice {
    var available = true
    var generated = 0
    var attested = 0
    var assertions: [Data] = []
    var failApple = false
    var permission: AttestPermission?
    let key = Data(repeating: 7, count: 32).base64EncodedString()
    func supported() -> Bool { available }
    func generateKey() -> String { generated += 1; return key }
    func attest(key: String, hash: Data) throws -> Data {
        attested += 1
        if failApple { failApple = false; throw LiteAttestFailure.appleTemporarilyUnavailable }
        return Data("synthetic-attestation".utf8)
    }
    func assertion(key: String, hash: Data) async -> Data {
        assertions.append(hash)
        await permission?.deny()
        return Data("assertion-\(assertions.count)".utf8)
    }
    func options(supported: Bool = true, unavailableOnce: Bool = false, revoke: AttestPermission? = nil) {
        available = supported; failApple = unavailableOnce; permission = revoke
    }
    func counts() -> (Int, Int, Int) { (generated, attested, assertions.count) }
    func hashes() -> [Data] { assertions }
}
actor AttestHTTPDouble: LiteAttestTransport {
    var registrations: [Data] = []
    var business: [URLRequest] = []
    var challengePurposes: [String] = []
    func purposes() -> [String] { challengePurposes }
    var challenges = 0
    var failRegistration = false
    var rejectRegistration = false
    var wrongAudience = false
    var expire = false
    var delay = false
    var inFlight = 0
    var peak = 0
    var reply: [String: Any] = ["status": "stored"]
    var failBusiness = false
    func businessReply(_ value: [String: Any], fail: Bool = false) { reply = value; failBusiness = fail }
    var businessStatus = 200
    var lastNonce = Data()
    let owner = String(repeating: "a", count: 32)
    func options(failRegistration: Bool = false, rejectRegistration: Bool = false,
                 wrongAudience: Bool = false, expire: Bool = false, delay: Bool = false, status: Int = 200) {
        self.failRegistration = failRegistration; self.rejectRegistration = rejectRegistration
        self.wrongAudience = wrongAudience; self.expire = expire; self.delay = delay; businessStatus = status
    }
    func snapshot() -> ([Data], [URLRequest], Int, Int, Data) { (registrations, business, challenges, peak, lastNonce) }
    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let url = request.url!
        var json: [String: Any] = [:], status = 200
        if url.path.hasSuffix("/attest/challenge") {
            let body = try JSONSerialization.jsonObject(with: request.httpBody!) as! [String: String]
            challengePurposes.append(body["purpose"]!)
            challenges += 1; lastNonce = Data(repeating: UInt8(challenges), count: 32)
            json = ["challenge_id": String(format: "%032x", challenges), "challenge": lastNonce.base64EncodedString(),
                    "expires_at": expire ? 900 : 1120, "audience": wrongAudience ? "other" : "lite-test"]
        } else if url.path.hasSuffix("/attest/register") {
            registrations.append(request.httpBody!)
            if failRegistration { failRegistration = false; throw URLError(.timedOut) }
            let input = try JSONSerialization.jsonObject(with: request.httpBody!) as! [String: String]
            json = ["key_id": input["key_id"]!, "installation": owner]
            status = rejectRegistration ? 401 : 200
        } else {
            business.append(request); inFlight += 1; peak = max(peak, inFlight)
            if delay { try await Task.sleep(nanoseconds: 50_000_000) }
            inFlight -= 1; status = businessStatus; json = reply
            if failBusiness { throw URLError(.timedOut) }
        }
        return (try JSONSerialization.data(withJSONObject: json), HTTPURLResponse(url: url, statusCode: status, httpVersion: nil, headerFields: nil)!)
    }
}

final class LiteAttestClientTests: XCTestCase {
    private let origin = URL(string: "https://lite.invalid")!
    private func client(_ device: AttestDeviceDouble, _ vault: AttestMemory, _ http: AttestHTTPDouble) throws -> LiteAttestClient {
        try LiteAttestClient(origin: origin, audience: "lite-test", device: device, persistence: vault,
                            transport: http, now: { Date(timeIntervalSince1970: 1000) })
    }
    private func request(_ path: String = "/api/v1/lite/segment", body: Data = Data([0, 1, 255, 10])) -> URLRequest {
        var r = URLRequest(url: origin.appendingPathComponent(path)); r.httpMethod = "POST"; r.httpBody = body
        r.setValue("multipart/form-data; boundary=exact", forHTTPHeaderField: "Content-Type"); return r
    }
    func testRegistrationPersistsKeyAndDropsAcknowledgedProof() async throws {
        let device = AttestDeviceDouble(), vault = AttestMemory(), http = AttestHTTPDouble()
        let c = try client(device, vault, http), owner = try await c.registeredInstallation()
        let stored = try JSONDecoder().decode(LiteAttestClient.Identity.self, from: await vault.load()!)
        XCTAssertEqual(stored.installation, owner); XCTAssertEqual(stored.phase, "registered")
        XCTAssertNil(stored.attestation); XCTAssertNil(stored.challengeID)
        let second = try client(device, vault, http)
        let restored = try await second.registeredInstallation(); XCTAssertEqual(restored, owner)
        let counts = await device.counts(); XCTAssertEqual(counts.0, 1); XCTAssertEqual(counts.1, 1)
    }
    func testLostRegistrationResponseRetriesExactProofAfterRestart() async throws {
        let device = AttestDeviceDouble(), vault = AttestMemory(), http = AttestHTTPDouble()
        await http.options(failRegistration: true)
        do { _ = try await client(device, vault, http).registeredInstallation(); XCTFail() } catch is URLError { }
        let pending = try JSONDecoder().decode(LiteAttestClient.Identity.self, from: await vault.load()!)
        XCTAssertEqual(pending.phase, "pending")
        _ = try await client(device, vault, http).registeredInstallation()
        let calls = await http.snapshot(); XCTAssertEqual(calls.0.count, 2); XCTAssertEqual(calls.0[0], calls.0[1])
        let counts = await device.counts(); XCTAssertEqual(counts.0, 1); XCTAssertEqual(counts.1, 1)
    }
    func testAppleUnavailableKeepsSameKey() async throws {
        let device = AttestDeviceDouble(), vault = AttestMemory(), http = AttestHTTPDouble()
        await device.options(unavailableOnce: true); let c = try client(device, vault, http)
        do { _ = try await c.registeredInstallation(); XCTFail() }
        catch { XCTAssertEqual(error as? LiteAttestFailure, .appleTemporarilyUnavailable) }
        _ = try await c.registeredInstallation()
        let counts = await device.counts(); XCTAssertEqual(counts.0, 1); XCTAssertEqual(counts.1, 2)
    }
    func testUnsupportedDoesNotGenerateKeyOrContactBackend() async throws {
        let device = AttestDeviceDouble(), vault = AttestMemory(), http = AttestHTTPDouble()
        await device.options(supported: false)
        do { _ = try await client(device, vault, http).registeredInstallation(); XCTFail() }
        catch { XCTAssertEqual(error as? LiteAttestFailure, .unsupported) }
        let counts = await device.counts(), calls = await http.snapshot()
        XCTAssertEqual(counts.0, 0); XCTAssertEqual(calls.2, 0)
    }
    func testPersistenceFailurePreventsRegistrationHTTP() async throws {
        let device = AttestDeviceDouble(), vault = AttestMemory(), http = AttestHTTPDouble()
        await vault.fail(at: 3)
        do { _ = try await client(device, vault, http).registeredInstallation(); XCTFail() } catch { }
        let calls = await http.snapshot(); XCTAssertTrue(calls.0.isEmpty)
    }
    func testServerRejectionRetainsPendingAndNeverRotatesKey() async throws {
        let device = AttestDeviceDouble(), vault = AttestMemory(), http = AttestHTTPDouble()
        await http.options(rejectRegistration: true); let c = try client(device, vault, http)
        for _ in 0..<2 {
            do { _ = try await c.registeredInstallation(); XCTFail() }
            catch { XCTAssertEqual(error as? LiteAttestFailure, .registrationRejected) }
        }
        let counts = await device.counts(); XCTAssertEqual(counts.0, 1); XCTAssertEqual(counts.1, 1)
    }
    func testWrongAudienceOrExpiredChallengeNeverAttests() async throws {
        for expired in [false, true] {
            let device = AttestDeviceDouble(), vault = AttestMemory(), http = AttestHTTPDouble()
            await http.options(wrongAudience: !expired, expire: expired)
            do { _ = try await client(device, vault, http).registeredInstallation(); XCTFail() } catch { }
            let counts = await device.counts(); XCTAssertEqual(counts.1, 0)
        }
    }
    func testCorruptOrDifferentOriginIdentityIsNotReplaced() async throws {
        let device = AttestDeviceDouble(), vault = AttestMemory(), http = AttestHTTPDouble()
        try await vault.save(Data("invalid".utf8))
        do { _ = try await client(device, vault, http).registeredInstallation(); XCTFail() } catch { }
        let counts = await device.counts(); XCTAssertEqual(counts.0, 0)
    }
    func testWithdrawalUsesDedicatedChallengeAndUploadKeepsNormalChallenge() async throws {
        let device = AttestDeviceDouble(), vault = AttestMemory(), http = AttestHTTPDouble()
        let c = try client(device, vault, http), owner = try await c.registeredInstallation()
        var deletion = URLRequest(url: URL(string: "https://demo.invalid/api/v1/lite/data/" + owner)!)
        deletion.httpMethod = "DELETE"
        // Use the same trusted origin as the regular fixture request.
        deletion.url = request().url!.deletingLastPathComponent().appendingPathComponent("data/" + owner)
        _ = try await c.send(deletion, installation: owner, requestID: UUID(), allowSending: { true })
        _ = try await c.send(request(), installation: owner, requestID: UUID(), allowSending: { true })
        let purposes = await http.purposes()
        XCTAssertEqual(purposes, ["register", "withdraw", "assert"])
    }
    func testExactBodyAndHeadersProduceExpectedHash() async throws {
        let device = AttestDeviceDouble(), vault = AttestMemory(), http = AttestHTTPDouble()
        let c = try client(device, vault, http), owner = try await c.registeredInstallation(), r = request(), id = UUID()
        _ = try await c.send(r, installation: owner, requestID: id, allowSending: { true })
        let calls = await http.snapshot(), sent = try XCTUnwrap(calls.1.first)
        XCTAssertEqual(sent.httpBody, r.httpBody); XCTAssertEqual(sent.value(forHTTPHeaderField: "Content-Type"), r.value(forHTTPHeaderField: "Content-Type"))
        XCTAssertEqual(sent.value(forHTTPHeaderField: "X-Lite-Request-ID"), id.uuidString.lowercased())
        let bytes = try LiteAttestRequest.clientData(audience: "lite-test", installation: owner, keyID: Data(repeating: 7, count: 32),
            challenge: calls.4, requestID: id.uuidString.lowercased(), method: "POST", path: r.url!.path,
            contentType: r.value(forHTTPHeaderField: "Content-Type")!, body: r.httpBody!)
        let hashes = await device.hashes(); XCTAssertEqual(hashes, [Data(SHA256.hash(data: bytes))])
    }
    func testConcurrentRegistrationAndSendsAreSerialized() async throws {
        let device = AttestDeviceDouble(), vault = AttestMemory(), http = AttestHTTPDouble()
        let c = try client(device, vault, http)
        async let first = c.registeredInstallation(); async let second = c.registeredInstallation()
        let owners = try await (first, second); XCTAssertEqual(owners.0, owners.1)
        await http.options(delay: true)
        let r = request()
        async let a = c.send(r, installation: owners.0, requestID: UUID(), allowSending: { true })
        async let b = c.send(r, installation: owners.0, requestID: UUID(), allowSending: { true })
        _ = try await (a, b)
        let calls = await http.snapshot(), counts = await device.counts()
        XCTAssertEqual(calls.3, 1); XCTAssertEqual(calls.1.count, 2); XCTAssertEqual(counts.0, 1)
        XCTAssertEqual(calls.1.map { $0.value(forHTTPHeaderField: "X-Lite-Assertion") }, [Data("assertion-1".utf8).base64EncodedString(), Data("assertion-2".utf8).base64EncodedString()])
    }
    func testRevocationDuringSigningPreventsMediaSend() async throws {
        let device = AttestDeviceDouble(), vault = AttestMemory(), http = AttestHTTPDouble(), permission = AttestPermission()
        let c = try client(device, vault, http), owner = try await c.registeredInstallation()
        await device.options(revoke: permission)
        do { _ = try await c.send(request(), installation: owner, requestID: UUID(), allowSending: { await permission.read() }); XCTFail() }
        catch { XCTAssertEqual(error as? LiteAttestFailure, .consentRequired) }
        let calls = await http.snapshot(); XCTAssertTrue(calls.1.isEmpty)
    }
    func testDifferentOwnerAndOriginNeverSend() async throws {
        let device = AttestDeviceDouble(), vault = AttestMemory(), http = AttestHTTPDouble()
        let c = try client(device, vault, http), owner = try await c.registeredInstallation()
        do { _ = try await c.send(request(), installation: "other", requestID: UUID(), allowSending: { true }); XCTFail() } catch { }
        var r = request(); r.url = URL(string: "https://other.invalid/api/v1/lite/segment")!
        do { _ = try await c.send(r, installation: owner, requestID: UUID(), allowSending: { true }); XCTFail() } catch { }
        let calls = await http.snapshot(); XCTAssertTrue(calls.1.isEmpty)
    }
    func testBusinessFailureIsNotAutomaticallyRetried() async throws {
        let device = AttestDeviceDouble(), vault = AttestMemory(), http = AttestHTTPDouble()
        let c = try client(device, vault, http), owner = try await c.registeredInstallation()
        await http.options(status: 503)
        let result = try await c.send(request(), installation: owner, requestID: UUID(), allowSending: { true })
        XCTAssertEqual(result.1.statusCode, 503)
        let calls = await http.snapshot(); XCTAssertEqual(calls.1.count, 1)
    }
    func testInstallMarkerPersistsAndChangesForNewContainer() throws {
        let base = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: base) }
        let first = try LiteAttestInstallMarker.loadOrCreate(in: base.appendingPathComponent("one"))
        XCTAssertEqual(first, try LiteAttestInstallMarker.loadOrCreate(in: base.appendingPathComponent("one")))
        XCTAssertNotEqual(first, try LiteAttestInstallMarker.loadOrCreate(in: base.appendingPathComponent("two")))
        let file = base.appendingPathComponent("one/app-attest-install-v1")
        XCTAssertEqual(try file.resourceValues(forKeys: [.isExcludedFromBackupKey]).isExcludedFromBackup, true)
        try Data("broken".utf8).write(to: file)
        XCTAssertThrowsError(try LiteAttestInstallMarker.loadOrCreate(in: base.appendingPathComponent("one")))
    }
    func testActualKeychainRoundTripInUniqueTestScope() async throws {
        let vault = LiteAttestKeychain(scope: "test-" + UUID().uuidString)
        do {
            let empty = try await vault.load(); XCTAssertNil(empty)
            try await vault.save(Data("synthetic-key-id".utf8))
            let first = try await vault.load(); XCTAssertEqual(first, Data("synthetic-key-id".utf8))
            try await vault.save(Data("updated".utf8))
            let second = try await vault.load(); XCTAssertEqual(second, Data("updated".utf8))
            try await vault.remove(); let removed = try await vault.load(); XCTAssertNil(removed)
        } catch { try? await vault.remove(); throw error }
    }
    func testInterruptedAppleCallStartsNewKeyBeforeAnyRegistration() async throws {
        let device = AttestDeviceDouble(), vault = AttestMemory(), http = AttestHTTPDouble()
        let old = LiteAttestClient.Identity(origin: origin.absoluteString, audience: "lite-test",
            key: Data(repeating: 3, count: 32).base64EncodedString(), phase: "attesting")
        try await vault.save(JSONEncoder().encode(old))
        _ = try await client(device, vault, http).registeredInstallation()
        let counts = await device.counts(), calls = await http.snapshot()
        XCTAssertEqual(counts.0, 1); XCTAssertEqual(counts.1, 1); XCTAssertEqual(calls.0.count, 1)
    }
    func testSharedRegistryNormalizesOriginWithoutNetworkOrAppleCalls() async throws {
        let registry = LiteAttestClients()
        let first = try await registry.client(origin: URL(string: "https://EXAMPLE.invalid:443/")!, audience: "lite-test")
        let same = try await registry.client(origin: URL(string: "https://example.invalid")!, audience: "lite-test")
        XCTAssertTrue(first === same)
        let other = try await registry.client(origin: URL(string: "https://example.invalid")!, audience: "another")
        XCTAssertFalse(first === other)
        do { _ = try await registry.client(origin: URL(string: "http://example.invalid")!, audience: "lite-test"); XCTFail() } catch { }
    }

}
