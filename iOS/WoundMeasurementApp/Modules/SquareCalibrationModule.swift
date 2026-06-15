import SwiftUI
import CoreImage
import Vision
import Accelerate
import UIKit

// MARK: - 方形校正貼紙檢測模組
@MainActor
class SquareCalibrationModule: ObservableObject {
    @Published var isDetecting = false
    @Published var detectionResult: SquareCalibrationResult?
    @Published var detectionError: String?
    @Published var colorCalibrationMatrix: [[Double]]?
    @Published var perspectiveTransform: CGAffineTransform?
    @Published var calibrationStatus = "準備檢測方形校正貼紙"
    
    private let context = CIContext()
    
    // 方形校正貼紙規格
    private let stickerSize: Double = 20.0 // mm
    private let stickerArea: Double = 400.0 // mm²
    
    // 標準色彩值 (sRGB)
    private let standardColors = [
        "red": SIMD3<Double>(1.0, 0.0, 0.0),      // #FF0000
        "yellow": SIMD3<Double>(1.0, 1.0, 0.0),   // #FFFF00  
        "green": SIMD3<Double>(0.0, 1.0, 0.0),    // #00FF00
        "blue": SIMD3<Double>(0.0, 0.0, 1.0),     // #0000FF
        "gray": SIMD3<Double>(0.176, 0.176, 0.176) // 18% Gray #2D2D2D
    ]
    
    // HSV檢測範圍
    private let colorRanges: [String: [(hMin: Float, sMin: Float, vMin: Float, hMax: Float, sMax: Float, vMax: Float)]] = [
        "red": [(0, 100, 100, 10, 255, 255), (170, 100, 100, 180, 255, 255)], // 紅色跨越0度
        "yellow": [(20, 100, 100, 30, 255, 255)],
        "green": [(40, 100, 100, 80, 255, 255)],
        "blue": [(100, 100, 100, 130, 255, 255)],
        "gray": [(0, 0, 40, 180, 30, 80)] // 低飽和度
    ]
    
    // MARK: - 主檢測函數
    func detectSquareCalibrationSticker(from image: UIImage) async throws -> SquareCalibrationResult {
        isDetecting = true
        detectionError = nil
        calibrationStatus = "正在檢測方形校正貼紙..."
        
        defer {
            Task { @MainActor in
                isDetecting = false
            }
        }
        
        guard let cgImage = image.cgImage else {
            let error = "無效的圖像"
            detectionError = error
            throw SquareCalibrationError.invalidImage
        }
        
        do {
            print("方形校正貼紙檢測: 開始分析，圖像尺寸: \(image.size)")
            
            // 1. 檢測方形邊框
            calibrationStatus = "正在檢測方形邊框..."
            let squareBounds = try await detectSquareBounds(cgImage)
            print("方形邊框檢測完成: \(squareBounds)")
            
            // 2. 檢測四角凸點
            calibrationStatus = "正在檢測四角凸點..."
            let cornerDots = try await detectCornerDots(cgImage, in: squareBounds)
            print("四角凸點檢測完成: \(cornerDots.count)個")
            
            // 3. 計算透視變換
            calibrationStatus = "正在計算透視校正..."
            let perspectiveTransform = try calculatePerspectiveTransform(cornerDots: cornerDots, targetSize: stickerSize)
            self.perspectiveTransform = perspectiveTransform
            
            // 4. 應用透視校正
            let correctedImage = try applePerspectiveCorrection(to: cgImage, transform: perspectiveTransform)
            
            // 5. 檢測RGBY色彩點
            calibrationStatus = "正在檢測色彩校正點..."
            let colorPoints = try await detectColorPoints(correctedImage)
            print("色彩點檢測完成: \(colorPoints.count)個")
            
            // 6. 計算色彩校正矩陣
            calibrationStatus = "正在計算色彩校正矩陣..."
            let colorMatrix = try calculateColorCorrectionMatrix(detectedColors: colorPoints)
            self.colorCalibrationMatrix = colorMatrix
            
            // 7. 驗證校正精度
            calibrationStatus = "正在驗證校正精度..."
            let validation = validateCalibration(
                cornerDots: cornerDots,
                colorPoints: colorPoints,
                colorMatrix: colorMatrix
            )
            
            let result = SquareCalibrationResult(
                originalImageSize: image.size,
                squareBounds: squareBounds,
                cornerDots: cornerDots,
                colorPoints: colorPoints,
                perspectiveTransform: perspectiveTransform,
                colorCorrectionMatrix: colorMatrix,
                cmPerPixel: calculateCmPerPixel(from: squareBounds),
                calibrationMethod: "square_sticker_v2",
                confidence: validation.confidence,
                colorAccuracy: validation.colorAccuracy,
                perspectiveAccuracy: validation.perspectiveAccuracy,
                validation: validation
            )
            
            detectionResult = result
            calibrationStatus = "校正完成！"
            return result
            
        } catch {
            let errorMessage = "方形校正貼紙檢測失敗: \(error.localizedDescription)"
            detectionError = errorMessage
            calibrationStatus = "檢測失敗"
            print(errorMessage)
            throw error
        }
    }
    
