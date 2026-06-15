import SwiftUI
import CoreImage
import Vision
import Accelerate
import UIKit

// 將VisionBasedStickerDetector類移到這裡以確保作用域正確
/// 基於Vision框架的校正貼紙檢測器
class VisionBasedStickerDetector: ObservableObject {
    
    // MARK: - 檢測參數（針對平面校正貼紙優化）
    struct DetectionConfig {
        static let circularityThreshold: Float = 0.65      // 降低圓形度閾值（平面貼紙邊緣可能不夠完美）
        static let minContourArea: Float = 50.0            // 降低最小輪廓面積
        static let maxContourArea: Float = 15000.0         // 增加最大輪廓面積
        static let aspectRatioTolerance: Float = 0.4       // 增加長寬比容忍度
        static let colorVarianceThreshold: Float = 30.0    // 降低顏色變異閾值（平面貼紙顏色更均勻）
        static let edgeThreshold: Float = 0.08             // 降低邊緣檢測閾值
        static let flatStickerBonus: Float = 0.1           // 平面貼紙檢測加分
    }
    
    // MARK: - 檢測結果
    struct StickerDetectionResult {
        let center: CGPoint
        let radius: CGFloat
        let confidence: Float
        let boundingBox: CGRect
        let circularity: Float
        let colorUniformity: Float
        let edgeSharpness: Float
        let stickerType: StickerType
        
        enum StickerType {
            case circular20mm      // 20mm圓形平面校正貼紙（無凸點）
            case square20mm        // 20mm方形平面校正貼紙（無凸點）
            case unknown
            
            var realWorldSize: CGFloat {
                switch self {
                case .circular20mm, .square20mm:
                    return 20.0 // mm
                case .unknown:
                    return 0.0
                }
            }
        }
    }
    
    // MARK: - 錯誤類型
    enum DetectionError: Error, LocalizedError {
        case invalidImage
        case visionRequestFailed
        case noStickersDetected
        case processingFailed
        
        var errorDescription: String? {
            switch self {
            case .invalidImage:
                return "無效的圖像數據"
            case .visionRequestFailed:
                return "Vision請求執行失敗"
            case .noStickersDetected:
                return "未檢測到校正貼紙"
            case .processingFailed:
                return "圖像處理失敗"
            }
        }
    }
    
    // MARK: - 檢測方法
    
    /// 檢測校正貼紙的主要入口
    func detectCalibrationStickers(in image: UIImage) async throws -> [StickerDetectionResult] {
        print("🔍 Vision貼紙檢測器: 開始分析圖像...")
        
        guard let cgImage = image.cgImage else {
            throw DetectionError.invalidImage
        }
        
        // 使用Vision框架進行形狀檢測
        let shapeResults = try await detectShapesUsingVision(cgImage)
        
        // 過濾和評分候選形狀
        let filteredResults = filterAndScoreCandidates(shapeResults, imageSize: image.size)
        
        // 按置信度排序
        let sortedResults = filteredResults.sorted { $0.confidence > $1.confidence }
        
        print("✅ Vision檢測器完成: 找到 \(sortedResults.count) 個校正貼紙候選")
        return sortedResults
    }
    
    /// 使用Vision框架檢測形狀
    private func detectShapesUsingVision(_ cgImage: CGImage) async throws -> [VNContoursObservation] {
        return try await withCheckedThrowingContinuation { continuation in
            let request = VNDetectContoursRequest { request, error in
                if let error = error {
                    continuation.resume(throwing: error)
                    return
                }
                
                guard let results = request.results as? [VNContoursObservation] else {
                    continuation.resume(returning: [])
                    return
                }
                
                continuation.resume(returning: results)
            }
            
            // 針對平面校正貼紙優化參數
            request.contrastAdjustment = 1.2  // 增強對比度以更好檢測平面邊界
            request.detectsDarkOnLight = true
            request.maximumImageDimension = 2048  // 保持高解析度以檢測小物體
            
            let handler = VNImageRequestHandler(cgImage: cgImage)
            do {
                try handler.perform([request])
            } catch {
                continuation.resume(throwing: error)
            }
        }
    }
    
    /// 過濾和評分候選形狀
    private func filterAndScoreCandidates(_ contours: [VNContoursObservation], imageSize: CGSize) -> [StickerDetectionResult] {
        var results: [StickerDetectionResult] = []
        
        for contour in contours {
            guard let pathPoints = extractPointsFromContour(contour),
                  pathPoints.count > 10 else {
                continue
            }
            
            // 分析輪廓幾何特性
            let center = calculateCentroid(pathPoints)
            let (avgRadius, circularity) = calculateCircularityMetrics(pathPoints, center: center)
            let boundingRect = calculateBoundingRect(pathPoints)
            
            // 檢查是否符合校正貼紙特徵
            let area = boundingRect.width * boundingRect.height
            guard area >= CGFloat(DetectionConfig.minContourArea),
                  area <= CGFloat(DetectionConfig.maxContourArea),
                  circularity >= DetectionConfig.circularityThreshold else {
                continue
            }
            
            // 檢測貼紙類型
            let stickerType = determineStickerType(boundingRect: boundingRect, circularity: circularity)
            
            // 計算綜合信心度
            let confidence = calculateConfidence(
                circularity: circularity,
                area: Float(area),
                boundingRect: boundingRect,
                stickerType: stickerType
            )
            
            let result = StickerDetectionResult(
                center: CGPoint(x: center.x * imageSize.width, y: center.y * imageSize.height),
                radius: avgRadius * max(imageSize.width, imageSize.height),
                confidence: confidence,
                boundingBox: CGRect(
                    x: boundingRect.minX * imageSize.width,
                    y: boundingRect.minY * imageSize.height,
                    width: boundingRect.width * imageSize.width,
                    height: boundingRect.height * imageSize.height
                ),
                circularity: circularity,
                colorUniformity: 0.8, // 平面貼紙顏色較均勻
                edgeSharpness: 0.7,   // 平面貼紙邊緣相對清晰
                stickerType: stickerType
            )
            
            results.append(result)
        }
        
        return results
    }
    
    // MARK: - 輔助方法
    
    private func extractPointsFromContour(_ contour: VNContoursObservation) -> [CGPoint]? {
        let path = contour.normalizedPath
        var points: [CGPoint] = []
        
        path.applyWithBlock { elementPtr in
            let element = elementPtr.pointee
            switch element.type {
            case .moveToPoint, .addLineToPoint:
                points.append(element.points[0])
            case .addQuadCurveToPoint:
                points.append(element.points[0])
                points.append(element.points[1])
            case .addCurveToPoint:
                points.append(element.points[0])
                points.append(element.points[1])
                points.append(element.points[2])
            case .closeSubpath:
                break
            @unknown default:
                break
            }
        }
        
        return points.isEmpty ? nil : points
    }
    
    private func calculateCentroid(_ points: [CGPoint]) -> CGPoint {
        let sumX = points.reduce(0) { $0 + $1.x }
        let sumY = points.reduce(0) { $0 + $1.y }
        return CGPoint(x: sumX / CGFloat(points.count), y: sumY / CGFloat(points.count))
    }
    
    private func calculateCircularityMetrics(_ points: [CGPoint], center: CGPoint) -> (avgRadius: CGFloat, circularity: Float) {
        var distances: [CGFloat] = []
        
        for point in points {
            let distance = sqrt(pow(point.x - center.x, 2) + pow(point.y - center.y, 2))
            distances.append(distance)
        }
        
        let avgRadius = distances.reduce(0, +) / CGFloat(distances.count)
        
        // 計算圓形度（距離變異度越小越圓）
        let variance = distances.reduce(0) { result, distance in
            result + pow(distance - avgRadius, 2)
        } / CGFloat(distances.count)
        
        let standardDeviation = sqrt(variance)
        let circularity = max(0, 1 - Float(standardDeviation / max(avgRadius, 1)))
        
        return (avgRadius, circularity)
    }
    
    private func calculateBoundingRect(_ points: [CGPoint]) -> CGRect {
        guard !points.isEmpty else { return .zero }
        
        let minX = points.min { $0.x < $1.x }!.x
        let maxX = points.max { $0.x < $1.x }!.x
        let minY = points.min { $0.y < $1.y }!.y
        let maxY = points.max { $0.y < $1.y }!.y
        
        return CGRect(x: minX, y: minY, width: maxX - minX, height: maxY - minY)
    }
    
    private func determineStickerType(boundingRect: CGRect, circularity: Float) -> StickerDetectionResult.StickerType {
        let aspectRatio = boundingRect.width / boundingRect.height
        
        if circularity > 0.75 && abs(aspectRatio - 1.0) < 0.2 {
            return .circular20mm
        } else if abs(aspectRatio - 1.0) < 0.15 && circularity < 0.9 {
            return .square20mm
        } else {
            return .unknown
        }
    }
    
    private func calculateConfidence(
        circularity: Float,
        area: Float,
        boundingRect: CGRect,
        stickerType: StickerDetectionResult.StickerType
    ) -> Float {
        var confidence: Float = 0.0
        
        // 圓形度評分 (40%)
        confidence += circularity * 0.4
        
        // 尺寸合理性評分 (30%)
        let normalizedArea = area / 10000.0 // 正規化面積
        let sizeScore = min(1.0, max(0.0, 1.0 - abs(normalizedArea - 0.5) * 2))
        confidence += sizeScore * 0.3
        
        // 長寬比評分 (20%)
        let aspectRatio = Float(boundingRect.width / boundingRect.height)
        let aspectScore = max(0.0, 1.0 - abs(aspectRatio - 1.0) * 3)
        confidence += aspectScore * 0.2
        
        // 貼紙類型加分 (10%)
        switch stickerType {
        case .circular20mm, .square20mm:
            confidence += DetectionConfig.flatStickerBonus
        case .unknown:
            confidence += 0.0
        }
        
        return min(1.0, confidence)
    }
}

@MainActor
class CalibrationStickerModule: ObservableObject {
    @Published var isDetecting = false
    @Published var detectionResult: StickerCalibrationResult?
    @Published var detectionError: String?
    @Published var roiDetectionConfidence: Double = 0.0
    @Published var shouldUseManualROI = false
    @Published var calibrationStatus = "準備檢測校準貼紙"
    @Published var debugOverlayImage: UIImage? // 開發者模式疊圖
    
    // 新增對方形貼紙的支援
    @Published var squareCalibrationResult: SquareCalibrationResult?
    
    // 貼紙類型枚舉
    enum StickerType {
        case circular
        case square
        case automatic // 自動檢測類型
    }
    
    private let context = CIContext()
    
