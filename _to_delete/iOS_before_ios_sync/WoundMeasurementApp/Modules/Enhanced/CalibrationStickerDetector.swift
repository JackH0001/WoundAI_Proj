import Foundation
import UIKit
import CoreImage
import Vision
import ARKit
import os.log

/// 校正貼紙檢測器 - 整合ArUco方形和圓形網格校正貼紙檢測
@MainActor
class CalibrationStickerDetector: ObservableObject {
    
    // MARK: - Properties
    
    @Published var detectionProgress: Double = 0.0
    @Published var detectionState: DetectionState = .idle
    @Published var arUcoDetectionResult: ArUcoDetectionResult?
    @Published var circleGridDetectionResult: CircleGridDetectionResult?
    @Published var combinedCalibrationResult: CombinedCalibrationResult?
    
    private let logger = os.Logger(subsystem: "WoundMeasurementApp", category: "CalibrationDetector")
    private let ciContext = CIContext(options: [.useSoftwareRenderer: false])
    
    // ArUco檢測器設定
    private lazy var arUcoRequest: VNDetectBarcodesRequest = {
        let request = VNDetectBarcodesRequest { [weak self] request, error in
            Task { @MainActor in
                await self?.handleArUcoDetection(request: request, error: error)
            }
        }
        request.symbologies = [.qr] // 我們會用自訂檢測
        return request
    }()
    
    // MARK: - 檢測狀態枚舉
    
    enum DetectionState {
        case idle
        case detectingArUco
        case detectingCircleGrid
        case calibratingColors
        case calibratingDepth
        case validatingCalibration
        case completed
        case failed(Error)
    }
    
    // MARK: - 主要檢測方法
    
    /// 執行校正貼紙檢測和校準
    func detectAndCalibrateStickers(
        _ image: UIImage,
        depthData: ARDepthData? = nil
    ) async throws -> CombinedCalibrationResult {
        
        logger.info("開始檢測校正貼紙")
        detectionProgress = 0.0
        detectionState = .detectingArUco
        
        do {
            // 階段1: ArUco方形校正貼紙檢測 (30%)
            let arUcoResult = try await detectArUcoSquareSticker(image)
            arUcoDetectionResult = arUcoResult
            detectionProgress = 0.3
            
            // 階段2: 圓形網格校正貼紙檢測 (30%)
            detectionState = .detectingCircleGrid
            let circleGridResult = try await detectCircleGridSticker(image)
            circleGridDetectionResult = circleGridResult
            detectionProgress = 0.6
            
            // 階段3: 色彩校準 (15%)
            detectionState = .calibratingColors
            let colorCalibration = try await performColorCalibration(image, arUcoResult, circleGridResult)
            detectionProgress = 0.75
            
            // 階段4: 深度校準 (15%)
            detectionState = .calibratingDepth
            let depthCalibration = try await performDepthCalibration(
                image: image,
                depthData: depthData,
                arUcoResult: arUcoResult,
                circleGridResult: circleGridResult
            )
            detectionProgress = 0.9
            
            // 階段5: 校準驗證 (10%)
            detectionState = .validatingCalibration
            let combinedResult = try await combineCalibrationsAndValidate(
                image: image,
                arUcoResult: arUcoResult,
                circleGridResult: circleGridResult,
                colorCalibration: colorCalibration,
                depthCalibration: depthCalibration
            )
            
            combinedCalibrationResult = combinedResult
            detectionProgress = 1.0
            detectionState = .completed
            
            logger.info("校正貼紙檢測和校準完成")
            return combinedResult
            
        } catch {
            detectionState = .failed(error)
            logger.error("校正貼紙檢測失敗: \(error.localizedDescription)")
            throw error
        }
    }
    
    // MARK: - ArUco方形貼紙檢測
    
