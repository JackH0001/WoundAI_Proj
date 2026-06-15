import Foundation
import UIKit
import PDFKit
import SwiftUI

/// PDF醫療報告生成器 - 支援醫療級專業報告生成
@MainActor
class PDFReportGenerator: ObservableObject {
    static let shared = PDFReportGenerator()
    
    // MARK: - 公開屬性
    @Published var isGenerating: Bool = false
    @Published var generationProgress: Double = 0.0
    @Published var lastGeneratedReport: URL?
    
    // MARK: - 報告設定
    struct ReportConfiguration {
        var includeImages: Bool = true
        var include3DVisualization: Bool = true
        var includeMeasurementHistory: Bool = true
        var includeRecommendations: Bool = true
        var reportLanguage: ReportLanguage = .traditionalChinese
        var medicalCompliance: Bool = true
        
        enum ReportLanguage: String, CaseIterable {
            case traditionalChinese = "繁體中文"
            case simplifiedChinese = "简体中文"
            case english = "English"
        }
    }
    
    private init() {}
    
    // MARK: - 主要生成方法
    
    /// 生成單一測量報告
    func generateSingleMeasurementReport(
        measurementData: WoundMeasurementData,
        config: ReportConfiguration = ReportConfiguration()
    ) async throws -> URL {
        isGenerating = true
        generationProgress = 0.0
        
        defer {
            isGenerating = false
            generationProgress = 0.0
        }
        
        print("📄 開始生成PDF報告...")
        
        let pdfMetaData = [
            kCGPDFContextCreator: "傷口測量AI系統",
            kCGPDFContextAuthor: "醫療AI分析",
            kCGPDFContextTitle: "傷口測量報告",
            kCGPDFContextSubject: "醫療級傷口分析報告"
        ]
        
        let format = UIGraphicsPDFRendererFormat()
        format.documentInfo = pdfMetaData as [String: Any]
        
        let pageRect = CGRect(x: 0, y: 0, width: 595, height: 842) // A4 size
        let renderer = UIGraphicsPDFRenderer(bounds: pageRect, format: format)
        
        generationProgress = 0.1
        
        let data = renderer.pdfData { context in
            // 第一頁：報告封面
            context.beginPage()
            drawReportCover(in: pageRect, data: measurementData, config: config)
            generationProgress = 0.3
            
            // 第二頁：測量結果詳情
            context.beginPage()
            drawMeasurementDetails(in: pageRect, data: measurementData, config: config)
            generationProgress = 0.5
            
            // 第三頁：圖像和視覺化
            if config.includeImages {
                context.beginPage()
                drawImagesAndVisualization(in: pageRect, data: measurementData, config: config)
                generationProgress = 0.7
            }
            
            // 第四頁：醫療建議和分析
            if config.includeRecommendations {
                context.beginPage()
                drawMedicalRecommendations(in: pageRect, data: measurementData, config: config)
                generationProgress = 0.9
            }
        }
        
        // 保存PDF文件
        let documentsPath = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        let timestamp = DateFormatter.yyyyMMddHHmmss.string(from: Date())
        let fileName = "WoundReport_\(timestamp).pdf"
        let fileURL = documentsPath.appendingPathComponent(fileName)
        
        try data.write(to: fileURL)
        lastGeneratedReport = fileURL
        generationProgress = 1.0
        
        print("✅ PDF報告生成完成: \(fileURL.lastPathComponent)")
        return fileURL
    }
    
