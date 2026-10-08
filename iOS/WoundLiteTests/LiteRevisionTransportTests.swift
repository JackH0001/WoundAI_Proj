import XCTest
@testable import WoundLite

private final class RevisionProtocol: URLProtocol {
    static var responseJSON: [String: Any] = [:]
    static var observed: URLRequest?
    static var observedBody: Data?
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        Self.observed = request
        var body = request.httpBody
        if body == nil, let stream = request.httpBodyStream {
            stream.open(); defer { stream.close() }
            var bytes = [UInt8](repeating: 0, count: 1024), data = Data()
            while stream.hasBytesAvailable {
                let count = stream.read(&bytes, maxLength: bytes.count)
                if count <= 0 { break }
                data.append(contentsOf: bytes.prefix(count))
            }
            body = data
        }
        Self.observedBody = body
        let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: try! JSONSerialization.data(withJSONObject: Self.responseJSON))
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}

final class LiteRevisionTransportTests: XCTestCase {
    func testRequestUsesRevisionEndpointAndRequiresMatchingReceipt() async throws {
        let config = URLSessionConfiguration.ephemeral; config.protocolClasses = [RevisionProtocol.self]
        let session = URLSession(configuration: config); defer { session.invalidateAndCancel() }
        let client = BackendClient(baseUrl: "https://original.invalid", liteTransport: LiteReceiptTestTransport(session: session))
        let payload = "{\"test\":1}", digest = LitePendingRevision.digest("{\"test\":1}")
        let valid: [String: Any] = ["status": "stored", "image_id": "image", "revision": 2, "payload_sha256": digest]
        RevisionProtocol.responseJSON = valid
        try await client.liteRevision(bindingAnonID: "installation", imageID: "image", revision: 2,
                                      payloadJSON: payload, digest: digest)
        XCTAssertEqual(RevisionProtocol.observed?.url?.path, "/api/v1/lite/annotation/revision")
        let body = try XCTUnwrap(RevisionProtocol.observedBody)
        let request = try XCTUnwrap(JSONSerialization.jsonObject(with: body) as? [String: Any])
        XCTAssertEqual(request["payload_json"] as? String, payload)
        XCTAssertEqual(request["anon_id"] as? String, "installation")
        XCTAssertNil(RevisionProtocol.observed?.value(forHTTPHeaderField: "Authorization"))
        for (key, bad) in [("image_id", "other" as Any), ("revision", 1 as Any),
                           ("revision", true as Any), ("payload_sha256", "wrong" as Any),
                           ("status", "queued" as Any)] {
            RevisionProtocol.responseJSON = valid; RevisionProtocol.responseJSON[key] = bad
            do {
                try await client.liteRevision(bindingAnonID: "installation", imageID: "image", revision: 2,
                                              payloadJSON: payload, digest: digest)
                XCTFail("Mismatched receipt accepted: \(key)")
            } catch { /* expected */ }
        }
        RevisionProtocol.responseJSON = ["status": "stored", "image_id": "image"]
        do {
            try await client.liteRevision(bindingAnonID: "installation", imageID: "image", revision: 2,
                                          payloadJSON: payload, digest: digest)
            XCTFail("Legacy acknowledgement must not confirm a revision")
        } catch { /* expected */ }
    }
}
