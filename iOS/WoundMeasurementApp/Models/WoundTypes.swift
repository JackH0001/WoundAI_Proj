import Foundation
import CoreImage
import UIKit
import SwiftUI

// MARK: - 開發者模式開關
struct AppDebugSettings {
    static var isDeveloperMode: Bool {
        #if DEBUG
        return true
        #else
        return false
        #endif
    }
}

// MARK: - 傷口分類相關結構

/// 傷口類型枚舉
enum WoundType: String, CaseIterable, Identifiable {
    case acute = "急性傷口"
    case chronic = "慢性傷口"
    case surgical = "手術傷口" 
    case traumatic = "外傷傷口"
    case burn = "燒燙傷"
    case ulcer = "潰瘍"
    case laceration = "撕裂傷"
    case abrasion = "擦傷"
    case puncture = "穿刺傷"
    case unknown = "未知類型"
    
    var id: String { rawValue }
    
    var color: Color {
        switch self {
        case .acute: return .red
        case .chronic: return .orange
        case .surgical: return .blue
        case .traumatic: return .purple
        case .burn: return .pink
        case .ulcer: return .brown
        case .laceration: return .indigo
        case .abrasion: return .yellow
        case .puncture: return .mint
        case .unknown: return .gray
        }
    }
}

/// 詳細傷口分類結果
struct DetailedWoundClassification {
    let primaryType: WoundType
    let acuteScore: Double        // 0-1
    let chronicScore: Double      // 0-1
    let infectedScore: Double     // 0-1
    let healingScore: Double      // 0-1
    let confidence: Double        // 0-1
    
    // 新增：高級分析字段
    let advancedAnalysisAvailable: Bool
    let pwatScore: Double?
    let tissueDistribution: [TissueType: Double]?
    let riskAssessment: WoundRiskAssessment?
    let modelConsensus: Double?
    let processingTime: TimeInterval?
    
    // 預設初始化器（向後兼容）
    init(acuteScore: Double = 0.0, chronicScore: Double = 0.0, infectedScore: Double = 0.0, healingScore: Double = 0.0, confidence: Double = 0.0) {
        self.acuteScore = acuteScore
        self.chronicScore = chronicScore  
        self.infectedScore = infectedScore
        self.healingScore = healingScore
        self.confidence = confidence
        
        // 預設值
        self.advancedAnalysisAvailable = false
        self.pwatScore = nil
        self.tissueDistribution = nil
        self.riskAssessment = nil
        self.modelConsensus = nil
        self.processingTime = nil
        
        // 根據分數決定主要類型
        let scores = [
            (WoundType.acute, acuteScore),
            (WoundType.chronic, chronicScore)
        ]
        self.primaryType = scores.max { $0.1 < $1.1 }?.0 ?? .unknown
    }
    
    // 完整初始化器（包含高級分析）
    init(acuteScore: Double, chronicScore: Double, infectedScore: Double, 
         healingScore: Double, confidence: Double,
         advancedAnalysisAvailable: Bool, pwatScore: Double? = nil,
         tissueDistribution: [TissueType: Double]? = nil,
         riskAssessment: WoundRiskAssessment? = nil,
         modelConsensus: Double? = nil, processingTime: TimeInterval? = nil) {
        self.acuteScore = acuteScore
        self.chronicScore = chronicScore
        self.infectedScore = infectedScore
        self.healingScore = healingScore
        self.confidence = confidence
        self.advancedAnalysisAvailable = advancedAnalysisAvailable
        self.pwatScore = pwatScore
        self.tissueDistribution = tissueDistribution
        self.riskAssessment = riskAssessment
        self.modelConsensus = modelConsensus
        self.processingTime = processingTime
        
        // 根據分數決定主要類型
        let scores = [
            (WoundType.acute, acuteScore),
            (WoundType.chronic, chronicScore)
        ]
        self.primaryType = scores.max { $0.1 < $1.1 }?.0 ?? .unknown
    }
    
