import XCTest
@testable import WoundLite

final class LiteGuideCopyTests: XCTestCase {
    private let en = Locale(identifier: "en_US")
    private let zh = Locale(identifier: "zh_Hant_TW")

    func testBothReviewedTablesArePackagedWithMatchingArguments() throws {
        func table(_ language: String) throws -> [String: String] {
            let url = try XCTUnwrap(Bundle.main.url(forResource: "LiteGuide", withExtension: "strings", subdirectory: nil, localization: language))
            return try XCTUnwrap(PropertyListSerialization.propertyList(from: Data(contentsOf: url), format: nil) as? [String: String])
        }
        let a = try table("en"), b = try table("zh-Hant")
        XCTAssertEqual(a.count, 56); XCTAssertEqual(Set(a.keys), Set(b.keys))
        for (key, value) in a {
            XCTAssertEqual(b[key], key); XCTAssertFalse(value.isEmpty)
            XCTAssertEqual(key.components(separatedBy: "%@").count, value.components(separatedBy: "%@").count)
            XCTAssertFalse(value.unicodeScalars.contains { (0x4E00...0x9FFF).contains($0.value) }, key)
        }
    }

    func testLanguageChangesAnswersButNeverTopicIDsOrAvailableFeatures() {
        for personal in [false, true] {
            let a = LiteHelpTopic.topics(personal: personal, locale: en)
            let b = LiteHelpTopic.topics(personal: personal, locale: zh)
            XCTAssertEqual(a.count, personal ? 11 : 7); XCTAssertEqual(a.map(\.id), b.map(\.id))
            for (english, chinese) in zip(a, b) {
                XCTAssertEqual(english.terms, chinese.terms)
                for value in [english.title, english.path, english.steps] {
                    XCTAssertFalse(value.unicodeScalars.contains { (0x4E00...0x9FFF).contains($0.value) }, english.id)
                }
                XCTAssertNotEqual(english.title, chinese.title)
            }
            XCTAssertEqual(LiteHelpLookup.validated("deletion", topics: a), personal ? .topic("deletion") : .noMatch)
            XCTAssertEqual(LiteHelpLookup.validated("subscription", topics: b), personal ? .topic("subscription") : .noMatch)
        }
    }

    func testEnglishAndChineseSearchReachInstructionsWithoutModelOrAccount() {
        let cases = ["take a picture": "camera", "focus": "camera", "depth missing": "area",
                     "correct boundary": "outline", "compare photos": "photos", "export PDF": "report",
                     "end tracking": "wounds", "stop research uploads": "research", "ＰＤＦ": "report",
                     "我要修邊": "outline", "撤回研究": "research"]
        for locale in [en, zh] {
            for personal in [false, true] {
                let topics = LiteHelpTopic.topics(personal: personal, locale: locale)
                for (question, expected) in cases {
                    XCTAssertTrue(LiteHelpTopic.matches(question, in: topics).contains { $0.id == expected }, question)
                }
                XCTAssertTrue(LiteHelpTopic.matches("zxqv unmatched", in: topics).isEmpty)
            }
            let ordinary = LiteHelpTopic.topics(personal: false, locale: locale)
            let personal = LiteHelpTopic.topics(personal: true, locale: locale)
            for (question, expected) in ["remaining uses": "balance", "how many times can I use this today": "balance", "restore purchases": "subscription",
                                         "cancel renewal": "billing", "close account": "deletion"] {
                XCTAssertTrue(LiteHelpTopic.matches(question, in: ordinary).isEmpty)
                XCTAssertTrue(LiteHelpTopic.matches(question, in: personal).contains { $0.id == expected })
            }
        }
    }

    func testMenuPathsUseTheSameEnglishLabelsAsTheirDestinationScreens() throws {
        let topics = LiteHelpTopic.topics(personal: true, locale: en)
        let history = LiteHistoryCopy(locale: en), service = LiteServiceCopy.self
        let expected = ["report": history.text("匯出此傷口時間軸 PDF"),
                        "photos": history.text("比較最近兩次照片"), "outline": history.text("修正圈選並更新紀錄"),
                        "deletion": service.text("服務帳號與刪除", locale: en),
                        "subscription": service.text("服務登入、訂閱與刪帳", locale: en)]
        for (id, label) in expected { XCTAssertTrue(try XCTUnwrap(topics.first { $0.id == id }).path.contains(label)) }
    }

    func testQuotaTrackingAndResearchWithdrawalRemainDifferentFromBillingAndDeletion() throws {
        let topics = LiteHelpTopic.topics(personal: true, locale: en)
        func steps(_ id: String) throws -> String { try XCTUnwrap(topics.first { $0.id == id }).steps }
        let quota = try steps("balance")
        XCTAssertTrue(quota.contains("2 successful")); XCTAssertTrue(quota.contains("total of 10"))
        XCTAssertTrue(quota.contains("shared across all wounds")); XCTAssertTrue(quota.contains("PDF uses no recognitions"))
        XCTAssertTrue(try steps("wounds").contains("up to 5 per account"))
        XCTAssertTrue(try steps("wounds").contains("original timeline and PDF remain"))
        XCTAssertTrue(try steps("research").contains("stops future submissions"))
        XCTAssertTrue(try steps("research").contains("Records on your phone are retained"))
        XCTAssertTrue(try steps("deletion").contains("requests withdrawal"))
        XCTAssertTrue(try steps("deletion").contains("Manage Apple renewal separately"))
        XCTAssertTrue(try steps("subscription").contains("Signing in does not charge"))
        XCTAssertTrue(try steps("subscription").contains("do not purchase again"))
    }

    func testModelInstructionsStayWithinAvailableTopicsAndMedicalJudgmentIsExcluded() {
        for locale in [en, zh] {
            let topics = LiteHelpTopic.topics(personal: false, locale: locale)
            let instructions = LiteHelpLookup.instructions(topics: topics, locale: locale)
            for topic in topics { XCTAssertTrue(instructions.contains(topic.id + " = " + topic.title)) }
            XCTAssertFalse(instructions.contains("deletion =")); XCTAssertFalse(instructions.contains("subscription ="))
            XCTAssertTrue(instructions.contains(locale == en ? "medical judgment" : "醫療判斷"))
            XCTAssertTrue(instructions.contains(locale == en ? "Output only one topic ID" : "只輸出一個主題代碼"))
            for result in ["report\nBuy now", "report,billing", "infection", "https://example.org", "deletion"] {
                XCTAssertEqual(LiteHelpLookup.validated(result, topics: topics), .noMatch)
            }
        }
    }

    func testModelLocaleAndFallbackMatchTheDisplayedGuideLanguage() {
        XCTAssertEqual(LiteGuideCopy(locale: en).modelLocale.identifier, "en")
        XCTAssertEqual(LiteGuideCopy(locale: Locale(identifier: "en_GB")).modelLocale.identifier, "en")
        XCTAssertEqual(LiteGuideCopy(locale: zh).modelLocale.identifier, "zh-Hant")
        let fallback = LiteGuideCopy(locale: Locale(identifier: "id_ID"))
        XCTAssertEqual(fallback.modelLocale.identifier, "zh-Hant")
        XCTAssertEqual(fallback.text("操作指南"), "操作指南")
        let version = "44 %@ %s\n(custom)"
        XCTAssertEqual(LiteGuideCopy(locale: en).format("來源：WoundAILite %@ 內建操作指南", version), "Source: built-in WoundAILite " + version + " operation guide")
        XCTAssertEqual(LiteGuideCopy(locale: en).text("unrecognized detail %@"), "unrecognized detail %@")
    }
}
