import XCTest
import UIKit
import PDFKit
@testable import WoundMeasurementApp

/// PDF報告生成器單元測試
final class PDFReportGeneratorTests: XCTestCase {
    
    // MARK: - 測試屬性
    var pdfGenerator: PDFReportGenerator!
    var testWoundData: WoundMeasurementData!
    var testBatchReport: BatchProcessingReport!
    
    // MARK: - 測試生命週期
    override func setUpWithError() throws {
        super.setUp()
        
        // 在主線程中創建PDFReportGenerator實例
        pdfGenerator = PDFReportGenerator.shared
        
        // 創建測試數據
        testWoundData = createTestWoundMeasurementData()
        testBatchReport = createTestBatchProcessingReport()
    }
    
    override func tearDownWithError() throws {
        pdfGenerator = nil
        testWoundData = nil
        testBatchReport = nil
        super.tearDown()
    }
    
    // MARK: - 單一測量報告生成測試
    
    /// 測試基本PDF報告生成
    func testBasicPDFGeneration() throws {
        // Given
        let config = PDFReportGenerator.ReportConfiguration()
        
        // When
        let expectation = XCTestExpectation(description: "PDF生成測試")
        var generatedURL: URL?
        var generationError: Error?
        
        Task { @MainActor in
            do {
                generatedURL = try await pdfGenerator.generateSingleMeasurementReport(
                    measurementData: testWoundData,
                    config: config
                )
                expectation.fulfill()
            } catch {
                generationError = error
                expectation.fulfill()
            }
        }
        
        wait(for: [expectation], timeout: 30.0)
        
        // Then
        XCTAssertNil(generationError, "PDF生成不應該出錯: \(generationError?.localizedDescription ?? "")")
        XCTAssertNotNil(generatedURL, "應該生成PDF文件URL")
        
        if let url = generatedURL {
            // 驗證文件存在
            XCTAssertTrue(FileManager.default.fileExists(atPath: url.path), "PDF文件應該存在")
            
            // 驗證文件大小合理
            let attributes = try FileManager.default.attributesOfItem(atPath: url.path)
            let fileSize = attributes[.size] as? Int64 ?? 0
            XCTAssertGreaterThan(fileSize, 1000, "PDF文件大小應該大於1KB")
            XCTAssertLessThan(fileSize, 10_000_000, "PDF文件大小應該小於10MB")
            
            // 驗證PDF內容
            validatePDFContent(at: url)
            
            // 清理測試文件
            try? FileManager.default.removeItem(at: url)
        }
    }
    
    /// 測試不同配置的PDF生成
    func testDifferentConfigurations() throws {
        let configurations = [
            PDFReportGenerator.ReportConfiguration(
                includeImages: true,
                include3DVisualization: true,
                includeRecommendations: true,
                reportLanguage: .traditionalChinese
            ),
            PDFReportGenerator.ReportConfiguration(
                includeImages: false,
                include3DVisualization: false,
                includeRecommendations: false,
                reportLanguage: .english
            ),
            PDFReportGenerator.ReportConfiguration(
                includeImages: true,
                include3DVisualization: false,
                includeRecommendations: true,
                medicalCompliance: false
            )
        ]
        
        for (index, config) in configurations.enumerated() {
            // When
            let expectation = XCTestExpectation(description: "配置\(index)PDF生成測試")
            var generatedURL: URL?
            var generationError: Error?
            
            Task { @MainActor in
                do {
                    generatedURL = try await pdfGenerator.generateSingleMeasurementReport(
                        measurementData: testWoundData,
                        config: config
                    )
                    expectation.fulfill()
                } catch {
                    generationError = error
                    expectation.fulfill()
                }
            }
            
            wait(for: [expectation], timeout: 30.0)
            
            // Then
            XCTAssertNil(generationError, "配置\(index)PDF生成不應該出錯")
            XCTAssertNotNil(generatedURL, "配置\(index)應該生成PDF")
            
            if let url = generatedURL {
                XCTAssertTrue(FileManager.default.fileExists(atPath: url.path))
                try? FileManager.default.removeItem(at: url)
            }
        }
    }
    
