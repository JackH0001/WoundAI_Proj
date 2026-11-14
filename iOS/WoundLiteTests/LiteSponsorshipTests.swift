import XCTest
@testable import WoundLite

@MainActor final class LiteSponsorshipTests: XCTestCase {
    final class Memory: LiteSponsorDismissal {
        var value: String?, failed = false, saves = 0
        func load() throws -> String? { if failed { throw LiteSponsorError.unavailable }; return value }
        func save(_ id: String) throws { saves += 1; if failed { throw LiteSponsorError.unavailable }; value = id }
    }
    let now = Date(timeIntervalSince1970: 1_800_000_000)
    func campaign() -> LiteSponsorCampaign {
        .init(version: 1, id: "synthetic-001", advertiser: "Synthetic sponsor", startsAt: 1_799_999_000, endsAt: 1_800_001_000,
              markets: ["TWN", "USA"], landing: "https://example.com/sponsor",
              copy: ["en": .init(title: "Synthetic sponsorship", body: "No sale or product claims."),
                     "zh-Hant": .init(title: "合成品牌贊助", body: "測試素材，不銷售或聲稱療效。")])
    }
    func eligibility(free: Bool = true, market: String = "TWN") -> LiteSponsorEligibility {
        .init(free: free, market: market, checkedAt: now)
    }
    func testAbsentCampaignDoesNotReadPlanOrWriteDismissal() async {
        let journal = Memory(); var calls = 0
        let model = LiteSponsorModel(campaign: nil, journal: journal) { calls += 1; throw LiteSponsorError.unavailable }
        await model.refresh(); XCTAssertEqual(calls, 0); XCTAssertEqual(journal.saves, 0)
        XCTAssertFalse(model.visible(at: now)); XCTAssertNil(model.destination(at: now)); XCTAssertNil(model.reportText)
        XCTAssertNil(LiteSponsorCampaign.bundled(), "No real campaign has been approved for this release")
    }
    func testOnlyConfirmedFreeWithinCampaignMarketAndFreshnessCanDisplay() {
        let c = campaign()
        XCTAssertTrue(eligibility().permits(c, at: now))
        XCTAssertFalse(eligibility(free: false).permits(c, at: now))
        XCTAssertFalse(eligibility(market: "GBR").permits(c, at: now))
        XCTAssertFalse(eligibility().permits(c, at: now.addingTimeInterval(60)))
        XCTAssertFalse(eligibility().permits(c, at: now.addingTimeInterval(-1)))
        XCTAssertFalse(LiteSponsorEligibility(free: true, market: "TWN", checkedAt: Date(timeIntervalSince1970: Double(c.endsAt))).permits(c, at: Date(timeIntervalSince1970: Double(c.endsAt))))
    }
    func testDecodeRejectsUnreviewedKeysTrackingAndMalformedCampaigns() throws {
        let good = try JSONEncoder().encode(campaign()); XCTAssertEqual(try LiteSponsorCampaign.decode(good), campaign())
        let original = try XCTUnwrap(JSONSerialization.jsonObject(with: good) as? [String: Any])
        for field in ["account", "wound", "pixel", "script", "trackingURL"] {
            var value = original; value[field] = "not-permitted"
            XCTAssertThrowsError(try LiteSponsorCampaign.decode(JSONSerialization.data(withJSONObject: value)))
        }
        for landing in ["http://example.com", "https://user@example.com", "https://example.com?user=123", "https://example.com#id", "https://example.com:443", "https://example.com/%2fsecret", "javascript:alert(1)", "https://127.0.0.1"] {
            var value = original; value["landing"] = landing
            XCTAssertThrowsError(try LiteSponsorCampaign.decode(JSONSerialization.data(withJSONObject: value)), landing)
        }
        var value = original; value["copy"] = ["en": ["title": "Title", "body": "Body", "pixel": "x"], "zh-Hant": ["title": "字", "body": "字"]]
        XCTAssertThrowsError(try LiteSponsorCampaign.decode(JSONSerialization.data(withJSONObject: value)))
        value = original; value["advertiser"] = "<html>"
        XCTAssertThrowsError(try LiteSponsorCampaign.decode(JSONSerialization.data(withJSONObject: value)))
        XCTAssertThrowsError(try LiteSponsorCampaign.decode(Data(repeating: 32, count: 8193)))
    }
    func testPlanErrorAndPaidPlanNeverBecomeFreeFallback() async {
        let paid = LiteSponsorModel(campaign: campaign(), journal: Memory()) { self.eligibility(free: false) }
        await paid.refresh(); XCTAssertFalse(paid.visible(at: now))
        let unknown = LiteSponsorModel(campaign: campaign(), journal: Memory()) { throw LiteSponsorError.unavailable }
        await unknown.refresh(); XCTAssertFalse(unknown.visible(at: now))
    }
    func testLateFreeReplyAfterLogoutOrUpgradeCannotRedisplay() async {
        var pending: CheckedContinuation<LiteSponsorEligibility, Error>?
        let model = LiteSponsorModel(campaign: campaign(), journal: Memory()) { try await withCheckedThrowingContinuation { pending = $0 } }
        let task = Task { await model.refresh() }
        while pending == nil { await Task.yield() }
        model.invalidate(); pending?.resume(returning: eligibility()); await task.value
        XCTAssertFalse(model.visible(at: now)); XCTAssertNil(model.destination(at: now))
    }
    func testConcurrentRefreshDoesNotMakeDuplicateLookup() async {
        var pending: CheckedContinuation<LiteSponsorEligibility, Error>?, calls = 0
        let model = LiteSponsorModel(campaign: campaign(), journal: Memory()) { calls += 1; return try await withCheckedThrowingContinuation { pending = $0 } }
        let first = Task { await model.refresh() }; while pending == nil { await Task.yield() }
        await model.refresh(); XCTAssertEqual(calls, 1)
        pending?.resume(returning: eligibility()); await first.value
        XCTAssertTrue(model.visible(at: now))
    }
    func testDismissalPersistsAcrossRestartWithoutAnotherPlanLookup() async {
        let journal = Memory(); var calls = 0
        let model = LiteSponsorModel(campaign: campaign(), journal: journal) { calls += 1; return self.eligibility() }
        await model.refresh(); model.dismiss(); XCTAssertNil(model.destination(at: now))
        let reopened = LiteSponsorModel(campaign: campaign(), journal: journal) { calls += 1; return self.eligibility() }
        await reopened.refresh(); XCTAssertFalse(reopened.visible(at: now)); XCTAssertEqual(calls, 1)
        XCTAssertEqual(journal.value, "synthetic-001")
    }
    func testDismissDuringLookupAndPersistenceFailureStillHidesCurrentCard() async {
        let journal = Memory(); var pending: CheckedContinuation<LiteSponsorEligibility, Error>?
        let model = LiteSponsorModel(campaign: campaign(), journal: journal) { try await withCheckedThrowingContinuation { pending = $0 } }
        let task = Task { await model.refresh() }; while pending == nil { await Task.yield() }
        journal.failed = true; model.dismiss(); pending?.resume(returning: eligibility()); await task.value
        XCTAssertFalse(model.visible(at: now)); XCTAssertEqual(journal.saves, 1)
    }
    func testCorruptDismissalDoesNotFetchPlanOrOverwrite() async {
        let journal = Memory(); journal.failed = true; var calls = 0
        let model = LiteSponsorModel(campaign: campaign(), journal: journal) { calls += 1; return self.eligibility() }
        await model.refresh(); XCTAssertFalse(model.visible(at: now)); XCTAssertEqual(calls, 0); XCTAssertEqual(journal.saves, 0)
    }
    func testDestinationAndReportUseOnlyReviewedPublicCampaignData() async {
        let model = LiteSponsorModel(campaign: campaign(), journal: Memory()) { self.eligibility() }
        XCTAssertNil(model.destination(at: now)); await model.refresh()
        XCTAssertEqual(model.destination(at: now)?.absoluteString, "https://example.com/sponsor")
        XCTAssertEqual(model.reportText, "WoundAILite advertisement report\nCampaign: synthetic-001\nAdvertiser: Synthetic sponsor\nContact: jack.hou@gmail.com")
        model.invalidate(); XCTAssertNil(model.destination(at: now))
    }
    func testFileJournalPreservesOtherFilesAndRejectsMalformedState() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: dir) }
        let journal = LiteSponsorFileDismissal(file: dir.appendingPathComponent("sponsor.json"))
        XCTAssertNil(try journal.load()); try journal.save("synthetic-001")
        let original = try Data(contentsOf: journal.file)
        XCTAssertEqual(try journal.load(), "synthetic-001")
        XCTAssertTrue(try journal.file.resourceValues(forKeys: [.isExcludedFromBackupKey]).isExcludedFromBackup == true)
        XCTAssertThrowsError(try journal.save("account/id")); XCTAssertEqual(try Data(contentsOf: journal.file), original)
        try Data("bad-json".utf8).write(to: journal.file)
        XCTAssertThrowsError(try journal.load()); XCTAssertEqual(try Data(contentsOf: journal.file), Data("bad-json".utf8))
    }
    func testEnglishAndChineseDisclosuresArePackaged() throws {
        for lang in ["en", "zh-Hant"] {
            let path = try XCTUnwrap(Bundle.main.path(forResource: lang, ofType: "lproj"))
            let table = try XCTUnwrap(PropertyListSerialization.propertyList(from: Data(contentsOf: URL(fileURLWithPath: path).appendingPathComponent("LiteSponsor.strings")), format: nil) as? [String: String])
            XCTAssertEqual(table.count, 13)
            for (key, value) in table { XCTAssertFalse(value.isEmpty); if lang == "zh-Hant" { XCTAssertEqual(key, value) } }
        }
        XCTAssertEqual(LiteSponsorCopy(locale: Locale(identifier: "en_US")).text("廣告・品牌贊助"), "Advertisement · Sponsor")
    }
}
