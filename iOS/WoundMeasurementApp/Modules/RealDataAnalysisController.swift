import Foundation
import UIKit
import CoreImage
import Vision
import os.log

/// 真實數據分析控制器 - 執行與雲端已知結果的完整比對分析
@MainActor 
class RealDataAnalysisController: ObservableObject {
    
    // MARK: - Published Properties
    
    @Published var analysisState: AnalysisState = .idle
    @Published var analysisProgress: Double = 0.0
    @Published var currentAnalysisResult: RealDataAnalysisResult?
    @Published var analysisHistory: [AnalysisHistoryRecord] = []
    @Published var differenceAnalysisResults: [DifferenceAnalysisResult] = []
    @Published var optimizationSuggestions: [OptimizationSuggestion] = []
    
    // MARK: - Private Properties
    
    private let realDataComparator: RealDataComparator
    private let differenceAnalyzer: DifferenceAnalyzer
    private let optimizationRecommender: OptimizationRecommender
    private let logger = os.Logger(subsystem: "WoundMeasurementApp", category: "RealDataAnalysis")
    
    init() {
        self.realDataComparator = RealDataComparator()
        self.differenceAnalyzer = DifferenceAnalyzer()
        self.optimizationRecommender = OptimizationRecommender()
        
        setupAnalysisPipeline()
    }
    
    // MARK: - 主要分析方法
    
    /// 執行完整的真實數據比對分析
    func executeRealDataAnalysis(sampleCount: Int = 20) async throws -> ComprehensiveAnalysisResult {
        logger.info("開始執行真實數據分析，樣本數量: \(sampleCount)")
        
        analysisState = .initializing
        analysisProgress = 0.0
        
        do {
            // 階段1: 執行真實數據比對 (60%)
            analysisState = .comparingWithGroundTruth
            let realDataResult = try await realDataComparator.performRealDataComparison(sampleCount: sampleCount)
            currentAnalysisResult = realDataResult
            analysisProgress = 0.6
            
            // 階段2: 深度差異分析 (25%)
            analysisState = .analyzingDifferences
            let differenceResults = try await performDeepDifferenceAnalysis(realDataResult)
            differenceAnalysisResults = differenceResults
            analysisProgress = 0.85
            
            // 階段3: 生成優化建議 (15%)
            analysisState = .generatingOptimizations
            let optimizations = try await generateComprehensiveOptimizations(
                realDataResult: realDataResult,
                differenceResults: differenceResults
            )
            optimizationSuggestions = optimizations
            analysisProgress = 1.0
            
            // 生成綜合分析報告
            let comprehensiveResult = ComprehensiveAnalysisResult(
                realDataAnalysis: realDataResult,
                differenceAnalysis: differenceResults,
                optimizationSuggestions: optimizations,
                analysisTimestamp: Date(),
                executionSummary: generateExecutionSummary(realDataResult, differenceResults, optimizations)
            )
            
            // 添加到歷史記錄
            addToHistory(comprehensiveResult)
            
            analysisState = .completed
            
            // 輸出詳細結果到Console
            await outputDetailedResults(comprehensiveResult)
            
            return comprehensiveResult
            
        } catch {
            analysisState = .failed(error)
            logger.error("真實數據分析失敗: \(error.localizedDescription)")
            throw error
        }
    }
    
    // MARK: - 深度差異分析
    
    /// 執行深度差異分析
    private func performDeepDifferenceAnalysis(_ realDataResult: RealDataAnalysisResult) async throws -> [DifferenceAnalysisResult] {
        logger.info("執行深度差異分析...")
        
        var analysisResults: [DifferenceAnalysisResult] = []
        
        // 1. 分割準確度差異分析
        let segmentationAnalysis = try await analyzeSegmentationDifferences(realDataResult.comparisonResults)
        analysisResults.append(segmentationAnalysis)
        
        // 2. 測量準確度差異分析
        let measurementAnalysis = try await analyzeMeasurementDifferences(realDataResult.comparisonResults)
        analysisResults.append(measurementAnalysis)
        
        // 3. 處理性能差異分析
        let performanceAnalysis = try await analyzePerformanceDifferences(realDataResult.comparisonResults)
        analysisResults.append(performanceAnalysis)
        
        // 4. 一致性差異分析
        let consistencyAnalysis = try await analyzeConsistencyDifferences(realDataResult.comparisonResults)
        analysisResults.append(consistencyAnalysis)
        
        return analysisResults
    }
    
