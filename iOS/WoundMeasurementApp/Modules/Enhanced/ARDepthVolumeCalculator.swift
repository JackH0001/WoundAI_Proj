import Foundation
import UIKit
import ARKit
import CoreImage
import simd
import os.log

/// AR深度體積計算器 - 整合LiDAR深度資訊計算傷口體積缺損
@MainActor
class ARDepthVolumeCalculator: ObservableObject {
    
    // MARK: - Properties
    
    @Published var calculationProgress: Double = 0.0
    @Published var calculationState: CalculationState = .idle
    @Published var currentVolumeResult: VolumeCalculationResult?
    @Published var depthCalibrationData: DepthCalibrationData?
    
    private let logger = os.Logger(subsystem: "WoundMeasurementApp", category: "ARDepthVolume")
    private let session = ARSession()
    private var isSessionRunning = false
    
    // 深度處理參數
    private let depthConfidenceThreshold: Float = 0.7
    private let maxDepthRange: Float = 2.0 // 最大深度範圍 (米)
    private let minDepthRange: Float = 0.1  // 最小深度範圍 (米)
    
    // MARK: - 計算狀態枚舉
    
    enum CalculationState {
        case idle
        case initializingARSession
        case capturingDepthData
        case aligningDepthWithRGB
        case calibratingDepthScale
        case segmentingWoundRegion
        case estimatingReferencePlane
        case calculatingVolumeDeficit
        case validatingResults
        case completed
        case failed(Error)
    }
    
    // MARK: - 初始化和AR會話管理
    
    init() {
        setupARConfiguration()
    }
    
    deinit {
        stopARSession()
    }
    