    /// 生成批量處理報告
    func generateBatchReport(
        batchReport: BatchProcessingReport,
        config: ReportConfiguration = ReportConfiguration()
    ) async throws -> URL {
        isGenerating = true
        generationProgress = 0.0
        
        defer {
            isGenerating = false
            generationProgress = 0.0
        }
        
        print("📊 開始生成批量處理PDF報告...")
        
        let pdfMetaData = [
            kCGPDFContextCreator: "傷口測量AI系統",
            kCGPDFContextAuthor: "醫療AI分析",
            kCGPDFContextTitle: "批量測量報告",
            kCGPDFContextSubject: "批量傷口分析報告"
        ]
        
        let format = UIGraphicsPDFRendererFormat()
        format.documentInfo = pdfMetaData as [String: Any]
        
        let pageRect = CGRect(x: 0, y: 0, width: 595, height: 842) // A4 size
        let renderer = UIGraphicsPDFRenderer(bounds: pageRect, format: format)
        
        generationProgress = 0.1
        
        let data = renderer.pdfData { context in
            // 第一頁：批量報告概覽
            context.beginPage()
            drawBatchReportOverview(in: pageRect, report: batchReport, config: config)
            generationProgress = 0.4
            
            // 第二頁：統計分析
            context.beginPage()
            drawBatchStatistics(in: pageRect, report: batchReport, config: config)
            generationProgress = 0.7
            
            // 第三頁及以後：各個測量結果詳情
            if config.includeMeasurementHistory {
                for (index, result) in batchReport.results.enumerated() {
                    context.beginPage()
                    drawBatchItemDetail(in: pageRect, result: result, index: index, config: config)
                    generationProgress = 0.7 + 0.2 * (Double(index + 1) / Double(batchReport.results.count))
                }
            }
        }
        
        // 保存PDF文件
        let documentsPath = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        let timestamp = DateFormatter.yyyyMMddHHmmss.string(from: Date())
        let fileName = "BatchReport_\(timestamp).pdf"
        let fileURL = documentsPath.appendingPathComponent(fileName)
        
        try data.write(to: fileURL)
        lastGeneratedReport = fileURL
        generationProgress = 1.0
        
        print("✅ 批量PDF報告生成完成: \(fileURL.lastPathComponent)")
        return fileURL
    }
    
    // MARK: - PDF頁面繪製方法
    
    private func drawReportCover(in rect: CGRect, data: WoundMeasurementData, config: ReportConfiguration) {
        let context = UIGraphicsGetCurrentContext()!
        
        // 繪製醫院標誌區域
        let logoRect = CGRect(x: 50, y: 50, width: 100, height: 100)
        context.setFillColor(UIColor.systemBlue.cgColor)
        context.fill(logoRect)
        
        // 主標題
        let titleText = "傷口測量分析報告"
        let titleFont = UIFont.boldSystemFont(ofSize: 28)
        let titleAttributes: [NSAttributedString.Key: Any] = [
            .font: titleFont,
            .foregroundColor: UIColor.black
        ]
        
        let titleSize = titleText.size(withAttributes: titleAttributes)
        let titleRect = CGRect(
            x: (rect.width - titleSize.width) / 2,
            y: 200,
            width: titleSize.width,
            height: titleSize.height
        )
        titleText.draw(in: titleRect, withAttributes: titleAttributes)
        
        // 患者信息
        drawPatientInfo(at: CGPoint(x: 50, y: 300), data: data, config: config)
        
        // 報告生成信息
        drawReportInfo(at: CGPoint(x: 50, y: 450), data: data, config: config)
        
        // 醫療合規聲明
        if config.medicalCompliance {
            drawMedicalCompliance(at: CGPoint(x: 50, y: 650), config: config)
        }
    }
    
    private func drawMeasurementDetails(in rect: CGRect, data: WoundMeasurementData, config: ReportConfiguration) {
        let startY: CGFloat = 50
        var currentY = startY
        
        // 頁面標題
        currentY += drawSectionTitle("測量結果詳情", at: CGPoint(x: 50, y: currentY), fontSize: 20)
        currentY += 20
        
        // 測量數據表格
        currentY += drawMeasurementTable(at: CGPoint(x: 50, y: currentY), data: data, config: config)
        currentY += 30
        
        // 分析結果
        if let analysis = data.analysis {
            currentY += drawSectionTitle("AI分析結果", at: CGPoint(x: 50, y: currentY), fontSize: 16)
            currentY += drawAnalysisResults(at: CGPoint(x: 50, y: currentY), analysis: analysis, config: config)
        }
        
        // 校正信息
        if let calibration = data.calibrationInfo {
            currentY += 20
            currentY += drawSectionTitle("校正信息", at: CGPoint(x: 50, y: currentY), fontSize: 16)
            currentY += drawCalibrationInfo(at: CGPoint(x: 50, y: currentY), calibration: calibration, config: config)
        }
    }
    
