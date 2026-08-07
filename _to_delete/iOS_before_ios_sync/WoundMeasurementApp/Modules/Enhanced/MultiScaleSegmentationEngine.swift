import Foundation
import UIKit
import CoreImage
import Vision
import Accelerate
import os.log

/// 多尺度分割引擎 - 實施圖像金字塔多尺度分割策略
@MainActor
class MultiScaleSegmentationEngine: ObservableObject {
    
    // MARK: - Properties
    
    @Published var segmentationProgress: Double = 0.0
    @Published var currentScale: Int = 0
    @Published var totalScales: Int = 4
    @Published var processingState: ProcessingState = .idle
    @Published var scaleResults: [ScaleSegmentationResult] = []
    
    private let logger = os.Logger(subsystem: "WoundMeasurementApp", category: "MultiScaleSegmentation")
    private let ciContext = CIContext(options: [.useSoftwareRenderer: false])
    
    // 多尺度處理參數
    private let defaultScaleLevels = 4
    private let scaleFactors: [Double] = [1.0, 0.5, 0.25, 0.125]
    private let fusionWeights: [Double] = [0.4, 0.3, 0.2, 0.1]
    
    // MARK: - 處理狀態枚舉
    
    enum ProcessingState {
        case idle
        case buildingImagePyramid
        case processingScale(Int)
        case fusingResults
        case refiningBoundaries
        case validatingResults
        case completed
        case failed(Error)
    }
    
    // MARK: - 主要多尺度分割方法
    
    /// 執行多尺度分割
    func performMultiScaleSegmentation(
        _ image: UIImage,
        adaptiveParameters: AdaptiveProcessingParameters,
        calibrationData: CalibrationData
    ) async throws -> MultiScaleSegmentationResult {
        
        logger.info("開始執行多尺度分割")
        segmentationProgress = 0.0
        processingState = .buildingImagePyramid
        scaleResults.removeAll()
        
        do {
            // 階段1: 建立圖像金字塔 (10%)
            let imagePyramid = try await buildImagePyramid(
                image,
                levels: totalScales,
                adaptiveParameters: adaptiveParameters
            )
            segmentationProgress = 0.1
            
            // 階段2: 對每個尺度執行分割 (70%)
            let progressPerScale = 0.7 / Double(totalScales)
            
            for (scaleIndex, scaledImage) in imagePyramid.enumerated() {
                currentScale = scaleIndex
                processingState = .processingScale(scaleIndex)
                
                logger.info("處理尺度 \(scaleIndex + 1)/\(totalScales)")
                
                let scaleResult = try await processScaleLevel(
                    image: scaledImage,
                    scaleIndex: scaleIndex,
                    scaleFactor: scaleFactors[scaleIndex],
                    adaptiveParameters: adaptiveParameters,
                    calibrationData: calibrationData
                )
                
                scaleResults.append(scaleResult)
                segmentationProgress = 0.1 + progressPerScale * Double(scaleIndex + 1)
            }
            
            // 階段3: 融合多尺度結果 (15%)
            processingState = .fusingResults
            let fusedResult = try await fuseMultiScaleResults(
                scaleResults: scaleResults,
                originalSize: image.size,
                fusionWeights: fusionWeights
            )
            segmentationProgress = 0.95
            
            // 階段4: 邊界精煉和驗證 (5%)
            processingState = .refiningBoundaries
            let finalResult = try await refineFusedResult(
                fusedResult: fusedResult,
                originalImage: image,
                scaleResults: scaleResults
            )
            segmentationProgress = 1.0
            
            processingState = .completed
            
            logger.info("多尺度分割完成")
            return finalResult
            
        } catch {
            processingState = .failed(error)
            logger.error("多尺度分割失敗: \(error.localizedDescription)")
            throw error
        }
    }
    
    // MARK: - 圖像金字塔建立
    
