import XCTest
#if WOUND_INSTITUTION_TESTS
@testable import WoundAIInstitution
#else
@testable import WoundMeasurementApp
#endif

@MainActor
final class ClinicalUploadFlowTests: XCTestCase {
    private enum Failure: Error { case offline }

    func testDuplicateStillAcknowledgesAndUploadsDepthInOrder() async {
        var events: [String] = []
        let message = await ClinicalUploadFlow.run(annotate: {
            events.append("annotation"); return .duplicateSkipped(note: "already stored")
        }, acknowledge: { events.append("ack") }, depth: { events.append("depth") },
        progress: { events.append($0.hasPrefix("1/") ? "phase1" : "phase2") })
        XCTAssertEqual(events, ["phase1", "annotation", "ack", "phase2", "depth"])
        XCTAssertTrue(message.hasPrefix("✅"))
    }

    func testStoredFollowsSameDepthPath() async {
        var acknowledged = false
        var uploaded = false
        let message = await ClinicalUploadFlow.run(annotate: { .enqueued(note: nil) },
            acknowledge: { acknowledged = true }, depth: {
                XCTAssertTrue(acknowledged); uploaded = true
            }, progress: { _ in })
        XCTAssertTrue(uploaded)
        XCTAssertTrue(message.contains("原始深度皆已"))
    }

    func testRejectedNeverAcknowledgesOrUploadsDepth() async {
        let message = await ClinicalUploadFlow.run(annotate: { .rejected(message: "withdrawn") },
            acknowledge: { XCTFail("must not acknowledge") },
            depth: { XCTFail("must not upload") }, progress: { _ in })
        XCTAssertTrue(message.hasPrefix("⚠️"))
        XCTAssertTrue(message.contains("withdrawn"))
    }

    func testLostAnnotationReplyLeavesRetryWithoutEditing() async {
        let message = await ClinicalUploadFlow.run(annotate: { throw Failure.offline },
            acknowledge: { XCTFail("no server acknowledgement") },
            depth: { XCTFail("no orphan upload") }, progress: { _ in })
        XCTAssertTrue(message.contains("不需重新修邊"))
        XCTAssertFalse(message.hasPrefix("✅"))
    }

    func testDepthFailureRetainsAnnotationAcknowledgementAndOnlyRequestsDepthRetry() async {
        var acknowledged = false
        let message = await ClinicalUploadFlow.run(annotate: { .enqueued(note: nil) },
            acknowledge: { acknowledged = true }, depth: { throw Failure.offline },
            progress: { _ in })
        XCTAssertTrue(acknowledged)
        XCTAssertTrue(message.contains("標註已保存"))
        XCTAssertTrue(message.contains("接續重試深度"))
        XCTAssertFalse(message.hasPrefix("✅"))
    }

    func testLocalAcknowledgementFailureDoesNotPretendComplete() async {
        let message = await ClinicalUploadFlow.run(annotate: { .enqueued(note: nil) },
            acknowledge: { throw Failure.offline },
            depth: { XCTFail("must surface failed local state") }, progress: { _ in })
        XCTAssertTrue(message.contains("本機送件狀態保存失敗"))
        XCTAssertFalse(message.hasPrefix("✅"))
    }

    func testNoDepthDoesNotClaimCompleteRGBD() async {
        let message = await ClinicalUploadFlow.run(annotate: { .enqueued(note: nil) },
            acknowledge: {}, depth: nil, progress: { _ in })
        XCTAssertTrue(message.contains("不代表完整 RGB-D"))
    }

    func testSlowDepthDoesNotReturnCompletionBeforeServerReply() async {
        var releaseDepth: CheckedContinuation<Void, Never>?
        var finished = false
        let started = expectation(description: "depth started")
        let task = Task { @MainActor in
            let result = await ClinicalUploadFlow.run(annotate: { .enqueued(note: nil) },
                acknowledge: {}, depth: {
                    await withCheckedContinuation { continuation in
                        releaseDepth = continuation
                        started.fulfill()
                    }
                }, progress: { _ in })
            finished = true
            return result
        }
        await fulfillment(of: [started], timeout: 2)
        XCTAssertFalse(finished)
        releaseDepth?.resume()
        let result = await task.value
        XCTAssertTrue(finished)
        XCTAssertTrue(result.hasPrefix("✅"))
    }
}