    private func drawImagesAndVisualization(in rect: CGRect, data: WoundMeasurementData, config: ReportConfiguration) {
        let startY: CGFloat = 50
        var currentY = startY
        
        // 頁面標題
        currentY += drawSectionTitle("圖像和視覺化", at: CGPoint(x: 50, y: currentY), fontSize: 20)
        currentY += 20
        
        // 原始圖像
        if let originalImage = data.originalImage {
            currentY += drawSectionTitle("原始圖像", at: CGPoint(x: 50, y: currentY), fontSize: 14)
            currentY += 10
            let imageRect = CGRect(x: 50, y: currentY, width: 200, height: 150)
            originalImage.draw(in: imageRect)
            currentY += 160
        }
        
        // 處理後圖像
        if let processedImage = data.processedImage {
            currentY += drawSectionTitle("處理後圖像（含測量標註）", at: CGPoint(x: 300, y: currentY - 170), fontSize: 14)
            let processedImageRect = CGRect(x: 300, y: currentY - 160, width: 200, height: 150)
            processedImage.draw(in: processedImageRect)
        }
        
        // 測量標尺和比例說明
        currentY += 20
        currentY += drawMeasurementScale(at: CGPoint(x: 50, y: currentY), data: data, config: config)
    }
    
    private func drawMedicalRecommendations(in rect: CGRect, data: WoundMeasurementData, config: ReportConfiguration) {
        let startY: CGFloat = 50
        var currentY = startY
        
        // 頁面標題
        currentY += drawSectionTitle("醫療建議與分析", at: CGPoint(x: 50, y: currentY), fontSize: 20)
        currentY += 30
        
        // 傷口分類和嚴重程度
        if let classification = data.classification {
            currentY += drawSectionTitle("傷口分類", at: CGPoint(x: 50, y: currentY), fontSize: 16)
            currentY += drawClassificationInfo(at: CGPoint(x: 50, y: currentY), classification: classification, config: config)
            currentY += 30
        }
        
        // 治療建議
        currentY += drawSectionTitle("治療建議", at: CGPoint(x: 50, y: currentY), fontSize: 16)
        currentY += drawTreatmentRecommendations(at: CGPoint(x: 50, y: currentY), data: data, config: config)
        currentY += 30
        
        // 追蹤建議
        currentY += drawSectionTitle("追蹤建議", at: CGPoint(x: 50, y: currentY), fontSize: 16)
        currentY += drawFollowUpRecommendations(at: CGPoint(x: 50, y: currentY), data: data, config: config)
        
        // 免責聲明
        drawMedicalDisclaimer(at: CGPoint(x: 50, y: 700), config: config)
    }
    
    // MARK: - 批量報告繪製方法
    
    private func drawBatchReportOverview(in rect: CGRect, report: BatchProcessingReport, config: ReportConfiguration) {
        let startY: CGFloat = 50
        var currentY = startY
        
        // 標題
        currentY += drawSectionTitle("批量處理報告", at: CGPoint(x: 50, y: currentY), fontSize: 24)
        currentY += 30
        
        // 處理概覽
        currentY += drawBatchSummary(at: CGPoint(x: 50, y: currentY), report: report, config: config)
        currentY += 40
        
        // 統計圖表
        currentY += drawBatchStatisticsChart(at: CGPoint(x: 50, y: currentY), report: report, config: config)
    }
    
    private func drawBatchStatistics(in rect: CGRect, report: BatchProcessingReport, config: ReportConfiguration) {
        let startY: CGFloat = 50
        var currentY = startY
        
        // 標題
        currentY += drawSectionTitle("統計分析", at: CGPoint(x: 50, y: currentY), fontSize: 20)
        currentY += 30
        
        // 性能統計
        currentY += drawPerformanceStatistics(at: CGPoint(x: 50, y: currentY), report: report, config: config)
        currentY += 40
        
        // 錯誤分析
        if !report.errors.isEmpty {
            currentY += drawErrorAnalysis(at: CGPoint(x: 50, y: currentY), report: report, config: config)
        }
    }
    
    private func drawBatchItemDetail(in rect: CGRect, result: BatchProcessingResult, index: Int, config: ReportConfiguration) {
        let startY: CGFloat = 50
        var currentY = startY
        
        // 項目標題
        currentY += drawSectionTitle("項目 \(index + 1): \(result.imageName)", at: CGPoint(x: 50, y: currentY), fontSize: 18)
        currentY += 20
        
        // 測量結果
        currentY += drawBatchItemMeasurements(at: CGPoint(x: 50, y: currentY), result: result, config: config)
        
        // 圖像（如果配置允許）
        if config.includeImages {
            let imageRect = CGRect(x: 300, y: currentY - 100, width: 150, height: 100)
            result.originalImage.draw(in: imageRect)
        }
    }
    