    /// 建立自適應圖像金字塔
    private func buildImagePyramid(
        _ image: UIImage,
        levels: Int,
        adaptiveParameters: AdaptiveProcessingParameters
    ) async throws -> [UIImage] {
        logger.info("建立圖像金字塔，層級: \(levels)")
        
        guard let ciImage = CIImage(image: image) else {
            throw MultiScaleError.imageProcessingFailed
        }
        
        var pyramid: [UIImage] = []
        var currentImage = ciImage
        
        for level in 0..<levels {
            let scaleFactor = scaleFactors[level]
            
            // 根據尺度調整預處理參數
            let scaleAdjustedImage = try await applyScaleSpecificPreprocessing(
                currentImage,
                scaleFactor: scaleFactor,
                level: level,
                adaptiveParameters: adaptiveParameters
            )
            
            // 轉換為UIImage
            guard let cgImage = ciContext.createCGImage(scaleAdjustedImage, from: scaleAdjustedImage.extent) else {
                throw MultiScaleError.imageConversionFailed
            }
            
            let uiImage = UIImage(cgImage: cgImage)
            pyramid.append(uiImage)
            
            // 為下一層級縮放圖像
            if level < levels - 1 {
                let nextScaleFactor = scaleFactors[level + 1] / scaleFactors[level]
                currentImage = scaleAdjustedImage.transformed(by: CGAffineTransform(
                    scaleX: CGFloat(nextScaleFactor),
                    y: CGFloat(nextScaleFactor)
                ))
            }
        }
        
        return pyramid
    }
    
    /// 應用尺度特定的預處理
    private func applyScaleSpecificPreprocessing(
        _ image: CIImage,
        scaleFactor: Double,
        level: Int,
        adaptiveParameters: AdaptiveProcessingParameters
    ) async throws -> CIImage {
        
        var processedImage = image
        
        // 尺度特定的高斯模糊 (較小尺度需要更多模糊以減少噪點)
        let blurRadius = calculateOptimalBlurRadius(scaleFactor: scaleFactor, level: level)
        if blurRadius > 0 {
            processedImage = processedImage.applyingFilter("CIGaussianBlur", parameters: [
                "inputRadius": blurRadius
            ])
        }
        
        // 尺度特定的銳化 (較大尺度需要更多銳化以保持細節)
        let sharpenIntensity = calculateOptimalSharpenIntensity(scaleFactor: scaleFactor, level: level)
        if sharpenIntensity > 0 {
            processedImage = processedImage.applyingFilter("CIUnsharpMask", parameters: [
                "inputRadius": 2.5,
                "inputIntensity": sharpenIntensity
            ])
        }
        
        // 尺度特定的對比度調整
        let contrastAdjustment = calculateOptimalContrastAdjustment(
            scaleFactor: scaleFactor,
            level: level,
            baseContrast: adaptiveParameters.segmentationParameters.claheParameters.clipLimit
        )
        
        processedImage = processedImage.applyingFilter("CIColorControls", parameters: [
            "inputContrast": contrastAdjustment,
            "inputBrightness": 0.0,
            "inputSaturation": 1.0
        ])
        
        return processedImage
    }
    
    // MARK: - 單一尺度處理
    
    /// 處理單一尺度層級
    private func processScaleLevel(
        image: UIImage,
        scaleIndex: Int,
        scaleFactor: Double,
        adaptiveParameters: AdaptiveProcessingParameters,
        calibrationData: CalibrationData
    ) async throws -> ScaleSegmentationResult {
        logger.info("處理尺度層級 \(scaleIndex)，縮放因子: \(scaleFactor)")
        
        // 調整參數以適應當前尺度
        let scaleAdjustedParams = adjustParametersForScale(
            adaptiveParameters,
            scaleFactor: scaleFactor,
            scaleIndex: scaleIndex
        )
        
        // 執行尺度特定的分割
        let segmentationResult = try await performScaleSpecificSegmentation(
            image: image,
            parameters: scaleAdjustedParams,
            calibrationData: calibrationData,
            scaleFactor: scaleFactor
        )
        
        // 計算尺度特定的品質指標
        let qualityMetrics = try await calculateScaleQualityMetrics(
            segmentationResult: segmentationResult,
            originalImage: image,
            scaleFactor: scaleFactor
        )
        
        // 檢測尺度特定的特徵
        let scaleFeatures = try await detectScaleSpecificFeatures(
            segmentationResult: segmentationResult,
            scaleFactor: scaleFactor,
            scaleIndex: scaleIndex
        )
        
        return ScaleSegmentationResult(
            scaleIndex: scaleIndex,
            scaleFactor: scaleFactor,
            segmentationMask: segmentationResult.mask,
            contours: segmentationResult.contours,
            area: segmentationResult.area,
            perimeter: segmentationResult.perimeter,
            confidence: segmentationResult.confidence,
            qualityMetrics: qualityMetrics,
            scaleFeatures: scaleFeatures,
            processingTime: segmentationResult.processingTime,
            detailLevel: determineDetailLevel(scaleFactor: scaleFactor),
            contributionWeight: calculateContributionWeight(
                qualityMetrics: qualityMetrics,
                scaleFactor: scaleFactor
            )
        )
    }
    