    /// 測試PDF生成進度追蹤
    func testPDFGenerationProgress() throws {
        // Given
        let config = PDFReportGenerator.ReportConfiguration()
        var progressValues: [Double] = []
        
        // 監控進度變化
        let progressExpectation = XCTestExpectation(description: "進度更新")
        progressExpectation.expectedFulfillmentCount = 5 // 預期至少5次進度更新
        
        Task { @MainActor in
            // 訂閱進度更新
            let cancellable = pdfGenerator.$generationProgress
                .sink { progress in
                    progressValues.append(progress)
                    if progressValues.count <= 5 {
                        progressExpectation.fulfill()
                    }
                }
            
            // 生成PDF
            do {
                let url = try await pdfGenerator.generateSingleMeasurementReport(
                    measurementData: testWoundData,
                    config: config
                )
                try? FileManager.default.removeItem(at: url)
            } catch {
                XCTFail("PDF生成失敗: \(error)")
            }
            
            cancellable.cancel()
        }
        
        wait(for: [progressExpectation], timeout: 30.0)
        
        // Then
        XCTAssertGreaterThan(progressValues.count, 3, "應該有多個進度更新")
        
        // 驗證進度遞增
        for i in 1..<progressValues.count {
            XCTAssertGreaterThanOrEqual(progressValues[i], progressValues[i-1], "進度應該遞增")
        }
        
        // 最終進度應該是100%
        if let lastProgress = progressValues.last {
            XCTAssertEqual(lastProgress, 1.0, accuracy: 0.01, "最終進度應該為100%")
        }
    }
    
    // MARK: - 批量報告生成測試
    
    /// 測試批量報告PDF生成
    func testBatchReportGeneration() throws {
        // Given
        let config = PDFReportGenerator.ReportConfiguration()
        
        // When
        let expectation = XCTestExpectation(description: "批量報告PDF生成測試")
        var generatedURL: URL?
        var generationError: Error?
        
        Task { @MainActor in
            do {
                generatedURL = try await pdfGenerator.generateBatchReport(
                    batchReport: testBatchReport,
                    config: config
                )
                expectation.fulfill()
            } catch {
                generationError = error
                expectation.fulfill()
            }
        }
        
        wait(for: [expectation], timeout: 30.0)
        
        // Then
        XCTAssertNil(generationError, "批量報告PDF生成不應該出錯")
        XCTAssertNotNil(generatedURL, "應該生成批量報告PDF")
        
        if let url = generatedURL {
            XCTAssertTrue(FileManager.default.fileExists(atPath: url.path))
            
            // 驗證批量報告PDF內容
            validateBatchPDFContent(at: url)
            
            // 清理測試文件
            try? FileManager.default.removeItem(at: url)
        }
    }
    
    /// 測試大型批量報告生成
    func testLargeBatchReportGeneration() throws {
        // Given - 創建包含更多結果的批量報告
        let largeBatchReport = createTestBatchProcessingReport(resultCount: 20)
        let config = PDFReportGenerator.ReportConfiguration()
        
        // When
        let expectation = XCTestExpectation(description: "大型批量報告PDF生成測試")
        var generatedURL: URL?
        var generationError: Error?
        
        Task { @MainActor in
            do {
                generatedURL = try await pdfGenerator.generateBatchReport(
                    batchReport: largeBatchReport,
                    config: config
                )
                expectation.fulfill()
            } catch {
                generationError = error
                expectation.fulfill()
            }
        }
        
        wait(for: [expectation], timeout: 60.0) // 增加超時時間
        
        // Then
        XCTAssertNil(generationError, "大型批量報告PDF生成不應該出錯")
        XCTAssertNotNil(generatedURL, "應該生成大型批量報告PDF")
        
        if let url = generatedURL {
            // 驗證文件大小合理
            let attributes = try FileManager.default.attributesOfItem(atPath: url.path)
            let fileSize = attributes[.size] as? Int64 ?? 0
            XCTAssertGreaterThan(fileSize, 10000, "大型報告PDF文件應該更大")
            
            // 清理測試文件
            try? FileManager.default.removeItem(at: url)
        }
    }
    
    // MARK: - 錯誤處理測試
    