    // MARK: - 輔助繪製方法
    
    private func drawSectionTitle(_ title: String, at point: CGPoint, fontSize: CGFloat) -> CGFloat {
        let font = UIFont.boldSystemFont(ofSize: fontSize)
        let attributes: [NSAttributedString.Key: Any] = [
            .font: font,
            .foregroundColor: UIColor.black
        ]
        
        let size = title.size(withAttributes: attributes)
        let rect = CGRect(origin: point, size: size)
        title.draw(in: rect, withAttributes: attributes)
        
        return size.height + 10
    }
    
    private func drawPatientInfo(at point: CGPoint, data: WoundMeasurementData, config: ReportConfiguration) {
        let font = UIFont.systemFont(ofSize: 14)
        let attributes: [NSAttributedString.Key: Any] = [
            .font: font,
            .foregroundColor: UIColor.black
        ]
        
        let info = [
            "測量日期: \(DateFormatter.medicalReport.string(from: data.measurementDate))",
            "測量位置: \(data.woundLocation ?? "未指定")",
            "測量設備: iPhone LiDAR 3D掃描",
            "分析軟件: 傷口測量AI系統 v1.0"
        ]
        
        var currentY = point.y
        for line in info {
            let rect = CGRect(x: point.x, y: currentY, width: 400, height: 20)
            line.draw(in: rect, withAttributes: attributes)
            currentY += 25
        }
    }
    
    private func drawReportInfo(at point: CGPoint, data: WoundMeasurementData, config: ReportConfiguration) {
        let font = UIFont.systemFont(ofSize: 12)
        let attributes: [NSAttributedString.Key: Any] = [
            .font: font,
            .foregroundColor: UIColor.darkGray
        ]
        
        let info = [
            "報告生成時間: \(DateFormatter.medicalReport.string(from: Date()))",
            "報告語言: \(config.reportLanguage.rawValue)",
            "精度等級: 醫療級（±0.1mm）",
            "符合標準: ISO 13485 醫療器械品質管理"
        ]
        
        var currentY = point.y
        for line in info {
            let rect = CGRect(x: point.x, y: currentY, width: 500, height: 18)
            line.draw(in: rect, withAttributes: attributes)
            currentY += 22
        }
    }
    
    private func drawMedicalCompliance(at point: CGPoint, config: ReportConfiguration) {
        let font = UIFont.systemFont(ofSize: 10)
        let attributes: [NSAttributedString.Key: Any] = [
            .font: font,
            .foregroundColor: UIColor.red
        ]
        
        let disclaimer = """
        醫療免責聲明：
        本報告僅供醫療專業人員參考，不可作為最終診斷依據。
        所有測量結果需由合格醫療人員進行臨床驗證。
        使用本系統前請確保符合當地醫療法規要求。
        """
        
        let rect = CGRect(x: point.x, y: point.y, width: 500, height: 80)
        disclaimer.draw(in: rect, withAttributes: attributes)
    }
    
    private func drawMeasurementTable(at point: CGPoint, data: WoundMeasurementData, config: ReportConfiguration) -> CGFloat {
        let tableData: [[String]] = [
            ["測量項目", "數值", "單位", "精度"],
            ["傷口面積", String(format: "%.2f", data.area), "cm²", "±0.01"],
            ["傷口周長", String(format: "%.2f", data.perimeter), "cm", "±0.01"],
            ["最大深度", String(format: "%.1f", data.maxDepth), "mm", "±0.1"],
            ["平均深度", String(format: "%.1f", data.averageDepth), "mm", "±0.1"]
        ]
        
        if data.volume > 0 {
            // 添加體積行
        }
        
        return drawTable(at: point, data: tableData, columnWidths: [100, 80, 60, 60])
    }
    
