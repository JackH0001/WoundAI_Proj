import SwiftUI
import CoreML
import Vision
import UIKit
import CoreImage

/// 簡化版高級機器學習分類模組
/// 使用UWM MobileNetV2輕量化實現，專為iOS優化
class SimplifiedAdvancedMLModule: ObservableObject {
    
    @Published var processingStatus: String = "準備就緒"
    @Published var processingProgress: Double = 0.0
    @Published var lastClassificationTime: TimeInterval = 0.0
    
    // MARK: - 模組整合
    private let uwmModule = UWMLightweightSegmentationModule()
    private let context = CIContext()
    
    struct AdvancedClassificationResult {
        let uwmSegmentation: UWMLightweightSegmentationModule.UWMSegmentationResult
        let tissueTypeAnalysis: TissueTypeAnalysis
        let confidenceMetrics: ConfidenceMetrics
        let processingTime: TimeInterval
        let modelsUsed: [String]
        
        // 簡化版結果，兼容原有接口
        var primarySegmentation: WoundSegmentationResult {
            return WoundSegmentationResult(
                segmentationMask: uwmSegmentation.segmentationMask,
                woundBoundary: uwmSegmentation.woundRegion.contourPoints,
                woundArea: uwmSegmentation.woundRegion.area,
                boundingBox: uwmSegmentation.woundRegion.boundingBox,
                confidence: uwmSegmentation.confidence,
                modelName: "UWM_MobileNetV2_Lightweight",
                tissueRegions: [] // 簡化實現
            )
        }
        
        var consensusResult: ConsensusAnalysis {
            return ConsensusAnalysis(
                agreedBoundary: uwmSegmentation.woundRegion.contourPoints,
                disagreementAreas: [], // 單一模型無分歧
                finalSegmentation: uwmSegmentation.segmentationMask,
                consensusConfidence: uwmSegmentation.confidence,
                conflictResolution: .preferPrimary
            )
        }
    }
    
    init() {
        // 監聽UWM模組狀態
        Task {
            await updateStatus("初始化輕量化ML模組...")
        }
    }
    
    // MARK: - 主要分析功能
    
    /// 執行輕量化高級傷口分析
    func performAdvancedWoundAnalysis(image: UIImage, depthData: Data? = nil) async throws -> AdvancedClassificationResult {
        let startTime = Date()
        await updateStatus("開始輕量化傷口分析...")
        await updateProgress(0.1)
        
        // 1. UWM MobileNetV2輕量化分割
        await updateStatus("執行UWM輕量化分割...")
        let uwmResult = try await uwmModule.performWoundSegmentation(image: image)
        await updateProgress(0.6)
        
        // 2. 組織類型分析
        await updateStatus("執行組織類型分析...")
        let tissueAnalysis = try await performTissueTypeAnalysis(
            image: image,
            uwmResult: uwmResult,
            depthData: depthData
        )
        await updateProgress(0.85)
        
        // 3. 信心度評估
        let confidenceMetrics = calculateConfidenceMetrics(uwmResult: uwmResult)
        
        let processingTime = Date().timeIntervalSince(startTime)
        await updateStatus("輕量化分析完成")
        await updateProgress(1.0)
        
        await MainActor.run {
            lastClassificationTime = processingTime
        }
        
        return AdvancedClassificationResult(
            uwmSegmentation: uwmResult,
            tissueTypeAnalysis: tissueAnalysis,
            confidenceMetrics: confidenceMetrics,
            processingTime: processingTime,
            modelsUsed: ["UWM_MobileNetV2_Lightweight"]
        )
    }
    
    // MARK: - 組織類型分析
    
