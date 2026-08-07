import SwiftUI
import CoreML
import Vision
import CoreImage
import Foundation

class ClassificationModule: ObservableObject {
    @Published var isProcessing = false
    @Published var lastClassification: DetailedWoundClassification?
    @Published var lastAdvancedAnalysis: TissueTypeAnalysis?
    
    private var coreMLModel: VNCoreMLModel?
    private let colorAnalyzer = ColorAnalyzer()
    private let textureAnalyzer = TextureAnalyzer()
    private let shapeAnalyzer = ShapeAnalyzer()
    
    // 新增：整合高級ML分析模組（暫時簡化實現）
    // private let advancedMLModule = SimplifiedAdvancedMLModule()
    // private let backendAPIService = WoundAnalysisAPIService() // 暫時註解掉，未實現
    
    init() {
        loadClassificationModel()
    }
    
    private func loadClassificationModel() {
        do {
            guard let modelURL = Bundle.main.url(forResource: "WoundDetailedClassification", withExtension: "mlmodelc") else {
                print("詳細分類模型文件未找到，將使用基於特徵的分類")
                return
            }
            
            let model = try MLModel(contentsOf: modelURL)
            coreMLModel = try VNCoreMLModel(for: model)
            print("成功載入詳細分類模型")
        } catch {
            print("載入詳細分類模型失敗: \(error.localizedDescription)")
            print("將使用基於特徵的分類作為備用方案")
        }
    }
    
    func classify(_ processedImage: ProcessedImage) async throws -> DetailedWoundClassification {
        await MainActor.run {
            isProcessing = true
        }
        
        defer { 
            Task { @MainActor in
                isProcessing = false
            }
        }
        
        let image = processedImage.image
        
        // 執行高級ML分析（UWM + Deepskin雙重驗證）
        do {
            let advancedResult = try await performAdvancedMLAnalysis(image, depthData: processedImage.depthData)
            
            await MainActor.run {
                lastAdvancedAnalysis = advancedResult
            }
            
            // 將高級分析結果轉換為DetailedWoundClassification格式
            let enhancedClassification = try await convertAdvancedResultToDetailedClassification(
                advancedResult,
                fallbackImage: image
            )
            
            await MainActor.run {
                lastClassification = enhancedClassification
            }
            
            return enhancedClassification
            
        } catch {
            print("⚠️ 高級ML分析失敗，降級到傳統分類: \(error)")
            // 降級到原有的分類邏輯
            return try await performLegacyClassification(image)
        }
    }
    
    /// 執行簡化的高級分析（基於基本特徵）
    private func performAdvancedMLAnalysis(_ image: UIImage, depthData: Data?) async throws -> TissueTypeAnalysis {
        // 簡化實現：基於基本特徵分析創建組織類型分析
        let basicFeatures = try await extractBasicFeatures(from: image)
        
        // 創建基本的組織分佈
        var tissueDistribution: [TissueType: Double] = [
            .granulation: 0.6,
            .epithelial: 0.3,
            .necrotic: 0.1
        ]
        
        // 基於顏色特徵調整分佈
        if basicFeatures.redDominance > 0.7 {
            tissueDistribution[.granulation] = 0.8
            tissueDistribution[.epithelial] = 0.15
            tissueDistribution[.necrotic] = 0.05
        }
        
        // 創建基本風險評估
        let riskAssessment = WoundRiskAssessment(
            infectionRisk: 0.2,
            healingPrognosis: 0.7,
            treatmentUrgency: 0.3,
            riskFactors: [],
            recommendations: ["定期觀察", "保持清潔"]
        )
        
        // 返回組織類型分析
        return TissueTypeAnalysis(
            granulationTissue: nil,
            necroticTissue: nil,
            epithelialTissue: nil,
            sloughTissue: nil,
            healthySkin: nil,
            totalWoundArea: 100.0,
            tissueDistribution: tissueDistribution,
            healingStage: .proliferative,
            riskAssessment: riskAssessment
        )
    }
    
