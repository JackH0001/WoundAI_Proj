import XCTest
@testable import WoundMeasurementApp

final class LiteCloudQuotaTests: XCTestCase {
    private var valid: [String: Any] {
        ["limit": 5, "used": 2, "remaining": 3,
         "scope": "installation", "operation": "segment",
         "resets_at": "2026-10-04T00:00:00Z"]
    }

    func testServerLimitIsAuthoritativeIncludingOlderOverrides() throws {
        var payload = valid
        payload["limit"] = 30
        payload["remaining"] = 28
        let quota = try XCTUnwrap(LiteCloudQuota.parse(payload))
        XCTAssertEqual(quota.limit, 30)
        XCTAssertEqual(quota.remaining, 28)
    }

    func testMissingOrMalformedReplyRemainsUnknown() {
        XCTAssertNil(LiteCloudQuota.parse(nil))
        XCTAssertNil(LiteCloudQuota.parse([:]))
        for field in ["limit", "used", "remaining", "resets_at", "scope", "operation"] {
            var payload = valid
            payload.removeValue(forKey: field)
            XCTAssertNil(LiteCloudQuota.parse(payload), field)
        }
    }

    func testInvalidCountsAndWrongOperationAreRejected() {
        for (key, value) in [("remaining", -1 as Any), ("remaining", 5 as Any),
                             ("used", -1 as Any), ("limit", true as Any),
                             ("used", 1.5 as Any), ("operation", "annotation" as Any),
                             ("scope", "account" as Any), ("resets_at", "tomorrow" as Any)] {
            var payload = valid
            payload[key] = value
            XCTAssertNil(LiteCloudQuota.parse(payload), key)
        }
    }

    func testLoweredLimitDoesNotInvalidateHistoricalUsage() throws {
        var payload = valid
        payload["used"] = 7
        payload["remaining"] = 0
        XCTAssertEqual(try XCTUnwrap(LiteCloudQuota.parse(payload)).remaining, 0)
    }

    func testSnapshotExpiresExactlyAtServerReset() throws {
        let quota = try XCTUnwrap(LiteCloudQuota.parse(valid))
        XCTAssertTrue(quota.isCurrent(at: quota.resetsAt.addingTimeInterval(-1)))
        XCTAssertFalse(quota.isCurrent(at: quota.resetsAt))
        XCTAssertFalse(quota.isCurrent(at: quota.resetsAt.addingTimeInterval(1)))
    }
}
