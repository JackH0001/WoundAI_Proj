import Foundation
import UIKit
import CoreGraphics
import SwiftUI
import CoreImage

// MARK: - 共享類型定義
// 此文件包含所有模組共享的結構體和枚舉類型，避免重複定義和編譯錯誤

// MARK: - OpenCV 相關類型
// OpenCV Swift Bridge 的結果類型，供所有模組使用

public struct OpenCVCircle {
    public let center: CGPoint
    public let radius: Double
    public let diameter: Double
    public let boundingBox: CGRect
    public let area: Double
    public let perimeter: Double
    public let circularity: Double
    public let confidence: Double
    
    public init(center: CGPoint, radius: Double, confidence: Double = 1.0) {
        self.center = center
        self.radius = radius
        self.diameter = radius * 2
        self.boundingBox = CGRect(x: center.x - radius, y: center.y - radius, 
                                  width: radius * 2, height: radius * 2)
        self.area = Double.pi * radius * radius
        self.perimeter = 2 * Double.pi * radius
        self.circularity = 1.0 // 假設完美圓形
        self.confidence = confidence
    }
}

public struct OpenCVSquare {
    public let center: CGPoint
    public let boundingBox: CGRect
    public let area: Double
    public let perimeter: Double
    public let aspectRatio: Double
    public let angleRotation: Double
    public let cornerPoints: [CGPoint]
    public let confidence: Double
}

public struct OpenCVColorPoint {
    public let center: CGPoint
    public let boundingBox: CGRect
    public let area: Double
    public let colorName: String
    public let rgbValues: [Double]
    public let hsvValues: [Double]
    public let confidence: Double
    
    public init(center: CGPoint, boundingBox: CGRect, area: Double, colorName: String, rgbValues: [Double], hsvValues: [Double], confidence: Double) {
        self.center = center
        self.boundingBox = boundingBox
        self.area = area
        self.colorName = colorName
        self.rgbValues = rgbValues
        self.hsvValues = hsvValues
        self.confidence = confidence
    }
}

public struct OpenCVContour {
    public let center: CGPoint
    public let boundingBox: CGRect
    public let area: Double
    public let perimeter: Double
    public let contourPoints: [CGPoint]
    public let confidence: Double
}

// MARK: - OpenCV 分析結果類型

public struct WoundAnalysisResult {
    let colorAnalysis: ColorAnalysisResult
    let textureAnalysis: TextureAnalysisResult
    let morphologyAnalysis: MorphologyAnalysisResult
    
    struct ColorAnalysisResult {
        let meanHue: Double
        let meanSaturation: Double
        let meanValue: Double
        let hueVariance: Double
        let saturationVariance: Double
        let valueVariance: Double
    }
    
    struct TextureAnalysisResult {
        let textureVariance: Double
        let edgeRoughness: Double
    }
    
    struct MorphologyAnalysisResult {
        let area: Double
        let aspectRatio: Double
        let edgePixels: Int
    }
    
    init(from dict: [String: Any]) {
        let colorDict = dict["colorAnalysis"] as? [String: Any] ?? [:]
        self.colorAnalysis = ColorAnalysisResult(
            meanHue: (colorDict["meanHue"] as? NSNumber)?.doubleValue ?? 0,
            meanSaturation: (colorDict["meanSaturation"] as? NSNumber)?.doubleValue ?? 0,
            meanValue: (colorDict["meanValue"] as? NSNumber)?.doubleValue ?? 0,
            hueVariance: (colorDict["hueVariance"] as? NSNumber)?.doubleValue ?? 0,
            saturationVariance: (colorDict["saturationVariance"] as? NSNumber)?.doubleValue ?? 0,
            valueVariance: (colorDict["valueVariance"] as? NSNumber)?.doubleValue ?? 0
        )
        
        let textureDict = dict["textureAnalysis"] as? [String: Any] ?? [:]
        self.textureAnalysis = TextureAnalysisResult(
            textureVariance: (textureDict["textureVariance"] as? NSNumber)?.doubleValue ?? 0,
            edgeRoughness: (textureDict["edgeRoughness"] as? NSNumber)?.doubleValue ?? 0
        )
        
        let morphDict = dict["morphologyAnalysis"] as? [String: Any] ?? [:]
        self.morphologyAnalysis = MorphologyAnalysisResult(
            area: (morphDict["area"] as? NSNumber)?.doubleValue ?? 0,
            aspectRatio: (morphDict["aspectRatio"] as? NSNumber)?.doubleValue ?? 0,
            edgePixels: (morphDict["edgePixels"] as? NSNumber)?.intValue ?? 0
        )
    }
}

public struct ImageQualityResult {
    let sharpness: Double
    let brightness: Double
    let contrast: Double
    let noiseLevel: Double
    let resolution: (width: Int, height: Int)
    
