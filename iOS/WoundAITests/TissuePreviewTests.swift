import XCTest
import UIKit
#if WOUND_INSTITUTION_TESTS
@testable import WoundAIInstitution
#else
@testable import WoundMeasurementApp
#endif

final class TissuePreviewTests: XCTestCase {
    private func fixture() -> (UIImage, EditRaster) {
        let format = UIGraphicsImageRendererFormat(); format.scale = 1
        let image = UIGraphicsImageRenderer(size: CGSize(width: 16, height: 12), format: format).image { c in
            UIColor.white.setFill(); c.fill(CGRect(x: 0, y: 0, width: 16, height: 12))
        }
        return (image, EditRaster(mask: [1,0,1,1], tissue: [1,0,3,5], origMask: [1,0,1,1],
            rx0: 6, ry0: 2, mw: 2, mh: 2, mScale: 0.5, cm2PerPx: nil, canvasW: 16, canvasH: 12))
    }
    private func pixels(_ image: UIImage) -> [UInt8] {
        let cg = image.cgImage!
        var bytes = [UInt8](repeating: 0, count: cg.width*cg.height*4)
        let context = CGContext(data: &bytes, width: cg.width, height: cg.height, bitsPerComponent: 8,
            bytesPerRow: cg.width*4, space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
        context.draw(cg, in: CGRect(x: 0, y: 0, width: cg.width, height: cg.height))
        return bytes
    }
    func testAsymmetricMaskUsesSavedOriginScaleAndTransparency() throws {
        let (image, raster) = fixture()
        let overlay = try XCTUnwrap(TissuePreviewRenderer.render(image: image, raster: raster))
        XCTAssertEqual(overlay.cgImage?.width, 16); XCTAssertEqual(overlay.cgImage?.height, 12)
        let data = pixels(overlay)
        func alpha(_ x: Int, _ y: Int) -> UInt8 { data[(y*16+x)*4+3] }
        XCTAssertEqual(alpha(0,0),0)
        XCTAssertGreaterThan(alpha(6,2),100)
        XCTAssertEqual(alpha(8,2),0, "Outside mask stays transparent, not classified")
        XCTAssertGreaterThan(alpha(8,4),100)
        XCTAssertEqual(alpha(10,4),0)
    }
    func testRejectsWrongCanvasAndOrientation() {
        let (image, original) = fixture(); var raster = original; raster.canvasW = 15
        XCTAssertNil(TissuePreviewRenderer.render(image: image, raster: raster))
        let rotated = UIImage(cgImage: image.cgImage!, scale: 1, orientation: .left)
        XCTAssertNil(TissuePreviewRenderer.render(image: rotated, raster: original))
    }
    func testRejectsTruncationUnknownTissueAndNonfiniteScale() {
        let (image, original) = fixture()
        var raster = original; raster.tissue.removeLast()
        XCTAssertNil(TissuePreviewRenderer.render(image: image, raster: raster))
        raster = original; raster.tissue[0] = 9
        XCTAssertNil(TissuePreviewRenderer.render(image: image, raster: raster))
        raster = original; raster.mScale = .nan
        XCTAssertNil(TissuePreviewRenderer.render(image: image, raster: raster))
    }
    func testSavedPixelsAreNotReclassifiedFromPhoto() throws {
        let (image, raster) = fixture()
        let before = raster.tissue
        let data = pixels(try XCTUnwrap(TissuePreviewRenderer.render(image: image, raster: raster)))
        let offset = (2*16+6)*4
        XCTAssertGreaterThan(data[offset+1],data[offset], "Saved granulation is green even on white input")
        XCTAssertEqual(raster.tissue,before)
    }
    @MainActor func testTissueToggleDoesNotResetZoomAndSharesViewport() throws {
        let (image, raster) = fixture()
        let overlay = try XCTUnwrap(TissuePreviewRenderer.render(image: image, raster: raster))
        let view = WoundZoomPreviewView(frame: CGRect(x:0,y:0,width:320,height:240))
        view.configure(image:image, polygons:[], imageW:16, imageH:12, resetToken:0, tissueOverlay:overlay)
        view.layoutIfNeeded(); view.scrollView.setZoomScale(3, animated:false)
        view.configure(image:image, polygons:[], imageW:16, imageH:12, resetToken:0, tissueOverlay:nil)
        view.layoutIfNeeded()
        XCTAssertEqual(view.scrollView.zoomScale,3)
        XCTAssertTrue(view.tissueView.superview === view.imageView.superview)
        XCTAssertEqual(view.tissueView.frame,view.imageView.frame)
        XCTAssertNil(view.tissueView.image)
    }
    func testAutomaticPreviewDoesNotInventEmptyWound() {
        let (image, _) = fixture()
        XCTAssertNil(TissuePreviewRenderer.automatic(image:image,polygons:[],wbGains:nil))
    }
}