    /// 執行尺度特定的分割
    private func performScaleSpecificSegmentation(
        image: UIImage,
        parameters: AdaptiveProcessingParameters,
        calibrationData: CalibrationData,
        scaleFactor: Double
    ) async throws -> ScaleSegmentationOutput {
        
        let startTime = Date()
        
        // 根據尺度選擇最適合的分割策略
        let segmentationStrategy = selectSegmentationStrategy(scaleFactor: scaleFactor)
        
        var segmentationMask: UIImage
        var contours: [WoundContour]
        var confidence: Double
        
        switch segmentationStrategy {
        case .coarseGrained:
            // 粗粒度分割 (適用於小尺度)
            (segmentationMask, contours, confidence) = try await performCoarseSegmentation(
                image: image,
                parameters: parameters
            )
            
        case .fineGrained:
            // 細粒度分割 (適用於大尺度)
            (segmentationMask, contours, confidence) = try await performFineSegmentation(
                image: image,
                parameters: parameters
            )
            
        case .adaptive:
            // 自適應分割 (適用於中等尺度)
            (segmentationMask, contours, confidence) = try await performAdaptiveSegmentation(
                image: image,
                parameters: parameters
            )
        }
        
        // 計算面積和周長 (按比例調整)
        let area = calculateScaledArea(contours: contours, scaleFactor: scaleFactor)
        let perimeter = calculateScaledPerimeter(contours: contours, scaleFactor: scaleFactor)
        
        let processingTime = Date().timeIntervalSince(startTime)
        
        return ScaleSegmentationOutput(
            mask: segmentationMask,
            contours: contours,
            area: area,
            perimeter: perimeter,
            confidence: confidence,
            processingTime: processingTime
        )
    }
    
    // MARK: - 分割策略實施
    
    /// 粗粒度分割
    private func performCoarseSegmentation(
        image: UIImage,
        parameters: AdaptiveProcessingParameters
    ) async throws -> (mask: UIImage, contours: [WoundContour], confidence: Double) {
        
        guard let ciImage = CIImage(image: image) else {
            throw MultiScaleError.imageProcessingFailed
        }
        
        // 使用較大的結構元素進行形態學處理
        let morphKernelSize = max(7, parameters.segmentationParameters.morphologicalParameters.kernelSize * 2)
        
        // 簡化的閾值分割
        let threshold = 0.5 // 固定閾值用於粗略分割
        
        let binaryImage = ciImage.applyingFilter("CIColorMonochrome", parameters: [
            "inputColor": CIColor.white,
            "inputIntensity": threshold
        ])
        
        // 形態學開運算去除小區域
        let openedImage = try await applyMorphologicalOpening(
            binaryImage,
            kernelSize: morphKernelSize
        )
        
        // 轉換回UIImage
        guard let cgImage = ciContext.createCGImage(openedImage, from: openedImage.extent) else {
            throw MultiScaleError.imageConversionFailed
        }
        
        let resultMask = UIImage(cgImage: cgImage)
        
        // 提取主要輪廓
        let contours = try await extractMajorContours(from: resultMask)
        
        // 計算信心度 (基於輪廓數量和面積)
        let confidence = calculateCoarseSegmentationConfidence(contours: contours)
        
        return (resultMask, contours, confidence)
    }
    