    /// 分析分割準確度差異
    private func analyzeSegmentationDifferences(_ results: [ImageComparisonResult]) async throws -> DifferenceAnalysisResult {
        logger.info("分析分割準確度差異...")
        
        let diceScores = results.map { $0.segmentationComparison.diceScore }
        let iouScores = results.map { $0.segmentationComparison.iouScore }
        
        // 識別表現不佳的案例
        let poorPerformanceCases = results.enumerated().compactMap { (index, result) -> PoorPerformanceCase? in
            if result.segmentationComparison.diceScore < 0.8 {
                return PoorPerformanceCase(
                    imageName: result.imageName,
                    issueType: .lowSegmentationAccuracy,
                    metric: result.segmentationComparison.diceScore,
                    expectedMetric: 0.85,
                    rootCause: identifySegmentationRootCause(result.segmentationComparison)
                )
            }
            return nil
        }
        
        // 分析差異模式
        let patterns = identifySegmentationPatterns(results)
        
        // 生成改善建議
        let improvements = generateSegmentationImprovements(poorPerformanceCases, patterns)
        
        return DifferenceAnalysisResult(
            analysisType: .segmentationAccuracy,
            overallScore: diceScores.average,
            standardDeviation: diceScores.standardDeviation,
            poorPerformanceCases: poorPerformanceCases,
            identifiedPatterns: patterns,
            rootCauses: extractRootCauses(poorPerformanceCases),
            improvementSuggestions: improvements,
            priorityLevel: calculatePriorityLevel(diceScores.average, target: 0.90)
        )
    }
    
    /// 分析測量準確度差異
    private func analyzeMeasurementDifferences(_ results: [ImageComparisonResult]) async throws -> DifferenceAnalysisResult {
        logger.info("分析測量準確度差異...")
        
        let areaAccuracies = results.map { $0.measurementComparison.areaAccuracy }
        let perimeterAccuracies = results.map { $0.measurementComparison.perimeterAccuracy }
        
        // 識別測量誤差較大的案例
        let poorPerformanceCases = results.enumerated().compactMap { (index, result) -> PoorPerformanceCase? in
            let measurement = result.measurementComparison
            if measurement.relativeAreaError > 0.15 || measurement.relativePerimeterError > 0.20 {
                return PoorPerformanceCase(
                    imageName: result.imageName,
                    issueType: .measurementInaccuracy,
                    metric: min(measurement.areaAccuracy, measurement.perimeterAccuracy),
                    expectedMetric: 0.90,
                    rootCause: identifyMeasurementRootCause(measurement)
                )
            }
            return nil
        }
        
        let patterns = identifyMeasurementPatterns(results)
        let improvements = generateMeasurementImprovements(poorPerformanceCases, patterns)
        
        return DifferenceAnalysisResult(
            analysisType: .measurementAccuracy,
            overallScore: (areaAccuracies.average + perimeterAccuracies.average) / 2.0,
            standardDeviation: areaAccuracies.standardDeviation,
            poorPerformanceCases: poorPerformanceCases,
            identifiedPatterns: patterns,
            rootCauses: extractRootCauses(poorPerformanceCases),
            improvementSuggestions: improvements,
            priorityLevel: calculatePriorityLevel((areaAccuracies.average + perimeterAccuracies.average) / 2.0, target: 0.92)
        )
    }
    
    // MARK: - 優化建議生成
    
