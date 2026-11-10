import XCTest

final class NativeEntryUITests: XCTestCase {
    private func application(language: String = "zh-Hant") -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments += ["-AppleLanguages", "(\(language))", "-AppleLocale", language == "en" ? "en_US" : "zh_Hant_TW"]
        return app
    }

    @MainActor func testEnglishResearchDisclosureDeclineKeepsUploadsOffAfterRelaunch() throws {
        try verifyResearchDecline(language: "en")
    }

    @MainActor func testChineseResearchDisclosureDeclineKeepsUploadsOffAfterRelaunch() throws {
        try verifyResearchDecline(language: "zh-Hant")
    }

    @MainActor private func verifyResearchDecline(language: String) throws {
        continueAfterFailure = false
        let english = language == "en", app = application(language: language)
        func openSettings() {
            app.launch()
            let gate = app.buttons[english ? "Settings and data withdrawal" : "設定與資料撤回"]
            let settings = gate.waitForExistence(timeout: 3) ? gate : app.buttons[english ? "Settings" : "設定"].firstMatch
            XCTAssertTrue(settings.waitForExistence(timeout: 8)); settings.tap()
        }
        func reveal(_ item: XCUIElement) {
            for _ in 0..<12 where !item.isHittable { app.swipeUp() }
            XCTAssertTrue(item.isHittable)
        }
        openSettings()
        let toggle = app.switches["lite.research.toggle"].firstMatch
        reveal(toggle)
        XCTAssertEqual(toggle.value as? String, "0", "The isolated simulator must begin with research uploads off")
        XCTAssertTrue(toggle.isEnabled, "Use a fresh simulator; do not reset an existing withdrawal to exercise first consent")
        toggle.coordinate(withNormalizedOffset: CGVector(dx: 0.91, dy: 0.5)).tap()
        let title = app.staticTexts["lite.research.consent.title"]
        XCTAssertTrue(title.waitForExistence(timeout: 5))
        #if LITE_QA_ORDINARY
        XCTAssertEqual(title.label, english ? "Choose how to measure" : "選擇您的量測方式")
        #else
        XCTAssertEqual(title.label, english ? "Do you agree to research uploads?" : "是否同意上傳研究資料？")
        #endif
        attach(app, language + "-research-disclosure")
        let depth = app.staticTexts.containing(NSPredicate(format: "label CONTAINS %@", english ? "entire depth map and camera parameters" : "整張深度圖和相機參數")).firstMatch
        reveal(depth)
        XCTAssertTrue(depth.label.contains(english ? "outside the outline" : "圈選外"))
        XCTAssertTrue(depth.label.contains(english ? "link records" : "連結"))
        attach(app, language + "-research-depth-scope")
        let decline = app.buttons["lite.research.decline"]
        reveal(decline)
        XCTAssertTrue(app.buttons["lite.research.agree"].exists)
        #if !LITE_QA_ORDINARY
        XCTAssertEqual(decline.label, english ? "Continue without research uploads" : "不同意研究上傳，繼續使用")
        #endif
        attach(app, language + "-research-choice")
        decline.tap()
        XCTAssertTrue(toggle.waitForExistence(timeout: 5))
        XCTAssertEqual(toggle.value as? String, "0")
        app.terminate()
        openSettings()
        reveal(toggle)
        XCTAssertEqual(toggle.value as? String, "0")
        attach(app, language + "-research-off-after-relaunch")
    }

    #if !LITE_QA_ORDINARY && !LITE_QA_MISSING_BINDING
    @MainActor func testEnglishSubscriptionEntryExplainsQuotaAndDoesNotOpenAPurchase() throws {
        continueAfterFailure = false
        let app = application(language: "en")
        app.launch()
        let gate = app.buttons["Settings and data withdrawal"]
        let settings = gate.waitForExistence(timeout: 3) ? gate : app.buttons["Settings"].firstMatch
        XCTAssertTrue(settings.waitForExistence(timeout: 8)); settings.tap()
        let entry = app.buttons["Sign-in, subscriptions and account deletion"]
        XCTAssertTrue(entry.waitForExistence(timeout: 5)); entry.tap()
        XCTAssertTrue(app.buttons["Verify device registration (no photo sent)"].waitForExistence(timeout: 5))
        app.buttons["Plus plan and price"].tap()
        XCTAssertTrue(app.navigationBars["Plus plan and price"].waitForExistence(timeout: 5))
        let quota = app.staticTexts["lite.offer.quota"]
        XCTAssertTrue(quota.label.contains("2 successful")); XCTAssertTrue(quota.label.contains("total of 10"))
        let button = app.buttons["lite.offer.signIn"]
        for _ in 0..<8 where !button.isHittable { app.swipeUp() }
        XCTAssertTrue(button.isHittable); XCTAssertEqual(button.label, "Sign in and continue to Plus")
        XCTAssertFalse(app.buttons["lite.offer.buy"].exists)
        XCTAssertFalse(app.buttons["Restore purchases"].exists)
        XCTAssertTrue(app.staticTexts["lite.offer.preparationStatus"].label.contains("does not charge"))
        attach(app, "english-subscription-before-registration")
    }
    #endif
    @MainActor func testUnmatchedHelpKeepsOfflineTopicsAndDoesNotRetainTheQuestion() throws {
        continueAfterFailure = false
        let app = application()
        func openGuide() {
            app.launch()
            let gate = app.buttons["設定與資料撤回"]
            let settings = gate.waitForExistence(timeout: 3) ? gate : app.buttons["設定"].firstMatch
            XCTAssertTrue(settings.waitForExistence(timeout: 8))
            settings.tap()
            let guide = app.buttons["lite.help.open"]
            XCTAssertTrue(guide.waitForExistence(timeout: 5))
            guide.tap()
        }
        openGuide()
        let query = app.textFields["lite.help.question"]
        XCTAssertTrue(query.waitForExistence(timeout: 5))
        query.tap()
        query.typeText("zxqv help acceptance")
        XCTAssertTrue(app.staticTexts["沒有相符的關鍵字，可從下方挑選主題。"].waitForExistence(timeout: 5))
        let report = app.buttons["lite.help.topic.report"]
        for _ in 0..<6 where !report.isHittable { app.swipeUp() }
        XCTAssertTrue(report.isHittable)
        report.tap()
        XCTAssertTrue(app.staticTexts["紀錄 → 選一處傷口 → 匯出此傷口時間軸 PDF"].waitForExistence(timeout: 5))
        attach(app, "unmatched-help-offline-report")
        app.terminate()
        openGuide()
        XCTAssertTrue(query.waitForExistence(timeout: 5))
        let value = query.value as? String ?? ""
        XCTAssertFalse(value.contains("zxqv"), "An operation question must not become saved app history")
        attach(app, "help-query-not-retained-after-relaunch")
    }

    @MainActor func testOperationGuideFindsReportWithoutAccountOrResearchRegistration() throws {
        continueAfterFailure = false
        let app = application()
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
        let app = application()
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
    #if LITE_QA_PHOTO_FIXTURE
    @MainActor func testLocalWoundPhotosOpenInTimeOrderAndReturnToTimeline() throws {
        continueAfterFailure = false
        let app = application()
        app.launch()
        let local = app.buttons["查看既有紀錄"]
        let history = local.waitForExistence(timeout: 3) ? local : app.buttons["紀錄"].firstMatch
        XCTAssertTrue(history.waitForExistence(timeout: 8))
        history.tap()
        let compare = app.buttons["lite.timeline.compare"]
        XCTAssertTrue(compare.waitForExistence(timeout: 5))
        compare.tap()
        XCTAssertTrue(app.navigationBars["前後照片"].waitForExistence(timeout: 5))
        let previous = app.images["前次照片"]
        let latest = app.images["最近一次照片"]
        XCTAssertTrue(previous.waitForExistence(timeout: 8))
        XCTAssertTrue(latest.waitForExistence(timeout: 8))
        XCTAssertTrue(app.staticTexts["合成紙上標靶・右・膝後"].exists)
        XCTAssertFalse(app.staticTexts["獨立對照"].exists)
        if app.frame.width >= 650 {
            XCTAssertLessThan(previous.frame.midX, latest.frame.midX)
        } else {
            XCTAssertLessThan(previous.frame.midY, latest.frame.midY)
        }
        attach(app, "local-two-photo-comparison")
        app.buttons["完成"].tap()
        XCTAssertTrue(compare.waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["lite.timeline.export"].exists)
    }
    #endif

    #if LITE_QA_SAVED_FIXTURE || LITE_QA_EXTERNAL_FIXTURE
    @MainActor func testSelectedWoundTimelineOffersDateRangePhotosAndSystemSharing() throws {
        continueAfterFailure = false
        let app = application()
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
        let app = application()
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
        let app = application()
        app.launch()
        let gate = app.buttons["設定與資料撤回"]
        let settings = gate.waitForExistence(timeout: 3) ? gate : app.buttons["設定"].firstMatch
        XCTAssertTrue(settings.waitForExistence(timeout: 8))
        settings.tap()
        let entry = app.buttons["服務登入、訂閱與刪帳"]
        let missing = app.staticTexts["此測試版本的服務網址尚未設定完成。您仍可使用本機紀錄。"]
        XCTAssertTrue(app.navigationBars["設定"].waitForExistence(timeout: 8))
        let appleManagement = app.descendants(matching: .any)["lite.billing.manage"].firstMatch
        #if LITE_QA_ORDINARY
        XCTAssertFalse(appleManagement.exists)
        XCTAssertFalse(entry.exists)
        XCTAssertFalse(missing.exists)
        #elseif LITE_QA_MISSING_BINDING
        XCTAssertTrue(appleManagement.isHittable)
        XCTAssertTrue(missing.waitForExistence(timeout: 8))
        XCTAssertFalse(entry.exists)
        #else
        XCTAssertTrue(appleManagement.isHittable, "Apple billing management must be reachable before device registration")
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
        XCTAssertTrue(app.staticTexts["lite.offer.captureRequirement"].exists)
        XCTAssertFalse(app.buttons["lite.offer.buy"].exists)
        XCTAssertFalse(app.buttons["恢復購買"].exists)
        XCTAssertFalse(app.buttons["核對未完成購買"].exists)
        XCTAssertFalse(app.staticTexts["nativePurchaseStatus"].exists)
        let continueSubscription = app.buttons["lite.offer.signIn"]
        for _ in 0..<4 where !continueSubscription.isHittable { app.swipeUp() }
        XCTAssertTrue(continueSubscription.isHittable)
        XCTAssertTrue(continueSubscription.isEnabled)
        attach(app, "subscription-sign-in-before-registration")
        continueSubscription.tap()
        // Simulator App Attest cannot register. The same page must explain
        // the unfinished step without opening an Apple purchase or hiding retry.
        let failure = app.staticTexts["lite.offer.preparationStatus"]
        let finished = XCTNSPredicateExpectation(predicate: NSPredicate(format: "label CONTAINS %@", "可能是連線或服務尚未就緒"), object: failure)
        XCTAssertEqual(XCTWaiter.wait(for: [finished], timeout: 15), .completed)
        XCTAssertTrue(continueSubscription.isEnabled)
        XCTAssertFalse(app.buttons["lite.offer.buy"].exists)
        attach(app, "subscription-registration-unavailable-no-purchase")
        for _ in 0..<4 where !appleManagement.isHittable { app.swipeUp() }
        XCTAssertTrue(appleManagement.isHittable)
        attach(app, "read-only-offer-before-registration")
        #endif
    }

    @MainActor func testPersonalQuotaHasItsOwnEntryWithoutResearchOrDeviceRegistration() throws {
        continueAfterFailure = false
        let app = application()
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

    /// Runs on a simulator without LiDAR or a service account. Exercise the
    /// real Settings switch and persisted preference without taking a photo.
    @MainActor func testResearchSwitchRequiresConfirmedConsentAndPersistsTheChoice() throws {
        continueAfterFailure = false
        let app = application()
        func openSettings() {
            app.launch()
            app.buttons["設定與資料撤回"].tap()
            XCTAssertTrue(app.navigationBars["設定"].waitForExistence(timeout: 5))
        }
        func reveal(_ element: XCUIElement) {
            for _ in 0..<8 where !element.isHittable { app.swipeUp() }
            XCTAssertTrue(element.isHittable)
        }
        let research = app.switches.matching(NSPredicate(format: "label CONTAINS %@", "供研究")).firstMatch
        func checkValue(_ value: String) {
            let changed = XCTNSPredicateExpectation(predicate: NSPredicate(format: "value == %@", value), object: research)
            XCTAssertEqual(XCTWaiter.wait(for: [changed], timeout: 5), .completed)
        }
        func toggle() {
            reveal(research)
            research.coordinate(withNormalizedOffset: CGVector(dx: 0.91, dy: 0.5)).tap()
        }
        openSettings()
        reveal(research)
        if research.value as? String == "1" { toggle() }
        toggle()
        let sheet = app.navigationBars["WoundAILite"]
        XCTAssertTrue(sheet.waitForExistence(timeout: 5), "The switch enabled uploads without the consent screen")
        // Closing the app mid-review must not apply the unconfirmed choice.
        app.terminate()
        openSettings()
        reveal(research)
        checkValue("0")
        toggle()
        XCTAssertTrue(sheet.waitForExistence(timeout: 5))
        let decline = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "不同意")).firstMatch
        reveal(decline)
        decline.tap()
        XCTAssertTrue(app.navigationBars["設定"].waitForExistence(timeout: 5))
        checkValue("0")
        toggle()
        XCTAssertTrue(sheet.waitForExistence(timeout: 5))
        let agree = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "同意研究上傳")).firstMatch
        reveal(agree)
        agree.tap()
        XCTAssertTrue(app.navigationBars["設定"].waitForExistence(timeout: 5))
        checkValue("1")
        app.terminate()
        openSettings()
        reveal(research)
        checkValue("1")
        toggle()
        checkValue("0")
        app.terminate()
        openSettings()
        reveal(research)
        checkValue("0")
    }

    @MainActor private func attach(_ app: XCUIApplication, _ name: String) {
        let item = XCTAttachment(screenshot: app.screenshot())
        item.name = name
        item.lifetime = .keepAlways
        add(item)
    }
}