    /// 設置AR配置
    private func setupARConfiguration() {
        guard ARWorldTrackingConfiguration.supportsSceneReconstruction(.meshWithClassification) else {
            logger.error("設備不支援場景重建")
            return
        }
        
        let configuration = ARWorldTrackingConfiguration()
        configuration.sceneReconstruction = .meshWithClassification
        configuration.environmentTexturing = .automatic
        
        if ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth) {
            configuration.frameSemantics.insert(.sceneDepth)
        }
    }
    
    /// 啟動AR會話
    func startARSession() async throws {
        guard !isSessionRunning else { return }
        
        let configuration = ARWorldTrackingConfiguration()
        configuration.frameSemantics = [.sceneDepth, .smoothedSceneDepth]
        configuration.environmentTexturing = .automatic
        
        session.run(configuration)
        isSessionRunning = true
        
        logger.info("AR會話已啟動")
    }
    
    /// 停止AR會話
    func stopARSession() {
        guard isSessionRunning else { return }
        
        session.pause()
        isSessionRunning = false
        
        logger.info("AR會話已停止")
    }
    
    // MARK: - 主要體積計算方法
    
    /// 計算傷口體積缺損
    func calculateWoundVolumeDeficit(
        segmentationResult: EnhancedSegmentationResult,
        calibrationData: CalibrationData,
        arFrame: ARFrame? = nil
    ) async throws -> VolumeCalculationResult {
        
        logger.info("開始計算傷口體積缺損")
        calculationProgress = 0.0
        calculationState = .capturingDepthData
        
        do {
            // 階段1: 獲取和驗證深度資料 (15%)
            let depthData = try await acquireDepthData(arFrame: arFrame)
            calculationProgress = 0.15
            
            // 階段2: 深度資料與RGB圖像對齊 (15%)
            calculationState = .aligningDepthWithRGB
            let alignedDepth = try await alignDepthWithRGB(
                depthData: depthData,
                segmentationResult: segmentationResult,
                calibrationData: calibrationData
            )
            calculationProgress = 0.3
            
            // 階段3: 深度刻度校準 (15%)
            calculationState = .calibratingDepthScale
            let calibratedDepth = try await calibrateDepthScale(
                alignedDepth: alignedDepth,
                calibrationData: calibrationData
            )
            calculationProgress = 0.45
            
            // 階段4: 傷口區域深度分割 (15%)
            calculationState = .segmentingWoundRegion
            let woundDepthMask = try await segmentWoundDepthRegion(
                calibratedDepth: calibratedDepth,
                segmentationMask: segmentationResult.segmentationMask,
                contours: segmentationResult.contours
            )
            calculationProgress = 0.6
            
            // 階段5: 估算參考平面 (15%)
            calculationState = .estimatingReferencePlane
            let referencePlane = try await estimateReferencePlane(
                depthData: calibratedDepth,
                woundMask: woundDepthMask,
                contours: segmentationResult.contours
            )
            calculationProgress = 0.75
            
            // 階段6: 計算體積缺損 (15%)
            calculationState = .calculatingVolumeDeficit
            let volumeDeficit = try await integrateVolumeDeficit(
                depthData: calibratedDepth,
                referencePlane: referencePlane,
                woundMask: woundDepthMask,
                pixelDensity: calibrationData.pixelDensityMmPerPixel
            )
            calculationProgress = 0.9
            
            // 階段7: 結果驗證和後處理 (10%)
            calculationState = .validatingResults
            let validatedResult = try await validateVolumeCalculation(
                volumeDeficit: volumeDeficit,
                referencePlane: referencePlane,
                segmentationResult: segmentationResult,
                depthQuality: assessDepthDataQuality(calibratedDepth)
            )
            calculationProgress = 1.0
            
            currentVolumeResult = validatedResult
            calculationState = .completed
            
            logger.info("傷口體積缺損計算完成")
            return validatedResult
            
        } catch {
            calculationState = .failed(error)
            logger.error("體積計算失敗: \(error.localizedDescription)")
            throw error
        }
    }
    
    // MARK: - 深度資料獲取和處理
    
    /// 獲取深度資料
    private func acquireDepthData(arFrame: ARFrame?) async throws -> ARDepthData {
        if let frame = arFrame, let sceneDepth = frame.sceneDepth {
            return ARDepthData(
                depthMap: sceneDepth.depthMap,
                confidenceMap: sceneDepth.confidenceMap,
                cameraIntrinsics: frame.camera.intrinsics,
                cameraTransform: frame.camera.transform
            )
        }
        
        // 如果沒有提供ARFrame，嘗試從當前AR會話獲取
        if !isSessionRunning {
            try await startARSession()
        }
        
        // 等待並獲取深度資料
        return try await withCheckedThrowingContinuation { continuation in
            let delegate = DepthCaptureDelegate { result in
                continuation.resume(with: result)
            }
            session.delegate = delegate
            
            // 設置超時
            Task {
                try await Task.sleep(nanoseconds: 5_000_000_000) // 5秒超時
                continuation.resume(throwing: VolumeCalculationError.depthDataTimeout)
            }
        }
    }
    
    /// 深度資料與RGB圖像對齊
    private func alignDepthWithRGB(
        depthData: ARDepthData,
        segmentationResult: EnhancedSegmentationResult,
        calibrationData: CalibrationData
    ) async throws -> AlignedDepthData {
        logger.info("對齊深度資料與RGB圖像")
        
        let depthMap = depthData.depthMap
        let confidenceMap = depthData.confidenceMap
        
        // 獲取深度資料的內參和變換矩陣
        let depthIntrinsics = depthData.cameraIntrinsics
        let depthTransform = depthData.cameraTransform
        
        // 計算從深度空間到RGB空間的變換
        let rgbToDepthTransform = try calculateRGBToDepthTransform(
            depthIntrinsics: depthIntrinsics,
            depthTransform: depthTransform,
            calibrationData: calibrationData
        )
        
        // 對齊深度圖像到RGB圖像
        let alignedDepthMap = try applyDepthAlignment(
            depthMap: depthMap,
            confidenceMap: confidenceMap,
            transform: rgbToDepthTransform,
            targetSize: segmentationResult.segmentationMask.size
        )
        
        // 過濾低信心度的深度資料
        let filteredDepthMap = filterDepthByConfidence(
            depthMap: alignedDepthMap.depthMap,
            confidenceMap: alignedDepthMap.confidenceMap,
            threshold: depthConfidenceThreshold
        )
        
        return AlignedDepthData(
            depthMap: filteredDepthMap,
            confidenceMap: alignedDepthMap.confidenceMap,
            alignmentTransform: rgbToDepthTransform,
            alignmentQuality: assessAlignmentQuality(filteredDepthMap, confidenceMap)
        )
    }
    
    /// 深度刻度校準
    private func calibrateDepthScale(
        alignedDepth: AlignedDepthData,
        calibrationData: CalibrationData
    ) async throws -> CalibratedDepthData {
        logger.info("校準深度刻度")
        
        // 使用校正貼紙的已知深度進行校準
        var depthScale: Float = 1.0
        var depthOffset: Float = 0.0
        
        if let arUcoResult = calibrationData.arucoDetection {
            // 使用ArUco貼紙的四個角點進行深度校準
            let cornerDepths = extractDepthAtPoints(
                alignedDepth.depthMap,
                points: arUcoResult.corners
            )
            
            // 假設校正貼紙是平面的，計算平面方程
            let (scale, offset) = try calibrateDepthUsingPlanarReference(
                observedDepths: cornerDepths,
                referencePoints: arUcoResult.corners,
                knownPlanarDistance: 0.0 // 假設貼紙在同一平面
            )
            
            depthScale = scale
            depthOffset = offset
        }
        
        if let circleGridResult = calibrationData.circleGridDetection {
            // 使用圓形網格進行輔助校準
            let gridDepth = extractDepthAtPoints(
                alignedDepth.depthMap,
                points: [circleGridResult.outerCircle.center]
            )
            
            // 交叉驗證深度校準
            if !gridDepth.isEmpty {
                let crossValidationScale = validateDepthCalibration(
                    observedDepth: gridDepth[0],
                    expectedDepth: depthOffset
                )
                
                // 加權平均校準參數
                depthScale = (depthScale + crossValidationScale) / 2.0
            }
        }
        
        // 應用深度校準
        let calibratedDepthMap = applyDepthCalibration(
            alignedDepth.depthMap,
            scale: depthScale,
            offset: depthOffset
        )
        
        return CalibratedDepthData(
            depthMap: calibratedDepthMap,
            confidenceMap: alignedDepth.confidenceMap,
            depthScale: depthScale,
            depthOffset: depthOffset,
            calibrationAccuracy: calculateCalibrationAccuracy(depthScale, depthOffset)
        )
    }
    
    // MARK: - 傷口深度區域分割
    
    /// 分割傷口深度區域
    private func segmentWoundDepthRegion(
        calibratedDepth: CalibratedDepthData,
        segmentationMask: UIImage,
        contours: [WoundContour]
    ) async throws -> WoundDepthMask {
        logger.info("分割傷口深度區域")
        
        // 將分割遮罩轉換為深度遮罩
        guard let depthMask = createDepthMaskFromSegmentation(
            segmentationMask: segmentationMask,
            depthMapSize: calibratedDepth.depthMap.size
        ) else {
            throw VolumeCalculationError.maskCreationFailed
        }
        
        // 在傷口區域內進行深度分析
        let woundDepthStatistics = calculateWoundDepthStatistics(
            depthMap: calibratedDepth.depthMap,
            mask: depthMask,
            confidenceMap: calibratedDepth.confidenceMap
        )
        
        // 使用深度信息精煉分割邊界
        let refinedMask = try await refineSegmentationUsingDepth(
            originalMask: depthMask,
            depthMap: calibratedDepth.depthMap,
            depthStatistics: woundDepthStatistics
        )
        
        return WoundDepthMask(
            mask: refinedMask,
            depthStatistics: woundDepthStatistics,
            contours: contours,
            maskQuality: assessMaskQuality(refinedMask, woundDepthStatistics)
        )
    }
    
    // MARK: - 參考平面估算
    
    /// 估算參考平面 (健康皮膚的基準面)
    private func estimateReferencePlane(
        depthData: CalibratedDepthData,
        woundMask: WoundDepthMask,
        contours: [WoundContour]
    ) async throws -> ReferencePlane {
        logger.info("估算參考平面")
        
        // 方法1: 使用傷口邊界周圍的健康組織
        let boundaryRegion = createBoundaryRegion(
            contours: contours,
            expansionRadius: 20.0 // 像素
        )
        
        let boundaryDepthPoints = extractDepthPoints(
            depthMap: depthData.depthMap,
            region: boundaryRegion,
            confidenceMap: depthData.confidenceMap,
            confidenceThreshold: depthConfidenceThreshold
        )
        
        // 方法2: 使用RANSAC進行robust平面擬合
        let ransacPlane = try performRANSACPlaneFitting(
            points: boundaryDepthPoints,
            maxIterations: 1000,
            distanceThreshold: 2.0 // mm
        )
        
        // 方法3: 使用最小二乘法進行精確擬合
        let leastSquaresPlane = performLeastSquaresPlaneFitting(
            points: boundaryDepthPoints
        )
        
        // 選擇最佳平面 (基於inlier數量和擬合誤差)
        let finalPlane = selectBestPlane([ransacPlane, leastSquaresPlane])
        
        // 驗證平面的合理性
        let planeValidation = validateReferencePlane(
            plane: finalPlane,
            woundContours: contours,
            depthStatistics: woundMask.depthStatistics
        )
        
        return ReferencePlane(
            planeEquation: finalPlane,
            confidence: planeValidation.confidence,
            inlierPoints: boundaryDepthPoints.filter { point in
                calculatePointToPlaneDistance(point, finalPlane) < 2.0
            },
            planeNormal: calculatePlaneNormal(finalPlane),
            centerPoint: calculatePlaneCenterPoint(finalPlane, contours)
        )
    }
    
    // MARK: - 體積積分計算
    
    /// 積分計算體積缺損
    private func integrateVolumeDeficit(
        depthData: CalibratedDepthData,
        referencePlane: ReferencePlane,
        woundMask: WoundDepthMask,
        pixelDensity: Double
    ) async throws -> VolumeDeficitMeasurement {
        logger.info("積分計算體積缺損")
        
        var totalVolume: Double = 0.0
        var validPixelCount = 0
        let pixelAreaMm2 = pow(1.0 / pixelDensity, 2) // 每像素面積 (mm²)
        
        // 遍歷傷口區域內的每個像素
        let maskData = woundMask.mask.pixelData()
        let depthMapData = depthData.depthMap.pixelData()
        let confidenceData = depthData.confidenceMap.pixelData()
        
        let width = Int(depthData.depthMap.size.width)
        let height = Int(depthData.depthMap.size.height)
        
        for y in 0..<height {
            for x in 0..<width {
                let index = y * width + x
                
                // 檢查是否在傷口區域內
                guard index < maskData.count && maskData[index] > 0 else { continue }
                
                // 檢查深度資料的可信度
                guard index < confidenceData.count,
                      confidenceData[index] >= depthConfidenceThreshold else { continue }
                
                // 獲取實際深度值
                guard index < depthMapData.count else { continue }
                let actualDepth = Double(depthMapData[index]) * 1000.0 // 轉換為mm
                
                // 計算該點在參考平面上的預期深度
                let worldPoint = pixelToWorldCoordinate(x: x, y: y, pixelDensity: pixelDensity)
                let expectedDepth = calculateDepthOnPlane(
                    point: worldPoint,
                    plane: referencePlane.planeEquation
                )
                
                // 計算深度差異 (體積缺損)
                let depthDeficit = max(0, expectedDepth - actualDepth)
                
                // 計算該像素的體積貢獻
                let pixelVolume = depthDeficit * pixelAreaMm2 // mm³
                totalVolume += pixelVolume
                validPixelCount += 1
            }
        }
        
        // 轉換單位為cm³
        let volumeCm3 = totalVolume / 1000.0
        
        // 計算統計數據
        let volumeStatistics = calculateVolumeStatistics(
            depthData: depthData,
            referencePlane: referencePlane,
            woundMask: woundMask
        )
        
        return VolumeDeficitMeasurement(
            totalVolumeCm3: volumeCm3,
            validPixelCount: validPixelCount,
            averageDepthDeficitMm: volumeStatistics.averageDeficit,
            maxDepthDeficitMm: volumeStatistics.maxDeficit,
            depthDeficitStdDev: volumeStatistics.stdDeviation,
            measurementAccuracy: calculateMeasurementAccuracy(volumeStatistics, referencePlane),
            volumeDistribution: calculateVolumeDistribution(
                depthData: depthData,
                referencePlane: referencePlane,
                woundMask: woundMask
            )
        )
    }
    
    // MARK: - 結果驗證
    
    /// 驗證體積計算結果
    private func validateVolumeCalculation(
        volumeDeficit: VolumeDeficitMeasurement,
        referencePlane: ReferencePlane,
        segmentationResult: EnhancedSegmentationResult,
        depthQuality: DepthQuality
    ) async throws -> VolumeCalculationResult {
        logger.info("驗證體積計算結果")
        
        // 合理性檢查
        let reasonabilityCheck = performReasonabilityCheck(
            volumeDeficit: volumeDeficit,
            woundArea: segmentationResult.calibratedArea
        )
        
        // 一致性檢查
        let consistencyCheck = performConsistencyCheck(
            volumeDeficit: volumeDeficit,
            referencePlane: referencePlane,
            depthQuality: depthQuality
        )
        
        // 精確度評估
        let accuracyAssessment = assessMeasurementAccuracy(
            volumeDeficit: volumeDeficit,
            depthQuality: depthQuality,
            planeConfidence: referencePlane.confidence
        )
        
        // 不確定性估算
        let uncertaintyEstimate = calculateMeasurementUncertainty(
            volumeDeficit: volumeDeficit,
            depthQuality: depthQuality,
            segmentationQuality: segmentationResult.qualityMetrics
        )
        
        // 生成詳細報告
        let detailedAnalysis = generateDetailedVolumeAnalysis(
            volumeDeficit: volumeDeficit,
            referencePlane: referencePlane,
            segmentationResult: segmentationResult,
            depthQuality: depthQuality
        )
        
        return VolumeCalculationResult(
            volumeDeficit: volumeDeficit,
            referencePlane: referencePlane,
            depthQuality: depthQuality,
            measurementAccuracy: accuracyAssessment,
            uncertaintyEstimate: uncertaintyEstimate,
            reasonabilityCheck: reasonabilityCheck,
            consistencyCheck: consistencyCheck,
            detailedAnalysis: detailedAnalysis,
            calculationTimestamp: Date(),
            validationStatus: determineValidationStatus(
                reasonabilityCheck, consistencyCheck, accuracyAssessment
            )
        )
    }
    
    // MARK: - 輔助方法
    
    private func calculateRGBToDepthTransform(
        depthIntrinsics: simd_float3x3,
        depthTransform: simd_float4x4,
        calibrationData: CalibrationData
    ) throws -> simd_float4x4 {
        // 簡化版本的變換矩陣計算
        return depthTransform
    }
    
    private func extractDepthAtPoints(
        _ depthMap: DepthMap,
        points: [CGPoint]
    ) -> [Float] {
        return points.compactMap { point in
            depthMap.depthValue(at: point)
        }
    }
    
    private func pixelToWorldCoordinate(x: Int, y: Int, pixelDensity: Double) -> simd_float3 {
        let worldX = Float(Double(x) / pixelDensity)
        let worldY = Float(Double(y) / pixelDensity)
        return simd_float3(worldX, worldY, 0)
    }
    
    private func calculateDepthOnPlane(point: simd_float3, plane: PlaneEquation) -> Double {
        // 計算點在平面上的深度
        let distance = plane.a * point.x + plane.b * point.y + plane.c * point.z + plane.d
        return Double(distance)
    }
    
    private func determineValidationStatus(
        _ reasonability: ReasonabilityCheck,
        _ consistency: ConsistencyCheck,
        _ accuracy: AccuracyAssessment
    ) -> ValidationStatus {
        
        let scores = [reasonability.score, consistency.score, accuracy.overallAccuracy]
        let averageScore = scores.reduce(0, +) / Double(scores.count)
        
        switch averageScore {
        case 0.9...1.0: return .excellent
        case 0.8..<0.9: return .good
        case 0.7..<0.8: return .acceptable
        default: return .needsImprovement
        }
    }
}

