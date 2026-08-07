import Foundation
import SwiftUI

/// 模擬數據服務，為開發和測試提供示例數據
class MockDataService {
    static let shared = MockDataService()
    
    private init() {}
    
    /// 生成模擬歷史測量數據
    func generateMockHistoricalData(timeRange: TimeInterval = 30 * 24 * 3600) -> [HistoricalMeasurement] {
        let endDate = Date()
        let startDate = endDate.addingTimeInterval(-timeRange)
        let numberOfPoints = Int(timeRange / (24 * 3600)) // 每天一個數據點
        
        var measurements: [HistoricalMeasurement] = []
        let initialArea = Double.random(in: 8.0...15.0) // cm²
        
        for i in 0..<numberOfPoints {
            let date = startDate.addingTimeInterval(Double(i) * 24 * 3600)
            
            // 模擬傷口癒合趨勢（面積逐漸減小，但有噪音）
            let progress = Double(i) / Double(numberOfPoints)
            let healingFactor = 1.0 - (progress * 0.6) // 60% 改善
            let noise = Double.random(in: -0.15...0.15) // ±15% 噪音
            let area = max(1.0, initialArea * healingFactor * (1.0 + noise))
            
            // 其他測量值基於面積估算
            let perimeter = 2 * sqrt(.pi * area) * Double.random(in: 0.9...1.1)
            let volume = area * Double.random(in: 0.3...0.8) // 深度 3-8mm
            let maxDepth = Double.random(in: 0.2...1.2)
            
            // 分類隨時間改善
            let classifications = ["急性傷口", "癒合中", "表皮化", "幾乎癒合"]
            let classificationIndex = min(classifications.count - 1, Int(progress * Double(classifications.count)))
            let classification = classifications[classificationIndex]
            
            let confidence = Double.random(in: 0.7...0.95)
            
            let measurement = HistoricalMeasurement(
                date: date,
                area: area,
                volume: volume,
                perimeter: perimeter,
                maxDepth: maxDepth,
                classification: classification,
                confidence: confidence,
                notes: i % 7 == 0 ? "定期檢查" : nil
            )
            
            measurements.append(measurement)
        }
        
        return measurements.sorted { $0.date < $1.date }
    }
    
    /// 生成趨勢分析
    func generateTrendAnalysis(for measurements: [HistoricalMeasurement]) -> TrendAnalysis {
        guard measurements.count >= 3 else {
            return TrendAnalysis(
                timeRange: 0,
                measurements: measurements,
                trend: .insufficient,
                changeRate: 0,
                significance: .none
            )
        }
        
        let timeRange = measurements.last!.date.timeIntervalSince(measurements.first!.date)
        let areas = measurements.compactMap { $0.area }
        
        guard areas.count >= 3 else {
            return TrendAnalysis(
                timeRange: timeRange,
                measurements: measurements,
                trend: .insufficient,
                changeRate: 0,
                significance: .none
            )
        }
        
        // 簡單線性趨勢計算
        let firstArea = areas.first!
        let lastArea = areas.last!
        let changeRate = ((lastArea - firstArea) / firstArea) * 100 / (timeRange / (24 * 3600))
        
        let trend: TrendAnalysis.TrendDirection
        let significance: TrendAnalysis.TrendSignificance
        
        if abs(changeRate) < 0.5 {
            trend = .stable
            significance = .none
        } else if changeRate < -0.5 {
            trend = .improving
            significance = abs(changeRate) > 2.0 ? .significant : .moderate
        } else {
            trend = .worsening
            significance = abs(changeRate) > 2.0 ? .significant : .moderate
        }
        
        return TrendAnalysis(
            timeRange: timeRange,
            measurements: measurements,
            trend: trend,
            changeRate: changeRate,
            significance: significance
        )
    }
    
