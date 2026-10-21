import XCTest
import UIKit
#if WOUND_INSTITUTION_TESTS
@testable import WoundAIInstitution
#else
@testable import WoundMeasurementApp
#endif

final class TissueEditCountTests: XCTestCase {
    @MainActor
    private func image() -> UIImage {
        let f = UIGraphicsImageRendererFormat(); f.scale = 1
        return UIGraphicsImageRenderer(size: CGSize(width: 512, height: 512), format: f).image { ctx in
            UIColor.orange.setFill(); ctx.fill(CGRect(x: 0, y: 0, width: 512, height: 512))
        }
    }
    @MainActor
    private func editor(resume: EditRaster? = nil) async -> EditCanvasModel {
        let m = EditCanvasModel(image: image(), initialPolygons: [[[200,200],[310,200],[310,310],[200,310]]],
            originalArea: nil, tissueFrac: [:], mmPerPx: nil, resume: resume, wbGains: nil)
        await m.runSeed()
        m.boxSize = CGSize(width: 512, height: 512); m.fitFull()
        return m
    }
    @MainActor
    private func assertCounts(_ m: EditCanvasModel, file: StaticString = #filePath, line: UInt = #line) throws {
        let snapshot = m.snap(), raster = try XCTUnwrap(m.finish(), file: file, line: line).raster
        let mask = snapshot.m.indices.filter { snapshot.m[$0] != 0 }
        let edited = mask.filter { snapshot.t[$0] != snapshot.auto[$0] }.count
        XCTAssertEqual(raster.maskPx, mask.count, file: file, line: line)
        XCTAssertEqual(raster.tissueEditedPx, edited, file: file, line: line)
        XCTAssertEqual(raster.tissueEditRatio, Double(edited) / Double(mask.count), accuracy: 1e-12, file: file, line: line)
        XCTAssertTrue((0...1).contains(raster.tissueEditRatio), file: file, line: line)
    }
    @MainActor
    func testReopenWithoutEditsDoesNotCountAutoReseedingAsDoctorEdits() async throws {
        let first = await editor()
        let saved = try XCTUnwrap(first.finish()).raster
        XCTAssertEqual(saved.tissueEditedPx, 0)
        let reopened = await editor(resume: saved)
        try assertCounts(reopened)
        XCTAssertEqual(reopened.finish()?.raster.mask, saved.mask)
        XCTAssertEqual(reopened.finish()?.raster.tissue, saved.tissue)
    }
    @MainActor
    func testSecondEditShrinksUncalibratedWoundThenUndoRedo() async throws {
        let first = await editor()
        let saved = try XCTUnwrap(first.finish()).raster
        let reopened = await editor(resume: saved)
        reopened.tool = .bErase
        reopened.touchesChanged(.began, [CGPoint(x: 255, y: 255)])
        reopened.touchesChanged(.ended, [])
        XCTAssertLessThan(try XCTUnwrap(reopened.finish()).raster.maskPx, saved.maskPx)
        try assertCounts(reopened)
        XCTAssertNil(reopened.finish()?.area)
        reopened.undo(); try assertCounts(reopened)
        reopened.redo(); try assertCounts(reopened)
    }
    @MainActor
    func testExpandedBoundaryAndTissueBrushKeepExactCounts() async throws {
        let m = await editor()
        m.touchesChanged(.began, [CGPoint(x: 100, y: 100)])
        m.touchesChanged(.ended, [])
        try assertCounts(m)
        m.tool = .tissue; m.curTissue = 3
        m.touchesChanged(.began, [CGPoint(x: 255, y: 255)])
        m.touchesChanged(.ended, [])
        try assertCounts(m)
        XCTAssertGreaterThan(try XCTUnwrap(m.finish()).raster.tissueEditedPx, 0)
    }
    @MainActor
    func testSecondEditPayloadAndTwoImageExportUseSameUncalibratedRevision() async throws {
        let first = await editor()
        let m = await editor(resume: try XCTUnwrap(first.finish()).raster)
        m.tool = .bErase
        m.touchesChanged(.began, [CGPoint(x: 255, y: 255)]); m.touchesChanged(.ended, [])
        let result = try XCTUnwrap(m.finish()), r = result.raster
        let encoded = try XCTUnwrap(EditRasterCodec.encode(r))
        var row = Measurement(); row.id = 50; row.imageId = "synthetic-reedit"; row.wdCode = "TEST"
        row.doctorVerified = true; row.imageW = 512; row.imageH = 512; row.exudate = 0
        row.rasterMeta = encoded.1; row.gtPolygon = PolygonJson.toJson(result.all)
        row.estimatedArea = result.area
        row.tissueGranulation = result.frac["granulation"]; row.tissueSlough = result.frac["slough"]
        row.tissueNecrosis = result.frac["necrosis"]; row.tissueEpithelial = result.frac["epithelial"]
        row.tissueOther = result.frac["other"]
        let payload = try BackendClient.annotationPayload(code: "TEST", gtPolygon: result.poly, exudate: 0,
            allPolygons: result.all, areaCm2: result.area, imageId: row.imageId, imageW: 512, imageH: 512,
            tissueFrac: result.frac, tissueMaskPngBase64: TissueMaskCodec.encodeBase64(tissue:r.tissue,mask:r.mask,mw:r.mw,mh:r.mh),
            tissueRaster: r, consentTrain: true, doctorVerified: true)
        let obj = try XCTUnwrap(JSONSerialization.jsonObject(with: payload) as? [String: Any])
        XCTAssertEqual(obj["tissue_edit_ratio"] as? Double, 0)
        XCTAssertNil(obj["area_cm2"], "No invented calibrated area")
        let raw = try XCTUnwrap(image().jpegData(compressionQuality: 0.9))
        let files = try InstitutionPrivateExports.submissionContents(row:row,image:raw,raster:encoded.0,payload:payload)
        XCTAssertEqual(files["submission.json"], payload)
        let delivery = try InstitutionPrivateExports.deliveryImages(files)
        XCTAssertEqual(Set(delivery.keys), ["image.jpg", "overlay.png"])
        XCTAssertEqual(delivery["image.jpg"], raw)
        XCTAssertNotNil(UIImage(data: try XCTUnwrap(delivery["overlay.png"])))
        var invalid = r; invalid.tissueEditedPx = r.maskPx + 1
        XCTAssertThrowsError(try BackendClient.annotationPayload(code: "TEST", gtPolygon: result.poly, exudate: 0,
            imageId: row.imageId, imageW: 512, imageH: 512,
            tissueMaskPngBase64: TissueMaskCodec.encodeBase64(tissue:r.tissue,mask:r.mask,mw:r.mw,mh:r.mh),
            tissueRaster: invalid, consentTrain: true, doctorVerified: true))
        try payload.write(to: FileManager.default.temporaryDirectory.appendingPathComponent("tissue-reedit-contract-ios.json"))
    }

