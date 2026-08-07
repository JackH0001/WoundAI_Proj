import XCTest
import UIKit
import Combine
@testable import WoundMeasurementApp

/// 批量處理服務單元測試
final class BatchProcessingServiceTests: XCTestCase {
    
    // MARK: - 測試屬性
    var batchService: BatchProcessingService!
    var cancellables: Set<AnyCancellable>!
    
    // MARK: - 測試生命週期
    override func setUpWithError() throws {
        super.setUp()
        batchService = BatchProcessingService.shared
        cancellables = Set<AnyCancellable>()
        
        // 重置服務狀態
        batchService.resetBatchProcessing()
    }
    
    override func tearDownWithError() throws {
        batchService = nil
        cancellables.removeAll()
        super.tearDown()
    }
    
    // MARK: - 批量處理核心功能測試
    
    /// 測試批量處理初始化
    func testBatchProcessingInitialization() throws {
        // Given
        let images = createTestImages(count: 3)
        let config = BatchProcessingService.BatchProcessingConfig()
        
        // When
        let expectation = XCTestExpectation(description: "批量處理初始化")
        
        batchService.$isProcessing
            .dropFirst()
            .sink { isProcessing in
                if isProcessing {
                    expectation.fulfill()
                }
            }
            .store(in: &cancellables)
        
        Task {
            await batchService.startBatchProcessing(images: images, config: config)
        }
        
        // Then
        wait(for: [expectation], timeout: 5.0)
        XCTAssertTrue(batchService.isProcessing)
        XCTAssertEqual(batchService.totalCount, 3)
        XCTAssertEqual(batchService.processedCount, 0)
    }
    
    /// 測試批量處理進度追蹤
    func testBatchProcessingProgress() throws {
        // Given
        let images = createTestImages(count: 5)
        let config = BatchProcessingService.BatchProcessingConfig(
            concurrentLimit: 2,
            enableProgressUpdates: true
        )
        
        var progressValues: [Double] = []
        
        // When
        let progressExpectation = XCTestExpectation(description: "進度更新")
        progressExpectation.expectedFulfillmentCount = 6 // 0% + 5個處理進度
        
        batchService.$progress
            .sink { progress in
                progressValues.append(progress)
                progressExpectation.fulfill()
            }
            .store(in: &cancellables)
        
        Task {
            await batchService.startBatchProcessing(images: images, config: config)
        }
        
        // Then
        wait(for: [progressExpectation], timeout: 10.0)
        
        // 驗證進度遞增
        for i in 1..<progressValues.count {
            XCTAssertGreaterThanOrEqual(progressValues[i], progressValues[i-1], "進度應該遞增")
        }
        
        // 最終進度應該是100%
        XCTAssertEqual(progressValues.last, 1.0, accuracy: 0.01, "最終進度應該為100%")
    }
    
    /// 測試批量處理結果收集
    func testBatchProcessingResults() throws {
        // Given
        let images = createTestImages(count: 3)
        let config = BatchProcessingService.BatchProcessingConfig()
        
        // When
        let completionExpectation = XCTestExpectation(description: "批量處理完成")
        
        batchService.$isProcessing
            .filter { !$0 }
            .dropFirst() // 忽略初始false值
            .sink { _ in
                completionExpectation.fulfill()
            }
            .store(in: &cancellables)
        
        Task {
            await batchService.startBatchProcessing(images: images, config: config)
        }
        
        // Then
        wait(for: [completionExpectation], timeout: 15.0)
        
        XCTAssertFalse(batchService.isProcessing)
        XCTAssertEqual(batchService.processedCount, 3)
        XCTAssertNotNil(batchService.currentReport)
        
        // 驗證報告內容
        let report = try XCTUnwrap(batchService.currentReport)
        XCTAssertEqual(report.totalImages, 3)
        XCTAssertGreaterThanOrEqual(report.successfulProcessing, 0)
        XCTAssertLessThanOrEqual(report.failedProcessing, 3)
        XCTAssertEqual(report.successfulProcessing + report.failedProcessing, 3)
    }
    
