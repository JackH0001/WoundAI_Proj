import XCTest
@testable import WoundLite

private final class StorageReceiptProtocol: URLProtocol {
    static var response: [String: Any] = [:]
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: try! JSONSerialization.data(withJSONObject: Self.response))
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}

final class LiteStorageReceiptTests: XCTestCase {
    private var valid: [String: Any] {
        ["schema_version": 1, "image": "stored", "metadata": "stored", "depth": "stored",
         "validity_mask": "stored", "rgbd_validation": "not_performed"]
    }
    func testMissingUnknownAndMalformedReceiptsStayUnconfirmed() {
        XCTAssertNil(LiteStorageReceipt.parse(nil))
        for (key,value) in [("schema_version",true as Any),("schema_version",2 as Any),
                            ("depth","unknown" as Any),("rgbd_validation","passed" as Any)] {
            var raw=valid;raw[key]=value
            XCTAssertNil(LiteStorageReceipt.parse(raw),key)
        }
        var missing=valid;missing.removeValue(forKey:"validity_mask")
        XCTAssertNil(LiteStorageReceipt.parse(missing))
        XCTAssertTrue(LiteStorageReceipt.message(imageStored:true,receipt:nil)!.contains("尚未確認"))
        XCTAssertNil(LiteStorageReceipt.message(imageStored:false,receipt:LiteStorageReceipt.parse(valid)))
    }
    func testSavedAssetsNeverClaimGeometricValidation() throws {
        let receipt=try XCTUnwrap(LiteStorageReceipt.parse(valid))
        let message=try XCTUnwrap(LiteStorageReceipt.message(imageStored:true,receipt:receipt))
        XCTAssertTrue(message.contains("深度圖與有效值遮罩已保存"))
        XCTAssertTrue(message.contains("配準與精確度尚未驗證"))
    }
    func testRejectedAndMissingAssetsAreDistinctFromSaved() {
        for field in ["depth","validity_mask"] {
            var raw=valid;raw[field]="rejected"
            XCTAssertTrue(LiteStorageReceipt.message(imageStored:true,receipt:LiteStorageReceipt.parse(raw))!.contains("未通過後端檢查"))
        }
        var raw=valid;raw["depth"]="not_provided"
        XCTAssertTrue(LiteStorageReceipt.message(imageStored:true,receipt:LiteStorageReceipt.parse(raw))!.contains("未確認保存深度圖"))
        raw=valid;raw["validity_mask"]="not_provided"
        XCTAssertTrue(LiteStorageReceipt.message(imageStored:true,receipt:LiteStorageReceipt.parse(raw))!.contains("缺少有效值遮罩"))
    }
    func testBindingPersistsReceiptAndReadsLegacyWithoutInventingOne() throws {
        let legacy: [String:Any] = ["server":"https://example.invalid","anonID":"a","imageID":"i","imageW":64,"imageH":48]
        var binding=try JSONDecoder().decode(LiteCloudBinding.self,from:JSONSerialization.data(withJSONObject:legacy))
        XCTAssertNil(binding.storageReceipt)
        binding.storageReceipt=try XCTUnwrap(LiteStorageReceipt.parse(valid))
        let restored=try JSONDecoder().decode(LiteCloudBinding.self,from:JSONEncoder().encode(binding))
        XCTAssertEqual(binding,restored)
    }
    func testSegmentResponseActuallyParsesReceiptThroughTransport() async throws {
        let config=URLSessionConfiguration.ephemeral;config.protocolClasses=[StorageReceiptProtocol.self]
        let session=URLSession(configuration:config);defer {session.invalidateAndCancel()}
        let client=BackendClient(baseUrl:"https://example.invalid",liteTransport:LiteReceiptTestTransport(session:session))
        StorageReceiptProtocol.response=["wound_polygons":[],"image_w":64,"image_h":48,"stored":true,"image_id":"image","storage_receipt":valid]
        let result=try await client.liteSegment(jpeg:Data([1]),anonId:"a",consentVersion:"test")
        XCTAssertEqual(result.storageReceipt,LiteStorageReceipt.parse(valid))
        StorageReceiptProtocol.response.removeValue(forKey:"storage_receipt")
        let old=try await client.liteSegment(jpeg:Data([1]),anonId:"a",consentVersion:"test")
        XCTAssertTrue(old.stored);XCTAssertNil(old.storageReceipt)
    }
}
