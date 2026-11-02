import XCTest
@testable import WoundLite

final class LiteOperationGuideTests: XCTestCase {
    func testEachTopicHasOneReviewedAnswerAndAnInAppPath() {
        for personal in [false, true] {
            let topics = LiteHelpTopic.topics(personal: personal)
            XCTAssertEqual(Set(topics.map(\.id)).count, topics.count)
            for topic in topics {
                XCTAssertFalse(topic.path.isEmpty)
                XCTAssertFalse(topic.steps.isEmpty)
                XCTAssertFalse(topic.terms.isEmpty)
                XCTAssertEqual(LiteHelpLookup.validated(topic.id, topics: topics), .topic(topic.id))
            }
        }
    }

    func testOrdinaryBuildDoesNotSuggestFeaturesOfThePersonalService() {
        let ordinary = LiteHelpTopic.topics(personal: false)
        let candidate = LiteHelpTopic.topics(personal: true)
        for id in ["balance", "subscription", "billing", "deletion"] {
            XCTAssertEqual(LiteHelpLookup.validated(id, topics: ordinary), .noMatch)
            XCTAssertEqual(LiteHelpLookup.validated(id, topics: candidate), .topic(id))
        }
        XCTAssertFalse(ordinary.contains { $0.steps.contains("Plus") })
        XCTAssertEqual(LiteHelpTopic.matches("恢復購買", in: ordinary).count, 0)
        XCTAssertEqual(LiteHelpTopic.matches("恢復購買", in: candidate).map(\.id), ["subscription"])
        XCTAssertEqual(LiteHelpTopic.matches("想退款", in: candidate).map(\.id), ["billing"])
        XCTAssertFalse(LiteHelpTopic.matches("如何取消訂閱", in: candidate).contains { $0.id == "deletion" })
    }

    func testOfflineSearchFindsTheExpectedInstructionsInBothProfiles() {
        let queries = ["相機黑畫面": "camera", "沒有 LiDAR 面積": "area",
            "我要修邊": "outline", "想把ＰＤＦ帶去看診": "report", "把以前量的幾張印在同一份": "report",
            "另一處傷口": "wounds", "撤回研究": "research"]
        for personal in [false, true] {
            let topics = LiteHelpTopic.topics(personal: personal)
            for (query, id) in queries {
                XCTAssertTrue(LiteHelpTopic.matches(query, in: topics).contains { $0.id == id }, query)
            }
            XCTAssertTrue(LiteHelpTopic.matches("   ", in: topics).isEmpty)
            XCTAssertTrue(LiteHelpTopic.matches("unrecognized", in: topics).isEmpty)
        }
    }

    func testModelOutputCannotIntroduceMedicalTextInstructionsOrAnUnlistedLink() {
        let topics = LiteHelpTopic.topics(personal: true)
        for answer in ["", "none", "infection", "report\n額外說明", "report,balance", "https://example.org"] {
            XCTAssertEqual(LiteHelpLookup.validated(answer, topics: topics), .noMatch)
        }
        XCTAssertEqual(LiteHelpLookup.validated(" report\n", topics: topics), .topic("report"))
    }

    func testEmptyAndOversizeInputsDoNotStartAModelSession() async {
        for query in ["", "   ", String(repeating: "測", count: 181)] {
            let result = await LiteHelpLookup.suggest(query, topics: LiteHelpTopic.topics(personal: true))
            XCTAssertEqual(result, .noMatch)
        }
    }
}
