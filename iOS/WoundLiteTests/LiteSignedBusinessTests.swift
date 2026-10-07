import XCTest
import CryptoKit
@testable import WoundLite

final class LiteSignedBusinessTests: XCTestCase {
    private let origin = URL(string: "https://lite.invalid")!
    private func setup(_ permission: AttestPermission = AttestPermission()) throws
        -> (BackendClient, LiteAttestClient, AttestDeviceDouble, AttestHTTPDouble) {
        let device = AttestDeviceDouble(), http = AttestHTTPDouble()
        let signer = try LiteAttestClient(origin: origin, audience: "lite-test", device: device,
            persistence: AttestMemory(), transport: http, now: { Date(timeIntervalSince1970: 1000) })
        return (BackendClient(baseUrl: origin.absoluteString,
                liteTransport: LiteSignedCloudTransport(client: signer, consent: { await permission.read() })),
                signer, device, http)
    }

    private func rawFixture() throws -> (LiteRawDepthPacket, [String: Any]) {
        let url = try XCTUnwrap(Bundle(for: Self.self).url(forResource: "lite_raw_receipt", withExtension: "json"))
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(contentsOf: url)) as? [String: Any])
        func bytes(_ name: String) throws -> Data {
            try XCTUnwrap(Data(base64Encoded: XCTUnwrap(object[name] as? String)))
        }
        return (try LiteRawDepthPacket(jpeg: bytes("jpeg"), metadata: bytes("metadata"), rawDepth: bytes("rawDepth")),
                try XCTUnwrap(object["receipt"] as? [String: Any]))
    }

    func testRawDepthBusinessRequestSignsBinaryAndPersistsVerifiedReceipt() async throws {
        let (backend, _, device, http) = try setup()
        let owner = try await backend.liteInstallation()
        var (packet, proof) = try rawFixture()
        proof["installation_id"] = owner
        let iid = try XCTUnwrap(proof["capture_id"] as? String)
        let receipt: [String: Any] = ["schema_version": 1, "image": "stored", "metadata": "stored",
            "depth": "stored", "validity_mask": "not_provided", "rgbd_validation": "not_performed", "raw_depth_receipt": proof]
        await http.businessReply(["image_id": iid, "stored": true, "image_w": 64, "image_h": 64,
                                 "storage_receipt": receipt])
        let result = try await backend.liteSegment(jpeg: packet.jpeg, anonId: owner,
            consentVersion: "2026-10-04.1", rawCapture: packet)
        XCTAssertNotNil(result.storageReceipt?.rawDepthReceipt)
        XCTAssertTrue(LiteStorageReceipt.message(imageStored: true, receipt: result.storageReceipt)!.contains("資料雜湊已核對"))
        let binding = LiteCloudBinding(server: origin.absoluteString, anonID: owner, imageID: iid,
            imageW: 64, imageH: 64, storageReceipt: result.storageReceipt)
        XCTAssertEqual(try JSONDecoder().decode(LiteCloudBinding.self, from: JSONEncoder().encode(binding)), binding)
        let snapshot = await http.snapshot(), request = try XCTUnwrap(snapshot.1.first)
        let body = try XCTUnwrap(request.httpBody)
        for part in [packet.jpeg, packet.metadata, packet.rawDepth] { XCTAssertNotNil(body.range(of: part)) }
        XCTAssertNil(body.range(of: Data("depth_map_png".utf8)))
        XCTAssertNil(body.range(of: Data("depth_conf_png".utf8)))
        let clientData = try LiteAttestRequest.clientData(audience: "lite-test", installation: owner,
            keyID: Data(base64Encoded: device.key)!, challenge: snapshot.4,
            requestID: XCTUnwrap(request.value(forHTTPHeaderField: "X-Lite-Request-ID")),
            method: "POST", path: "/api/v1/lite/segment",
            contentType: XCTUnwrap(request.value(forHTTPHeaderField: "Content-Type")), body: body)
        let hashes = await device.hashes()
        XCTAssertEqual(hashes.last, Data(SHA256.hash(data: clientData)))
    }

    func testUnmatchedRawProofCannotClaimVerifiedStorage() async throws {
        let (backend, _, _, http) = try setup()
        let owner = try await backend.liteInstallation()
        var (packet, proof) = try rawFixture(); proof["installation_id"] = owner
        proof["depth_sha256"] = String(repeating: "0", count: 64)
        let receipt: [String: Any] = ["schema_version": 1, "image": "stored", "metadata": "stored",
            "depth": "stored", "validity_mask": "not_provided", "rgbd_validation": "not_performed", "raw_depth_receipt": proof]
        await http.businessReply(["image_id": proof["capture_id"]!, "stored": true, "storage_receipt": receipt])
        let result = try await backend.liteSegment(jpeg: packet.jpeg, anonId: owner, rawCapture: packet)
        XCTAssertTrue(result.stored); XCTAssertNil(result.storageReceipt?.rawDepthReceipt)
        XCTAssertFalse(LiteStorageReceipt.message(imageStored: true, receipt: result.storageReceipt)!.contains("資料雜湊已核對"))
    }

    func testRawRequestRejectsDifferentJpegOrConflictingLegacyDepthBeforeSending() async throws {
        let (backend, _, _, http) = try setup()
        let owner = try await backend.liteInstallation(), (packet, _) = try rawFixture()
        do { _ = try await backend.liteSegment(jpeg: packet.jpeg + Data([0]), anonId: owner, rawCapture: packet); XCTFail() }
        catch { XCTAssertTrue(error is LiteRawDepthPacket.Failure) }
        do { _ = try await backend.liteSegment(jpeg: packet.jpeg, anonId: owner, depthMapPngBase64: "old", rawCapture: packet); XCTFail() }
        catch { XCTAssertTrue(error is LiteRawDepthPacket.Failure) }
        let requests = await http.snapshot(); XCTAssertTrue(requests.1.isEmpty)
    }

    func testSegmentSignsActualMultipartWithServerOwnerAndExactMediaBytes() async throws {
        let (backend, _, device, http) = try setup()
        let owner = try await backend.liteInstallation()
        let receipt: [String: Any] = ["schema_version": 1, "image": "stored", "metadata": "stored",
            "depth": "stored", "validity_mask": "stored", "rgbd_validation": "not_performed"]
        await http.businessReply(["image_id": "image", "stored": true, "image_w": 64, "image_h": 48,
                                 "wound_polygons": [[[1,1],[20,1],[1,20]]], "storage_receipt": receipt])
        let jpeg = Data([0, 1, 255, 254, 0, 7])
        let result = try await backend.liteSegment(jpeg: jpeg, anonId: owner, consentVersion: "v1",
            depthMapPngBase64: "depth-bytes", depthConfPngBase64: "validity-bytes",
            cameraIntrinsics: ["fx": 100], measured: ["surface_cm2": 1.5])
        XCTAssertEqual(result.imageId, "image"); XCTAssertTrue(result.stored)
        XCTAssertNotNil(result.storageReceipt); XCTAssertEqual(result.polygons.count, 1)
        let snapshot = await http.snapshot(), request = try XCTUnwrap(snapshot.1.first)
        let body = try XCTUnwrap(request.httpBody)
        XCTAssertNotNil(body.range(of: jpeg))
        for token in [owner, "depth-bytes", "validity-bytes", "consent_version", "surface_cm2", "camera_intrinsics"] {
            XCTAssertNotNil(body.range(of: Data(token.utf8)), token)
        }
        XCTAssertNil(request.value(forHTTPHeaderField: "Authorization"))
        XCTAssertEqual(request.value(forHTTPHeaderField: "Content-Length"), String(body.count))
        let key = device.key
        let clientData = try LiteAttestRequest.clientData(audience: "lite-test", installation: owner,
            keyID: Data(base64Encoded: key)!, challenge: snapshot.4,
            requestID: XCTUnwrap(request.value(forHTTPHeaderField: "X-Lite-Request-ID")),
            method: "POST", path: "/api/v1/lite/segment",
            contentType: XCTUnwrap(request.value(forHTTPHeaderField: "Content-Type")), body: body)
        let hashes = await device.hashes()
        XCTAssertEqual(hashes.last, Data(SHA256.hash(data: clientData)))
        XCTAssertNotNil(request.value(forHTTPHeaderField: "X-Lite-Assertion"))
    }

    func testRevisionUsesOriginalOwnerAndRequiresDigestReceiptThroughSigner() async throws {
        let (backend, _, _, http) = try setup(); let owner = try await backend.liteInstallation()
        let payload = "{\"surface_cm2\":11.22}", digest = LitePendingRevision.digest(payload)
        await http.businessReply(["status": "stored", "image_id": "image", "revision": 2, "payload_sha256": digest])
        try await backend.liteRevision(bindingAnonID: owner, imageID: "image", revision: 2, payloadJSON: payload, digest: digest)
        let first = await http.snapshot(); let request = try XCTUnwrap(first.1.first)
        XCTAssertEqual(request.url?.path, "/api/v1/lite/annotation/revision")
        XCTAssertNotNil(request.value(forHTTPHeaderField: "X-Lite-Assertion"))
        let body = try JSONSerialization.jsonObject(with: XCTUnwrap(request.httpBody)) as! [String: Any]
        XCTAssertEqual(body["payload_json"] as? String, payload); XCTAssertEqual(body["anon_id"] as? String, owner)
        await http.businessReply(["status": "stored", "image_id": "image", "revision": 2, "payload_sha256": "wrong"])
        do { try await backend.liteRevision(bindingAnonID: owner, imageID: "image", revision: 2, payloadJSON: payload, digest: digest); XCTFail() }
        catch is BackendError { }
        let second = await http.snapshot()
        XCTAssertEqual(second.1.count, 2)
        XCTAssertNotEqual(second.1[0].value(forHTTPHeaderField: "X-Lite-Request-ID"), second.1[1].value(forHTTPHeaderField: "X-Lite-Request-ID"))
    }

    func testOldOwnerRejectedWithoutNetworkOrReplacementEnrollment() async throws {
        let (backend, _, device, http) = try setup()
        for registerFirst in [false, true] {
            if registerFirst { _ = try await backend.liteInstallation() }
            let before = await http.snapshot()
            do { try await backend.liteRevision(bindingAnonID: "legacy-uuid", imageID: "old", revision: 1, payloadJSON: "{}", digest: "x"); XCTFail() }
            catch { XCTAssertEqual(error as? LiteAttestFailure, .identityMismatch) }
            let after = await http.snapshot()
            XCTAssertEqual(before.2, after.2); XCTAssertEqual(before.0.count, after.0.count); XCTAssertTrue(after.1.isEmpty)
        }
        let counts = await device.counts(); XCTAssertEqual(counts.0, 1)
    }

    func testConsentOffDoesNotGenerateKeyOrContactServer() async throws {
        let permission = AttestPermission(); await permission.deny()
        let (backend, _, device, http) = try setup(permission)
        do { _ = try await backend.liteInstallation(); XCTFail() }
        catch { XCTAssertEqual(error as? LiteAttestFailure, .consentRequired) }
        let counts = await device.counts(); XCTAssertEqual(counts.0, 0)
        let requests = await http.snapshot(); XCTAssertEqual(requests.2, 0)
    }

    func testConsentRevokedWhileSigningBlocksActualSegment() async throws {
        let permission = AttestPermission()
        let (backend, _, device, http) = try setup(permission); let owner = try await backend.liteInstallation()
        await device.options(revoke: permission)
        do { _ = try await backend.liteSegment(jpeg: Data([1]), anonId: owner); XCTFail() }
        catch { XCTAssertEqual(error as? LiteAttestFailure, .consentRequired) }
        let requests = await http.snapshot(); XCTAssertTrue(requests.1.isEmpty)
    }

    func testSegmentTimeoutHasNoUnsignedOrAutomaticRetry() async throws {
        let (backend, _, _, http) = try setup(); let owner = try await backend.liteInstallation()
        await http.businessReply([:], fail: true)
        do { _ = try await backend.liteSegment(jpeg: Data([1]), anonId: owner); XCTFail() }
        catch is URLError { }
        let requests = await http.snapshot(); XCTAssertEqual(requests.1.count, 1)
    }

    func testOldAnnotationAlsoRequiresAssertion() async throws {
        let (backend, _, _, http) = try setup(); let owner = try await backend.liteInstallation()
        try await backend.liteAnnotation(anonId: owner, imageId: "image", polygons: [[[1,1],[2,1],[1,2]]],
                                         imageW: 3, imageH: 3, source: "manual", consentVersion: "v1")
        let requests = await http.snapshot(); XCTAssertEqual(requests.1.count, 1)
        XCTAssertEqual(requests.1[0].url?.path, "/api/v1/lite/annotation")
        XCTAssertNotNil(requests.1[0].value(forHTTPHeaderField: "X-Lite-Assertion"))
    }

    func testPackagedAudienceIsExplicit() throws {
        XCTAssertEqual(Bundle.main.object(forInfoDictionaryKey: "LiteAttestAudience") as? String, "woundlite-research-v1")
    }
    func testRejectedEnrollmentDoesNotFallBackToLegacyAnonymousUpload() async throws {
        let (backend, _, device, http) = try setup()
        await http.options(rejectRegistration: true)
        do { _ = try await backend.liteInstallation(); XCTFail() }
        catch { XCTAssertEqual(error as? LiteAttestFailure, .registrationRejected) }
        do { _ = try await backend.liteSegment(jpeg: Data([1]), anonId: "legacy-uuid"); XCTFail() }
        catch { XCTAssertEqual(error as? LiteAttestFailure, .identityMismatch) }
        let calls = await http.snapshot(); XCTAssertEqual(calls.0.count, 1); XCTAssertTrue(calls.1.isEmpty)
        let counts = await device.counts(); XCTAssertEqual(counts.0, 1)
    }

}
