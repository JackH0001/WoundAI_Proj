import XCTest
import CryptoKit
import AVFoundation
#if WOUND_INSTITUTION_TESTS
@testable import WoundAIInstitution
#else
@testable import WoundMeasurementApp
#endif

final class DepthProvenanceTests: XCTestCase {
    @MainActor
    func testUnfilteredDiagnosticIsExplicitAndRequiresDepthCapability() {
        let normal = CameraModel.photoSettings(depthEnabled:true,arguments:[])
        XCTAssertTrue(normal.isDepthDataDeliveryEnabled)
        #if DEBUG && WOUND_DEPTH_DIAGNOSTICS
        XCTAssertTrue(CameraModel.isUnfilteredDiagnosticBuild)
        XCTAssertFalse(normal.isDepthDataFiltered, "Dedicated local build must survive launch without arguments")
        #else
        XCTAssertFalse(CameraModel.isUnfilteredDiagnosticBuild)
        XCTAssertTrue(normal.isDepthDataFiltered)
        #endif
        let diagnostic = CameraModel.photoSettings(depthEnabled:true,arguments:["-WoundDepthUnfilteredDiagnostics"])
        #if DEBUG
        XCTAssertFalse(diagnostic.isDepthDataFiltered)
        #else
        XCTAssertTrue(diagnostic.isDepthDataFiltered)
        #endif
        let unsupported = CameraModel.photoSettings(depthEnabled:false,arguments:["-WoundDepthUnfilteredDiagnostics"])
        XCTAssertFalse(unsupported.isDepthDataDeliveryEnabled)
        XCTAssertTrue(unsupported.isDepthDataFiltered)
    }

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
            XCTAssertEqual(sidecar["version"] as? Int,3)
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

    func testCaptureIdentityAndTimeSurviveResaveAndNewStoreInstance() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: dir) }
        let store = LocalImageStore(directory: dir)
        var d = capture(6)
        d.captureProvenance = .init(id: UUID(uuidString: "00000000-0000-4000-8000-000000000001")!,
                                    observedAt: Date(timeIntervalSince1970: 1_700_000_000))
        let initial = BackendClient.depthUploadMetadata(d.metaJson)
        for key in ["first-save", "later-retrim"] {
            XCTAssertTrue(DepthStore.attach(imagePath: key, capture: d, store: store))
            let reopened = LocalImageStore(directory: dir)
            let saved = try XCTUnwrap(DepthStore.load(imagePath: key, store: reopened))
            let uploaded = BackendClient.depthUploadMetadata(saved.meta)
            for field in ["capture_id", "captured_at", "capture_time_source"] {
                XCTAssertEqual(uploaded[field] as? String, initial[field] as? String)
            }
            XCTAssertEqual(uploaded["captured_at"] as? String, "2023-11-14T22:13:20.000Z")
            XCTAssertEqual(uploaded["capture_time_source"] as? String, "photo_callback_wall_clock")
        }
    }

    func testDepthReceiptSurvivesIndexReopenWithoutCrossingImages() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: dir) }
        let store = LocalImageStore(directory: dir)
        XCTAssertTrue(DepthStore.attach(imagePath: "first", capture: capture(6), store: store))
        XCTAssertTrue(DepthStore.attach(imagePath: "second", capture: capture(6), store: store))
        let receipt = BackendClient.DepthUploadReceipt(depthId: "0123456789abcdef",
            annotationRevisionID: "fedcba9876543210", metadataSHA256: String(repeating: "a", count: 64),
            payloadSHA256: String(repeating: "b", count: 64), receiptID: String(repeating: "c", count: 64), replaced: false)
        XCTAssertTrue(DepthStore.markUploaded(imagePath: "first", store: store, receipt: receipt))
        let reopened = LocalImageStore(directory: dir)
        let entry = try XCTUnwrap(DepthStore.lookup(imagePath: "first", store: reopened))
        XCTAssertEqual(entry["annotation_receipt_id"], receipt.annotationRevisionID)
        XCTAssertEqual(entry["payload_sha256"], receipt.payloadSHA256)
        XCTAssertEqual(entry["meta_sha256"], receipt.metadataSHA256)
        XCTAssertEqual(entry["depth_receipt_id"], receipt.receiptID)
        XCTAssertNil(DepthStore.lookup(imagePath: "second", store: reopened)?["uploaded"])
        XCTAssertFalse(DepthStore.markUploaded(imagePath: "missing", store: reopened, receipt: receipt))
    }

    func testUnknownCaptureAndLegacySaveTimeAreNeverReplacedByNow() throws {
        let fresh = capture(nil).metaJson
        XCTAssertNil(fresh["captured_at"])
        XCTAssertNil(fresh["capture_id"])
        XCTAssertEqual(fresh["capture_time_source"] as? String, "unknown")
        let legacy: [String: Any] = ["version": 2, "captured_at": "2020-01-01T00:00:00Z"]
        let upload = BackendClient.depthUploadMetadata(legacy)
        XCTAssertEqual(upload["captured_at"] as? String, legacy["captured_at"] as? String)
        XCTAssertNil(upload["capture_time_source"])
        XCTAssertNil(upload["capture_id"])
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
                OrientationRequestProtocol.requestMetadata = json
                let meta = try? JSONSerialization.jsonObject(with:json) as? [String:Any]
                XCTAssertEqual(meta?["source_exif_orientation"] as? Int,6)
                XCTAssertEqual(meta?["normalized_exif_orientation"] as? Int,1)
                XCTAssertEqual(meta?["registration"] as? String,"not_verified")
                XCTAssertEqual(meta?["captured_at"] as? String,"2023-11-14T22:13:20.000Z")
                XCTAssertEqual(meta?["capture_time_source"] as? String,"photo_callback_wall_clock")
            } else { XCTFail("Missing metadata multipart field") }
            expected.fulfill()
        }
        defer { OrientationRequestProtocol.inspect = nil }
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [OrientationRequestProtocol.self]
        let session = URLSession(configuration:config)
        defer { session.invalidateAndCancel() }
        let client = BackendClient(baseUrl:"https://orientation-test.invalid",jwt:UUID().uuidString,session:session,institutionTransport:OrientationTestTransport(session:session))
        var d = capture(6)
        d.captureProvenance = .init(observedAt: Date(timeIntervalSince1970: 1_700_000_000))
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
    static var requestMetadata = Data()
    override class func canInit(with request:URLRequest) -> Bool { true }
    override class func canonicalRequest(for request:URLRequest) -> URLRequest { request }
    override func startLoading() {
        Self.inspect?(request)
        let response = HTTPURLResponse(url:request.url!,statusCode:200,httpVersion:nil,headerFields:nil)!
        client?.urlProtocol(self,didReceive:response,cacheStoragePolicy:.notAllowed)
        let raw = [Float](repeating: 0.3, count: 192).withUnsafeBufferPointer { Data(buffer: $0) }
        let body = try! JSONSerialization.data(withJSONObject: depthReceiptFixture(raw: raw, metadata: Self.requestMetadata,
            image: "fixture", revision: "0123456789abcdef"))
        client?.urlProtocol(self, didLoad: body)
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}

private actor OrientationTestTransport: InstitutionAuthenticatedTransport {
    let session: URLSession
    init(session: URLSession) { self.session = session }
    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let (data, response) = try await session.data(for: request)
        return (data, response as! HTTPURLResponse)
    }
    func invalidate() async {}
}