    var secondaryIndicators: [String] {
        var indicators: [String] = []
        
        if healingScore > 0.7 {
            indicators.append("正在癒合")
        }
        
        if infectedScore > 0.6 {
            indicators.append("感染徵象")
        }
        
        if chronicScore > 0.5 && acuteScore > 0.5 {
            indicators.append("急慢性混合")
        }
        
        // 新增：基於高級分析的指標
        if let tissueDistribution = tissueDistribution {
            if let necroticPercent = tissueDistribution[.necrotic], necroticPercent > 30 {
                indicators.append("組織壞死")
            }
            
            if let granulationPercent = tissueDistribution[.granulation], granulationPercent > 60 {
                indicators.append("良好肉芽生成")
            }
            
            if let sloughPercent = tissueDistribution[.slough], sloughPercent > 40 {
                indicators.append("需要清創")
            }
        }
        
        if let riskAssessment = riskAssessment {
            if riskAssessment.infectionRisk > 0.7 {
                indicators.append("高感染風險")
            }
            
            if riskAssessment.healingPrognosis < 0.3 {
                indicators.append("癒合困難")
            }
        }
        
        if let pwatScore = pwatScore {
            if pwatScore > 12 {
                indicators.append("嚴重程度高")
            } else if pwatScore < 5 {
                indicators.append("輕微傷口")
            }
        }
        
        return indicators
    }
    
    var detailedReport: String {
        var report = "=== 詳細傷口分析報告 ===\n\n"
        
        // 基本分類
        report += "主要分類: \(primaryType.rawValue) (信心度: \(String(format: "%.1f%%", confidence * 100)))\n"
        report += "急性評分: \(String(format: "%.2f", acuteScore))\n"
        report += "慢性評分: \(String(format: "%.2f", chronicScore))\n"
        report += "感染評分: \(String(format: "%.2f", infectedScore))\n"
        report += "癒合評分: \(String(format: "%.2f", healingScore))\n\n"
        
        // 高級分析結果
        if advancedAnalysisAvailable {
            report += "=== 高級AI分析 ===\n"
            
            if let pwatScore = pwatScore {
                report += "PWAT評分: \(String(format: "%.1f", pwatScore))/17\n"
            }
            
            if let modelConsensus = modelConsensus {
                report += "模型一致性: \(String(format: "%.1f%%", modelConsensus * 100))\n"
            }
            
            if let processingTime = processingTime {
                report += "分析時間: \(String(format: "%.2f", processingTime))秒\n"
            }
            
            // 組織分佈
            if let tissueDistribution = tissueDistribution {
                report += "\n=== 組織分佈 ===\n"
                for (tissueType, percentage) in tissueDistribution.sorted(by: { $0.value > $1.value }) {
                    if percentage > 5 {  // 只顯示占比超過5%的組織
                        report += "\(tissueType.displayName): \(String(format: "%.1f%%", percentage))\n"
                    }
                }
            }
            
            // 風險評估
            if let riskAssessment = riskAssessment {
                report += "\n=== 風險評估 ===\n"
                report += "感染風險: \(String(format: "%.1f%%", riskAssessment.infectionRisk * 100))\n"
                report += "癒合預後: \(String(format: "%.1f%%", riskAssessment.healingPrognosis * 100))\n"
                report += "治療緊急度: \(String(format: "%.1f%%", riskAssessment.treatmentUrgency * 100))\n"
                
                if !riskAssessment.riskFactors.isEmpty {
                    report += "\n風險因子:\n"
                    for factor in riskAssessment.riskFactors {
                        report += "• \(factor)\n"
                    }
                }
                
                if !riskAssessment.recommendations.isEmpty {
                    report += "\n建議事項:\n"
                    for recommendation in riskAssessment.recommendations {
                        report += "• \(recommendation)\n"
                    }
                }
            }
        }
        
        // 次要指標
        if !secondaryIndicators.isEmpty {
            report += "\n=== 臨床特徵 ===\n"
            for indicator in secondaryIndicators {
                report += "• \(indicator)\n"
            }
        }
        
        return report
    }
}

// MARK: - 傷口特徵結構
struct WoundFeatures {
    let area: Double
    let aspectRatio: Double
    let colorDistribution: ColorDistribution
    let textureHomogeneity: Double
    let textureContrast: Double
    let edgeRoughness: Double
    let symmetryIndex: Double
    let centroid: CGPoint
    let boundingBox: CGRect
    let perimeter: Double
    let circularity: Double
    let compactness: Double
}