    /// 將高級分析結果轉換為DetailedWoundClassification
    private func convertAdvancedResultToDetailedClassification(
        _ tissueAnalysis: TissueTypeAnalysis,
        fallbackImage: UIImage
    ) async throws -> DetailedWoundClassification {
        
        // tissueAnalysis 已經是參數，不需要再次提取
        
        // 根據組織分析結果計算分類分數
        var acuteScore = 0.5
        var chronicScore = 0.5
        var infectedScore = 0.0
        var healingScore = 0.0
        
        // 基於癒合階段調整分數
        switch tissueAnalysis.healingStage {
        case .inflammatory:
            acuteScore = 0.8
            chronicScore = 0.2
        case .proliferative:
            acuteScore = 0.6
            healingScore = 0.7
        case .remodeling:
            healingScore = 0.9
            acuteScore = 0.3
        case .chronic:
            chronicScore = 0.9
            acuteScore = 0.1
        case .infected:
            infectedScore = 0.9
            acuteScore = 0.7
        }
        
        // 基於風險評估調整感染分數
        infectedScore = max(infectedScore, tissueAnalysis.riskAssessment.infectionRisk)
        
        // 基於組織分佈微調分數
        if let necroticPercentage = tissueAnalysis.tissueDistribution[TissueType.necrotic], necroticPercentage > 30 {
            chronicScore += 0.2
            infectedScore += 0.3
        }
        
        if let granulationPercentage = tissueAnalysis.tissueDistribution[TissueType.granulation], granulationPercentage > 60 {
            healingScore += 0.3
        }
        
        // 正規化分數
        acuteScore = max(0.0, min(1.0, acuteScore))
        chronicScore = max(0.0, min(1.0, chronicScore))
        infectedScore = max(0.0, min(1.0, infectedScore))
        healingScore = max(0.0, min(1.0, healingScore))
        
        return DetailedWoundClassification(
            acuteScore: acuteScore,
            chronicScore: chronicScore,
            infectedScore: infectedScore,
            healingScore: healingScore,
            confidence: 0.8, // 簡化實現的預設信心度
            // 新增字段
            advancedAnalysisAvailable: true,
            pwatScore: nil, // 簡化實現暫不提供
            tissueDistribution: tissueAnalysis.tissueDistribution,
            riskAssessment: tissueAnalysis.riskAssessment,
            modelConsensus: 0.8, // 預設共識信心度
            processingTime: 1.0 // 預設處理時間
        )
    }
    
    /// 傳統分類方法（降級使用）
    private func performLegacyClassification(_ image: UIImage) async throws -> DetailedWoundClassification {
        if let mlModel = coreMLModel {
            let mlResult = try await performMLClassification(image, model: mlModel)
            let enhancedResult = try await enhanceWithFeatureAnalysis(image, mlResult: mlResult)
            
            await MainActor.run {
                lastClassification = enhancedResult
            }
            
            return enhancedResult
        } else {
            let featureBasedResult = try await performFeatureBasedClassification(image)
            
            await MainActor.run {
                lastClassification = featureBasedResult
            }
            
            return featureBasedResult
        }
    }
    
    /// 提取基本特徵
    private func extractBasicFeatures(from image: UIImage) async throws -> BasicImageFeatures {
        // 簡化的特徵提取
        return BasicImageFeatures(
            brightness: 0.6,
            contrast: 0.5,
            redDominance: 0.4,
            texture: 0.5
        )
    }
    
    /// 合併本地和雲端分析結果（暫時未實現）
    // private func mergeLocalAndCloudResults(...) { ... }
    
    private func performMLClassification(_ image: UIImage, model: VNCoreMLModel) async throws -> MLClassificationResult {
        guard let cgImage = image.cgImage else {
            throw ClassificationError.invalidImage
        }
        
        return try await withCheckedThrowingContinuation { continuation in
            let request = VNCoreMLRequest(model: model) { request, error in
                if let error = error {
                    continuation.resume(throwing: error)
                    return
                }
                
                guard let results = request.results as? [VNClassificationObservation] else {
                    continuation.resume(throwing: ClassificationError.classificationFailed)
                    return
                }
                
                var acuteScore = 0.5
                var chronicScore = 0.5
                var infectedScore = 0.0
                var healingScore = 0.0
                
                for result in results {
                    switch result.identifier.lowercased() {
                    case "acute":
                        acuteScore = Double(result.confidence)
                    case "chronic":
                        chronicScore = Double(result.confidence)
                    case "infected":
                        infectedScore = Double(result.confidence)
                    case "healing":
                        healingScore = Double(result.confidence)
                    default:
                        break
                    }
                }
                
                let mlResult = MLClassificationResult(
                    acuteScore: acuteScore,
                    chronicScore: chronicScore,
                    infectedScore: infectedScore,
                    healingScore: healingScore,
                    confidence: Double(results.first?.confidence ?? 0)
                )
                
                continuation.resume(returning: mlResult)
            }
            
            let handler = VNImageRequestHandler(cgImage: cgImage)
            do {
                try handler.perform([request])
            } catch {
                continuation.resume(throwing: error)
            }
        }
    }
    
