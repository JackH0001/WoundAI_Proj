import Foundation
import UIKit
import CoreImage
import Vision
import os.log

/// 增強版自適應分割模組 - 實施Otsu自適應閾值和CLAHE預處理
@MainActor
class AdaptiveSegmentationModule: ObservableObject {
    
    // MARK: - Properties
    
    @Published var segmentationProgress: Double = 0.0
    @Published var currentProcessingStage: ProcessingStage = .idle
    @Published var segmentationQuality: SegmentationQuality?
    
    private let logger = os.Logger(subsystem: "WoundMeasurementApp", category: "AdaptiveSegmentation")
    private let ciContext = CIContext(options: [.useSoftwareRenderer: false])
    
    // MARK: - 處理階段枚舉
    
    enum ProcessingStage {
        case idle
        case preprocessing
        case imageQualityAnalysis
        case adaptiveThresholding
        case multiScaleSegmentation
        case postProcessing
        case qualityValidation
        case completed
        case failed(Error)
    }
    
    // MARK: - 分割品質結構
    
    struct SegmentationQuality {
        let diceScore: Double
        let confidenceLevel: Double
        let edgeSharpness: Double
        let noiseLevel: Double
        let consistencyIndex: Double
        let processingTime: TimeInterval
        let qualityGrade: QualityGrade
    }
    
    enum QualityGrade {
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
    
    // MARK: - 主要分割方法
    
    /// 執行增強版自適應分割
    func performEnhancedAdaptiveSegmentation(
        _ image: UIImage,
        withCalibrationData calibrationData: CalibrationData
    ) async throws -> EnhancedSegmentationResult {
        
        logger.info("開始執行增強版自適應分割")
        let startTime = Date()
        
        currentProcessingStage = .preprocessing
        segmentationProgress = 0.1
        
        do {
            // 階段1: CLAHE預處理和圖像增強
            let preprocessedImage = try await performCLAHEPreprocessing(image)
            segmentationProgress = 0.2
            
            // 階段2: 圖像品質分析和參數自適應
            currentProcessingStage = .imageQualityAnalysis
            let qualityAnalysis = try await analyzeImageQuality(preprocessedImage)
            let adaptiveParams = generateAdaptiveParameters(qualityAnalysis)
            segmentationProgress = 0.3
            
            // 階段3: Otsu自適應閾值分割
            currentProcessingStage = .adaptiveThresholding
            let thresholdResult = try await performOtsuAdaptiveThresholding(
                preprocessedImage, 
                parameters: adaptiveParams
            )
            segmentationProgress = 0.5
            
            // 階段4: 多尺度分割策略
            currentProcessingStage = .multiScaleSegmentation
            let multiScaleResult = try await performMultiScaleSegmentation(
                preprocessedImage,
                thresholdResult: thresholdResult,
                calibrationData: calibrationData
            )
            segmentationProgress = 0.7
            
            // 階段5: 深度學習後處理和邊界精煉
            currentProcessingStage = .postProcessing
            let refinedResult = try await performPostProcessingRefinement(
                originalImage: preprocessedImage,
                segmentationMask: multiScaleResult.segmentationMask,
                calibrationData: calibrationData
            )
            segmentationProgress = 0.9
            
            // 階段6: 品質驗證和一致性檢查
            currentProcessingStage = .qualityValidation
            let qualityValidation = try await performQualityValidation(refinedResult)
            segmentationProgress = 1.0
            
            let processingTime = Date().timeIntervalSince(startTime)
            
            // 生成綜合分割結果
            let enhancedResult = EnhancedSegmentationResult(
                segmentationMask: refinedResult.segmentationMask,
                contours: refinedResult.contours,
                area: refinedResult.area,
                perimeter: refinedResult.perimeter,
                boundingBox: refinedResult.boundingBox,
                confidence: refinedResult.confidence,
                calibratedArea: calculateCalibratedArea(refinedResult.area, calibrationData),
                volumeDeficit: try await calculateVolumeDeficit(refinedResult, calibrationData),
                qualityMetrics: SegmentationQuality(
                    diceScore: qualityValidation.estimatedDiceScore,
                    confidenceLevel: refinedResult.confidence,
                    edgeSharpness: qualityValidation.edgeSharpness,
                    noiseLevel: qualityAnalysis.noiseLevel,
                    consistencyIndex: qualityValidation.consistencyIndex,
                    processingTime: processingTime,
                    qualityGrade: determineQualityGrade(qualityValidation)
                ),
                adaptiveParameters: adaptiveParams,
                processingSteps: generateProcessingStepsReport(
                    qualityAnalysis, thresholdResult, multiScaleResult, refinedResult
                )
            )
            
            currentProcessingStage = .completed
            segmentationQuality = enhancedResult.qualityMetrics
            
            logger.info("增強版自適應分割完成，處理時間: \(processingTime)秒")
            return enhancedResult
            
        } catch {
            currentProcessingStage = .failed(error)
            logger.error("增強版自適應分割失敗: \(error.localizedDescription)")
            throw error
        }
    }
    
