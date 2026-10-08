import XCTest
import UIKit
import SwiftUI
@testable import WoundMeasurementApp

final class EditorGestureTests: XCTestCase {
    @MainActor
    private func model(boundaryOnly: Bool = true) async -> EditCanvasModel {
        let f = UIGraphicsImageRendererFormat(); f.scale = 1
        let image = UIGraphicsImageRenderer(size: CGSize(width: 512, height: 512), format: f).image { ctx in
            UIColor.orange.setFill(); ctx.fill(CGRect(x: 0, y: 0, width: 512, height: 512))
        }
        let m = EditCanvasModel(image: image,
            initialPolygons: [[[220,220],[280,220],[280,280],[220,280]]],
            originalArea: 10, tissueFrac: [:], mmPerPx: 0.5, resume: nil,
            wbGains: nil, boundaryOnly: boundaryOnly)
        await m.runSeed()
        m.boxSize = CGSize(width: 512, height: 512); m.fitFull()
        return m
    }

    @MainActor
    private func equal(_ a: EditCanvasModel.Snap, _ b: EditCanvasModel.Snap,
                       file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertEqual(a.m, b.m, file: file, line: line)
        XCTAssertEqual(a.t, b.t, file: file, line: line)
        XCTAssertEqual(a.orig, b.orig, file: file, line: line)
        XCTAssertEqual(a.auto, b.auto, file: file, line: line)
        XCTAssertEqual(a.mw, b.mw, file: file, line: line)
        XCTAssertEqual(a.mh, b.mh, file: file, line: line)
        XCTAssertEqual(a.rx0, b.rx0, file: file, line: line)
        XCTAssertEqual(a.ry0, b.ry0, file: file, line: line)
    }

    @MainActor
    func testSecondFingerRollsBackFirstStampEvenAfterROIExpansion() async {
        for boundaryOnly in [true, false] {
            let m = await model(boundaryOnly: boundaryOnly), before = m.snap()
            m.touchesChanged(.began, [CGPoint(x: 180, y: 180)])
            XCTAssertNotEqual(m.snap().mw, before.mw, "Exercise the expansion path")
            XCTAssertGreaterThan(m.snap().m.filter { $0 != 0 }.count, before.m.filter { $0 != 0 }.count, "First finger must actually paint a dot before rollback")
            m.touchesChanged(.began, [CGPoint(x: 180, y: 180), CGPoint(x: 150, y: 150)])
            m.touchesChanged(.ended, [CGPoint(x: 180, y: 180)])
            m.touchesChanged(.moved, [CGPoint(x: 50, y: 50)])
            m.touchesChanged(.ended, [])
            equal(m.snap(), before)
            XCTAssertEqual(m.undoCount, 0)
            XCTAssertEqual(m.finish()?.iou, 1)
        }
    }

    @MainActor
    func testExpandedStrokeUndoAndRedoPreserveGeometryAndPixels() async {
        let m = await model(), before = m.snap()
        let offset = m.viewOffset, scale = m.viewScale
        m.touchesChanged(.began, [CGPoint(x: 180, y: 180)])
        m.touchesChanged(.moved, [CGPoint(x: 50, y: 50)])
        m.touchesChanged(.ended, [])
        let painted = m.snap()
        XCTAssertNotEqual(painted.mw, before.mw)
        XCTAssertEqual(m.viewOffset, offset); XCTAssertEqual(m.viewScale, scale)
        XCTAssertEqual(m.undoCount, 1)
        m.undo(); equal(m.snap(), before)
        m.redo(); equal(m.snap(), painted)
    }

    @MainActor
    func testCancelledStrokeDoesNotCommitPixelsOrUndoEntry() async {
        let m = await model(), before = m.snap()
        m.touchesChanged(.began, [CGPoint(x: 180, y: 180)])
        m.touchesChanged(.cancelled, [])
        equal(m.snap(), before); XCTAssertEqual(m.undoCount, 0)
    }

