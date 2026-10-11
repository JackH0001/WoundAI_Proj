import XCTest
#if WOUND_INSTITUTION_TESTS
@testable import WoundAIInstitution
#else
@testable import WoundMeasurementApp
#endif

@MainActor
final class ClinicalReviewRecordTests: XCTestCase {
    func testOpeningAfterAcknowledgementDoesNotReuseTimelineSnapshot() async throws {
        let repo = CaseRepository(store: WoundStore(path: ":memory:"))
        var m = Measurement(); m.imageId = "synthetic"
        let inserted = await repo.insertMeasurement(m)
        let id = try XCTUnwrap(inserted)
        let stale = await repo.measurement(id: id)
        XCTAssertFalse(try XCTUnwrap(stale).annotationSubmitted)
        await repo.markAnnotationSubmitted(id: id)
        let state = ClinicalReviewRecord()
        state.measurement = stale
        await state.refresh(id: id, repo: repo)
        XCTAssertTrue(try XCTUnwrap(state.measurement).annotationSubmitted)
        let send = await state.prepareSubmission(id: id, repo: repo)
        XCTAssertNil(send)
    }
    func testAcknowledgementAfterOpeningIsCheckedAgainAtSendBoundary() async throws {
        let repo = CaseRepository(store: WoundStore(path: ":memory:"))
        let inserted = await repo.insertMeasurement(Measurement())
        let id = try XCTUnwrap(inserted)
        let state = ClinicalReviewRecord()
        await state.refresh(id: id, repo: repo)
        XCTAssertFalse(try XCTUnwrap(state.measurement).annotationSubmitted)
        await repo.markAnnotationSubmitted(id: id)
        let send = await state.prepareSubmission(id: id, repo: repo)
        XCTAssertNil(send)
        XCTAssertTrue(try XCTUnwrap(state.measurement).annotationSubmitted)
    }
    func testUnsubmittedRevisionIsAllowedButMissingRecordIsNot() async throws {
        let repo = CaseRepository(store: WoundStore(path: ":memory:"))
        let inserted = await repo.insertMeasurement(Measurement())
        let id = try XCTUnwrap(inserted)
        let state = ClinicalReviewRecord()
        let send = await state.prepareSubmission(id: id, repo: repo)
        XCTAssertNotNil(send)
        let missing = await state.prepareSubmission(id: -999, repo: repo)
        XCTAssertNil(missing)
        XCTAssertNil(state.measurement)
    }
    func testUnchangedSavePreservesRevisionButClinicalChangesDoNot() {
        var old = Measurement(); old.imageId = "image"; old.annotationSubmitted = true
        old.rasterPath = "old"; old.rasterMeta = "{\"width\":2,\"height\":2}"
        var current = old; current.annotationSubmitted = false; current.imagePath = "new"
        current.rasterPath = "new-raster"; current.rasterMeta = "{\"height\":2,\"width\":2}"
        let pixels = Data([1,2,3,4])
        XCTAssertTrue(ClinicalAnnotationRevision.matches(current, old, rasterA: pixels, rasterB: pixels))
        var changed = current; changed.exudate = 2
        XCTAssertFalse(ClinicalAnnotationRevision.matches(changed, old, rasterA: pixels, rasterB: pixels))
        changed = current; changed.gtPolygon = "[[1,2],[3,4],[5,6]]"
        XCTAssertFalse(ClinicalAnnotationRevision.matches(changed, old, rasterA: pixels, rasterB: pixels))
        XCTAssertFalse(ClinicalAnnotationRevision.matches(current, old, rasterA: Data([4,3,2,1]), rasterB: pixels))
        XCTAssertFalse(ClinicalAnnotationRevision.matches(current, old, rasterA: nil, rasterB: nil))
    }
    func testSidecarSaveTimeDoesNotCreateANewCaptureButGeometryDoes() {
        let raw = Data([1,2,3,4])
        XCTAssertTrue(ClinicalAnnotationRevision.sameDepth((raw,["captured_at":"a","width":2]), (raw,["captured_at":"b","width":2])))
        XCTAssertFalse(ClinicalAnnotationRevision.sameDepth((raw,["width":2]), (raw,["width":3])))
        XCTAssertFalse(ClinicalAnnotationRevision.sameDepth((raw,["width":2]), (Data([5]),["width":2])))
    }

}
