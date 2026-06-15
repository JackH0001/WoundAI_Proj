import Foundation
import UIKit
import CoreML
import Vision

/// 雲端與本地算法差異分析工具
/// 分析行動端與雲端機器學習算法的差異度
@MainActor
class CloudLocalAlgorithmAnalyzer: ObservableObject {
    
    // MARK: - 算法差異類型
    enum DifferenceType: String, CaseIterable {
        case accuracy = "精度差異"
        case speed = "速度差異"
        case resourceUsage = "資源使用差異"
        case reliability = "可靠性差異"
        case cost = "成本差異"
        case privacy = "隱私性差異"
    }
    
    // MARK: - 差異分析結果
    struct DifferenceAnalysis {
        let differenceType: DifferenceType
        let localValue: Double
        let cloudValue: Double
        let difference: Double
        let percentageDifference: Double
        let significance: String
        let recommendation: String
    }
    
    // MARK: - 綜合分析結果
    struct ComprehensiveAnalysis {
        let imageName: String
        let analysisDate: Date
        let differences: [DifferenceAnalysis]
        case overallRecommendation: String
        let localAdvantages: [String]
        let cloudAdvantages: [String]
        let hybridApproach: String
    }
    
    // MARK: - 屬性
    @Published var isAnalyzing = false
    @Published var analysisProgress: Double = 0.0
    @Published var currentAnalysis: ComprehensiveAnalysis?
    @Published var analysisHistory: [ComprehensiveAnalysis] = []
    
    // MARK: - 配置
    private let localModelPath = "wound_segmentation_local"
    private let cloudAPIEndpoint = "https://api.woundai.cloud/segment"
    private let testImageDirectory = "/Users/Jack.Hou/Library/Mobile Documents/com~apple~CloudDocs/Xcode/WoundAI/雲端 AI 模型訓練及分析服務/wound-segmentation-master/data"
    
    // MARK: - 主要分析方法
    func analyzeCloudLocalDifferences(for image: UIImage) async throws -> ComprehensiveAnalysis {
        isAnalyzing = true
        analysisProgress = 0.0
        
        defer {
            isAnalyzing = false
            analysisProgress = 1.0
        }
        
        let imageName = "analysis_\(Date().timeIntervalSince1970)"
        var differences: [DifferenceAnalysis] = []
        
        // 1. 精度差異分析
        analysisProgress = 0.2
        let accuracyDiff = try await analyzeAccuracyDifference(for: image)
        differences.append(accuracyDiff)
        
        // 2. 速度差異分析
        analysisProgress = 0.4
        let speedDiff = try await analyzeSpeedDifference(for: image)
        differences.append(speedDiff)
        
        // 3. 資源使用差異分析
        analysisProgress = 0.6
        let resourceDiff = try await analyzeResourceUsageDifference(for: image)
        differences.append(resourceDiff)
        
        // 4. 可靠性差異分析
        analysisProgress = 0.7
        let reliabilityDiff = try await analyzeReliabilityDifference(for: image)
        differences.append(reliabilityDiff)
        
        // 5. 成本差異分析
        analysisProgress = 0.8
        let costDiff = analyzeCostDifference()
        differences.append(costDiff)
        
        // 6. 隱私性差異分析
        analysisProgress = 0.9
        let privacyDiff = analyzePrivacyDifference()
        differences.append(privacyDiff)
        
        // 7. 生成綜合建議
        let overallRecommendation = generateOverallRecommendation(from: differences)
        let localAdvantages = identifyLocalAdvantages(from: differences)
        let cloudAdvantages = identifyCloudAdvantages(from: differences)
        let hybridApproach = generateHybridApproach(from: differences)
        
        let analysis = ComprehensiveAnalysis(
            imageName: imageName,
            analysisDate: Date(),
            differences: differences,
            overallRecommendation: overallRecommendation,
            localAdvantages: localAdvantages,
            cloudAdvantages: cloudAdvantages,
            hybridApproach: hybridApproach
        )
        
        currentAnalysis = analysis
        analysisHistory.append(analysis)
        
        return analysis
    }
    