    /// 測試並發限制
    func testConcurrencyLimit() throws {
        // Given
        let images = createTestImages(count: 10)
        let config = BatchProcessingService.BatchProcessingConfig(
            concurrentLimit: 3,
            enableProgressUpdates: true
        )
        
        // When
        let startTime = Date()
        let completionExpectation = XCTestExpectation(description: "並發處理完成")
        
        Task {
            await batchService.startBatchProcessing(images: images, config: config)
            completionExpectation.fulfill()
        }
        
        // Then
        wait(for: [completionExpectation], timeout: 30.0)
        
        let processingTime = Date().timeIntervalSince(startTime)
        
        // 驗證並發處理確實提高了效率
        // 如果是序列處理，10張圖像大約需要20-30秒
        // 使用並發限制3，應該能在15秒內完成
        XCTAssertLessThan(processingTime, 20.0, "並發處理應該提高效率")
        
        // 驗證所有圖像都被處理
        XCTAssertEqual(batchService.processedCount, 10)
    }
    
    // MARK: - 錯誤處理測試
    
    /// 測試無效圖像處理
    func testInvalidImageHandling() throws {
        // Given
        var images = createTestImages(count: 2)
        // 添加無效圖像（空圖像）
        images.append(BatchProcessingService.BatchImageInput(image: UIImage(), name: "invalid"))
        
        let config = BatchProcessingService.BatchProcessingConfig()
        
        // When
        let completionExpectation = XCTestExpectation(description: "處理包含無效圖像的批次")
        
        Task {
            await batchService.startBatchProcessing(images: images, config: config)
            completionExpectation.fulfill()
        }
        
        // Then
        wait(for: [completionExpectation], timeout: 15.0)
        
        let report = try XCTUnwrap(batchService.currentReport)
        
        // 驗證錯誤處理
        XCTAssertGreaterThan(report.failedProcessing, 0, "應該有處理失敗的圖像")
        XCTAssertFalse(report.errors.isEmpty, "應該記錄錯誤信息")
        
        // 驗證成功率計算
        XCTAssertLessThan(report.successRate, 1.0, "成功率應該小於100%")
    }
    
    /// 測試批量處理中斷
    func testBatchProcessingCancellation() throws {
        // Given
        let images = createTestImages(count: 10)
        let config = BatchProcessingService.BatchProcessingConfig()
        
        // When
        let startExpectation = XCTestExpectation(description: "批量處理開始")
        
        batchService.$isProcessing
            .filter { $0 }
            .sink { _ in
                startExpectation.fulfill()
            }
            .store(in: &cancellables)
        
        Task {
            await batchService.startBatchProcessing(images: images, config: config)
        }
        
        wait(for: [startExpectation], timeout: 5.0)
        
        // 中斷處理
        batchService.stopBatchProcessing()
        
        // Then
        // 等待一段時間確保處理停止
        Thread.sleep(forTimeInterval: 2.0)
        
        let processedCount = batchService.processedCount
        XCTAssertLessThan(processedCount, 10, "處理應該被中斷")
        XCTAssertFalse(batchService.isProcessing, "處理狀態應該為false")
    }
    
    // MARK: - 配置測試
    
    /// 測試不同配置選項
    func testDifferentConfigurations() throws {
        // Given
        let images = createTestImages(count: 3)
        
        let configs = [
            BatchProcessingService.BatchProcessingConfig(
                enableCalibration: true,
                enableClassification: true,
                saveToHistory: true
            ),
            BatchProcessingService.BatchProcessingConfig(
                enableCalibration: false,
                enableClassification: false,
                saveToHistory: false
            )
        ]
        
        // When & Then
        for (index, config) in configs.enumerated() {
            let expectation = XCTestExpectation(description: "配置\(index)測試")
            
            Task {
                await batchService.startBatchProcessing(images: images, config: config)
                expectation.fulfill()
            }
            
            wait(for: [expectation], timeout: 15.0)
            
            let report = try XCTUnwrap(batchService.currentReport)
            XCTAssertEqual(report.totalImages, 3)
            
            // 重置服務狀態以進行下一次測試
            batchService.resetBatchProcessing()
        }
    }
    
    // MARK: - 性能測試
    
    /// 測試批量處理性能
    func testBatchProcessingPerformance() throws {
        // Given
        let images = createTestImages(count: 5)
        let config = BatchProcessingService.BatchProcessingConfig()
        
        // When
        measure {
            let expectation = XCTestExpectation(description: "性能測試")
            
            Task {
                await batchService.startBatchProcessing(images: images, config: config)
                expectation.fulfill()
            }
            
            wait(for: [expectation], timeout: 30.0)
        }
    }
    
