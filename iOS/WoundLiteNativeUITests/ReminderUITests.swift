import XCTest

/// Run each case with a fresh, isolated QA bundle identifier. No test hook
/// replaces the permission dialog or UNUserNotificationCenter readback.
final class ReminderUITests: XCTestCase {
    @MainActor func testEnglishSystemPermissionScheduleRestartAndDisable() throws { try exercise(language: "en", permit: true) }
    @MainActor func testChineseSystemPermissionScheduleRestartAndDisable() throws { try exercise(language: "zh-Hant", permit: true) }
    @MainActor func testDeniedPermissionKeepsReminderOffWithoutRepeatedDialog() throws { try exercise(language: "en", permit: false) }

    @MainActor private func exercise(language: String, permit: Bool) throws {
        continueAfterFailure = false
        let en = language == "en", app = XCUIApplication()
        app.launchArguments += ["-AppleLanguages", "(\(language))", "-AppleLocale", en ? "en_US" : "zh_Hant_TW"]
        let off = en ? "Reminders are off." : "提醒已關閉。"
        let scheduled = en ? "Daily reminder scheduled using this device's local time." : "已設定每天提醒；時間以這部裝置的當地時間為準。"
        let denied = en ? "Notifications are not allowed. Enable them in iOS Settings, then return here and save the reminder." : "系統通知未允許。請到 iOS 設定允許通知，再回來儲存提醒。"
        let system = XCUIApplication(bundleIdentifier: "com.apple.springboard")
        func open() {
            app.launch()
            let gate = app.buttons[en ? "Settings and data withdrawal" : "設定與資料撤回"]
            let settings = gate.waitForExistence(timeout: 3) ? gate : app.buttons[en ? "Settings" : "設定"].firstMatch
            XCTAssertTrue(settings.waitForExistence(timeout: 8)); settings.tap()
            let entry = app.buttons["lite.reminder.open"]
            for _ in 0..<4 where !entry.isHittable { app.swipeUp() }
            XCTAssertTrue(entry.isHittable); entry.tap()
            XCTAssertTrue(app.staticTexts["lite.reminder.status"].waitForExistence(timeout: 5))
        }
        func state(_ expected: String) {
            let save = app.buttons["lite.reminder.save"]
            XCTAssertTrue(XCTWaiter.wait(for: [XCTNSPredicateExpectation(predicate: NSPredicate(format: "enabled == true"), object: save)], timeout: 8) == .completed)
            let element = app.staticTexts["lite.reminder.status"]
            XCTAssertTrue(NSPredicate(format: "label == %@", expected).evaluate(with: element) ||
                XCTWaiter.wait(for: [XCTNSPredicateExpectation(predicate: NSPredicate(format: "label == %@", expected), object: element)], timeout: 8) == .completed, element.label)
        }
        func screenshot(_ name: String) {
            let item = XCTAttachment(screenshot: app.screenshot()); item.name = language + "-" + name; item.lifetime = .keepAlways; add(item)
        }
        open(); state(off)
        XCTAssertFalse(system.alerts.firstMatch.exists)
        screenshot("reminder-off")
        app.buttons["lite.reminder.save"].tap()
        let alert = system.alerts.firstMatch
        XCTAssertTrue(alert.waitForExistence(timeout: 8), "Use a fresh QA bundle; only the explicit enable action may request permission")
        let labels = permit ? ["Allow", "允許"] : ["Don’t Allow", "Don't Allow", "不允許"]
        let choice = alert.buttons.matching(NSPredicate(format: "label IN %@", labels)).firstMatch
        XCTAssertTrue(choice.exists); choice.tap()
        state(permit ? scheduled : denied); screenshot(permit ? "reminder-confirmed" : "reminder-denied")
        app.terminate(); open(); state(permit ? scheduled : denied)
        app.buttons["lite.reminder.save"].tap()
        state(permit ? scheduled : denied)
        XCTAssertFalse(system.alerts.firstMatch.exists, "A repeated save must not show another permission dialog")
        app.buttons["lite.reminder.disable"].tap(); state(off)
        app.terminate(); open(); state(off)
        screenshot("reminder-off-after-relaunch"); app.terminate()
    }
}