    private func performTissueTypeAnalysis(
        image: UIImage,
        uwmResult: UWMLightweightSegmentationModule.UWMSegmentationResult,
        depthData: Data?
    ) async throws -> TissueTypeAnalysis {
        
        let woundRegion = uwmResult.woundRegion
        
        // 1. 基於顏色的組織分類
        let colorAnalysis = try await analyzeWoundColors(
            image: image,
            woundRegion: woundRegion
        )
        
        // 2. 基於形狀特徵的組織推斷
        let shapeAnalysis = analyzeWoundShape(woundRegion: woundRegion)
        
        // 3. 可選：深度輔助分析
        var depthAnalysis: [TissueType: Double] = [:]
        if let depthData = depthData {
            depthAnalysis = try await analyzeDepthBasedTissues(
                depthData: depthData,
                woundRegion: woundRegion
            )
        }
        
        // 4. 融合分析結果
        let tissueDistribution = fuseTissueAnalysis(
            colorAnalysis: colorAnalysis,
            shapeAnalysis: shapeAnalysis,
            depthAnalysis: depthAnalysis
        )
        
        // 5. 評估癒合階段
        let healingStage = determineHealingStage(tissueDistribution: tissueDistribution)
        
        // 6. 風險評估
        let riskAssessment = calculateRiskAssessment(
            tissueDistribution: tissueDistribution,
            healingStage: healingStage,
            woundCharacteristics: woundRegion
        )
        
        return TissueTypeAnalysis(
            granulationTissue: createTissueRegion(.granulation, percentage: tissueDistribution[.granulation] ?? 0),
            necroticTissue: createTissueRegion(.necrotic, percentage: tissueDistribution[.necrotic] ?? 0),
            epithelialTissue: createTissueRegion(.epithelial, percentage: tissueDistribution[.epithelial] ?? 0),
            sloughTissue: createTissueRegion(.slough, percentage: tissueDistribution[.slough] ?? 0),
            healthySkin: createTissueRegion(.healthySkin, percentage: tissueDistribution[.healthySkin] ?? 0),
            totalWoundArea: woundRegion.area,
            tissueDistribution: tissueDistribution,
            healingStage: healingStage,
            riskAssessment: riskAssessment
        )
    }
    
    // MARK: - 顏色分析
    
    private func analyzeWoundColors(
        image: UIImage,
        woundRegion: UWMLightweightSegmentationModule.WoundRegion
    ) async throws -> [TissueType: Double] {
        
        guard let cgImage = image.cgImage else {
            throw MLError.imageProcessingFailed
        }
        
        // 在傷口區域內分析顏色分佈
        let boundingBox = woundRegion.boundingBox
        let croppedImage = cgImage.cropping(to: boundingBox)
        
        guard let croppedCGImage = croppedImage else {
            throw MLError.imageProcessingFailed
        }
        
        // 分析RGB值分佈
        let colorStats = try await extractColorStatistics(croppedCGImage)
        
        var tissueDistribution: [TissueType: Double] = [:]
        
        // 基於顏色特徵推斷組織類型
        // 肉芽組織：紅色為主
        if colorStats.redMean > 0.6 && colorStats.saturation > 0.4 {
            tissueDistribution[.granulation] = min(0.8, colorStats.redMean)
        }
        
        // 壞死組織：深色/黑色
        if colorStats.brightness < 0.3 {
            tissueDistribution[.necrotic] = min(0.7, 1.0 - colorStats.brightness)
        }
        
        // 腐肉組織：黃色調
        if colorStats.yellowIndex > 0.5 {
            tissueDistribution[.slough] = min(0.6, colorStats.yellowIndex)
        }
        
        // 上皮組織：粉紅色調
        if colorStats.pinkIndex > 0.5 && colorStats.brightness > 0.5 {
            tissueDistribution[.epithelial] = min(0.7, colorStats.pinkIndex)
        }
        
        // 正規化分佈
        let total = tissueDistribution.values.reduce(0, +)
        if total > 0 {
            for key in tissueDistribution.keys {
                tissueDistribution[key] = (tissueDistribution[key] ?? 0) / total
            }
        }
        
        return tissueDistribution
    }
    
    // MARK: - 形狀分析
    