    // MARK: - 輔助方法
    
    /// 創建測試用圖像
    private func createTestImages(count: Int) -> [BatchProcessingService.BatchImageInput] {
        var images: [BatchProcessingService.BatchImageInput] = []
        
        for i in 0..<count {
            let image = createTestUIImage(size: CGSize(width: 400, height: 300))
            images.append(BatchProcessingService.BatchImageInput(
                image: image,
                name: "test_image_\(i).jpg"
            ))
        }
        
        return images
    }
    
    /// 創建測試用UIImage
    private func createTestUIImage(size: CGSize) -> UIImage {
        let rect = CGRect(origin: .zero, size: size)
        UIGraphicsBeginImageContext(size)
        defer { UIGraphicsEndImageContext() }
        
        let context = UIGraphicsGetCurrentContext()!
        
        // 創建帶有圓形（模擬傷口）的測試圖像
        context.setFillColor(UIColor.systemBackground.cgColor)
        context.fill(rect)
        
        // 添加圓形傷口區域
        let woundRect = CGRect(
            x: size.width * 0.3,
            y: size.height * 0.3,
            width: size.width * 0.4,
            height: size.height * 0.4
        )
        context.setFillColor(UIColor.systemRed.cgColor)
        context.fillEllipse(in: woundRect)
        
        // 添加校正貼紙（小圓圈）
        let stickerRect = CGRect(
            x: size.width * 0.8,
            y: size.height * 0.1,
            width: size.width * 0.1,
            height: size.width * 0.1
        )
        context.setFillColor(UIColor.systemBlue.cgColor)
        context.fillEllipse(in: stickerRect)
        
        return UIGraphicsGetImageFromCurrentImageContext() ?? UIImage()
    }
}

// MARK: - 測試擴展

extension BatchProcessingServiceTests {
    
    /// 測試報告生成功能
    func testReportGeneration() throws {
        // Given
        let images = createTestImages(count: 2)
        let config = BatchProcessingService.BatchProcessingConfig()
        
        // When
        let expectation = XCTestExpectation(description: "報告生成測試")
        
        Task {
            await batchService.startBatchProcessing(images: images, config: config)
            expectation.fulfill()
        }
        
        wait(for: [expectation], timeout: 15.0)
        
        // Then
        let report = try XCTUnwrap(batchService.currentReport)
        
        // 驗證報告基本信息
        XCTAssertEqual(report.totalImages, 2)
        XCTAssertNotNil(report.timestamp)
        XCTAssertGreaterThan(report.processingDuration, 0)
        XCTAssertGreaterThan(report.averageProcessingTime, 0)
        
        // 驗證統計數據一致性
        XCTAssertEqual(
            report.successfulProcessing + report.failedProcessing,
            report.totalImages,
            "成功和失敗處理數量之和應該等於總數"
        )
        
        // 驗證成功率計算
        let expectedSuccessRate = Double(report.successfulProcessing) / Double(report.totalImages)
        XCTAssertEqual(report.successRate, expectedSuccessRate, accuracy: 0.001)
        
        // 驗證報告摘要
        XCTAssertFalse(report.summary.isEmpty, "報告摘要不應為空")
        XCTAssertTrue(report.summary.contains("批量處理"), "摘要應包含批量處理字樣")
    }
    
    /// 測試內存使用優化
    func testMemoryOptimization() throws {
        // Given
        let images = createTestImages(count: 8)
        let config = BatchProcessingService.BatchProcessingConfig(
            concurrentLimit: 2 // 限制並發數以控制內存使用
        )
        
        // When
        let expectation = XCTestExpectation(description: "內存優化測試")
        
        // 監控內存使用（簡化版本）
        let initialMemory = getCurrentMemoryUsage()
        
        Task {
            await batchService.startBatchProcessing(images: images, config: config)
            expectation.fulfill()
        }
        
        wait(for: [expectation], timeout: 30.0)
        
        // Then
        let finalMemory = getCurrentMemoryUsage()
        let memoryIncrease = finalMemory - initialMemory
        
        // 內存增長應該是合理的（這是一個估計值）
        XCTAssertLessThan(memoryIncrease, 100_000_000, "內存使用增長應該控制在100MB以內")
    }
    
    /// 獲取當前內存使用量（簡化版本）
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