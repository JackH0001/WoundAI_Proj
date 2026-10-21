import XCTest
@testable import WoundLite

final class LiteGeometryQualityTests: XCTestCase {
    private func depth() -> DepthCapture {
        DepthCapture(map: Array(repeating: 0.3, count: 32 * 24), width: 32, height: 24,
            fx: 30, fy: 31, cx: 15, cy: 11, refWidth: 32, refHeight: 24,
            accuracy: "absolute", filtered: true, rgbWidth: 128, rgbHeight: 96,
            sourceExifOrientation: 6)
    }
    private func result() -> DepthAreaResult {
        DepthAreaResult(surfaceAreaCm2: 10, projectedAreaCm2: 10,
            volumeMl: 0, maxDepthMm: 0, coverage: 1, medianDistanceM: 0.3, tiltDeg: 0)
    }

    func testGeometryChecksBindingWithoutClaimingPhysicalRegistration() {
        let d = depth()
        XCTAssertNil(d.geometryIssue(imageW: 128, imageH: 96, requireOrientation: true))
        XCTAssertEqual(d.geometryIssue(imageW: 256, imageH: 192), "rgb_binding_mismatch")
        XCTAssertEqual(d.geometryIssue(imageW: 96, imageH: 128), "aspect_mismatch")
        var changed = d; changed.refWidth = 24; changed.refHeight = 32
        XCTAssertEqual(changed.geometryIssue(imageW: 128, imageH: 96), "aspect_mismatch")
        changed = d; changed.cx = 32
        XCTAssertEqual(changed.geometryIssue(imageW: 128, imageH: 96), "invalid_intrinsics")
        changed = d; changed.sourceExifOrientation = nil
        XCTAssertEqual(changed.geometryIssue(imageW: 128, imageH: 96, requireOrientation: true), "orientation_unknown")
        XCTAssertNil(changed.geometryIssue(imageW: 128, imageH: 96))
    }

    @MainActor
    func testCloudResizeOrBadContourCannotRebindOriginalDepth() {
        let vm = LiteMeasureVM()
        vm.imageW = 128; vm.imageH = 96; vm.depth = depth()
        var reply = BackendClient.LiteSegmentResult(polygons: [[[5,5],[20,5],[20,20]]],
            imageW: 128, imageH: 96, confidence: 1, stored: false,
            imageId: nil, userMessage: nil, route: nil, rawPolyCount: 1)
        XCTAssertTrue(vm.acceptsCloudGeometry(reply))
        // Same aspect ratio is not sufficient: a resized mask needs an explicit mapping.
        reply.imageW = 256; reply.imageH = 192
        XCTAssertFalse(vm.acceptsCloudGeometry(reply))
        reply.imageW = 128; reply.imageH = 96
        for bad in [[[[5],[20,5],[20,20]]], [[[5,5],[129,5],[20,20]]], [[[5,5],[-1,5],[20,20]]]] {
            reply.polygons = bad
            XCTAssertFalse(vm.acceptsCloudGeometry(reply))
        }
        XCTAssertEqual(vm.imageW, 128); XCTAssertEqual(vm.imageH, 96)
        XCTAssertEqual(vm.depth?.rgbWidth, 128); XCTAssertEqual(vm.depth?.map, depth().map)
    }

    func testMalformedPolygonCannotCrashSharedEstimator() {
        for bad in [[[[5],[20,5],[20,20]]], [[[5,5],[129,5],[20,20]]]] {
            XCTAssertNil(DepthAreaEstimator.estimate(polygons: bad, depth: depth(), imageW: 128, imageH: 96))
        }
    }

    func testMissingDepthAlwaysDisclosedEvenAbovePreviousSeventyPercentThreshold() {
        var r = result(); r.coverage = 0.95
        let verdict = liteVerdict(r)
        XCTAssertTrue(verdict.usable)
        XCTAssertTrue(verdict.warnings.contains { $0.contains("補值") })
        r.tiltDeg = nil
        XCTAssertTrue(liteVerdict(r).warnings.contains { $0.contains("未知傾角") })
    }

    func testInvalidNumbersNeverYieldUsableQuality() {
        for invalid in [Double.nan, .infinity, -1, 0] {
            var r = result(); r.surfaceAreaCm2 = invalid
            XCTAssertFalse(liteVerdict(r).usable)
            r = result(); r.projectedAreaCm2 = invalid
            XCTAssertFalse(liteVerdict(r).usable)
        }
        var r = result(); r.coverage = 1.1
        XCTAssertFalse(liteVerdict(r).usable)
        r = result(); r.tiltDeg = .nan
        XCTAssertFalse(liteVerdict(r).usable)
    }

    func testNewProvenanceNeverClaimsMetricVolumeEvenForPerfectCoverage() throws {
        let original = result()
        let context = LiteMeasurementContext(depth: depth(), result: original, warnings: [])
        XCTAssertTrue(context.isKnownSurface)
        XCTAssertEqual(context.registration, "not_verified")
        XCTAssertEqual(context.volumeStatus, "research_only_not_physically_validated")
        XCTAssertEqual(try JSONDecoder().decode(LiteMeasurementContext.self, from: JSONEncoder().encode(context)), context)
        XCTAssertEqual(original.volumeMl, 0) // The diagnostic is kept; not corrected into a larger number.
    }

    func testTrendSeparatesHistoricalUnknownMethodAndNewSurfaceWithoutChangingOldNumbers() {
        var old = LiteRecord(id: "old", dateISO: "2026-10-20T00:00:00Z", surfaceCm2: 10,
            projectedCm2: 9, volumeMl: 0.02, maxDepthMm: 0.5, quality: "ok",
            imageName: "fixture", source: "manual", woundID: "a")
        var current = old
        current.id = "new"; current.dateISO = "2026-10-21T00:00:00Z"; current.surfaceCm2 = 8
        current.measurementContext = LiteMeasurementContext(depth: depth(), result: result(), warnings: [])
        XCTAssertNil(LiteGrouping.changePercent(current, in: [old, current]))
        XCTAssertEqual(LiteWoundTrend.points(woundID: "a", records: [old, current]).map(\.id), ["new"])
        XCTAssertEqual(LiteWoundTrend.points(woundID: "a", records: [old, current], series: .legacy).map(\.area), [10])
        old.measurementContext = current.measurementContext
        old.measurementContext?.areaSource = "sticker_plane"
        XCTAssertNil(old.areaSeries)
        XCTAssertTrue(LiteWoundTrend.points(woundID: "a", records: [old]).isEmpty)
        XCTAssertEqual(old.volumeMl, 0.02)
    }

    func testSavedSurfaceUnchangedByGeometryGateAndImageResolution() throws {
        let d = depth()
        let a = try XCTUnwrap(DepthAreaEstimator.estimate(
            polygons: [[[32,24],[96,24],[96,72],[32,72]]], depth: d, imageW: 128, imageH: 96))
        var large = d; large.rgbWidth = 1024; large.rgbHeight = 768
        let b = try XCTUnwrap(DepthAreaEstimator.estimate(
            polygons: [[[256,192],[768,192],[768,576],[256,576]]], depth: large, imageW: 1024, imageH: 768))
        XCTAssertEqual(a.surfaceAreaCm2, b.surfaceAreaCm2, accuracy: 1e-10)
        XCTAssertEqual(a.projectedAreaCm2, b.projectedAreaCm2, accuracy: 1e-10)
        XCTAssertEqual(d.map, Array(repeating: 0.3, count: 32 * 24))
    }
}
