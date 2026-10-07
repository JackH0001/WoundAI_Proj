import XCTest
@testable import WoundLite

final class LiteAngleGuidanceTests: XCTestCase {
    private func measurement(_ tilt: Double, coverage: Double = 1, distance: Double = 0.3) -> DepthAreaResult {
        DepthAreaResult(surfaceAreaCm2: 11.22, projectedAreaCm2: 8.2, volumeMl: nil,
                        maxDepthMm: nil, coverage: coverage, medianDistanceM: distance, tiltDeg: tilt)
    }
    func testLargeAngleRetainsReferenceMeasurementWithWarning() {
        for angle in [25.1, 43.0, 60.0] {
            let verdict = liteVerdict(measurement(angle))
            XCTAssertTrue(verdict.usable); XCTAssertNil(verdict.blocker)
            XCTAssertTrue(verdict.warnings.contains { $0.contains("誤差可能增加") })
        }
    }
    func testAngleWarningDoesNotOverrideInsufficientDepthOrTooClose() {
        XCTAssertFalse(liteVerdict(measurement(43, coverage: 0.49)).usable)
        XCTAssertFalse(liteVerdict(measurement(43, distance: 0.21)).usable)
    }
    func testLowerAnglesAreGuidanceNotAnAccuracyGuarantee() {
        for angle in [10.0, 10.1, 25.0] {
            var result = measurement(angle); result.projectedAreaCm2 = result.surfaceAreaCm2
            let verdict = liteVerdict(result)
            XCTAssertTrue(verdict.usable)
            XCTAssertEqual(verdict.warnings.count, angle > 10 ? 1 : 0)
            XCTAssertFalse(verdict.warnings.contains { $0.contains("最準") || $0.contains("影響小") })
        }
    }
}