    private func analyzeWoundShape(
        woundRegion: UWMLightweightSegmentationModule.WoundRegion
    ) -> [TissueType: Double] {
        
        var shapeBasedTissues: [TissueType: Double] = [:]
        
        // 基於傷口形狀特徵推斷
        if woundRegion.compactness > 0.7 && woundRegion.solidity > 0.8 {
            // 規則形狀通常表示良好癒合
            shapeBasedTissues[.epithelial] = 0.3
            shapeBasedTissues[.granulation] = 0.5
        } else if woundRegion.compactness < 0.3 {
            // 不規則形狀可能表示壞死或感染
            shapeBasedTissues[.necrotic] = 0.4
            shapeBasedTissues[.slough] = 0.3
        }
        
        return shapeBasedTissues
    }
    
    // MARK: - 深度分析
    
    private func analyzeDepthBasedTissues(
        depthData: Data,
        woundRegion: UWMLightweightSegmentationModule.WoundRegion
    ) async throws -> [TissueType: Double] {
        
        // 簡化的深度分析
        var depthTissues: [TissueType: Double] = [:]
        
        // 解析深度資料
        let depthValues = depthData.withUnsafeBytes { bytes in
            Array(bytes.bindMemory(to: Float32.self))
        }
        
        if !depthValues.isEmpty {
            let averageDepth = depthValues.reduce(0, +) / Float32(depthValues.count)
            let maxDepth = depthValues.max() ?? 0
            
            // 基於深度特徵推斷組織
            if averageDepth > 0.02 { // 深度大於2cm
                depthTissues[.necrotic] = 0.3
            } else if averageDepth < 0.005 { // 深度小於5mm
                depthTissues[.epithelial] = 0.4
            }
        }
        
        return depthTissues
    }
    
    // MARK: - 分析結果融合
    
    private func fuseTissueAnalysis(
        colorAnalysis: [TissueType: Double],
        shapeAnalysis: [TissueType: Double],
        depthAnalysis: [TissueType: Double]
    ) -> [TissueType: Double] {
        
        var fusedResult: [TissueType: Double] = [:]
        let allTissueTypes: [TissueType] = [.granulation, .necrotic, .epithelial, .slough, .healthySkin]
        
        for tissueType in allTissueTypes {
            let colorWeight = colorAnalysis[tissueType] ?? 0.0
            let shapeWeight = shapeAnalysis[tissueType] ?? 0.0
            let depthWeight = depthAnalysis[tissueType] ?? 0.0
            
            // 加權融合：顏色50%，形狀30%，深度20%
            let fusedScore = colorWeight * 0.5 + shapeWeight * 0.3 + depthWeight * 0.2
            
            if fusedScore > 0.1 { // 只保留有意義的分數
                fusedResult[tissueType] = fusedScore
            }
        }
        
        // 確保有基本的組織分佈
        if fusedResult.isEmpty {
            fusedResult[.granulation] = 0.6
            fusedResult[.healthySkin] = 0.4
        }
        
        // 正規化到總和為1
        let total = fusedResult.values.reduce(0, +)
        if total > 0 {
            for key in fusedResult.keys {
                fusedResult[key] = (fusedResult[key] ?? 0) / total
            }
        }
        
        return fusedResult
    }
    
    // MARK: - 癒合階段評估
    
    private func determineHealingStage(tissueDistribution: [TissueType: Double]) -> HealingStage {
        let necroticPercent = (tissueDistribution[.necrotic] ?? 0) * 100
        let granulationPercent = (tissueDistribution[.granulation] ?? 0) * 100
        let epithelialPercent = (tissueDistribution[.epithelial] ?? 0) * 100
        let sloughPercent = (tissueDistribution[.slough] ?? 0) * 100
        
        if necroticPercent > 40 {
            return .chronic
        } else if sloughPercent > 30 {
            return .infected
        } else if epithelialPercent > 50 {
            return .remodeling
        } else if granulationPercent > 40 {
            return .proliferative
        } else {
            return .inflammatory
        }
    }
    
