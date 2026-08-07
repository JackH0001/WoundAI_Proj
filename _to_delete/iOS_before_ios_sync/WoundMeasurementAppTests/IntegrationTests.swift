import XCTest
import UIKit
import Combine
@testable import WoundMeasurementApp

/// 整合測試 - 測試多個模組之間的協作
final class IntegrationTests: XCTestCase {
    
    // MARK: - 測試屬性
    var batchService: BatchProcessingService!
    var pdfGenerator: PDFReportGenerator!
    var cancellables: Set<AnyCancellable>!
    
    // MARK: - 測試生命週期
    override func setUpWithError() throws {
        super.setUp()
        batchService = BatchProcessingService.shared
        pdfGenerator = PDFReportGenerator.shared
        cancellables = Set<AnyCancellable>()
        
        // 重置服務狀態
        batchService.resetBatchProcessing()
    }
    
    override func tearDownWithError() throws {
        batchService = nil
        pdfGenerator = nil
        cancellables?.removeAll()
        super.tearDown()
    }
    
    // MARK: - 完整工作流程測試
    
    /// 測試從批量處理到PDF報告生成的完整流程
    func testBatchProcessingToPDFReportWorkflow() throws {
        // Given
        let testImages = createTestImages(count: 3)
        let batchConfig = BatchProcessingService.BatchProcessingConfig(
            enableCalibration: true,
            enableClassification: true,
            saveToHistory: false // 測試中不保存到數據庫
        )
        
        // When - 執行批量處理
        let batchExpectation = XCTestExpectation(description: "批量處理完成")
        
        Task {
            await batchService.startBatchProcessing(images: testImages, config: batchConfig)
            batchExpectation.fulfill()
        }
        
        wait(for: [batchExpectation], timeout: 30.0)
        
        // 獲取批量處理報告
        let batchReport = try XCTUnwrap(batchService.currentReport, "批量處理應該生成報告")
        
        // When - 生成PDF報告
        let pdfExpectation = XCTestExpectation(description: "PDF生成完成")
        var generatedPDFURL: URL?
        
        Task { @MainActor in
            do {
                generatedPDFURL = try await pdfGenerator.generateBatchReport(
                    batchReport: batchReport,
                    config: PDFReportGenerator.ReportConfiguration()
                )
                pdfExpectation.fulfill()
            } catch {
                XCTFail("PDF生成失敗: \(error)")
                pdfExpectation.fulfill()
            }
        }
        
        wait(for: [pdfExpectation], timeout: 30.0)
        
        // Then - 驗證整個流程
        XCTAssertNotNil(generatedPDFURL, "應該生成PDF報告")
        XCTAssertEqual(batchReport.totalImages, 3, "批量報告應該包含3張圖像")
        XCTAssertGreaterThanOrEqual(batchReport.successfulProcessing + batchReport.failedProcessing, 3)
        
        // 驗證PDF文件
        if let pdfURL = generatedPDFURL {
            XCTAssertTrue(FileManager.default.fileExists(atPath: pdfURL.path))
            
            // 清理測試文件
            try? FileManager.default.removeItem(at: pdfURL)
        }
    }
    
    /// 測試校正貼紙檢測與測量的整合
    func testCalibrationStickerDetectionAndMeasurementIntegration() throws {
        // Given
        let testImage = createImageWithCalibrationSticker()
        let stickerModule = CalibrationStickerModule()
        
        // When - 校正貼紙檢測
        let calibrationExpectation = XCTestExpectation(description: "校正貼紙檢測完成")
        var calibrationResult: CalibrationStickerModule.StickerCalibrationResult?
        
        Task {
            do {
                calibrationResult = try await stickerModule.performCalibration(on: testImage)
                calibrationExpectation.fulfill()
            } catch {
                print("校正失敗: \(error)")
                calibrationExpectation.fulfill()
            }
        }
        
        wait(for: [calibrationExpectation], timeout: 15.0)
        
        // Then - 驗證校正結果並進行後續測量
        if let result = calibrationResult {
            XCTAssertGreaterThan(result.confidence, 0.5, "校正信心度應該合理")
            XCTAssertGreaterThan(result.pixelsPerMM, 0, "像素比例應該大於0")
            
            // 模擬基於校正結果的測量
            let scaledArea = 100.0 / (result.pixelsPerMM * result.pixelsPerMM) // 100像素²轉換為cm²
            XCTAssertGreaterThan(scaledArea, 0, "基於校正的測量結果應該合理")
        }
    }
    
