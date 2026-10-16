#if WOUND_INSTITUTION_TESTS
import XCTest
import UIKit
@testable import WoundAIInstitution

private final class ReviewHTTP: URLProtocol {
    static let lock = NSLock()
    private static var callback: ((URLRequest) -> (Int, Data))?
    private static var recorded: [URLRequest] = []
    static func use(_ callback: @escaping (URLRequest) -> (Int, Data)) {
        lock.lock(); defer { lock.unlock() }; self.callback = callback; recorded = []
    }
    static func requests() -> [URLRequest] { lock.lock(); defer { lock.unlock() }; return recorded }
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        Self.lock.lock(); Self.recorded.append(request); let callback = Self.callback; Self.lock.unlock()
        guard let callback else { client?.urlProtocol(self, didFailWithError: URLError(.cancelled)); return }
        let (code, data) = callback(request)
        client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: code, httpVersion: nil, headerFields: nil)!, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: data); client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}

private actor RuntimeReply: InstitutionAuthenticatedTransport {
    let org: String
    private(set) var calls = 0
    private var hold = false
    private var waiter: CheckedContinuation<Void, Never>?
    init(org: String) { self.org = org }
    func pause() { hold = true }
    func release() { hold = false; waiter?.resume(); waiter = nil }
    func invalidate() {}
    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        calls += 1
        if hold { await withCheckedContinuation { waiter = $0 } }
        let login = request.url!.path.hasSuffix("/login")
        var body: [String: Any] = ["identity": org + ":dr01", "org": org, "user": "dr01", "role": "physician",
            "role_zh": "醫師", "perms": ["measure.sample", "gt.verify", "annotation.submit"]]
        if login { body["access_token"] = "synthetic-runtime-fixture" }
        else { body["session_contract"] = "account-revision-v1"; body["expires_at"] = Date().timeIntervalSince1970 + 300 }
        return (try JSONSerialization.data(withJSONObject: body), HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!)
    }
}

