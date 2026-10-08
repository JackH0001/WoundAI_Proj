import XCTest
import UIKit
@testable import WoundMeasurementApp

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
    func testStandardMedicalBuildDoesNotExport() { XCTAssertFalse(InstitutionExport.enabled) }
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