// MARK: - 資料結構定義

struct ARDepthData {
    let depthMap: CVPixelBuffer
    let confidenceMap: CVPixelBuffer?
    let cameraIntrinsics: simd_float3x3
    let cameraTransform: simd_float4x4
}

struct AlignedDepthData {
    let depthMap: DepthMap
    let confidenceMap: ConfidenceMap
    let alignmentTransform: simd_float4x4
    let alignmentQuality: Double
}

struct CalibratedDepthData {
    let depthMap: DepthMap
    let confidenceMap: ConfidenceMap
    let depthScale: Float
    let depthOffset: Float
    let calibrationAccuracy: Double
}

struct WoundDepthMask {
    let mask: BinaryMask
    let depthStatistics: WoundDepthStatistics
    let contours: [WoundContour]
    let maskQuality: Double
}

struct ReferencePlane {
    let planeEquation: PlaneEquation
    let confidence: Double
    let inlierPoints: [simd_float3]
    let planeNormal: simd_float3
    let centerPoint: simd_float3
}

struct PlaneEquation {
    let a: Float
    let b: Float
    let c: Float
    let d: Float
}

struct VolumeDeficitMeasurement {
    let totalVolumeCm3: Double
    let validPixelCount: Int
    let averageDepthDeficitMm: Double
    let maxDepthDeficitMm: Double
    let depthDeficitStdDev: Double
    let measurementAccuracy: Double
    let volumeDistribution: VolumeDistribution
}

