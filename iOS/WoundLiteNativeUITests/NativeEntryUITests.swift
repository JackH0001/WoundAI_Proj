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
        try verifyUnmatchedHelp(language: "zh-Hant")
    }

    @MainActor func testEnglishUnmatchedHelpKeepsOfflineTopicsAndDoesNotRetainTheQuestion() throws {
        try verifyUnmatchedHelp(language: "en")
    }

    @MainActor private func verifyUnmatchedHelp(language: String) throws {
        continueAfterFailure = false
        let english = language == "en", app = application(language: language)
        func openGuide() {
            app.launch()
            let gate = app.buttons[english ? "Settings and data withdrawal" : "設定與資料撤回"]
            let settings = gate.waitForExistence(timeout: 3) ? gate : app.buttons[english ? "Settings" : "設定"].firstMatch
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
        XCTAssertTrue(app.staticTexts[english ? "No matching keywords. Choose a topic below." : "沒有相符的關鍵字，可從下方挑選主題。"].waitForExistence(timeout: 5))
        let report = app.buttons["lite.help.topic.report"]
        for _ in 0..<6 where !report.isHittable { app.swipeUp() }
        XCTAssertTrue(report.isHittable)
        report.tap()
        XCTAssertTrue(app.staticTexts[english ? "Records → Select a wound → Export this wound timeline as PDF" : "紀錄 → 選一處傷口 → 匯出此傷口時間軸 PDF"].waitForExistence(timeout: 5))
        attach(app, language + "-unmatched-help-offline-report")
        app.terminate()
        openGuide()
        XCTAssertTrue(query.waitForExistence(timeout: 5))
        let value = query.value as? String ?? ""
        XCTAssertFalse(value.contains("zxqv"), "An operation question must not become saved app history")
        attach(app, language + "-help-query-not-retained-after-relaunch")
    }

    #if !LITE_QA_ORDINARY && !LITE_QA_MISSING_BINDING
    @MainActor func testEnglishGuideSeparatesResearchRestoreAndAccountDeletionWithoutTakingActions() throws {
        continueAfterFailure = false
        let app = application(language: "en")
        for (query, id, expected) in [("research", "research", "Records on your phone are retained"),
                                      ("restore purchases", "subscription", "Signing in does not charge"),
                                      ("cancel renewal", "billing", "cancel renewal"),
                                      ("close account", "deletion", "requests withdrawal")] {
            app.launch()
            let gate = app.buttons["Settings and data withdrawal"]
            let settings = gate.waitForExistence(timeout: 3) ? gate : app.buttons["Settings"].firstMatch
            XCTAssertTrue(settings.waitForExistence(timeout: 8)); settings.tap()
            let guide = app.buttons["lite.help.open"]; XCTAssertTrue(guide.waitForExistence(timeout: 5)); guide.tap()
            let field = app.textFields["lite.help.question"]; XCTAssertTrue(field.waitForExistence(timeout: 5))
            XCTAssertFalse((field.value as? String ?? "").contains("close account"))
            field.tap(); field.typeText(query)
            let topic = app.buttons["lite.help.topic." + id]; XCTAssertTrue(topic.waitForExistence(timeout: 5)); topic.tap()
            let steps = app.staticTexts["lite.help.steps"]; XCTAssertTrue(steps.waitForExistence(timeout: 5))
            XCTAssertTrue(steps.label.contains(expected))
            XCTAssertFalse(app.buttons["lite.offer.buy"].exists)
            attach(app, "en-guide-" + id)
            app.terminate()
        }
    }
    #endif

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
        try verifyHistory(language: "zh-Hant")
    }
    @MainActor func testEnglishWoundHistoryPhotosAndTrackingKeepOriginalNames() throws {
        try verifyHistory(language: "en")
    }
    @MainActor func testEnglishCaptureAndAccuracyUseReadOnlyCopy() throws {
        try verifyCaptureLanguage(language: "en")
    }
    @MainActor func testChineseCaptureAndAccuracyKeepExistingCopy() throws {
        try verifyCaptureLanguage(language: "zh-Hant")
    }
    @MainActor private func verifyCaptureLanguage(language: String) throws {
        continueAfterFailure = false
        let en = language == "en", app = application(language: language)
        app.launch()
        let local = app.buttons[en ? "View existing records" : "查看既有紀錄"]
        if local.waitForExistence(timeout: 4) { local.tap() }
        let measure = app.tabBars.buttons[en ? "Measure" : "量測"]
        XCTAssertTrue(measure.waitForExistence(timeout: 8)); measure.tap()
        XCTAssertTrue(app.buttons["lite.capture"].waitForExistence(timeout: 5))
        XCTAssertEqual(app.buttons["lite.capture"].label, en ? "Photograph wound" : "拍攝傷口")
        attach(app, language + "-capture-start")
        let settings = app.tabBars.buttons[en ? "Settings" : "設定"]
        XCTAssertTrue(settings.exists); settings.tap()
        let info = app.buttons[en ? "Accuracy and limitations" : "精確度與限制"]
        for _ in 0..<8 where !info.isHittable { app.swipeUp() }
        XCTAssertTrue(info.isHittable); info.tap()
        XCTAssertTrue(app.navigationBars[en ? "Accuracy and limitations" : "精確度與限制"].waitForExistence(timeout: 5))
        let precision = en
            ? "Two decimal places do not mean accuracy to 0.01 cm². Small changes may come from capture or outline differences rather than a change in the wound."
            : "顯示到小數點後兩位，不代表精確到 0.01 cm²。小幅數值變化可能來自拍攝或圈選差異，不一定是傷口真的改變。"
        XCTAssertTrue(app.staticTexts.matching(NSPredicate(format: "label == %@", precision)).firstMatch.exists)
        attach(app, language + "-capture-accuracy")
        let endText = en ? "This app provides a reference for personal records, not a medical diagnosis. Seek medical care if the wound worsens, you develop a fever, or drainage is heavy." : "本 App 提供個人紀錄參考，不提供醫療診斷。若傷口惡化、發燒或大量滲液，請尋求醫療協助。"
        let end = app.staticTexts.matching(NSPredicate(format: "label == %@", endText)).firstMatch
        for _ in 0..<10 where !end.isHittable { app.swipeUp() }
        XCTAssertTrue(end.isHittable)
        app.swipeUp() // Bring the final paragraph above the floating tab bar.
        XCTAssertTrue(end.isHittable)
        attach(app, language + "-capture-accuracy-limits")
    }
    @MainActor private func verifyHistory(language: String) throws {
        continueAfterFailure = false
        let english = language == "en", app = application(language: language)
        app.launch()
        let local = app.buttons[english ? "View existing records" : "查看既有紀錄"]
        let history = local.waitForExistence(timeout: 3) ? local : app.buttons[english ? "Records" : "紀錄"].firstMatch
        XCTAssertTrue(history.waitForExistence(timeout: 8))
        history.tap()
        let compare = app.buttons["lite.timeline.compare"]
        XCTAssertTrue(compare.waitForExistence(timeout: 5))
        compare.tap()
        XCTAssertTrue(app.navigationBars[english ? "Photo comparison" : "前後照片"].waitForExistence(timeout: 5))
        let previous = app.images[english ? "Previous photo" : "前次照片"]
        let latest = app.images[english ? "Latest photo" : "最近一次照片"]
        XCTAssertTrue(previous.waitForExistence(timeout: 8))
        XCTAssertTrue(latest.waitForExistence(timeout: 8))
        XCTAssertTrue(app.staticTexts[english ? "合成紙上標靶, Right, Back of knee" : "合成紙上標靶・右・膝後"].exists)
        XCTAssertFalse(app.staticTexts["獨立對照"].exists)
        if app.frame.width >= 650 {
            XCTAssertLessThan(previous.frame.midX, latest.frame.midX)
        } else {
            XCTAssertLessThan(previous.frame.midY, latest.frame.midY)
        }
        attach(app, language + "-local-two-photo-comparison")
        app.buttons[english ? "Done" : "完成"].tap()
        XCTAssertTrue(compare.waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["lite.timeline.export"].exists)
        let row = app.buttons["lite.record.qa-photo-1"]
        for _ in 0..<5 where !row.isHittable { app.swipeUp() }
        XCTAssertTrue(row.isHittable); row.tap()
        XCTAssertTrue(app.navigationBars[english ? "Measurement record" : "量測紀錄"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons[english ? "Reset view" : "重設檢視"].exists)
        XCTAssertTrue(app.staticTexts[english ? "Current LiDAR surface area" : "本版 LiDAR 表面積"].exists)
        XCTAssertTrue(app.staticTexts[english ? "Saved only on this device" : "僅存於此手機"].exists)
        XCTAssertFalse(app.buttons["lite.revision.retry"].exists)
        attach(app, language + "-local-record-detail")
        app.buttons[english ? "Close" : "關閉"].tap()
        let tracking = app.buttons[english ? "Manage tracking" : "管理追蹤"]
        XCTAssertTrue(tracking.waitForExistence(timeout: 5)); tracking.tap()
        XCTAssertTrue(app.navigationBars[english ? "Manage wound tracking" : "管理追蹤傷口"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts[english ? "Free accounts can track 1 wound at a time; Plus allows up to 5 per account." : "免費可追蹤 1 處，Plus 每帳戶最多 5 處。"].exists)
        attach(app, language + "-local-tracking")
        app.buttons[english ? "Close" : "關閉"].tap()
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

    #if LITE_QA_EDITOR_FIXTURE
    @MainActor func testEnglishCameraPermissionDescriptionIsLocalized() throws {
        continueAfterFailure = false
        let app = application(language: "en"); app.launch()
        XCTAssertTrue(app.buttons["qa.camera"].waitForExistence(timeout: 8)); app.buttons["qa.camera"].tap()
        let system = XCUIApplication(bundleIdentifier:"com.apple.springboard")
        let message = "Take photos and LiDAR depth to outline and estimate area. Research upload requires separate consent."
        let description = system.staticTexts.matching(NSPredicate(format:"label == %@",message)).firstMatch
        XCTAssertTrue(description.waitForExistence(timeout: 8))
        attach(app,"en-system-camera-permission")
        let deny = system.alerts.buttons.matching(NSPredicate(format:"label IN %@",["不允許","Don’t Allow","Don't Allow"])).firstMatch
        XCTAssertTrue(deny.exists); deny.tap()
        let error = "Camera access is not allowed. Enable it in iOS Settings → WoundAILite."
        XCTAssertTrue(app.staticTexts.matching(NSPredicate(format:"label == %@",error)).firstMatch.waitForExistence(timeout: 5))
        app.buttons["camera.cancel"].tap()
        XCTAssertEqual(app.staticTexts["qa.captures"].label,"0"); app.terminate()
    }
    @MainActor func testEnglishCameraEditorControlsPreserveOutline() throws { try verifyCameraEditor(language: "en") }
    @MainActor func testChineseCameraEditorControlsPreserveOutline() throws { try verifyCameraEditor(language: "zh-Hant") }
    @MainActor private func verifyCameraEditor(language: String) throws {
        continueAfterFailure = false
        let en = language == "en", app = application(language: language)
        app.launch()
        let status = app.staticTexts["qa.status"]
        XCTAssertTrue(app.buttons["qa.empty"].waitForExistence(timeout: 8))
        app.buttons["qa.empty"].tap()
        let done = app.buttons["editor.done"], cancel = app.buttons["editor.cancel"]
        XCTAssertTrue(done.waitForExistence(timeout: 5)); XCTAssertFalse(done.isEnabled)
        XCTAssertEqual(done.label, en ? "Done outlining" : "完成圈選")
        attach(app, language + "-empty-editor")
        cancel.tap(); XCTAssertTrue(status.waitForExistence(timeout: 5)); XCTAssertEqual(status.label, "cancelled")
        app.buttons["qa.edit"].tap()
        let preview = app.buttons["editor.originalPreview"]
        XCTAssertTrue(preview.waitForExistence(timeout: 5)); preview.tap()
        XCTAssertEqual(preview.value as? String, en ? "Original photo; painting paused" : "原圖，暫停塗抹")
        XCTAssertFalse(app.sliders.firstMatch.isEnabled)
        let canvas = app.otherElements["editor.canvas"]
        XCTAssertTrue(canvas.exists)
        canvas.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        attach(app, language + "-original-preview")
        preview.tap()
        let undo = app.buttons[en ? "Undo" : "復原"]
        XCTAssertFalse(undo.isEnabled)
        app.buttons[en ? "Erase" : "擦除"].tap()
        canvas.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        XCTAssertTrue(undo.isEnabled)
        attach(app, language + "-edited-outline")
        undo.tap(); XCTAssertFalse(undo.isEnabled)
        done.tap(); XCTAssertTrue(status.waitForExistence(timeout: 5)); XCTAssertEqual(status.label, "unchanged")
        app.buttons["qa.edit"].tap(); XCTAssertTrue(done.waitForExistence(timeout: 5))
        app.buttons[en ? "Erase" : "擦除"].tap()
        canvas.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        XCTAssertTrue(undo.isEnabled); done.tap()
        XCTAssertTrue(status.waitForExistence(timeout: 5)); XCTAssertEqual(status.label, "changed")
        app.buttons["qa.camera"].tap()
        let cameraCancel = app.buttons["camera.cancel"]
        XCTAssertTrue(cameraCancel.waitForExistence(timeout: 8))
        XCTAssertFalse(app.buttons["camera.shutter"].isEnabled)
        let noDepth = en ? "No LiDAR depth is available. Sticker-free area estimation is unavailable." : "目前沒有可用 LiDAR 深度；本次無法提供免貼紙面積估算。"
        XCTAssertTrue(app.staticTexts.matching(NSPredicate(format:"label == %@",noDepth)).firstMatch.exists)
        let noCamera = en ? "No camera is available. Try on a device with a camera. Cancel keeps the previous photo and outline." : "找不到可用相機。請在有相機的裝置重試；取消會保留先前的照片與圈選。"
        XCTAssertTrue(app.staticTexts.matching(NSPredicate(format:"label == %@",noCamera)).firstMatch.waitForExistence(timeout: 8))
        attach(app, language + "-unavailable-camera")
        cameraCancel.tap()
        XCTAssertTrue(status.waitForExistence(timeout: 5)); XCTAssertEqual(status.label, "camera-cancelled")
        XCTAssertEqual(app.staticTexts["qa.completions"].label,"2")
        XCTAssertEqual(app.staticTexts["qa.captures"].label,"0")
        app.terminate()
    }
    #endif

    @MainActor private func attach(_ app: XCUIApplication, _ name: String) {
        let item = XCTAttachment(screenshot: app.screenshot())
        item.name = name
        item.lifetime = .keepAlways
        add(item)
    }
}