// MARK: - 色彩分布結構
struct ColorDistribution {
    let redMean: Double
    let greenMean: Double
    let blueMean: Double
    let redStd: Double
    let greenStd: Double
    let blueStd: Double
    let saturation: Double
    let brightness: Double
    let contrast: Double
}

// MARK: - 預測品質結構
struct PredictedQuality {
    let overallScore: Double
    let sharpnessScore: Double
    let lightingScore: Double
    let colorAccuracyScore: Double
    let noiseLevel: Double
    let confidenceInterval: (Double, Double)
}

// MARK: - 品質指標結構
struct QualityMetrics {
    let snr: Double
    let blurVariance: Double
    let contrastRatio: Double
    let colorBalance: Double
    let overallQuality: Double
    let isAcceptable: Bool
    let blurLevel: Double
    let depthCoverage: Double
}

// MARK: - 處理後影像結構
struct ProcessedImage {
    let image: UIImage
    let depthData: Data
    let qualityMetrics: QualityMetrics
    let roi: CGRect
    let woundFeatures: WoundFeatures?
    let multiScaleImages: [CIImage]
    let roiConfidence: Double
}

// MARK: - 智慧ROI結果結構
struct SmartROIResult {
    let roi: CGRect
    let confidence: Double
    let features: WoundFeatures
    let processingTime: Double
}

// MARK: - ROI候選結構
struct ROICandidate {
    let boundingBox: CGRect
    var confidence: Double
    let shapeScore: Double
    let depthScore: Double
}

// MARK: - 自適應品質門檻結構
struct QualityThresholds {
    let minSNR: Double
    let minBlurVariance: Double
    let minContrastRatio: Double
    let minColorBalance: Double
    let minOverallQuality: Double
    let minDepthCoverage: Double
    
    static let standard = QualityThresholds(
        minSNR: 20.0,
        minBlurVariance: 100.0,
        minContrastRatio: 0.3,
        minColorBalance: 0.6,
        minOverallQuality: 0.7,
        minDepthCoverage: 0.8
    )
    
    static let simulator = QualityThresholds(
        minSNR: 15.0,
        minBlurVariance: 50.0,
        minContrastRatio: 0.25,
        minColorBalance: 0.5,
        minOverallQuality: 0.5,
        minDepthCoverage: 0.4
    )
    
    static let relaxed = QualityThresholds(
        minSNR: 12.0,
        minBlurVariance: 30.0,
        minContrastRatio: 0.2,
        minColorBalance: 0.4,
        minOverallQuality: 0.4,
        minDepthCoverage: 0.3
    )
    
    static var current: QualityThresholds {
        #if targetEnvironment(simulator)
        return .simulator
        #else
        return .standard
        #endif
    }
    
    init(minSNR: Double, minBlurVariance: Double, minContrastRatio: Double, minColorBalance: Double, minOverallQuality: Double, minDepthCoverage: Double) {
        self.minSNR = minSNR
        self.minBlurVariance = minBlurVariance
        self.minContrastRatio = minContrastRatio
        self.minColorBalance = minColorBalance
        self.minOverallQuality = minOverallQuality
        self.minDepthCoverage = minDepthCoverage
    }
    
    /// 根據圖像特徵動態調整門檻
    func adaptiveThresholds(for imageMetrics: QualityMetrics) -> QualityThresholds {
        var adjustedSNR = minSNR
        var adjustedBlur = minBlurVariance
        var adjustedContrast = minContrastRatio
        var adjustedColorBalance = minColorBalance
        var adjustedOverall = minOverallQuality
        var adjustedDepth = minDepthCoverage
        
        // 如果圖像整體品質較低，適當降低要求
        if imageMetrics.overallQuality < 0.6 {
            adjustedSNR *= 0.8
            adjustedBlur *= 0.7
            adjustedContrast *= 0.8
            adjustedColorBalance *= 0.8
            adjustedOverall *= 0.8
            adjustedDepth *= 0.7
        }
        
        // 如果深度資料品質差，降低深度相關要求
        if imageMetrics.depthCoverage < 0.5 {
            adjustedDepth *= 0.6
        }
        
        return QualityThresholds(
            minSNR: adjustedSNR,
            minBlurVariance: adjustedBlur,
            minContrastRatio: adjustedContrast,
            minColorBalance: adjustedColorBalance,
            minOverallQuality: adjustedOverall,
            minDepthCoverage: adjustedDepth
        )
    }
}