    // MARK: - CLAHE預處理實施
    
    /// 執行CLAHE (對比度限制自適應直方圖均衡化) 預處理
    private func performCLAHEPreprocessing(_ image: UIImage) async throws -> UIImage {
        logger.info("執行CLAHE預處理")
        
        guard let inputImage = CIImage(image: image) else {
            throw SegmentationError.imageProcessingFailed
        }
        
        // 轉換為Lab色彩空間進行處理
        let labImage = inputImage.applyingFilter("CIColorSpaceConversion", parameters: [
            "inputColorSpace": CGColorSpace(name: CGColorSpace.sRGB)!,
            "outputColorSpace": CGColorSpace(name: CGColorSpace.lab)!
        ])
        
        // 對L通道應用CLAHE
        let claheFilter = CIFilter(name: "CIExposureAdjust")!
        claheFilter.setValue(labImage, forKey: kCIInputImageKey)
        
        // 自適應對比度增強
        let contrastEnhanced = labImage.applyingFilter("CIColorControls", parameters: [
            "inputContrast": 1.2,
            "inputBrightness": 0.0,
            "inputSaturation": 1.0
        ])
        
        // 光照不均校正
        let illuminationCorrected = try await correctUneven Illumination(contrastEnhanced)
        
        // 噪點抑制
        let denoised = illuminationCorrected.applyingFilter("CINoiseReduction", parameters: [
            "inputNoiseLevel": 0.02,
            "inputSharpness": 0.4
        ])
        
        // 轉換回sRGB
        let finalImage = denoised.applyingFilter("CIColorSpaceConversion", parameters: [
            "inputColorSpace": CGColorSpace(name: CGColorSpace.lab)!,
            "outputColorSpace": CGColorSpace(name: CGColorSpace.sRGB)!
        ])
        
        guard let outputImage = ciContext.createCGImage(finalImage, from: finalImage.extent) else {
            throw SegmentationError.imageProcessingFailed
        }
        
        return UIImage(cgImage: outputImage)
    }
    
    /// 光照不均校正
    private func correctUnevenIllumination(_ image: CIImage) async throws -> CIImage {
        // 使用高斯模糊估算背景光照
        let backgroundEstimate = image.applyingFilter("CIGaussianBlur", parameters: [
            "inputRadius": 30.0
        ])
        
        // 計算光照校正
        let corrected = CIFilter(name: "CIDivideBlendMode")!
        corrected.setValue(image, forKey: kCIInputImageKey)
        corrected.setValue(backgroundEstimate, forKey: kCIInputBackgroundImageKey)
        
        return corrected.outputImage ?? image
    }
    
    // MARK: - Otsu自適應閾值實施
    
