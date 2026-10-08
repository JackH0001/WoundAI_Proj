import XCTest
import AVFoundation
import ImageIO
import UIKit
@testable import WoundLite

final class DepthPhotoAlignmentTests: XCTestCase {
    private func depthData() throws -> AVDepthData {
        var values: [Float] = [1, 2, 3, 4, 5, 6]
        let bytes = values.withUnsafeMutableBytes { Data($0) }
        return try AVDepthData(fromDictionaryRepresentation: [
            kCGImageAuxiliaryDataInfoData as String: bytes,
            kCGImageAuxiliaryDataInfoDataDescription as String: [
                "Width": 3, "Height": 2, "BytesPerRow": 12,
                "PixelFormat": kCVPixelFormatType_DepthFloat32
            ]
        ])
    }

    func testAllEightEXIFTransformsWithIndependentNonSquareFixture() throws {
        let cases: [(UIImage.Orientation, Int, Int, Int, [Float])] = [
            (.up, 1, 3, 2, [1,2,3,4,5,6]),
            (.upMirrored, 2, 3, 2, [3,2,1,6,5,4]),
            (.down, 3, 3, 2, [6,5,4,3,2,1]),
            (.downMirrored, 4, 3, 2, [4,5,6,1,2,3]),
            (.leftMirrored, 5, 2, 3, [1,4,2,5,3,6]),
            (.right, 6, 2, 3, [4,1,5,2,6,3]),
            (.rightMirrored, 7, 2, 3, [6,3,5,2,4,1]),
            (.left, 8, 2, 3, [3,6,2,5,1,4])
        ]
        let source = try depthData()
        for (orientation, exif, w, h, expected) in cases {
            let aligned = try XCTUnwrap(DepthCapture.fromPhoto(source, imageOrientation: orientation))
            XCTAssertEqual(aligned.width, w)
            XCTAssertEqual(aligned.height, h)
            XCTAssertEqual(aligned.map, expected, "EXIF \(exif)")
            XCTAssertEqual(aligned.sourceExifOrientation, exif)
        }
        // Derivative copies must not mutate the native buffer between captures.
        XCTAssertEqual(DepthCapture.from(source)?.map, [1,2,3,4,5,6])
    }

    func testPortraitRGBAndDepthHaveMatchingDimensionsAfterNormalization() throws {
        let format = UIGraphicsImageRendererFormat(); format.scale = 1
        let rgb = UIGraphicsImageRenderer(size: CGSize(width: 96, height: 64), format: format).image { ctx in
            UIColor.red.setFill(); ctx.fill(CGRect(x: 0, y: 0, width: 96, height: 64))
        }
        for orientation: UIImage.Orientation in [.up, .down, .left, .right, .upMirrored, .downMirrored, .leftMirrored, .rightMirrored] {
            let raw = UIImage(cgImage: try XCTUnwrap(rgb.cgImage), scale: 1, orientation: orientation)
            let normalized = LiteMeasureVM.normalize(raw)
            let depth = try XCTUnwrap(DepthCapture.fromPhoto(try depthData(), imageOrientation: orientation))
            XCTAssertEqual(normalized.imageOrientation, .up)
            XCTAssertTrue(depth.matchesImageAspect(width: normalized.cgImage!.width, height: normalized.cgImage!.height))
        }
    }

    func testLegacyAspectMismatchAndTruncatedDepthCannotProduceNewEstimate() {
        var depth = DepthCapture(map: Array(repeating: 0.3, count: 32*24), width: 32, height: 24,
                                 fx: 30, fy: 30, cx: 16, cy: 12, refWidth: 32, refHeight: 24,
                                 accuracy: "absolute", filtered: false)
        let polygon = [[[4,4],[20,4],[20,20],[4,20]]]
        XCTAssertFalse(depth.matchesImageAspect(width: 1536, height: 2048))
        XCTAssertNil(DepthAreaEstimator.estimate(polygons: polygon, depth: depth, imageW: 24, imageH: 32))
        XCTAssertNotNil(DepthAreaEstimator.estimate(polygons: polygon, depth: depth, imageW: 32, imageH: 24))
        depth.map.removeLast()
        XCTAssertNil(DepthAreaEstimator.estimate(polygons: polygon, depth: depth, imageW: 32, imageH: 24))
    }
}