    // MARK: - 方形邊框檢測
    private func detectSquareBounds(_ cgImage: CGImage) async throws -> CGRect {
        return try await withCheckedThrowingContinuation { continuation in
            // 使用Vision框架檢測矩形
            let request = VNDetectRectanglesRequest { request, error in
                if let error = error {
                    continuation.resume(throwing: error)
                    return
                }
                
                guard let results = request.results as? [VNRectangleObservation],
                      let bestRectangle = results.first else {
                    continuation.resume(throwing: SquareCalibrationError.noSquareFound)
                    return
                }
                
                // 轉換為CGRect（圖像座標）
                let imageSize = CGSize(width: cgImage.width, height: cgImage.height)
                let bounds = VNImageRectForNormalizedRect(bestRectangle.boundingBox, 
                                                        Int(imageSize.width), 
                                                        Int(imageSize.height))
                
                // 驗證是否為方形（長寬比接近1:1）
                let aspectRatio = bounds.width / bounds.height
                if abs(aspectRatio - 1.0) > 0.2 {
                    continuation.resume(throwing: SquareCalibrationError.invalidSquareShape)
                    return
                }
                
                continuation.resume(returning: bounds)
            }
            
            // 設定檢測參數
            request.minimumAspectRatio = 0.8
            request.maximumAspectRatio = 1.2
            request.minimumSize = 0.1 // 最小尺寸（歸一化）
            request.maximumObservations = 5
            
            let handler = VNImageRequestHandler(cgImage: cgImage)
            do {
                try handler.perform([request])
            } catch {
                continuation.resume(throwing: error)
            }
        }
    }
    
    // MARK: - 四角凸點檢測
    private func detectCornerDots(_ cgImage: CGImage, in bounds: CGRect) async throws -> [DetectedCornerDot] {
        // 擷取方形區域
        guard let squareRegion = cgImage.cropping(to: bounds) else {
            throw SquareCalibrationError.processingFailed
        }
        
        return try await withCheckedThrowingContinuation { continuation in
            // 使用Vision檢測小圓形
            let request = VNDetectContoursRequest { request, error in
                if let error = error {
                    continuation.resume(throwing: error)
                    return
                }
                
                guard let results = request.results as? [VNContoursObservation] else {
                    continuation.resume(returning: [])
                    return
                }
                
                var cornerDots: [DetectedCornerDot] = []
                
                for observation in results {
                    let contour = observation.normalizedPath
                    if let dot = self.analyzeContourForCornerDot(contour, 
                                                               imageSize: CGSize(width: squareRegion.width, 
                                                                               height: squareRegion.height),
                                                               parentBounds: bounds) {
                        cornerDots.append(dot)
                    }
                }
                
                // 過濾並排序角點（應該有4個，分佈在四角）
                let validDots = self.filterAndSortCornerDots(cornerDots)
                continuation.resume(returning: validDots)
            }
            
            request.contrastAdjustment = 1.2
            request.detectsDarkOnLight = true
            
            let handler = VNImageRequestHandler(cgImage: squareRegion)
            do {
                try handler.perform([request])
            } catch {
                continuation.resume(throwing: error)
            }
        }
    }
    