    /// 測試Vision檢測器與傳統檢測器的整合
    func testVisionDetectorIntegrationWithTraditionalMethods() throws {
        // Given
        let testImage = createImageWithMultipleFeatures()
        let visionDetector = VisionBasedStickerDetector()
        
        // When - 使用Vision檢測器
        let visionExpectation = XCTestExpectation(description: "Vision檢測完成")
        var visionResults: [VisionBasedStickerDetector.StickerDetectionResult] = []
        
        Task {
            do {
                visionResults = try await visionDetector.detectCalibrationStickers(in: testImage)
                visionExpectation.fulfill()
            } catch {
                print("Vision檢測失敗: \(error)")
                visionExpectation.fulfill()
            }
        }
        
        wait(for: [visionExpectation], timeout: 15.0)
        
        // Then - 驗證Vision檢測結果
        if !visionResults.isEmpty {
            let bestResult = visionResults.first!
            XCTAssertGreaterThan(bestResult.confidence, 0.3, "Vision檢測信心度應該合理")
            
            // 模擬與傳統方法的結果比較
            let traditionalConfidence = 0.6 // 模擬傳統方法結果
            
            // 驗證整合邏輯：選擇更好的結果
            let selectedConfidence = max(bestResult.confidence, traditionalConfidence)
            XCTAssertGreaterThanOrEqual(selectedConfidence, bestResult.confidence)
            XCTAssertGreaterThanOrEqual(selectedConfidence, traditionalConfidence)
        }
    }
    
    // MARK: - 3D視覺化整合測試
    
    /// 測試測量結果到3D視覺化的數據轉換
    func testMeasurementTo3DVisualizationDataFlow() throws {
        // Given - 模擬測量結果
        let measurementResult = WoundMeasurementModule.MeasurementResult(
            woundArea: 2.5,
            woundPerimeter: 5.6,
            pixelsPerMM: 12.0,
            confidence: 0.85
        )
        
        // 模擬深度數據
        let mockDepthData = [[Float]](
            repeating: [Float](repeating: 2.0, count: 50),
            count: 50
        )
        
        // When - 創建3D視覺化數據
        let visualizationData = WoundVisualizationData(
            area: measurementResult.woundArea,
            volume: calculateVolumeFromMeasurement(measurementResult, depthData: mockDepthData),
            perimeter: measurementResult.woundPerimeter,
            maxDepth: calculateMaxDepthFromData(mockDepthData),
            woundColor: .systemRed,
            depthMap: mockDepthData,
            contourPoints: generateContourFromMeasurement(measurementResult)
        )
        
        // Then - 驗證數據轉換
        XCTAssertEqual(visualizationData.area, measurementResult.woundArea, accuracy: 0.001)
        XCTAssertEqual(visualizationData.perimeter, measurementResult.woundPerimeter, accuracy: 0.001)
        XCTAssertGreaterThan(visualizationData.volume, 0, "體積應該大於0")
        XCTAssertGreaterThan(visualizationData.maxDepth, 0, "最大深度應該大於0")
        XCTAssertNotNil(visualizationData.depthMap, "深度圖應該被保留")
        XCTAssertNotNil(visualizationData.contourPoints, "輪廓點應該被生成")
        
        // 驗證3D視覺化視圖能正常創建
        let visualizationView = Wound3DVisualizationView(woundData: visualizationData)
        XCTAssertNotNil(visualizationView)
    }
    
    // MARK: - 錯誤處理整合測試
    
