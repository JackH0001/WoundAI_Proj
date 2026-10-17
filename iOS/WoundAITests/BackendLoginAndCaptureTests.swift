import XCTest
import UIKit
#if WOUND_INSTITUTION_TESTS
@testable import WoundAIInstitution
#else
@testable import WoundMeasurementApp
#endif

private actor CaptureTransportFixture: InstitutionAuthenticatedTransport {
    var consentStatus = 200
    var consentBody = "{}"
    var retryAfter: String?
    func setConsent(_ status: Int, _ body: String, _ retry: String? = nil) { consentStatus=status; consentBody=body; retryAfter=retry }
    var loginStatus = 200
    var classifyCount = 0
    var loginCount = 0
    var sessionCount = 0
    var sessionStatus = 200
    func setSessionStatus(_ value: Int) { sessionStatus = value }
    func setLoginStatus(_ value: Int) { loginStatus = value }
    func invalidate() {}
    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let path = request.url!.path
        var status = 200
        var body = "{}"
        if path.hasPrefix("/api/v1/consent/") { status=consentStatus; body=consentBody }
        else if path == "/api/auth/login" {
            loginCount += 1; status = loginStatus
            body = #"{"access_token":"synthetic-app-token","identity":"mmhps20261007:dr01","org":"mmhps20261007","user":"dr01","role":"physician","perms":["gt.verify","annotation.submit"]}"#
        } else if path == "/api/v1/auth/session" {
            sessionCount += 1; status = sessionStatus
            let object: [String: Any] = ["identity":"mmhps20261007:dr01", "org":"mmhps20261007",
                "user":"dr01", "role":"physician", "role_zh":"醫師",
                "perms":["measure.sample", "gt.verify", "annotation.submit"],
                "session_contract":"account-revision-v1", "expires_at":Date().timeIntervalSince1970 + 300]
            body = String(data: try JSONSerialization.data(withJSONObject: object), encoding: .utf8)!
        } else if path == "/api/v1/classify" {
            classifyCount += 1
            try await Task.sleep(nanoseconds: 40_000_000)
            body = "{\"image_w\":32,\"image_h\":32,\"persisted\":true,\"image_id\":\"fixture-\(classifyCount)\",\"stage3_calibrate\":{\"area_cm2\":10}}"
        }
        return (Data(body.utf8), HTTPURLResponse(url:request.url!,statusCode:status,httpVersion:nil,headerFields:retryAfter.map { ["Retry-After":$0] })!)
    }
}