    // 增強Vision檢測器
    private let visionDetector = VisionBasedStickerDetector()
    
    // 按照您的要求支援三種規格的校準貼紙
    private let standardStickerDiameter: Double = 20.0 // mm (預設 2cm)
    private let supportedDiameters: [Double] = [10.0, 20.0, 30.0] // 1cm, 2cm, 3cm
    private let standardArea: Double = 3.14159 // cm² (π * (1cm)²)
    private let expectedColorPoints = ["紅色", "綠色", "藍色", "黑色", "白色"] // RGB + 3D凸點 + 白平衡
    
    // 技術文件建議的校準精度標準
    private let calibrationAccuracyThreshold: Double = 0.05 // 5%誤差內視為準確
    private let minimumConfidenceThreshold: Double = 0.8 // 最低置信度要求
    
    // 按照技術文件建議的多階段校準貼紙檢測
    func detectCalibrationSticker(from image: UIImage, expectedDiameter: Double? = nil) async throws -> StickerCalibrationResult {
        isDetecting = true
        detectionError = nil
        
        defer {
            Task { @MainActor in
                isDetecting = false
            }
        }
        
        guard let cgImage = image.cgImage else {
            let error = "無效的圖像"
            detectionError = error
            throw StickerCalibrationError.invalidImage
        }
        
        do {
            print("校正貼紙檢測: 開始分析圖像，尺寸: \(image.size)")
            print("校正貼紙檢測: 原始CGImage尺寸: \(cgImage.width)x\(cgImage.height)")
            let hasOpenCV = (NSClassFromString("OpenCVCircleDetector") != nil)
            print("校正貼紙檢測: OpenCV 可用 = \(hasOpenCV ? "是" : "否")")

            // 降採樣到安全尺寸進行檢測，避免 7K×5K 尺寸導致耗時或崩潰
            let (workCG, scaleFactor): (CGImage, CGFloat) = makeScaledCGImageIfNeeded(from: cgImage, maxDimension: 2048)
            print("校正貼紙檢測: 工作CGImage尺寸: \(workCG.width)x\(workCG.height) (scaleFactor=\(String(format: "%.3f", scaleFactor)))")
            print("校正貼紙檢測: 預期貼紙直徑: \(standardStickerDiameter)mm")
            
            // 首先使用增強Vision檢測器
            print("🔍 嘗試使用增強Vision檢測器...")
            do {
                let visionResults = try await visionDetector.detectCalibrationStickers(in: image)
                if !visionResults.isEmpty {
                    print("✅ Vision檢測器找到 \(visionResults.count) 個校正貼紙")
                    
                    // 選擇最佳候選
                    let bestVisionResult = visionResults.sorted { $0.confidence > $1.confidence }.first!
                    
                    // 轉換為現有的DetectedCircle格式
                    let detectedCircle = convertVisionResultToDetectedCircle(bestVisionResult)
                    
                    let result = try processDetectedCircle(detectedCircle, cgImage: cgImage)
                    detectionResult = result
                    print("🎯 Vision檢測器成功完成校正貼紙檢測")
                    return result
                }
            } catch {
                print("⚠️ Vision檢測器失敗，回退到傳統檢測方法: \(error.localizedDescription)")
            }
            
            // 1. 轉換為灰階圖像（傳統檢測方法的備用方案）
            print("📷 使用傳統檢測方法作為備用方案...")
            let grayImage = try convertToGrayscale(workCG)
            print("校正貼紙檢測: 灰階轉換完成，灰階圖像尺寸: \(grayImage.width)x\(grayImage.height)")
            
            // 2. 使用Vision框架檢測圓形
            var circles: [DetectedCircle] = []
            do {
                circles = try await detectCirclesUsingVision(workCG)
            if AppDebugSettings.isDeveloperMode { print("校正貼紙檢測: Vision檢測到 \(circles.count) 個圓形區域") }
            } catch {
                print("校正貼紙檢測: Vision檢測失敗: \(error)")
            }
            
            // 3. 使用增強多通道 OpenCV 檢測（與 Vision 互補）
            var houghCircles: [DetectedCircle] = []
            if AppDebugSettings.isDeveloperMode {
                print("校正貼紙檢測: 開始執行多通道Hough圓形檢測...")
            }
            do {
                // 優先嘗試多通道增強檢測
                if NSClassFromString("OpenCVCircleDetector") != nil {
                    let minDim = min(workCG.width, workCG.height)
                    let minR = max(8, Int(Double(minDim) * 0.015))
                    let maxR = max(minR + 10, Int(Double(minDim) * 0.30))
                    let dpRatio = 1.3
                    let minDist = Double(minDim) / 20.0
                    let canny: Double = 120
                    let acc: Double = 30
                    let topN: UInt = 8

                    // 先嘗試標準檢測
                    var cvResults = OpenCVCircleDetector.detectCircles(in: workCG,
                                                                       minRadius: Int32(minR),
                                                                       maxRadius: Int32(maxR),
                                                                       dpRatio: dpRatio,
                                                                       minDistBetween: minDist,
                                                                       cannyThreshold: canny,
                                                                       accumulatorThreshold: acc,
                                                                       topN: topN)

                    // 如果標準檢測結果不足，使用多通道增強檢測
                    if cvResults.count < 3 {
                        print("校正貼紙檢測: 標準檢測結果不足(\(cvResults.count))，啟用多通道增強檢測...")
                        cvResults = OpenCVCircleDetector.detectCirclesMultiChannel(
                            in: workCG,
                            minRadius: Int32(minR),
                            maxRadius: Int32(maxR),
                            dpRatio: dpRatio,
                            minDistBetween: minDist,
                            cannyThreshold: canny,
                            accumulatorThreshold: acc,
                            topN: topN,
                            useLabChannel: true,
                            useHSVChannel: true,
                            useRGBChannels: false, // RGB通道計算量大，先不啟用
                            parameterSweep: true
                        )
                    }

                    for anyVal in cvResults {
                        if let rect = (anyVal as? NSValue)?.cgRectValue {
                            let center = CGPoint(x: rect.origin.x, y: rect.origin.y)
                            let radius = Double(rect.size.width)
                            // 回推至原始比例
                            let rescaled = DetectedCircle(center: CGPoint(x: center.x * scaleFactor,
                                                                          y: center.y * scaleFactor),
                                                          radius: radius * Double(scaleFactor),
                                                          confidence: 0.7)
                            houghCircles.append(rescaled)
                        }
                    }
                    if AppDebugSettings.isDeveloperMode {
                        print("增強OpenCV檢測: 偵測到 \(houghCircles.count) 個候選圓形 (minR=\(minR), maxR=\(maxR))")
                    }
                } else {
                    print("OpenCV HoughCircles: OpenCVCircleDetector 不可用，改用 CI Hough")
                }

                // 若 OpenCV 候選不足或不可用，退回 CI-Hough
                if houghCircles.isEmpty {
                    houghCircles = try detectCirclesUsingHough(grayImage, imageSize: CGSize(width: workCG.width, height: workCG.height))
                }

                if AppDebugSettings.isDeveloperMode {
                    print("校正貼紙檢測: 總共檢測到 \(houghCircles.count) 個圓形")
                }
            } catch {
                print("校正貼紙檢測: Hough檢測失敗: \(error)")
            }
            
            // 檢查是否有足夠的候選圓形
            if circles.isEmpty && houghCircles.isEmpty {
                print("校正貼紙檢測: 未檢測到任何圓形，嘗試降低檢測門檻...")
                // 嘗試更寬鬆的參數重新檢測
                do {
                    let relaxedHoughCircles = try detectCirclesUsingRelaxedHough(grayImage, imageSize: image.size)
                    print("校正貼紙檢測: 寬鬆模式檢測到 \(relaxedHoughCircles.count) 個圓形")
                    
                    if !relaxedHoughCircles.isEmpty {
                        let bestCircle = try selectBestCalibrationCircle(visionCircles: [], houghCircles: relaxedHoughCircles, originalImage: workCG)
                        // 將工作座標的圓放大回原始比例
                        let rescaled = DetectedCircle(
                            center: CGPoint(x: bestCircle.center.x * scaleFactor, y: bestCircle.center.y * scaleFactor),
                            radius: bestCircle.radius * Double(scaleFactor),
                            confidence: bestCircle.confidence
                        )
                        return try processDetectedCircle(rescaled, cgImage: cgImage)
                    }
                } catch {
                    print("校正貼紙檢測: 寬鬆模式也失敗: \(error)")
                }
                
                print("校正貼紙檢測: 所有檢測方法都失敗，未找到校正貼紙")
                throw StickerCalibrationError.noStickerFound
            }
            
            // 4. 合併並篩選最佳圓形候選（若失敗，嘗試較寬鬆的 OpenCV 參數再次偵測）
            do {
                let bestCircleWork = try selectBestCalibrationCircle(visionCircles: circles, houghCircles: houghCircles, originalImage: workCG)
                // 轉回原始像素尺度
                let bestCircle = DetectedCircle(
                    center: CGPoint(x: bestCircleWork.center.x * scaleFactor, y: bestCircleWork.center.y * scaleFactor),
                    radius: bestCircleWork.radius * Double(scaleFactor),
                    confidence: bestCircleWork.confidence
                )
                let result = try processDetectedCircle(bestCircle, cgImage: cgImage)
                detectionResult = result
                return result
            } catch {
                if AppDebugSettings.isDeveloperMode {
                    print("校正貼紙檢測: 首次挑選失敗，嘗試較寬鬆的 OpenCV 參數...")
                }
                var relaxedCV: [DetectedCircle] = []
                let minDim2 = min(workCG.width, workCG.height)
                let minR2 = max(6, Int(Double(minDim2) * 0.012))
                let maxR2 = max(minR2 + 8, Int(Double(minDim2) * 0.35))
                let dp2 = 1.2
                let minDist2 = Double(minDim2) / 24.0
                let canny2: Double = 100
                let acc2: Double = 25
                let topN2: UInt = 12
                let cvResults2 = OpenCVCircleDetector.detectCircles(in: workCG,
                                                                     minRadius: Int32(minR2),
                                                                     maxRadius: Int32(maxR2),
                                                                     dpRatio: dp2,
                                                                     minDistBetween: minDist2,
                                                                     cannyThreshold: canny2,
                                                                     accumulatorThreshold: acc2,
                                                                     topN: topN2)
                for anyVal in cvResults2 {
                    if let rect = (anyVal as? NSValue)?.cgRectValue {
                        let center = CGPoint(x: rect.origin.x, y: rect.origin.y)
                        let radius = Double(rect.size.width)
                        let rescaled = DetectedCircle(center: CGPoint(x: center.x * scaleFactor,
                                                                      y: center.y * scaleFactor),
                                                     radius: radius * Double(scaleFactor),
                                                     confidence: 0.6)
                        relaxedCV.append(rescaled)
                    }
                }
                if !relaxedCV.isEmpty {
                    if AppDebugSettings.isDeveloperMode { print("校正貼紙檢測: 寬鬆OpenCV偵測到 \(relaxedCV.count) 個候選，重新挑選最佳圓形") }
                    let bestCircle2 = try selectBestCalibrationCircle(visionCircles: [], houghCircles: relaxedCV, originalImage: cgImage)
                    let result = try processDetectedCircle(bestCircle2, cgImage: cgImage)
                    detectionResult = result
                    return result
                }
                throw error
            }
            
        } catch {
            let errorMessage = "校正貼紙檢測失敗: \(error.localizedDescription)"
            detectionError = errorMessage
            print(errorMessage)
            throw error
        }
    }
    
