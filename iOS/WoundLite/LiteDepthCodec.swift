import Foundation
import CryptoKit
import ImageIO

/// 本機 RGB-D 側檔 v2。保留採集 Float32 位元；不是新的雲端上傳契約。
/// SHA-256 用於錯配／損壞偵測，不能取代 LocalImageStore 的驗證加密。
enum LiteDepthCodec {
    enum Failure: Error { case invalidMetadata, invalidLength, invalidFormat, checksum, imageMismatch }
    private static let maxPixels = 1_048_576
    private static let maxFileBytes = 16 * 1024 * 1024

    private struct Envelope: Codable {
        var schemaVersion: Int
        var payload: Data
        var payloadSHA256: String
    }

    // Optional 新欄位只為讀取舊版；v2 路徑另外要求其存在且符合規格。
    private struct Payload: Codable {
        var w: Int; var h: Int
        var fx: Double; var fy: Double; var cx: Double; var cy: Double
        var refW: Double; var refH: Double
        var accuracy: String; var filtered: Bool
        var rgbW: Int; var rgbH: Int
        var mapB64: String
        var format: String?
        var unit: String?
        var rgbSHA256: String?
        var sourceExifOrientation: Int? = nil
    }

    static func encode(_ depth: DepthCapture, matchingJPEG jpeg: Data) throws -> Data {
        var p = Payload(w: depth.width, h: depth.height, fx: depth.fx, fy: depth.fy,
                        cx: depth.cx, cy: depth.cy, refW: depth.refWidth, refH: depth.refHeight,
                        accuracy: depth.accuracy, filtered: depth.filtered,
                        rgbW: depth.rgbWidth, rgbH: depth.rgbHeight, mapB64: "",
                        format: "float32_le", unit: "m", rgbSHA256: hash(jpeg))
        p.sourceExifOrientation = depth.sourceExifOrientation
        let count = try validate(p)
        guard depth.map.count == count else { throw Failure.invalidLength }
        try validateImage(jpeg, width: p.rgbW, height: p.rgbH)
        var bytes = Data(capacity: count * 4)
        for value in depth.map {
            var bits = value.bitPattern.littleEndian
            withUnsafeBytes(of: &bits) { bytes.append(contentsOf: $0) }
        }
        p.mapB64 = bytes.base64EncodedString()
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        let payload = try encoder.encode(p)
        return try encoder.encode(Envelope(schemaVersion: 2, payload: payload, payloadSHA256: hash(payload)))
    }

    static func decode(_ data: Data, matchingJPEG jpeg: Data) throws -> DepthCapture {
        guard data.count <= maxFileBytes,
              let object = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw Failure.invalidFormat
        }
        let p: Payload
        if object["schemaVersion"] != nil || object["payload"] != nil || object["payloadSHA256"] != nil {
            let envelope = try JSONDecoder().decode(Envelope.self, from: data)
            guard envelope.schemaVersion == 2 else { throw Failure.invalidFormat }
            guard hash(envelope.payload) == envelope.payloadSHA256 else { throw Failure.checksum }
            p = try JSONDecoder().decode(Payload.self, from: envelope.payload)
            guard p.format == "float32_le", p.unit == "m" else { throw Failure.invalidFormat }
            guard p.rgbSHA256 == hash(jpeg) else { throw Failure.imageMismatch }
        } else {
            // 舊檔由 iOS little-endian 環境產生；無照片雜湊，只能檢查尺寸。
            p = try JSONDecoder().decode(Payload.self, from: data)
            guard p.format == nil, p.unit == nil, p.rgbSHA256 == nil else { throw Failure.invalidFormat }
        }
        let count = try validate(p)
        try validateImage(jpeg, width: p.rgbW, height: p.rgbH)
        guard let raw = Data(base64Encoded: p.mapB64), raw.count == count * 4 else {
            throw Failure.invalidLength
        }
        // 不使用未對齊 load；每個樣本精確四個位元組，拒絕截斷與額外尾碼。
        let bytes = [UInt8](raw)
        let map = stride(from: 0, to: bytes.count, by: 4).map { i in
            Float(bitPattern: UInt32(bytes[i]) | UInt32(bytes[i+1]) << 8
                  | UInt32(bytes[i+2]) << 16 | UInt32(bytes[i+3]) << 24)
        }
        return DepthCapture(map: map, width: p.w, height: p.h, fx: p.fx, fy: p.fy,
                            cx: p.cx, cy: p.cy, refWidth: p.refW, refHeight: p.refH,
                            accuracy: p.accuracy, filtered: p.filtered,
                            rgbWidth: p.rgbW, rgbHeight: p.rgbH, sourceExifOrientation: p.sourceExifOrientation)
    }

    private static func validate(_ p: Payload) throws -> Int {
        guard p.w >= 8, p.h >= 8, p.w <= 1024, p.h <= 1024,
              p.w <= maxPixels / p.h,
              p.rgbW > 0, p.rgbH > 0, p.rgbW <= 16384, p.rgbH <= 16384,
              [p.fx, p.fy, p.cx, p.cy, p.refW, p.refH].allSatisfy(\.isFinite),
              p.fx > 0, p.fy > 0, p.refW > 0, p.refH > 0,
              p.refW <= 16384, p.refH <= 16384,
              ["absolute", "relative"].contains(p.accuracy) else { throw Failure.invalidMetadata }
        return p.w * p.h
    }

    private static func validateImage(_ jpeg: Data, width: Int, height: Int) throws {
        guard jpeg.count <= maxFileBytes,
              let source = CGImageSourceCreateWithData(jpeg as CFData, [kCGImageSourceShouldCache: false] as CFDictionary),
              CGImageSourceGetType(source) as String? == "public.jpeg",
              let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
              properties[kCGImagePropertyPixelWidth] as? Int == width,
              properties[kCGImagePropertyPixelHeight] as? Int == height,
              (properties[kCGImagePropertyOrientation] as? Int ?? 1) == 1 else {
            throw Failure.imageMismatch
        }
    }

    private static func hash(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }
}