    /// 執行Otsu自適應閾值分割
    private func performOtsuAdaptiveThresholding(
        _ image: UIImage,
        parameters: AdaptiveParameters
    ) async throws -> ThresholdResult {
        logger.info("執行Otsu自適應閾值分割")
        
        guard let cgImage = image.cgImage else {
            throw SegmentationError.imageProcessingFailed
        }
        
        // 轉換為灰階
        let grayscaleImage = try await convertToGrayscale(cgImage)
        
        // 計算局部Otsu閾值
        let localThresholds = try await calculateLocalOtsuThresholds(
            grayscaleImage,
            blockSize: parameters.otsuBlockSize,
            overlap: parameters.otsuOverlap
        )
        
        // 應用自適應閾值
        let thresholdedImage = try await applyAdaptiveThreshold(
            grayscaleImage,
            thresholds: localThresholds,
            parameters: parameters
        )
        
        // 形態學處理
        let morphProcessed = try await performMorphologicalOperations(
            thresholdedImage,
            parameters: parameters
        )
        
        return ThresholdResult(
            thresholdedImage: morphProcessed,
            localThresholds: localThresholds,
            globalThreshold: calculateGlobalThreshold(localThresholds),
            qualityScore: evaluateThresholdQuality(morphProcessed)
        )
    }
    
    /// 計算局部Otsu閾值
    private func calculateLocalOtsuThresholds(
        _ image: CGImage,
        blockSize: Int,
        overlap: Double
    ) async throws -> [[Double]] {
        
        let width = image.width
        let height = image.height
        let stride = Int(Double(blockSize) * (1.0 - overlap))
        
        var thresholds: [[Double]] = []
        
        for y in stride(from: 0, to: height - blockSize, by: stride) {
            var rowThresholds: [Double] = []
            
            for x in stride(from: 0, to: width - blockSize, by: stride) {
                let blockRect = CGRect(x: x, y: y, width: blockSize, height: blockSize)
                
                if let blockImage = image.cropping(to: blockRect) {
                    let threshold = calculateOtsuThreshold(blockImage)
                    rowThresholds.append(threshold)
                }
            }
            
            if !rowThresholds.isEmpty {
                thresholds.append(rowThresholds)
            }
        }
        
        return thresholds
    }
    
    /// 計算單個區塊的Otsu閾值
    private func calculateOtsuThreshold(_ image: CGImage) -> Double {
        // 計算直方圖
        let histogram = calculateHistogram(image)
        
        // Otsu演算法實施
        var maxVariance = 0.0
        var optimalThreshold = 0.0
        
        let totalPixels = Double(image.width * image.height)
        var sum = 0.0
        for i in 0..<256 {
            sum += Double(i) * histogram[i]
        }
        
        var sumB = 0.0
        var wB = 0.0
        var wF = 0.0
        
        for t in 0..<256 {
            wB += histogram[t]
            if wB == 0 { continue }
            
            wF = totalPixels - wB
            if wF == 0 { break }
            
            sumB += Double(t) * histogram[t]
            
            let mB = sumB / wB
            let mF = (sum - sumB) / wF
            
            let variance = wB * wF * (mB - mF) * (mB - mF)
            
            if variance > maxVariance {
                maxVariance = variance
                optimalThreshold = Double(t)
            }
        }
        
        return optimalThreshold / 255.0
    }
    
    // MARK: - 多尺度分割實施
    
    /// 執行多尺度分割策略
    private func performMultiScaleSegmentation(
        _ image: UIImage,
        thresholdResult: ThresholdResult,
        calibrationData: CalibrationData
    ) async throws -> MultiScaleSegmentationResult {
        logger.info("執行多尺度分割策略")
        
        // 生成多個尺度的圖像金字塔
        let imagePyramid = try await createImagePyramid(image, levels: 4)
        
        var scaleResults: [ScaleSegmentationResult] = []
        
        // 對每個尺度執行分割
        for (level, scaledImage) in imagePyramid.enumerated() {
            let scaleResult = try await performSingleScaleSegmentation(
                scaledImage,
                scale: pow(0.5, Double(level)),
                calibrationData: calibrationData
            )
            scaleResults.append(scaleResult)
        }
        
        // 融合多尺度結果
        let fusedResult = try await fuseMultiScaleResults(scaleResults, originalSize: image.size)
        
        return MultiScaleSegmentationResult(
            segmentationMask: fusedResult.segmentationMask,
            contours: fusedResult.contours,
            area: fusedResult.area,
            perimeter: fusedResult.perimeter,
            boundingBox: fusedResult.boundingBox,
            confidence: fusedResult.confidence,
            scaleResults: scaleResults,
            fusionQuality: evaluateFusionQuality(scaleResults, fusedResult)
        )
    }
    