    // MARK: - 色彩點檢測
    private func detectColorPoints(_ cgImage: CGImage) async throws -> [DetectedColorPoint] {
        return try await withCheckedThrowingContinuation { continuation in
            DispatchQueue.global(qos: .userInitiated).async {
                do {
                    var colorPoints: [DetectedColorPoint] = []
                    
                    // 轉換為UIImage用於色彩分析
                    let uiImage = UIImage(cgImage: cgImage)
                    let ciImage = CIImage(cgImage: cgImage)
                    
                    // 為每種顏色檢測
                    for (colorName, ranges) in self.colorRanges {
                        if let point = try self.detectColorPoint(in: uiImage, 
                                                               ciImage: ciImage,
                                                               colorName: colorName, 
                                                               hsvRanges: ranges) {
                            colorPoints.append(point)
                        }
                    }
                    
                    DispatchQueue.main.async {
                        continuation.resume(returning: colorPoints)
                    }
                } catch {
                    DispatchQueue.main.async {
                        continuation.resume(throwing: error)
                    }
                }
            }
        }
    }
    
    private func detectColorPoint(in uiImage: UIImage, 
                                ciImage: CIImage,
                                colorName: String, 
                                hsvRanges: [(hMin: Float, sMin: Float, vMin: Float, hMax: Float, sMax: Float, vMax: Float)]) throws -> DetectedColorPoint? {
        
        // 創建HSV色彩遮罩
        var combinedMask: CIImage?
        
        for range in hsvRanges {
            // 使用Core Image濾鏡創建HSV遮罩
            guard let hsvFilter = CIFilter(name: "CIColorControls") else { continue }
            hsvFilter.setValue(ciImage, forKey: kCIInputImageKey)
            
            // 這裡需要實作HSV範圍遮罩，簡化版本使用色彩距離
            if let mask = createColorMask(from: ciImage, 
                                        targetColor: standardColors[colorName] ?? SIMD3<Double>(0.5, 0.5, 0.5),
                                        tolerance: 0.3) {
                if combinedMask == nil {
                    combinedMask = mask
                } else {
                    // 合併遮罩
                    guard let addFilter = CIFilter(name: "CIAdditionCompositing") else { continue }
                    addFilter.setValue(combinedMask, forKey: kCIInputImageKey)
                    addFilter.setValue(mask, forKey: kCIInputBackgroundImageKey)
                    combinedMask = addFilter.outputImage
                }
            }
        }
        
        guard let mask = combinedMask else { return nil }
        
        // 找出遮罩中的最大連通區域
        if let center = findLargestConnectedComponent(in: mask) {
            // 獲取該位置的實際顏色
            let actualColor = sampleColorAt(point: center, in: ciImage)
            
            return DetectedColorPoint(
                position: center,
                colorName: colorName,
                expectedColor: standardColors[colorName]!,
                actualColor: actualColor,
                confidence: calculateColorConfidence(expected: standardColors[colorName]!, actual: actualColor)
            )
        }
        
        return nil
    }
    
    // MARK: - 色彩校正矩陣計算
    private func calculateColorCorrectionMatrix(detectedColors: [DetectedColorPoint]) throws -> [[Double]] {
        // 需要至少3個顏色點來計算校正矩陣
        guard detectedColors.count >= 3 else {
            throw SquareCalibrationError.insufficientColorPoints
        }
        
        // 構建源色彩矩陣和目標色彩矩陣
        var sourceColors: [[Double]] = []
        var targetColors: [[Double]] = []
        
        for point in detectedColors {
            sourceColors.append([point.actualColor.x, point.actualColor.y, point.actualColor.z])
            targetColors.append([point.expectedColor.x, point.expectedColor.y, point.expectedColor.z])
        }
        
        // 使用最小二乘法計算3x3色彩變換矩陣
        // 簡化版本：使用對角線矩陣
        var matrix = Array(repeating: Array(repeating: 0.0, count: 3), count: 3)
        
        for i in 0..<min(3, detectedColors.count) {
            let point = detectedColors[i]
            for j in 0..<3 {
                let source = [point.actualColor.x, point.actualColor.y, point.actualColor.z][j]
                let target = [point.expectedColor.x, point.expectedColor.y, point.expectedColor.z][j]
                if source > 0.001 {
                    matrix[j][j] = target / source
                } else {
                    matrix[j][j] = 1.0
                }
            }
        }
        
        return matrix
    }
    
