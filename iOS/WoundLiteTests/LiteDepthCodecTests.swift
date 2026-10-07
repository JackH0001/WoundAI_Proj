import XCTest
import UIKit
import CryptoKit
@testable import WoundLite

final class LiteDepthCodecTests: XCTestCase {
    private func jpeg(_ color: UIColor = .red, width: Int = 64, height: Int = 48) -> Data {
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        return UIGraphicsImageRenderer(size: CGSize(width: width, height: height), format: format).image { ctx in
            color.setFill()
            ctx.fill(CGRect(x: 0, y: 0, width: width, height: height))
        }.jpegData(compressionQuality: 0.85)!
    }

    private func depth() -> DepthCapture {
        DepthCapture(map: Array(repeating: 1, count: 64), width: 8, height: 8,
                     fx: 50, fy: 51, cx: 32, cy: 24, refWidth: 64, refHeight: 48,
                     accuracy: "absolute", filtered: true, rgbWidth: 64, rgbHeight: 48)
    }

    private func object(_ data: Data) throws -> [String: Any] {
        try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
    }
    private func json(_ object: [String: Any]) throws -> Data {
        try JSONSerialization.data(withJSONObject: object, options: .sortedKeys)
    }
    private func payload(_ data: Data) throws -> [String: Any] {
        let envelope = try object(data)
        return try object(XCTUnwrap(Data(base64Encoded: XCTUnwrap(envelope["payload"] as? String))))
    }
    private func envelope(_ payload: [String: Any]) throws -> Data {
        let data = try json(payload)
        let digest = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
        return try json(["schemaVersion": 2, "payload": data.base64EncodedString(), "payloadSHA256": digest])
    }
    private func legacy() -> [String: Any] {
        // Independent fixture: 1.0 Float32 is 00 00 80 3f in little endian.
        let bytes = Data((0..<64).flatMap { _ in [UInt8(0), 0, 0x80, 0x3f] })
        return ["w": 8, "h": 8, "fx": 50, "fy": 51, "cx": 32, "cy": 24,
                "refW": 64, "refH": 48, "accuracy": "absolute", "filtered": true,
                "rgbW": 64, "rgbH": 48, "mapB64": bytes.base64EncodedString()]
    }

    func testFloatBitsAndAllCalibrationMetadataSurviveRoundTrip() throws {
        var input = depth()
        input.sourceExifOrientation = 6
        let bits: [UInt32] = [0, 0x80000000, 0x7fc01234, 0x7f800000, 0x3e99aabb]
        for (i, value) in bits.enumerated() { input.map[i] = Float(bitPattern: value) }
        let image = jpeg()
        let output = try LiteDepthCodec.decode(LiteDepthCodec.encode(input, matchingJPEG: image), matchingJPEG: image)
        XCTAssertEqual(output.map.map(\.bitPattern), input.map.map(\.bitPattern))
        XCTAssertEqual([output.fx, output.fy, output.cx, output.cy, output.refWidth, output.refHeight], [50, 51, 32, 24, 64, 48])
        XCTAssertEqual([output.width, output.height, output.rgbWidth, output.rgbHeight], [8, 8, 64, 48])
        XCTAssertEqual(output.accuracy, "absolute")
        XCTAssertTrue(output.filtered)
        XCTAssertEqual(output.sourceExifOrientation, 6)
    }

    func testWireByteOrderAndLegacyCompatibilityUseIndependentFixture() throws {
        let image = jpeg()
        let p = try payload(LiteDepthCodec.encode(depth(), matchingJPEG: image))
        XCTAssertEqual(p["mapB64"] as? String, legacy()["mapB64"] as? String)
        XCTAssertEqual(p["format"] as? String, "float32_le")
        XCTAssertEqual(p["unit"] as? String, "m")
        let decoded = try LiteDepthCodec.decode(json(legacy()), matchingJPEG: image)
        XCTAssertEqual(decoded.map, Array(repeating: 1, count: 64))
    }

    func testTruncationAndOneToFourTrailingBytesAreRejected() throws {
        let image = jpeg()
        for length in [255, 257, 258, 259, 260] {
            var p = legacy()
            p["mapB64"] = Data(repeating: 0, count: length).base64EncodedString()
            XCTAssertThrowsError(try LiteDepthCodec.decode(json(p), matchingJPEG: image), "length \(length)")
        }
    }

    func testUnknownSchemaFormatAndUnitAreRejectedEvenWithValidChecksum() throws {
        let image = jpeg()
        let encoded = try LiteDepthCodec.encode(depth(), matchingJPEG: image)
        var outer = try object(encoded)
        outer["schemaVersion"] = 3
        XCTAssertThrowsError(try LiteDepthCodec.decode(json(outer), matchingJPEG: image))
        for (key, value) in [("format", "float32_be"), ("unit", "mm")] {
            var p = try payload(encoded)
            p[key] = value
            XCTAssertThrowsError(try LiteDepthCodec.decode(envelope(p), matchingJPEG: image))
        }
    }

