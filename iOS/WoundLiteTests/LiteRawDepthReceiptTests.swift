import XCTest
@testable import WoundLite

final class LiteRawDepthReceiptTests: XCTestCase {
    private func fixture() throws -> (LiteRawDepthPacket, [String:Any]) {
        let url = try XCTUnwrap(Bundle(for:Self.self).url(forResource:"lite_raw_receipt",withExtension:"json"))
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with:Data(contentsOf:url)) as? [String:Any])
        func bytes(_ key:String) throws -> Data {
            try XCTUnwrap(Data(base64Encoded:try XCTUnwrap(object[key] as? String)))
        }
        let packet = try LiteRawDepthPacket(jpeg:bytes("jpeg"), metadata:bytes("metadata"), rawDepth:bytes("rawDepth"))
        return (packet,try XCTUnwrap(object["receipt"] as? [String:Any]))
    }
    private func verify(_ packet:LiteRawDepthPacket,_ receipt:[String:Any]) throws -> Bool {
        packet.verifiesReceipt(try JSONSerialization.data(withJSONObject:receipt),installationID:"synthetic-install",captureID:"native-swift-frame")
    }
    func testRealPythonPersistenceReceiptMatchesExactSwiftPacket() throws {
        let (p,r) = try fixture();XCTAssertTrue(try verify(p,r))
    }
    func testEachScopeDigestAndStatusFieldIsRequired() throws {
        let (p,r) = try fixture()
        for field in ["rgb_sha256","depth_sha256","metadata_sha256","bundle_sha256","installation_id","capture_id","schema","status","registration","training_admission"] {
            var changed = r;changed[field] = "wrong"
            XCTAssertFalse(try verify(p,changed),field)
            changed = r;changed.removeValue(forKey:field)
            XCTAssertFalse(try verify(p,changed),"missing " + field)
        }
        var changed = r;changed["bundle_bytes"] = 1
        XCTAssertFalse(try verify(p,changed))
        changed["bundle_bytes"] = true
        XCTAssertFalse(try verify(p,changed))
    }
    func testModifiedLocalMetadataAndDepthCannotAcceptOldReceipt() throws {
        let (p,r) = try fixture()
        let metadataChanged = LiteRawDepthPacket(jpeg:p.jpeg,metadata:p.metadata + Data(" ".utf8),rawDepth:p.rawDepth)
        XCTAssertFalse(try verify(metadataChanged,r))
        var raw = p.rawDepth;raw[0] ^= 1
        XCTAssertFalse(try verify(LiteRawDepthPacket(jpeg:p.jpeg,metadata:p.metadata,rawDepth:raw),r))
        let data = try JSONSerialization.data(withJSONObject:r)
        XCTAssertFalse(p.verifiesReceipt(data,installationID:"other-installation",captureID:"native-swift-frame"))
        XCTAssertFalse(p.verifiesReceipt(data,installationID:"synthetic-install",captureID:"other-capture"))
    }
    func testReceiptRequiresSubmittedPacketBeforeBecomingStorageEvidence() throws {
        let (packet, proof) = try fixture()
        let raw: [String: Any] = ["schema_version": 1, "image": "stored", "metadata": "stored",
            "depth": "stored", "validity_mask": "not_provided", "rgbd_validation": "not_performed", "raw_depth_receipt": proof]
        XCTAssertNil(LiteStorageReceipt.parse(raw)?.rawDepthReceipt)
        XCTAssertNil(LiteStorageReceipt.parse(raw, rawPacket: packet, installationID: "other", captureID: "native-swift-frame")?.rawDepthReceipt)
        XCTAssertNotNil(LiteStorageReceipt.parse(raw, rawPacket: packet, installationID: "synthetic-install", captureID: "native-swift-frame")?.rawDepthReceipt)
    }

    func testMultipartBusinessFieldsCannotReplaceAssetsOrInjectBoundary() throws {
        let (packet, _) = try fixture()
        let boundary = "----------------native-fixture"
        XCTAssertThrowsError(try packet.multipart(boundary: boundary, fields: ["raw_depth_metadata": "replace"]))
        XCTAssertThrowsError(try packet.multipart(boundary: boundary, fields: ["anon_id": "--" + boundary]))
        let body = try packet.multipart(boundary: boundary, fields: ["anon_id": "owner", "research_consent": "true"])
        XCTAssertNotNil(body.range(of: packet.rawDepth)); XCTAssertNotNil(body.range(of: Data("owner".utf8)))
    }

}