    /// 生成綜合優化建議
    private func generateComprehensiveOptimizations(realDataResult: RealDataAnalysisResult, 
                                                  differenceResults: [DifferenceAnalysisResult]) async throws -> [OptimizationSuggestion] {
        logger.info("生成綜合優化建議...")
        
        var suggestions: [OptimizationSuggestion] = []
        
        // 基於整體統計的建議
        let overallStats = realDataResult.overallAnalysis.overallStatistics
        if overallStats.avgDiceScore < 0.90 {
            suggestions.append(OptimizationSuggestion(
                category: .algorithmImprovement,
                priority: .high,
                title: "提升分割演算法準確度",
                description: "目前Dice Score平均值為 \(String(format: "%.3f", overallStats.avgDiceScore))，低於醫療級標準0.90",
                specificRecommendations: [
                    "調整分割閾值參數，從當前設定優化至更精確的範圍",
                    "增強邊緣檢測演算法，特別針對模糊邊界的處理",
                    "實施多尺度分割策略，結合粗細粒度的分析",
                    "加強後處理步驟，移除小面積噪點和填補空洞"
                ],
                expectedImprovement: 0.08,
                implementationComplexity: .medium,
                estimatedEffort: "2-3週"
            ))
        }
        
        // 基於差異分析的建議
        for differenceResult in differenceResults {
            let categoryOptimizations = generateOptimizationsForCategory(differenceResult)
            suggestions.append(contentsOf: categoryOptimizations)
        }
        
        // 基於一致性問題的建議
        if overallStats.consistencyIndex < 0.80 {
            suggestions.append(OptimizationSuggestion(
                category: .consistencyImprovement,
                priority: .high,
                title: "提升演算法一致性",
                description: "演算法在不同圖像上的表現差異較大，一致性指數為 \(String(format: "%.3f", overallStats.consistencyIndex))",
                specificRecommendations: [
                    "標準化圖像前處理流程，確保輸入的一致性",
                    "實施品質檢查機制，對低品質輸入進行預警",
                    "調整演算法參數以減少對圖像變異的敏感度",
                    "增加魯棒性測試，涵蓋更多樣化的輸入場景"
                ],
                expectedImprovement: 0.12,
                implementationComplexity: .high,
                estimatedEffort: "3-4週"
            ))
        }
        
        // 基於性能的建議
        if overallStats.avgProcessingTime > 3.0 {
            suggestions.append(OptimizationSuggestion(
                category: .performanceOptimization,
                priority: .medium,
                title: "優化處理速度",
                description: "平均處理時間為 \(String(format: "%.2f", overallStats.avgProcessingTime))秒，超過目標3秒",
                specificRecommendations: [
                    "優化圖像縮放策略，降低不必要的解析度",
                    "實施並行處理，同步執行獨立的計算步驟",
                    "優化記憶體使用，減少重複的圖像拷貝操作",
                    "使用更高效的數據結構和算法實現"
                ],
                expectedImprovement: 0.25,
                implementationComplexity: .medium,
                estimatedEffort: "1-2週"
            ))
        }
        
        return suggestions.sorted { $0.priority.rawValue > $1.priority.rawValue }
    }
    
    // MARK: - 結果輸出方法
    
    /// 輸出詳細分析結果到Console
    private func outputDetailedResults(_ result: ComprehensiveAnalysisResult) async {
        let report = generateDetailedReport(result)
        print("\n" + "="*80)
        print("📊 iOS App 圖像計算與雲端數據比對分析報告")
        print("="*80)
        print(report)
        print("="*80)
    }
    
