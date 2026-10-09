import XCTest
import AVFoundation
import ImageIO
import UIKit
#if WOUND_MEDICAL_TESTS
@testable import WoundMeasurementApp
#else
@testable import WoundLite
#endif

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

    private func normalize(_ image: UIImage) -> UIImage {
        #if WOUND_MEDICAL_TESTS
        return MeasureViewModel.normalizeForBackend(image)
        #else
        return LiteMeasureVM.normalize(image)
        #endif
    }

    private func fixture(mask: Bool) -> UIImage {
        let colors: [UIColor] = [.red, .green, .blue, .yellow, .magenta, .cyan]
        let format = UIGraphicsImageRendererFormat(); format.scale = 1; format.preferredRange = .standard
        return UIGraphicsImageRenderer(size: CGSize(width: 96, height: 64), format: format).image { ctx in
            for i in 0..<6 {
                (mask ? ([0,2,4].contains(i) ? UIColor.white : .black) : colors[i]).setFill()
                ctx.fill(CGRect(x: (i % 3)*32, y: (i / 3)*32, width: 32, height: 32))
            }
        }
    }

    private func sample(_ image: CGImage, x: Int, y: Int) throws -> [UInt8] {
        let pixel = try XCTUnwrap(image.cropping(to: CGRect(x:x, y:y, width:1, height:1)))
        var rgba = [UInt8](repeating:0, count:4)
        try rgba.withUnsafeMutableBytes { bytes in
            let context = try XCTUnwrap(CGContext(data: bytes.baseAddress, width:1, height:1,
                bitsPerComponent:8, bytesPerRow:4, space:CGColorSpaceCreateDeviceRGB(),
                bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue | CGBitmapInfo.byteOrder32Big.rawValue))
            context.draw(pixel, in:CGRect(x:0,y:0,width:1,height:1))
        }
        return Array(rgba.prefix(3))
    }

    func testAllEightNonSymmetricRGBMaskAndDepthCoordinates() throws {
        let cases: [(UIImage.Orientation, [Int])] = [
            (.up,[1,2,3,4,5,6]), (.upMirrored,[3,2,1,6,5,4]),
            (.down,[6,5,4,3,2,1]), (.downMirrored,[4,5,6,1,2,3]),
            (.leftMirrored,[1,4,2,5,3,6]), (.right,[4,1,5,2,6,3]),
            (.rightMirrored,[6,3,5,2,4,1]), (.left,[3,6,2,5,1,4])]
        let palette: [[UInt8]] = [[255,0,0],[0,255,0],[0,0,255],[255,255,0],[255,0,255],[0,255,255]]
        for (orientation, expected) in cases {
            let rgb = normalize(UIImage(cgImage:try XCTUnwrap(fixture(mask:false).cgImage), scale:1, orientation:orientation))
            let mask = normalize(UIImage(cgImage:try XCTUnwrap(fixture(mask:true).cgImage), scale:1, orientation:orientation))
            let cg = try XCTUnwrap(rgb.cgImage), maskCG = try XCTUnwrap(mask.cgImage)
            let depth = try XCTUnwrap(DepthCapture.fromPhoto(try depthData(), imageOrientation:orientation))
            XCTAssertEqual(cg.width, depth.width*32); XCTAssertEqual(cg.height, depth.height*32)
            XCTAssertEqual(maskCG.width,cg.width); XCTAssertEqual(maskCG.height,cg.height)
            for i in 0..<6 {
                let x = (i % depth.width)*32+16, y = (i / depth.width)*32+16
                let got = try sample(cg,x:x,y:y)
                for c in 0..<3 { XCTAssertEqual(Int(got[c]),Int(palette[expected[i]-1][c]),accuracy:2, "RGB orientation \(orientation), cell \(i)") }
                let selected = try sample(maskCG,x:x,y:y)[0] > 127
                XCTAssertEqual(selected,[1,3,5].contains(expected[i]),"mask orientation \(orientation), cell \(i)")
                XCTAssertEqual(depth.map[i],Float(expected[i]))
            }
        }
    }

    func testSyntheticDepthWithoutCalibrationDoesNotInventIntrinsics() throws {
        let source = try depthData()
        XCTAssertNil(source.cameraCalibrationData)
        let aligned = try XCTUnwrap(DepthCapture.fromPhoto(source,imageOrientation:.right))
        XCTAssertEqual(aligned.fx,0); XCTAssertEqual(aligned.refWidth,0)
        // This fixture exercises pixel transforms, not Apple's calibration transform.
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