    /// 細粒度分割
    private func performFineSegmentation(
        image: UIImage,
        parameters: AdaptiveProcessingParameters
    ) async throws -> (mask: UIImage, contours: [WoundContour], confidence: Double) {
        
        guard let ciImage = CIImage(image: image) else {
            throw MultiScaleError.imageProcessingFailed
        }
        
        // 使用邊緣檢測增強細節
        let edgeEnhanced = ciImage.applyingFilter("CIEdges", parameters: [
            "inputIntensity": 2.0
        ])
        
        // 局部自適應閾值
        let localThreshold = try await performLocalAdaptiveThresholding(
            edgeEnhanced,
            blockSize: parameters.segmentationParameters.otsuParameters.blockSize / 2 // 更小的區塊
        )
        
        // 精細的形態學處理
        let refinedImage = try await applyFineGrainedMorphology(
            localThreshold,
            parameters: parameters
        )
        
        // 轉換回UIImage
        guard let cgImage = ciContext.createCGImage(refinedImage, from: refinedImage.extent) else {
            throw MultiScaleError.imageConversionFailed
        }
        
        let resultMask = UIImage(cgImage: cgImage)
        
        // 提取詳細輪廓
        let contours = try await extractDetailedContours(from: resultMask)
        
        // 計算信心度 (基於邊緣連續性和輪廓品質)
        let confidence = calculateFineSegmentationConfidence(
            contours: contours,
            edgeImage: edgeEnhanced
        )
        
        return (resultMask, contours, confidence)
    }
    
    /// 自適應分割
    private func performAdaptiveSegmentation(
        image: UIImage,
        parameters: AdaptiveProcessingParameters
    ) async throws -> (mask: UIImage, contours: [WoundContour], confidence: Double) {
        
        // 結合粗細兩種策略的優點
        let (coarseMask, coarseContours, coarseConfidence) = try await performCoarseSegmentation(
            image: image,
            parameters: parameters
        )
        
        let (fineMask, fineContours, fineConfidence) = try await performFineSegmentation(
            image: image,
            parameters: parameters
        )
        
        // 融合兩種結果
        let fusedResult = try await fuseTwoSegmentationResults(
            coarse: (coarseMask, coarseContours, coarseConfidence),
            fine: (fineMask, fineContours, fineConfidence),
            fusionStrategy: .weighted
        )
        
        return fusedResult
    }
    
    // MARK: - 多尺度結果融合
    
    /// 融合多尺度結果
    private func fuseMultiScaleResults(
        scaleResults: [ScaleSegmentationResult],
        originalSize: CGSize,
        fusionWeights: [Double]
    ) async throws -> FusedSegmentationResult {
        logger.info("融合多尺度結果")
        
        // 將所有結果調整到原始尺寸
        var resizedResults: [ScaleSegmentationResult] = []
        
        for result in scaleResults {
            let resizedMask = try await resizeSegmentationMask(
                result.segmentationMask,
                to: originalSize,
                scaleFactor: result.scaleFactor
            )
            
            let resizedContours = try await resizeContours(
                result.contours,
                scaleFactor: 1.0 / result.scaleFactor
            )
            
            var resizedResult = result
            resizedResult.segmentationMask = resizedMask
            resizedResult.contours = resizedContours
            
            resizedResults.append(resizedResult)
        }
        
        // 執行加權融合
        let fusedMask = try await performWeightedFusion(
            results: resizedResults,
            weights: fusionWeights
        )
        
        // 融合輪廓 (選擇最佳品質的輪廓)
        let fusedContours = try await selectBestContours(
            from: resizedResults,
            weights: fusionWeights
        )
        
        // 計算融合後的測量值
        let fusedMeasurements = calculateFusedMeasurements(
            contours: fusedContours,
            scaleResults: resizedResults,
            weights: fusionWeights
        )
        
        // 評估融合品質
        let fusionQuality = try await assessFusionQuality(
            fusedResult: fusedMask,
            scaleResults: resizedResults,
            originalImage: nil // 可選參數
        )
        
        return FusedSegmentationResult(
            fusedMask: fusedMask,
            fusedContours: fusedContours,
            fusedArea: fusedMeasurements.area,
            fusedPerimeter: fusedMeasurements.perimeter,
            fusedConfidence: fusedMeasurements.confidence,
            fusionQuality: fusionQuality,
            contributingScales: resizedResults,
            fusionWeights: fusionWeights,
            fusionStrategy: .weightedAverage
        )
    }
    