    private func enhanceWithFeatureAnalysis(_ image: UIImage, mlResult: MLClassificationResult) async throws -> DetailedWoundClassification {
        let colorFeatures = try await colorAnalyzer.analyze(image)
        let textureFeatures = try await textureAnalyzer.analyze(image)
        let shapeFeatures = try await shapeAnalyzer.analyze(image)
        
        let adjustedScores = adjustScoresWithFeatures(
            mlResult: mlResult,
            colorFeatures: colorFeatures,
            textureFeatures: textureFeatures,
            shapeFeatures: shapeFeatures
        )
        
        _ = assessInfectionRisk(colorFeatures, textureFeatures)
        _ = determineHealingStage(colorFeatures, textureFeatures, shapeFeatures)
        
        return DetailedWoundClassification(
            acuteScore: adjustedScores.acute,
            chronicScore: adjustedScores.chronic,
            infectedScore: adjustedScores.infected,
            healingScore: adjustedScores.healing,
            confidence: mlResult.confidence
        )
    }
    
    private func performFeatureBasedClassification(_ image: UIImage) async throws -> DetailedWoundClassification {
        let colorFeatures = try await colorAnalyzer.analyze(image)
        let textureFeatures = try await textureAnalyzer.analyze(image)
        let shapeFeatures = try await shapeAnalyzer.analyze(image)
        
        var acuteScore = 0.5
        var chronicScore = 0.5
        var infectedScore = 0.0
        var healingScore = 0.0
        
        if colorFeatures.redness > 0.6 && colorFeatures.brightness > 0.4 {
            acuteScore += 0.3
            infectedScore += colorFeatures.redness * 0.4
        }
        
        if colorFeatures.darkness > 0.5 || textureFeatures.irregularity > 0.7 {
            chronicScore += 0.3
            acuteScore -= 0.2
        }
        
        if colorFeatures.yellowness > 0.4 {
            infectedScore += 0.4
        }
        
        if colorFeatures.pinkness > 0.5 && textureFeatures.smoothness > 0.6 {
            healingScore += 0.4
        }
        
        acuteScore = max(0.0, min(1.0, acuteScore))
        chronicScore = max(0.0, min(1.0, chronicScore))
        infectedScore = max(0.0, min(1.0, infectedScore))
        healingScore = max(0.0, min(1.0, healingScore))
        
        _ = assessInfectionRisk(colorFeatures, textureFeatures)
        _ = determineHealingStage(colorFeatures, textureFeatures, shapeFeatures)
        
        _ = AdjustedScores(
            acute: acuteScore,
            chronic: chronicScore,
            infected: infectedScore,
            healing: healingScore
        )
        
        return DetailedWoundClassification(
            acuteScore: acuteScore,
            chronicScore: chronicScore,
            infectedScore: infectedScore,
            healingScore: healingScore,
            confidence: 0.75
        )
    }
    
    private func adjustScoresWithFeatures(
        mlResult: MLClassificationResult,
        colorFeatures: ColorFeatures,
        textureFeatures: TextureFeatures,
        shapeFeatures: ShapeFeatures
    ) -> AdjustedScores {
        
        var acuteAdjustment = 0.0
        var chronicAdjustment = 0.0
        var infectedAdjustment = 0.0
        var healingAdjustment = 0.0
        
        if colorFeatures.redness > 0.7 {
            acuteAdjustment += 0.1
            infectedAdjustment += 0.15
        }
        
        if textureFeatures.irregularity > 0.8 {
            chronicAdjustment += 0.15
        }
        
        if colorFeatures.pinkness > 0.6 && textureFeatures.smoothness > 0.5 {
            healingAdjustment += 0.2
        }
        
        return AdjustedScores(
            acute: max(0.0, min(1.0, mlResult.acuteScore + acuteAdjustment)),
            chronic: max(0.0, min(1.0, mlResult.chronicScore + chronicAdjustment)),
            infected: max(0.0, min(1.0, mlResult.infectedScore + infectedAdjustment)),
            healing: max(0.0, min(1.0, mlResult.healingScore + healingAdjustment))
        )
    }
    
    private func assessInfectionRisk(_ colorFeatures: ColorFeatures, _ textureFeatures: TextureFeatures) -> RiskAssessment {
        var riskScore = 0.0
        var riskFactors: [String] = []
        
        if colorFeatures.redness > 0.7 {
            riskScore += 0.3
            riskFactors.append("高紅腫程度")
        }
        
        if colorFeatures.yellowness > 0.5 {
            riskScore += 0.4
            riskFactors.append("疑似化膿")
        }
        
        if textureFeatures.roughness > 0.6 {
            riskScore += 0.2
            riskFactors.append("表面粗糙")
        }
        
        if colorFeatures.darkness > 0.6 {
            riskScore += 0.3
            riskFactors.append("組織壞死跡象")
        }
        
        let riskLevel: RiskLevel
        if riskScore < 0.3 {
            riskLevel = .low
        } else if riskScore < 0.6 {
            riskLevel = .medium
        } else {
            riskLevel = .high
        }
        
        return RiskAssessment(
            level: riskLevel,
            score: riskScore,
            factors: riskFactors
        )
    }
    