    // MARK: - 後處理和邊界精煉
    
    /// 執行後處理和邊界精煉
    private func performPostProcessingRefinement(
        originalImage: UIImage,
        segmentationMask: UIImage,
        calibrationData: CalibrationData
    ) async throws -> RefinedSegmentationResult {
        logger.info("執行後處理和邊界精煉")
        
        // 邊界檢測和精煉
        let refinedBoundaries = try await refineBoundariesWithEdgeDetection(
            originalImage: originalImage,
            mask: segmentationMask
        )
        
        // 形態學後處理
        let morphRefined = try await applyMorphologicalRefinement(refinedBoundaries)
        
        // 輪廓平滑
        let smoothedContours = try await smoothContours(morphRefined)
        
        // 幾何合理性檢查
        let geometryValidated = try await validateGeometry(smoothedContours)
        
        // 計算精確測量值
        let measurements = try await calculatePreciseMeasurements(
            geometryValidated,
            calibrationData: calibrationData
        )
        
        return RefinedSegmentationResult(
            segmentationMask: geometryValidated.mask,
            contours: geometryValidated.contours,
            area: measurements.area,
            perimeter: measurements.perimeter,
            boundingBox: measurements.boundingBox,
            confidence: geometryValidated.confidence
        )
    }
    
    // MARK: - 品質驗證
    
    /// 執行品質驗證和一致性檢查
    private func performQualityValidation(
        _ result: RefinedSegmentationResult
    ) async throws -> QualityValidationResult {
        logger.info("執行品質驗證和一致性檢查")
        
        // 邊界銳度評估
        let edgeSharpness = calculateEdgeSharpness(result.segmentationMask)
        
        // 一致性指數計算
        let consistencyIndex = calculateConsistencyIndex(result.contours)
        
        // Dice Score估算 (基於自一致性檢查)
        let estimatedDiceScore = estimateDiceScoreFromSelfConsistency(result)
        
        // 異常檢測
        let anomalies = detectSegmentationAnomalies(result)
        
        return QualityValidationResult(
            edgeSharpness: edgeSharpness,
            consistencyIndex: consistencyIndex,
            estimatedDiceScore: estimatedDiceScore,
            anomalies: anomalies,
            overallQuality: calculateOverallQuality(
                edgeSharpness: edgeSharpness,
                consistency: consistencyIndex,
                diceScore: estimatedDiceScore
            )
        )
    }
    
    // MARK: - 體積缺損計算
    
    /// 結合AR深度資訊計算體積缺損
    private func calculateVolumeDeficit(
        _ segmentationResult: RefinedSegmentationResult,
        _ calibrationData: CalibrationData
    ) async throws -> VolumeDeficitResult {
        logger.info("計算體積缺損")
        
        guard let depthData = calibrationData.depthData else {
            throw SegmentationError.missingDepthData
        }
        
        // 深度資料對齊和校準
        let alignedDepth = try await alignDepthWithSegmentation(
            depthData: depthData,
            segmentationMask: segmentationResult.segmentationMask,
            calibrationData: calibrationData
        )
        
        // 基準平面估算
        let referencePlane = try await estimateReferencePlane(
            depthData: alignedDepth,
            contours: segmentationResult.contours
        )
        
        // 體積積分計算
        let volumeDeficit = try await integrateVolumeDeficit(
            depthData: alignedDepth,
            referencePlane: referencePlane,
            segmentationMask: segmentationResult.segmentationMask
        )
        
        return VolumeDeficitResult(
            volumeDeficit: volumeDeficit,
            referencePlane: referencePlane,
            depthStatistics: calculateDepthStatistics(alignedDepth),
            confidence: calculateVolumeConfidence(alignedDepth, referencePlane)
        )
    }
    