    // MARK: - Vision結果轉換方法
    
    /// 將VisionBasedStickerDetector的結果轉換為DetectedCircle格式
    private func convertVisionResultToDetectedCircle(_ visionResult: VisionBasedStickerDetector.StickerDetectionResult) -> DetectedCircle {
        return DetectedCircle(
            center: visionResult.center,
            radius: Double(visionResult.radius),
            confidence: Double(visionResult.confidence)
        )
    }
    
    // MARK: - 私有檢測方法
    
    private func convertToGrayscale(_ cgImage: CGImage) throws -> CGImage {
        let ciImage = CIImage(cgImage: cgImage)
        
        guard let filter = CIFilter(name: "CIColorMonochrome") else {
            throw StickerCalibrationError.processingFailed
        }
        
        filter.setValue(ciImage, forKey: kCIInputImageKey)
        filter.setValue(CIColor.gray, forKey: "inputColor")
        filter.setValue(1.0, forKey: "inputIntensity")
        
        guard let outputImage = filter.outputImage,
              let grayImage = context.createCGImage(outputImage, from: outputImage.extent) else {
            throw StickerCalibrationError.processingFailed
        }
        
        return grayImage
    }

    // 將超大圖降採樣到 maxDimension 內，回傳縮放後 CGImage 與縮放比例（原圖/工作圖）
    private func makeScaledCGImageIfNeeded(from cgImage: CGImage, maxDimension: Int) -> (CGImage, CGFloat) {
        let w = cgImage.width
        let h = cgImage.height
        let maxSide = max(w, h)
        if maxSide <= maxDimension { return (cgImage, 1.0) }

        let scale = CGFloat(maxSide) / CGFloat(maxDimension)
        let newW = Int(round(CGFloat(w) / scale))
        let newH = Int(round(CGFloat(h) / scale))

        let colorSpace = CGColorSpaceCreateDeviceRGB()
        guard let ctx = CGContext(
            data: nil,
            width: newW,
            height: newH,
            bitsPerComponent: 8,
            bytesPerRow: newW * 4,
            space: colorSpace,
            bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue
        ) else { return (cgImage, 1.0) }

        ctx.interpolationQuality = .medium
        ctx.draw(cgImage, in: CGRect(x: 0, y: 0, width: newW, height: newH))
        guard let scaled = ctx.makeImage() else { return (cgImage, 1.0) }
        return (scaled, scale)
    }
    
    private func detectCirclesUsingVision(_ cgImage: CGImage) async throws -> [DetectedCircle] {
        return try await withCheckedThrowingContinuation { continuation in
            let request = VNDetectContoursRequest { request, error in
                if let error = error {
                    continuation.resume(throwing: error)
                    return
                }
                
                guard let results = request.results as? [VNContoursObservation] else {
                    continuation.resume(returning: [])
                    return
                }
                
                var circles: [DetectedCircle] = []
                
                for observation in results {
                    let contour = observation.normalizedPath
                    if let circle = self.analyzeContourForCircularity(contour, imageSize: CGSize(width: cgImage.width, height: cgImage.height)) {
                        circles.append(circle)
                    }
                }
                
                continuation.resume(returning: circles)
            }
            
            request.contrastAdjustment = 1.1
            request.detectsDarkOnLight = true
            request.maximumImageDimension = 1400  // 控制運算量
            
            let handler = VNImageRequestHandler(cgImage: cgImage)
            do {
                try handler.perform([request])
            } catch {
                continuation.resume(throwing: error)
            }
        }
    }
    
    private func detectCirclesUsingHough(_ grayImage: CGImage, imageSize: CGSize) throws -> [DetectedCircle] {
        if AppDebugSettings.isDeveloperMode {
            print("Hough檢測: 開始處理圖像尺寸 \(grayImage.width)x\(grayImage.height)")
        }

        // 邊緣增強
        let workGray: CGImage
        do {
            workGray = try enhanceEdgesForHough(grayImage)
            if AppDebugSettings.isDeveloperMode { print("Hough檢測: 已套用邊緣增強") }
        } catch {
            if AppDebugSettings.isDeveloperMode { print("Hough檢測: 邊緣增強失敗，使用原灰階圖像: \(error)") }
            workGray = grayImage
        }

        let width = workGray.width
        let height = workGray.height

        guard let data = workGray.dataProvider?.data,
              let bytes = CFDataGetBytePtr(data) else {
            if AppDebugSettings.isDeveloperMode { print("Hough檢測: 無法獲取圖像數據") }
            throw StickerCalibrationError.processingFailed
        }
        
        var circles: [DetectedCircle] = []
        
        // 搜索合理的圓形半徑範圍 (基於圖像尺寸)
        // 預估貼紙直徑約占較短邊的 2%~20%
        let minRadius = max(8, min(width, height) / 50)
        let maxRadius = max(min(width, height) / 5, minRadius + 10)
        
        if AppDebugSettings.isDeveloperMode { print("Hough檢測: 搜索半徑範圍 \(minRadius) - \(maxRadius)") }
        
        var candidatesCount = 0
        
        // 簡化的圓形檢測邏輯
        for r in stride(from: minRadius, to: maxRadius, by: max(3, (maxRadius-minRadius)/60)) {  // 控制計算量
            for y in stride(from: r, to: height - r, by: 6) {    // 更精細的掃描
                for x in stride(from: r, to: width - r, by: 6) {
                    let circularity = calculateCircularityAtPosition(bytes, width: width, height: height, centerX: x, centerY: y, radius: r)
                    
                    if circularity > 0.20 { // 輕度放寬圓形度門檻
                        let circle = DetectedCircle(
                            center: CGPoint(x: x, y: y),
                            radius: Double(r),
                            confidence: circularity
                        )
                        circles.append(circle)
                        candidatesCount += 1
                        
                        if AppDebugSettings.isDeveloperMode {
                            if candidatesCount <= 5 {
                                print("Hough檢測: 找到候選圓形 #\(candidatesCount) - 中心:(\(x),\(y)) 半徑:\(r) 圓形度:\(String(format: "%.3f", circularity))")
                            }
                        }
                    }
                }
            }
        }
        
        if AppDebugSettings.isDeveloperMode { print("Hough檢測: 總共找到 \(circles.count) 個候選圓形") }
        
        // 返回按信心度排序的結果，最多保留前10個
        let sortedCircles = circles.sorted { circle1, circle2 in circle1.confidence > circle2.confidence }
        let topCircles = Array(sortedCircles.prefix(10))
        
        if AppDebugSettings.isDeveloperMode { print("Hough檢測: 返回前 \(topCircles.count) 個最佳候選") }
        return topCircles
    }

    // 針對 Hough 的邊緣增強：Gamma/對比 + Unsharp + Laplacian + 限幅
    private func enhanceEdgesForHough(_ gray: CGImage) throws -> CGImage {
        var ci = CIImage(cgImage: gray)
        if let gamma = CIFilter(name: "CIGammaAdjust") {
            gamma.setValue(ci, forKey: kCIInputImageKey)
            gamma.setValue(0.85, forKey: "inputPower")
            ci = gamma.outputImage ?? ci
        }
        if let controls = CIFilter(name: "CIColorControls") {
            controls.setValue(ci, forKey: kCIInputImageKey)
            controls.setValue(1.15, forKey: kCIInputContrastKey)
            controls.setValue(0.0, forKey: kCIInputSaturationKey)
            ci = controls.outputImage ?? ci
        }
        if let unsharp = CIFilter(name: "CIUnsharpMask") {
            unsharp.setValue(ci, forKey: kCIInputImageKey)
            unsharp.setValue(1.0, forKey: kCIInputRadiusKey)
            unsharp.setValue(0.6, forKey: kCIInputIntensityKey)
            ci = unsharp.outputImage ?? ci
        }
        let lapKernel: [CGFloat] = [
            0,  -1,  0,
           -1,   4, -1,
            0,  -1,  0
        ]
        if let conv = CIFilter(name: "CIConvolution3X3") {
            conv.setValue(ci, forKey: kCIInputImageKey)
            let weightVector: CIVector = lapKernel.withUnsafeBufferPointer { ptr in
                return CIVector(values: ptr.baseAddress!, count: lapKernel.count)
            }
            conv.setValue(weightVector, forKey: "inputWeights")
            conv.setValue(1.0, forKey: "inputBias")
            ci = conv.outputImage ?? ci
        }
        if let clamp = CIFilter(name: "CIColorClamp") {
            clamp.setValue(ci, forKey: kCIInputImageKey)
            clamp.setValue(CIVector(x: 0, y: 0, z: 0, w: 0.0), forKey: "inputMinComponents")
            clamp.setValue(CIVector(x: 1, y: 1, z: 1, w: 1.0), forKey: "inputMaxComponents")
            ci = clamp.outputImage ?? ci
        }
        // 以原始圖像大小建立輸出，避免因濾鏡造成的無效 extent
        let renderRect = CGRect(x: 0, y: 0, width: gray.width, height: gray.height)
        let ctx = CIContext()
        guard let out = ctx.createCGImage(ci, from: renderRect) else { throw StickerCalibrationError.processingFailed }
        return out
    }
    