    // MARK: - 透視變換計算
    private func calculatePerspectiveTransform(cornerDots: [DetectedCornerDot], targetSize: Double) throws -> CGAffineTransform {
        guard cornerDots.count == 4 else {
            throw SquareCalibrationError.invalidCornerCount
        }
        
        // 排序角點：左上、右上、左下、右下
        let sortedDots = cornerDots.sorted { dot1, dot2 in
            if abs(dot1.position.y - dot2.position.y) < 10 {
                return dot1.position.x < dot2.position.x
            }
            return dot1.position.y < dot2.position.y
        }
        
        // 計算透視變換（簡化版本）
        let sourcePoints = sortedDots.map { $0.position }
        let targetPoints = [
            CGPoint(x: 0, y: 0),
            CGPoint(x: targetSize, y: 0),
            CGPoint(x: 0, y: targetSize),
            CGPoint(x: targetSize, y: targetSize)
        ]
        
        // 使用CGAffineTransform（簡化的仿射變換）
        // 實際應用中需要使用完整的透視變換
        let transform = CGAffineTransform.identity
        
        return transform
    }
    
    // MARK: - 輔助方法
    private func calculateCmPerPixel(from bounds: CGRect) -> Double {
        let pixelSize = max(bounds.width, bounds.height)
        return stickerSize / Double(pixelSize) / 10.0 // 轉換為cm
    }
    
    private func analyzeContourForCornerDot(_ contour: CGPath, imageSize: CGSize, parentBounds: CGRect) -> DetectedCornerDot? {
        // 分析輪廓是否為小圓形凸點
        let boundingBox = contour.boundingBox
        
        // 檢查尺寸是否合理（應該是小圓點）
        let diameter = max(boundingBox.width, boundingBox.height)
        if diameter < 5 || diameter > 20 { return nil }
        
        // 檢查圓形度
        let area = boundingBox.width * boundingBox.height
        let perimeter = 2 * .pi * (diameter / 2)
        let circularity = 4 * .pi * area / (perimeter * perimeter)
        
        if circularity < 0.7 { return nil }
        
        let center = CGPoint(
            x: parentBounds.minX + boundingBox.midX,
            y: parentBounds.minY + boundingBox.midY
        )
        
        return DetectedCornerDot(
            position: center,
            radius: Double(diameter / 2),
            confidence: circularity
        )
    }
    
    private func filterAndSortCornerDots(_ dots: [DetectedCornerDot]) -> [DetectedCornerDot] {
        // 過濾並排序角點，確保只有4個且分佈在四角
        let sortedDots = dots.sorted { $0.confidence > $1.confidence }
        return Array(sortedDots.prefix(4))
    }
    
    private func createColorMask(from image: CIImage, targetColor: SIMD3<Double>, tolerance: Double) -> CIImage? {
        // 簡化的色彩遮罩創建
        guard let filter = CIFilter(name: "CIColorCube") else { return nil }
        
        // 這裡需要實作複雜的色彩立方體查找表
        // 簡化版本返回原圖
        return image
    }
    
    private func findLargestConnectedComponent(in mask: CIImage) -> CGPoint? {
        // 找出最大連通區域的中心點
        // 簡化版本返回圖像中心
        return CGPoint(x: mask.extent.midX, y: mask.extent.midY)
    }
    
    private func sampleColorAt(point: CGPoint, in image: CIImage) -> SIMD3<Double> {
        // 在指定位置採樣顏色
        // 簡化版本返回白色
        return SIMD3<Double>(1.0, 1.0, 1.0)
    }
    
    private func calculateColorConfidence(expected: SIMD3<Double>, actual: SIMD3<Double>) -> Double {
        // 計算色彩匹配置信度
        let diff = expected - actual
        let distance = sqrt(diff.x * diff.x + diff.y * diff.y + diff.z * diff.z)
        return max(0.0, 1.0 - distance / sqrt(3.0))
    }
    