struct VolumeCalculationResult {
    let volumeDeficit: VolumeDeficitMeasurement
    let referencePlane: ReferencePlane
    let depthQuality: DepthQuality
    let measurementAccuracy: AccuracyAssessment
    let uncertaintyEstimate: UncertaintyEstimate
    let reasonabilityCheck: ReasonabilityCheck
    let consistencyCheck: ConsistencyCheck
    let detailedAnalysis: DetailedVolumeAnalysis
    let calculationTimestamp: Date
    let validationStatus: ValidationStatus
}

struct DepthQuality {
    let averageConfidence: Double
    let depthCoverage: Double
    let noiseLevel: Double
    let calibrationAccuracy: Double
}

struct AccuracyAssessment {
    let overallAccuracy: Double
    let depthAccuracy: Double
    let segmentationAccuracy: Double
    let calibrationAccuracy: Double
}

struct UncertaintyEstimate {
    let volumeUncertaintyPercent: Double
    let depthUncertaintyMm: Double
    let confidenceInterval95: (lower: Double, upper: Double)
}

struct ReasonabilityCheck {
    let score: Double
    let volumeToAreaRatio: Double
    let isReasonable: Bool
    let issues: [String]
}

struct ConsistencyCheck {
    let score: Double
    let spatialConsistency: Double
    let temporalConsistency: Double
    let crossValidationScore: Double
}