@MainActor
final class MedicalRuntimeTests: XCTestCase {
    private var root: URL!
    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    }
    override func tearDownWithError() throws { try? FileManager.default.removeItem(at: root) }
    private func runtime(_ scope: MedicalDataScope, transport: RuntimeReply? = nil) throws -> MedicalRuntime {
        let suite = "runtime-test-" + UUID().uuidString
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        addTeardownBlock { defaults.removePersistentDomain(forName: suite) }
        let org = scope == .mmh ? "mmhps20261007" : "mmh-review-demo"
        let t = transport ?? RuntimeReply(org: org)
        return try MedicalRuntime(scope: scope, support: root, preferences: defaults, clientFactory: { origin, epoch, google in
            BackendClient(baseUrl: origin, institutionTransport: t, sessionContext: epoch,
                          bindInstitutionSession: google, expectedMedicalOrg: org)
        })
    }
    func testDemoAccountAndConsentNeverReadMMHSettings() throws {
        let a = try runtime(.mmh), b = try runtime(.syntheticReview)
        XCTAssertTrue(a.setCredentials(user: "mmhps20261007:dr01", password: UUID().uuidString))
        XCTAssertEqual(b.backendUser(), ""); XCTAssertEqual(b.backendPassword(), "")
        a.local.accounts.add(["MMH-SYNTH"], to: .withdrawal)
        b.local.accounts.add(["REVIEW-SYNTH"], to: .restore)
        XCTAssertEqual(a.consent.accounts.pending(.withdrawal), ["MMH-SYNTH"])
        XCTAssertTrue(b.consent.accounts.pending(.withdrawal).isEmpty)
        XCTAssertTrue(a.consent.accounts.pending(.restore).isEmpty)
        b.clearCredentials()
        XCTAssertEqual(a.backendUser(), "mmhps20261007:dr01")
        XCTAssertNotEqual(a.local.privateExports, b.local.privateExports)
        XCTAssertNotEqual(a.exportPreferences, b.exportPreferences)
    }
    func testDemoCanRetryOutputWithoutUploadAndRetainsPrivateCopyOnFolderFailure() async throws {
        let transport = RuntimeReply(org: "mmh-review-demo")
        let demo = try runtime(.syntheticReview, transport: transport)
        defer { demo.deactivate() }
        XCTAssertTrue(demo.setCredentials(user: "review01", password: UUID().uuidString))
        demo.exportPreferences.set(true, forKey: InstitutionPrivateExports.preferenceKey)
        let format = UIGraphicsImageRendererFormat(); format.scale = 1
        let image = UIGraphicsImageRenderer(size: CGSize(width: 64, height: 64), format: format).image { c in
            UIColor.white.setFill(); c.fill(CGRect(x: 0, y: 0, width: 64, height: 64))
        }
        let raw = try XCTUnwrap(image.jpegData(compressionQuality: 0.9))
        let raster = EditRaster(mask: [1,1,1,1], tissue: [1,2,3,5], origMask: [1,1,1,1],
            rx0: 10, ry0: 10, mw: 2, mh: 2, mScale: 1, cm2PerPx: nil, canvasW: 64, canvasH: 64)
        let encoded = try XCTUnwrap(EditRasterCodec.encode(raster))
        var row = Measurement(); row.id = 1; row.source = "phantom"; row.doctorVerified = true
        row.imageId = "synthetic-export"; row.wdCode = "SYNTH-EXPORT"; row.imageW = 64; row.imageH = 64
        row.imagePath = try XCTUnwrap(demo.local.images.save(jpeg: raw))
        row.rasterPath = try XCTUnwrap(demo.local.images.saveRaw(encoded.0)); row.rasterMeta = encoded.1
        row.gtPolygon = PolygonJson.toJson([[[10,10],[11,10],[11,11],[10,11]]])
        let payload = try BackendClient.annotationPayload(code: row.wdCode!, gtPolygon: row.polygons[0], exudate: nil,
            imageId: row.imageId, imageW: 64, imageH: 64,
            tissueMaskPngBase64: TissueMaskCodec.encodeBase64(tissue: raster.tissue, mask: raster.mask, mw: 2, mh: 2),
            tissueRaster: raster, consentTrain: true, doctorVerified: true)
        let jobID = try demo.local.outbox.enqueue(measurement: row, payload: payload, server: demo.backendURL(),
            configuredUser: demo.backendUser(), actor: "mmh-review-demo:review01", depth: nil, requiresDepth: false)
        XCTAssertTrue(demo.hasSubmissionForExport)
        demo.exportPreferences.set(Data([0]), forKey: InstitutionPrivateExports.folderKey)
        let failed = await demo.retryLatestSubmissionExport()
        XCTAssertTrue(failed.contains("自訂資料夾輸出失敗"), failed)
        XCTAssertEqual(demo.local.outbox.jobs.count, 1)
        let copies = try FileManager.default.contentsOfDirectory(at: demo.local.privateExports, includingPropertiesForKeys: nil)
        XCTAssertEqual(copies.count, 1)
        let copy = try InstitutionPrivateExports.read(try XCTUnwrap(copies.first))
        XCTAssertEqual(copy["submission.json"], payload)
        let localFolder = root.appendingPathComponent("Demo-output")
        try FileManager.default.createDirectory(at: localFolder, withIntermediateDirectories: true)
        try InstitutionPrivateExports.writeDelivery(files: copy, name: "demo-retry", folder: localFolder)
        let delivered = try XCTUnwrap(FileManager.default.contentsOfDirectory(at: localFolder, includingPropertiesForKeys: nil).first)
        XCTAssertEqual(Set(try FileManager.default.contentsOfDirectory(atPath: delivered.path)), ["image.jpg", "overlay.png"])
        XCTAssertEqual(try Data(contentsOf: delivered.appendingPathComponent("image.jpg")), raw)
        XCTAssertNotNil(UIImage(data: try Data(contentsOf: delivered.appendingPathComponent("overlay.png"))))
        demo.exportPreferences.removeObject(forKey: InstitutionPrivateExports.folderKey)
        let success = await demo.retryLatestSubmissionExport()
        XCTAssertTrue(success.contains("已保存於 App 私密空間"), success)
        XCTAssertEqual(demo.exportPreferences.string(forKey: InstitutionPrivateExports.lastResultKey), success.trimmingCharacters(in: .whitespacesAndNewlines))
        XCTAssertEqual(demo.local.outbox.jobs.map(\.id), [jobID])
        XCTAssertEqual(demo.local.outbox.jobs.first?.annotationPayload, payload)
        let calls = await transport.calls; XCTAssertEqual(calls, 0, "Local retry must not authenticate or upload")
        XCTAssertFalse(try runtime(.mmh).hasSubmissionForExport)
        XCTAssertTrue(demo.setCredentials(user: "another-user", password: UUID().uuidString))
        XCTAssertFalse(demo.hasSubmissionForExport, "Do not select another account's submission")
    }
    func testClosedEnvironmentRejectsLateLoginAndNewClients() async throws {
        let transport = RuntimeReply(org: "mmh-review-demo"); await transport.pause()
        let old = try runtime(.syntheticReview, transport: transport)
        XCTAssertTrue(old.setCredentials(user: "dr01", password: UUID().uuidString))
        let work = Task { try await old.acquire() }
        while await transport.calls == 0 { await Task.yield() }
        old.deactivate(); await transport.release()
        do { _ = try await work.value; XCTFail("Closed context accepted late login") } catch {}
        do { _ = try await old.makeBackend().login(username: "dr01", password: UUID().uuidString); XCTFail("Closed epoch used by new client") } catch {}
        let calls = await transport.calls; XCTAssertEqual(calls, 1)
    }
    func testDemoAndMMHValidateTheirOwnOrgAndGoogleOnlyInvalidatesMMH() async throws {
        let a = try runtime(.mmh), b = try runtime(.syntheticReview)
        for value in [a, b] { XCTAssertTrue(value.setCredentials(user: "dr01", password: UUID().uuidString)) }
        let ca = try await a.acquire(), cb = try await b.acquire()
        let ia = await ca.currentIdentity(), ib = await cb.currentIdentity()
        XCTAssertEqual(ia?.org, "mmhps20261007"); XCTAssertEqual(ib?.org, "mmh-review-demo")
        MedicalSessionEpoch.shared.invalidate()
        do { _ = try await ca.validateMedicalSession(); XCTFail("MMH stale Google generation accepted") } catch {}
        let alive = try await cb.validateMedicalSession(); XCTAssertEqual(alive.org, "mmh-review-demo")
    }
    func testUnexpectedMMHSessionCannotValidateInReview() async throws {
        let b = try runtime(.syntheticReview, transport: RuntimeReply(org: "mmhps20261007"))
        XCTAssertTrue(b.setCredentials(user: "dr01", password: UUID().uuidString))
        do { _ = try await b.acquire(); XCTFail("Foreign institution accepted") } catch {}
    }
    func testCapturedOutboxConnectionCannotFollowEnvironmentOrAccountSwitch() throws {
        let a = try runtime(.mmh), b = try runtime(.syntheticReview)
        XCTAssertTrue(a.setCredentials(user: "dr01", password: UUID().uuidString))
        let captured = a.connection
        XCTAssertTrue(captured.active())
        XCTAssertTrue(a.setCredentials(user: "dr02", password: UUID().uuidString))
        XCTAssertFalse(captured.active())
        let second = a.connection; a.deactivate()
        XCTAssertFalse(second.active()); XCTAssertTrue(b.connection.active())
        XCTAssertNotEqual(second.origin(), b.connection.origin())
    }
    func testSwitchWaitsForWorkerAndReopenPreservesUnsentSnapshot() async throws {
        let t = RuntimeReply(org: "mmh-review-demo"); await t.pause()
        let old = try runtime(.syntheticReview, transport: t)
        XCTAssertTrue(old.setCredentials(user: "dr01", password: UUID().uuidString))
        var measurement = Measurement(); measurement.source = "phantom"; measurement.imageId = "synthetic-switch"
        let saved = await old.local.repo.insertMeasurement(measurement)
        measurement.id = try XCTUnwrap(saved)
        let bytes = Data("immutable synthetic submission".utf8)
        let jobID = try old.local.outbox.enqueue(measurement: measurement, payload: bytes,
            server: old.backendURL(), configuredUser: "dr01", actor: "mmh-review-demo:dr01",
            depth: nil, requiresDepth: false)
        old.resumeUploads()
        while await t.calls == 0 { await Task.yield() }
        XCTAssertEqual(old.local.outbox.activeID, jobID)
        old.deactivate()
        var settled = false
        let shutdown = Task { await old.waitUntilSuspended(); settled = true }
        await Task.yield()
        XCTAssertFalse(settled, "Cannot reopen storage before the held worker has unwound")
        await t.release(); await shutdown.value
        XCTAssertTrue(settled); XCTAssertNil(old.local.outbox.activeID)
        let reopened = try runtime(.syntheticReview)
        let job = try XCTUnwrap(reopened.local.outbox.jobs.first)
        XCTAssertEqual(job.id, jobID); XCTAssertEqual(job.annotationPayload, bytes)
        XCTAssertFalse(job.completed); XCTAssertFalse(job.annotationConfirmed)
        XCTAssertNotNil(job.lastIssue)
        XCTAssertTrue(try runtime(.mmh).local.outbox.jobs.isEmpty)
        let calls = await t.calls; XCTAssertEqual(calls, 1, "Closed environment must not send annotations")
    }
    func testConsentIsPersistedBeforeAwaitAndClosedContextCannotClearIt() async throws {
        let t = RuntimeReply(org: "mmh-review-demo"); await t.pause()
        let review = try runtime(.syntheticReview, transport: t), mmh = try runtime(.mmh)
        XCTAssertTrue(review.setCredentials(user: "dr01", password: UUID().uuidString))
        let work = Task { await ConsentSync.pushToBackend(codes: ["SYNTH-CONSENT"], context: review.consent) }
        while await t.calls == 0 { await Task.yield() }
        XCTAssertEqual(review.local.accounts.pending(.withdrawal), ["SYNTH-CONSENT"])
        review.deactivate(); await t.release()
        let result = await work.value
        XCTAssertEqual(result.pending, ["SYNTH-CONSENT"])
        XCTAssertEqual(review.local.accounts.pending(.withdrawal), ["SYNTH-CONSENT"])
        XCTAssertTrue(mmh.local.accounts.pending(.withdrawal).isEmpty)
    }
}

