import XCTest

final class NativeEntryUITests: XCTestCase {
    @MainActor func testOperationGuideFindsReportWithoutAccountOrResearchRegistration() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launch()
        let gate = app.buttons["設定與資料撤回"]
        let settings = gate.waitForExistence(timeout: 3) ? gate : app.buttons["設定"].firstMatch
        XCTAssertTrue(settings.waitForExistence(timeout: 8))
        settings.tap()
        let guide = app.buttons["lite.help.open"]
        XCTAssertTrue(guide.waitForExistence(timeout: 5))
        guide.tap()
        let query = app.textFields["lite.help.question"]
        XCTAssertTrue(query.waitForExistence(timeout: 5))
        query.tap()
        query.typeText("PDF")
        let report = app.buttons["lite.help.topic.report"]
        XCTAssertTrue(report.waitForExistence(timeout: 5))
        report.tap()
        XCTAssertTrue(app.staticTexts["紀錄 → 選一處傷口 → 匯出此傷口時間軸 PDF"].waitForExistence(timeout: 5))
        attach(app, "offline-guide-report")
    }

    @MainActor func testPrivacyPolicyOpensFromSettingsWithoutRegistrationAndReturns() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launch()
        let gate = app.buttons["設定與資料撤回"]
        let settings = gate.waitForExistence(timeout: 3) ? gate : app.buttons["設定"].firstMatch
        XCTAssertTrue(settings.waitForExistence(timeout: 8))
        settings.tap()
        let terms = app.descendants(matching: .any)["lite.legal.terms"].firstMatch
        XCTAssertTrue(terms.waitForExistence(timeout: 5))
        let privacy = app.buttons["lite.legal.privacy"]
        XCTAssertTrue(privacy.isHittable)
        privacy.tap()
        XCTAssertTrue(app.navigationBars["隱私權政策"].waitForExistence(timeout: 5))
        let heading = app.webViews.descendants(matching: .any)
            .matching(NSPredicate(format: "label CONTAINS %@", "WoundAILite 民眾版隱私權政策草稿")).firstMatch
        XCTAssertTrue(heading.waitForExistence(timeout: 10))
        attach(app, "offline-privacy-policy")
        app.navigationBars.buttons.element(boundBy: 0).tap()
        XCTAssertTrue(privacy.waitForExistence(timeout: 5))
    }
    #if LITE_QA_SAVED_FIXTURE || LITE_QA_EXTERNAL_FIXTURE
    @MainActor func testSelectedWoundTimelineOffersDateRangePhotosAndSystemSharing() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launch()
        let local = app.buttons["查看既有紀錄"]
        let history = local.waitForExistence(timeout:3) ? local : app.buttons["紀錄"].firstMatch
        XCTAssertTrue(history.waitForExistence(timeout:8))
        history.tap()
        let export = app.buttons["lite.timeline.export"]
        XCTAssertTrue(export.waitForExistence(timeout:5))
        export.tap()
        XCTAssertTrue(app.navigationBars["匯出時間軸"].waitForExistence(timeout:5))
        XCTAssertTrue(app.switches["全部日期"].exists)
        XCTAssertTrue(app.switches["附上照片與圈選"].exists)
        let share = app.buttons["產生並分享 PDF"]
        XCTAssertTrue(share.isEnabled)
        attach(app,"selected-wound-timeline-export-options")
        share.tap()
        let copy = app.buttons["Copy"].firstMatch
        let sheet = app.otherElements["ActivityListView"]
        let appeared = NSPredicate { _,_ in copy.exists || sheet.exists || app.buttons["拷貝"].firstMatch.exists }
        expectation(for:appeared,evaluatedWith:app)
        waitForExpectations(timeout:12)
        attach(app,"selected-wound-timeline-share-sheet")
    }

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
        #if LITE_QA_EXTERNAL_FIXTURE
        let share = app.buttons["lite.report.share"]
        for _ in 0..<5 where !share.isHittable { app.swipeUp() }
        XCTAssertTrue(share.isHittable)
        XCTAssertFalse(service.exists)
        attach(app, "external-history-without-internal-entry")
        #else
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
        #endif
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
        XCTAssertFalse(app.buttons["訂閱與恢復購買"].exists)
        XCTAssertFalse(app.buttons["核對原刪帳進度"].exists)
        attach(app, "first-use-registration")
        let preview = app.buttons["Plus 方案與價格"]
        XCTAssertTrue(preview.isHittable)
        preview.tap()
        XCTAssertTrue(app.navigationBars["Plus 方案與價格"].waitForExistence(timeout: 8))
        XCTAssertTrue(app.staticTexts["lite.offer.quota"].exists)
        XCTAssertFalse(app.buttons["lite.offer.buy"].exists)
        XCTAssertFalse(app.buttons["恢復購買"].exists)
        XCTAssertFalse(app.buttons["核對未完成購買"].exists)
        XCTAssertFalse(app.staticTexts["nativePurchaseStatus"].exists)
        attach(app, "read-only-offer-before-registration")
        #endif
    }

    @MainActor func testPersonalQuotaHasItsOwnEntryWithoutResearchOrDeviceRegistration() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launch()
        let gate=app.buttons["設定與資料撤回"]
        let settings=gate.waitForExistence(timeout:3) ? gate : app.buttons["設定"].firstMatch
        XCTAssertTrue(settings.waitForExistence(timeout:8))
        settings.tap()
        let link=app.buttons["查看個人帳號今日額度"]
        #if LITE_QA_ORDINARY || LITE_QA_MISSING_BINDING
        XCTAssertFalse(link.exists)
        #else
        for _ in 0..<5 where !link.isHittable { app.swipeUp() }
        XCTAssertTrue(link.isHittable)
        link.tap()
        let query=app.buttons["personalQuotaCheck"]
        XCTAssertTrue(query.waitForExistence(timeout:8))
        XCTAssertTrue(app.staticTexts["登入個人帳號後，可核對今天還能辨識幾次。"].exists)
        query.tap()
        XCTAssertTrue(app.staticTexts["今日額度尚未確認。請先核對個人服務登入，或稍後再試。"].waitForExistence(timeout:8))
        XCTAssertFalse(app.staticTexts.matching(NSPredicate(format:"label CONTAINS %@", "研究辨識剩餘")).firstMatch.exists)
        attach(app,"personal-quota-without-service-registration")
        #endif
    }

    @MainActor private func attach(_ app: XCUIApplication, _ name: String) {
        let item = XCTAttachment(screenshot: app.screenshot())
        item.name = name
        item.lifetime = .keepAlways
        add(item)
    }
}