struct DetailedVolumeAnalysis {
    let volumeByRegion: [VolumeRegion]
    let depthProfile: DepthProfile
    let healingIndicators: HealingIndicators
    let recommendedActions: [String]
}

struct VolumeDistribution {
    let shallowRegionPercent: Double  // 0-2mm
    let moderateRegionPercent: Double // 2-5mm
    let deepRegionPercent: Double     // >5mm
}

enum ValidationStatus {
    case excellent, good, acceptable, needsImprovement
    
    var description: String {
        switch self {
        case .excellent: return "優秀"
        case .good: return "良好"
        case .acceptable: return "可接受"
        case .needsImprovement: return "需要改善"
        }
    }
}

// MARK: - 輔助類型定義

typealias DepthMap = UIImage
typealias ConfidenceMap = UIImage
typealias BinaryMask = UIImage

struct WoundDepthStatistics {
    let averageDepth: Double
    let medianDepth: Double
    let stdDeviation: Double
    let minDepth: Double
    let maxDepth: Double
}

struct VolumeRegion {
    let region: CGRect
    let volumeCm3: Double
    let averageDepthMm: Double
    let severity: WoundSeverity
}

enum WoundSeverity {
    case shallow, moderate, deep, critical
}

struct DepthProfile {
    let crossSections: [CrossSection]
    let contourLines: [ContourLine]
    let gradientMap: UIImage
}