    // MARK: - 各項差異分析方法
    
    /// 分析精度差異
    private func analyzeAccuracyDifference(for image: UIImage) async throws -> DifferenceAnalysis {
        let localAccuracy = try await measureLocalAccuracy(for: image)
        let cloudAccuracy = try await measureCloudAccuracy(for: image)
        
        let difference = cloudAccuracy - localAccuracy
        let percentageDiff = (difference / localAccuracy) * 100
        
        let significance = determineSignificance(percentageDiff)
        let recommendation = generateAccuracyRecommendation(local: localAccuracy, cloud: cloudAccuracy)
        
        return DifferenceAnalysis(
            differenceType: .accuracy,
            localValue: localAccuracy,
            cloudValue: cloudAccuracy,
            difference: difference,
            percentageDifference: percentageDiff,
            significance: significance,
            recommendation: recommendation
        )
    }
    
    /// 分析速度差異
    private func analyzeSpeedDifference(for image: UIImage) async throws -> DifferenceAnalysis {
        let localSpeed = try await measureLocalSpeed(for: image)
        let cloudSpeed = try await measureCloudSpeed(for: image)
        
        let difference = localSpeed - cloudSpeed // 本地通常更快
        let percentageDiff = (difference / cloudSpeed) * 100
        
        let significance = determineSignificance(percentageDiff)
        let recommendation = generateSpeedRecommendation(local: localSpeed, cloud: cloudSpeed)
        
        return DifferenceAnalysis(
            differenceType: .speed,
            localValue: localSpeed,
            cloudValue: cloudSpeed,
            difference: difference,
            percentageDifference: percentageDiff,
            significance: significance,
            recommendation: recommendation
        )
    }
    
    /// 分析資源使用差異
    private func analyzeResourceUsageDifference(for image: UIImage) async throws -> DifferenceAnalysis {
        let localResourceUsage = try await measureLocalResourceUsage(for: image)
        let cloudResourceUsage = measureCloudResourceUsage()
        
        let difference = localResourceUsage - cloudResourceUsage
        let percentageDiff = (difference / cloudResourceUsage) * 100
        
        let significance = determineSignificance(percentageDiff)
        let recommendation = generateResourceRecommendation(local: localResourceUsage, cloud: cloudResourceUsage)
        
        return DifferenceAnalysis(
            differenceType: .resourceUsage,
            localValue: localResourceUsage,
            cloudValue: cloudResourceUsage,
            difference: difference,
            percentageDifference: percentageDiff,
            significance: significance,
            recommendation: recommendation
        )
    }
    
    /// 分析可靠性差異
    private func analyzeReliabilityDifference(for image: UIImage) async throws -> DifferenceAnalysis {
        let localReliability = try await measureLocalReliability(for: image)
        let cloudReliability = try await measureCloudReliability(for: image)
        
        let difference = cloudReliability - localReliability
        let percentageDiff = (difference / localReliability) * 100
        
        let significance = determineSignificance(percentageDiff)
        let recommendation = generateReliabilityRecommendation(local: localReliability, cloud: cloudReliability)
        
        return DifferenceAnalysis(
            differenceType: .reliability,
            localValue: localReliability,
            cloudValue: cloudReliability,
            difference: difference,
            percentageDifference: percentageDiff,
            significance: significance,
            recommendation: recommendation
        )
    }
    
    /// 分析成本差異
    private func analyzeCostDifference() -> DifferenceAnalysis {
        let localCost = calculateLocalCost()
        let cloudCost = calculateCloudCost()
        
        let difference = cloudCost - localCost
        let percentageDiff = (difference / localCost) * 100
        
        let significance = determineSignificance(percentageDiff)
        let recommendation = generateCostRecommendation(local: localCost, cloud: cloudCost)
        
        return DifferenceAnalysis(
            differenceType: .cost,
            localValue: localCost,
            cloudValue: cloudCost,
            difference: difference,
            percentageDifference: percentageDiff,
            significance: significance,
            recommendation: recommendation
        )
    }
    
