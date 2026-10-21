import AVFoundation
import ImageIO
import UIKit

/// Apply the same EXIF transform that UIKit applies when drawing the RGB upright.
/// AVFoundation transforms both the depth map and its calibration, not just pixels.
extension DepthCapture {
    static func fromPhoto(_ data: AVDepthData, imageOrientation: UIImage.Orientation,
                          captureProvenance: CaptureProvenance? = nil) -> DepthCapture? {
        guard let exif = exifOrientation(imageOrientation) else { return nil }
        var result = from(data.applyingExifOrientation(exif))
        result?.sourceExifOrientation = Int(exif.rawValue)
        result?.captureProvenance = captureProvenance
        return result
    }

    static func exifOrientation(_ orientation: UIImage.Orientation) -> CGImagePropertyOrientation? {
        // UIImage and EXIF numeric enum values are different; never cast rawValue.
        switch orientation {
        case .up: return .up
        case .upMirrored: return .upMirrored
        case .down: return .down
        case .downMirrored: return .downMirrored
        case .left: return .left
        case .leftMirrored: return .leftMirrored
        case .right: return .right
        case .rightMirrored: return .rightMirrored
        @unknown default: return nil
        }
    }

    /// Necessary condition, not proof of registration. A ratio check cannot detect
    /// a 180° error, mirroring, cropping, lens distortion or a mismatched exposure.
    func matchesImageAspect(width imageW: Int, height imageH: Int) -> Bool {
        guard width > 0, height > 0, imageW > 0, imageH > 0 else { return false }
        let ratio = (Double(width) / Double(height)) / (Double(imageW) / Double(imageH))
        return abs(ratio - 1) <= 0.01
    }

    /// Necessary metadata checks only: same aspect does not prove optical registration.
    /// Legacy records remain readable, but Lite requires recorded orientation before
    /// making a new metric estimate. No rotation or calibration is guessed here.
    func geometryIssue(imageW: Int, imageH: Int, requireOrientation: Bool = false) -> String? {
        guard (8...4096).contains(width), (8...4096).contains(height),
              map.count == width * height, (1...16384).contains(imageW),
              (1...16384).contains(imageH) else { return "invalid_dimensions" }
        guard [fx, fy, cx, cy, refWidth, refHeight].allSatisfy(\.isFinite),
              fx > 0, fy > 0, refWidth > 0, refHeight > 0,
              refWidth <= 16384, refHeight <= 16384,
              cx >= 0, cx < refWidth, cy >= 0, cy < refHeight else {
            return "invalid_intrinsics"
        }
        guard matchesImageAspect(width: imageW, height: imageH),
              abs((refWidth / refHeight) / (Double(width) / Double(height)) - 1) <= 0.01 else {
            return "aspect_mismatch"
        }
        // Zero/zero denotes an older caller without a bound work image.
        guard (rgbWidth == 0 && rgbHeight == 0)
                || (rgbWidth == imageW && rgbHeight == imageH) else { return "rgb_binding_mismatch" }
        if let exif = sourceExifOrientation {
            guard (1...8).contains(exif) else { return "invalid_orientation" }
        } else if requireOrientation {
            return "orientation_unknown"
        }
        guard accuracy == "absolute" else { return "relative_depth" }
        return nil
    }

    static func polygonsFit(_ polygons: [[[Int]]], width: Int, height: Int) -> Bool {
        guard width > 0, height > 0, polygons.count <= 1024 else { return false }
        return polygons.allSatisfy { polygon in
            polygon.count >= 3 && polygon.count <= 100_000 && polygon.allSatisfy {
                $0.count == 2 && (0...width).contains($0[0]) && (0...height).contains($0[1])
            }
        }
    }
}