struct CrossSection {
    let direction: CrossSectionDirection
    let depthValues: [Double]
    let positions: [CGPoint]
}

enum CrossSectionDirection {
    case horizontal, vertical, diagonal
}

struct ContourLine {
    let depthLevel: Double
    let contour: [CGPoint]
}

struct HealingIndicators {
    let tissueViability: Double
    let healingProgression: Double
    let riskFactors: [RiskFactor]
}

struct RiskFactor {
    let type: RiskType
    let severity: Double
    let recommendation: String
}

enum RiskType {
    case infection, poorCirculation, tissueNecrosis
}

// MARK: - 深度捕獲代理

class DepthCaptureDelegate: NSObject, ARSessionDelegate {
    private let completion: (Result<ARDepthData, Error>) -> Void
    private var hasCompleted = false
    
    init(completion: @escaping (Result<ARDepthData, Error>) -> Void) {
        self.completion = completion
    }
    
    func session(_ session: ARSession, didUpdate frame: ARFrame) {
        guard !hasCompleted else { return }
        
        if let sceneDepth = frame.sceneDepth {
            hasCompleted = true
            let depthData = ARDepthData(
                depthMap: sceneDepth.depthMap,
                confidenceMap: sceneDepth.confidenceMap,
                cameraIntrinsics: frame.camera.intrinsics,
                cameraTransform: frame.camera.transform
            )
            completion(.success(depthData))
        }
    }
    