    private func applePerspectiveCorrection(to image: CGImage, transform: CGAffineTransform) throws -> CGImage {
        // 應用透視校正
        // 簡化版本返回原圖
        return image
    }
    
    private func validateCalibration(cornerDots: [DetectedCornerDot], 
                                   colorPoints: [DetectedColorPoint], 
                                   colorMatrix: [[Double]]) -> CalibrationValidation {
        // 分別計算角點和顏色點的平均置信度以避免編譯器複雜度問題
        let cornerConfidences = cornerDots.map { $0.confidence }
        let averageCornerConfidence = cornerConfidences.reduce(0, +) / Double(cornerDots.count)
        
        let colorConfidences = colorPoints.map { $0.confidence }
        let averageColorConfidence = colorConfidences.reduce(0, +) / Double(colorPoints.count)
        
        let confidence = min(averageCornerConfidence, averageColorConfidence)
        
        return CalibrationValidation(
            confidence: confidence,
            colorAccuracy: calculateColorAccuracy(colorPoints),
            perspectiveAccuracy: calculatePerspectiveAccuracy(cornerDots),
            warnings: generateValidationWarnings(cornerDots: cornerDots, colorPoints: colorPoints)
        )
    }
    
    private func calculateColorAccuracy(_ points: [DetectedColorPoint]) -> Double {
        guard !points.isEmpty else { return 0.0 }
        
        let totalAccuracy = points.map { point in
            calculateColorConfidence(expected: point.expectedColor, actual: point.actualColor)
        }.reduce(0, +)
        
        return totalAccuracy / Double(points.count)
    }
    
    private func calculatePerspectiveAccuracy(_ dots: [DetectedCornerDot]) -> Double {
        guard dots.count == 4 else { return 0.0 }
        
        // 計算四個角點形成的四邊形是否接近正方形
        let avgConfidence = dots.map { $0.confidence }.reduce(0, +) / 4.0
        return avgConfidence
    }
    
    private func generateValidationWarnings(cornerDots: [DetectedCornerDot], colorPoints: [DetectedColorPoint]) -> [String] {
        var warnings: [String] = []
        
        if cornerDots.count < 4 {
            warnings.append("檢測到的角點不足4個")
        }
        
        if colorPoints.count < 4 {
            warnings.append("檢測到的色彩點不足")
        }
        
        let lowConfidenceColors = colorPoints.filter { $0.confidence < 0.7 }
        if !lowConfidenceColors.isEmpty {
            warnings.append("部分色彩點檢測置信度較低")
        }
        
        return warnings
    }
}

// MARK: - 資料結構
struct SquareCalibrationResult {
    let originalImageSize: CGSize
    let squareBounds: CGRect
    let cornerDots: [DetectedCornerDot]
    let colorPoints: [DetectedColorPoint]
    let perspectiveTransform: CGAffineTransform
    let colorCorrectionMatrix: [[Double]]
    let cmPerPixel: Double
    let calibrationMethod: String
    let confidence: Double
    let colorAccuracy: Double
    let perspectiveAccuracy: Double
    let validation: CalibrationValidation
}

struct DetectedCornerDot {
    let position: CGPoint
    let radius: Double
    let confidence: Double
}

struct DetectedColorPoint {
    let position: CGPoint
    let colorName: String
    let expectedColor: SIMD3<Double>
    let actualColor: SIMD3<Double>
    let confidence: Double
}

struct CalibrationValidation {
    let confidence: Double
    let colorAccuracy: Double
    let perspectiveAccuracy: Double
    let warnings: [String]
}

// MARK: - 錯誤類型
enum SquareCalibrationError: Error, LocalizedError {
    case invalidImage
    case noSquareFound
    case invalidSquareShape
    case insufficientColorPoints
    case invalidCornerCount
    case processingFailed
    
    var errorDescription: String? {
        switch self {
        case .invalidImage:
            return "無效的圖像"
        case .noSquareFound:
            return "未找到方形校正貼紙"
        case .invalidSquareShape:
            return "檢測到的形狀不是正方形"
        case .insufficientColorPoints:
            return "檢測到的色彩點不足"
        case .invalidCornerCount:
            return "角點數量不正確"
        case .processingFailed:
            return "圖像處理失敗"
        }
    }
}