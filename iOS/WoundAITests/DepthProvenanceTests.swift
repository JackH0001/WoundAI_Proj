import XCTest
@testable import WoundMeasurementApp

final class DepthProvenanceTests: XCTestCase {
    private func capture(_ exif: Int?) -> DepthCapture {
        DepthCapture(map:Array(repeating:0.3,count:192),width:16,height:12,
                     fx:13,fy:17,cx:4,cy:3,refWidth:16,refHeight:12,
                     accuracy:"absolute",filtered:false,rgbWidth:160,rgbHeight:120,
                     sourceExifOrientation:exif)
    }

    private func roundTrip(_ meta: [String: Any]) throws -> [String: Any] {
        try XCTUnwrap(JSONSerialization.jsonObject(with:JSONSerialization.data(withJSONObject:meta)) as? [String:Any])
    }

    func testAllEightOriginsSurviveSidecarAndUploadJSONWithKnownIntrinsics() throws {
        for exif in 1...8 {
            let sidecar = try roundTrip(capture(exif).metaJson)
            XCTAssertEqual(sidecar["version"] as? Int,2)
            let upload = try roundTrip(BackendClient.depthUploadMetadata(sidecar))
            XCTAssertEqual(upload["source_exif_orientation"] as? Int,exif)
            XCTAssertEqual(upload["normalized_exif_orientation"] as? Int,1)
            XCTAssertEqual(upload["orientation_status"] as? String,"normalized")
            XCTAssertEqual(upload["registration"] as? String,"not_verified")
            let k = try XCTUnwrap(upload["camera_intrinsics"] as? [String:Any])
            for (key,value) in ["fx":13,"fy":17,"cx":4,"cy":3,"ref_width":16,"ref_height":12] {
                XCTAssertEqual(k[key] as? Int,value)
            }
            XCTAssertEqual(upload["rgb_w"] as? Int,160); XCTAssertEqual(upload["rgb_h"] as? Int,120)
        }
    }

    func testEncryptedSidecarReadBackPreservesProvenanceAndRawDepth() throws {
        let store = LocalImageStore(), key = "orientation-fixture-" + UUID().uuidString
        defer { DepthStore.purge(imagePath:key,store:store) }
        let d = capture(7)
        _ = try PhiCrypto.encryptBytes(Data("synthetic".utf8))
        XCTAssertTrue(DepthStore.attach(imagePath:key,capture:d,store:store))
        let saved = try XCTUnwrap(DepthStore.load(imagePath:key,store:store))
        XCTAssertEqual(saved.raw,d.map.withUnsafeBufferPointer { Data(buffer:$0) })
        XCTAssertEqual(saved.meta["source_exif_orientation"] as? Int,7)
        XCTAssertEqual(BackendClient.depthUploadMetadata(saved.meta)["source_exif_orientation"] as? Int,7)
    }

    func testActualUploadRequestContainsSidecarProvenance() async throws {
        let expected = expectation(description:"multipart metadata")
        OrientationRequestProtocol.inspect = { request in
            XCTAssertEqual(request.url?.path,"/api/v1/depth")
            var data = request.httpBody ?? Data()
            if let stream = request.httpBodyStream {
                stream.open(); defer { stream.close() }
                var bytes = [UInt8](repeating:0,count:4096)
                while stream.hasBytesAvailable {
                    let count = stream.read(&bytes,maxLength:bytes.count)
                    if count <= 0 { break }; data.append(contentsOf:bytes.prefix(count))
                }
            }
            let text = String(decoding:data,as:UTF8.self)
            let header = "name=\"meta\"\r\n\r\n"
            if let start = text.range(of:header), let end = text.range(of:"\r\n--",range:start.upperBound..<text.endIndex) {
                let json = Data(text[start.upperBound..<end.lowerBound].utf8)
                let meta = try? JSONSerialization.jsonObject(with:json) as? [String:Any]
                XCTAssertEqual(meta?["source_exif_orientation"] as? Int,6)
                XCTAssertEqual(meta?["normalized_exif_orientation"] as? Int,1)
                XCTAssertEqual(meta?["registration"] as? String,"not_verified")
            } else { XCTFail("Missing metadata multipart field") }
            expected.fulfill()
        }
        defer { OrientationRequestProtocol.inspect = nil }
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [OrientationRequestProtocol.self]
        let session = URLSession(configuration:config)
        defer { session.invalidateAndCancel() }
        let client = BackendClient(baseUrl:"https://orientation-test.invalid",jwt:UUID().uuidString,session:session)
        let d = capture(6)
        _ = try await client.uploadDepth(imageId:"fixture",raw:d.map.withUnsafeBufferPointer { Data(buffer:$0) },sidecarMeta:try roundTrip(d.metaJson))
        await fulfillment(of:[expected],timeout:2)
    }

    func testLegacyAndInvalidOriginsStayUnknown() throws {
        for exif: Int? in [nil,0,9] {
            let sidecar = try roundTrip(capture(exif).metaJson)
            XCTAssertNil(sidecar["source_exif_orientation"])
            let upload = BackendClient.depthUploadMetadata(try roundTrip(sidecar))
            XCTAssertEqual(upload["orientation_status"] as? String,"unknown")
            XCTAssertNil(upload["normalized_exif_orientation"])
            XCTAssertNil(upload["source_exif_orientation"])
        }
        let legacy: [String:Any] = ["version":1,"width":16,"height":12]
        XCTAssertEqual(BackendClient.depthUploadMetadata(legacy)["orientation_status"] as? String,"unknown")
    }

    func testIncompleteOrContradictoryProvenanceCannotBePromoted() throws {
        for changes: [String:Any] in [["source_exif_orientation":9],
            ["normalized_exif_orientation":6],["orientation_status":"unknown"],
            ["source_exif_orientation":"6"],["source_exif_orientation":true],
            ["normalized_exif_orientation":true],["source_exif_orientation":1.5]] {
            var sidecar = capture(6).metaJson
            changes.forEach { sidecar[$0.key] = $0.value }
            let upload = BackendClient.depthUploadMetadata(try roundTrip(sidecar))
            XCTAssertEqual(upload["orientation_status"] as? String,"unknown")
            XCTAssertNil(upload["source_exif_orientation"])
        }
    }
}

private final class OrientationRequestProtocol: URLProtocol {
    static var inspect: ((URLRequest) -> Void)?
    override class func canInit(with request:URLRequest) -> Bool { true }
    override class func canonicalRequest(for request:URLRequest) -> URLRequest { request }
    override func startLoading() {
        Self.inspect?(request)
        let response = HTTPURLResponse(url:request.url!,statusCode:200,httpVersion:nil,headerFields:nil)!
        client?.urlProtocol(self,didReceive:response,cacheStoragePolicy:.notAllowed)
        client?.urlProtocol(self,didLoad:Data("{\"status\":\"stored\",\"depth_id\":\"fixture\",\"replaced_previous\":false}".utf8))
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}