    /// 測試無效數據處理
    func testInvalidDataHandling() throws {
        // Given - 創建無效的測量數據
        let invalidData = WoundMeasurementData(
            measurementDate: Date(),
            area: -1.0, // 無效的負面積
            perimeter: 0.0,
            maxDepth: -1.0, // 無效的負深度
            averageDepth: 0.0,
            volume: 0.0,
            woundLocation: nil,
            originalImage: nil,
            processedImage: nil,
            calibrationInfo: nil,
            analysis: nil,
            classification: nil
        )
        
        let config = PDFReportGenerator.ReportConfiguration()
        
        // When
        let expectation = XCTestExpectation(description: "無效數據處理測試")
        var generatedURL: URL?
        var generationError: Error?
        
        Task { @MainActor in
            do {
                // 即使數據無效，PDF生成器也應該能處理並生成報告
                generatedURL = try await pdfGenerator.generateSingleMeasurementReport(
                    measurementData: invalidData,
                    config: config
                )
                expectation.fulfill()
            } catch {
                generationError = error
                expectation.fulfill()
            }
        }
        
        wait(for: [expectation], timeout: 30.0)
        
        // Then - 應該能處理無效數據，生成帶有適當標註的報告
        XCTAssertNil(generationError, "PDF生成器應該能處理無效數據")
        XCTAssertNotNil(generatedURL, "即使數據無效也應該生成PDF")
        
        if let url = generatedURL {
            try? FileManager.default.removeItem(at: url)
        }
    }
    
    /// 測試並發PDF生成
    func testConcurrentPDFGeneration() throws {
        // Given
        let config = PDFReportGenerator.ReportConfiguration()
        let concurrentCount = 3
        
        // When - 同時生成多個PDF
        let expectations = (0..<concurrentCount).map { i in
            XCTestExpectation(description: "並發PDF生成\(i)")
        }
        
        var generatedURLs: [URL] = []
        let urlsLock = NSLock()
        
        for i in 0..<concurrentCount {
            Task { @MainActor in
                do {
                    let url = try await pdfGenerator.generateSingleMeasurementReport(
                        measurementData: testWoundData,
                        config: config
                    )
                    
                    urlsLock.lock()
                    generatedURLs.append(url)
                    urlsLock.unlock()
                    
                    expectations[i].fulfill()
                } catch {
                    XCTFail("並發PDF生成\(i)失敗: \(error)")
                    expectations[i].fulfill()
                }
            }
        }
        
        wait(for: expectations, timeout: 60.0)
        
        // Then
        XCTAssertEqual(generatedURLs.count, concurrentCount, "應該生成所有並發PDF")
        
        // 驗證每個PDF都是唯一的
        let uniqueURLs = Set(generatedURLs.map(\.lastPathComponent))
        XCTAssertEqual(uniqueURLs.count, concurrentCount, "每個PDF文件名應該是唯一的")
        
        // 清理測試文件
        for url in generatedURLs {
            try? FileManager.default.removeItem(at: url)
        }
    }
    
    // MARK: - 性能測試
    
    /// 測試PDF生成性能
    func testPDFGenerationPerformance() throws {
        // Given
        let config = PDFReportGenerator.ReportConfiguration()
        
        // When & Then
        measure {
            let expectation = XCTestExpectation(description: "PDF生成性能測試")
            
            Task { @MainActor in
                do {
                    let url = try await pdfGenerator.generateSingleMeasurementReport(
                        measurementData: testWoundData,
                        config: config
                    )
                    try? FileManager.default.removeItem(at: url)
                    expectation.fulfill()
                } catch {
                    XCTFail("性能測試中PDF生成失敗: \(error)")
                    expectation.fulfill()
                }
            }
            
            wait(for: [expectation], timeout: 30.0)
        }
    }
    
    // MARK: - 輔助方法
    
    /// 創建測試用的傷口測量數據
    private func createTestWoundMeasurementData() -> WoundMeasurementData {
        let testImage = createTestImage()
        
        return WoundMeasurementData(
            measurementDate: Date(),
            area: 2.5,
            perimeter: 5.6,
            maxDepth: 3.2,
            averageDepth: 1.8,
            volume: 0.45,
            woundLocation: "左前臂",
            originalImage: testImage,
            processedImage: testImage,
            calibrationInfo: CalibrationInfo(
                pixelsPerMM: 12.5,
                calibrationType: "校正貼紙",
                accuracy: 0.95
            ),
            analysis: WoundAnalysis(
                tissueTypes: ["健康組織": 0.6, "壞死組織": 0.3, "肉芽組織": 0.1],
                healingStage: "炎症期",
                riskFactors: ["感染風險", "深度較深"]
            ),
            classification: WoundClassification(
                primaryType: "創傷性傷口",
                severity: "中度",
                confidence: 0.85
            )
        )
    }
    