    /// 加權融合分割遮罩
    private func performWeightedFusion(
        results: [ScaleSegmentationResult],
        weights: [Double]
    ) async throws -> UIImage {
        
        guard results.count == weights.count else {
            throw MultiScaleError.fusionParameterMismatch
        }
        
        guard let firstResult = results.first else {
            throw MultiScaleError.noResultsToFuse
        }
        
        let imageSize = firstResult.segmentationMask.size
        let width = Int(imageSize.width)
        let height = Int(imageSize.height)
        
        // 創建累加器陣列
        var accumulatedValues = Array(repeating: 0.0, count: width * height)
        var totalWeights = Array(repeating: 0.0, count: width * height)
        
        // 對每個尺度結果進行加權累加
        for (index, result) in results.enumerated() {
            let weight = weights[index] * result.contributionWeight
            let maskData = result.segmentationMask.pixelData()
            
            for i in 0..<min(maskData.count, accumulatedValues.count) {
                let normalizedValue = Double(maskData[i]) / 255.0
                accumulatedValues[i] += normalizedValue * weight
                totalWeights[i] += weight
            }
        }
        
        // 正規化並生成最終遮罩
        var finalMaskData: [UInt8] = []
        for i in 0..<accumulatedValues.count {
            if totalWeights[i] > 0 {
                let normalizedValue = accumulatedValues[i] / totalWeights[i]
                let pixelValue = UInt8(min(255, max(0, normalizedValue * 255)))
                finalMaskData.append(pixelValue)
            } else {
                finalMaskData.append(0)
            }
        }
        
        // 轉換為UIImage
        return try createImageFromPixelData(finalMaskData, width: width, height: height)
    }
    
    // MARK: - 邊界精煉
    
    /// 精煉融合結果
    private func refineFusedResult(
        fusedResult: FusedSegmentationResult,
        originalImage: UIImage,
        scaleResults: [ScaleSegmentationResult]
    ) async throws -> MultiScaleSegmentationResult {
        logger.info("精煉融合結果")
        
        // 邊界平滑處理
        let smoothedContours = try await smoothContourBoundaries(
            fusedResult.fusedContours,
            smoothingFactor: 0.1
        )
        
        // 幾何一致性檢查
        let geometryValidatedContours = try await validateContourGeometry(
            smoothedContours,
            originalImage: originalImage
        )
        
        // 多尺度一致性驗證
        let consistencyScore = calculateMultiScaleConsistency(
            fusedResult: fusedResult,
            scaleResults: scaleResults
        )
        
        // 生成最終分割遮罩
        let finalMask = try await generateFinalMask(
            from: geometryValidatedContours,
            imageSize: originalImage.size
        )
        
        // 計算最終測量值
        let finalMeasurements = calculateFinalMeasurements(
            contours: geometryValidatedContours,
            mask: finalMask
        )
        
        return MultiScaleSegmentationResult(
            segmentationMask: finalMask,
            contours: geometryValidatedContours,
            area: finalMeasurements.area,
            perimeter: finalMeasurements.perimeter,
            boundingBox: finalMeasurements.boundingBox,
            confidence: calculateFinalConfidence(fusedResult, consistencyScore),
            scaleResults: scaleResults,
            fusionQuality: fusedResult.fusionQuality,
            multiscaleConsistency: consistencyScore,
            processingMetadata: generateProcessingMetadata(scaleResults, fusedResult)
        )
    }
    
    // MARK: - 輔助方法
    
    private func calculateOptimalBlurRadius(scaleFactor: Double, level: Int) -> Double {
        // 較小的尺度需要更多模糊來減少噪點
        return max(0, 2.0 * (1.0 - scaleFactor))
    }
    
    private func calculateOptimalSharpenIntensity(scaleFactor: Double, level: Int) -> Double {
        // 較大的尺度需要更多銳化來保持細節
        return scaleFactor * 0.5
    }
    