    var qualityScore: Double {
        let sharpnessScore = min(sharpness / 1000, 1.0)
        let brightnessScore = 1.0 - abs(brightness - 127) / 127
        let contrastScore = min(contrast / 50, 1.0)
        let noiseScore = max(0, 1.0 - noiseLevel / 100)
        
        return (sharpnessScore + brightnessScore + contrastScore + noiseScore) / 4.0
    }
    
    var qualityLevel: String {
        switch qualityScore {
        case 0.8...:
            return "優秀"
        case 0.6..<0.8:
            return "良好"
        case 0.4..<0.6:
            return "一般"
        case 0.2..<0.4:
            return "較差"
        default:
            return "很差"
        }
    }
    
    init(from dict: [String: Any]) {
        self.sharpness = (dict["sharpness"] as? NSNumber)?.doubleValue ?? 0
        self.brightness = (dict["brightness"] as? NSNumber)?.doubleValue ?? 0
        self.contrast = (dict["contrast"] as? NSNumber)?.doubleValue ?? 0
        self.noiseLevel = (dict["noiseLevel"] as? NSNumber)?.doubleValue ?? 0
        
        let resolutionDict = dict["resolution"] as? [String: Any] ?? [:]
        let width = (resolutionDict["width"] as? NSNumber)?.intValue ?? 0
        let height = (resolutionDict["height"] as? NSNumber)?.intValue ?? 0
        self.resolution = (width, height)
    }
}

struct TextureAnalysisResult {
    let lbpMean: Double
    let lbpStd: Double
    let textureHomogeneity: Double
    
    var textureComplexity: String {
        switch lbpStd {
        case 0..<30:
            return "平滑"
        case 30..<60:
            return "中等紋理"
        case 60..<100:
            return "複雜紋理"
        default:
            return "極複雜紋理"
        }
    }
    
    init(from dict: [String: Any]) {
        self.lbpMean = (dict["lbpMean"] as? NSNumber)?.doubleValue ?? 0
        self.lbpStd = (dict["lbpStd"] as? NSNumber)?.doubleValue ?? 0
        self.textureHomogeneity = (dict["textureHomogeneity"] as? NSNumber)?.doubleValue ?? 0
    }
}

// MARK: - 校正貼紙結果類型
// 統一改由各模組內定義：
// - OpenCV 流程：使用 `OpenCVStickerCalibrationResult`（定義於 OpenCV/OpenCVSwiftBridge.swift）
// - 圓/方混合流程：使用 `StickerCalibrationResult`（定義於 Modules/CalibrationStickerModule.swift）

// MARK: - 原有校正相關類型
// 保持向後兼容的類型定義

struct LegacyStickerCalibrationResult {
    // 使用 WoundTypes.swift 定義的 DetectedCircle，避免重複
    let circle: DetectedCircle
    let pixelsPerMM: Double
    let internalStructure: StickerInternalStructure
    let colorCalibration: ColorCalibrationData
    let confidence: Double
    let detectionTime: Date
    
    var description: String {
        return """
        校正貼紙檢測結果:
        - 中心位置: (\(String(format: "%.1f", circle.center.x)), \(String(format: "%.1f", circle.center.y)))
        - 半徑: \(String(format: "%.1f", circle.radius)) pixels
        - 像素比例: \(String(format: "%.3f", pixelsPerMM)) pixels/mm
        - 檢測置信度: \(String(format: "%.1f", confidence * 100))%
        - 3D點數量: \(internalStructure.dots3D.count)
        """
    }
}

// DetectedCircle 已在 WoundTypes.swift 中定義

// 下列型別改由 `Modules/CalibrationStickerModule.swift` 提供，避免重複定義：
// StickerInternalStructure / ColorAnalysis / ColorCalibrationData / WhiteBalanceGains / ColorCorrectionMatrix

// MARK: - 方形貼紙相關類型
// SquareCalibrationResult 已在 SquareCalibrationModule.swift 中定義

// MARK: - 測量相關類型
// 統一使用 Models/WoundTypes.swift 中的 `WoundMeasurementResult` 定義，避免型別衝突

struct ImageMetadata {
    let imageSize: CGSize
    let appVersion: String
    let deviceModel: String
    let calibrationSource: String // "sticker", "square", "lidar", "estimated"
    let cameraSettings: CameraSettings?
    
    struct CameraSettings {
        let iso: Double?
        let shutterSpeed: Double?
        let aperture: Double?
        let focalLength: Double?
        let whiteBalance: String?
    }
}

// MARK: - 圖像分析相關類型
struct ImageAnalysisResult {
    let qualityScore: Double
    let sharpness: Double
    let exposure: Double
    let colorBalance: Double
    let noiseLevel: Double
    let recommendations: [String]
    