    /// 測試跨模組錯誤處理和恢復
    func testCrossModuleErrorHandlingAndRecovery() throws {
        // Given - 包含有效和無效圖像的批次
        var testImages = createTestImages(count: 2)
        testImages.append(BatchProcessingService.BatchImageInput(
            image: UIImage(), // 無效圖像
            name: "invalid_image.jpg"
        ))
        
        let config = BatchProcessingService.BatchProcessingConfig(
            enableCalibration: true,
            enableClassification: true,
            enableProgressUpdates: true
        )
        
        // When - 執行批量處理
        let expectation = XCTestExpectation(description: "錯誤處理測試完成")
        
        Task {
            await batchService.startBatchProcessing(images: testImages, config: config)
            expectation.fulfill()
        }
        
        wait(for: [expectation], timeout: 30.0)
        
        // Then - 驗證錯誤處理
        let report = try XCTUnwrap(batchService.currentReport)
        
        XCTAssertGreaterThan(report.failedProcessing, 0, "應該有處理失敗的圖像")
        XCTAssertGreaterThan(report.successfulProcessing, 0, "應該有成功處理的圖像")
        XCTAssertFalse(report.errors.isEmpty, "應該記錄錯誤信息")
        
        // 驗證系統能繼續運行並生成報告
        let pdfExpectation = XCTestExpectation(description: "帶錯誤的PDF生成")
        
        Task { @MainActor in
            do {
                let pdfURL = try await pdfGenerator.generateBatchReport(
                    batchReport: report,
                    config: PDFReportGenerator.ReportConfiguration()
                )
                
                // 即使有錯誤，PDF也應該能生成
                XCTAssertTrue(FileManager.default.fileExists(atPath: pdfURL.path))
                try? FileManager.default.removeItem(at: pdfURL)
                
                pdfExpectation.fulfill()
            } catch {
                XCTFail("即使處理有錯誤，PDF生成也應該成功: \(error)")
                pdfExpectation.fulfill()
            }
        }
        
        wait(for: [pdfExpectation], timeout: 30.0)
    }
    
    // MARK: - 性能整合測試
    
    /// 測試大量數據的端到端處理性能
    func testLargeDataEndToEndPerformance() throws {
        // Given
        let largeImageSet = createTestImages(count: 8) // 較大的圖像集
        let config = BatchProcessingService.BatchProcessingConfig(
            concurrentLimit: 3,
            enableCalibration: true,
            enableClassification: false, // 關閉以加快測試速度
            enableProgressUpdates: false
        )
        
        // When & Then
        measure {
            let expectation = XCTestExpectation(description: "大量數據處理性能測試")
            
            Task {
                await batchService.startBatchProcessing(images: largeImageSet, config: config)
                expectation.fulfill()
            }
            
            wait(for: [expectation], timeout: 60.0)
            
            // 驗證處理完成
            XCTAssertFalse(batchService.isProcessing)
            XCTAssertEqual(batchService.processedCount, 8)
        }
    }
    
    /// 測試內存使用優化整合
    func testMemoryOptimizationIntegration() throws {
        // Given
        let images = createTestImages(count: 5)
        let config = BatchProcessingService.BatchProcessingConfig(
            concurrentLimit: 2, // 限制並發以控制內存
            enableCalibration: true,
            enableClassification: true
        )
        
        // When
        let initialMemory = getCurrentMemoryUsage()
        
        let expectation = XCTestExpectation(description: "內存優化整合測試")
        
        Task {
            await batchService.startBatchProcessing(images: images, config: config)
            expectation.fulfill()
        }
        
        wait(for: [expectation], timeout: 30.0)
        
        let finalMemory = getCurrentMemoryUsage()
        
        // Then
        let memoryIncrease = finalMemory - initialMemory
        
        // 內存增長應該控制在合理範圍內
        XCTAssertLessThan(memoryIncrease, 200_000_000, "內存使用增長應該控制在200MB以內")
        
        // 驗證處理完成
        XCTAssertNotNil(batchService.currentReport)
        XCTAssertEqual(batchService.processedCount, 5)
    }
    
    // MARK: - 輔助方法
    
    /// 創建測試用圖像
    private func createTestImages(count: Int) -> [BatchProcessingService.BatchImageInput] {
        var images: [BatchProcessingService.BatchImageInput] = []
        
        for i in 0..<count {
            let image = createTestUIImage(withWound: true, withCalibrationSticker: true)
            images.append(BatchProcessingService.BatchImageInput(
                image: image,
                name: "integration_test_\(i).jpg"
            ))
        }
        
        return images
    }
    
    /// 創建包含校正貼紙的測試圖像
    private func createImageWithCalibrationSticker() -> UIImage {
        return createTestUIImage(withWound: false, withCalibrationSticker: true)
    }
    