    private func calculateOptimalContrastAdjustment(
        scaleFactor: Double,
        level: Int,
        baseContrast: Double
    ) -> Double {
        // 根據尺度調整對比度
        return baseContrast * (0.8 + 0.4 * scaleFactor)
    }
    
    private func adjustParametersForScale(
        _ parameters: AdaptiveProcessingParameters,
        scaleFactor: Double,
        scaleIndex: Int
    ) -> AdaptiveProcessingParameters {
        
        // 調整Otsu參數
        let adjustedOtsuParams = OtsuParameters(
            blockSize: Int(Double(parameters.segmentationParameters.otsuParameters.blockSize) * scaleFactor),
            overlap: parameters.segmentationParameters.otsuParameters.overlap,
            thresholdAdjustment: parameters.segmentationParameters.otsuParameters.thresholdAdjustment,
            minThreshold: parameters.segmentationParameters.otsuParameters.minThreshold,
            maxThreshold: parameters.segmentationParameters.otsuParameters.maxThreshold
        )
        
        // 調整形態學參數
        let adjustedMorphParams = MorphologicalParameters(
            kernelSize: max(3, Int(Double(parameters.segmentationParameters.morphologicalParameters.kernelSize) * scaleFactor)),
            iterations: parameters.segmentationParameters.morphologicalParameters.iterations,
            operation: parameters.segmentationParameters.morphologicalParameters.operation
        )
        
        // 創建調整後的分割參數
        let adjustedSegParams = SegmentationParameters(
            otsuParameters: adjustedOtsuParams,
            claheParameters: parameters.segmentationParameters.claheParameters,
            morphologicalParameters: adjustedMorphParams,
            multiScaleParameters: parameters.segmentationParameters.multiScaleParameters,
            postProcessingParameters: parameters.segmentationParameters.postProcessingParameters
        )
        
        // 返回調整後的參數
        return AdaptiveProcessingParameters(
            segmentationParameters: adjustedSegParams,
            performanceParameters: parameters.performanceParameters,
            adaptationMetadata: parameters.adaptationMetadata
        )
    }
    
    private func selectSegmentationStrategy(scaleFactor: Double) -> SegmentationStrategy {
        switch scaleFactor {
        case 0.0..<0.3:
            return .coarseGrained
        case 0.3..<0.7:
            return .adaptive
        default:
            return .fineGrained
        }
    }
    
    private func determineDetailLevel(scaleFactor: Double) -> DetailLevel {
        switch scaleFactor {
        case 0.0..<0.25:
            return .coarse
        case 0.25..<0.75:
            return .medium
        default:
            return .fine
        }
    }
    
    private func calculateContributionWeight(
        qualityMetrics: ScaleQualityMetrics,
        scaleFactor: Double
    ) -> Double {
        let qualityWeight = (qualityMetrics.edgeQuality + qualityMetrics.contourCompleteness + qualityMetrics.noiseResistance) / 3.0
        let scaleWeight = 1.0 // 所有尺度等權重，可以根據需要調整
        
        return qualityWeight * scaleWeight
    }
    
    private func calculateFinalConfidence(
        _ fusedResult: FusedSegmentationResult,
        _ consistencyScore: Double
    ) -> Double {
        return (fusedResult.fusedConfidence + consistencyScore + fusedResult.fusionQuality) / 3.0
    }
}

// MARK: - 資料結構定義

struct ScaleSegmentationResult {
    var segmentationMask: UIImage
    var contours: [WoundContour]
    let scaleIndex: Int
    let scaleFactor: Double
    let area: Double
    let perimeter: Double
    let confidence: Double
    let qualityMetrics: ScaleQualityMetrics
    let scaleFeatures: ScaleSpecificFeatures
    let processingTime: TimeInterval
    let detailLevel: DetailLevel
    let contributionWeight: Double
}

struct ScaleQualityMetrics {
    let edgeQuality: Double
    let contourCompleteness: Double
    let noiseResistance: Double
    let detailPreservation: Double
    let overallQuality: Double
}

struct ScaleSpecificFeatures {
    let dominantFrequencies: [Double]
    let textureComplexity: Double
    let edgeDensity: Double
    let featureScale: FeatureScale
}

