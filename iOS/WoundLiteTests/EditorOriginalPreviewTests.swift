import XCTest
import UIKit
@testable import WoundLite

final class EditorOriginalPreviewTests: XCTestCase {
    @MainActor
    private func model(boundaryOnly: Bool = true) -> EditCanvasModel {
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        let image = UIGraphicsImageRenderer(size: CGSize(width: 128, height: 128), format: format).image { ctx in
            UIColor.orange.setFill()
            ctx.fill(CGRect(x: 0, y: 0, width: 128, height: 128))
        }
        let model = EditCanvasModel(image: image, initialPolygons: [[[32,32],[96,32],[96,96],[32,96]]],
                                    originalArea: nil, tissueFrac: [:], mmPerPx: nil, resume: nil,
                                    wbGains: nil, boundaryOnly: boundaryOnly)
        model.boxSize = CGSize(width: 300, height: 300)
        model.fitFull()
        return model
    }

    @MainActor
    func testOriginalHidesEntireOverlayAndRestoresIdenticalPixels() throws {
        for boundaryOnly in [true, false] {
            let m = model(boundaryOnly: boundaryOnly)
            let before = try XCTUnwrap(m.overlayImage()?.dataProvider?.data) as Data
            XCTAssertTrue(before.contains { $0 != 0 })
            m.setOriginalPreview(true)
            XCTAssertTrue(m.peeking)
            XCTAssertNil(m.overlayImage(), "Original view must also hide cyan boundaries")
            m.setOriginalPreview(false)
            let after = try XCTUnwrap(m.overlayImage()?.dataProvider?.data) as Data
            XCTAssertEqual(before, after)
        }
    }

    @MainActor
    func testTouchingOriginalDoesNotPaintOrCreateUndoHistory() throws {
        let m = model()
        let before = try XCTUnwrap(m.finish()).raster
        m.setOriginalPreview(true)
        m.touchesChanged(.began, [CGPoint(x: 20, y: 20)])
        m.touchesChanged(.moved, [CGPoint(x: 40, y: 40)])
        m.touchesChanged(.ended, [])
        let after = try XCTUnwrap(m.finish()).raster
        XCTAssertEqual(after.mask, before.mask)
        XCTAssertEqual(after.tissue, before.tissue)
        XCTAssertEqual(after.origMask, before.origMask)
        XCTAssertEqual(m.undoCount, 0)
        XCTAssertEqual(m.redoCount, 0)
    }

    @MainActor
    func testActualEditorFinishAndUndoDoNotChangeUneditedAIRecord() async throws {
        let m = model(), vm = LiteMeasureVM()
        vm.source = "cloud"; vm.polys = [[[32,32],[96,32],[96,96],[32,96]]]
        vm.estimate = DepthAreaResult(surfaceAreaCm2: 11.22, projectedAreaCm2: 8.39,
            volumeMl: nil, maxDepthMm: nil, coverage: 1, medianDistanceM: 0.3, tiltDeg: 43)
        let original = vm.polys
        let untouched = try XCTUnwrap(m.finish()); XCTAssertEqual(untouched.iou, 1)
        await vm.applyManual(untouched.all, correctionIoU: untouched.iou)
        XCTAssertEqual(vm.polys, original); XCTAssertEqual(vm.estimate?.surfaceAreaCm2, 11.22)
        XCTAssertEqual(vm.source, "cloud"); XCTAssertFalse(vm.hasManualContour)
        m.touchesChanged(.began, [CGPoint(x: 20, y: 20)]); m.touchesChanged(.ended, [])
        XCTAssertLessThan(try XCTUnwrap(m.finish()).iou, 1)
        m.undo()
        let restored = try XCTUnwrap(m.finish()); XCTAssertEqual(restored.iou, 1)
        await vm.applyManual(restored.all, correctionIoU: restored.iou)
        XCTAssertEqual(vm.polys, original); XCTAssertEqual(vm.estimate?.surfaceAreaCm2, 11.22)
        XCTAssertFalse(vm.hasManualContour)
    }

    @MainActor
    func testReturnToEditingCanPaintUndoAndRedo() throws {
        let m = model()
        let before = try XCTUnwrap(m.finish()).raster.mask
        m.setOriginalPreview(true)
        m.setOriginalPreview(false)
        m.touchesChanged(.began, [CGPoint(x: 20, y: 20)])
        m.touchesChanged(.ended, [])
        let painted = try XCTUnwrap(m.finish()).raster.mask
        XCTAssertNotEqual(painted, before)
        XCTAssertEqual(m.undoCount, 1)
        m.undo()
        XCTAssertEqual(try XCTUnwrap(m.finish()).raster.mask, before)
        m.redo()
        XCTAssertEqual(try XCTUnwrap(m.finish()).raster.mask, painted)
    }
}