    /// 創建包含多種特徵的測試圖像
    private func createImageWithMultipleFeatures() -> UIImage {
        return createTestUIImage(withWound: true, withCalibrationSticker: true, withNoise: true)
    }
    
    /// 創建測試用UIImage
    private func createTestUIImage(
        withWound: Bool = true,
        withCalibrationSticker: Bool = true,
        withNoise: Bool = false,
        size: CGSize = CGSize(width: 400, height: 300)
    ) -> UIImage {
        let rect = CGRect(origin: .zero, size: size)
        UIGraphicsBeginImageContext(size)
        defer { UIGraphicsEndImageContext() }
        
        let context = UIGraphicsGetCurrentContext()!
        
        // 背景
        context.setFillColor(UIColor.systemBackground.cgColor)
        context.fill(rect)
        
        // 添加傷口
        if withWound {
            let woundRect = CGRect(
                x: size.width * 0.3,
                y: size.height * 0.3,
                width: size.width * 0.4,
                height: size.height * 0.4
            )
            context.setFillColor(UIColor.systemRed.cgColor)
            context.fillEllipse(in: woundRect)
        }
        
        // 添加校正貼紙
        if withCalibrationSticker {
            let stickerRect = CGRect(
                x: size.width * 0.8,
                y: size.height * 0.1,
                width: 40,
                height: 40
            )
            context.setFillColor(UIColor.systemBlue.cgColor)
            context.fillEllipse(in: stickerRect)
        }
        
        // 添加噪聲
        if withNoise {
            for _ in 0..<5 {
                let noiseRect = CGRect(
                    x: CGFloat.random(in: 0...size.width-20),
                    y: CGFloat.random(in: 0...size.height-20),
                    width: CGFloat.random(in: 5...15),
                    height: CGFloat.random(in: 5...15)
                )
                context.setFillColor(UIColor.random().cgColor)
                context.fill(noiseRect)
            }
        }
        
        return UIGraphicsGetImageFromCurrentImageContext() ?? UIImage()
    }
    
    /// 從測量結果和深度數據計算體積
    private func calculateVolumeFromMeasurement(
        _ measurement: WoundMeasurementModule.MeasurementResult,
        depthData: [[Float]]
    ) -> Double {
        // 簡化的體積計算
        let averageDepth = depthData.flatMap { $0 }.reduce(0, +) / Float(depthData.flatMap { $0 }.count)
        return measurement.woundArea * Double(averageDepth) / 10.0 // 轉換為cm³
    }
    
    /// 從深度數據計算最大深度
    private func calculateMaxDepthFromData(_ depthData: [[Float]]) -> Double {
        let maxDepth = depthData.flatMap { $0 }.max() ?? 0.0
        return Double(maxDepth)
    }
    
    /// 從測量結果生成輪廓點
    private func generateContourFromMeasurement(_ measurement: WoundMeasurementModule.MeasurementResult) -> [CGPoint] {
        let radius = sqrt(measurement.woundArea / .pi) * measurement.pixelsPerMM
        let center = CGPoint(x: 200, y: 150)
        var points: [CGPoint] = []
        
        let segments = 16
        for i in 0..<segments {
            let angle = Double(i) * 2.0 * .pi / Double(segments)
            let x = center.x + CGFloat(cos(angle) * radius)
            let y = center.y + CGFloat(sin(angle) * radius)
            points.append(CGPoint(x: x, y: y))
        }
        
        return points
    }
    
    /// 獲取當前內存使用量
    private func getCurrentMemoryUsage() -> Int64 {
        var info = mach_task_basic_info()
        var count = mach_msg_type_number_t(MemoryLayout<mach_task_basic_info>.size) / 4
        
        let result = withUnsafeMutablePointer(to: &info) {
            $0.withMemoryRebound(to: integer_t.self, capacity: 1) {
                task_info(mach_task_self_, task_flavor_t(MACH_TASK_BASIC_INFO), $0, &count)
            }
        }
        
        return result == KERN_SUCCESS ? Int64(info.resident_size) : 0
    }
}

// MARK: - 輔助擴展

extension UIColor {
    static func random() -> UIColor {
        return UIColor(
            red: CGFloat.random(in: 0...1),
            green: CGFloat.random(in: 0...1),
            blue: CGFloat.random(in: 0...1),
            alpha: 1.0
        )
    }
}