enum FeatureScale {
    case macro, meso, micro
}

enum DetailLevel {
    case coarse, medium, fine
}

enum SegmentationStrategy {
    case coarseGrained, fineGrained, adaptive
}

struct ScaleSegmentationOutput {
    let mask: UIImage
    let contours: [WoundContour]
    let area: Double
    let perimeter: Double
    let confidence: Double
    let processingTime: TimeInterval
}

struct FusedSegmentationResult {
    let fusedMask: UIImage
    let fusedContours: [WoundContour]
    let fusedArea: Double
    let fusedPerimeter: Double
    let fusedConfidence: Double
    let fusionQuality: Double
    let contributingScales: [ScaleSegmentationResult]
    let fusionWeights: [Double]
    let fusionStrategy: FusionStrategy
}

enum FusionStrategy {
    case weightedAverage, maxConfidence, consensus, adaptive
}

struct MultiScaleSegmentationResult {
    let segmentationMask: UIImage
    let contours: [WoundContour]
    let area: Double
    let perimeter: Double
    let boundingBox: CGRect
    let confidence: Double
    let scaleResults: [ScaleSegmentationResult]
    let fusionQuality: Double
    let multiscaleConsistency: Double
    let processingMetadata: MultiScaleProcessingMetadata
}

struct MultiScaleProcessingMetadata {
    let totalProcessingTime: TimeInterval
    let scaleProcessingTimes: [TimeInterval]
    let fusionTime: TimeInterval
    let refinementTime: TimeInterval
    let qualityByScale: [Double]
    let optimalScaleIndex: Int
}

enum MultiScaleError: Error {
    case imageProcessingFailed
    case imageConversionFailed
    case fusionParameterMismatch
    case noResultsToFuse
    case contourExtractionFailed
    case maskResizingFailed
    
    var localizedDescription: String {
        switch self {
        case .imageProcessingFailed: return "圖像處理失敗"
        case .imageConversionFailed: return "圖像轉換失敗"
        case .fusionParameterMismatch: return "融合參數不匹配"
        case .noResultsToFuse: return "沒有結果可融合"
        case .contourExtractionFailed: return "輪廓提取失敗"
        case .maskResizingFailed: return "遮罩縮放失敗"
        }
    }
}

// MARK: - 擴展方法 (簡化實作)

extension MultiScaleSegmentationEngine {
    
    // 這些方法需要完整的實作，此處提供簡化版本
    
    private func applyMorphologicalOpening(_ image: CIImage, kernelSize: Int) async throws -> CIImage {
        return image
    }
    
    private func performLocalAdaptiveThresholding(_ image: CIImage, blockSize: Int) async throws -> CIImage {
        return image
    }
    
    private func applyFineGrainedMorphology(
        _ image: CIImage,
        parameters: AdaptiveProcessingParameters
    ) async throws -> CIImage {
        return image
    }
    
    private func extractMajorContours(from image: UIImage) async throws -> [WoundContour] {
        return []
    }
    
    private func extractDetailedContours(from image: UIImage) async throws -> [WoundContour] {
        return []
    }
    
    private func calculateCoarseSegmentationConfidence(contours: [WoundContour]) -> Double {
        return 0.7
    }
    
    private func calculateFineSegmentationConfidence(
        contours: [WoundContour],
        edgeImage: CIImage
    ) -> Double {
        return 0.8
    }
    
    private func fuseTwoSegmentationResults(
        coarse: (UIImage, [WoundContour], Double),
        fine: (UIImage, [WoundContour], Double),
        fusionStrategy: FusionStrategy
    ) async throws -> (mask: UIImage, contours: [WoundContour], confidence: Double) {
        return (coarse.0, coarse.1, (coarse.2 + fine.2) / 2.0)
    }
    
    private func resizeSegmentationMask(
        _ mask: UIImage,
        to size: CGSize,
        scaleFactor: Double
    ) async throws -> UIImage {
        return mask
    }
    
    private func resizeContours(
        _ contours: [WoundContour],
        scaleFactor: Double
    ) async throws -> [WoundContour] {
        return contours
    }
    