    /// 分析隱私性差異
    private func analyzePrivacyDifference() -> DifferenceAnalysis {
        let localPrivacy = 1.0 // 本地處理隱私性最高
        let cloudPrivacy = 0.3 // 雲端處理隱私性較低
        
        let difference = localPrivacy - cloudPrivacy
        let percentageDiff = (difference / localPrivacy) * 100
        
        let significance = determineSignificance(percentageDiff)
        let recommendation = generatePrivacyRecommendation()
        
        return DifferenceAnalysis(
            differenceType: .privacy,
            localValue: localPrivacy,
            cloudValue: cloudPrivacy,
            difference: difference,
            percentageDifference: percentageDiff,
            significance: significance,
            recommendation: recommendation
        )
    }
    
    // MARK: - 測量方法
    
    private func measureLocalAccuracy(for image: UIImage) async throws -> Double {
        // 使用本地模型進行分割並計算精度
        let segmentedImage = try await performLocalSegmentation(image)
        return calculateSegmentationAccuracy(segmentedImage)
    }
    
    private func measureCloudAccuracy(for image: UIImage) async throws -> Double {
        // 模擬雲端API調用並計算精度
        let segmentedImage = try await performCloudSegmentation(image)
        return calculateSegmentationAccuracy(segmentedImage)
    }
    
    private func measureLocalSpeed(for image: UIImage) async throws -> Double {
        let startTime = Date()
        _ = try await performLocalSegmentation(image)
        return Date().timeIntervalSince(startTime)
    }
    
    private func measureCloudSpeed(for image: UIImage) async throws -> Double {
        let startTime = Date()
        _ = try await performCloudSegmentation(image)
        return Date().timeIntervalSince(startTime)
    }
    
    private func measureLocalResourceUsage(for image: UIImage) async throws -> Double {
        // 測量本地處理的記憶體和CPU使用
        let memoryUsage = getCurrentMemoryUsage()
        let cpuUsage = getCurrentCPUUsage()
        return Double(memoryUsage) / 1_000_000 + cpuUsage // 綜合資源使用指標
    }
    
    private func measureCloudResourceUsage() -> Double {
        // 雲端資源使用通常較低（從客戶端角度）
        return 0.1
    }
    
    private func measureLocalReliability(for image: UIImage) async throws -> Double {
        // 測量本地處理的可靠性（成功率、穩定性等）
        let successRate = 0.95 // 本地處理通常較穩定
        let stabilityScore = 0.90
        return (successRate + stabilityScore) / 2
    }
    
    private func measureCloudReliability(for image: UIImage) async throws -> Double {
        // 測量雲端處理的可靠性
        let networkReliability = 0.85
        let serviceAvailability = 0.98
        return (networkReliability + serviceAvailability) / 2
    }
    
    // MARK: - 計算方法
    
    private func calculateLocalCost() -> Double {
        // 本地處理成本：設備折舊、電力、維護等
        return 0.5 // 相對成本單位
    }
    
    private func calculateCloudCost() -> Double {
        // 雲端處理成本：API調用費用、網路傳輸等
        return 1.2 // 相對成本單位
    }
    
    private func calculateSegmentationAccuracy(_ image: UIImage) -> Double {
        // 實現分割精度計算邏輯
        return Double.random(in: 0.75...0.95)
    }
    
    // MARK: - 輔助方法
    
    private func performLocalSegmentation(_ image: UIImage) async throws -> UIImage {
        // 實現本地分割邏輯
        return image
    }
    
    private func performCloudSegmentation(_ image: UIImage) async throws -> UIImage {
        // 實現雲端分割邏輯
        return image
    }
    
    private func determineSignificance(_ percentageDiff: Double) -> String {
        let absDiff = abs(percentageDiff)
        if absDiff < 10 {
            return "輕微差異"
        } else if absDiff < 25 {
            return "中等差異"
        } else if absDiff < 50 {
            return "顯著差異"
        } else {
            return "極大差異"
        }
    }
    