    /// 檢測ArUco方形校正貼紙 (20mm x 20mm)
    private func detectArUcoSquareSticker(_ image: UIImage) async throws -> ArUcoDetectionResult {
        logger.info("檢測ArUco方形校正貼紙")
        
        guard let ciImage = CIImage(image: image) else {
            throw CalibrationError.imageProcessingFailed
        }
        
        // 預處理：增強對比度以提高ArUco檢測精度
        let enhancedImage = ciImage.applyingFilter("CIColorControls", parameters: [
            "inputContrast": 1.3,
            "inputBrightness": 0.1,
            "inputSaturation": 0.8
        ])
        
        // 自訂ArUco檢測 (使用OpenCV風格的模式匹配)
        let arUcoCorners = try await detectArUcoPattern(enhancedImage)
        
        guard let corners = arUcoCorners, corners.count == 4 else {
            throw CalibrationError.arUcoNotFound
        }
        
        // 檢測色彩校正點 (紅、綠、藍、黃)
        let colorPoints = try await detectColorCalibrationPoints(enhancedImage, corners: corners)
        
        // 檢測深度校正點 (四角黑點)
        let depthPoints = try await detectDepthCorrectionPoints(enhancedImage, corners: corners)
        
        // 檢測十字座標線
        let crosshairLines = try await detectCrosshairLines(enhancedImage, corners: corners)
        
        // 計算像素密度 (已知20mm x 20mm)
        let pixelDensity = calculatePixelDensity(corners: corners, physicalSize: 20.0)
        
        // 計算變換矩陣
        let transformMatrix = calculatePerspectiveTransform(corners: corners)
        
        return ArUcoDetectionResult(
            corners: corners,
            colorCalibrationPoints: colorPoints,
            depthCorrectionPoints: depthPoints,
            crosshairLines: crosshairLines,
            pixelDensityMmPerPixel: pixelDensity,
            perspectiveTransform: transformMatrix,
            confidence: calculateArUcoConfidence(corners, colorPoints, depthPoints),
            detectedFeatures: ArUcoFeatures(
                hasValidPattern: true,
                hasColorPoints: colorPoints.count == 4,
                hasDepthPoints: depthPoints.count == 4,
                hasCrosshair: crosshairLines.count == 2
            )
        )
    }
    
    /// 檢測ArUco 5x5模式
    private func detectArUcoPattern(_ image: CIImage) async throws -> [CGPoint]? {
        // 轉換為灰階
        let grayscale = image.applyingFilter("CIPhotoEffectNoir")
        
        // 二值化
        let threshold = grayscale.applyingFilter("CIColorMonochrome", parameters: [
            "inputColor": CIColor.white,
            "inputIntensity": 1.0
        ])
        
        // 尋找矩形輪廓
        let rectangles = try await findRectangularContours(threshold)
        
        // 驗證ArUco模式
        for rect in rectangles {
            if let corners = try await validateArUcoPattern(threshold, rectangle: rect) {
                return corners
            }
        }
        
        return nil
    }
    
    /// 檢測色彩校正點 (紅、綠、藍、黃圓點)
    private func detectColorCalibrationPoints(
        _ image: CIImage, 
        corners: [CGPoint]
    ) async throws -> [ColorCalibrationPoint] {
        
        var colorPoints: [ColorCalibrationPoint] = []
        
        // 根據ArUco貼紙設計，色彩點在特定位置
        let expectedPositions = [
            CGPoint(x: 0.5, y: 0.15),  // 紅色 (上)
            CGPoint(x: 0.5, y: 0.85),  // 綠色 (下)
            CGPoint(x: 0.15, y: 0.5),  // 藍色 (左)
            CGPoint(x: 0.85, y: 0.5)   // 黃色 (右)
        ]
        
        let expectedColors = [
            UIColor.red, UIColor.green, UIColor.blue, UIColor.yellow
        ]
        
        for (index, relativePos) in expectedPositions.enumerated() {
            let worldPos = transformRelativeToWorld(relativePos, corners: corners)
            
            if let colorValue = try await sampleColorAtPoint(image, point: worldPos),
               let detectedColor = try await validateColorMatch(colorValue, expected: expectedColors[index]) {
                
                colorPoints.append(ColorCalibrationPoint(
                    position: worldPos,
                    expectedColor: expectedColors[index],
                    detectedColor: detectedColor,
                    confidence: calculateColorConfidence(detectedColor, expectedColors[index])
                ))
            }
        }
        
        return colorPoints
    }
    
