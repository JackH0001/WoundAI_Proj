import XCTest

/// Use the isolated sponsorship host (not the TestFlight bundle). It injects
/// synthetic eligibility and intercepts openURL; production card/model are used.
final class SponsorshipUITests: XCTestCase {
    @MainActor func testEnglishConfirmationCancelHideAndRestart() throws { try exercise(language: "en") }
    @MainActor func testChineseConfirmationCancelHideAndRestart() throws { try exercise(language: "zh-Hant") }
    @MainActor func testPaidAndUnavailablePlanHaveNoAdOrLink() {
        for mode in ["plus", "unavailable"] {
            let app = XCUIApplication(); app.launchArguments = ["--sponsor-mode", mode]
            app.launch(); XCTAssertTrue(app.staticTexts["qa.ready"].waitForExistence(timeout: 8))
            XCTAssertFalse(app.staticTexts["lite.sponsor.label"].exists)
            XCTAssertFalse(app.buttons["lite.sponsor.open"].exists)
            XCTAssertEqual(app.staticTexts["qa.openCount"].label, "0")
            app.terminate()
        }
    }
    @MainActor private func exercise(language: String) throws {
        continueAfterFailure = false
        let en = language == "en", app = XCUIApplication()
        app.launchArguments = ["--sponsor-mode", "free", "-AppleLanguages", "(\(language))", "-AppleLocale", en ? "en_US" : "zh_Hant_TW"]
        app.launch(); XCTAssertTrue(app.staticTexts["qa.ready"].waitForExistence(timeout: 8))
        let label = app.staticTexts["lite.sponsor.label"]
        XCTAssertTrue(label.waitForExistence(timeout: 5)); XCTAssertEqual(label.label, en ? "Advertisement · Sponsor" : "廣告・品牌贊助")
        XCTAssertEqual(app.staticTexts["qa.openCount"].label, "0")
        let capture = XCTAttachment(screenshot: app.screenshot()); capture.name = language + "-sponsor-card"; capture.lifetime = .keepAlways; add(capture)
        app.buttons["lite.sponsor.open"].tap()
        let cancel = app.buttons[en ? "Cancel" : "取消"].firstMatch
        XCTAssertTrue(cancel.waitForExistence(timeout: 5))
        XCTAssertTrue(app.alerts.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "https://example.com/sponsor")).firstMatch.exists)
        let dialog = XCTAttachment(screenshot: app.screenshot()); dialog.name = language + "-sponsor-confirmation"; dialog.lifetime = .keepAlways; add(dialog)
        cancel.tap()
        XCTAssertEqual(app.staticTexts["qa.openCount"].label, "0")
        app.buttons["lite.sponsor.open"].tap()
        let proceed = app.buttons[en ? "Continue to sponsor website" : "繼續前往品牌網站"].firstMatch
        XCTAssertTrue(proceed.waitForExistence(timeout: 5)); proceed.tap()
        XCTAssertTrue(XCTWaiter.wait(for: [XCTNSPredicateExpectation(predicate: NSPredicate(format: "label == '1'"), object: app.staticTexts["qa.openCount"])], timeout: 5) == .completed)
        XCTAssertEqual(app.staticTexts["qa.lastURL"].label, "https://example.com/sponsor")
        XCTAssertTrue(app.buttons["lite.sponsor.report"].exists)
        app.buttons["lite.sponsor.hide"].tap(); XCTAssertFalse(label.exists)
        app.terminate(); app.launch(); XCTAssertTrue(app.staticTexts["qa.ready"].waitForExistence(timeout: 8))
        XCTAssertFalse(label.exists); XCTAssertEqual(app.staticTexts["qa.openCount"].label, "0")
        app.terminate()
    }
}