// MARK: - 錯誤類型
enum PreProcessingError: Error, LocalizedError {
    case invalidImage
    case processingFailed
    case filterCreationFailed
    case depthDataInvalid
    case roiDetectionFailed
    
    var errorDescription: String? {
        switch self {
        case .invalidImage:
            return "無效的圖像格式或損壞的圖像"
        case .processingFailed:
            return "圖像處理過程中發生錯誤"
        case .filterCreationFailed:
            return "無法創建圖像濾鏡"
        case .depthDataInvalid:
            return "深度數據無效或不可用"
        case .roiDetectionFailed:
            return "無法檢測到感興趣區域"
        }
    }
}

enum SmartROIError: Error {
    case invalidImage
    case noValidROI
    case detectionFailed
    case featureExtractionFailed
    case processingFailed
}

// MARK: - 擴展方法
extension WoundFeatures {
    static func empty() -> WoundFeatures {
        return WoundFeatures(
            area: 0.0,
            aspectRatio: 1.0,
            colorDistribution: ColorDistribution.empty(),
            textureHomogeneity: 0.5,
            textureContrast: 0.5,
            edgeRoughness: 0.5,
            symmetryIndex: 0.5,
            centroid: CGPoint.zero,
            boundingBox: CGRect.zero,
            perimeter: 0.0,
            circularity: 1.0,
            compactness: 1.0
        )
    }
}

extension ColorDistribution {
    static func empty() -> ColorDistribution {
        return ColorDistribution(
            redMean: 0.5,
            greenMean: 0.5,
            blueMean: 0.5,
            redStd: 0.1,
            greenStd: 0.1,
            blueStd: 0.1,
            saturation: 0.5,
            brightness: 0.5,
            contrast: 0.5
        )
    }
}

extension PredictedQuality {
    static func defaultQuality() -> PredictedQuality {
        return PredictedQuality(
            overallScore: 0.8,
            sharpnessScore: 0.8,
            lightingScore: 0.8,
            colorAccuracyScore: 0.8,
            noiseLevel: 0.2,
            confidenceInterval: (0.75, 0.85)
        )
    }
}

extension QualityMetrics {
    static func defaultMetrics() -> QualityMetrics {
        return QualityMetrics(
            snr: 25.0,
            blurVariance: 150.0,
            contrastRatio: 0.5,
            colorBalance: 0.8,
            overallQuality: 0.8,
            isAcceptable: true,
            blurLevel: 120.0,  // 提高預設模糊度值
            depthCoverage: 0.85
        )
    }
    
    // 新增：為模擬器環境提供更寬鬆的預設值
    static func simulatorMetrics() -> QualityMetrics {
        return QualityMetrics(
            snr: 18.0,  // 降低 SNR 要求
            blurVariance: 80.0,  // 降低模糊度要求
            contrastRatio: 0.4,  // 降低對比度要求
            colorBalance: 0.7,  // 降低色彩平衡要求
            overallQuality: 0.6,  // 降低整體品質要求
            isAcceptable: true,
            blurLevel: 60.0,  // 適中的模糊度
            depthCoverage: 0.5  // 降低深度覆蓋要求
        )
    }
}

// MARK: - 影像處理結構
struct SegmentedImage {
    let originalImage: UIImage
    let contours: [WoundContour]
}

struct WoundContour {
    let points: [CGPoint]
    let area: Double
    let perimeter: Double
}

// MARK: - 相機內參結構
struct CameraIntrinsics {
    let fx: Double      // X方向焦距 (像素單位)
    let fy: Double      // Y方向焦距 (像素單位) 
    let cx: Double      // 主點X座標 (像素單位)
    let cy: Double      // 主點Y座標 (像素單位)
    let fov: Double     // 視野角 (弧度)
    let imageWidth: Int // 圖像寬度
    let imageHeight: Int // 圖像高度
    