    func testChangedMetadataFailsPayloadChecksum() throws {
        let image = jpeg()
        let encoded = try LiteDepthCodec.encode(depth(), matchingJPEG: image)
        var p = try payload(encoded)
        p["fx"] = 500
        var outer = try object(encoded)
        outer["payload"] = try json(p).base64EncodedString()
        XCTAssertThrowsError(try LiteDepthCodec.decode(json(outer), matchingJPEG: image))
    }

    func testWrongPhotoOfSameSizeAndInvalidImageAreRejected() throws {
        let image = jpeg()
        let encoded = try LiteDepthCodec.encode(depth(), matchingJPEG: image)
        XCTAssertThrowsError(try LiteDepthCodec.decode(encoded, matchingJPEG: jpeg(.blue)))
        XCTAssertThrowsError(try LiteDepthCodec.encode(depth(), matchingJPEG: jpeg(width: 65)))
        XCTAssertThrowsError(try LiteDepthCodec.decode(json(legacy()), matchingJPEG: Data([1, 2, 3])))
    }

    func testInvalidDimensionsIntrinsicsAndSampleCountAreRejected() throws {
        let image = jpeg()
        for (key, value) in [("w", -1), ("h", 0), ("w", Int.max), ("rgbW", 0), ("fx", 0), ("refH", -1)] {
            var p = legacy()
            p[key] = value
            XCTAssertThrowsError(try LiteDepthCodec.decode(json(p), matchingJPEG: image), key)
        }
        var p = legacy()
        p["accuracy"] = "unknown"
        XCTAssertThrowsError(try LiteDepthCodec.decode(json(p), matchingJPEG: image))
        var d = depth()
        d.fx = .nan
        XCTAssertThrowsError(try LiteDepthCodec.encode(d, matchingJPEG: image))
        d = depth()
        d.map.removeLast()
        XCTAssertThrowsError(try LiteDepthCodec.encode(d, matchingJPEG: image))
    }

    @MainActor
    func testEncryptedStoreRoundTripAndPhotoMismatch() throws {
        let store = LiteStore(fileURL: FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString))
        let image = jpeg()
        let imageName = try XCTUnwrap(store.images.save(jpeg: image))
        defer { store.images.delete(imageName) }
        let other = try XCTUnwrap(store.images.save(jpeg: jpeg(.blue)))
        defer { store.images.delete(other) }
        let depthName = try XCTUnwrap(store.saveDepth(depth(), matchingJPEG: image))
        defer { store.images.delete(depthName) }
        XCTAssertEqual(store.loadDepth(depthName, matchingImage: imageName)?.map, depth().map)
        XCTAssertNil(store.loadDepth(depthName, matchingImage: other))
        XCTAssertNil(store.saveDepth(depth(), matchingJPEG: Data()))
        XCTAssertNotNil(store.storageError)
        XCTAssertTrue(store.records.isEmpty)
    }

    func testSavedDepthReproducesSurfaceVolumeAndDepthEstimates() throws {
        var map = [Float](repeating: 0.3, count: 128 * 128)
        for y in 0..<128 {
            for x in 0..<128 {
                if abs(x - 64) < 20 && abs(y - 64) < 20 { map[y * 128 + x] = 0.305 }
            }
        }
        let input = DepthCapture(map: map, width: 128, height: 128, fx: 100, fy: 100,
                                 cx: 64, cy: 64, refWidth: 128, refHeight: 128,
                                 accuracy: "absolute", filtered: false, rgbWidth: 1024, rgbHeight: 1024)
        let image = jpeg(width: 1024, height: 1024)
        let loaded = try LiteDepthCodec.decode(LiteDepthCodec.encode(input, matchingJPEG: image), matchingJPEG: image)
        let polygons = [[[352, 352], [672, 352], [672, 672], [352, 672]]]
        // Exercise the same default smoothing used by Lite, not a special unsmoothed path.
        let before = try XCTUnwrap(DepthAreaEstimator.estimate(polygons: polygons, depth: input, imageW: 1024, imageH: 1024))
        let after = try XCTUnwrap(DepthAreaEstimator.estimate(polygons: polygons, depth: loaded, imageW: 1024, imageH: 1024))
        XCTAssertGreaterThan(before.surfaceAreaCm2, 0)
        XCTAssertGreaterThan(try XCTUnwrap(before.volumeMl), 0)
        XCTAssertGreaterThan(try XCTUnwrap(before.maxDepthMm), 0)
        XCTAssertEqual(after.surfaceAreaCm2, before.surfaceAreaCm2)
        XCTAssertEqual(after.projectedAreaCm2, before.projectedAreaCm2)
        XCTAssertEqual(after.volumeMl, before.volumeMl)
        XCTAssertEqual(after.maxDepthMm, before.maxDepthMm)
        XCTAssertEqual(after.coverage, before.coverage)
    }
}