    // 更寬鬆的圓形檢測
    private func detectCirclesUsingRelaxedHough(_ grayImage: CGImage, imageSize: CGSize) throws -> [DetectedCircle] {
        let width = grayImage.width
        let height = grayImage.height
        
        guard let data = grayImage.dataProvider?.data,
              let bytes = CFDataGetBytePtr(data) else {
            throw StickerCalibrationError.processingFailed
        }
        
        var circles: [DetectedCircle] = []
        
        // 更寬鬆的半徑範圍
        let minRadius = min(width, height) / 30  // 更小的最小半徑
        let maxRadius = min(width, height) / 3   // 更大的最大半徑
        
        print("寬鬆模式: 半徑範圍 \(minRadius)-\(maxRadius)")
        
        // 更密集的搜索
        for r in stride(from: minRadius, to: maxRadius, by: 2) {
            for y in stride(from: r, to: height - r, by: 6) {
                for x in stride(from: r, to: width - r, by: 6) {
                    let circularity = calculateCircularityAtPosition(bytes, width: width, height: height, centerX: x, centerY: y, radius: r)
                    
                    if circularity > 0.2 { // 非常寬鬆的圓形度門檻
                        let circle = DetectedCircle(
                            center: CGPoint(x: x, y: y),
                            radius: Double(r),
                            confidence: circularity
                        )
                        circles.append(circle)
                    }
                }
            }
        }
        
        return circles.sorted { circle1, circle2 in circle1.confidence > circle2.confidence }
    }
    
    // 處理檢測到的圓形的通用函數
    private func processDetectedCircle(_ bestCircle: DetectedCircle, cgImage: CGImage) throws -> StickerCalibrationResult {
        print("校正貼紙檢測: 選中最佳圓形 - 中心: (\(bestCircle.center.x), \(bestCircle.center.y)), 半徑: \(bestCircle.radius)")
        
        // 分析圓形內部結構（容錯）
        let internalStructure: StickerInternalStructure
        do {
            internalStructure = try analyzeInternalStructure(cgImage, circle: bestCircle)
            print("校正貼紙檢測: 內部結構分析完成")
        } catch {
            print("⚠️ 校正貼紙檢測: 內部結構分析失敗，使用預設結構繼續。錯誤=\(error)")
            let zeroColor = ColorAnalysis(red: 0, green: 0, blue: 0, pixelCount: 0)
            internalStructure = StickerInternalStructure(
                centerGrayColor: zeroColor,
                redPatch: zeroColor,
                greenPatch: zeroColor,
                bluePatch: zeroColor,
                dots3D: [],
                ringInnerRadius: bestCircle.radius * 0.6,
                ringOuterRadius: bestCircle.radius * 0.9
            )
        }
        
        // 🔧 修復校正貼紙面積計算邏輯：統一20mm標準並完善驗證
        let pixelsPerMM = calculatePixelsPerMM(radius: bestCircle.radius, realDiameter: standardStickerDiameter)
        print("校正貼紙檢測: 像素比例 = \(String(format: "%.3f", pixelsPerMM)) pixels/mm")
        
        // 面積一致性驗證：統一標準為20mm直徑 → 3.1416 cm²
        let cmPerPixel = 1.0 / (pixelsPerMM * 10.0)
        let radiusPixels = bestCircle.radius
        let radiusCm = radiusPixels * cmPerPixel
        let calculatedAreaCm2 = Double.pi * radiusCm * radiusCm
        
        let expectedAreaCm2 = 3.1416  // π × (1cm)²，20mm直徑標準
        let areaError = abs(calculatedAreaCm2 - expectedAreaCm2) / expectedAreaCm2
        
        print("📏 校正貼紙面積驗證:")
        print("  - 檢測半徑: \(String(format: "%.1f", radiusPixels)) px = \(String(format: "%.2f", radiusCm)) cm")
        print("  - 計算面積: \(String(format: "%.4f", calculatedAreaCm2)) cm²")
        print("  - 預期面積: \(String(format: "%.4f", expectedAreaCm2)) cm²")
        print("  - 面積誤差: \(String(format: "%.1f", areaError * 100.0))%")
        
        // 面積誤差檢查：放寬至30%容忍度
        if areaError > 0.3 {
            print("⚠️ 校正貼紙面積誤差 \(String(format: "%.1f", areaError * 100.0))% > 30%，可能檢測不準確")
            if areaError > 0.6 {  // 超過60%才拒絕
                print("❌ 面積誤差過大，放棄此次校正結果")
                throw StickerCalibrationError.noStickerFound
            }
        } else {
            print("✅ 校正貼紙面積誤差在可接受範圍內")
        }

        // 合理性檢查與早退（上限稍放寬至 70，>50 發出警告）
        guard pixelsPerMM >= 1.0, pixelsPerMM <= 70.0 else {
            print("⚠️ 校正貼紙檢測: 像素比例超出可接受範圍(>70 px/mm)，放棄此次結果")
            throw StickerCalibrationError.noStickerFound
        }
        if pixelsPerMM > 50.0 {
            print("⚠️ 校正貼紙檢測: 像素比例偏高(>50 px/mm)，請檢查圓形選擇與縮放比例")
        }
        
        // 進行白平衡和色彩校正分析（容錯）
        let colorCalibration: ColorCalibrationData
        do {
            colorCalibration = try analyzeColorCalibration(cgImage, circle: bestCircle, structure: internalStructure)
            print("校正貼紙檢測: 色彩校正分析完成")
        } catch {
            print("⚠️ 校正貼紙檢測: 色彩校正分析失敗，使用預設值繼續。錯誤=\(error)")
            let gains = WhiteBalanceGains(red: 1.0, green: 1.0, blue: 1.0)
            let identity = ColorCorrectionMatrix(
                m11: 1, m12: 0, m13: 0,
                m21: 0, m22: 1, m23: 0,
                m31: 0, m32: 0, m33: 1
            )
            colorCalibration = ColorCalibrationData(whiteBalanceGains: gains, colorCorrectionMatrix: identity, gamma: 1.0)
        }
        
        // 計算整體置信度
        let overallConfidence = calculateOverallConfidence(circle: bestCircle, structure: internalStructure)
        
        print("校正貼紙檢測: 成功完成，整體置信度: \(String(format: "%.3f", overallConfidence))")
        
        return StickerCalibrationResult(
            circle: bestCircle,
            pixelsPerMM: pixelsPerMM,
            internalStructure: internalStructure,
            colorCalibration: colorCalibration,
            confidence: overallConfidence,
            detectionTime: Date()
        )
    }
    
    private func analyzeContourForCircularity(_ path: CGPath, imageSize: CGSize) -> DetectedCircle? {
        let points = path.cgPathPoints()
        guard points.count > 10 else { return nil }

        // 計算輪廓的質心（避免大型表達式超時）
        var sumX: Double = 0
        var sumY: Double = 0
        for p in points {
            sumX += Double(p.x)
            sumY += Double(p.y)
        }
        let invCount = 1.0 / Double(points.count)
        let centerX = sumX * invCount
        let centerY = sumY * invCount
        let center = CGPoint(x: centerX, y: centerY)

        // 計算平均半徑
        var distances: [Double] = []
        distances.reserveCapacity(points.count)
        for p in points {
            let dx = Double(p.x) - centerX
            let dy = Double(p.y) - centerY
            distances.append(hypot(dx, dy))
        }
        var sumR: Double = 0
        for d in distances { sumR += d }
        let avgRadius = sumR * invCount

        // 計算圓形度 (距離變異度越小越圓)
        var varSum: Double = 0
        for d in distances {
            let diff = d - avgRadius
            varSum += diff * diff
        }
        let variance = varSum * invCount
        let circularity = max(0, 1 - (sqrt(variance) / max(1e-6, avgRadius)))
        
        // 在高解析度下放寬上限，但避免極大半徑
        let maxRadiusAllowed = max(50.0, Double(min(imageSize.width, imageSize.height)) / 2.5)
        guard circularity > 0.3, avgRadius > 10, avgRadius < maxRadiusAllowed else {
            return nil
        }
        
        return DetectedCircle(center: center, radius: avgRadius, confidence: circularity)
    }
    
    private func calculateCircularityAtPosition(_ bytes: UnsafePointer<UInt8>, width: Int, height: Int, centerX: Int, centerY: Int, radius: Int) -> Double {
        var edgePixels = 0
        var totalPixels = 0
        
        for angle in stride(from: 0, to: 360, by: 10) {
            let radian = Double(angle) * .pi / 180
            let x = centerX + Int(Double(radius) * cos(radian))
            let y = centerY + Int(Double(radius) * sin(radian))
            
            if x >= 0 && x < width && y >= 0 && y < height {
                let index = y * width + x
                let intensity = bytes[index]
                
                // 檢查是否為邊緣像素 (簡化的邊緣檢測)
                if intensity < 100 { // 假設邊緣為暗色
                    edgePixels += 1
                }
                totalPixels += 1
            }
        }
        
        return totalPixels > 0 ? Double(edgePixels) / Double(totalPixels) : 0
    }
    