    // MARK: - 圓形網格貼紙檢測
    
    /// 檢測圓形網格校正貼紙 (20mm直徑)
    private func detectCircleGridSticker(_ image: UIImage) async throws -> CircleGridDetectionResult {
        logger.info("檢測圓形網格校正貼紙")
        
        guard let ciImage = CIImage(image: image) else {
            throw CalibrationError.imageProcessingFailed
        }
        
        // 檢測外圓邊界
        let outerCircle = try await detectOuterCircle(ciImage)
        
        guard let circle = outerCircle else {
            throw CalibrationError.circleGridNotFound
        }
        
        // 檢測3x3圓形網格模式
        let gridPattern = try await detectCircleGridPattern(ciImage, outerCircle: circle)
        
        // 檢測色彩校正點
        let colorPoints = try await detectCircleGridColorPoints(ciImage, outerCircle: circle)
        
        // 檢測深度校正點
        let depthPoints = try await detectCircleGridDepthPoints(ciImage, outerCircle: circle)
        
        // 檢測十字座標線
        let crosshairLines = try await detectCircleGridCrosshair(ciImage, outerCircle: circle)
        
        // 計算像素密度 (已知20mm直徑)
        let pixelDensity = circle.radius * 2.0 / 20.0 // pixels per mm
        
        // 驗證已知面積 (3.14 cm²)
        let calculatedArea = Double.pi * pow(circle.radius / pixelDensity / 10.0, 2) // cm²
        let areaAccuracy = 1.0 - abs(calculatedArea - 3.14) / 3.14
        
        return CircleGridDetectionResult(
            outerCircle: circle,
            gridPattern: gridPattern,
            colorCalibrationPoints: colorPoints,
            depthCorrectionPoints: depthPoints,
            crosshairLines: crosshairLines,
            pixelDensityMmPerPixel: pixelDensity,
            knownAreaCm2: 3.14,
            calculatedAreaCm2: calculatedArea,
            areaCalibrationAccuracy: areaAccuracy,
            confidence: calculateCircleGridConfidence(circle, gridPattern, colorPoints),
            detectedFeatures: CircleGridFeatures(
                hasValidOuterCircle: true,
                hasCompleteGrid: gridPattern.detectedCircles.count >= 8, // 9個圓，中心是灰色
                hasColorPoints: colorPoints.count == 4,
                hasDepthPoints: depthPoints.count == 4
            )
        )
    }
    
    /// 檢測外圓邊界
    private func detectOuterCircle(_ image: CIImage) async throws -> DetectedCircle? {
        // 邊緣檢測
        let edges = image.applyingFilter("CIEdges", parameters: [
            "inputIntensity": 2.0
        ])
        
        // Hough圓變換檢測
        let circles = try await performHoughCircleTransform(edges)
        
        // 尋找最符合預期大小的圓 (約20mm直徑)
        let expectedRadius = 50.0 // 預期半徑範圍 (像素)
        
        for circle in circles {
            if abs(circle.radius - expectedRadius) / expectedRadius < 0.3 {
                return circle
            }
        }
        
        return nil
    }
    
    /// 檢測3x3圓形網格模式
    private func detectCircleGridPattern(
        _ image: CIImage, 
        outerCircle: DetectedCircle
    ) async throws -> CircleGridPattern {
        
        var detectedCircles: [GridCircle] = []
        
        // 3x3網格的相對位置 (相對於外圓中心)
        let gridPositions = [
            // 第一行
            (-0.4, -0.4), (0.0, -0.4), (0.4, -0.4),
            // 第二行  
            (-0.4, 0.0),  (0.0, 0.0),  (0.4, 0.0),
            // 第三行
            (-0.4, 0.4),  (0.0, 0.4),  (0.4, 0.4)
        ]
        
        // 預期的圓形類型 (基於SVG設計)
        let expectedTypes: [CircleType] = [
            .large, .small, .hollow,      // 第一行
            .small, .centerGray, .large,  // 第二行
            .hollow, .small, .large       // 第三行
        ]
        
        for (index, (relX, relY)) in gridPositions.enumerated() {
            let worldX = outerCircle.center.x + CGFloat(relX) * outerCircle.radius * 0.8
            let worldY = outerCircle.center.y + CGFloat(relY) * outerCircle.radius * 0.8
            let position = CGPoint(x: worldX, y: worldY)
            
            if let detectedCircle = try await detectCircleAtPosition(
                image, 
                position: position,
                expectedType: expectedTypes[index]
            ) {
                detectedCircles.append(detectedCircle)
            }
        }
        
        return CircleGridPattern(
            detectedCircles: detectedCircles,
            gridCompleteness: Double(detectedCircles.count) / 9.0,
            patternAccuracy: calculatePatternAccuracy(detectedCircles, expectedTypes)
        )
    }
    
