import XCTest
import CryptoKit
@testable import WoundLite

// Explicit opt-in, device-only diagnostics. Reads this app's existing pending
// attestation without exporting a key ID, receipt, proof or nonce, and never
// generates keys, sends network requests, or changes the persisted identity.
#if WOUNDAI_ATTEST_READONLY_DIAGNOSTIC && !targetEnvironment(simulator)
final class LiteAttestReadOnlyDeviceDiagnostic: XCTestCase {
    private struct CBOR {
        let bytes: [UInt8]
        var offset = 0
        mutating func read(_ depth: Int = 0) throws -> Any {
            guard depth < 12, offset < bytes.count else { throw LiteAttestFailure.invalidState }
            let first = bytes[offset]; offset += 1
            let major = first >> 5, info = first & 31
            var n = Int(info)
            if info >= 24 {
                guard info <= 26 else { throw LiteAttestFailure.invalidState }
                let width = 1 << Int(info - 24)
                guard width <= bytes.count - offset else { throw LiteAttestFailure.invalidState }
                n = 0
                for _ in 0..<width { n = (n << 8) | Int(bytes[offset]); offset += 1 }
            }
            guard n <= 100_000 else { throw LiteAttestFailure.invalidState }
            switch major {
            case 0: return n
            case 1: return -1 - n
            case 2, 3:
                guard n <= bytes.count - offset else { throw LiteAttestFailure.invalidState }
                let data = Data(bytes[offset..<offset+n]); offset += n
                if major == 2 { return data }
                guard let string = String(data: data, encoding: .utf8) else { throw LiteAttestFailure.invalidState }
                return string
            case 4:
                guard n <= 32 else { throw LiteAttestFailure.invalidState }
                return try (0..<n).map { _ in try read(depth+1) }
            case 5:
                guard n <= 32 else { throw LiteAttestFailure.invalidState }
                var map: [AnyHashable: Any] = [:]
                for _ in 0..<n {
                    guard let key = try read(depth+1) as? AnyHashable, map[key] == nil else { throw LiteAttestFailure.invalidState }
                    map[key] = try read(depth+1)
                }
                return map
            default: throw LiteAttestFailure.invalidState
            }
        }
    }

