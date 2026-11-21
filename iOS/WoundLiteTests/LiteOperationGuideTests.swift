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

    func testRequestedOperationAppearsBeforeBackgroundPhotoOrSubscriptionTerms() {
        let cases = [
            "I only want to turn off automatic renewal, without deleting photos.": "billing",
            "How do I cancel my subscription and keep my photos?": "billing",
            "Export photos as PDF": "report",
            "Compare photos before and after": "photos",
            "Correct boundary on a photo": "outline",
            "Resume tracking with the old photos": "wounds",
            "我只想關掉自動續費，照片不要刪掉。": "billing",
            "如何取消訂閱但保留相機照片？": "billing",
            "匯出相機照片與時間軸 PDF": "report",
            "拍攝後如何修正圈選？": "outline"
        ]
        for locale in [Locale(identifier: "en_US"), Locale(identifier: "zh_Hant_TW")] {
            let topics = LiteHelpTopic.topics(personal: true, locale: locale)
            for (question, expected) in cases {
                XCTAssertEqual(LiteHelpTopic.matches(question, in: topics).first?.id, expected, question)
            }
        }
    }

    func testEnglishFragmentsDoNotOfferUnrelatedTopics() {
        for locale in [Locale(identifier: "en_US"), Locale(identifier: "zh_Hant_TW")] {
            let topics = LiteHelpTopic.topics(personal: true, locale: locale)
            for query in ["fingerprint", "caprice", "warehouse", "photocopy", "masking tape", "trackpad", "studying"] {
                XCTAssertTrue(LiteHelpTopic.matches(query, in: topics).isEmpty, query)
            }
        }
    }

    func testRestorePhotosDoesNotSuggestBuyingOrRestoringASubscription() {
        let topics = LiteHelpTopic.topics(personal: true)
        XCTAssertFalse(LiteHelpTopic.matches("How do I restore photos?", in: topics).contains { $0.id == "subscription" })
        for query in ["RESTORE PURCHASES", "restore   purchases", "恢復購買"] {
            XCTAssertEqual(LiteHelpTopic.matches(query, in: topics).first?.id, "subscription")
        }
    }

    func testEverydayFramingAndFocusQuestionsReachCameraInstructionsOffline() {
        let topics = LiteHelpTopic.topics(personal: true)
        for query in ["取景時手機要斜著拿還是面向紙面？", "鏡頭一直糊糊的",
                      "The lens view is blurry", "Help with framing", "take a photo"] {
            XCTAssertEqual(LiteHelpTopic.matches(query, in: topics).first?.id, "camera", query)
        }
    }

    func testRankingKeepsOtherMatchingTopicsWithoutDuplicatesAndNeverMutatesTopics() {
        let topics = LiteHelpTopic.topics(personal: true), before = topics.map(\.id)
        let query = "Export photos as PDF"
        let result = LiteHelpTopic.matches(query, in: topics)
        XCTAssertEqual(result.first?.id, "report")
        XCTAssertTrue(result.contains { $0.id == "camera" })
        XCTAssertEqual(result.count, Set(result.map(\.id)).count)
        XCTAssertEqual(result.map(\.id), LiteHelpTopic.matches(query, in: topics).map(\.id))
        XCTAssertEqual(topics.map(\.id), before)
        XCTAssertEqual(LiteHelpTopic.matches("相機的ＬｉＤＡＲ品質", in: topics).first?.id, "area")
    }

    func testActionRankingStillRespectsAvailableFeatures() {
        let ordinary = LiteHelpTopic.topics(personal: false)
        for query in ["automatic renewal", "auto-renewal", "restore purchases", "close account", "取消續費"] {
            XCTAssertTrue(LiteHelpTopic.matches(query, in: ordinary).isEmpty, query)
        }
    }

    func testEmptyAndOversizeInputsDoNotStartAModelSession() async {
        for locale in [Locale(identifier: "en_US"), Locale(identifier: "zh_Hant_TW")] {
            for query in ["", "   ", String(repeating: "測", count: 181)] {
                let result = await LiteHelpLookup.suggest(query, topics: LiteHelpTopic.topics(personal: true, locale: locale), locale: locale)
                XCTAssertEqual(result, .noMatch)
            }
        }
    }
}