    @MainActor
    func testLargeMaskFallbackIsAutomaticAndBaselineSurvivesPNGReopen() async throws {
        let f=UIGraphicsImageRendererFormat();f.scale=1
        let image=UIGraphicsImageRenderer(size:CGSize(width:1200,height:1000),format:f).image { c in
            UIColor.orange.setFill();c.fill(CGRect(x:0,y:0,width:1200,height:1000))
        }
        let poly=[[[121,123],[1067,181],[921,893],[210,751]]]
        let m=EditCanvasModel(image:image,initialPolygons:poly,originalArea:nil,tissueFrac:[:],mmPerPx:nil,resume:nil,wbGains:nil)
        await m.runSeed()
        var r=try XCTUnwrap(m.finish()).raster
        XCTAssertEqual(r.tissueEditedPx,0,"Downsampling holes filled automatically must not become human edits")
        XCTAssertNotNil(r.autoBaseline)
        let i=try XCTUnwrap(r.mask.indices.first { r.mask[$0] != 0 })
        r.tissue[i] = r.tissue[i] == 3 ? 1 : 3; r.tissueEditedPx=1
        r.planarCalibration=PlanarAreaCalibration(version:1,status:"accepted",reason:"test",h:[1,0,0,0,1,0,0,0,1],bounds:[-1,-1,1200,1000],corner_error_px:0.5,relative_sensitivity:0.02,profile:"woundai-a4-coplanar-v1",marker_ids:[40,41,42,43],reprojection_rms_px:0.25,holdout_max_px:1.2)
        let encoded=try XCTUnwrap(EditRasterCodec.encode(r))
        let restored=try XCTUnwrap(EditRasterCodec.decode(png:encoded.0,meta:encoded.1,canvasW:1200,canvasH:1000))
        XCTAssertEqual(restored.autoBaseline,r.autoBaseline)
        XCTAssertEqual(restored.planarCalibration,r.planarCalibration)
        let reopened=EditCanvasModel(image:image,initialPolygons:poly,originalArea:nil,tissueFrac:[:],mmPerPx:nil,resume:restored,wbGains:[2,0.5,1])
        await reopened.runSeed()
        XCTAssertEqual(reopened.finish()?.raster.tissueEditedPx,1,"Reclassification must not replace a historical baseline")
        XCTAssertEqual(reopened.finish()?.raster.tissue,r.tissue)
    }
    @MainActor
    func testLegacyBaselineIsUnknownRatherThanInvented() async throws {
        let m=await editor();var r=try XCTUnwrap(m.finish()).raster;r.autoBaseline=nil
        let reopened=await editor(resume:r)
        XCTAssertFalse(try XCTUnwrap(reopened.finish()).raster.tissueEditCountKnown)
    }
}
