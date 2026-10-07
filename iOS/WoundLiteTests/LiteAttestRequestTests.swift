import XCTest
import CryptoKit
@testable import WoundLite

final class LiteAttestRequestTests: XCTestCase {
    private func fixture() throws -> [[String:String]] {
        let url = try XCTUnwrap(Bundle(for:Self.self).url(forResource:"lite_attest_request",withExtension:"json"))
        return try XCTUnwrap(JSONSerialization.jsonObject(with:Data(contentsOf:url)) as? [[String:String]])
    }
    private func encode(_ f:[String:String]) throws -> Data {
        try LiteAttestRequest.clientData(audience:f["audience"]!,installation:f["installation"]!,
            keyID:try XCTUnwrap(Data(base64Encoded:f["key_id"]!)),challenge:try XCTUnwrap(Data(base64Encoded:f["challenge"]!)),
            requestID:f["request_id"]!,method:f["method"]!,path:f["path"]!,contentType:f["content_type"]!,
            body:try XCTUnwrap(Data(base64Encoded:f["body"]!)))
    }
    func testPythonFixturesMatchExactNativeBytesIncludingBinaryAndDelete() throws {
        let cases = try fixture();XCTAssertEqual(cases.count,3)
        for f in cases {
            let data = try encode(f)
            XCTAssertEqual(data.base64EncodedString(),f["expected_client_data"])
            XCTAssertEqual(SHA256.hash(data:data).map { String(format:"%02x",$0) }.joined(),f["expected_sha256"])
        }
    }
    func testEachIdentityChallengeAndRequestFieldChangesBinding() throws {
        let base = try XCTUnwrap(fixture().first);let original = try encode(base)
        let changes = ["audience":"woundlite-staging","installation":"another-install", "key_id":Data(repeating:1,count:32).base64EncodedString(),
                       "challenge":Data(repeating:2,count:32).base64EncodedString(),"request_id":"00000000-0000-0000-0000-000000000002",
                       "path":"/api/v1/lite/segment","content_type":"application/json; charset=utf-8","body":Data("{}".utf8).base64EncodedString()]
        for (field,value) in changes {
            var changed = base;changed[field] = value
            XCTAssertNotEqual(try encode(changed),original,field)
        }
    }
    func testRejectsCrossOwnerDeleteAndNoncanonicalPaths() throws {
        let cases = try fixture();var deletion = cases[2]
        deletion["path"] = "/api/v1/lite/data/other"
        XCTAssertThrowsError(try encode(deletion))
        deletion = cases[2];deletion["body"] = Data("{}".utf8).base64EncodedString()
        XCTAssertThrowsError(try encode(deletion))
        for path in ["/api/v1/lite/segment?x=1","/api/v1/lite/segment/","/api/v1/lite/%73egment","/api/v1/lite/../segment"] {
            var changed = cases[0];changed["path"] = path
            XCTAssertThrowsError(try encode(changed),path)
        }
    }
    func testRejectsControlCharactersAndWrongChallengeSizes() throws {
        let base = try XCTUnwrap(fixture().first)
        for (field,value) in ["installation":"synthetic-install\n","request_id":"00000000-0000-0000-0000-000000000001\n",
                              "content_type":"application/json\r\nInjected:yes","audience":"production\0staging",
                              "challenge":Data(repeating:1,count:31).base64EncodedString(),"key_id":Data(repeating:2,count:33).base64EncodedString()] {
            var changed = base;changed[field] = value
            XCTAssertThrowsError(try encode(changed),field)
        }
    }
}