    /// 生成詳細報告
    private func generateDetailedReport(_ result: ComprehensiveAnalysisResult) -> String {
        var report = ""
        
        let stats = result.realDataAnalysis.overallAnalysis.overallStatistics
        
        // 1. 執行摘要
        report += "\n🎯 執行摘要\n"
        report += "分析時間: \(result.analysisTimestamp.formatted())\n"
        report += "樣本數量: \(result.realDataAnalysis.totalSamples)\n"
        report += "整體準確度: \(String(format: "%.2f%%", stats.avgOverallAccuracy * 100))\n"
        
        // 2. 關鍵指標對比
        report += "\n📈 關鍵指標對比\n"
        report += String(format: "Dice Score:      %.3f ± %.3f (目標: ≥0.900)\n", stats.avgDiceScore, stats.stdDiceScore)
        report += String(format: "IoU Score:       %.3f ± %.3f (目標: ≥0.850)\n", stats.avgIouScore, stats.stdIouScore) 
        report += String(format: "面積準確度:      %.3f ± %.3f (目標: ≥0.920)\n", stats.avgAreaAccuracy, stats.stdAreaAccuracy)
        report += String(format: "周長準確度:      %.3f ± %.3f (目標: ≥0.880)\n", stats.avgPerimeterAccuracy, stats.stdPerimeterAccuracy)
        report += String(format: "處理時間:        %.2f ± %.2f 秒\n", stats.avgProcessingTime, stats.stdProcessingTime)
        report += String(format: "一致性指數:      %.3f (目標: ≥0.800)\n", stats.consistencyIndex)
        
        // 3. 醫療級評估
        report += "\n🏥 醫療級評估\n"
        report += "達到90%準確度的樣本: \(stats.samplesAbove90Percent)/\(result.realDataAnalysis.totalSamples) (\(String(format: "%.1f%%", Double(stats.samplesAbove90Percent)/Double(result.realDataAnalysis.totalSamples)*100)))\n"
        report += "達到95%準確度的樣本: \(stats.samplesAbove95Percent)/\(result.realDataAnalysis.totalSamples) (\(String(format: "%.1f%%", Double(stats.samplesAbove95Percent)/Double(result.realDataAnalysis.totalSamples)*100)))\n"
        
        let medicalGradeStatus = determineMedicalGradeStatus(stats)
        report += "醫療級評級: \(medicalGradeStatus.level) (\(medicalGradeStatus.description))\n"
        
        // 4. 主要差異分析
        report += "\n🔍 主要差異分析\n"
        for (index, differenceResult) in result.differenceAnalysis.enumerated() {
            report += "\(index + 1). \(differenceResult.analysisType.description)\n"
            report += "   當前得分: \(String(format: "%.3f", differenceResult.overallScore))\n"
            report += "   標準差: \(String(format: "%.3f", differenceResult.standardDeviation))\n"
            report += "   問題案例: \(differenceResult.poorPerformanceCases.count) 個\n"
            report += "   優先級: \(differenceResult.priorityLevel.description)\n"
            
            if !differenceResult.rootCauses.isEmpty {
                report += "   主要原因:\n"
                for cause in differenceResult.rootCauses.prefix(3) {
                    report += "   - \(cause.description) (影響: \(String(format: "%.1f%%", cause.impact * 100)))\n"
                }
            }
            report += "\n"
        }
        
        // 5. 優化建議
        report += "💡 優化建議 (按優先級排序)\n"
        for (index, suggestion) in result.optimizationSuggestions.prefix(5).enumerated() {
            report += "\(index + 1). [\(suggestion.priority.rawValue)] \(suggestion.title)\n"
            report += "   \(suggestion.description)\n"
            report += "   預期改善: +\(String(format: "%.1f%%", suggestion.expectedImprovement * 100))\n"
            report += "   實施複雜度: \(suggestion.implementationComplexity.description)\n"
            report += "   預估工期: \(suggestion.estimatedEffort)\n"
            
            if !suggestion.specificRecommendations.isEmpty {
                report += "   具體措施:\n"
                for recommendation in suggestion.specificRecommendations.prefix(2) {
                    report += "   - \(recommendation)\n"
                }
            }
            report += "\n"
        }
        
        // 6. 結論與洞察
        report += "🎪 結論與洞察\n"
        for insight in result.executionSummary.keyInsights {
            report += "• \(insight)\n"
        }
        
        return report
    }
    
    // MARK: - 輔助方法
    
    private func setupAnalysisPipeline() {
        // 設置分析管道
        logger.info("設置真實數據分析管道")
    }
    