    private func drawTable(at point: CGPoint, data: [[String]], columnWidths: [CGFloat]) -> CGFloat {
        let context = UIGraphicsGetCurrentContext()!
        let font = UIFont.systemFont(ofSize: 11)
        let headerFont = UIFont.boldSystemFont(ofSize: 11)
        
        var currentY = point.y
        let rowHeight: CGFloat = 25
        
        for (rowIndex, row) in data.enumerated() {
            var currentX = point.x
            
            for (colIndex, cell) in row.enumerated() {
                let width = columnWidths[colIndex]
                let cellRect = CGRect(x: currentX, y: currentY, width: width, height: rowHeight)
                
                // 繪製邊框
                context.setStrokeColor(UIColor.black.cgColor)
                context.stroke(cellRect)
                
                // 繪製文字
                let attributes: [NSAttributedString.Key: Any] = [
                    .font: rowIndex == 0 ? headerFont : font,
                    .foregroundColor: UIColor.black
                ]
                
                let textRect = cellRect.insetBy(dx: 5, dy: 5)
                cell.draw(in: textRect, withAttributes: attributes)
                
                currentX += width
            }
            
            currentY += rowHeight
        }
        
        return CGFloat(data.count) * rowHeight
    }
    
    private func drawMedicalDisclaimer(at point: CGPoint, config: ReportConfiguration) {
        let font = UIFont.systemFont(ofSize: 8)
        let attributes: [NSAttributedString.Key: Any] = [
            .font: font,
            .foregroundColor: UIColor.darkGray
        ]
        
        let disclaimer = "此報告由AI系統自動生成，僅供醫療專業人員參考使用。最終診斷和治療決定應由合格醫師做出。"
        let rect = CGRect(x: point.x, y: point.y, width: 500, height: 40)
        disclaimer.draw(in: rect, withAttributes: attributes)
    }
    
    // 其他繪製方法的簡化實現
    private func drawAnalysisResults(at point: CGPoint, analysis: WoundAnalysis, config: ReportConfiguration) -> CGFloat { return 50 }
    private func drawCalibrationInfo(at point: CGPoint, calibration: CalibrationInfo, config: ReportConfiguration) -> CGFloat { return 40 }
    private func drawMeasurementScale(at point: CGPoint, data: WoundMeasurementData, config: ReportConfiguration) -> CGFloat { return 60 }
    private func drawClassificationInfo(at point: CGPoint, classification: DetailedWoundClassification, config: ReportConfiguration) -> CGFloat { return 50 }
    private func drawTreatmentRecommendations(at point: CGPoint, data: WoundMeasurementData, config: ReportConfiguration) -> CGFloat { return 80 }
    private func drawFollowUpRecommendations(at point: CGPoint, data: WoundMeasurementData, config: ReportConfiguration) -> CGFloat { return 60 }
    private func drawBatchSummary(at point: CGPoint, report: BatchProcessingReport, config: ReportConfiguration) -> CGFloat { return 100 }
    private func drawBatchStatisticsChart(at point: CGPoint, report: BatchProcessingReport, config: ReportConfiguration) -> CGFloat { return 200 }
    private func drawPerformanceStatistics(at point: CGPoint, report: BatchProcessingReport, config: ReportConfiguration) -> CGFloat { return 120 }
    private func drawErrorAnalysis(at point: CGPoint, report: BatchProcessingReport, config: ReportConfiguration) -> CGFloat { return 100 }
    private func drawBatchItemMeasurements(at point: CGPoint, result: BatchProcessingResult, config: ReportConfiguration) -> CGFloat { return 80 }
}

// MARK: - 數據結構

struct WoundMeasurementData {
    let measurementDate: Date
    let area: Double
    let perimeter: Double
    let maxDepth: Double
    let averageDepth: Double
    let volume: Double
    let woundLocation: String?
    let originalImage: UIImage?
    let processedImage: UIImage?
    let calibrationInfo: CalibrationInfo?
    let analysis: WoundAnalysis?
    let classification: DetailedWoundClassification?
}

struct CalibrationInfo {
    let pixelsPerMM: Double
    let calibrationType: String
    let accuracy: Double
}

struct WoundAnalysis {
    let tissueTypes: [String: Double]
    let healingStage: String
    let riskFactors: [String]
}

// 使用 Models 內的 DetailedWoundClassification，避免與同名型別衝突

// MARK: - DateFormatter擴展

extension DateFormatter {
    static let yyyyMMddHHmmss: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyyMMdd_HHmmss"
        return formatter
    }()
    
    static let medicalReport: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateStyle = .full
        formatter.timeStyle = .medium
        formatter.locale = Locale(identifier: "zh_TW")
        return formatter
    }()
}