@MainActor
final class SyntheticReviewTransportTests: XCTestCase {
    private func transport(_ handler: @escaping (URLRequest) -> (Int, Data)) -> SyntheticReviewTransport {
        ReviewHTTP.use(handler)
        let config = URLSessionConfiguration.ephemeral; config.protocolClasses = [ReviewHTTP.self]
        return SyntheticReviewTransport(session: URLSession(configuration: config))
    }
    private func health(service: String = "woundai-mmh-review-demo", environment: String = "synthetic-review") -> Data {
        try! JSONSerialization.data(withJSONObject: ["build": ["service": service, "revision": "synthetic-r1"], "deployment_environment": environment])
    }
    func testServiceProbePrecedesCredentialsAndNoGoogleHeadersLeave() async throws {
        let health = health()
        let t = transport { r in (200, r.url!.path == "/api/health" ? health : Data("{}".utf8)) }
        var request = URLRequest(url: URL(string: SyntheticReviewTransport.origin + "/api/auth/login")!)
        request.httpMethod = "POST"
        request.setValue("Bearer application-fixture", forHTTPHeaderField: "Authorization")
        for header in ["Proxy-Authorization", "X-Serverless-Authorization", "Cookie"] { request.setValue("must-not-leave", forHTTPHeaderField: header) }
        _ = try await t.send(request)
        let requests = ReviewHTTP.requests()
        XCTAssertEqual(requests.map { $0.url!.path }, ["/api/health", "/api/auth/login"])
        XCTAssertNil(requests[0].value(forHTTPHeaderField: "Authorization"))
        for r in requests { for h in ["Proxy-Authorization", "X-Serverless-Authorization", "Cookie"] { XCTAssertNil(r.value(forHTTPHeaderField: h)) } }
        XCTAssertEqual(requests[1].value(forHTTPHeaderField: "Authorization"), "Bearer application-fixture")
    }
    func testWrongServiceOrEnvironmentCannotReceiveAppCredentials() async throws {
        for data in [health(service: "woundai-backend-mmhps20261007"), health(environment: "medical"), Data("{}".utf8)] {
            let t = transport { _ in (200, data) }
            let request = URLRequest(url: URL(string: SyntheticReviewTransport.origin + "/api/auth/login")!)
            do { _ = try await t.send(request); XCTFail("Wrong service accepted") } catch {}
            XCTAssertEqual(ReviewHTTP.requests().map { $0.url!.path }, ["/api/health"])
        }
    }
    func testOriginConfusionRejectedBeforeNetwork() async throws {
        let t = transport { _ in XCTFail("Untrusted request sent"); return (200, Data()) }
        for url in ["https://woundai-backend-mmhps20261007-421209514056.asia-east1.run.app/api/health",
                    SyntheticReviewTransport.origin + ".attacker.invalid/api/health",
                    SyntheticReviewTransport.origin + ":443/api/health",
                    SyntheticReviewTransport.origin + "/api/%2e%2e/console",
                    SyntheticReviewTransport.origin + "/console",
                    SyntheticReviewTransport.origin + "/api/health#fragment"] {
            do { _ = try await t.send(URLRequest(url: URL(string: url)!)); XCTFail("Untrusted destination accepted") } catch {}
        }
        XCTAssertTrue(ReviewHTTP.requests().isEmpty)
    }
    func testRedirectOrFailedProbeNeverSendsLoginAndNoAutomaticRetry() async throws {
        for code in [302, 401, 503] {
            let t = transport { _ in (code, Data()) }
            do { _ = try await t.send(URLRequest(url: URL(string: SyntheticReviewTransport.origin + "/api/auth/login")!)); XCTFail("Bad probe accepted") } catch {}
            XCTAssertEqual(ReviewHTTP.requests().count, 1)
        }
    }
    func testInvalidatedTransportRejectsFurtherNetwork() async throws {
        let t = transport { _ in XCTFail("Inactive request sent"); return (200, Data()) }
        await t.invalidate()
        do { _ = try await t.send(URLRequest(url: URL(string: SyntheticReviewTransport.origin + "/api/health")!)); XCTFail("Inactive transport used") } catch {}
        XCTAssertTrue(ReviewHTTP.requests().isEmpty)
    }
}

#endif