    private func selectBestCalibrationCircle(visionCircles: [DetectedCircle], houghCircles: [DetectedCircle], originalImage: CGImage) throws -> DetectedCircle {
        var allCircles = visionCircles + houghCircles
        
        print("🔍 貼紙評分器: 開始分析 \(allCircles.count) 個候選圓形")
        
        // 合併相近的圓形
        allCircles = mergeNearbyCircles(allCircles)
        print("🔍 貼紙評分器: 合併後剩餘 \(allCircles.count) 個候選")
        
        // 幾何合理性過濾：排除超出影像邊界、極端偏心或半徑過小/過大的候選
        let minDimGlobal = Double(min(originalImage.width, originalImage.height))
        let hardMinR = max(8.0, minDimGlobal * 0.015)
        let hardMaxR = minDimGlobal * 0.30
        allCircles = allCircles.filter { c in
            let withinBounds = c.center.x - c.radius >= 0 && c.center.x + c.radius <= Double(originalImage.width) && c.center.y - c.radius >= 0 && c.center.y + c.radius <= Double(originalImage.height)
            let radiusOk = c.radius >= hardMinR && c.radius <= hardMaxR
            return withinBounds && radiusOk
        }
        print("🔍 貼紙評分器: 幾何過濾後剩餘 \(allCircles.count) 個候選")
        
        // 預先計算候選半徑的中位數（自適應尺寸中心）
        let radiiAll = allCircles.map { circle in circle.radius }.sorted()
        let medianRAll: Double = {
            guard !radiiAll.isEmpty else { return 0 }
            if radiiAll.count % 2 == 1 {
                return radiiAll[radiiAll.count/2]
            } else {
                return (radiiAll[radiiAll.count/2 - 1] + radiiAll[radiiAll.count/2]) / 2.0
            }
        }()

        var scoredCircles: [(DetectedCircle, Double, [String: Double])] = [] // 加入詳細評分
        for (index, circle) in allCircles.enumerated() {
            var score: Double = 0.0
            var detailScores: [String: Double] = [:]
            
            // 1. 基礎置信度 (25%)
            let confidenceScore = Double(circle.confidence) * 0.25
            score += confidenceScore
            detailScores["confidence"] = confidenceScore
            
            // 2. 半徑合理性評分 (20%)
            let minDim = Double(min(originalImage.width, originalImage.height))
            let expectedR = minDim * 0.08 // 期望半徑約8%
            let radiusScore = max(0, 1.0 - Swift.abs(circle.radius - expectedR) / expectedR) * 0.20
            score += radiusScore
            detailScores["radius"] = radiusScore
            
            // 3. 尺寸一致性評分 (15%) - 與其他候選的一致性
            let sizeConsistencyScore: Double
            if medianRAll > 0 {
                sizeConsistencyScore = max(0, 1.0 - Swift.abs(circle.radius - medianRAll) / medianRAll) * 0.15
            } else {
                sizeConsistencyScore = 0.0
            }
            score += sizeConsistencyScore
            detailScores["sizeConsistency"] = sizeConsistencyScore
            
            // 4. 位置評分 (15%) - 偏好中心附近
            let cxD = Double(circle.center.x)
            let cyD = Double(circle.center.y)
            let imgW = Double(originalImage.width)
            let imgH = Double(originalImage.height)
            let centerX = imgW / 2.0
            let centerY = imgH / 2.0
            let distFromCenter = sqrt(pow(cxD - centerX, 2) + pow(cyD - centerY, 2))
            let maxDistFromCenter = sqrt(pow(imgW/2.0, 2) + pow(imgH/2.0, 2))
            let positionScore = max(0, 1.0 - distFromCenter / maxDistFromCenter) * 0.15
            score += positionScore
            detailScores["position"] = positionScore
            
            // 5. 圓形度評分 (10%) - 檢查邊緣圓形度
            let circularityScore = calculateCircularityScore(circle: circle, image: originalImage) * 0.10
            score += circularityScore
            detailScores["circularity"] = circularityScore
            
            // 6. 邊緣對比度評分 (10%) - 校正貼紙應有明顯邊界
            let contrastScore = calculateEdgeContrastScore(circle: circle, image: originalImage) * 0.10
            score += contrastScore
            detailScores["contrast"] = contrastScore
            
            // 7. 避免邊界懲罰 (5%) - 太靠近圖像邊界的扣分
            let edgeMargin = circle.radius
            let tooNearEdge = (cxD < edgeMargin) || (cyD < edgeMargin) || 
                             (cxD > imgW - edgeMargin) || (cyD > imgH - edgeMargin)
            let edgePenalty = tooNearEdge ? -0.05 : 0.0
            score += edgePenalty
            detailScores["edgePenalty"] = edgePenalty
            
            scoredCircles.append((circle, score, detailScores))
            
            if AppDebugSettings.isDeveloperMode {
                print("📊 候選 \(index): r=\(String(format: "%.1f", circle.radius)), 總分=\(String(format: "%.3f", score))")
                print("   詳細: conf=\(String(format: "%.3f", detailScores["confidence"] ?? 0)), " +
                      "radius=\(String(format: "%.3f", detailScores["radius"] ?? 0)), " +
                      "pos=\(String(format: "%.3f", detailScores["position"] ?? 0))")
            }
        }
        
        // 排序並選擇最佳候選
        scoredCircles.sort { tuple1, tuple2 in tuple1.1 > tuple2.1 } // 按分數降序
        
        if AppDebugSettings.isDeveloperMode {
            print("🏆 最佳候選前3名:")
            for (index, (circle, score, details)) in scoredCircles.prefix(3).enumerated() {
                print("   \(index + 1). 總分=\(String(format: "%.3f", score)), r=\(String(format: "%.1f", circle.radius))")
                print("      置信度=\(String(format: "%.3f", details["confidence"] ?? 0)), " +
                      "位置=\(String(format: "%.3f", details["position"] ?? 0)), " +
                      "對比度=\(String(format: "%.3f", details["contrast"] ?? 0))")
            }
        }
        
        // 若評分後仍無合適候選，使用保險策略
        guard let best = scoredCircles.first else {
            // 保險策略：取半徑近中位且信心高的候選
            if !radiiAll.isEmpty {
                let medianR = medianRAll
                let filteredCircles = allCircles
                    .filter { circle in Swift.abs(circle.radius - medianR) <= max(10.0, medianR * 0.3) }
                    .sorted(by: { circle1, circle2 in circle1.confidence > circle2.confidence })
                
                if let fallback = filteredCircles.first {
                    print("⚠️ 使用保險策略選擇候選: r=\(String(format: "%.1f", fallback.radius))")
                    return fallback
                }
            }
            throw StickerCalibrationError.noStickerFound
        }
        
        print("✅ 選定最佳校正貼紙: r=\(String(format: "%.1f", best.0.radius)), 最終得分=\(String(format: "%.3f", best.1))")
        
        // 生成開發者模式調試疊圖
        if AppDebugSettings.isDeveloperMode {
            Task { @MainActor in
                self.debugOverlayImage = self.generateDebugOverlay(
                    originalImage: originalImage,
                    allCandidates: allCircles,
                    scoredCandidates: scoredCircles,
                    selectedCircle: best.0
                )
            }
        }
        
        return best.0
    }
    
    // MARK: - 輔助評分函數
    
    /// 計算圓形度評分
    private func calculateCircularityScore(circle: DetectedCircle, image: CGImage) -> Double {
        // 簡化的圓形度檢查 - 采樣邊界點檢查距離變化
        let samples = 16
        let tolerance = circle.radius * 0.15 // 15% 容差
        var validPoints = 0
        
        for i in 0..<samples {
            let angle = Double(i) * 2.0 * Double.pi / Double(samples)
            let expectedX = circle.center.x + cos(angle) * circle.radius
            let expectedY = circle.center.y + sin(angle) * circle.radius
            
            // 檢查是否在圖像邊界內
            if expectedX >= 0 && expectedX < Double(image.width) &&
               expectedY >= 0 && expectedY < Double(image.height) {
                validPoints += 1
            }
        }
        
        return Double(validPoints) / Double(samples)
    }
    
    /// 計算邊緣對比度評分
    private func calculateEdgeContrastScore(circle: DetectedCircle, image: CGImage) -> Double {
        guard let dataProvider = image.dataProvider,
              let pixelData = dataProvider.data,
              let bytes = CFDataGetBytePtr(pixelData) else {
            return 0.5 // 預設中等分數
        }
        
        let width = image.width
        let height = image.height
        let bytesPerPixel = 4 // 假設 RGBA
        
        let samples = 12
        var contrastSum = 0.0
        var validSamples = 0
        
        for i in 0..<samples {
            let angle = Double(i) * 2.0 * Double.pi / Double(samples)
            
            // 內圈點 (半徑 * 0.8)
            let innerX = Int(circle.center.x + cos(angle) * circle.radius * 0.8)
            let innerY = Int(circle.center.y + sin(angle) * circle.radius * 0.8)
            
            // 外圈點 (半徑 * 1.2)
            let outerX = Int(circle.center.x + cos(angle) * circle.radius * 1.2)
            let outerY = Int(circle.center.y + sin(angle) * circle.radius * 1.2)
            
            // 檢查邊界
            if innerX >= 0 && innerX < width && innerY >= 0 && innerY < height &&
               outerX >= 0 && outerX < width && outerY >= 0 && outerY < height {
                
                let innerIndex = (innerY * width + innerX) * bytesPerPixel
                let outerIndex = (outerY * width + outerX) * bytesPerPixel
                
                // 計算灰階值 (簡化為取綠色通道)
                let innerGray = Double(bytes[innerIndex + 1])
                let outerGray = Double(bytes[outerIndex + 1])
                
                let contrast = abs(innerGray - outerGray) / 255.0
                contrastSum += contrast
                validSamples += 1
            }
        }
        
        return validSamples > 0 ? contrastSum / Double(validSamples) : 0.5
    }
    
    // MARK: - 開發者模式調試功能
    
    /// 生成調試疊圖，顯示所有候選圓形和評分信息
    private func generateDebugOverlay(
        originalImage: CGImage,
        allCandidates: [DetectedCircle],
        scoredCandidates: [(DetectedCircle, Double, [String: Double])],
        selectedCircle: DetectedCircle
    ) -> UIImage? {
        let imageSize = CGSize(width: originalImage.width, height: originalImage.height)
        let renderer = UIGraphicsImageRenderer(size: imageSize)
        
        return renderer.image { context in
            let cgContext = context.cgContext
            
            // 繪製原始圖像
            cgContext.draw(originalImage, in: CGRect(origin: .zero, size: imageSize))
            
            // 設定繪圖參數
            cgContext.setLineWidth(2.0)
            cgContext.setTextDrawingMode(.fill)
            
            // 繪製所有候選圓形（灰色）
            cgContext.setStrokeColor(UIColor.gray.withAlphaComponent(0.5).cgColor)
            for candidate in allCandidates {
                let rect = CGRect(
                    x: candidate.center.x - candidate.radius,
                    y: candidate.center.y - candidate.radius,
                    width: candidate.radius * 2,
                    height: candidate.radius * 2
                )
                cgContext.strokeEllipse(in: rect)
            }
            
            // 繪製評分後的候選圓形（根據分數着色）
            for (index, (circle, score, _)) in scoredCandidates.enumerated() {
                if index >= 10 { break } // 只顯示前10個
                
                // 根據分數設定顏色（紅色=低分，綠色=高分）
                let normalizedScore = max(0, min(1, score))
                let color = UIColor(
                    red: 1.0 - normalizedScore,
                    green: normalizedScore,
                    blue: 0.0,
                    alpha: 0.8
                )
                cgContext.setStrokeColor(color.cgColor)
                cgContext.setLineWidth(3.0)
                
                let rect = CGRect(
                    x: circle.center.x - circle.radius,
                    y: circle.center.y - circle.radius,
                    width: circle.radius * 2,
                    height: circle.radius * 2
                )
                cgContext.strokeEllipse(in: rect)
                
                // 添加分數標籤
                let scoreText = String(format: "%.2f", score)
                let textAttributes: [NSAttributedString.Key: Any] = [
                    .font: UIFont.systemFont(ofSize: 14, weight: .bold),
                    .foregroundColor: color
                ]
                let attributedText = NSAttributedString(string: scoreText, attributes: textAttributes)
                let textSize = attributedText.size()
                let textRect = CGRect(
                    x: circle.center.x + circle.radius + 5,
                    y: circle.center.y - textSize.height / 2,
                    width: textSize.width,
                    height: textSize.height
                )
                attributedText.draw(in: textRect)
                
                // 添加排名標籤
                let rankText = "#\(index + 1)"
                let rankAttributes: [NSAttributedString.Key: Any] = [
                    .font: UIFont.systemFont(ofSize: 12, weight: .medium),
                    .foregroundColor: UIColor.black,
                    .backgroundColor: UIColor.white.withAlphaComponent(0.8)
                ]
                let attributedRank = NSAttributedString(string: rankText, attributes: rankAttributes)
                let rankSize = attributedRank.size()
                let rankRect = CGRect(
                    x: circle.center.x - rankSize.width / 2,
                    y: circle.center.y - circle.radius - rankSize.height - 5,
                    width: rankSize.width,
                    height: rankSize.height
                )
                attributedRank.draw(in: rankRect)
            }
            
            // 特別標示最終選擇的圓形（藍色粗線框）
            cgContext.setStrokeColor(UIColor.systemBlue.cgColor)
            cgContext.setLineWidth(5.0)
            let selectedRect = CGRect(
                x: selectedCircle.center.x - selectedCircle.radius,
                y: selectedCircle.center.y - selectedCircle.radius,
                width: selectedCircle.radius * 2,
                height: selectedCircle.radius * 2
            )
            cgContext.strokeEllipse(in: selectedRect)
            
            // 添加 "SELECTED" 標籤
            let selectedText = "✓ SELECTED"
            let selectedAttributes: [NSAttributedString.Key: Any] = [
                .font: UIFont.systemFont(ofSize: 16, weight: .bold),
                .foregroundColor: UIColor.white,
                .backgroundColor: UIColor.systemBlue.withAlphaComponent(0.9)
            ]
            let attributedSelected = NSAttributedString(string: selectedText, attributes: selectedAttributes)
            let selectedSize = attributedSelected.size()
            let selectedTextRect = CGRect(
                x: selectedCircle.center.x - selectedSize.width / 2,
                y: selectedCircle.center.y + selectedCircle.radius + 10,
                width: selectedSize.width,
                height: selectedSize.height
            )
            attributedSelected.draw(in: selectedTextRect)
            
            // 添加圖例
            addDebugLegend(to: cgContext, imageSize: imageSize)
        }
    }
    
