import XCTest
import UIKit
#if WOUND_INSTITUTION_TESTS
@testable import WoundAIInstitution
#else
@testable import WoundMeasurementApp
#endif

final class InstitutionExportTests: XCTestCase {
    private func fixture() -> (UIImage, EditRaster) {
        let format = UIGraphicsImageRendererFormat(); format.scale = 1
        let image = UIGraphicsImageRenderer(size: CGSize(width: 64, height: 64), format: format).image { c in
            UIColor.white.setFill(); c.fill(CGRect(x: 0, y: 0, width: 64, height: 64))
        }
        let raster = EditRaster(mask: [1,1,1,1], tissue: [1,2,3,5], origMask: [1,1,1,1],
            rx0: 10, ry0: 10, mw: 2, mh: 2, mScale: 1, cm2PerPx: 0.01, canvasW: 64, canvasH: 64)
        return (image, raster)
    }
    func testSubmissionUsesExactSavedImageAndNativeMasksWithSavedScores() throws {
        let (image, raster) = fixture()
        let encoded = try XCTUnwrap(EditRasterCodec.encode(raster))
        var row = Measurement(); row.id = 77; row.doctorVerified = true
        row.imageId = "synthetic-image"; row.imageW = 64; row.imageH = 64
        row.rasterMeta = encoded.1; row.estimatedArea = 9.25; row.exudate = 2
        let wound = try XCTUnwrap(WoundMaskCodec.encodeBase64(mask: raster.mask, mw: raster.mw, mh: raster.mh))
        let tissue = try XCTUnwrap(TissueMaskCodec.encodeBase64(tissue: raster.tissue, mask: raster.mask, mw: raster.mw, mh: raster.mh))
        var body: [String: Any] = ["image_id":row.imageId!, "image_w":64, "image_h":64, "doctor_verified":true,
            "area_cm2":9.25, "exudate":2, "wound_mask_png":wound, "tissue_mask_png":tissue]
        let raw = try XCTUnwrap(image.jpegData(compressionQuality: 0.9))
        let payload = try JSONSerialization.data(withJSONObject: body, options: [.sortedKeys])
        let files = try InstitutionPrivateExports.submissionContents(row: row, image: raw, raster: encoded.0, payload: payload)
        XCTAssertEqual(files["measurement-image.jpg"],raw)
        XCTAssertEqual(files["submission.json"],payload)
        XCTAssertEqual(files["wound.png"],Data(base64Encoded:wound))
        let metadata = try XCTUnwrap(JSONSerialization.jsonObject(with: files["measurement.json"]!) as? [String:Any])
        XCTAssertEqual(metadata["area_cm2"] as? Double,9.25)
        body["area_cm2"] = 1.0
        XCTAssertThrowsError(try InstitutionPrivateExports.submissionContents(row: row,image: raw,raster: encoded.0,
            payload: JSONSerialization.data(withJSONObject:body)))
        body["area_cm2"] = 9.25; body["wound_mask_png"] = "corrupt"
        XCTAssertThrowsError(try InstitutionPrivateExports.submissionContents(row: row,image: raw,raster: encoded.0,
            payload: JSONSerialization.data(withJSONObject:body)))
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at:root,withIntermediateDirectories:true)
        defer { try? FileManager.default.removeItem(at:root) }
        try InstitutionPrivateExports.writeDelivery(files:files,name:"test-revision",folder:root)
        let folder = try XCTUnwrap(FileManager.default.contentsOfDirectory(at:root,includingPropertiesForKeys:nil).first)
        XCTAssertEqual(Set(try FileManager.default.contentsOfDirectory(atPath:folder.path)), ["image.jpg", "overlay.png"])
        for (name, bytes) in try InstitutionPrivateExports.deliveryImages(files) { XCTAssertEqual(try Data(contentsOf:folder.appendingPathComponent(name)),bytes) }
        let wrapper = try InstitutionExportDocument(files:files)
        XCTAssertEqual(Set(wrapper.files.keys), ["image.jpg", "overlay.png"])
        var corrupt=files;corrupt["wound.png"]=Data([0])
        XCTAssertThrowsError(try InstitutionPrivateExports.writeDelivery(files:corrupt,name:"bad",folder:root))
        XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath:root.path).count,1)
        XCTAssertThrowsError(try InstitutionPrivateExports.deliver(files:files,name:"test",bookmark:Data([0])))
    }

    func testExportMatchesRealAnnotationEncoderAndRejectsRevisionMixup() throws {
        let (image,raster) = fixture()
        let encoded = try XCTUnwrap(EditRasterCodec.encode(raster))
        var row = Measurement(); row.id=78; row.doctorVerified=true
        row.imageId="synthetic-encoder-image"; row.wdCode="synthetic-case"
        row.imageW=64; row.imageH=64; row.rasterMeta=encoded.1
        row.estimatedArea=0.04; row.exudate=1
        let fractions=raster.tissueFrac()
        row.tissueGranulation=fractions["granulation"]; row.tissueSlough=fractions["slough"]
        row.tissueNecrosis=fractions["necrosis"]; row.tissueEpithelial=fractions["epithelial"]; row.tissueOther=fractions["other"]
        row.gtPolygon=PolygonJson.toJson([[[10,10],[11,10],[11,11],[10,11]]])
        let raw = try XCTUnwrap(image.jpegData(compressionQuality:0.9))
        let payload = try BackendClient.annotationPayload(code:row.wdCode!,gtPolygon:row.polygons[0],exudate:row.exudate,
            allPolygons:row.polygons,areaCm2:row.estimatedArea,imageId:row.imageId,imageW:64,imageH:64,
            tissueFrac:row.tissueFrac,tissueMaskPngBase64:TissueMaskCodec.encodeBase64(tissue:raster.tissue,mask:raster.mask,mw:raster.mw,mh:raster.mh),
            tissueRaster:raster,consentTrain:true,doctorVerified:true)
        let files = try InstitutionPrivateExports.submissionContents(row:row,image:raw,raster:encoded.0,payload:payload,medicalRecordNumber:" MRN123 ")
        XCTAssertEqual(files["submission.json"],payload)
        XCTAssertEqual(try InstitutionPrivateExports.deliveryName(files:files,at:Date(timeIntervalSince1970:0)),"MRN123_19700101_080000_000")
        let unsafe = try InstitutionPrivateExports.submissionContents(row:row,image:raw,raster:encoded.0,payload:payload,medicalRecordNumber:"../../A:123")
        XCTAssertEqual(try InstitutionPrivateExports.deliveryName(files:unsafe,at:Date(timeIntervalSince1970:0)),"______A_123_19700101_080000_000")
        let legacy = try InstitutionPrivateExports.submissionContents(row:row,image:raw,raster:encoded.0,payload:payload)
        XCTAssertTrue(try InstitutionPrivateExports.deliveryName(files:legacy).hasPrefix("無病歷號_"))
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at:root,withIntermediateDirectories:true)
        defer { try? FileManager.default.removeItem(at:root) }
        let sameName = try InstitutionPrivateExports.deliveryName(files:files,at:Date(timeIntervalSince1970:0))
        try InstitutionPrivateExports.writeDelivery(files:files,name:sameName,folder:root)
        try InstitutionPrivateExports.writeDelivery(files:files,name:sameName,folder:root)
        XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath:root.path).count,2)
        for mutation in 0..<4 {
            var changed=row
            switch mutation {
            case 0: changed.imageId="different-image"
            case 1: changed.wdCode="different-case"
            case 2: changed.gtPolygon=PolygonJson.toJson([[[1,1],[2,1],[2,2]]])
            default: changed.tissueGranulation=1.0
            }
            XCTAssertThrowsError(try InstitutionPrivateExports.submissionContents(row:changed,image:raw,raster:encoded.0,payload:payload))
        }
    }

    func testExportAvailabilityFollowsBuildPolicy() {
        #if WOUND_INSTITUTION_TESTS && !WOUND_ADMIN_EXPORT_DISABLED
        XCTAssertTrue(InstitutionExport.enabled)
        #else
        XCTAssertFalse(InstitutionExport.enabled)
        #endif
    }
    func testMismatchedCoordinateSpaceIsRejected() {
        var (image, raster) = fixture(); raster.canvasW = 63
        XCTAssertThrowsError(try InstitutionExport.validate(image: image, raster: raster))
    }
    func testMalformedMaskAndNonfiniteScaleAreRejected() {
        let (image, original) = fixture()
        var raster = original; raster.mask.removeLast()
        XCTAssertThrowsError(try InstitutionExport.validate(image: image, raster: raster))
        raster = original; raster.mScale = .nan
        XCTAssertThrowsError(try InstitutionExport.validate(image: image, raster: raster))
        raster = original; raster.tissue[0] = 9
        XCTAssertThrowsError(try InstitutionExport.validate(image: image, raster: raster))
    }
    func testCaptionIsOutsideImageAndCanvasWidthIsPreserved() throws {
        let (image, raster) = fixture()
        let overlay = try InstitutionExport.overlay(image: image, raster: raster,
            polygons: [], marker: nil, caption: "MMHPS20261007 test")
        XCTAssertEqual(overlay.cgImage?.width, 64)
        XCTAssertGreaterThan(overlay.cgImage?.height ?? 0, 64)
    }
    func testRetryIsIdempotentAndCorruptCopyIsRejected() throws {
        let (image, raster) = fixture()
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        func write() throws -> URL {
            try InstitutionExport.write(image: image, raster: raster, polygons: [], marker: nil,
                measurementID: 1, doctorVerified: false, exudate: nil, directory: directory)
        }
        let first = try write(); XCTAssertEqual(try write(), first)
        XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath: directory.path).count, 1)
        let metadata = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(contentsOf: first.appendingPathComponent("measurement.json"))) as? [String: Any])
        XCTAssertEqual(metadata["cloud_uploaded"] as? Bool, false)
        XCTAssertEqual(metadata["depth_included"] as? Bool, false)
        XCTAssertEqual(metadata["doctor_verified"] as? Bool, false)
        try Data("broken".utf8).write(to: first.appendingPathComponent("image.png"))
        XCTAssertThrowsError(try write())
    }
}
