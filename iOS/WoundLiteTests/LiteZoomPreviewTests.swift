import XCTest
import UIKit
#if WOUND_MEDICAL_TESTS
@testable import WoundMeasurementApp
#else
@testable import WoundLite
#endif

@MainActor
final class LiteZoomPreviewTests: XCTestCase {
    private let polygon = [[[100, 50], [300, 50], [300, 150], [100, 150]]]
    private func image() -> UIImage {
        UIGraphicsImageRenderer(size: CGSize(width: 400, height: 200)).image { context in
            UIColor.white.setFill(); context.fill(CGRect(x: 0, y: 0, width: 400, height: 200))
        }
    }
    private func viewport(_ image: UIImage) -> WoundZoomPreviewView {
        let view = WoundZoomPreviewView(frame: CGRect(x: 0, y: 0, width: 320, height: 280))
        view.configure(image: image, polygons: polygon, imageW: 400, imageH: 200, resetToken: 0)
        view.layoutIfNeeded()
        return view
    }
    func testImageAndContourShareZoomTransformAndCoordinates() throws {
        let view = viewport(image())
        XCTAssertTrue(view.viewForZooming(in: view.scrollView) === view.contentView)
        XCTAssertTrue(view.imageView.superview === view.contentView)
        XCTAssertTrue(view.contourLayer.superlayer === view.contentView.layer)
        XCTAssertEqual(view.imageView.frame, CGRect(x: 0, y: 0, width: 320, height: 160))
        let rect = try XCTUnwrap(view.contourLayer.path).boundingBoxOfPath
        XCTAssertEqual(rect, CGRect(x: 80, y: 40, width: 160, height: 80))
        view.scrollView.setZoomScale(3, animated: false)
        XCTAssertEqual(view.scrollView.zoomScale, 3)
        XCTAssertEqual(view.contourLayer.path?.boundingBoxOfPath, rect)
        XCTAssertEqual(view.imageView.bounds.size, view.contentView.bounds.size)
    }
    func testSwiftUIRefreshPreservesInspectionZoomButResetRestoresFit() {
        let source = image(); let view = viewport(source)
        view.scrollView.setZoomScale(3, animated: false)
        view.configure(image: source, polygons: polygon, imageW: 400, imageH: 200, resetToken: 0)
        view.layoutIfNeeded()
        XCTAssertEqual(view.scrollView.zoomScale, 3)
        view.configure(image: source, polygons: polygon, imageW: 400, imageH: 200, resetToken: 1)
        view.layoutIfNeeded()
        XCTAssertEqual(view.scrollView.zoomScale, 1)
        XCTAssertEqual(view.scrollView.contentInset.top, 60, accuracy: 0.01)
        XCTAssertEqual(view.scrollView.contentOffset.y, -60, accuracy: 0.01)
    }
    func testNewPhotoAndViewportResizeResetOldTransform() {
        let source = image(); let view = viewport(source)
        view.scrollView.setZoomScale(4, animated: false)
        view.configure(image: image(), polygons: polygon, imageW: 400, imageH: 200, resetToken: 0)
        view.layoutIfNeeded()
        XCTAssertEqual(view.scrollView.zoomScale, 1)
        view.scrollView.setZoomScale(2, animated: false)
        view.frame.size = CGSize(width: 280, height: 320)
        view.setNeedsLayout(); view.layoutIfNeeded()
        XCTAssertEqual(view.scrollView.zoomScale, 1)
        XCTAssertEqual(view.contentView.bounds.size, CGSize(width: 280, height: 140))
    }
    func testTwoFingerPanDoesNotEditContourAndNewContourRefreshesWithoutReset() {
        let source = image(); let view = viewport(source)
        XCTAssertEqual(view.scrollView.panGestureRecognizer.minimumNumberOfTouches, 2)
        XCTAssertEqual(view.scrollView.panGestureRecognizer.maximumNumberOfTouches, 2)
        view.scrollView.setZoomScale(2, animated: false)
        let changed = [[[0, 0], [100, 0], [100, 100]]]
        view.configure(image: source, polygons: changed, imageW: 400, imageH: 200, resetToken: 0)
        XCTAssertEqual(view.scrollView.zoomScale, 2)
        XCTAssertEqual(view.contourLayer.path?.boundingBoxOfPath, CGRect(x: 0, y: 0, width: 80, height: 80))
    }
    func testMarkerSharesPhotoCoordinatesAndZoomWhileTogglePreservesInspection() throws {
        let source = image(); let view = viewport(source)
        let marker = [[[20, 10], [60, 10], [60, 50], [20, 50]]]
        view.configure(image: source, polygons: polygon, imageW: 400, imageH: 200,
                       resetToken: 0, markerPolygons: marker)
        XCTAssertTrue(view.markerLayer.superlayer === view.imageView.superview?.layer)
        XCTAssertEqual(view.markerLayer.path?.boundingBoxOfPath, CGRect(x: 16, y: 8, width: 32, height: 32))
        view.scrollView.setZoomScale(3, animated: false)
        view.configure(image: source, polygons: [], imageW: 400, imageH: 200,
                       resetToken: 0, markerPolygons: marker)
        XCTAssertEqual(view.scrollView.zoomScale, 3)
        XCTAssertTrue(try XCTUnwrap(view.contourLayer.path).isEmpty)
        XCTAssertFalse(try XCTUnwrap(view.markerLayer.path).isEmpty)
        view.configure(image: source, polygons: polygon, imageW: 400, imageH: 200, resetToken: 0)
        XCTAssertEqual(view.scrollView.zoomScale, 3)
        XCTAssertTrue(try XCTUnwrap(view.markerLayer.path).isEmpty)
        XCTAssertFalse(try XCTUnwrap(view.contourLayer.path).isEmpty)
    }

    func testMalformedCoordinatesDoNotCrashPreview() throws {
        let source = image(); let view = viewport(source)
        view.configure(image: source, polygons: [[[1], [2], [3]]], imageW: 400, imageH: 200,
                       resetToken: 0, markerPolygons: [[[0, 0], [], [1, 1]]])
        XCTAssertTrue(try XCTUnwrap(view.contourLayer.path).isEmpty)
        XCTAssertTrue(try XCTUnwrap(view.markerLayer.path).isEmpty)
    }

}