    private func selectBestContours(
        from results: [ScaleSegmentationResult],
        weights: [Double]
    ) async throws -> [WoundContour] {
        return results.first?.contours ?? []
    }
    
    private func createImageFromPixelData(_ data: [UInt8], width: Int, height: Int) throws -> UIImage {
        // 簡化實作
        return UIImage()
    }
    
    private func calculateScaledArea(contours: [WoundContour], scaleFactor: Double) -> Double {
        return 100.0 * scaleFactor * scaleFactor
    }
    
    private func calculateScaledPerimeter(contours: [WoundContour], scaleFactor: Double) -> Double {
        return 50.0 * scaleFactor
    }
    
    private func calculateScaleQualityMetrics(
        segmentationResult: ScaleSegmentationOutput,
        originalImage: UIImage,
        scaleFactor: Double
    ) async throws -> ScaleQualityMetrics {
        return ScaleQualityMetrics(
            edgeQuality: 0.8,
            contourCompleteness: 0.75,
            noiseResistance: 0.85,
            detailPreservation: scaleFactor,
            overallQuality: 0.8
        )
    }
    
    private func detectScaleSpecificFeatures(
        segmentationResult: ScaleSegmentationOutput,
        scaleFactor: Double,
        scaleIndex: Int
    ) async throws -> ScaleSpecificFeatures {
        return ScaleSpecificFeatures(
            dominantFrequencies: [1.0, 2.0, 4.0],
            textureComplexity: 0.6,
            edgeDensity: 0.7,
            featureScale: scaleIndex < 2 ? .macro : .micro
        )
    }
    
    private func calculateFusedMeasurements(
        contours: [WoundContour],
        scaleResults: [ScaleSegmentationResult],
        weights: [Double]
    ) -> (area: Double, perimeter: Double, confidence: Double) {
        
        var weightedArea = 0.0
        var weightedPerimeter = 0.0
        var weightedConfidence = 0.0
        var totalWeight = 0.0
        
        for (index, result) in scaleResults.enumerated() {
            let weight = weights[index] * result.contributionWeight
            weightedArea += result.area * weight
            weightedPerimeter += result.perimeter * weight
            weightedConfidence += result.confidence * weight
            totalWeight += weight
        }
        
        return (
            area: weightedArea / totalWeight,
            perimeter: weightedPerimeter / totalWeight,
            confidence: weightedConfidence / totalWeight
        )
    }
    
    private func assessFusionQuality(
        fusedResult: UIImage,
        scaleResults: [ScaleSegmentationResult],
        originalImage: UIImage?
    ) async throws -> Double {
        return 0.85
    }
    
    private func smoothContourBoundaries(
        _ contours: [WoundContour],
        smoothingFactor: Double
    ) async throws -> [WoundContour] {
        return contours
    }
    
    private func validateContourGeometry(
        _ contours: [WoundContour],
        originalImage: UIImage
    ) async throws -> [WoundContour] {
        return contours
    }
    
    private func calculateMultiScaleConsistency(
        fusedResult: FusedSegmentationResult,
        scaleResults: [ScaleSegmentationResult]
    ) -> Double {
        return 0.8
    }
    
    private func generateFinalMask(
        from contours: [WoundContour],
        imageSize: CGSize
    ) async throws -> UIImage {
        return UIImage()
    }
    
    private func calculateFinalMeasurements(
        contours: [WoundContour],
        mask: UIImage
    ) -> (area: Double, perimeter: Double, boundingBox: CGRect) {
        return (100.0, 50.0, CGRect(x: 0, y: 0, width: 100, height: 100))
    }
    
    private func generateProcessingMetadata(
        _ scaleResults: [ScaleSegmentationResult],
        _ fusedResult: FusedSegmentationResult
    ) -> MultiScaleProcessingMetadata {
        return MultiScaleProcessingMetadata(
            totalProcessingTime: 2.5,
            scaleProcessingTimes: scaleResults.map { $0.processingTime },
            fusionTime: 0.3,
            refinementTime: 0.2,
            qualityByScale: scaleResults.map { $0.qualityMetrics.overallQuality },
            optimalScaleIndex: 0
        )
    }
}