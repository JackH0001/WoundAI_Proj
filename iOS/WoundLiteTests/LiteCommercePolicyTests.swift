#if DEBUG && targetEnvironment(simulator)
import XCTest
@testable import WoundLite

final class LiteCommercePolicyTests: XCTestCase {
    func testFixedHalfDistributionAndPersistence() throws {
        var model = LiteLabCohort()
        var selected = 0
        for i in 0..<10000 {
            let first = try XCTUnwrap(model.enroll(subject: "synthetic-\(i)", policy: .fixedHalf, agreed: true))
            selected += first.selected ? 1 : 0
            // Config changes never re-roll an existing decision.
            XCTAssertEqual(try model.enroll(subject: "synthetic-\(i)", policy: .allConsenting, agreed: true), first)
        }
        XCTAssertTrue((4800...5200).contains(selected), "Observed \(selected)/10000; expected fraction, not forced exact half")
        let restored = try JSONDecoder().decode(LiteLabCohort.self, from: JSONEncoder().encode(model))
        XCTAssertEqual(model, restored)
        print("COHORT_SYNTHETIC selected=\(selected) total=10000")
    }

    func testNonSelectedStillReceivesServiceAndCompleteLocalTimeline() throws {
        var model = LiteLabCohort()
        let subject = try XCTUnwrap((0..<100).map { "synthetic-\($0)" }.first {
            try model.enroll(subject: $0, policy: .fixedHalf, agreed: true)?.selected == false
        })
        for i in 0..<10 {
            XCTAssertFalse(try model.capture(subject: subject, day: "2026-10-25", plan: .plus,
                id: "photo-\(i)", wound: "one-wound", mask: "ai-mask", researchEnabled: true))
        }
        XCTAssertEqual(model.localTimeline(subject: subject).count, 10)
        XCTAssertEqual(Set(model.localTimeline(subject: subject).map(\.wound)), ["one-wound"])
        XCTAssertTrue(model.researchTimeline(subject: subject).isEmpty)
    }

    func testWithdrawalReconsentKeepsDecisionAndDoesNotResurrect() throws {
        var model = LiteLabCohort()
        let subject = "synthetic-selected"
        let decision = try model.enroll(subject: subject, policy: .allConsenting, agreed: true)
        XCTAssertTrue(try model.capture(subject: subject, day: "2026-10-25", plan: .plus,
            id: "old", wound: "one", mask: "ai", researchEnabled: true))
        model.withdraw(subject: subject)
        XCTAssertEqual(model.localTimeline(subject: subject).count, 1)
        XCTAssertTrue(model.researchTimeline(subject: subject).isEmpty)
        XCTAssertEqual(try model.enroll(subject: subject, policy: .fixedHalf, agreed: true), decision)
        // Delayed exact retry and revision of the withdrawn epoch cannot restore research content.
        XCTAssertFalse(try model.capture(subject: subject, day: "2026-10-25", plan: .plus,
            id: "old", wound: "one", mask: "ai", researchEnabled: true))
        try model.revise(subject: subject, id: "old", expectedRevision: 1, mask: "edited", researchEnabled: true)
        XCTAssertTrue(model.researchTimeline(subject: subject).isEmpty)
        XCTAssertTrue(try model.capture(subject: subject, day: "2026-10-25", plan: .plus,
            id: "new", wound: "one", mask: "ai", researchEnabled: true))
        XCTAssertEqual(model.researchTimeline(subject: subject).map(\.id), ["new"])
        XCTAssertEqual(model.researchTimeline(subject: subject).first?.epoch, 2)
        XCTAssertEqual(model.audit.filter { $0.hasPrefix("withdraw:") }.count, 1)
    }

    func testConsentAndSamplingAreIndependentOfService() throws {
        for policy in LiteLabSampling.allCases {
            var model = LiteLabCohort()
            XCTAssertNil(try model.enroll(subject: "synthetic-a", policy: policy, agreed: false))
            XCTAssertFalse(try model.capture(subject: "synthetic-a", day: "2026-10-25", plan: .free,
                id: "one", wound: "w", mask: "mask", researchEnabled: policy != .disabled))
            XCTAssertEqual(model.localTimeline(subject: "synthetic-a").count, 1)
        }
    }

    func testQuotaUpgradeDoesNotResetUsageAndRetriesDoNotCharge() throws {
        var model = LiteLabCohort()
        for i in 0..<2 {
            _ = try model.capture(subject: "synthetic-a", day: "2026-10-25", plan: .free,
                id: "\(i)", wound: "w", mask: "mask", researchEnabled: false)
        }
        _ = try model.capture(subject: "synthetic-a", day: "2026-10-25", plan: .free,
            id: "0", wound: "w", mask: "mask", researchEnabled: false)
        XCTAssertEqual(model.remaining(subject: "synthetic-a", day: "2026-10-25", plan: .free), 0)
        XCTAssertEqual(model.remaining(subject: "synthetic-a", day: "2026-10-25", plan: .plus), 8)
        XCTAssertEqual(model.remaining(subject: "synthetic-a", day: "2026-10-26", plan: .free), 2)
        XCTAssertThrowsError(try model.capture(subject: "synthetic-a", day: "2026-10-25", plan: .free,
            id: "3", wound: "w", mask: "mask", researchEnabled: false))
        XCTAssertThrowsError(try model.capture(subject: "synthetic-a", day: "2026-10-25", plan: .plus,
            id: "0", wound: "other-wound", mask: "mask", researchEnabled: false))
    }

    func testRevisionPreservesOriginalMediaAndRejectsStaleVersion() throws {
        var model = LiteLabCohort()
        _ = try model.enroll(subject: "synthetic-a", policy: .allConsenting, agreed: true)
        _ = try model.capture(subject: "synthetic-a", day: "2026-10-25", plan: .free,
            id: "one", wound: "w", mask: "AI-original", researchEnabled: true)
        let first = try XCTUnwrap(model.researchTimeline(subject: "synthetic-a").first)
        try model.revise(subject: "synthetic-a", id: "one", expectedRevision: 1, mask: "edited", researchEnabled: true)
        try model.revise(subject: "synthetic-a", id: "one", expectedRevision: 1, mask: "edited", researchEnabled: true)
        XCTAssertThrowsError(try model.revise(subject: "synthetic-a", id: "one", expectedRevision: 1,
            mask: "stale-different", researchEnabled: true))
        let next = try XCTUnwrap(model.researchTimeline(subject: "synthetic-a").first)
        XCTAssertEqual(first.rawReference, next.rawReference)
        XCTAssertEqual(first.depthReference, next.depthReference)
        XCTAssertEqual(next.masks, ["AI-original", "edited"])
        XCTAssertEqual(model.remaining(subject: "synthetic-a", day: "2026-10-25", plan: .free), 1)
    }

    func testAdvertisingNotOnHealthOrConsentScreensAndNotForPlus() {
        XCTAssertTrue(LiteLabAdvertisement.visible(plan: .free, placement: .plans))
        for placement in [LiteLabAdPlacement.plans, .measurement, .history, .consent] {
            XCTAssertFalse(LiteLabAdvertisement.visible(plan: .plus, placement: placement))
        }
        for placement in [LiteLabAdPlacement.measurement, .history, .consent] {
            XCTAssertFalse(LiteLabAdvertisement.visible(plan: .free, placement: placement))
        }
    }
}
#endif