    // 預設iPhone相機參數 (基於iPhone 12 Pro)
    static let defaultiPhone = CameraIntrinsics(
        fx: 1400.0,  // 典型iPhone相機焦距
        fy: 1400.0,
        cx: 640.0,   // 圖像中心點
        cy: 480.0,
        fov: 1.047,  // 約60度視野角
        imageWidth: 1280,
        imageHeight: 960
    )
    
    // 計算像素在指定距離的實際尺寸
    func pixelSizeAtDistance(_ distance: Double) -> (width: Double, height: Double) {
        let pixelWidth = 2.0 * tan(fov / 2.0) * distance / Double(imageWidth)
        let pixelHeight = 2.0 * tan(fov / 2.0) * distance / Double(imageHeight)
        return (width: pixelWidth, height: pixelHeight)
    }
}

// MARK: - 深度品質指標
struct DepthQualityInfo {
    let validPixelRatio: Double    // 有效深度像素比例
    let averageConfidence: Double  // 平均信心值
    let depthConsistency: Double   // 深度一致性 (0-1)
    let noiseLevel: Double         // 噪聲水平
    let coverageInROI: Double      // ROI區域深度覆蓋率
    
    var isAcceptable: Bool {
        return validPixelRatio >= 0.8 && 
               averageConfidence >= 0.7 && 
               coverageInROI >= 0.8
    }
}

// MARK: - 測量結果結構
struct WoundMeasurement {
    let area: Double               // cm²
    let perimeter: Double          // cm
    let volume: Double             // cm³
    let maxDepth: Double           // cm
    let avgDepth: Double           // cm
    let length: Double             // cm
    let width: Double              // cm
    let tissueComposition: TissueComposition
    let qualityMetrics: QualityMetrics
    let depthQuality: DepthQualityInfo
    let cameraDistance: Double     // 拍攝距離 (cm)
    let pixelScale: Double         // 像素比例 (cm/pixel)
    let timestamp: Date
}

struct TissueComposition {
    let healthyPercentage: Double
    let granulationPercentage: Double
    let necroticPercentage: Double
    let epithelialPercentage: Double
    let fibrinPercentage: Double
    let sloughPercentage: Double
    
    init(
        healthyPercentage: Double = 0.0,
        granulationPercentage: Double = 0.6,
        necroticPercentage: Double = 0.2,
        epithelialPercentage: Double = 0.2,
        fibrinPercentage: Double = 0.0,
        sloughPercentage: Double = 0.0
    ) {
        self.healthyPercentage = healthyPercentage
        self.granulationPercentage = granulationPercentage
        self.necroticPercentage = necroticPercentage
        self.epithelialPercentage = epithelialPercentage
        self.fibrinPercentage = fibrinPercentage
        self.sloughPercentage = sloughPercentage
    }
}

// MARK: - 高級分析新增結構

/// 組織類型枚舉（與AdvancedMLClassificationModule同步）
enum TissueType: String, CaseIterable, Identifiable, Codable {
    case granulation = "granulation"      // 肉芽組織
    case necrotic = "necrotic"           // 壞死組織  
    case epithelial = "epithelial"       // 上皮組織
    case slough = "slough"               // 腐肉組織
    case healthySkin = "healthy_skin"    // 健康皮膚
    case exudate = "exudate"             // 滲出物
    
    var id: String { rawValue }
    
    var displayName: String {
        switch self {
        case .granulation: return "肉芽組織"
        case .necrotic: return "壞死組織"
        case .epithelial: return "上皮組織"
        case .slough: return "腐肉組織"
        case .healthySkin: return "健康皮膚"
        case .exudate: return "滲出物"
        }
    }
    
    var healthScore: Double {
        switch self {
        case .epithelial: return 1.0      // 最健康
        case .granulation: return 0.8     // 癒合中
        case .healthySkin: return 0.9     // 健康
        case .slough: return 0.4          // 需清創
        case .necrotic: return 0.1        // 需緊急處理
        case .exudate: return 0.6         // 感染風險
        }
    }
    
    var color: Color {
        switch self {
        case .granulation: return .red
        case .necrotic: return .black
        case .epithelial: return .pink
        case .slough: return .yellow
        case .healthySkin: return .brown
        case .exudate: return .green
        }
    }
}