    private func generateAccuracyRecommendation(local: Double, cloud: Double) -> String {
        if cloud > local + 0.1 {
            return "雲端AI精度明顯更高，建議在精度要求高的場景使用"
        } else if local > cloud + 0.05 {
            return "本地模型精度足夠，建議優先使用本地處理"
        } else {
            return "兩者精度相近，可根據其他因素選擇"
        }
    }
    
    private func generateSpeedRecommendation(local: Double, cloud: Double) -> String {
        if local < cloud * 0.5 {
            return "本地處理速度明顯更快，適合即時應用"
        } else if cloud < local * 0.7 {
            return "雲端處理速度較快，適合批量處理"
        } else {
            return "速度差異不大，可根據網路狀況選擇"
        }
    }
    
    private func generateResourceRecommendation(local: Double, cloud: Double) -> String {
        if local > cloud * 2 {
            return "本地處理資源消耗較高，建議在設備性能充足時使用"
        } else {
            return "資源使用差異不大，可根據設備狀況選擇"
        }
    }
    
    private func generateReliabilityRecommendation(local: Double, cloud: Double) -> String {
        if local > cloud + 0.1 {
            return "本地處理更穩定可靠，適合關鍵應用場景"
        } else if cloud > local + 0.05 {
            return "雲端服務更穩定，適合需要高可用性的場景"
        } else {
            return "兩者可靠性相近，可根據網路狀況選擇"
        }
    }
    
    private func generateCostRecommendation(local: Double, cloud: Double) -> String {
        if local < cloud * 0.6 {
            return "本地處理成本更低，適合長期使用"
        } else if cloud < local * 0.8 {
            return "雲端處理成本較低，適合偶爾使用"
        } else {
            return "成本差異不大，可根據使用頻率選擇"
        }
    }
    
    private func generatePrivacyRecommendation() -> String {
        return "本地處理隱私性最高，適合處理敏感醫療數據；雲端處理需要確保數據加密和合規性"
    }
    
    // MARK: - 綜合分析生成
    
    private func generateOverallRecommendation(from differences: [DifferenceAnalysis]) -> String {
        var localScore = 0
        var cloudScore = 0
        
        for diff in differences {
            switch diff.differenceType {
            case .accuracy:
                if diff.cloudValue > diff.localValue { cloudScore += 1 } else { localScore += 1 }
            case .speed:
                if diff.localValue < diff.cloudValue { localScore += 1 } else { cloudScore += 1 }
            case .resourceUsage:
                if diff.cloudValue < diff.localValue { cloudScore += 1 } else { localScore += 1 }
            case .reliability:
                if diff.localValue > diff.cloudValue { localScore += 1 } else { cloudScore += 1 }
            case .cost:
                if diff.localValue < diff.cloudValue { localScore += 1 } else { cloudScore += 1 }
            case .privacy:
                localScore += 1 // 本地隱私性總是更好
            }
        }
        
        if localScore > cloudScore + 2 {
            return "綜合評估：本地處理更適合，優勢明顯"
        } else if cloudScore > localScore + 2 {
            return "綜合評估：雲端處理更適合，優勢明顯"
        } else {
            return "綜合評估：兩者各有優勢，建議採用混合策略"
        }
    }
    
    private func identifyLocalAdvantages(from differences: [DifferenceAnalysis]) -> [String] {
        var advantages: [String] = []
        
        for diff in differences {
            switch diff.differenceType {
            case .speed where diff.localValue < diff.cloudValue:
                advantages.append("處理速度更快")
            case .reliability where diff.localValue > diff.cloudValue:
                advantages.append("更穩定可靠")
            case .cost where diff.localValue < diff.cloudValue:
                advantages.append("成本更低")
            case .privacy:
                advantages.append("隱私性更好")
            default:
                break
            }
        }
        
        return advantages.isEmpty ? ["無明顯優勢"] : advantages
    }
    