    // MARK: - 色彩和深度校準
    
    /// 執行色彩校準
    private func performColorCalibration(
        _ image: UIImage,
        _ arUcoResult: ArUcoDetectionResult,
        _ circleGridResult: CircleGridDetectionResult
    ) async throws -> ColorCalibrationResult {
        logger.info("執行色彩校準")
        
        // 合併兩個貼紙的色彩校正點
        var allColorPoints = arUcoResult.colorCalibrationPoints
        allColorPoints.append(contentsOf: circleGridResult.colorCalibrationPoints)
        
        // 建立色彩變換矩陣
        let colorTransformMatrix = try await calculateColorTransformMatrix(allColorPoints)
        
        // 驗證色彩準確度
        let colorAccuracy = try await validateColorAccuracy(allColorPoints)
        
        // 計算白平衡參數
        let whiteBalanceParams = calculateWhiteBalanceParameters(allColorPoints)
        
        return ColorCalibrationResult(
            colorTransformMatrix: colorTransformMatrix,
            whiteBalanceParameters: whiteBalanceParams,
            colorAccuracy: colorAccuracy,
            calibrationPoints: allColorPoints,
            confidence: calculateColorCalibrationConfidence(colorAccuracy, allColorPoints.count)
        )
    }
    
    /// 執行深度校準
    private func performDepthCalibration(
        image: UIImage,
        depthData: ARDepthData?,
        arUcoResult: ArUcoDetectionResult,
        circleGridResult: CircleGridDetectionResult
    ) async throws -> DepthCalibrationResult {
        logger.info("執行深度校準")
        
        guard let depthData = depthData else {
            // 如果沒有深度資料，回傳預設校準
            return DepthCalibrationResult(
                depthScale: 1.0,
                depthOffset: 0.0,
                confidenceThreshold: 0.7,
                depthAccuracy: 0.0,
                hasValidDepthData: false
            )
        }
        
        // 合併兩個貼紙的深度校正點
        var allDepthPoints = arUcoResult.depthCorrectionPoints
        allDepthPoints.append(contentsOf: circleGridResult.depthCorrectionPoints)
        
        // 對齊深度資料與RGB影像
        let alignedDepthData = try await alignDepthWithRGB(
            depthData: depthData,
            rgbImage: image,
            calibrationPoints: allDepthPoints
        )
        
        // 校準深度刻度和偏移
        let (depthScale, depthOffset) = try await calibrateDepthParameters(
            alignedDepthData: alignedDepthData,
            knownDistances: calculateKnownDistances(allDepthPoints)
        )
        
        // 驗證深度準確度
        let depthAccuracy = try await validateDepthAccuracy(
            alignedDepthData,
            scale: depthScale,
            offset: depthOffset
        )
        
        return DepthCalibrationResult(
            depthScale: depthScale,
            depthOffset: depthOffset,
            confidenceThreshold: 0.7,
            depthAccuracy: depthAccuracy,
            hasValidDepthData: true
        )
    }
    
    // MARK: - 校準整合和驗證
    
