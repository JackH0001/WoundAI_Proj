import XCTest

final class InstitutionEntryUITests: XCTestCase {
    @MainActor
    func testDemoSwitchShowsNoGoogleAndReturnsToProtectedMMH() throws {
        let app = XCUIApplication(); app.launch()
        let open = app.buttons["OpenSyntheticReview"]
        if app.buttons["返回 MMH 機構服務"].waitForExistence(timeout: 2) { app.buttons["返回 MMH 機構服務"].tap() }
        XCTAssertTrue(open.waitForExistence(timeout: 10)); open.tap()
        XCTAssertTrue(app.staticTexts["MedicalEnvironment"].waitForExistence(timeout: 5))
        XCTAssertEqual(app.staticTexts["MedicalEnvironment"].label, "合成示範環境")
        app.buttons["設定（後端連線・帳號・佇列健康度）"].tap()
        XCTAssertTrue(app.staticTexts.containing(NSPredicate(format: "label CONTAINS %@", "使用專屬 WoundAI 示範帳號")).firstMatch.waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons["MMHGoogleSignIn"].exists)
        XCTAssertTrue(app.buttons["儲存並登入"].isEnabled)
        XCTAssertFalse(app.textFields["位址"].exists)
        let screenshot = XCTAttachment(screenshot: app.screenshot()); screenshot.name = "Isolated demo login without Google"; screenshot.lifetime = .keepAlways; add(screenshot)
        app.buttons["返回"].tap(); app.buttons["返回 MMH 機構服務"].tap()
        XCTAssertEqual(app.staticTexts["MedicalEnvironment"].label, "MMH 機構服務")
        app.buttons["設定（後端連線・帳號・佇列健康度）"].tap()
        XCTAssertTrue(app.buttons["MMHGoogleSignIn"].waitForExistence(timeout: 5))
        for _ in 0..<6 { if app.buttons["儲存並登入"].exists { break }; scrollSettings(app) }
        XCTAssertFalse(app.buttons["儲存並登入"].isEnabled)
    }

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
        let toggle = app.switches["確認上傳時輸出影像與結果"]
        for _ in 0..<10 {
            if toggle.exists && toggle.isHittable { break }
            scrollSettings(app)
        }
        add(XCTAttachment(screenshot: app.screenshot()))
        XCTAssertTrue(toggle.waitForExistence(timeout: 5))
        // Normalize persistent simulator UI state; default-off itself is verified by the unit test.
        if toggle.value as? String == "1" { toggle.coordinate(withNormalizedOffset: CGVector(dx: 0.9, dy: 0.5)).tap() }
        XCTAssertEqual(toggle.value as? String, "0")
        toggle.coordinate(withNormalizedOffset: CGVector(dx: 0.9, dy: 0.5)).tap(); XCTAssertEqual(toggle.value as? String, "1")
        let folder = app.buttons["選擇輸出資料夾…"]
        for _ in 0..<4 { if folder.exists && folder.isHittable { break }; scrollSettings(app) }
        XCTAssertTrue(folder.waitForExistence(timeout: 5)); folder.tap()
        let cancel = app.buttons["Cancel"].firstMatch
        if !cancel.waitForExistence(timeout: 2) { app.buttons["瀏覽"].tap() }
        XCTAssertTrue(cancel.waitForExistence(timeout: 5)); cancel.tap()
        toggle.coordinate(withNormalizedOffset: CGVector(dx: 0.9, dy: 0.5)).tap(); XCTAssertEqual(toggle.value as? String, "0")
        let export = app.buttons["選擇副本並匯出…"]
        for _ in 0..<4 {
            if export.exists && export.isHittable { break }
            scrollSettings(app)
        }
        XCTAssertTrue(export.exists)
        add(XCTAttachment(screenshot: app.screenshot()))
    }

    /// Reference simulator: run before the persisted-folder hosted test in a separate invocation.
    @MainActor
    func testSelectLocalFolderForRestartValidation() throws {
        let app = XCUIApplication(); app.launch()
        app.buttons["設定（後端連線・帳號・佇列健康度）"].tap()
        let toggle = app.switches["確認上傳時輸出影像與結果"]
        for _ in 0..<10 { if toggle.exists && toggle.isHittable { break }; scrollSettings(app) }
        XCTAssertTrue(toggle.waitForExistence(timeout: 5))
        if toggle.value as? String == "0" { toggle.coordinate(withNormalizedOffset: CGVector(dx: 0.9, dy: 0.5)).tap() }
        let folder = app.buttons["選擇輸出資料夾…"]
        for _ in 0..<4 { if folder.exists && folder.isHittable { break }; scrollSettings(app) }
        folder.tap()
        XCTAssertTrue(app.buttons["瀏覽"].waitForExistence(timeout: 5)); app.buttons["瀏覽"].tap()
        let open = app.buttons["打開"]
        if !open.exists {
            let local = app.cells["DOC.sidebar.item.我的iPhone"]
            XCTAssertTrue(local.waitForExistence(timeout: 5)); local.tap()
            let own = app.cells.containing(NSPredicate(format: "label CONTAINS %@", "WoundAI MMHPS")).firstMatch
            if own.waitForExistence(timeout: 5) { own.tap() }
        }
        XCTAssertTrue(open.waitForExistence(timeout: 5)); XCTAssertTrue(open.isEnabled); open.tap()
        let selected = app.staticTexts.containing(NSPredicate(format: "label BEGINSWITH %@ AND NOT label CONTAINS %@", "輸出位置：", "App 私密空間")).firstMatch
        XCTAssertTrue(selected.waitForExistence(timeout: 5))
        app.terminate(); app.launch()
        app.buttons["設定（後端連線・帳號・佇列健康度）"].tap()
        for _ in 0..<10 { if selected.exists && selected.isHittable { break }; scrollSettings(app) }
        XCTAssertTrue(selected.exists)
        let shot = XCTAttachment(screenshot: app.screenshot()); shot.name="Output folder survives app restart"; shot.lifetime = .keepAlways; add(shot)
        app.terminate()
    }

    @MainActor
    func testPrivateEntryShowsGoogleSignInButBlocksCredentialsUntilIAPVerified() throws {
        let app = XCUIApplication()
        app.launch()
        let settings = app.buttons["設定（後端連線・帳號・佇列健康度）"]
        XCTAssertTrue(settings.waitForExistence(timeout: 10))
        settings.tap()
        XCTAssertTrue(app.staticTexts.containing(NSPredicate(format: "label CONTAINS %@", "https://woundai-backend-mmhps20261007")).firstMatch.waitForExistence(timeout: 5))
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