    @MainActor
    func testMovementWhoseTouchBeganWhileInputWasBlockedCannotPaint() async {
        let m = await model(), before = m.snap()
        m.touchesChanged(.moved, [CGPoint(x: 180, y: 180)])
        m.touchesChanged(.ended, [])
        equal(m.snap(), before); XCTAssertEqual(m.undoCount, 0)
    }

    @MainActor
    func testCombinedPinchAndPanKeepsImagePointUnderCentroid() async {
        let m = await model(), before = m.snap()
        let initial = CGPoint(x: 140, y: 100)
        let imagePoint = CGPoint(x: m.viewOffset.x + initial.x / m.k(),
                                 y: m.viewOffset.y + initial.y / m.k())
        m.touchesChanged(.began, [CGPoint(x: 90, y: 100), CGPoint(x: 190, y: 100)])
        m.touchesChanged(.moved, [CGPoint(x: 60, y: 80), CGPoint(x: 260, y: 80)])
        XCTAssertEqual((imagePoint.x - m.viewOffset.x) * m.k(), 160, accuracy: 0.001)
        XCTAssertEqual((imagePoint.y - m.viewOffset.y) * m.k(), 80, accuracy: 0.001)
        m.touchesChanged(.ended, []); equal(m.snap(), before)
    }

    @MainActor
    func testPinchRollbackPreservesPreviouslyCommittedUndo() async {
        let m = await model()
        m.touchesChanged(.began, [CGPoint(x: 285, y: 250)])
        m.touchesChanged(.ended, [])
        let prior = m.snap(), count = m.undoCount
        m.touchesChanged(.began, [CGPoint(x: 180, y: 180)])
        m.touchesChanged(.began, [CGPoint(x: 180, y: 180), CGPoint(x: 100, y: 100)])
        m.touchesChanged(.ended, [])
        equal(m.snap(), prior); XCTAssertEqual(m.undoCount, count)
    }
    @MainActor
    func testMedicalEditorRendersWithUsableCanvas() async throws {
        let format = UIGraphicsImageRendererFormat(); format.scale = 1
        let image = UIGraphicsImageRenderer(size: CGSize(width: 512, height: 512), format: format).image { c in
            UIColor.systemOrange.setFill(); c.fill(CGRect(x: 0, y: 0, width: 512, height: 512))
        }
        let view = WoundEditView(image: image,
            initialPolygons: [[[180,180],[330,180],[330,330],[180,330]]],
            originalArea: 10, tissueFrac: [:], exudate: 1, mmPerPx: 0.5,
            resume: nil, wbGains: nil, onCancel: {}, onDone: { _,_,_,_,_,_ in })
        let scene = try XCTUnwrap(UIApplication.shared.connectedScenes.first as? UIWindowScene)
        let prior = scene.windows.first(where: { $0.isKeyWindow })
        let window = UIWindow(windowScene: scene)
        let host = UIHostingController(rootView: view)
        window.rootViewController = host; window.makeKeyAndVisible()
        defer { window.isHidden = true; prior?.makeKey() }
        try await Task.sleep(nanoseconds: 500_000_000)
        host.view.layoutIfNeeded()
        func canvas(_ v: UIView) -> UIView? {
            if String(describing: type(of: v)).contains("TouchProxyUIView") { return v }
            return v.subviews.lazy.compactMap { canvas($0) }.first
        }
        let touchCanvas = try XCTUnwrap(canvas(host.view))
        XCTAssertGreaterThan(touchCanvas.bounds.height, 200)
        XCTAssertGreaterThan(touchCanvas.bounds.width, 250)
        let screenshot = UIGraphicsImageRenderer(bounds: host.view.bounds).image { _ in
            host.view.drawHierarchy(in: host.view.bounds, afterScreenUpdates: true)
        }
        let attachment = XCTAttachment(image: screenshot)
        attachment.name = "medical-editor-synthetic-preview"
        attachment.lifetime = .keepAlways; add(attachment)
    }

}