    /// 整合校準結果並驗證
    private func combineCalibrationsAndValidate(
        image: UIImage,
        arUcoResult: ArUcoDetectionResult,
        circleGridResult: CircleGridDetectionResult,
        colorCalibration: ColorCalibrationResult,
        depthCalibration: DepthCalibrationResult
    ) async throws -> CombinedCalibrationResult {
        logger.info("整合校準結果並驗證")
        
        // 計算最終像素密度 (綜合兩個貼紙的結果)
        let finalPixelDensity = (arUcoResult.pixelDensityMmPerPixel + circleGridResult.pixelDensityMmPerPixel) / 2.0
        
        // 交叉驗證校準準確度
        let crossValidationResult = try await performCrossValidation(
            arUcoResult: arUcoResult,
            circleGridResult: circleGridResult,
            colorCalibration: colorCalibration,
            depthCalibration: depthCalibration
        )
        
        // 計算整體校準信心度
        let overallConfidence = calculateOverallConfidence(
            arUcoConfidence: arUcoResult.confidence,
            circleGridConfidence: circleGridResult.confidence,
            colorConfidence: colorCalibration.confidence,
            depthConfidence: depthCalibration.hasValidDepthData ? 0.8 : 0.3,
            crossValidationScore: crossValidationResult.overallScore
        )
        
        // 生成校準資料
        let calibrationData = CalibrationData(
            pixelDensityMmPerPixel: finalPixelDensity,
            depthData: depthCalibration.hasValidDepthData ? ARDepthData() : nil, // 簡化版本
            arucoDetection: arUcoResult,
            circleGridDetection: circleGridResult,
            colorCalibration: colorCalibration
        )
        
        return CombinedCalibrationResult(
            calibrationData: calibrationData,
            arUcoResult: arUcoResult,
            circleGridResult: circleGridResult,
            colorCalibration: colorCalibration,
            depthCalibration: depthCalibration,
            crossValidation: crossValidationResult,
            overallConfidence: overallConfidence,
            calibrationQuality: determineCalibrationQuality(overallConfidence),
            recommendedActions: generateRecommendations(crossValidationResult, overallConfidence)
        )
    }
    
    // MARK: - 輔助方法
    
    private func calculatePixelDensity(corners: [CGPoint], physicalSize: Double) -> Double {
        // 計算ArUco正方形的像素尺寸
        let width = abs(corners[1].x - corners[0].x)
        let height = abs(corners[2].y - corners[1].y)
        let averagePixelSize = (width + height) / 2.0
        
        return Double(averagePixelSize) / physicalSize // pixels per mm
    }
    
    private func calculateOverallConfidence(
        arUcoConfidence: Double,
        circleGridConfidence: Double,
        colorConfidence: Double,
        depthConfidence: Double,
        crossValidationScore: Double
    ) -> Double {
        let weights = [0.25, 0.25, 0.2, 0.15, 0.15] // 權重分配
        let scores = [arUcoConfidence, circleGridConfidence, colorConfidence, depthConfidence, crossValidationScore]
        
        return zip(weights, scores).reduce(0) { $0 + $1.0 * $1.1 }
    }
    
    private func determineCalibrationQuality(_ confidence: Double) -> CalibrationQuality {
        switch confidence {
        case 0.9...1.0: return .excellent
        case 0.8..<0.9: return .good
        case 0.7..<0.8: return .acceptable
        default: return .poor
        }
    }
    
    private func generateRecommendations(
        _ crossValidation: CrossValidationResult,
        _ confidence: Double
    ) -> [CalibrationRecommendation] {
        var recommendations: [CalibrationRecommendation] = []
        
        if confidence < 0.8 {
            recommendations.append(.improveStickersPlacement)
        }
        
        if crossValidation.pixelDensityVariation > 0.1 {
            recommendations.append(.checkStickerDistance)
        }
        
        if crossValidation.colorAccuracyScore < 0.7 {
            recommendations.append(.improveLighting)
        }
        
        return recommendations
    }
}

// MARK: - 資料結構定義

struct ArUcoDetectionResult {
    let corners: [CGPoint]
    let colorCalibrationPoints: [ColorCalibrationPoint]
    let depthCorrectionPoints: [DepthCorrectionPoint]
    let crosshairLines: [DetectedLine]
    let pixelDensityMmPerPixel: Double
    let perspectiveTransform: CGAffineTransform
    let confidence: Double
    let detectedFeatures: ArUcoFeatures
}