    private func addToHistory(_ result: ComprehensiveAnalysisResult) {
        let record = AnalysisHistoryRecord(
            timestamp: result.analysisTimestamp,
            sampleCount: result.realDataAnalysis.totalSamples,
            overallAccuracy: result.realDataAnalysis.overallAnalysis.overallStatistics.avgOverallAccuracy,
            diceScore: result.realDataAnalysis.overallAnalysis.overallStatistics.avgDiceScore,
            optimizationCount: result.optimizationSuggestions.count
        )
        
        analysisHistory.append(record)
        
        // 保持歷史記錄在合理範圍內
        if analysisHistory.count > 10 {
            analysisHistory.removeFirst()
        }
    }
    
    private func determineMedicalGradeStatus(_ stats: OverallStatistics) -> (level: String, description: String) {
        let accuracy = stats.avgOverallAccuracy
        let consistency = stats.consistencyIndex
        let diceScore = stats.avgDiceScore
        
        if accuracy >= 0.95 && consistency >= 0.85 && diceScore >= 0.90 {
            return ("臨床級", "達到臨床應用標準")
        } else if accuracy >= 0.90 && consistency >= 0.80 && diceScore >= 0.85 {
            return ("醫療級", "達到醫療器械標準")
        } else if accuracy >= 0.85 && consistency >= 0.75 && diceScore >= 0.80 {
            return ("研究級", "適用於研究環境")
        } else {
            return ("開發級", "需要進一步改善")
        }
    }
}

// MARK: - 支援資料結構

enum AnalysisState {
    case idle
    case initializing
    case comparingWithGroundTruth
    case analyzingDifferences
    case generatingOptimizations
    case completed
    case failed(Error)
}

struct ComprehensiveAnalysisResult {
    let realDataAnalysis: RealDataAnalysisResult
    let differenceAnalysis: [DifferenceAnalysisResult]
    let optimizationSuggestions: [OptimizationSuggestion]
    let analysisTimestamp: Date
    let executionSummary: ExecutionSummary
}

struct DifferenceAnalysisResult {
    let analysisType: AnalysisType
    let overallScore: Double
    let standardDeviation: Double
    let poorPerformanceCases: [PoorPerformanceCase]
    let identifiedPatterns: [AnalysisPattern]
    let rootCauses: [RootCause]
    let improvementSuggestions: [String]
    let priorityLevel: PriorityLevel
}

enum AnalysisType {
    case segmentationAccuracy
    case measurementAccuracy
    case processingPerformance
    case algorithmConsistency
    
    var description: String {
        switch self {
        case .segmentationAccuracy: return "分割準確度分析"
        case .measurementAccuracy: return "測量準確度分析"
        case .processingPerformance: return "處理性能分析"
        case .algorithmConsistency: return "算法一致性分析"
        }
    }
}

struct PoorPerformanceCase {
    let imageName: String
    let issueType: IssueType
    let metric: Double
    let expectedMetric: Double
    let rootCause: RootCause
}

enum IssueType {
    case lowSegmentationAccuracy
    case measurementInaccuracy
    case slowProcessing
    case inconsistentResults
}

struct RootCause {
    let type: String
    let description: String
    let impact: Double
}

struct OptimizationSuggestion {
    let category: OptimizationCategory
    let priority: PriorityLevel
    let title: String
    let description: String
    let specificRecommendations: [String]
    let expectedImprovement: Double
    let implementationComplexity: ComplexityLevel
    let estimatedEffort: String
}

enum OptimizationCategory {
    case algorithmImprovement
    case performanceOptimization
    case consistencyImprovement
    case dataPreprocessing
    case postProcessing
}

enum ComplexityLevel {
    case low, medium, high, veryHigh
    
    var description: String {
        switch self {
        case .low: return "低"
        case .medium: return "中"
        case .high: return "高" 
        case .veryHigh: return "很高"
        }
    }
}

struct AnalysisHistoryRecord {
    let timestamp: Date
    let sampleCount: Int
    let overallAccuracy: Double
    let diceScore: Double
    let optimizationCount: Int
}

struct ExecutionSummary {
    let keyInsights: [String]
    let criticalIssues: [String]
    let quickWins: [String]
    let longTermGoals: [String]
}