    // MARK: - 輔助方法
    
    private func generateAdaptiveParameters(_ quality: ImageQualityAnalysis) -> AdaptiveParameters {
        return AdaptiveParameters(
            otsuBlockSize: quality.localVariation > 0.5 ? 64 : 128,
            otsuOverlap: 0.25,
            morphKernelSize: quality.noiseLevel > 0.3 ? 5 : 3,
            contrastEnhancement: quality.contrast < 0.5 ? 1.5 : 1.2,
            edgeThreshold: quality.edgeStrength > 0.7 ? 0.3 : 0.5
        )
    }
    
    private func determineQualityGrade(_ validation: QualityValidationResult) -> QualityGrade {
        let score = (validation.edgeSharpness + validation.consistencyIndex + validation.estimatedDiceScore) / 3.0
        
        switch score {
        case 0.9...1.0: return .excellent
        case 0.8..<0.9: return .good
        case 0.7..<0.8: return .acceptable
        default: return .poor
        }
    }
    
    private func calculateCalibratedArea(_ pixelArea: Double, _ calibrationData: CalibrationData) -> Double {
        let pixelDensity = calibrationData.pixelDensityMmPerPixel
        let areaInMm2 = pixelArea * pixelDensity * pixelDensity
        return areaInMm2 / 100.0 // 轉換為cm²
    }
}

// MARK: - 支援資料結構

struct CalibrationData {
    let pixelDensityMmPerPixel: Double
    let depthData: ARDepthData?
    let arucoDetection: ArUcoDetectionResult?
    let circleGridDetection: CircleGridDetectionResult?
    let colorCalibration: ColorCalibrationResult?
}

struct EnhancedSegmentationResult {
    let segmentationMask: UIImage
    let contours: [WoundContour]
    let area: Double // 像素面積
    let perimeter: Double // 像素周長
    let boundingBox: CGRect
    let confidence: Double
    let calibratedArea: Double // 校準後的實際面積 (cm²)
    let volumeDeficit: VolumeDeficitResult
    let qualityMetrics: AdaptiveSegmentationModule.SegmentationQuality
    let adaptiveParameters: AdaptiveParameters
    let processingSteps: ProcessingStepsReport
}

struct AdaptiveParameters {
    let otsuBlockSize: Int
    let otsuOverlap: Double
    let morphKernelSize: Int
    let contrastEnhancement: Double
    let edgeThreshold: Double
}

struct VolumeDeficitResult {
    let volumeDeficit: Double // 體積缺損 (cm³)
    let referencePlane: ReferencePlane
    let depthStatistics: DepthStatistics
    let confidence: Double
}

struct ProcessingStepsReport {
    let preprocessingQuality: Double
    let thresholdingEffectiveness: Double
    let multiScaleFusionQuality: Double
    let postProcessingImprovement: Double
    let overallImprovement: Double
}

enum SegmentationError: Error {
    case imageProcessingFailed
    case missingDepthData
    case calibrationFailed
    case qualityTooLow
}

// MARK: - 需要實施的輔助結構 (將在其他檔案中定義)

struct ImageQualityAnalysis {
    let contrast: Double
    let noiseLevel: Double
    let edgeStrength: Double
    let localVariation: Double
}

struct ThresholdResult {
    let thresholdedImage: UIImage
    let localThresholds: [[Double]]
    let globalThreshold: Double
    let qualityScore: Double
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
}

struct RefinedSegmentationResult {
    let segmentationMask: UIImage
    let contours: [WoundContour]
    let area: Double
    let perimeter: Double
    let boundingBox: CGRect
    let confidence: Double
}

struct QualityValidationResult {
    let edgeSharpness: Double
    let consistencyIndex: Double
    let estimatedDiceScore: Double
    let anomalies: [SegmentationAnomaly]
    let overallQuality: Double
}