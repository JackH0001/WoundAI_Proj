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
    func testMissingOrientationAndAspectMismatchRejected() {
        var d = depth();d.sourceExifOrientation = nil
        XCTAssertThrowsError(try LiteRawDepthPacket.make(depth:d,jpeg:jpeg()))
        d = depth();d.rgbWidth = 48
        XCTAssertThrowsError(try LiteRawDepthPacket.make(depth:d,jpeg:jpeg()))
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
