import XCTest
@testable import WoundLite

final class LiteServiceCopyTests: XCTestCase {
    private let en = Locale(identifier: "en_US")
    private let zh = Locale(identifier: "zh_Hant_TW")

    private func table(_ language: String) throws -> [String: String] {
        let url = try XCTUnwrap(Bundle.main.url(forResource: "LiteService", withExtension: "strings", subdirectory: nil, localization: language))
        return try XCTUnwrap(PropertyListSerialization.propertyList(from: Data(contentsOf: url), format: nil) as? [String: String])
    }

    func testBothTablesAreInTheAppAndHaveMatchingKeys() throws {
        let english = try table("en"), chinese = try table("zh-Hant")
        XCTAssertEqual(Set(english.keys), Set(chinese.keys))
        XCTAssertGreaterThan(english.count, 160)
        for (key, value) in chinese { XCTAssertEqual(key, value, "The original displayed source must be preserved") }
    }

    func testEnglishTranslationsDoNotContainUntranslatedChineseOrChangePlaceholders() throws {
        for (key, value) in try table("en") {
            XCTAssertFalse(value.isEmpty)
            XCTAssertFalse(value.unicodeScalars.contains { (0x4E00...0x9FFF).contains($0.value) }, key)
            XCTAssertEqual(key.components(separatedBy: "%@").count, value.components(separatedBy: "%@").count, key)
        }
    }

    func testSubscriptionDisclosuresPreserveQuotaWoundLimitAndResearchChoice() {
        let quota = LiteServiceCopy.text("免費方案每日可成功辨識 2 次。Plus 每日共 10 次，包含當日已使用的次數。", locale: en)
        XCTAssertTrue(quota.contains("2 successful")); XCTAssertTrue(quota.contains("total of 10"))
        let wounds = LiteServiceCopy.text("免費可同時追蹤 1 處傷口，Plus 每帳戶最多 5 處。每日辨識次數由各處傷口合計。結束追蹤可換另一處，舊時間軸仍可查看及匯出 PDF。", locale: en)
        XCTAssertTrue(wounds.contains("up to 5 per account")); XCTAssertTrue(wounds.contains("shared across all wounds"))
        XCTAssertEqual(LiteServiceCopy.text("研究上傳由您另行決定。", locale: en), "Research uploads are your separate choice.")
    }

    func testAppleFormattedPriceIsAnOpaqueArgument() {
        for price in ["$60.00", "US$1.99", "1,99 €", "%@ $60"] {
            XCTAssertEqual(LiteServiceCopy.format("%@／月", locale: en, price), price + "/month")
            XCTAssertEqual(LiteServiceCopy.format("%@／月", locale: zh, price), price + "／月")
            XCTAssertEqual(LiteServiceCopy.format("以 %@／月訂閱", locale: en, price), "Subscribe for " + price + "/month")
        }
    }

    func testUnknownOrUntranslatedContentFallsBackWithoutRewriting() {
        for value in ["", "wound-\(UUID().uuidString)", "未定義的新訊息 100%", "A user's own name"] {
            XCTAssertEqual(LiteServiceCopy.text(value, locale: en), value)
        }
        XCTAssertEqual(LiteServiceCopy.text("刪除帳號", locale: Locale(identifier: "fr_FR")), "刪除帳號")
    }

    func testComposedStatusKeepsOriginalTextAndDoesNotHideANewerFailure() {
        let summary = LiteServiceCopy.Message(pieces: [.init("雲端已受理，帳號服務已關閉。"),
            .init("Apple 授權撤銷已確認。"), .init("帳號資料清理尚未開始，尚未完成刪除；手機紀錄仍保留。")])
        XCTAssertEqual(summary.source, "雲端已受理，帳號服務已關閉。Apple 授權撤銷已確認。帳號資料清理尚未開始，尚未完成刪除；手機紀錄仍保留。")
        XCTAssertTrue(summary.render(matching: summary.source, locale: en).contains("deletion is not complete"))
        let failure = "無法核對原刪帳要求；已停止新增要求。請保留紀錄，稍後再核對。"
        XCTAssertEqual(summary.render(matching: failure, locale: en), LiteServiceCopy.text(failure, locale: en))
        XCTAssertFalse(summary.render(matching: failure, locale: en).contains("accepted"))
    }

    func testPreparationDoesNotClaimPaymentOrEnableResearch() {
        let status = LiteServiceCopy.Message(pieces: [.init("服務帳號登入與研究同意分開處理。"),
            .init(" 尚未付款，也未開啟 Apple 購買。")])
        XCTAssertTrue(status.source.hasSuffix(" 尚未付款，也未開啟 Apple 購買。"))
        XCTAssertTrue(status.render(locale: en).contains("No payment has been made"))
        XCTAssertTrue(status.render(locale: en).contains("Apple purchase has not opened"))
    }

    func testProgressTranslationDoesNotChangeCodableContractOrCompletionClaims() throws {
        let progress = LiteDispositionProgress(v: 1, catalog_sha256: LiteDispositionProgress.sharedCatalog,
            revision: 2, service_total: 7, service_sealed: 3, service_state: "pending", grant_key_state: "backup_pending",
            blocked_lanes: ["service_results"], pending_lanes: LiteDispositionProgress.sharedPending)
        try progress.validate(apple: "confirmed")
        let encoder = JSONEncoder(); encoder.outputFormatting = .sortedKeys
        let before = try encoder.encode(progress)
        XCTAssertEqual(progress.serviceText, "服務結果暫待核對（已確認 3/7）")
        XCTAssertEqual(progress.serviceCopy.render(locale: en), "Service results await verification (3/7 confirmed)")
        XCTAssertTrue(LiteServiceCopy.text(progress.keyText, locale: en).contains("still need processing"))
        XCTAssertEqual(before, try encoder.encode(progress))
        XCTAssertFalse(String(decoding: before, as: UTF8.self).contains("Service results"))
    }
}