/// 癒合階段枚舉
enum HealingStage: String, Codable, CaseIterable {
    case inflammatory = "inflammatory"     // 發炎階段
    case proliferative = "proliferative"  // 增殖階段
    case remodeling = "remodeling"        // 重塑階段
    case chronic = "chronic"              // 慢性不癒合
    case infected = "infected"            // 感染
    
    var displayName: String {
        switch self {
        case .inflammatory: return "發炎期"
        case .proliferative: return "增殖期"
        case .remodeling: return "重塑期"
        case .chronic: return "慢性期"
        case .infected: return "感染期"
        }
    }
    
    var color: Color {
        switch self {
        case .inflammatory: return .orange
        case .proliferative: return .green
        case .remodeling: return .blue
        case .chronic: return .gray
        case .infected: return .red
        }
    }
}

/// 傷口風險評估
struct WoundRiskAssessment: Codable {
    let infectionRisk: Double      // 感染風險 0-1
    let healingPrognosis: Double   // 癒合預後 0-1
    let treatmentUrgency: Double   // 治療緊急度 0-1
    let riskFactors: [String]      // 風險因子列表
    let recommendations: [String]   // 建議事項
    
    var overallRisk: RiskLevel {
        let averageRisk = (infectionRisk + (1.0 - healingPrognosis) + treatmentUrgency) / 3.0
        
        if averageRisk < 0.3 {
            return .low
        } else if averageRisk < 0.7 {
            return .medium
        } else {
            return .high
        }
    }
}

/// 風險等級
enum RiskLevel: String, Codable {
    case low = "low"
    case medium = "medium"
    case high = "high"
    
    var displayName: String {
        switch self {
        case .low: return "低風險"
        case .medium: return "中等風險"
        case .high: return "高風險"
        }
    }
    
    var color: Color {
        switch self {
        case .low: return .green
        case .medium: return .orange
        case .high: return .red
        }
    }
}

/// WoundType擴展，增加displayName屬性
extension WoundType {
    var displayName: String {
        return self.rawValue
    }
}

// MARK: - 測量結果結構

/// 傷口測量結果（用於與DataManager整合）
struct WoundMeasurementResult {
    let area: Double?
    let volume: Double?
    let perimeter: Double?
    let maxDepth: Double?
    let classification: DetailedWoundClassification?
    let qualityMetrics: QualityMetrics?
    let tissueComposition: TissueComposition?
    let originalImage: UIImage?
    let processedImage: UIImage?
    let depthData: Data?
    let timestamp: Date
    let error: String?
    let notes: String?
    let recommendations: [String]?
    
    init(area: Double? = nil, volume: Double? = nil, perimeter: Double? = nil,
         maxDepth: Double? = nil, classification: DetailedWoundClassification? = nil,
         qualityMetrics: QualityMetrics? = nil, tissueComposition: TissueComposition? = nil,
         originalImage: UIImage? = nil, processedImage: UIImage? = nil,
         depthData: Data? = nil, timestamp: Date = Date(), error: String? = nil,
         notes: String? = nil, recommendations: [String]? = nil) {
        self.area = area
        self.volume = volume
        self.perimeter = perimeter
        self.maxDepth = maxDepth
        self.classification = classification
        self.qualityMetrics = qualityMetrics
        self.tissueComposition = tissueComposition
        self.originalImage = originalImage
        self.processedImage = processedImage
        self.depthData = depthData
        self.timestamp = timestamp
        self.error = error
        self.notes = notes
        self.recommendations = recommendations
    }
}

/// 簡化的傷口分類（向後兼容）
struct WoundClassification {
    let acuteScore: Double
    let chronicScore: Double
    let confidence: Double
    let infectedScore: Double
    let healingScore: Double
    
    init(acuteScore: Double, chronicScore: Double, confidence: Double,
         infectedScore: Double = 0.0, healingScore: Double = 0.0) {
        self.acuteScore = acuteScore
        self.chronicScore = chronicScore
        self.confidence = confidence
        self.infectedScore = infectedScore
        self.healingScore = healingScore
    }
}

// MARK: - ML Error Types