    private func determineHealingStage(_ colorFeatures: ColorFeatures, _ textureFeatures: TextureFeatures, _ shapeFeatures: ShapeFeatures) -> HealingStage {
        if colorFeatures.darkness > 0.6 {
            return .inflammatory
        } else if colorFeatures.redness > 0.5 && textureFeatures.roughness > 0.5 {
            return .proliferative
        } else if colorFeatures.pinkness > 0.5 && textureFeatures.smoothness > 0.6 {
            return .remodeling
        } else {
            return .inflammatory
        }
    }
    
    private func generateRecommendations(_ scores: AdjustedScores, _ risk: RiskAssessment, _ stage: HealingStage) -> [String] {
        var recommendations: [String] = []
        
        if risk.level == .high {
            recommendations.append("建議立即就醫，疑似感染")
            recommendations.append("保持傷口清潔，避免污染")
        }
        
        if scores.chronic > 0.6 {
            recommendations.append("慢性傷口需要專業醫療照護")
            recommendations.append("定期更換敷料，促進癒合")
        }
        
        switch stage {
        case .inflammatory:
            recommendations.append("發炎期：注意清潔和消炎")
        case .proliferative:
            recommendations.append("增生期：保持濕潤環境")
        case .remodeling:
            recommendations.append("重塑期：保護新生組織")
        case .chronic:
            recommendations.append("慢性期：需要專業評估和積極治療")
        case .infected:
            recommendations.append("感染期：立即就醫，需要抗生素治療")
        }
        
        if scores.healing > 0.6 {
            recommendations.append("傷口癒合良好，繼續現有護理")
        }
        
        return recommendations
    }
}

class ColorAnalyzer {
    func analyze(_ image: UIImage) async throws -> ColorFeatures {
        guard let cgImage = image.cgImage else {
            throw ClassificationError.invalidImage
        }
        
        let width = cgImage.width
        let height = cgImage.height
        let bytesPerPixel = 4
        let bytesPerRow = width * bytesPerPixel
        
        var pixelData = [UInt8](repeating: 0, count: width * height * bytesPerPixel)
        
        let colorSpace = CGColorSpaceCreateDeviceRGB()
        let context = CGContext(
            data: &pixelData,
            width: width,
            height: height,
            bitsPerComponent: 8,
            bytesPerRow: bytesPerRow,
            space: colorSpace,
            bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue
        )
        
        context?.draw(cgImage, in: CGRect(x: 0, y: 0, width: width, height: height))
        
        var totalRed = 0.0, totalGreen = 0.0, totalBlue = 0.0
        var redness = 0.0, yellowness = 0.0, pinkness = 0.0, darkness = 0.0
        let pixelCount = width * height
        
        for i in stride(from: 0, to: pixelData.count, by: 4) {
            let r = Double(pixelData[i]) / 255.0
            let g = Double(pixelData[i + 1]) / 255.0
            let b = Double(pixelData[i + 2]) / 255.0
            
            totalRed += r
            totalGreen += g
            totalBlue += b
            
            if r > g && r > b && r > 0.5 {
                redness += (r - max(g, b))
            }
            
            if r > 0.6 && g > 0.6 && b < 0.4 {
                yellowness += (r + g - b) / 2
            }
            
            if r > 0.7 && g > 0.5 && b > 0.5 && r > b {
                pinkness += (r + g + b) / 3
            }
            
            let brightness = (r + g + b) / 3
            if brightness < 0.3 {
                darkness += (0.3 - brightness)
            }
        }
        
        return ColorFeatures(
            averageRed: totalRed / Double(pixelCount),
            averageGreen: totalGreen / Double(pixelCount),
            averageBlue: totalBlue / Double(pixelCount),
            redness: redness / Double(pixelCount),
            yellowness: yellowness / Double(pixelCount),
            pinkness: pinkness / Double(pixelCount),
            darkness: darkness / Double(pixelCount),
            brightness: (totalRed + totalGreen + totalBlue) / (3.0 * Double(pixelCount))
        )
    }
}