    func testReadExistingPendingProofMetadataWithoutMutation() async throws {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        let marker = try String(contentsOf: base.appendingPathComponent("app-attest/app-attest-install-v1"), encoding: .utf8)
        XCTAssertTrue(LiteAttestClient.matches(marker, "[0-9a-f]{32}"))
        let origin = "https://woundai-lite-candidate-421209514056.asia-east1.run.app"
        let binding = origin + "\0woundlite-research-v1\0" + marker
        let scope = SHA256.hash(data: Data(binding.utf8)).map { String(format: "%02x", $0) }.joined()
        let vault = LiteAttestKeychain(scope: scope)
        let loaded = try await vault.load()
        let before = try XCTUnwrap(loaded)
        let identity = try JSONDecoder().decode(LiteAttestClient.Identity.self, from: before)
        XCTAssertEqual(identity.phase, "pending")
        let proof = try XCTUnwrap(identity.attestation)
        var outer = CBOR(bytes: Array(proof))
        let map = try XCTUnwrap(try outer.read() as? [AnyHashable: Any])
        XCTAssertEqual(outer.offset, proof.count)
        let auth = try XCTUnwrap(map["authData"] as? Data)
        XCTAssertGreaterThan(auth.count, 87)
        let credentialSize = Int(auth[53]) << 8 | Int(auth[54])
        XCTAssertEqual(credentialSize, 32)
        var tail = CBOR(bytes: Array(auth.dropFirst(55 + credentialSize)))
        _ = try tail.read() // credential public key, deliberately not exported
        let ext = try XCTUnwrap(try tail.read() as? [AnyHashable: Any])
        let categoryBytes = try XCTUnwrap(ext["apple_validation_category_01"] as? Data)
        XCTAssertEqual(categoryBytes.count, 4)
        let category = categoryBytes.enumerated().reduce(UInt32(0)) { $0 | UInt32($1.element) << (8 * $1.offset) }
        let version = try XCTUnwrap(ext["apple_bundle_version_01"] as? String)
        let production = Data(auth[37..<53]) == Data("appattest".utf8) + Data(repeating: 0, count: 7)
        let after = try await vault.load()
        XCTAssertEqual(before, after)
        let summary: [String: Any] = ["phase": identity.phase, "production_aaguid": production,
            "observed_launch_category": category, "observed_bundle_version": version,
            "proof_bytes": proof.count, "identity_unchanged": before == after,
            "signature_verified_by_this_diagnostic": false, "network_requests": 0,
            "private_key_access": false, "authentication_material_exported": false]
        let data = try JSONSerialization.data(withJSONObject: summary, options: [.sortedKeys, .prettyPrinted])
        let attachment = XCTAttachment(data: data, uniformTypeIdentifier: "public.json")
        attachment.name = "lite-attest-safe-summary"; attachment.lifetime = .keepAlways; add(attachment)
        try data.write(to: base.appendingPathComponent("lite-attest-safe-summary.json"), options: [.atomic, .completeFileProtection])
    }
}
#endif

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
    func generateKey() -> String {
        generated += 1
        return generated == 1 ? key : Data(repeating: UInt8(7 + generated), count: 32).base64EncodedString()
    }
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
    func recoveryOptions(rejectChallenge: Bool = false, fixedID: String? = nil, deny: AttestPermission? = nil) {
        self.rejectChallenge = rejectChallenge; fixedChallengeID = fixedID; denyPermissionAfterChallenge = deny
    }
    var challenges = 0
    var failRegistration = false
    var rejectRegistration = false
    var rejectChallenge = false
    var omitRecoveryLineage = false
    func omitLineage() { omitRecoveryLineage = true }
    var wrongRecoveryLineage = false
    func wrongLineage() { wrongRecoveryLineage = true }
    var fixedChallengeID: String?
    var denyPermissionAfterChallenge: AttestPermission?
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
            if let fixedChallengeID { json["challenge_id"] = fixedChallengeID }
            if body["purpose"] == "recover_register", !omitRecoveryLineage {
                json["replaces_challenge_id"] = wrongRecoveryLineage ? String(repeating: "f", count: 32) : body["previous_challenge_id"]
            }
            if rejectChallenge { status = 401 }
            if challenges > 1 { await denyPermissionAfterChallenge?.deny() }
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
    func testDeletionPostsUsePrivacyChallengesButOrdinaryAccountPostsDoNot() async throws {
        let device=AttestDeviceDouble(), vault=AttestMemory(), http=AttestHTTPDouble()
        let c=try client(device,vault,http), owner=try await c.registeredInstallation()
        for suffix in ["deletion/request", "deletion/status", "deletion/reauth/challenge", "deletion/reauth/confirm", "status"] {
            var r=request("/api/v1/lite/account/"+suffix,body:Data("{}".utf8))
            r.setValue("application/json",forHTTPHeaderField:"Content-Type")
            _=try await c.send(r,installation:owner,requestID:UUID(),allowSending:{true})
        }
        let purposes=await http.purposes()
        XCTAssertEqual(purposes,["register","withdraw","withdraw","withdraw","withdraw","assert"])
        let calls=await http.snapshot();XCTAssertEqual(calls.1.count,5)
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

    private func rejectedPending() async throws -> (LiteAttestClient, AttestDeviceDouble, AttestMemory, AttestHTTPDouble) {
        let device = AttestDeviceDouble(), vault = AttestMemory(), http = AttestHTTPDouble()
        await http.options(rejectRegistration: true)
        let c = try client(device, vault, http)
        do { _ = try await c.registeredInstallation(); XCTFail() }
        catch { XCTAssertEqual(error as? LiteAttestFailure, .registrationRejected) }
        return (c, device, vault, http)
    }

    func testExplicitRecoveryArchivesExactProofAndPreservesAuditAfterRegistration() async throws {
        let (c, device, vault, http) = try await rejectedPending()
        let beforeData = await vault.load()
        let before = try JSONDecoder().decode(LiteAttestClient.Identity.self, from: XCTUnwrap(beforeData))
        _ = try await c.recoverUnregisteredIdentity(allowRecovery: { true })
        let afterData = await vault.load()
        let fresh = try JSONDecoder().decode(LiteAttestClient.Identity.self, from: XCTUnwrap(afterData))
        XCTAssertEqual(fresh.phase, "generated"); XCTAssertNil(fresh.installation)
        XCTAssertNotEqual(fresh.key, before.key)
        let audit = try XCTUnwrap(fresh.registrationRecoveries?.first)
        XCTAssertEqual(audit.oldKey, before.key); XCTAssertEqual(audit.oldAttestation, before.attestation)
        XCTAssertEqual(audit.oldChallengeID, before.challengeID)
        XCTAssertNotEqual(audit.invalidatingChallengeID, audit.oldChallengeID)
        let calls = await http.snapshot(); XCTAssertEqual(calls.0.count, 2)
        XCTAssertEqual(calls.0[0], calls.0[1]); XCTAssertTrue(calls.1.isEmpty)
        let counts = await device.counts(); XCTAssertEqual(counts.0, 2); XCTAssertEqual(counts.1, 1)
        await http.options()
        _ = try await c.registeredInstallation()
        let registeredData = await vault.load()
        let registered = try JSONDecoder().decode(LiteAttestClient.Identity.self, from: XCTUnwrap(registeredData))
        XCTAssertEqual(registered.phase, "registered"); XCTAssertEqual(registered.registrationRecoveries, fresh.registrationRecoveries)
        // Decoder/stored validation accepts the persisted audit after restart.
        let restored = try await client(device, vault, http).existingInstallation()
        XCTAssertEqual(restored, registered.installation)
    }

    func testExplicitRecoveryRecoversLostAcknowledgementWithoutNewKey() async throws {
        let (c, device, vault, http) = try await rejectedPending()
        await http.options()
        let result = try await c.recoverUnregisteredIdentity(allowRecovery: { true })
        if case .restoredExistingRegistration = result {} else { XCTFail() }
        let counts = await device.counts(); XCTAssertEqual(counts.0, 1)
        let calls = await http.snapshot(); XCTAssertEqual(calls.2, 1)
        let data = await vault.load()
        let identity = try JSONDecoder().decode(LiteAttestClient.Identity.self, from: XCTUnwrap(data))
        XCTAssertEqual(identity.phase, "registered"); XCTAssertNil(identity.registrationRecoveries)
    }

    func testRecoveryDeniedForBoundRecordsOrWithdrawalDoesNotContactServer() async throws {
        let (c, device, vault, http) = try await rejectedPending()
        let before = await vault.load(), callsBefore = await http.snapshot()
        do { _ = try await c.recoverUnregisteredIdentity(allowRecovery: { false }); XCTFail() }
        catch { XCTAssertEqual(error as? LiteAttestFailure, .registrationRecoveryNotAllowed) }
        let after = await vault.load(), callsAfter = await http.snapshot(), counts = await device.counts()
        XCTAssertEqual(before, after); XCTAssertEqual(callsBefore.0.count, callsAfter.0.count); XCTAssertEqual(counts.0, 1)
    }

    func testRecoveryRejectsUnexpiredSameChallengeWithoutRotating() async throws {
        let (c, device, vault, http) = try await rejectedPending()
        await http.recoveryOptions(fixedID: String(format: "%032x", 1))
        let before = await vault.load()
        do { _ = try await c.recoverUnregisteredIdentity(allowRecovery: { true }); XCTFail() }
        catch { XCTAssertEqual(error as? LiteAttestFailure, .registrationRecoveryNotReady) }
        let after = await vault.load(), counts = await device.counts()
        XCTAssertEqual(before, after); XCTAssertEqual(counts.0, 1)
    }

    func testRecoveryRejectedChallengeNeverReplacesActiveRevokedOrUnknownIdentity() async throws {
        let (c, device, vault, http) = try await rejectedPending()
        await http.recoveryOptions(rejectChallenge: true)
        let before = await vault.load()
        do { _ = try await c.recoverUnregisteredIdentity(allowRecovery: { true }); XCTFail() }
        catch { XCTAssertEqual(error as? LiteAttestFailure, .registrationRejected) }
        let after = await vault.load(), counts = await device.counts()
        XCTAssertEqual(before, after); XCTAssertEqual(counts.0, 1)
    }

    func testRecoveryMissingOrWrongServerLineageNeverRotatesKey() async throws {
        for wrong in [false, true] {
            let (c, device, vault, http) = try await rejectedPending()
            let before = await vault.load()
            if wrong { await http.wrongLineage() } else { await http.omitLineage() }
            do { _ = try await c.recoverUnregisteredIdentity(allowRecovery: { true }); XCTFail() }
            catch { XCTAssertEqual(error as? LiteAttestFailure, .invalidResponse) }
            let after = await vault.load(), counts = await device.counts(), purposes = await http.purposes()
            XCTAssertEqual(before, after); XCTAssertEqual(counts.0, 1)
            XCTAssertEqual(purposes, ["register", "recover_register"])
        }
    }
    func testRecoveryTimeoutRetainsExactPendingIdentity() async throws {
        let (c, device, vault, http) = try await rejectedPending()
        await http.options(failRegistration: true)
        let before = await vault.load()
        do { _ = try await c.recoverUnregisteredIdentity(allowRecovery: { true }); XCTFail() } catch { }
        let after = await vault.load(), counts = await device.counts()
        XCTAssertEqual(before, after); XCTAssertEqual(counts.0, 1)
    }

    func testRecoveryRechecksConsentAfterNetworkBeforeGeneratingKey() async throws {
        let (c, device, vault, http) = try await rejectedPending()
        let permission = AttestPermission()
        await http.recoveryOptions(deny: permission)
        let before = await vault.load()
        do { _ = try await c.recoverUnregisteredIdentity(allowRecovery: { await permission.read() }); XCTFail() }
        catch { XCTAssertEqual(error as? LiteAttestFailure, .registrationRecoveryNotAllowed) }
        let after = await vault.load(), counts = await device.counts()
        XCTAssertEqual(before, after); XCTAssertEqual(counts.0, 1)
    }

    func testRecoveryWriteFailurePreservesOldProof() async throws {
        let (c, _, vault, _) = try await rejectedPending()
        await vault.fail(at: 4)
        let before = await vault.load()
        do { _ = try await c.recoverUnregisteredIdentity(allowRecovery: { true }); XCTFail() } catch { }
        let after = await vault.load(); XCTAssertEqual(before, after)
    }

    func testRegisteredIdentityCannotBeResetByRecovery() async throws {
        let device = AttestDeviceDouble(), vault = AttestMemory(), http = AttestHTTPDouble()
        let c = try client(device, vault, http)
        _ = try await c.registeredInstallation()
        let before = await vault.load()
        do { _ = try await c.recoverUnregisteredIdentity(allowRecovery: { true }); XCTFail() }
        catch { XCTAssertEqual(error as? LiteAttestFailure, .registrationRecoveryNotAllowed) }
        let after = await vault.load(), counts = await device.counts()
        XCTAssertEqual(before, after); XCTAssertEqual(counts.0, 1)
    }

    func testRecoveryAuditSurvivesInterruptedAppleAttestationAndRestart() async throws {
        let (c, device, vault, http) = try await rejectedPending()
        _ = try await c.recoverUnregisteredIdentity(allowRecovery: { true })
        let bytes = await vault.load()
        var interrupted = try JSONDecoder().decode(LiteAttestClient.Identity.self, from: XCTUnwrap(bytes))
        interrupted.phase = "attesting"
        try await vault.save(JSONEncoder().encode(interrupted))
        await http.options()
        _ = try await client(device, vault, http).registeredInstallation()
        let finalData = await vault.load()
        let final = try JSONDecoder().decode(LiteAttestClient.Identity.self, from: XCTUnwrap(finalData))
        XCTAssertEqual(final.phase, "registered")
        XCTAssertEqual(final.registrationRecoveries, interrupted.registrationRecoveries)
        XCTAssertNotEqual(final.key, interrupted.key)
    }

    func testInterruptedReplacementWriteFailureKeepsAuditAtomically() async throws {
        let (c, device, vault, http) = try await rejectedPending()
        _ = try await c.recoverUnregisteredIdentity(allowRecovery: { true })
        let bytes = await vault.load()
        var interrupted = try JSONDecoder().decode(LiteAttestClient.Identity.self, from: XCTUnwrap(bytes))
        interrupted.phase = "attesting"
        try await vault.save(JSONEncoder().encode(interrupted))
        let before = await vault.load()
        await vault.fail(at: 6)
        do { _ = try await client(device, vault, http).registeredInstallation(); XCTFail() } catch { }
        let after = await vault.load(); XCTAssertEqual(before, after)
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