    /// 生成模擬標註數據
    func generateMockAnnotationData() -> AnnotationData {
        let imageSize = CGSize(width: 1000, height: 800)
        let woundCenter = CGPoint(x: imageSize.width * 0.5, y: imageSize.height * 0.4)
        let woundRadius = min(imageSize.width, imageSize.height) * 0.15
        
        // 生成傷口邊界點（橢圓形）
        var woundPoints: [CGPoint] = []
        for i in 0..<20 {
            let angle = Double(i) * 2.0 * .pi / 20.0
            let radiusVariation = Double.random(in: 0.8...1.2)
            let x = woundCenter.x + cos(angle) * woundRadius * radiusVariation
            let y = woundCenter.y + sin(angle) * woundRadius * radiusVariation * 0.7 // 橢圓形
            woundPoints.append(CGPoint(x: x, y: y))
        }
        
        let annotations = [
            AnnotationData.Annotation(
                type: .wound,
                points: woundPoints,
                label: "主要傷口區域",
                confidence: 0.92
            ),
            AnnotationData.Annotation(
                type: .reference,
                points: [
                    CGPoint(x: 100, y: 100),
                    CGPoint(x: 200, y: 100),
                    CGPoint(x: 200, y: 200),
                    CGPoint(x: 100, y: 200)
                ],
                label: "20mm 校正貼紙",
                confidence: 0.98
            )
        ]
        
        let measurements = AnnotationData.MeasurementAnnotations(
            area: Double.random(in: 8.0...12.0),
            perimeter: Double.random(in: 10.0...15.0),
            maxLength: Double.random(in: 4.0...6.0),
            maxWidth: Double.random(in: 3.0...5.0),
            depth: Double.random(in: 0.3...1.0),
            pixelsPerMM: Double.random(in: 15.0...25.0)
        )
        
        return AnnotationData(
            timestamp: Date(),
            imageData: nil, // 實際應用中會包含圖像數據
            annotations: annotations,
            measurements: measurements,
            notes: "標註完成，品質良好"
        )
    }
    
    /// 生成模擬雲端分析結果
    func generateMockCloudAnalysisResult() -> CloudAnalysisResult {
        let tissueAnalysis = CloudAnalysisResult.TissueAnalysis(
            granulationPercentage: Double.random(in: 40...70),
            necroticPercentage: Double.random(in: 5...20),
            epithelialPercentage: Double.random(in: 15...35),
            healthyPercentage: Double.random(in: 10...25)
        )
        
        let recommendations = [
            "建議每日清潔傷口並更換敷料",
            "注意觀察感染跡象",
            "保持傷口濕潤環境促進癒合",
            "如有疼痛加劇或發燒，請立即就醫"
        ].shuffled().prefix(Int.random(in: 2...4)).map { String($0) }
        
        return CloudAnalysisResult(
            analysisId: UUID().uuidString,
            timestamp: Date(),
            qualityScore: Double.random(in: 0.8...0.95),
            bjwatScore: Int.random(in: 3...12),
            revpwatScore: Int.random(in: 2...10),
            tissueAnalysis: tissueAnalysis,
            recommendations: recommendations,
            confidence: Double.random(in: 0.75...0.92)
        )
    }
    
    /// 生成模擬校準結果
    func generateMockCalibrationResults() -> [CalibrationResult] {
        return [
            CalibrationResult(
                method: "20mm 校正貼紙",
                pixelsPerMM: 18.5,
                confidence: 0.95,
                referenceSize: 20.0,
                notes: "標準校正貼紙，檢測良好"
            ),
            CalibrationResult(
                method: "硬幣校正 (1元)",
                pixelsPerMM: 19.2,
                confidence: 0.88,
                referenceSize: 20.0,
                notes: "使用1元硬幣作為參考"
            ),
            CalibrationResult(
                method: "尺規校正",
                pixelsPerMM: 18.8,
                confidence: 0.82,
                referenceSize: 10.0,
                notes: "手動尺規校正"
            )
        ]
    }
}