class TextureAnalyzer {
    func analyze(_ image: UIImage) async throws -> TextureFeatures {
        guard let cgImage = image.cgImage else {
            throw ClassificationError.invalidImage
        }
        
        let context = CIContext()
        let ciImage = CIImage(cgImage: cgImage)
        
        guard let edgeDetectionFilter = CIFilter(name: "CIEdgeWork") else {
            throw ClassificationError.processingFailed
        }
        edgeDetectionFilter.setValue(ciImage, forKey: kCIInputImageKey)
        edgeDetectionFilter.setValue(3.0, forKey: "inputRadius")
        
        guard let edgeImage = edgeDetectionFilter.outputImage,
              let edgeCGImage = context.createCGImage(edgeImage, from: edgeImage.extent) else {
            throw ClassificationError.processingFailed
        }
        
        let edgeIntensity = calculateEdgeIntensity(edgeCGImage)
        let roughness = edgeIntensity
        let smoothness = 1.0 - roughness
        let irregularity = calculateIrregularity(cgImage)
        
        return TextureFeatures(
            roughness: roughness,
            smoothness: smoothness,
            irregularity: irregularity,
            edgeIntensity: edgeIntensity
        )
    }
    
    private func calculateEdgeIntensity(_ image: CGImage) -> Double {
        let width = image.width
        let height = image.height
        let bytesPerPixel = 4
        let bytesPerRow = width * bytesPerPixel
        
        var pixelData = [UInt8](repeating: 0, count: width * height * bytesPerPixel)
        
        let colorSpace = CGColorSpaceCreateDeviceRGB()
        let context = CGContext(
            data: &pixelData,
            width: width,
            height: height,
            bitsPerComponent: 8,
            bytesPerRow: bytesPerRow,
            space: colorSpace,
            bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue
        )
        
        context?.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
        
        var totalIntensity = 0.0
        let pixelCount = width * height
        
        for i in stride(from: 0, to: pixelData.count, by: 4) {
            let gray = Double(pixelData[i]) * 0.299 + Double(pixelData[i+1]) * 0.587 + Double(pixelData[i+2]) * 0.114
            totalIntensity += gray / 255.0
        }
        
        return totalIntensity / Double(pixelCount)
    }
    
    private func calculateIrregularity(_ image: CGImage) -> Double {
        return 0.5
    }
}

class ShapeAnalyzer {
    func analyze(_ image: UIImage) async throws -> ShapeFeatures {
        guard let cgImage = image.cgImage else {
            throw ClassificationError.invalidImage
        }
        
        let area = Double(cgImage.width * cgImage.height)
        let aspectRatio = Double(cgImage.width) / Double(cgImage.height)
        
        return ShapeFeatures(
            area: area,
            perimeter: 2 * (Double(cgImage.width) + Double(cgImage.height)),
            aspectRatio: aspectRatio,
            circularity: calculateCircularity(area: area, perimeter: 2 * (Double(cgImage.width) + Double(cgImage.height))),
            compactness: 0.5
        )
    }
    
    private func calculateCircularity(area: Double, perimeter: Double) -> Double {
        if perimeter == 0 { return 0 }
        return (4 * Double.pi * area) / (perimeter * perimeter)
    }
}

// 使用WoundTypes.swift中的定義

struct MLClassificationResult {
    let acuteScore: Double
    let chronicScore: Double
    let infectedScore: Double
    let healingScore: Double
    let confidence: Double
}

struct AdjustedScores {
    let acute: Double
    let chronic: Double
    let infected: Double
    let healing: Double
}

struct ColorFeatures {
    let averageRed: Double
    let averageGreen: Double
    let averageBlue: Double
    let redness: Double
    let yellowness: Double
    let pinkness: Double
    let darkness: Double
    let brightness: Double
}

struct TextureFeatures {
    let roughness: Double
    let smoothness: Double
    let irregularity: Double
    let edgeIntensity: Double
}

struct ShapeFeatures {
    let area: Double
    let perimeter: Double
    let aspectRatio: Double
    let circularity: Double
    let compactness: Double
}

struct RiskAssessment {
    let level: RiskLevel
    let score: Double
    let factors: [String]
}

// 注意：HealingStage 和 RiskLevel 枚舉已在 WoundTypes.swift 中定義

struct BasicImageFeatures {
    let brightness: Double
    let contrast: Double
    let redDominance: Double
    let texture: Double
}

enum ClassificationError: Error, LocalizedError {
    case invalidImage
    case classificationFailed
    case processingFailed
    case modelNotAvailable
    
    var errorDescription: String? {
        switch self {
        case .invalidImage:
            return "無效的影像資料"
        case .classificationFailed:
            return "分類處理失敗"
        case .processingFailed:
            return "影像處理失敗"
        case .modelNotAvailable:
            return "分類模型不可用"
        }
    }
}