    /// 創建測試用的批量處理報告
    private func createTestBatchProcessingReport(resultCount: Int = 5) -> BatchProcessingReport {
        var results: [BatchProcessingResult] = []
        var errors: [BatchProcessingError] = []
        
        // 創建成功結果
        for i in 0..<resultCount {
            let result = BatchProcessingResult(
                imageName: "test_image_\(i).jpg",
                originalImage: createTestImage(),
                processingTime: Double.random(in: 2.0...5.0),
                timestamp: Date().addingTimeInterval(-Double(i) * 60),
                measurementResult: WoundMeasurementModule.MeasurementResult(
                    woundArea: Double.random(in: 1.0...5.0),
                    woundPerimeter: Double.random(in: 3.0...8.0),
                    pixelsPerMM: Double.random(in: 10.0...15.0),
                    confidence: Double.random(in: 0.7...0.95)
                ),
                calibrationResult: nil,
                classificationResult: nil,
                savedRecord: nil
            )
            results.append(result)
        }
        
        // 創建一些錯誤
        for i in 0..<2 {
            let error = BatchProcessingError(
                imageName: "error_image_\(i).jpg",
                error: NSError(domain: "TestError", code: i, userInfo: [
                    NSLocalizedDescriptionKey: "測試錯誤\(i)"
                ]),
                timestamp: Date()
            )
            errors.append(error)
        }
        
        return BatchProcessingReport(
            timestamp: Date(),
            totalImages: resultCount + 2,
            successfulProcessing: resultCount,
            failedProcessing: 2,
            results: results,
            errors: errors,
            processingDuration: Double(resultCount) * 3.5 + 10.0,
            averageProcessingTime: 3.5
        )
    }
    
    /// 創建測試用圖像
    private func createTestImage() -> UIImage {
        let size = CGSize(width: 300, height: 200)
        UIGraphicsBeginImageContext(size)
        defer { UIGraphicsEndImageContext() }
        
        let context = UIGraphicsGetCurrentContext()!
        
        // 背景
        context.setFillColor(UIColor.systemBackground.cgColor)
        context.fill(CGRect(origin: .zero, size: size))
        
        // 模擬傷口
        context.setFillColor(UIColor.systemRed.cgColor)
        context.fillEllipse(in: CGRect(x: 100, y: 50, width: 100, height: 80))
        
        return UIGraphicsGetImageFromCurrentImageContext() ?? UIImage()
    }
    
    /// 驗證PDF內容
    private func validatePDFContent(at url: URL) {
        guard let pdfDocument = PDFDocument(url: url) else {
            XCTFail("無法加載PDF文檔")
            return
        }
        
        XCTAssertGreaterThan(pdfDocument.pageCount, 0, "PDF應該至少有一頁")
        XCTAssertLessThanOrEqual(pdfDocument.pageCount, 10, "PDF頁數應該合理")
        
        // 檢查第一頁是否包含基本內容
        if let firstPage = pdfDocument.page(at: 0) {
            let pageContent = firstPage.string ?? ""
            XCTAssertTrue(pageContent.contains("傷口測量"), "PDF應該包含傷口測量相關內容")
        }
    }
    
    /// 驗證批量報告PDF內容
    private func validateBatchPDFContent(at url: URL) {
        guard let pdfDocument = PDFDocument(url: url) else {
            XCTFail("無法加載批量報告PDF文檔")
            return
        }
        
        XCTAssertGreaterThan(pdfDocument.pageCount, 0, "批量報告PDF應該至少有一頁")
        
        if let firstPage = pdfDocument.page(at: 0) {
            let pageContent = firstPage.string ?? ""
            XCTAssertTrue(pageContent.contains("批量處理"), "批量報告PDF應該包含批量處理相關內容")
        }
    }
}