@MainActor
final class BackendLoginAndCaptureTests: XCTestCase {
    func testConsentRequiresBoundReceiptAndCarriesCapacityDeadline() async throws {
        let transport = CaptureTransportFixture()
        let client = BackendClient(baseUrl:"https://test.invalid",institutionTransport:transport)
        let loggedIn = try await client.login(username:"synthetic",password:UUID().uuidString)
        XCTAssertTrue(loggedIn)
        for body in ["{}", #"{"status":"withdrawn","code":"wrong"}"#, #"{"status":"restored","code":"fixture"}"#] {
            await transport.setConsent(200,body)
            let accepted = await client.withdrawConsent(code:"fixture"); XCTAssertFalse(accepted)
        }
        await transport.setConsent(200,#"{"status":"withdrawn","code":"fixture"}"#)
        let accepted = await client.withdrawConsent(code:"fixture"); XCTAssertTrue(accepted)
        await transport.setConsent(503,"{}","300")
        var wait: ResearchCapacityWait?
        let pending = await client.withdrawConsent(code:"fixture",onCapacity:{ wait=$0 })
        XCTAssertFalse(pending); XCTAssertGreaterThan(try XCTUnwrap(wait).notBefore.timeIntervalSinceNow,295)
    }
    func testSavedPasswordIsNotAuthenticatedAndVerifiedBusyIsNotLoggingIn() {
        let saved = BackendLoginPresentation(entryReady:true,verified:false,busy:false,saved:true)
        XCTAssertFalse(saved.showLogout); XCTAssertEqual(saved.button,"儲存並登入")
        let verified = BackendLoginPresentation(entryReady:true,verified:true,busy:true,saved:true)
        XCTAssertTrue(verified.status.contains("已登入"))
        XCTAssertEqual(verified.button,"正在讀取雲端狀態…")
        XCTAssertFalse(BackendLoginPresentation(entryReady:false,verified:true,busy:false,saved:true).showLogout)
    }
    func testUnverifiedGoogleKeepsLoginActionVisibleWithWaitingLabel() {
        let waiting = BackendLoginPresentation(entryReady:false,verified:false,busy:false,saved:true)
        XCTAssertEqual(waiting.button, "儲存並登入（待 Google 入口驗證）")
        XCTAssertFalse(waiting.showLogout)
        XCTAssertTrue(waiting.status.contains("請先完成 Google"))
    }
    func testSavedPasswordCannotMoveToAnotherOriginOrUser() {
        XCTAssertTrue(BackendLoginPresentation.mayReusePassword(inputUser:"doctor",savedUser:"doctor",inputURL:"https://test.invalid/",savedURL:"https://test.invalid"))
        XCTAssertFalse(BackendLoginPresentation.mayReusePassword(inputUser:"nurse",savedUser:"doctor",inputURL:"https://test.invalid",savedURL:"https://test.invalid"))
        XCTAssertFalse(BackendLoginPresentation.mayReusePassword(inputUser:"doctor",savedUser:"doctor",inputURL:"https://other.invalid",savedURL:"https://test.invalid"))
    }
    private func image() -> UIImage {
        let format = UIGraphicsImageRendererFormat(); format.scale = 1
        return UIGraphicsImageRenderer(size:CGSize(width:32,height:32), format:format).image { ctx in
            UIColor.red.setFill(); ctx.fill(CGRect(x:0,y:0,width:32,height:32))
        }
    }
    private func model(_ transport: CaptureTransportFixture) -> MeasureViewModel {
        MeasureViewModel(backendFactory: { BackendClient(baseUrl:"https://test.invalid",institutionTransport:transport) },
                         credentials: { ("fixture-doctor","synthetic-password") })
    }
    private func sharedModel(_ transport: CaptureTransportFixture) -> MeasureViewModel {
        let pool = MedicalSessionPool(input: {
            MedicalSessionPool.Input(origin:"https://test.invalid", user:"dr01", password:UUID().uuidString,
                                     epoch:MedicalSessionEpoch.shared.snapshot())
        }, factory: { BackendClient(baseUrl:$0, institutionTransport:transport) })
        return MeasureViewModel(credentials: { ("dr01", "fixture") },
                                sessionProvider: { try await pool.acquire() })
    }
    func testEditedResultKeepsConfirmedAreaInsteadOfRecalculatingRaster() async throws {
        let vm = sharedModel(CaptureTransportFixture())
        vm.source = "phantom" // Match the quick-measurement UI's explicit source selection.
        await vm.analyze(image(), phantom:true)
        let raster = EditRaster(mask:[1,1,1,1],tissue:[1,1,1,4],origMask:[1,1,1,1],
            rx0:0,ry0:0,mw:2,mh:2,mScale:1,cm2PerPx:0.0034034715225,canvasW:32,canvasH:32)
        vm.applyEdited(polygon:[[0,0],[2,0],[2,2]],all:[],iou:1,newArea:0.01,
                       tissue:raster.tissueFrac(),raster:raster)
        XCTAssertEqual(vm.result?.areaCm2,0.01)
        XCTAssertNotEqual(vm.result?.areaCm2,raster.areaCm2)
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let repo = CaseRepository(store: WoundStore(path: ":memory:"))
        let saved = await vm.saveToTimeline(repo: repo, imageStore: LocalImageStore(directory: root), woundCase: nil)
        XCTAssertTrue(saved)
        let id = try XCTUnwrap(vm.lastSavedId)
        let row = await repo.measurement(id: id)
        XCTAssertEqual(row?.estimatedArea, 0.01)

    }
    func testSharedSessionTenCapturesKeepImageBindingsWithoutTenLogins() async throws {
        let transport = CaptureTransportFixture(); let vm = sharedModel(transport)
        for index in 1...10 {
            await vm.analyze(image(), phantom:true)
            XCTAssertEqual(vm.result?.imageId,"fixture-\(index)")
            XCTAssertNil(vm.error)
        }
        let logins = await transport.loginCount, sessions = await transport.sessionCount
        let images = await transport.classifyCount
        XCTAssertEqual(logins,1); XCTAssertEqual(sessions,10); XCTAssertEqual(images,10)
    }
    func testSharedSessionSecondCaptureRevalidationFailureDoesNotUpload() async {
        let transport = CaptureTransportFixture(); let vm = sharedModel(transport)
        await vm.analyze(image(), phantom:true)
        await transport.setSessionStatus(503)
        await vm.analyze(image(), phantom:true)
        XCTAssertNil(vm.result); XCTAssertNil(vm.image); XCTAssertNil(vm.identity)
        XCTAssertNil(vm.lastSavedId); XCTAssertFalse(vm.loading)
        XCTAssertTrue(vm.error?.contains("後端容量暫滿") == true)
        let logins = await transport.loginCount, images = await transport.classifyCount
        XCTAssertEqual(logins,1); XCTAssertEqual(images,1)
    }
    func testSecondCaptureLoginFailureCannotDisplayPreviousResult() async {
        let transport = CaptureTransportFixture(); let vm = model(transport)
        await vm.analyze(image(),phantom:true)
        XCTAssertEqual(vm.result?.imageId,"fixture-1")
        await transport.setLoginStatus(429)
        await vm.analyze(image(),phantom:true)
        XCTAssertNil(vm.result); XCTAssertNil(vm.image); XCTAssertNil(vm.pendingDepth)
        XCTAssertNil(vm.lastSavedId); XCTAssertFalse(vm.loading)
        XCTAssertTrue(vm.error?.contains("後端容量暫滿") == true)
        let count = await transport.classifyCount
        XCTAssertEqual(count,1,"Failed login must not upload the next image")
    }
    func testTenSequentialCapturesAndDuplicateTapKeepSeparateResults() async {
        let transport = CaptureTransportFixture(); let vm = model(transport); let picture = image()
        for index in 1...10 {
            await vm.analyze(picture,phantom:true)
            XCTAssertEqual(vm.result?.imageId,"fixture-\(index)")
            XCTAssertNil(vm.error)
            XCTAssertEqual(Set(vm.analysisDurations.keys),Set(["登入驗證","影像準備","保存授權","傳送／辨識／保存"]))
        }
        vm.loading = true
        await vm.analyze(picture,phantom:true)
        XCTAssertEqual(vm.result?.imageId,"fixture-10")
        let count = await transport.classifyCount
        XCTAssertEqual(count,10)
    }
}
