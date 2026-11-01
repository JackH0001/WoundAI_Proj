import XCTest
import UIKit
@testable import WoundLite

final class LiteRawDepthPacketTests: XCTestCase {
    private func depth() -> DepthCapture {
        var d = DepthCapture(map: Array(repeating: 0.3, count: 64), width: 8, height: 8,
                             fx: 50, fy: 50, cx: 32, cy: 32, refWidth: 64, refHeight: 64,
                             accuracy: "absolute", filtered: false, rgbWidth: 64, rgbHeight: 64)
        d.sourceExifOrientation = 1
        return d
    }
    private func jpeg() -> Data {
        let f = UIGraphicsImageRendererFormat(); f.scale = 1
        return UIGraphicsImageRenderer(size: CGSize(width: 64,height: 64),format: f).image { c in
            UIColor.blue.setFill(); c.fill(CGRect(x:0,y:0,width:64,height:64))
        }.jpegData(compressionQuality: 0.92)!
    }
    @MainActor
    func testIngestRetainsOneCanonicalJPEGForCloudAndLocalDepthBinding() async throws {
        let vm = LiteMeasureVM()
        let raw = try XCTUnwrap(UIImage(data: jpeg()))
        _ = await vm.ingest(raw, depth: nil) // No cloud request or consent mutation.
        let captured = try XCTUnwrap(vm.captureJPEG)
        XCTAssertEqual(captured, raw.jpegData(compressionQuality: 0.92))
        let packet = try LiteRawDepthPacket.make(depth: depth(), jpeg: captured)
        XCTAssertEqual(packet.jpeg, captured)
        let format = UIGraphicsImageRendererFormat(); format.scale = 1
        let other = UIGraphicsImageRenderer(size: CGSize(width:64,height:64),format:format).image { c in
            UIColor.red.setFill(); c.fill(CGRect(x:0,y:0,width:64,height:64))
        }
        _ = await vm.ingest(other, depth: nil)
        XCTAssertNotEqual(vm.captureJPEG, captured)
    }
    @MainActor
    func testCaptureRejectsDifferentOrPartialPriorRGBDimensionsInsteadOfRebinding() async throws {
        let photo = try XCTUnwrap(UIImage(data: jpeg()))
        for (w, h) in [(128, 128), (0, 64), (64, 0)] {
            var d = depth()
            d.rgbWidth = w
            d.rgbHeight = h
            let vm = LiteMeasureVM()
            _ = await vm.ingest(photo, depth: d)
            XCTAssertNil(vm.depth, "Conflicting capture dimensions \(w)×\(h)")
            XCTAssertNil(vm.estimate)
            XCTAssertNil(vm.cloudBinding)
            XCTAssertNotNil(vm.image)
        }
    }

    @MainActor
    func testCaptureRejectsDepthOrientedForAnotherPhotoOfSameDimensions() async throws {
        let photo = try XCTUnwrap(UIImage(data: jpeg()))
        for exif in [2, 3, 6] {
            var d = depth()
            d.sourceExifOrientation = exif
            let vm = LiteMeasureVM()
            _ = await vm.ingest(photo, depth: d)
            XCTAssertNil(vm.depth, "Depth used EXIF \(exif) but this camera photo is upright")
            XCTAssertNil(vm.estimate)
            XCTAssertNil(vm.cloudBinding)
        }
    }

    func testExactFloatBitsAndHonestProvenance() throws {
        var d = depth();d.map[0] = Float(bitPattern: 0x7fc01234)
        let j = jpeg();let p = try LiteRawDepthPacket.make(depth:d,jpeg:j)
        XCTAssertEqual(p.jpeg,j);XCTAssertEqual(p.rawDepth.count,256)
        XCTAssertEqual(Array(p.rawDepth.prefix(4)),[0x34,0x12,0xc0,0x7f])
        let m = try XCTUnwrap(JSONSerialization.jsonObject(with:p.metadata) as? [String:Any])
        XCTAssertEqual(m["schema"] as? String,"lite.rgbd/1")
        XCTAssertEqual(m["pose"] as? String,"unavailable")
        XCTAssertEqual(m["registration"] as? String,"not_verified")
    }