    /// 添加調試圖例說明
    private func addDebugLegend(to context: CGContext, imageSize: CGSize) {
        let legendItems = [
            ("候選圓形", UIColor.gray.withAlphaComponent(0.5)),
            ("評分圓形", UIColor.orange),
            ("最終選擇", UIColor.systemBlue)
        ]
        
        let legendY: CGFloat = 20
        let legendX: CGFloat = 20
        let lineHeight: CGFloat = 25
        
        // 繪製半透明背景
        context.setFillColor(UIColor.black.withAlphaComponent(0.7).cgColor)
        let legendBg = CGRect(x: legendX - 5, y: legendY - 5, width: 150, height: CGFloat(legendItems.count) * lineHeight + 10)
        context.fill(legendBg)
        
        for (index, (text, color)) in legendItems.enumerated() {
            let y = legendY + CGFloat(index) * lineHeight
            
            // 繪製顏色指示器
            context.setFillColor(color.cgColor)
            let colorRect = CGRect(x: legendX, y: y, width: 15, height: 15)
            context.fill(colorRect)
            
            // 繪製文字
            let textAttributes: [NSAttributedString.Key: Any] = [
                .font: UIFont.systemFont(ofSize: 12),
                .foregroundColor: UIColor.white
            ]
            let attributedText = NSAttributedString(string: text, attributes: textAttributes)
            let textRect = CGRect(x: legendX + 20, y: y, width: 100, height: 15)
            attributedText.draw(in: textRect)
        }
    }
    
    private func mergeNearbyCircles(_ circles: [DetectedCircle]) -> [DetectedCircle] {
        var merged: [DetectedCircle] = []
        var used = Set<Int>()
        
        for i in 0..<circles.count {
            if used.contains(i) { continue }
            
            var group = [circles[i]]
            used.insert(i)
            
            for j in (i+1)..<circles.count {
                if used.contains(j) { continue }
                
                let distance = sqrt(
                    pow(circles[i].center.x - circles[j].center.x, 2) +
                    pow(circles[i].center.y - circles[j].center.y, 2)
                )
                
                if distance < (circles[i].radius + circles[j].radius) / 2 {
                    group.append(circles[j])
                    used.insert(j)
                }
            }
            
            // 合併群組中的圓形
            if group.count == 1 {
                merged.append(group[0])
            } else {
                let avgX = group.map { circle in circle.center.x }.reduce(0, +) / Double(group.count)
                let avgY = group.map { circle in circle.center.y }.reduce(0, +) / Double(group.count)
                let avgRadius = group.map { circle in circle.radius }.reduce(0, +) / Double(group.count)
                let maxConfidence = group.map { circle in circle.confidence }.max() ?? 0
                
                merged.append(DetectedCircle(
                    center: CGPoint(x: avgX, y: avgY),
                    radius: avgRadius,
                    confidence: maxConfidence
                ))
            }
        }
        
        return merged
    }
    
    private func analyzeInternalStructure(_ cgImage: CGImage, circle: DetectedCircle) throws -> StickerInternalStructure {
        let centerRegionRadius = circle.radius * 0.4  // 平面貼紙中心區域可以更大
        let ringInnerRadius = circle.radius * 0.7     // 調整環形區域
        let ringOuterRadius = circle.radius * 0.95
        
        // 分析中心區域（平面貼紙通常是純色或單一圖案）
        let centerColor = try analyzeRegionColor(cgImage, center: circle.center, radius: centerRegionRadius)
        
        // 平面貼紙可能沒有RGB色塊，但仍然嘗試分析
        let redPatch: ColorAnalysis
        let greenPatch: ColorAnalysis
        let bluePatch: ColorAnalysis
        
        do {
            // 嘗試在不同角度尋找色塊（平面貼紙可能有簡單的色彩區域）
            let tempRedPatch = try analyzeColorPatch(cgImage, circle: circle, angle: 0)
            let tempGreenPatch = try analyzeColorPatch(cgImage, circle: circle, angle: 120)
            let tempBluePatch = try analyzeColorPatch(cgImage, circle: circle, angle: 240)
            
            redPatch = tempRedPatch
            greenPatch = tempGreenPatch
            bluePatch = tempBluePatch
        } catch {
            // 平面貼紙沒有明顯色塊，使用預設值
            let defaultColor = ColorAnalysis(red: centerColor.red, green: centerColor.green, blue: centerColor.blue, pixelCount: 10)
            redPatch = defaultColor
            greenPatch = defaultColor
            bluePatch = defaultColor
        }
        
        // 平面貼紙沒有3D點，返回空陣列
        let dots3D: [CGPoint] = []  // 平面貼紙無凸點
        
        print("🎨 平面貼紙內部結構分析: 中心區域像素數=\(centerColor.pixelCount)")
        
        return StickerInternalStructure(
            centerGrayColor: centerColor,
            redPatch: redPatch,
            greenPatch: greenPatch,
            bluePatch: bluePatch,
            dots3D: dots3D,  // 平面貼紙無凸點
            ringInnerRadius: ringInnerRadius,
            ringOuterRadius: ringOuterRadius
        )
    }
    
    private func analyzeRegionColor(_ cgImage: CGImage, center: CGPoint, radius: Double) throws -> ColorAnalysis {
        guard let data = cgImage.dataProvider?.data,
              let bytes = CFDataGetBytePtr(data) else {
            throw StickerCalibrationError.processingFailed
        }
        
        let width = cgImage.width
        let height = cgImage.height
        let bytesPerPixel = 4
        
        var redSum: Double = 0
        var greenSum: Double = 0
        var blueSum: Double = 0
        var pixelCount = 0
        
        let centerX = Int(center.x)
        let centerY = Int(center.y)
        let radiusInt = Int(radius)
        
        let minY = max(0, centerY - radiusInt)
        let maxY = min(height - 1, centerY + radiusInt)
        let minX = max(0, centerX - radiusInt)
        let maxX = min(width - 1, centerX + radiusInt)
        for y in minY...maxY {
            for x in minX...maxX {
                if x >= 0 && x < width && y >= 0 && y < height {
                    let distance = sqrt(pow(Double(x - centerX), 2) + pow(Double(y - centerY), 2))
                    if distance <= radius {
                        let index = (y * width + x) * bytesPerPixel
                        redSum += Double(bytes[index])
                        greenSum += Double(bytes[index + 1])
                        blueSum += Double(bytes[index + 2])
                        pixelCount += 1
                    }
                }
            }
        }
        
        guard pixelCount > 0 else {
            throw StickerCalibrationError.processingFailed
        }
        
        return ColorAnalysis(
            red: redSum / Double(pixelCount) / 255.0,
            green: greenSum / Double(pixelCount) / 255.0,
            blue: blueSum / Double(pixelCount) / 255.0,
            pixelCount: pixelCount
        )
    }
    
    private func analyzeColorPatch(_ cgImage: CGImage, circle: DetectedCircle, angle: Double) throws -> ColorAnalysis {
        let radian = angle * .pi / 180
        let patchRadius = circle.radius * 0.15
        let patchDistance = circle.radius * 0.7
        
        let patchCenter = CGPoint(
            x: circle.center.x + patchDistance * cos(radian),
            y: circle.center.y + patchDistance * sin(radian)
        )
        
        return try analyzeRegionColor(cgImage, center: patchCenter, radius: patchRadius)
    }
    
    private func detect3DDots(_ cgImage: CGImage, circle: DetectedCircle) throws -> [CGPoint] {
        var dots: [CGPoint] = []
        
        // 檢測3個3D點的大致位置 (0°, 120°, 240°)
        for angle in [0.0, 120.0, 240.0] {
            let radian = angle * .pi / 180
            let dotDistance = circle.radius * 0.8
            let searchCenter = CGPoint(
                x: circle.center.x + dotDistance * cos(radian),
                y: circle.center.y + dotDistance * sin(radian)
            )
            
            if let dot = try findDotNearPosition(cgImage, center: searchCenter, searchRadius: circle.radius * 0.2) {
                dots.append(dot)
            }
        }
        
        return dots
    }
    
