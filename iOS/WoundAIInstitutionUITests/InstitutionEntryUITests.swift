import XCTest

final class InstitutionEntryUITests: XCTestCase {
    @MainActor private func scrollSettings(_ app: XCUIApplication) {
        let list = app.collectionViews.firstMatch
        list.coordinate(withNormalizedOffset: CGVector(dx: 0.94, dy: 0.83))
            .press(forDuration: 0.1, thenDragTo: list.coordinate(withNormalizedOffset: CGVector(dx: 0.94, dy: 0.3)))
    }

    @MainActor
    func testAdministrativeExportIsOptInAndPrivate() throws {
        let app = XCUIApplication()
        app.launch()
        let settings = app.buttons["設定（後端連線・帳號・佇列健康度）"]
        XCTAssertTrue(settings.waitForExistence(timeout: 10)); settings.tap()
        let toggle = app.switches["建立行政影像與結果副本"]
        for _ in 0..<10 {
            if toggle.exists && toggle.isHittable { break }
            scrollSettings(app)
        }
        add(XCTAttachment(screenshot: app.screenshot()))
        XCTAssertTrue(toggle.waitForExistence(timeout: 5))
        XCTAssertEqual(toggle.value as? String, "0")
        toggle.coordinate(withNormalizedOffset: CGVector(dx: 0.9, dy: 0.5)).tap(); XCTAssertEqual(toggle.value as? String, "1")
        toggle.coordinate(withNormalizedOffset: CGVector(dx: 0.9, dy: 0.5)).tap(); XCTAssertEqual(toggle.value as? String, "0")
        let export = app.buttons["選擇副本並匯出…"]
        for _ in 0..<4 {
            if export.exists && export.isHittable { break }
            scrollSettings(app)
        }
        XCTAssertTrue(export.exists)
        add(XCTAttachment(screenshot: app.screenshot()))
    }

    @MainActor
    func testPrivateEntryShowsGoogleSignInButBlocksCredentialsUntilIAPVerified() throws {
        let app = XCUIApplication()
        app.launch()
        let settings = app.buttons["設定（後端連線・帳號・佇列健康度）"]
        XCTAssertTrue(settings.waitForExistence(timeout: 10))
        settings.tap()
        XCTAssertTrue(app.staticTexts.containing(NSPredicate(format: "label CONTAINS %@", "candidate---woundai-backend-mmhps20261007")).firstMatch.waitForExistence(timeout: 5))
        let explanation = app.buttons["為什麼需要兩次登入？"]
        XCTAssertTrue(explanation.waitForExistence(timeout: 5))
        explanation.tap()
        XCTAssertTrue(app.staticTexts.containing(NSPredicate(format: "label CONTAINS %@", "機構管理員在 Google Cloud IAP 管理")).firstMatch.exists)
        XCTAssertTrue(app.staticTexts.containing(NSPredicate(format: "label CONTAINS %@", "不會自動取得醫師權限")).firstMatch.exists)
        explanation.tap()
        let signIn = app.buttons["MMHGoogleSignIn"]
        XCTAssertTrue(signIn.waitForExistence(timeout: 5))
        XCTAssertTrue(signIn.isEnabled, "Google sign-in must have an attached presenter")
        let save = app.buttons["儲存並登入"]
        for _ in 0..<8 {
            if save.exists { break }
            scrollSettings(app)
        }
        add(XCTAttachment(screenshot: app.screenshot()))
        XCTAssertTrue(save.exists)
        XCTAssertFalse(app.buttons["登出 WoundAI 帳號"].exists)
        XCTAssertFalse(app.buttons["登出 Google 與 WoundAI"].exists)
        XCTAssertFalse(save.isEnabled, "Google/IAP verification must precede application credentials")
        XCTAssertFalse(app.textFields["位址"].exists, "Institution origin cannot be edited by a user")
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = "MMH Google entry - protected candidate"
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