    func session(_ session: ARSession, didFailWithError error: Error) {
        guard !hasCompleted else { return }
        hasCompleted = true
        completion(.failure(error))
    }
}

enum VolumeCalculationError: Error {
    case depthDataTimeout
    case depthDataUnavailable
    case alignmentFailed
    case calibrationFailed
    case maskCreationFailed
    case planeFittingFailed
    case volumeIntegrationFailed
    case validationFailed
    
    var localizedDescription: String {
        switch self {
        case .depthDataTimeout: return "深度資料獲取超時"
        case .depthDataUnavailable: return "深度資料不可用"
        case .alignmentFailed: return "深度對齊失敗"
        case .calibrationFailed: return "深度校準失敗"
        case .maskCreationFailed: return "遮罩建立失敗"
        case .planeFittingFailed: return "平面擬合失敗"
        case .volumeIntegrationFailed: return "體積積分失敗"
        case .validationFailed: return "結果驗證失敗"
        }
    }
}

// MARK: - 擴展方法 (簡化實作)

extension ARDepthVolumeCalculator {
    
    // 這些方法需要完整的實作，此處提供簡化版本
    
    private func applyDepthAlignment(
        depthMap: CVPixelBuffer,
        confidenceMap: CVPixelBuffer?,
        transform: simd_float4x4,
        targetSize: CGSize
    ) throws -> (depthMap: DepthMap, confidenceMap: ConfidenceMap) {
        // 簡化實作
        return (UIImage(), UIImage())
    }
    
    private func filterDepthByConfidence(
        depthMap: DepthMap,
        confidenceMap: ConfidenceMap,
        threshold: Float
    ) -> DepthMap {
        return depthMap
    }
    
    private func assessAlignmentQuality(_ depthMap: DepthMap, _ confidenceMap: ConfidenceMap) -> Double {
        return 0.85
    }
    
    private func calibrateDepthUsingPlanarReference(
        observedDepths: [Float],
        referencePoints: [CGPoint],
        knownPlanarDistance: Float
    ) throws -> (scale: Float, offset: Float) {
        return (1.0, 0.0)
    }
    
    private func validateDepthCalibration(observedDepth: Float, expectedDepth: Float) -> Float {
        return 1.0
    }
    
    private func applyDepthCalibration(_ depthMap: DepthMap, scale: Float, offset: Float) -> DepthMap {
        return depthMap
    }
    
    private func calculateCalibrationAccuracy(_ scale: Float, _ offset: Float) -> Double {
        return 0.9
    }
    
    private func performRANSACPlaneFitting(
        points: [simd_float3],
        maxIterations: Int,
        distanceThreshold: Double
    ) throws -> PlaneEquation {
        return PlaneEquation(a: 0, b: 0, c: 1, d: 0)
    }
    
    private func performLeastSquaresPlaneFitting(points: [simd_float3]) -> PlaneEquation {
        return PlaneEquation(a: 0, b: 0, c: 1, d: 0)
    }
    
    private func selectBestPlane(_ planes: [PlaneEquation]) -> PlaneEquation {
        return planes.first ?? PlaneEquation(a: 0, b: 0, c: 1, d: 0)
    }
    
    private func validateReferencePlane(
        plane: PlaneEquation,
        woundContours: [WoundContour],
        depthStatistics: WoundDepthStatistics
    ) -> (confidence: Double) {
        return (confidence: 0.8)
    }
    
    private func assessDepthDataQuality(_ depthData: CalibratedDepthData) -> DepthQuality {
        return DepthQuality(
            averageConfidence: 0.8,
            depthCoverage: 0.85,
            noiseLevel: 0.1,
            calibrationAccuracy: depthData.calibrationAccuracy
        )
    }
    
    // 其他簡化實作方法...
}

// MARK: - 圖像處理擴展

extension UIImage {
    var size: CGSize {
        return self.size
    }
    
    func pixelData() -> [UInt8] {
        // 簡化實作
        return []
    }
    
    func depthValue(at point: CGPoint) -> Float? {
        // 簡化實作
        return nil
    }
}