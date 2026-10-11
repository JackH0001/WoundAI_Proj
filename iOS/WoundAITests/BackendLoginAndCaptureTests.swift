import XCTest
import UIKit
#if WOUND_INSTITUTION_TESTS
@testable import WoundAIInstitution
#else
@testable import WoundMeasurementApp
#endif

private actor CaptureTransportFixture: InstitutionAuthenticatedTransport {
    var loginStatus = 200
    var classifyCount = 0
    var loginCount = 0
    func setLoginStatus(_ value: Int) { loginStatus = value }
    func invalidate() {}
    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let path = request.url!.path
        var status = 200
        var body = "{}"
        if path == "/api/auth/login" {
            loginCount += 1; status = loginStatus
            body = #"{"access_token":"synthetic-app-token","identity":"fixture:doctor","org":"fixture","user":"doctor","role":"physician","perms":["gt.verify","annotation.submit"]}"#
        } else if path == "/api/v1/classify" {
            classifyCount += 1
            try await Task.sleep(nanoseconds: 40_000_000)
            body = "{\"image_w\":32,\"image_h\":32,\"persisted\":true,\"image_id\":\"fixture-\(classifyCount)\",\"stage3_calibrate\":{\"area_cm2\":10}}"
        }
        return (Data(body.utf8), HTTPURLResponse(url:request.url!,statusCode:status,httpVersion:nil,headerFields:nil)!)
    }
}

@MainActor
final class BackendLoginAndCaptureTests: XCTestCase {
    func testSavedPasswordIsNotAuthenticatedAndVerifiedBusyIsNotLoggingIn() {
        let saved = BackendLoginPresentation(entryReady:true,verified:false,busy:false,saved:true)
        XCTAssertFalse(saved.showLogout); XCTAssertEqual(saved.button,"儲存並登入")
        let verified = BackendLoginPresentation(entryReady:true,verified:true,busy:true,saved:true)
        XCTAssertTrue(verified.status.contains("已登入"))
        XCTAssertEqual(verified.button,"正在讀取雲端狀態…")
        XCTAssertFalse(BackendLoginPresentation(entryReady:false,verified:true,busy:false,saved:true).showLogout)
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
    func testSecondCaptureLoginFailureCannotDisplayPreviousResult() async {
        let transport = CaptureTransportFixture(); let vm = model(transport)
        await vm.analyze(image(),phantom:true)
        XCTAssertEqual(vm.result?.imageId,"fixture-1")
        await transport.setLoginStatus(429)
        await vm.analyze(image(),phantom:true)
        XCTAssertNil(vm.result); XCTAssertNil(vm.image); XCTAssertNil(vm.pendingDepth)
        XCTAssertNil(vm.lastSavedId); XCTAssertFalse(vm.loading)
        XCTAssertTrue(vm.error?.contains("HTTP 429") == true)
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