    private func findDotNearPosition(_ cgImage: CGImage, center: CGPoint, searchRadius: Double) throws -> CGPoint? {
        // 簡化的點檢測 - 尋找局部暗點
        guard let data = cgImage.dataProvider?.data,
              let bytes = CFDataGetBytePtr(data) else {
            return nil
        }
        
        let width = cgImage.width
        let height = cgImage.height
        let bytesPerPixel = 4
        
        var darkestPoint = center
        var darkestValue: Double = 255
        
        let centerX = Int(center.x)
        let centerY = Int(center.y)
        let radiusInt = Int(searchRadius)
        
        let minY = max(0, centerY - radiusInt)
        let maxY = min(height - 1, centerY + radiusInt)
        let minX = max(0, centerX - radiusInt)
        let maxX = min(width - 1, centerX + radiusInt)
        for y in minY...maxY {
            for x in minX...maxX {
                if x >= 0 && x < width && y >= 0 && y < height {
                    let distance = sqrt(pow(Double(x - centerX), 2) + pow(Double(y - centerY), 2))
                    if distance <= searchRadius {
                        let index = (y * width + x) * bytesPerPixel
                        let gray = (Double(bytes[index]) + Double(bytes[index + 1]) + Double(bytes[index + 2])) / 3.0
                        
                        if gray < darkestValue {
                            darkestValue = gray
                            darkestPoint = CGPoint(x: x, y: y)
                        }
                    }
                }
            }
        }
        
        return darkestValue < 100 ? darkestPoint : nil
    }
    
    private func analyzeColorCalibration(_ cgImage: CGImage, circle: DetectedCircle, structure: StickerInternalStructure) throws -> ColorCalibrationData {
        // 基於檢測到的RGB色塊計算色彩校正參數
        let whiteBalanceGains = calculateWhiteBalanceGains(
            grayReference: structure.centerGrayColor,
            redPatch: structure.redPatch,
            greenPatch: structure.greenPatch,
            bluePatch: structure.bluePatch
        )
        
        let colorMatrix = calculateColorCorrectionMatrix(
            redPatch: structure.redPatch,
            greenPatch: structure.greenPatch,
            bluePatch: structure.bluePatch
        )
        
        return ColorCalibrationData(
            whiteBalanceGains: whiteBalanceGains,
            colorCorrectionMatrix: colorMatrix,
            gamma: calculateGammaCorrection(structure.centerGrayColor)
        )
    }
    
    private func calculatePixelsPerMM(radius: Double, realDiameter: Double) -> Double {
        let pixelDiameter = radius * 2
        return pixelDiameter / realDiameter
    }
    
    private func calculateOverallConfidence(circle: DetectedCircle, structure: StickerInternalStructure) -> Double {
        var confidence = circle.confidence * 0.6  // 平面貼紙更依賴基本圓形檢測
        
        // 平面貼紙的結構元素評分調整
        // 不再要求3D點（平面貼紙無凸點）
        
        // 中心區域品質評分
        if structure.centerGrayColor.pixelCount > 50 {
            confidence += 0.25  // 增加中心區域的權重
        }
        
        // 色彩一致性評分（平面貼紙可能有統一色彩）
        let avgPixelCount = (structure.redPatch.pixelCount + structure.greenPatch.pixelCount + structure.bluePatch.pixelCount) / 3
        if avgPixelCount > 20 {
            confidence += 0.15
        }
        
        return min(1.0, confidence)
    }
    
    // MARK: - 色彩校正計算方法
    
    private func calculateWhiteBalanceGains(grayReference: ColorAnalysis, redPatch: ColorAnalysis, greenPatch: ColorAnalysis, bluePatch: ColorAnalysis) -> WhiteBalanceGains {
        // 使用灰色參考計算白平衡增益
        let targetGray = 0.5 // 目標灰色值
        
        let redGain = targetGray / max(grayReference.red, 0.1)
        let greenGain = targetGray / max(grayReference.green, 0.1)
        let blueGain = targetGray / max(grayReference.blue, 0.1)
        
        return WhiteBalanceGains(red: redGain, green: greenGain, blue: blueGain)
    }
    
    private func calculateColorCorrectionMatrix(redPatch: ColorAnalysis, greenPatch: ColorAnalysis, bluePatch: ColorAnalysis) -> ColorCorrectionMatrix {
        // 簡化的色彩校正矩陣計算
        // 實際應用中會使用更復雜的色彩科學算法
        
        return ColorCorrectionMatrix(
            m11: 1.0, m12: 0.0, m13: 0.0,
            m21: 0.0, m22: 1.0, m23: 0.0,
            m31: 0.0, m32: 0.0, m33: 1.0
        )
    }
    
    private func calculateGammaCorrection(_ grayColor: ColorAnalysis) -> Double {
        // 基於灰色區域計算gamma校正值
        let averageGray = (grayColor.red + grayColor.green + grayColor.blue) / 3.0
        return averageGray > 0 ? log(0.5) / log(averageGray) : 1.0
    }
    
    // MARK: - ROI 信心度評估功能
    
    /// 評估傷口ROI的檢測信心度，決定是否需要手動調整
    func evaluateWoundROIConfidence(woundROI: CGRect, in image: UIImage, withSticker stickerResult: StickerCalibrationResult?) async -> ROIEvaluationResult {
        await MainActor.run {
            calibrationStatus = "評估ROI檢測信心度..."
        }
        
        return await Task.detached { [weak self] in
            guard let self = self else {
                return ROIEvaluationResult(confidence: 0.0, shouldUseManual: true, issues: ["系統錯誤"])
            }
            
            var confidence = 0.0
            var issues: [String] = []
            
            // 1. 檢查ROI基本有效性
            if woundROI.width <= 0 || woundROI.height <= 0 {
                issues.append("ROI區域無效")
                return ROIEvaluationResult(confidence: 0.0, shouldUseManual: true, issues: issues)
            }
            
            // 2. 如果有校準貼紙，進行相對評估
            if let sticker = stickerResult {
                confidence += self.evaluateROIRelativeToSticker(woundROI: woundROI, sticker: sticker, issues: &issues)
            } else {
                // 沒有校準貼紙時的獨立評估
                confidence += self.evaluateROIIndependently(woundROI: woundROI, image: image, issues: &issues)
            }
            
            // 3. 檢查ROI內容品質
            confidence += await self.evaluateROIContent(woundROI: woundROI, in: image, issues: &issues)
            
            // 4. 最終信心度標準化
            confidence = min(1.0, max(0.0, confidence))
            
            let shouldUseManual = confidence < 0.7 // 信心度低於70%建議手動調整
            let finalConfidence = confidence // 捕獲最終信心度值
            
            await MainActor.run {
                self.roiDetectionConfidence = finalConfidence
                self.shouldUseManualROI = shouldUseManual
                
                if shouldUseManual {
                    self.calibrationStatus = "ROI檢測信心度不足，建議手動調整"
                } else {
                    self.calibrationStatus = "ROI檢測信心度良好"
                }
            }
            
            return ROIEvaluationResult(confidence: confidence, shouldUseManual: shouldUseManual, issues: issues)
        }.value
    }
    
    private nonisolated func evaluateROIRelativeToSticker(woundROI: CGRect, sticker: StickerCalibrationResult, issues: inout [String]) -> Double {
        var score = 0.0
        
        // 檢查ROI與貼紙的相對大小
        let roiArea = Double(woundROI.width * woundROI.height)
        let stickerArea = Double.pi * pow(sticker.circle.radius, 2)
        let areaRatio = roiArea / stickerArea
        
        if areaRatio < 0.005 {
            issues.append("ROI區域相對於校準貼紙過小")
            score += 0.1
        } else if areaRatio > 5.0 {
            issues.append("ROI區域相對於校準貼紙過大")
            score += 0.2
        } else {
            score += 0.4 // 合理的大小比例
        }
        
        // 檢查ROI與貼紙的距離
        let roiCenter = CGPoint(x: woundROI.midX, y: woundROI.midY)
        let stickerCenter = CGPoint(
            x: sticker.circle.center.x,
            y: sticker.circle.center.y
        )
        
        let dx = roiCenter.x - stickerCenter.x
        let dy = roiCenter.y - stickerCenter.y
        let distance = sqrt(dx * dx + dy * dy)
        
        let minSafeDistance = sticker.circle.radius * 1.5 // 至少1.5倍半徑的距離
        
        if distance < minSafeDistance {
            issues.append("ROI區域與校準貼紙距離過近，可能影響測量精度")
            score += 0.1
        } else {
            score += 0.3 // 安全距離
        }
        
        return score
    }
    
    private nonisolated func evaluateROIIndependently(woundROI: CGRect, image: UIImage, issues: inout [String]) -> Double {
        var score = 0.0
        
        // 檢查ROI相對圖像的大小比例
        let imageArea = image.size.width * image.size.height
        let roiArea = woundROI.width * woundROI.height
        let ratio = Double(roiArea) / Double(imageArea)
        
        if ratio < 0.001 {
            issues.append("ROI區域過小")
            score += 0.1
        } else if ratio > 0.8 {
            issues.append("ROI區域過大，可能包含過多背景")
            score += 0.2
        } else if ratio >= 0.01 && ratio <= 0.3 {
            score += 0.4 // 理想大小範圍
        } else {
            score += 0.3 // 可接受範圍
        }
        
        // 檢查ROI形狀合理性
        let aspectRatio = woundROI.width / woundROI.height
        if aspectRatio > 0.2 && aspectRatio < 5.0 {
            score += 0.3 // 合理的寬高比
        } else {
            issues.append("ROI形狀異常（寬高比: \(String(format: "%.2f", aspectRatio))）")
            score += 0.1
        }
        
        return score
    }
    
    private nonisolated func evaluateROIContent(woundROI: CGRect, in image: UIImage, issues: inout [String]) async -> Double {
        guard let cgImage = image.cgImage else {
            issues.append("圖像數據無效")
            return 0.0
        }
        
        // 裁切ROI區域
        let scaledROI = CGRect(
            x: woundROI.origin.x * CGFloat(cgImage.width),
            y: woundROI.origin.y * CGFloat(cgImage.height),
            width: woundROI.width * CGFloat(cgImage.width),
            height: woundROI.height * CGFloat(cgImage.height)
        )
        
        guard let croppedImage = cgImage.cropping(to: scaledROI) else {
            issues.append("無法裁切ROI區域")
            return 0.0
        }
        
        var score = 0.0
        
        // 分析ROI內容的色彩特徵
        do {
            let colorAnalysis = try await Task.detached {
                try await self.analyzeRegionColor(croppedImage, center: CGPoint(x: scaledROI.width/2, y: scaledROI.height/2), radius: min(scaledROI.width, scaledROI.height)/2)
            }.value
            
            // 檢查是否有典型的傷口色彩特徵
            let redness = colorAnalysis.red
            let hasWoundColors = redness > 0.3 || // 有紅色成分
                                (colorAnalysis.red > colorAnalysis.green && colorAnalysis.red > colorAnalysis.blue) // 偏紅
            
            if hasWoundColors {
                score += 0.3
            } else {
                issues.append("ROI區域缺乏典型傷口色彩特徵")
                score += 0.1
            }
            
        } catch {
            issues.append("ROI內容分析失敗")
        }
        
        return score
    }
    