    // MARK: - 風險評估
    
    private func calculateRiskAssessment(
        tissueDistribution: [TissueType: Double],
        healingStage: HealingStage,
        woundCharacteristics: UWMLightweightSegmentationModule.WoundRegion
    ) -> WoundRiskAssessment {
        
        var infectionRisk: Double = 0.2 // 基礎風險
        var healingPrognosis: Double = 0.7 // 基礎預後
        var treatmentUrgency: Double = 0.3 // 基礎緊急度
        
        var riskFactors: [String] = []
        var recommendations: [String] = []
        
        // 基於組織分佈評估風險
        let necroticPercent = (tissueDistribution[.necrotic] ?? 0) * 100
        let sloughPercent = (tissueDistribution[.slough] ?? 0) * 100
        
        if necroticPercent > 30 {
            infectionRisk += 0.4
            healingPrognosis -= 0.3
            treatmentUrgency += 0.4
            riskFactors.append("大量壞死組織")
            recommendations.append("需要積極清創")
        }
        
        if sloughPercent > 20 {
            infectionRisk += 0.3
            treatmentUrgency += 0.2
            riskFactors.append("腐肉組織")
            recommendations.append("清潔和抗感染治療")
        }
        
        // 基於癒合階段評估
        switch healingStage {
        case .chronic:
            healingPrognosis = 0.3
            treatmentUrgency += 0.5
            riskFactors.append("慢性不癒合")
            recommendations.append("檢查潛在病因")
            
        case .infected:
            infectionRisk = 0.8
            treatmentUrgency += 0.6
            riskFactors.append("感染徵象")
            recommendations.append("抗生素治療評估")
            
        case .proliferative:
            healingPrognosis = 0.8
            recommendations.append("維持良好護理")
            
        case .remodeling:
            healingPrognosis = 0.9
            infectionRisk = 0.1
            recommendations.append("保護新生組織")
            
        default:
            recommendations.append("密切觀察傷口變化")
        }
        
        // 基於傷口特徵評估
        if woundCharacteristics.area > 1000 { // 大傷口
            treatmentUrgency += 0.2
            riskFactors.append("大面積傷口")
        }
        
        if woundCharacteristics.compactness < 0.3 { // 不規則形狀
            infectionRisk += 0.1
            riskFactors.append("不規則邊緣")
        }
        
        // 正規化分數
        infectionRisk = max(0.0, min(1.0, infectionRisk))
        healingPrognosis = max(0.0, min(1.0, healingPrognosis))
        treatmentUrgency = max(0.0, min(1.0, treatmentUrgency))
        
        return WoundRiskAssessment(
            infectionRisk: infectionRisk,
            healingPrognosis: healingPrognosis,
            treatmentUrgency: treatmentUrgency,
            riskFactors: riskFactors,
            recommendations: recommendations
        )
    }
    
    // MARK: - 輔助方法
    
    private func calculateConfidenceMetrics(
        uwmResult: UWMLightweightSegmentationModule.UWMSegmentationResult
    ) -> ConfidenceMetrics {
        
        let overallConfidence = uwmResult.confidence
        
        // 基於單一模型的信心度評估
        var tissueConfidence: [TissueType: Double] = [:]
        for tissueType in TissueType.allCases {
            tissueConfidence[tissueType] = overallConfidence * 0.8 // 組織分類信心度略低於分割信心度
        }
        
        let recommendedAction: RecommendedAction
        if overallConfidence > 0.8 {
            recommendedAction = .acceptResult
        } else if overallConfidence > 0.6 {
            recommendedAction = .reviewResult
        } else {
            recommendedAction = .retakeImage
        }
        
        return ConfidenceMetrics(
            overallConfidence: overallConfidence,
            modelAgreement: 1.0, // 單一模型無分歧
            segmentationQuality: overallConfidence,
            tissueClassificationConfidence: tissueConfidence,
            uncertaintyAreas: [], // 簡化實現
            recommendedAction: recommendedAction
        )
    }
    
