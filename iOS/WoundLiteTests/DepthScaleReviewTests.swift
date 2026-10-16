import XCTest
#if WOUND_MEDICAL_TESTS
@testable import WoundMeasurementApp
#else
@testable import WoundLite
#endif

final class DepthScaleReviewTests: XCTestCase {
    private func result(_ projected: Double) -> DepthAreaResult {
        DepthAreaResult(surfaceAreaCm2: projected, projectedAreaCm2: projected,
            volumeMl: nil, maxDepthMm: nil, coverage: 1, medianDistanceM: 0.3, tiltDeg: 0)
    }

    func testAllReportedIPhone12CapturesAreFlaggedWithoutChangingValues() {
        for (projection, sticker) in [(267.96, 15.97), (188.97, 16.0), (231.61, 15.96)] {
            let original = result(projection)
            XCTAssertTrue(original.hasGrossScaleConflict(stickerAreaCm2: sticker))
            XCTAssertEqual(original.projectedAreaCm2, projection)
        }
    }

    func testGrossScreenIsSymmetricAndIsNotAnAccuracyPass() {
        XCTAssertTrue(result(40.01).hasGrossScaleConflict(stickerAreaCm2: 20))
        XCTAssertTrue(result(9.99).hasGrossScaleConflict(stickerAreaCm2: 20))
        XCTAssertFalse(result(40).hasGrossScaleConflict(stickerAreaCm2: 20))
        XCTAssertFalse(result(10).hasGrossScaleConflict(stickerAreaCm2: 20))
        XCTAssertFalse(result(22).hasGrossScaleConflict(stickerAreaCm2: 20))
    }

    func testMissingInvalidMarkerCannotPretendToValidateDepth() {
        for marker: Double? in [nil, 0, -1, .nan, .infinity] {
            XCTAssertFalse(result(267.96).hasGrossScaleConflict(stickerAreaCm2: marker))
        }
        for value in [Double.nan, .infinity, 0, -1] {
            XCTAssertTrue(result(value).hasGrossScaleConflict(stickerAreaCm2: 16))
        }
    }

    func testRelativeOrMalformedCalibrationCannotYieldMetricEstimate() throws {
        let polygon = [[[4,4],[20,4],[20,20],[4,20]]]
        let valid = DepthCapture(map: Array(repeating: 0.3, count: 32*24), width: 32, height: 24,
            fx: 30, fy: 30, cx: 16, cy: 12, refWidth: 32, refHeight: 24,
            accuracy: "absolute", filtered: false)
        func estimate(_ d: DepthCapture) -> DepthAreaResult? {
            DepthAreaEstimator.estimate(polygons: polygon, depth: d, imageW: 32, imageH: 24)
        }
        XCTAssertNotNil(estimate(valid))
        var d = valid; d.accuracy = "relative"; XCTAssertNil(estimate(d))
        d = valid; d.refWidth = 24; d.refHeight = 32; XCTAssertNil(estimate(d))
        for invalid in [Double.nan, .infinity] {
            d = valid; d.fx = invalid; XCTAssertNil(estimate(d))
            d = valid; d.cx = invalid; XCTAssertNil(estimate(d))
            d = valid; d.refWidth = invalid; XCTAssertNil(estimate(d))
        }
    }

    func testReferenceResolutionScalingPreservesArea() throws {
        let polygon = [[[4,4],[20,4],[20,20],[4,20]]]
        let original = DepthCapture(map: Array(repeating: 0.3, count: 32*24), width: 32, height: 24,
            fx: 30, fy: 30, cx: 16, cy: 12, refWidth: 32, refHeight: 24,
            accuracy: "absolute", filtered: false)
        var scaled = original
        scaled.fx *= 4; scaled.fy *= 4; scaled.cx *= 4; scaled.cy *= 4
        scaled.refWidth *= 4; scaled.refHeight *= 4
        let a = try XCTUnwrap(DepthAreaEstimator.estimate(polygons: polygon, depth: original, imageW: 32, imageH: 24))
        let b = try XCTUnwrap(DepthAreaEstimator.estimate(polygons: polygon, depth: scaled, imageW: 32, imageH: 24))
        XCTAssertEqual(a.projectedAreaCm2, b.projectedAreaCm2, accuracy: 0.000001)
    }
}