    /// 整合LiDAR校準功能
    func integrateWithLiDAR(_ lidarModule: LiDARCalibrationModule) async -> IntegratedCalibrationResult {
        await MainActor.run {
            calibrationStatus = "整合LiDAR和貼紙校準數據..."
        }
        
        return await Task.detached(priority: .background) { [weak self] in
            guard let self = self,
                  let stickerResult = await MainActor.run(body: { self.detectionResult }) else {
                return IntegratedCalibrationResult(
                    isSuccess: false,
                    pixelsPerMM: 0.0,
                    confidence: 0.0,
                    calibrationSource: .failed,
                    error: "校準貼紙檢測失敗"
                )
            }
            
            let lidarPixelsPerMM = await MainActor.run {
                lidarModule.getCalibratedPixelScale(imageSize: CGSize(width: 1920, height: 1080))
            }
            
            // 比較兩種校準方法的結果
            let stickerPixelsPerMM = stickerResult.pixelsPerMM
            let difference = Swift.abs(stickerPixelsPerMM - lidarPixelsPerMM) / max(stickerPixelsPerMM, lidarPixelsPerMM)
            
            var finalPixelsPerMM: Double
            var confidence: Double
            var source: CalibrationSource
            
            if difference < 0.2 { // 兩種方法結果相近
                // 使用加權平均
                let stickerWeight = stickerResult.confidence
                let lidarWeight = await MainActor.run { lidarModule.confidence }
                let totalWeight = stickerWeight + lidarWeight
                
                if totalWeight > 0 {
                    finalPixelsPerMM = (stickerPixelsPerMM * stickerWeight + lidarPixelsPerMM * lidarWeight) / totalWeight
                    confidence = min(1.0, totalWeight / 2.0)
                    source = .combined
                } else {
                    finalPixelsPerMM = stickerPixelsPerMM
                    confidence = stickerResult.confidence
                    source = .sticker
                }
                
            } else {
                // 選擇信心度較高的方法
                let lidarConfidence = await MainActor.run { lidarModule.confidence }
                if stickerResult.confidence > lidarConfidence {
                    finalPixelsPerMM = stickerPixelsPerMM
                    confidence = stickerResult.confidence
                    source = .sticker
                } else {
                    finalPixelsPerMM = lidarPixelsPerMM
                    confidence = lidarConfidence
                    source = .lidar
                }
            }
            
            let sourceDescription = source.description
            await MainActor.run {
                self.calibrationStatus = "校準整合完成 - 來源: \(sourceDescription)"
            }
            
            return IntegratedCalibrationResult(
                isSuccess: true,
                pixelsPerMM: finalPixelsPerMM,
                confidence: confidence,
                calibrationSource: source,
                error: nil
            )
        }.value
    }
}

// MARK: - 開發者設定（在 WoundTypes.swift 中定義）

// MARK: - 方形校正貼紙模組（在 SquareCalibrationModule.swift 中實現）

// MARK: - 資料結構定義

struct StickerInternalStructure {
    let centerGrayColor: ColorAnalysis
    let redPatch: ColorAnalysis
    let greenPatch: ColorAnalysis
    let bluePatch: ColorAnalysis
    let dots3D: [CGPoint]
    let ringInnerRadius: Double
    let ringOuterRadius: Double
}

struct ColorAnalysis {
    let red: Double
    let green: Double
    let blue: Double
    let pixelCount: Int
}

struct ColorCalibrationData {
    let whiteBalanceGains: WhiteBalanceGains
    let colorCorrectionMatrix: ColorCorrectionMatrix
    let gamma: Double
}

struct WhiteBalanceGains {
    let red: Double
    let green: Double
    let blue: Double
}

struct ColorCorrectionMatrix {
    let m11, m12, m13: Double
    let m21, m22, m23: Double
    let m31, m32, m33: Double
}

struct StickerCalibrationResult {
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

// MARK: - ROI 評估結果
struct ROIEvaluationResult {
    let confidence: Double // 0.0 - 1.0
    let shouldUseManual: Bool
    let issues: [String]
    
    var description: String {
        let status = shouldUseManual ? "建議手動調整" : "自動檢測可信"
        return """
        ROI評估結果:
        - 信心度: \(String(format: "%.1f", confidence * 100))%
        - 狀態: \(status)
        - 問題: \(issues.isEmpty ? "無" : issues.joined(separator: ", "))
        """
    }
}

// MARK: - 整合校準結果
struct IntegratedCalibrationResult {
    let isSuccess: Bool
    let pixelsPerMM: Double
    let confidence: Double
    let calibrationSource: CalibrationSource
    let error: String?
    
    var description: String {
        return """
        整合校準結果:
        - 成功: \(isSuccess ? "是" : "否")
        - 像素比例: \(String(format: "%.3f", pixelsPerMM)) pixels/mm
        - 信心度: \(String(format: "%.1f", confidence * 100))%
        - 來源: \(calibrationSource.description)
        \(error != nil ? "- 錯誤: \(error!)" : "")
        """
    }
}

// MARK: - 統一校正檢測方法（移至模組擴充以使用實例狀態）
extension CalibrationStickerModule {
    // 取代 CGPath.points()：安全地收集路徑上的離散點
    fileprivate func dg_addPointHandler(_ points: inout [CGPoint], element: CGPathElement) {
        switch element.type {
        case .moveToPoint:
            points.append(element.points[0])
        case .addLineToPoint:
            points.append(element.points[0])
        case .addQuadCurveToPoint:
            points.append(element.points[1])
        case .addCurveToPoint:
            points.append(element.points[2])
        case .closeSubpath:
            break
        @unknown default:
            break
        }
    }
}

fileprivate extension CGPath {
    func cgPathPoints(maxPointsPerElement: Int = 1) -> [CGPoint] {
        var out: [CGPoint] = []
        self.applyWithBlock { ptr in
            let element = ptr.pointee
            switch element.type {
            case .moveToPoint, .addLineToPoint:
                out.append(element.points[0])
            case .addQuadCurveToPoint:
                out.append(element.points[1])
            case .addCurveToPoint:
                out.append(element.points[2])
            case .closeSubpath:
                break
            @unknown default:
                break
            }
        }
        return out
    }
}

// MARK: - 統一入口擴充（保持與上方類定義同一檔案）
extension CalibrationStickerModule {
    /// 檢測校正貼紙 - 支援圓形和方形貼紙的統一入口
    /// - Parameters:
    ///   - image: 輸入圖像
    ///   - stickerType: 貼紙類型（.automatic 為自動檢測）
    ///   - expectedDiameter: 預期直徑（僅對圓形貼紙有效）
    /// - Returns: 統一的校正結果
    func detectCalibrationStickerUniversal(
        from image: UIImage,
        stickerType: CalibrationStickerModule.StickerType = .automatic,
        expectedDiameter: Double? = nil
    ) async throws -> (circular: StickerCalibrationResult?, square: SquareCalibrationResult?) {
        isDetecting = true
        calibrationStatus = "正在檢測校正貼紙類型..."
        defer { Task { @MainActor in isDetecting = false } }

        var circularResult: StickerCalibrationResult?
        var squareResult: SquareCalibrationResult?

        switch stickerType {
        case .circular:
            circularResult = try await detectCalibrationSticker(from: image, expectedDiameter: expectedDiameter)
        case .square:
            let squareModule = SquareCalibrationModule()
            squareResult = try await squareModule.detectSquareCalibrationSticker(from: image)
            squareCalibrationResult = squareResult
        case .automatic:
            calibrationStatus = "正在嘗試圓形貼紙檢測..."
            do {
                circularResult = try await detectCalibrationSticker(from: image, expectedDiameter: expectedDiameter)
                calibrationStatus = "圓形貼紙檢測成功"
            } catch {
                print("圓形貼紙檢測失敗，嘗試方形貼紙: \(error.localizedDescription)")
                calibrationStatus = "正在嘗試方形貼紙檢測..."
                do {
                    let squareModule = SquareCalibrationModule()
                    squareResult = try await squareModule.detectSquareCalibrationSticker(from: image)
                    squareCalibrationResult = squareResult
                    calibrationStatus = "方形貼紙檢測成功"
                } catch let squareError {
                    calibrationStatus = "校正貼紙檢測失敗"
                    print("方形貼紙檢測也失敗: \(squareError.localizedDescription)")
                    throw StickerCalibrationError.noStickerFound
                }
            }
        }

        return (circular: circularResult, square: squareResult)
    }

    /// 從圓形和方形結果中選擇置信度最高者並回傳像素比例
    func getBestCalibrationResult(
        circular: StickerCalibrationResult?,
        square: SquareCalibrationResult?
    ) -> (pixelsPerMM: Double, confidence: Double, type: String) {
        let circularConfidence = circular?.confidence ?? 0.0
        let squareConfidence = square?.confidence ?? 0.0

        if circularConfidence > squareConfidence {
            return (pixelsPerMM: circular?.pixelsPerMM ?? 1.0, confidence: circularConfidence, type: "圓形貼紙")
        } else if let square = square {
            return (pixelsPerMM: 1.0 / square.cmPerPixel * 10.0, confidence: squareConfidence, type: "方形RGBY貼紙")
        } else {
            return (pixelsPerMM: 1.0, confidence: 0.0, type: "未檢測到")
        }
    }
}

enum CalibrationSource {
    case sticker    // 僅使用校準貼紙
    case lidar      // 僅使用LiDAR
    case combined   // 結合兩種方法
    case failed     // 校準失敗
    
    var description: String {
        switch self {
        case .sticker: return "校準貼紙"
        case .lidar: return "LiDAR"
        case .combined: return "貼紙+LiDAR"
        case .failed: return "失敗"
        }
    }
}

enum StickerCalibrationError: Error, LocalizedError {
    case invalidImage
    case processingFailed
    case noStickerFound
    case insufficientStructure
    
    var errorDescription: String? {
        switch self {
        case .invalidImage:
            return "無效的圖像資料"
        case .processingFailed:
            return "圖像處理失敗"
        case .noStickerFound:
            return "未找到校正貼紙"
        case .insufficientStructure:
            return "校正貼紙結構不完整"
        }
    }
}