import AVFoundation
import ImageIO
import UIKit

/// Apply the same EXIF transform that UIKit applies when drawing the RGB upright.
/// AVFoundation transforms both the depth map and its calibration, not just pixels.
extension DepthCapture {
    static func fromPhoto(_ data: AVDepthData, imageOrientation: UIImage.Orientation) -> DepthCapture? {
        guard let exif = exifOrientation(imageOrientation) else { return nil }
        var result = from(data.applyingExifOrientation(exif))
        result?.sourceExifOrientation = Int(exif.rawValue)
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
}