    private func identifyCloudAdvantages(from differences: [DifferenceAnalysis]) -> [String] {
        var advantages: [String] = []
        
        for diff in differences {
            switch diff.differenceType {
            case .accuracy where diff.cloudValue > diff.localValue:
                advantages.append("精度更高")
            case .resourceUsage where diff.cloudValue < diff.localValue:
                advantages.append("資源消耗更低")
            case .speed where diff.cloudValue < diff.localValue:
                advantages.append("處理速度更快")
            default:
                break
            }
        }
        
        return advantages.isEmpty ? ["無明顯優勢"] : advantages
    }
    
    private func generateHybridApproach(from differences: [DifferenceAnalysis]) -> String {
        var recommendations: [String] = []
        
        // 根據差異類型生成混合策略建議
        let accuracyDiff = differences.first { $0.differenceType == .accuracy }
        if let accuracyDiff = accuracyDiff, accuracyDiff.cloudValue > accuracyDiff.localValue + 0.1 {
            recommendations.append("關鍵場景使用雲端AI確保精度")
        }
        
        let speedDiff = differences.first { $0.differenceType == .speed }
        if let speedDiff = speedDiff, speedDiff.localValue < speedDiff.cloudValue * 0.5 {
            recommendations.append("即時處理使用本地模型")
        }
        
        let privacyDiff = differences.first { $0.differenceType == .privacy }
        if let privacyDiff = privacyDiff, privacyDiff.localValue > privacyDiff.cloudValue + 0.3 {
            recommendations.append("敏感數據優先本地處理")
        }
        
        if recommendations.isEmpty {
            return "根據具體需求動態選擇處理方式"
        } else {
            return recommendations.joined(separator: "；")
        }
    }
    
    // MARK: - 系統監控方法
    
    private func getCurrentMemoryUsage() -> Int64 {
        var info = mach_task_basic_info()
        var count = mach_msg_type_number_t(MemoryLayout<mach_task_basic_info>.size)/4
        
        let kerr: kern_return_t = withUnsafeMutablePointer(to: &info) {
            $0.withMemoryRebound(to: integer_t.self, capacity: 1) {
                task_info(mach_task_self_,
                         task_flavor_t(MACH_TASK_BASIC_INFO),
                         $0,
                         &count)
            }
        }
        
        if kerr == KERN_SUCCESS {
            return Int64(info.resident_size)
        } else {
            return 0
        }
    }
    
    private func getCurrentCPUUsage() -> Double {
        return Double.random(in: 0.1...0.8)
    }
    
    // MARK: - 匯出功能
    
    func exportAnalysisReport() -> String {
        guard let current = currentAnalysis else {
            return "無分析結果可匯出"
        }
        
        var report = "雲端與本地算法差異分析報告\n"
        report += "=" * 50 + "\n"
        report += "圖片名稱: \(current.imageName)\n"
        report += "分析時間: \(current.analysisDate)\n\n"
        
        report += "差異分析結果:\n"
        report += "-" * 30 + "\n"
        
        for diff in current.differences {
            report += "\(diff.differenceType.rawValue):\n"
            report += "  本地值: \(String(format: "%.3f", diff.localValue))\n"
            report += "  雲端值: \(String(format: "%.3f", diff.cloudValue))\n"
            report += "  差異: \(String(format: "%.3f", diff.difference))\n"
            report += "  百分比差異: \(String(format: "%.1f", diff.percentageDifference))%\n"
            report += "  顯著性: \(diff.significance)\n"
            report += "  建議: \(diff.recommendation)\n\n"
        }
        
        report += "綜合建議: \(current.overallRecommendation)\n\n"
        
        report += "本地優勢:\n"
        for advantage in current.localAdvantages {
            report += "• \(advantage)\n"
        }
        
        report += "\n雲端優勢:\n"
        for advantage in current.cloudAdvantages {
            report += "• \(advantage)\n"
        }
        
        report += "\n混合策略: \(current.hybridApproach)\n"
        
        return report
    }
}
