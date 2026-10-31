import XCTest

final class NativeEntryUITests: XCTestCase {
    #if LITE_QA_SAVED_FIXTURE
    /// The history/PDF fixture has an existing synthetic 10.08 cm² record.
    /// This run uses a simulator without a registered Sandbox installation.
    @MainActor func testSavedRecordCanBeReadBeforeServiceRegistration() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launch()
        let offline = app.buttons["查看既有紀錄"]
        let history = offline.waitForExistence(timeout: 3) ? offline : app.buttons["紀錄"].firstMatch
        XCTAssertTrue(history.waitForExistence(timeout: 8))
        history.tap()
        let saved = app.buttons.matching(NSPredicate(format: "label CONTAINS %@", "LiDAR 10.08")).firstMatch
        for _ in 0..<5 where !saved.isHittable { app.swipeUp() }
        XCTAssertTrue(saved.isHittable)
        saved.tap()
        let service = app.buttons["此筆服務辨識驗收（內部）"]
        for _ in 0..<5 where !service.isHittable { app.swipeUp() }
        XCTAssertTrue(service.isHittable)
        service.tap()
        let send = app.buttons["送出此筆照片供服務辨識"]
        XCTAssertTrue(send.waitForExistence(timeout: 8))
        XCTAssertFalse(send.isEnabled)
        let reread = app.buttons["重讀本機服務紀錄（不連網）"]
        XCTAssertTrue(reread.isEnabled)
        reread.tap()
        XCTAssertTrue(send.exists)
        XCTAssertFalse(send.isEnabled)
        attach(app, "saved-record-before-registration")
    }
    #endif

    // Run with a simulator. Only the test runner changes its expectation.
    // The application always uses its compiled profile and signed bundle settings.
    @MainActor func testSettingsOpensTheExpectedBuildProfile() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launch()
        let gate = app.buttons["設定與資料撤回"]
        let settings = gate.waitForExistence(timeout: 3) ? gate : app.buttons["設定"].firstMatch
        XCTAssertTrue(settings.waitForExistence(timeout: 8))
        settings.tap()
        let entry = app.buttons["服務登入、訂閱與刪帳"]
        let missing = app.staticTexts["此測試版本的服務網址尚未設定完成。您仍可使用本機紀錄。"]
        XCTAssertTrue(app.navigationBars["設定"].waitForExistence(timeout: 8))
        #if LITE_QA_ORDINARY
        XCTAssertFalse(entry.exists)
        XCTAssertFalse(missing.exists)
        #elseif LITE_QA_MISSING_BINDING
        XCTAssertTrue(missing.waitForExistence(timeout: 8))
        XCTAssertFalse(entry.exists)
        #else
        XCTAssertTrue(entry.waitForExistence(timeout: 8))
        entry.tap()
        // App Attest cannot enroll a simulator. A clean install must stop at
        // the explicit registration step and must not imply a deletion error.
        XCTAssertTrue(app.buttons["核對裝置登記（不傳照片）"].waitForExistence(timeout: 8))
        XCTAssertFalse(app.buttons["服務帳號與刪除"].exists)
        XCTAssertFalse(app.buttons["Sandbox 訂閱與恢復購買"].exists)
        XCTAssertFalse(app.buttons["核對原刪帳進度"].exists)
        attach(app, "first-use-registration")
        #endif
    }

    @MainActor private func attach(_ app: XCUIApplication, _ name: String) {
        let item = XCTAttachment(screenshot: app.screenshot())
        item.name = name
        item.lifetime = .keepAlways
        add(item)
    }
}