    var isAcceptable: Bool {
        return qualityScore > 0.7
    }
}

// 使用 WoundTypes.swift 的 QualityMetrics 定義，避免重複
/* struct QualityMetrics {
    let snr: Double
    let blurVariance: Double
    let contrastRatio: Double
    let colorBalance: Double
    let overallQuality: Double
    let isAcceptable: Bool
    let blurLevel: Double
    let depthCoverage: Double
} */

/* struct ProcessedImage {
    let image: UIImage
    let depthData: Data
    let qualityMetrics: QualityMetrics
    let roi: CGRect
    let woundFeatures: WoundFeatures?
    let multiScaleImages: [CIImage]
    let roiConfidence: Double
} */

/* struct WoundFeatures {
    let area: Double
    let perimeter: Double
    let centroid: CGPoint
    let boundingBox: CGRect
    let colorProfile: ColorProfile
    let textureMetrics: TextureMetrics
    let shapeMetrics: ShapeMetrics
    
    struct ColorProfile {
        let dominantColors: [UIColor]
        let averageColor: UIColor
        let rednessFactor: Double
        let saturation: Double
        let brightness: Double
    }
    
    struct TextureMetrics {
        let roughness: Double
        let uniformity: Double
        let entropy: Double
        let contrast: Double
    }
    
    struct ShapeMetrics {
        let circularity: Double
        let convexity: Double
        let solidity: Double
        let aspectRatio: Double
    }
} */

/* struct WoundContour {
    let points: [CGPoint]
    let boundingRect: CGRect
    let area: Double
    let perimeter: Double
    let confidence: Double
} */

struct SegmentationResult {
    let mask: UIImage
    let confidence: Double
    let boundingRect: CGRect
    let contour: [CGPoint]
    let processingTime: TimeInterval
    
    var area: Double {
        return contour.count > 3 ? calculatePolygonArea(points: contour) : 0
    }
    
    private func calculatePolygonArea(points: [CGPoint]) -> Double {
        guard points.count >= 3 else { return 0 }
        
        var area: Double = 0
        let n = points.count
        
        for i in 0..<n {
            let j = (i + 1) % n
            area += points[i].x * points[j].y
            area -= points[j].x * points[i].y
        }
        
        return abs(area) / 2.0
    }
}

// MARK: - 錯誤處理相關類型
struct MemoryError: Error, LocalizedError {
    let message: String
    
    init(message: String = "記憶體不足") {
        self.message = message
    }
    
    var errorDescription: String? {
        return message
    }
}

// InitializationError和QualityError已移至ContentView.swift

// WoundMeasurementError已移至ContentView.swift以解決編譯問題

// PreProcessingError 已在 WoundTypes.swift 中定義

// CalibrationError 僅供本檔案內部使用者原先引用，現暫不重複定義，避免衝突

// MARK: - 主要錯誤類型定義

public enum WoundMeasurementError: Error, LocalizedError {
    case calibrationRequired
    case imageProcessingFailed  
    case segmentationFailed
    case insufficientQuality
    case noStickerDetected
    case invalidImageFormat
    case cameraNotAvailable
    case permissionDenied
    case networkError(String)
    case unknown(String)
    case lidarNotAvailable
    case measurementFailed
    case classificationFailed
    case dataSaveFailed
    
    public var errorDescription: String? {
        switch self {
        case .calibrationRequired: return "需要先完成校正"
        case .imageProcessingFailed: return "圖像處理失敗"
        case .segmentationFailed: return "傷口分割失敗"
        case .insufficientQuality: return "圖像品質不足"
        case .noStickerDetected: return "未檢測到校正貼紙"
        case .invalidImageFormat: return "無效的圖像格式"
        case .cameraNotAvailable: return "相機不可用"
        case .permissionDenied: return "權限被拒絕"
        case .networkError(let message): return "網路錯誤: \(message)"
        case .unknown(let message): return "未知錯誤: \(message)"
        case .lidarNotAvailable: return "LiDAR 感測器不可用"
        case .measurementFailed: return "測量失敗"
        case .classificationFailed: return "分類失敗"
        case .dataSaveFailed: return "資料保存失敗"
        }
    }
}

// SegmentationError和TimeoutError已移至ContentView.swift

// MARK: - 擴展功能（移至 Modules/ErrorHandlingModule.swift，避免重複定義）

// MARK: - 輔助功能
extension CGPoint {
    func distance(to other: CGPoint) -> Double {
        let dx = self.x - other.x
        let dy = self.y - other.y
        return sqrt(dx * dx + dy * dy)
    }
}

extension CGRect {
    var center: CGPoint {
        return CGPoint(x: midX, y: midY)
    }
}