    @MainActor
    func testFreshCameraMapCanBindOnceWithAllMatchingPhotoOrientations() async throws {
        let pixels = try XCTUnwrap(UIImage(data: jpeg())?.cgImage)
        let orientations: [UIImage.Orientation] = [.up, .upMirrored, .down, .downMirrored,
                                                    .left, .leftMirrored, .right, .rightMirrored]
        for orientation in orientations {
            let raw = UIImage(cgImage: pixels, scale: 1, orientation: orientation)
            var d = depth()
            d.rgbWidth = 0
            d.rgbHeight = 0
            d.sourceExifOrientation = Int(try XCTUnwrap(DepthCapture.exifOrientation(orientation)).rawValue)
            let vm = LiteMeasureVM()
            _ = await vm.ingest(raw, depth: d)
            let paired = try XCTUnwrap(vm.depth)
            XCTAssertEqual(paired.rgbWidth, 64)
            XCTAssertEqual(paired.rgbHeight, 64)
            XCTAssertEqual(paired.sourceExifOrientation, d.sourceExifOrientation)
            XCTAssertEqual(paired.map.map(\.bitPattern), d.map.map(\.bitPattern))
            let packet = try LiteRawDepthPacket.make(depth: paired, jpeg: XCTUnwrap(vm.captureJPEG))
            XCTAssertEqual(packet.rawDepth.count, d.map.count * 4)
        }
    }
    func testCaptureV2IsStableAcrossSerializationAndBindsTimeToPacket() throws {
        var d = depth()
        d.captureProvenance = .init(id: UUID(uuidString: "00000000-0000-4000-8000-000000000001")!,
                                    observedAt: Date(timeIntervalSince1970: 1_700_000_000))
        let image = jpeg()
        let packet = try LiteRawDepthPacket.make(depth: d, jpeg: image)
        let metadata = try XCTUnwrap(JSONSerialization.jsonObject(with: packet.metadata) as? [String: Any])
        XCTAssertEqual(metadata["schema"] as? String, "lite.rgbd/2")
        XCTAssertEqual(metadata["captured_at"] as? String, "2023-11-14T22:13:20.000Z")
        XCTAssertEqual(metadata["capture_time_source"] as? String, "photo_callback_wall_clock")
        XCTAssertEqual(metadata["pose"] as? String, "unavailable")
        XCTAssertEqual(metadata["registration"] as? String, "not_verified")
        XCTAssertEqual(try LiteRawDepthPacket.make(depth: d, jpeg: image).metadata, packet.metadata)
        d.captureProvenance = .init(observedAt: Date(timeIntervalSince1970: 1_700_000_100))
        let other = try LiteRawDepthPacket.make(depth: d, jpeg: image)
        XCTAssertNotEqual(other.metadata, packet.metadata)
        XCTAssertEqual(other.rawDepth, packet.rawDepth)
    }

    func testMissingOrientationAndAspectMismatchRejected() {
        var d = depth();d.sourceExifOrientation = nil
        XCTAssertThrowsError(try LiteRawDepthPacket.make(depth:d,jpeg:jpeg()))
        d = depth();d.rgbWidth = 48
        XCTAssertThrowsError(try LiteRawDepthPacket.make(depth:d,jpeg:jpeg()))
    }

    @MainActor
    func testPortraitCameraNormalizationBindsTheResizedPixelsAndKeepsDepthSamples() async throws {
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        let landscape = UIGraphicsImageRenderer(size: CGSize(width: 2400, height: 1200), format: format).image { ctx in
            UIColor.blue.setFill()
            ctx.fill(CGRect(x: 0, y: 0, width: 1200, height: 1200))
        }
        let raw = UIImage(cgImage: try XCTUnwrap(landscape.cgImage), scale: 1, orientation: .right)
        let samples = (0..<128).map { Float(0.30) + Float($0) / 100_000 }
        let d = DepthCapture(map: samples, width: 8, height: 16, fx: 20, fy: 20, cx: 4, cy: 8,
                             refWidth: 8, refHeight: 16, accuracy: "absolute", filtered: false, sourceExifOrientation: 6)
        let vm = LiteMeasureVM()
        _ = await vm.ingest(raw, depth: d)
        let paired = try XCTUnwrap(vm.depth)
        XCTAssertEqual([paired.rgbWidth, paired.rgbHeight], [1024, 2048])
        let packet = try LiteRawDepthPacket.make(depth: paired, jpeg: XCTUnwrap(vm.captureJPEG))
        XCTAssertEqual(packet.rawDepth, samples.withUnsafeBytes { Data($0) })
        let size = try LiteServiceBytes.imageSize(packet.jpeg)
        XCTAssertEqual([size.0, size.1], [1024, 2048])
    }
    func testBadDimensionsAndTruncatedDepthRejected() {
        var d = depth();d.map.removeLast()
        XCTAssertThrowsError(try LiteRawDepthPacket.make(depth:d,jpeg:jpeg()))
        d = depth();d.width = Int.max
        XCTAssertThrowsError(try LiteRawDepthPacket.make(depth:d,jpeg:jpeg()))
    }
    func testMultipartRejectsUnsafeBoundaryAndPreservesBytes() throws {
        let p = try LiteRawDepthPacket.make(depth:depth(),jpeg:jpeg())
        XCTAssertThrowsError(try p.multipart(boundary:"bad\r\nheader"))
        let boundary = "WoundLite-0123456789abcdef"
        let body = try p.multipart(boundary:boundary)
        XCTAssertNotNil(body.range(of:p.rawDepth))
        XCTAssertNotNil(body.range(of:p.jpeg))
        XCTAssertNotNil(body.range(of:p.metadata))
        XCTAssertTrue(body.suffix(Data(("--"+boundary+"--\r\n").utf8).count) == Data(("--"+boundary+"--\r\n").utf8))
    }
    func testInvalidImageAndIntrinsicsRejected() {
        XCTAssertThrowsError(try LiteRawDepthPacket.make(depth:depth(),jpeg:Data()))
        var d = depth();d.fx = .nan
        XCTAssertThrowsError(try LiteRawDepthPacket.make(depth:d,jpeg:jpeg()))
        d = depth();d.refHeight = 32
        XCTAssertThrowsError(try LiteRawDepthPacket.make(depth:d,jpeg:jpeg()))
    }
}