    private func createTissueRegion(_ type: TissueType, percentage: Double) -> TissueRegion? {
        guard percentage > 0.05 else { return nil } // 只創建佔比超過5%的組織區域
        
        return TissueRegion(
            type: type,
            mask: UIImage(), // 簡化實現
            area: percentage * 1000, // 假設總面積1000像素
            percentage: percentage * 100,
            confidence: 0.75,
            characteristics: TissueCharacteristics(
                color: getTissueColor(type),
                texture: TextureAnalysis(entropy: 0.5, contrast: 0.5, homogeneity: 0.5, roughness: 0.5),
                depth: nil,
                vascularity: getVascularityLevel(type),
                healthScore: type.healthScore
            )
        )
    }
    
    private func getTissueColor(_ type: TissueType) -> TissueColor {
        switch type {
        case .granulation: return .red
        case .necrotic: return .black
        case .epithelial: return .pink
        case .slough: return .yellow
        case .healthySkin: return .white
        case .exudate: return .green
        }
    }
    
    private func getVascularityLevel(_ type: TissueType) -> VascularityLevel {
        switch type {
        case .granulation: return .abundant
        case .epithelial: return .moderate
        case .healthySkin: return .minimal
        default: return .none
        }
    }
    
    // MARK: - 狀態更新
    
    @MainActor
    private func updateStatus(_ status: String) {
        processingStatus = status
        print("🔬 簡化ML分析: \(status)")
    }
    
    @MainActor
    private func updateProgress(_ progress: Double) {
        processingProgress = progress
    }
}

// MARK: - 顏色統計結構

struct ColorStatistics {
    let redMean: Double
    let greenMean: Double
    let blueMean: Double
    let brightness: Double
    let saturation: Double
    let yellowIndex: Double  // (R + G - B) / 2
    let pinkIndex: Double    // (R + B - G) / 2
}

// MARK: - 輔助擴展

extension SimplifiedAdvancedMLModule {
    
    private func extractColorStatistics(_ cgImage: CGImage) async throws -> ColorStatistics {
        guard let data = cgImage.dataProvider?.data,
              let bytes = CFDataGetBytePtr(data) else {
            throw MLError.imageProcessingFailed
        }
        
        let width = cgImage.width
        let height = cgImage.height
        let bytesPerPixel = 4
        let pixelCount = width * height
        
        var totalRed: Double = 0
        var totalGreen: Double = 0
        var totalBlue: Double = 0
        
        for y in 0..<height {
            for x in 0..<width {
                let offset = y * width * bytesPerPixel + x * bytesPerPixel
                let red = Double(bytes[offset]) / 255.0
                let green = Double(bytes[offset + 1]) / 255.0
                let blue = Double(bytes[offset + 2]) / 255.0
                
                totalRed += red
                totalGreen += green
                totalBlue += blue
            }
        }
        
        let redMean = totalRed / Double(pixelCount)
        let greenMean = totalGreen / Double(pixelCount)
        let blueMean = totalBlue / Double(pixelCount)
        
        let brightness = (redMean + greenMean + blueMean) / 3.0
        let maxChannel = max(redMean, max(greenMean, blueMean))
        let saturation = maxChannel > 0 ? (maxChannel - min(redMean, min(greenMean, blueMean))) / maxChannel : 0
        
        let yellowIndex = (redMean + greenMean - blueMean) / 2.0
        let pinkIndex = (redMean + blueMean - greenMean) / 2.0
        
        return ColorStatistics(
            redMean: redMean,
            greenMean: greenMean,
            blueMean: blueMean,
            brightness: brightness,
            saturation: saturation,
            yellowIndex: max(0, yellowIndex),
            pinkIndex: max(0, pinkIndex)
        )
    }
}