struct CircleGridDetectionResult {
    let outerCircle: DetectedCircle
    let gridPattern: CircleGridPattern
    let colorCalibrationPoints: [ColorCalibrationPoint]
    let depthCorrectionPoints: [DepthCorrectionPoint]
    let crosshairLines: [DetectedLine]
    let pixelDensityMmPerPixel: Double
    let knownAreaCm2: Double
    let calculatedAreaCm2: Double
    let areaCalibrationAccuracy: Double
    let confidence: Double
    let detectedFeatures: CircleGridFeatures
}

struct ColorCalibrationResult {
    let colorTransformMatrix: ColorTransformMatrix
    let whiteBalanceParameters: WhiteBalanceParams
    let colorAccuracy: Double
    let calibrationPoints: [ColorCalibrationPoint]
    let confidence: Double
}

struct DepthCalibrationResult {
    let depthScale: Double
    let depthOffset: Double
    let confidenceThreshold: Double
    let depthAccuracy: Double
    let hasValidDepthData: Bool
}

struct CombinedCalibrationResult {
    let calibrationData: CalibrationData
    let arUcoResult: ArUcoDetectionResult
    let circleGridResult: CircleGridDetectionResult
    let colorCalibration: ColorCalibrationResult
    let depthCalibration: DepthCalibrationResult
    let crossValidation: CrossValidationResult
    let overallConfidence: Double
    let calibrationQuality: CalibrationQuality
    let recommendedActions: [CalibrationRecommendation]
}

// MARK: - 輔助結構

struct DetectedCircle {
    let center: CGPoint
    let radius: CGFloat
    let confidence: Double
}

struct ColorCalibrationPoint {
    let position: CGPoint
    let expectedColor: UIColor
    let detectedColor: UIColor
    let confidence: Double
}

struct DepthCorrectionPoint {
    let position: CGPoint
    let expectedDepth: Double
    let detectedDepth: Double
    let confidence: Double
}

struct DetectedLine {
    let startPoint: CGPoint
    let endPoint: CGPoint
    let confidence: Double
}

struct CircleGridPattern {
    let detectedCircles: [GridCircle]
    let gridCompleteness: Double
    let patternAccuracy: Double
}

struct GridCircle {
    let position: CGPoint
    let radius: CGFloat
    let type: CircleType
    let confidence: Double
}

enum CircleType {
    case large, small, hollow, centerGray
}

struct ArUcoFeatures {
    let hasValidPattern: Bool
    let hasColorPoints: Bool
    let hasDepthPoints: Bool
    let hasCrosshair: Bool
}

struct CircleGridFeatures {
    let hasValidOuterCircle: Bool
    let hasCompleteGrid: Bool
    let hasColorPoints: Bool
    let hasDepthPoints: Bool
}

struct CrossValidationResult {
    let pixelDensityVariation: Double
    let colorAccuracyScore: Double
    let spatialConsistency: Double
    let overallScore: Double
}

enum CalibrationQuality {
    case excellent, good, acceptable, poor
    
    var description: String {
        switch self {
        case .excellent: return "優秀"
        case .good: return "良好"  
        case .acceptable: return "可接受"
        case .poor: return "差"
        }
    }
}

enum CalibrationRecommendation {
    case improveStickersPlacement
    case checkStickerDistance
    case improveLighting
    case adjustCameraAngle
    case recalibrateDepth
    
    var description: String {
        switch self {
        case .improveStickersPlacement: return "改善校正貼紙擺放位置"
        case .checkStickerDistance: return "檢查校正貼紙距離"
        case .improveLighting: return "改善照明條件"
        case .adjustCameraAngle: return "調整相機角度"
        case .recalibrateDepth: return "重新校準深度"
        }
    }
}

enum CalibrationError: Error {
    case imageProcessingFailed
    case arUcoNotFound
    case circleGridNotFound
    case colorCalibrationFailed
    case depthCalibrationFailed
    case insufficientCalibrationPoints
}

// 需要定義的類型別名和結構
typealias ColorTransformMatrix = [[Double]]
typealias WhiteBalanceParams = (temperature: Double, tint: Double)
typealias ARDepthData = Data // 簡化版本，實際應該使用ARKit的深度資料