/// 機器學習相關錯誤
enum MLError: LocalizedError {
    case modelNotLoaded(String)
    case imagePreprocessingFailed
    case invalidModelOutput
    case imageProcessingFailed
    case tensorProcessingFailed
    
    var errorDescription: String? {
        switch self {
        case .modelNotLoaded(let modelName):
            return "\(modelName)模型未正確載入"
        case .imagePreprocessingFailed:
            return "影像預處理失敗"
        case .invalidModelOutput:
            return "模型輸出格式錯誤"
        case .imageProcessingFailed:
            return "影像處理失敗"
        case .tensorProcessingFailed:
            return "張量處理失敗"
        }
    }
}

// MARK: - 校正相關結構

/// 檢測到的圓形結構（用於校正貼紙檢測）
struct DetectedCircle {
    let center: CGPoint
    let diameter: CGFloat
    let confidence: CGFloat
    
    // 兼容性屬性，支持radius形式
    var radius: Double {
        return Double(diameter) / 2.0
    }
    
    // 構造函數，支持radius形式
    init(center: CGPoint, radius: Double, confidence: Double) {
        self.center = center
        self.diameter = CGFloat(radius * 2.0)
        self.confidence = CGFloat(confidence)
    }
    
    init(center: CGPoint, diameter: CGFloat, confidence: CGFloat) {
        self.center = center
        self.diameter = diameter
        self.confidence = confidence
    }
}

// MARK: - Advanced ML Structure Definitions

/// 組織區域結構
struct TissueRegion {
    let type: TissueType
    let mask: UIImage
    let area: Double
    let percentage: Double
    let confidence: Double
    let characteristics: TissueCharacteristics
}

/// 組織特徵
struct TissueCharacteristics {
    let color: TissueColor
    let texture: TextureAnalysis
    let depth: Double?
    let vascularity: VascularityLevel
    let healthScore: Double
}

/// 組織顏色
enum TissueColor: String, Codable {
    case red = "red"
    case black = "black"
    case pink = "pink"
    case yellow = "yellow"
    case white = "white"
    case green = "green"
}

/// 血管化程度
enum VascularityLevel: String, Codable {
    case none = "none"
    case minimal = "minimal"
    case moderate = "moderate"
    case abundant = "abundant"
}

/// 紋理分析
struct TextureAnalysis {
    let entropy: Double
    let contrast: Double
    let homogeneity: Double
    let roughness: Double
}

/// 組織類型分析結果
struct TissueTypeAnalysis {
    let granulationTissue: TissueRegion?
    let necroticTissue: TissueRegion?
    let epithelialTissue: TissueRegion?
    let sloughTissue: TissueRegion?
    let healthySkin: TissueRegion?
    let totalWoundArea: Double
    let tissueDistribution: [TissueType: Double]
    let healingStage: HealingStage
    let riskAssessment: WoundRiskAssessment
}

/// 信心度評估指標
struct ConfidenceMetrics {
    let overallConfidence: Double
    let modelAgreement: Double
    let segmentationQuality: Double
    let tissueClassificationConfidence: [TissueType: Double]
    let uncertaintyAreas: [CGRect]
    let recommendedAction: RecommendedAction
}

/// 建議動作
enum RecommendedAction: String, Codable {
    case acceptResult = "accept"
    case reviewResult = "review"
    case retakeImage = "retake"
}

/// 傷口分割結果
struct WoundSegmentationResult {
    let segmentationMask: UIImage
    let woundBoundary: [CGPoint]
    let woundArea: Double
    let boundingBox: CGRect
    let confidence: Double
    let modelName: String
    let tissueRegions: [TissueRegion]
}

/// 共識分析結果
struct ConsensusAnalysis {
    let agreedBoundary: [CGPoint]
    let disagreementAreas: [CGRect]
    let finalSegmentation: UIImage
    let consensusConfidence: Double
    let conflictResolution: ConflictResolutionMethod
}

/// 衝突解決方法
enum ConflictResolutionMethod: String, Codable {
    case preferPrimary = "prefer_primary"
    case preferSecondary = "prefer_secondary"
    case weightedAverage = "weighted_average"
    case intersectionOnly = "intersection_only"
}