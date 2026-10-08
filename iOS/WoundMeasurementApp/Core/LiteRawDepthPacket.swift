import Foundation
import CryptoKit
import ImageIO

/// lite.rgbd/1 packet. Transmission requires separate consent and App Attest admission.
/// The same JPEG bytes must be sent alongside metadata and rawDepth.
struct LiteRawDepthPacket {
    let jpeg: Data
    let metadata: Data
    let rawDepth: Data

    enum Failure: Error { case invalidMetadata, invalidImage, invalidLength }

    static func make(depth d: DepthCapture, jpeg: Data) throws -> Self {
        guard (8...1024).contains(d.width), (8...1024).contains(d.height),
              (1...4096).contains(d.rgbWidth), (1...4096).contains(d.rgbHeight),
              d.map.count == d.width * d.height,
              [d.fx,d.fy,d.cx,d.cy,d.refWidth,d.refHeight].allSatisfy(\.isFinite),
              d.fx > 0, d.fy > 0, d.refWidth > 0, d.refHeight > 0,
              d.refWidth <= 16384, d.refHeight <= 16384,
              d.cx >= 0, d.cx < d.refWidth, d.cy >= 0, d.cy < d.refHeight,
              ["absolute", "relative"].contains(d.accuracy),
              let orientation = d.sourceExifOrientation, (1...8).contains(orientation)
        else { throw Failure.invalidMetadata }
        let aspect = Double(d.width) / Double(d.height)
        guard abs((Double(d.rgbWidth) / Double(d.rgbHeight)) / aspect - 1) <= 0.01,
              abs((d.refWidth / d.refHeight) / aspect - 1) <= 0.01
        else { throw Failure.invalidMetadata }
        guard !jpeg.isEmpty, jpeg.count <= 16 * 1024 * 1024,
              let source = CGImageSourceCreateWithData(jpeg as CFData, nil),
              CGImageSourceGetType(source) as String? == "public.jpeg",
              let props = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
              props[kCGImagePropertyPixelWidth] as? Int == d.rgbWidth,
              props[kCGImagePropertyPixelHeight] as? Int == d.rgbHeight,
              (props[kCGImagePropertyOrientation] as? Int ?? 1) == 1,
              CGImageSourceCreateImageAtIndex(source, 0, nil) != nil
        else { throw Failure.invalidImage }
        var raw = Data(capacity: d.map.count * 4)
        for value in d.map {
            var bits = value.bitPattern.littleEndian
            withUnsafeBytes(of: &bits) { raw.append(contentsOf: $0) }
        }
        let fields: [String: Any] = [
            "schema": "lite.rgbd/1", "format": "float32_le", "unit": "m",
            "width": d.width, "height": d.height,
            "rgb_width": d.rgbWidth, "rgb_height": d.rgbHeight,
            "rgb_sha256": hash(jpeg), "depth_sha256": hash(raw),
            "intrinsics": ["fx":d.fx, "fy":d.fy, "cx":d.cx, "cy":d.cy,
                           "reference_width":d.refWidth, "reference_height":d.refHeight],
            "source_exif_orientation": orientation, "accuracy": d.accuracy,
            "filtered": d.filtered, "confidence_kind": "validity_only",
            "pose": "unavailable", "capture_time": "unavailable", "registration": "not_verified"
        ]
        let metadata = try JSONSerialization.data(withJSONObject: fields, options: [.sortedKeys])
        return Self(jpeg: jpeg, metadata: metadata, rawDepth: raw)
    }

    /// Binary multipart only; does not send a request or assert consent.
    func multipart(boundary: String, fields: [String: String] = [:]) throws -> Data {
        guard (16...70).contains(boundary.utf8.count),
              boundary.utf8.allSatisfy({ (48...57).contains($0) || (65...90).contains($0) || (97...122).contains($0) || $0 == 45 })
        else { throw Failure.invalidMetadata }
        guard Set(fields.keys).isSubset(of: ["anon_id", "client", "research_consent", "consent_version"]),
              fields.values.allSatisfy({ $0.utf8.count <= 128 }) else { throw Failure.invalidMetadata }
        let delimiter = Data(("--" + boundary).utf8)
        guard ([jpeg, metadata, rawDepth] + fields.values.map { Data($0.utf8) }).allSatisfy({ $0.range(of: delimiter) == nil })
        else { throw Failure.invalidMetadata }
        var body = Data()
        func part(_ name: String, filename: String?, type: String, bytes: Data) {
            body.append(Data(("--" + boundary + "\r\n").utf8))
            var disposition = "Content-Disposition: form-data; name=\"" + name + "\""
            if let filename { disposition += "; filename=\"" + filename + "\"" }
            body.append(Data((disposition + "\r\nContent-Type: " + type + "\r\n\r\n").utf8))
            body.append(bytes); body.append(Data("\r\n".utf8))
        }
        for (name, value) in fields.sorted(by: { $0.key < $1.key }) {
            part(name, filename: nil, type: "text/plain; charset=utf-8", bytes: Data(value.utf8))
        }
        part("raw_depth_metadata", filename: nil, type: "application/json", bytes: metadata)
        part("image", filename: "capture.jpg", type: "image/jpeg", bytes: jpeg)
        part("raw_depth", filename: "depth.f32le", type: "application/octet-stream", bytes: rawDepth)
        body.append(Data(("--" + boundary + "--\r\n").utf8))
        return body
    }

    /// Verify exact persisted assets and scope. This does not authorize research use.
    func verifiesReceipt(_ data: Data, installationID: String, captureID: String) -> Bool {
        struct Receipt: Decodable {
            let schema: String; let installation_id: String; let capture_id: String
            let bundle_sha256: String; let bundle_bytes: Int; let metadata_sha256: String
            let rgb_sha256: String; let depth_sha256: String; let status: String
            let registration: String; let training_admission: String
        }
        guard data.count <= 16384, !installationID.isEmpty, !captureID.isEmpty,
              let r = try? JSONDecoder().decode(Receipt.self, from:data),
              r.schema == "lite.rgbd.receipt/1", r.status == "stored",
              r.installation_id == installationID, r.capture_id == captureID,
              r.metadata_sha256 == Self.hash(metadata), r.rgb_sha256 == Self.hash(jpeg),
              r.depth_sha256 == Self.hash(rawDepth),
              r.bundle_bytes == 16 + metadata.count + jpeg.count + rawDepth.count,
              r.registration == "not_verified", r.training_admission == "not_evaluated"
        else { return false }
        // Streaming hash avoids allocating a second full RGB-D bundle.
        var digest = SHA256()
        digest.update(data:Data("LRD1".utf8))
        for size in [metadata.count,jpeg.count,rawDepth.count] {
            var sizeBE = UInt32(size).bigEndian
            withUnsafeBytes(of:&sizeBE) { digest.update(data:Data($0)) }
        }
        for part in [metadata,jpeg,rawDepth] { digest.update(data:part) }
        let expected = digest.finalize().map { String(format:"%02x",$0) }.joined()
        return r.bundle_sha256 == expected
    }

    private static func hash(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }
}
