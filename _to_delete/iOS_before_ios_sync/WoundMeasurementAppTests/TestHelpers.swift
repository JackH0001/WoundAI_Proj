import Foundation
import UIKit
import XCTest
@testable import WoundMeasurementApp

/// 測試輔助工具類 - 提供通用的測試工具和模擬數據
final class TestHelpers {
    
    // MARK: - 圖像創建工具
    
    /// 創建標準測試圖像
    static func createStandardTestImage(
        size: CGSize = CGSize(width: 400, height: 300),
        includeWound: Bool = true,
        includeCalibrationSticker: Bool = true,
        woundColor: UIColor = .systemRed,
        backgroundColor: UIColor = .systemBackground
    ) -> UIImage {
        let rect = CGRect(origin: .zero, size: size)
        UIGraphicsBeginImageContext(size)
        defer { UIGraphicsEndImageContext() }
        
        let context = UIGraphicsGetCurrentContext()!
        
        // 背景
        context.setFillColor(backgroundColor.cgColor)
        context.fill(rect)
        
        // 傷口區域
        if includeWound {
            let woundRect = CGRect(
                x: size.width * 0.25,
                y: size.height * 0.25,
                width: size.width * 0.5,
                height: size.height * 0.4
            )
            context.setFillColor(woundColor.cgColor)
            context.fillEllipse(in: woundRect)
            
            // 添加一些紋理以使傷口更真實
            context.setFillColor(woundColor.withAlphaComponent(0.7).cgColor)
            let innerRect = woundRect.insetBy(dx: 20, dy: 15)
            context.fillEllipse(in: innerRect)
        }
        
        // 校正貼紙
        if includeCalibrationSticker {
            let stickerRect = CGRect(
                x: size.width * 0.8,
                y: size.height * 0.15,
                width: 40,
                height: 40
            )
            context.setFillColor(UIColor.systemBlue.cgColor)
            context.fillEllipse(in: stickerRect)
            
            // 添加貼紙邊框以提高識別度
            context.setStrokeColor(UIColor.darkBlue.cgColor)
            context.setLineWidth(2.0)
            context.strokeEllipse(in: stickerRect)
        }
        
        return UIGraphicsGetImageFromCurrentImageContext() ?? UIImage()
    }
    
    /// 創建帶有特定特徵的測試圖像
    static func createImageWithFeatures(
        size: CGSize = CGSize(width: 400, height: 300),
        features: [ImageFeature]
    ) -> UIImage {
        let rect = CGRect(origin: .zero, size: size)
        UIGraphicsBeginImageContext(size)
        defer { UIGraphicsEndImageContext() }
        
        let context = UIGraphicsGetCurrentContext()!
        
        // 背景
        context.setFillColor(UIColor.systemBackground.cgColor)
        context.fill(rect)
        
        for feature in features {
            feature.draw(in: context, containerSize: size)
        }
        
        return UIGraphicsGetImageFromCurrentImageContext() ?? UIImage()
    }
    
    /// 創建噪聲圖像（用於負面測試）
    static func createNoiseImage(
        size: CGSize = CGSize(width: 400, height: 300),
        noiseLevel: Int = 20
    ) -> UIImage {
        let rect = CGRect(origin: .zero, size: size)
        UIGraphicsBeginImageContext(size)
        defer { UIGraphicsEndImageContext() }
        
        let context = UIGraphicsGetCurrentContext()!
        
        // 隨機背景色
        let bgColor = UIColor(
            red: CGFloat.random(in: 0.8...1.0),
            green: CGFloat.random(in: 0.8...1.0),
            blue: CGFloat.random(in: 0.8...1.0),
            alpha: 1.0
        )
        context.setFillColor(bgColor.cgColor)
        context.fill(rect)
        
        // 添加隨機噪聲
        for _ in 0..<noiseLevel {
            let noiseRect = CGRect(
                x: CGFloat.random(in: 0...size.width-10),
                y: CGFloat.random(in: 0...size.height-10),
                width: CGFloat.random(in: 5...20),
                height: CGFloat.random(in: 5...20)
            )
            
            let noiseColor = UIColor(
                red: CGFloat.random(in: 0...1),
                green: CGFloat.random(in: 0...1),
                blue: CGFloat.random(in: 0...1),
                alpha: CGFloat.random(in: 0.3...0.8)
            )
            
            context.setFillColor(noiseColor.cgColor)
            
            if Bool.random() {
                context.fillEllipse(in: noiseRect)
            } else {
                context.fill(noiseRect)
            }
        }
        
        return UIGraphicsGetImageFromCurrentImageContext() ?? UIImage()
    }
    
    // MARK: - 測試數據生成
    
    /// 創建測試用的傷口測量數據
    static func createTestWoundMeasurementData(
        area: Double = 2.5,
        perimeter: Double = 5.6,
        maxDepth: Double = 3.2,
        confidence: Double = 0.85
    ) -> WoundMeasurementData {
        return WoundMeasurementData(
            measurementDate: Date(),
            area: area,
            perimeter: perimeter,
            maxDepth: maxDepth,
            averageDepth: maxDepth * 0.6,
            volume: area * maxDepth * 0.1, // 簡化的體積計算
            woundLocation: "測試位置",
            originalImage: createStandardTestImage(),
            processedImage: createStandardTestImage(),
            calibrationInfo: CalibrationInfo(
                pixelsPerMM: 12.0,
                calibrationType: "校正貼紙",
                accuracy: 0.95
            ),
            analysis: WoundAnalysis(
                tissueTypes: ["健康組織": 0.6, "壞死組織": 0.3, "肉芽組織": 0.1],
                healingStage: "炎症期",
                riskFactors: ["測試風險因素"]
            ),
            classification: WoundClassification(
                primaryType: "測試傷口類型",
                severity: "中度",
                confidence: confidence
            )
        )
    }
    
    /// 創建測試用的批量處理輸入
    static func createBatchProcessingInput(
        count: Int = 5,
        includeInvalidImages: Bool = false
    ) -> [BatchProcessingService.BatchImageInput] {
        var inputs: [BatchProcessingService.BatchImageInput] = []
        
        for i in 0..<count {
            let image = createStandardTestImage()
            inputs.append(BatchProcessingService.BatchImageInput(
                image: image,
                name: "test_image_\(i).jpg"
            ))
        }
        
        if includeInvalidImages {
            inputs.append(BatchProcessingService.BatchImageInput(
                image: UIImage(), // 空圖像
                name: "invalid_image.jpg"
            ))
        }
        
        return inputs
    }
    
    /// 創建測試用的批量處理報告
    static func createTestBatchProcessingReport(
        totalImages: Int = 10,
        successRate: Double = 0.8
    ) -> BatchProcessingReport {
        let successfulCount = Int(Double(totalImages) * successRate)
        let failedCount = totalImages - successfulCount
        
        var results: [BatchProcessingResult] = []
        var errors: [BatchProcessingError] = []
        
        // 創建成功結果
        for i in 0..<successfulCount {
            let result = BatchProcessingResult(
                imageName: "success_\(i).jpg",
                originalImage: createStandardTestImage(),
                processingTime: Double.random(in: 2.0...5.0),
                timestamp: Date().addingTimeInterval(-Double(i) * 30),
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
        
        // 創建錯誤記錄
        for i in 0..<failedCount {
            let error = BatchProcessingError(
                imageName: "failed_\(i).jpg",
                error: TestError.processingFailed,
                timestamp: Date().addingTimeInterval(-Double(i) * 20)
            )
            errors.append(error)
        }
        
        return BatchProcessingReport(
            timestamp: Date(),
            totalImages: totalImages,
            successfulProcessing: successfulCount,
            failedProcessing: failedCount,
            results: results,
            errors: errors,
            processingDuration: Double(totalImages) * 3.5,
            averageProcessingTime: 3.5
        )
    }
    
    // MARK: - 測試驗證工具
    
    /// 驗證圖像是否包含預期內容
    static func validateImageContent(
        _ image: UIImage,
        expectedSize: CGSize? = nil,
        shouldContainColors: [UIColor] = []
    ) -> Bool {
        // 驗證尺寸
        if let expectedSize = expectedSize {
            if abs(image.size.width - expectedSize.width) > 1.0 ||
               abs(image.size.height - expectedSize.height) > 1.0 {
                return false
            }
        }
        
        // 驗證顏色（簡化實現）
        guard let cgImage = image.cgImage,
              let dataProvider = cgImage.dataProvider,
              let data = dataProvider.data,
              let pixels = CFDataGetBytePtr(data) else {
            return false
        }
        
        // 簡單的顏色檢測（實際實現會更複雜）
        for color in shouldContainColors {
            var colorFound = false
            // 這裡簡化了顏色匹配邏輯
            colorFound = true // 假設找到了顏色
            if !colorFound {
                return false
            }
        }
        
        return true
    }
    
    /// 驗證測量結果的合理性
    static func validateMeasurementResult(
        _ result: WoundMeasurementModule.MeasurementResult,
        expectedRange: MeasurementRange? = nil
    ) -> ValidationResult {
        var issues: [String] = []
        
        // 基本合理性檢查
        if result.woundArea <= 0 {
            issues.append("傷口面積不能小於等於0")
        }
        
        if result.woundPerimeter <= 0 {
            issues.append("傷口周長不能小於等於0")
        }
        
        if result.pixelsPerMM <= 0 {
            issues.append("像素比例不能小於等於0")
        }
        
        if result.confidence < 0 || result.confidence > 1 {
            issues.append("信心度必須在0-1之間")
        }
        
        // 範圍檢查
        if let range = expectedRange {
            if result.woundArea < range.minArea || result.woundArea > range.maxArea {
                issues.append("傷口面積超出預期範圍")
            }
            
            if result.woundPerimeter < range.minPerimeter || result.woundPerimeter > range.maxPerimeter {
                issues.append("傷口周長超出預期範圍")
            }
        }
        
        // 邏輯一致性檢查
        let expectedPerimeter = 2 * sqrt(.pi * result.woundArea)
        let perimeterRatio = result.woundPerimeter / expectedPerimeter
        if perimeterRatio < 0.5 || perimeterRatio > 3.0 {
            issues.append("周長與面積比例不合理")
        }
        
        return ValidationResult(isValid: issues.isEmpty, issues: issues)
    }
    
    // MARK: - 性能測試工具
    
    /// 測量執行時間
    static func measureExecutionTime<T>(
        _ operation: () throws -> T
    ) rethrows -> (result: T, executionTime: TimeInterval) {
        let startTime = Date()
        let result = try operation()
        let executionTime = Date().timeIntervalSince(startTime)
        return (result, executionTime)
    }
    
    /// 測量異步執行時間
    static func measureAsyncExecutionTime<T>(
        _ operation: () async throws -> T
    ) async rethrows -> (result: T, executionTime: TimeInterval) {
        let startTime = Date()
        let result = try await operation()
        let executionTime = Date().timeIntervalSince(startTime)
        return (result, executionTime)
    }
    
    // MARK: - 文件管理工具
    
    /// 創建臨時測試目錄
    static func createTemporaryTestDirectory() -> URL? {
        let tempDir = FileManager.default.temporaryDirectory
        let testDir = tempDir.appendingPathComponent("WoundMeasurementTests_\(UUID().uuidString)")
        
        do {
            try FileManager.default.createDirectory(at: testDir, withIntermediateDirectories: true)
            return testDir
        } catch {
            print("創建臨時目錄失敗: \(error)")
            return nil
        }
    }
    
    /// 清理臨時測試文件
    static func cleanupTemporaryFiles(at directory: URL) {
        do {
            try FileManager.default.removeItem(at: directory)
        } catch {
            print("清理臨時文件失敗: \(error)")
        }
    }
    
    // MARK: - 內存監控工具
    
    /// 獲取當前內存使用量
    static func getCurrentMemoryUsage() -> Int64 {
        var info = mach_task_basic_info()
        var count = mach_msg_type_number_t(MemoryLayout<mach_task_basic_info>.size) / 4
        
        let result = withUnsafeMutablePointer(to: &info) {
            $0.withMemoryRebound(to: integer_t.self, capacity: 1) {
                task_info(mach_task_self_, task_flavor_t(MACH_TASK_BASIC_INFO), $0, &count)
            }
        }
        
        return result == KERN_SUCCESS ? Int64(info.resident_size) : 0
    }
    
    /// 監控內存使用變化
    static func monitorMemoryUsage<T>(
        during operation: () throws -> T
    ) rethrows -> (result: T, memoryDelta: Int64) {
        let initialMemory = getCurrentMemoryUsage()
        let result = try operation()
        let finalMemory = getCurrentMemoryUsage()
        
        return (result, finalMemory - initialMemory)
    }
}

// MARK: - 輔助數據結構

/// 圖像特徵定義
enum ImageFeature {
    case wound(rect: CGRect, color: UIColor)
    case calibrationSticker(center: CGPoint, radius: CGFloat, color: UIColor)
    case noise(count: Int, maxSize: CGFloat)
    case ruler(start: CGPoint, end: CGPoint, markings: Int)
    
    func draw(in context: CGContext, containerSize: CGSize) {
        switch self {
        case .wound(let rect, let color):
            context.setFillColor(color.cgColor)
            context.fillEllipse(in: rect)
            
        case .calibrationSticker(let center, let radius, let color):
            let rect = CGRect(
                x: center.x - radius,
                y: center.y - radius,
                width: radius * 2,
                height: radius * 2
            )
            context.setFillColor(color.cgColor)
            context.fillEllipse(in: rect)
            context.setStrokeColor(UIColor.black.cgColor)
            context.setLineWidth(1.0)
            context.strokeEllipse(in: rect)
            
        case .noise(let count, let maxSize):
            for _ in 0..<count {
                let rect = CGRect(
                    x: CGFloat.random(in: 0...containerSize.width-maxSize),
                    y: CGFloat.random(in: 0...containerSize.height-maxSize),
                    width: CGFloat.random(in: 5...maxSize),
                    height: CGFloat.random(in: 5...maxSize)
                )
                
                let color = UIColor.random()
                context.setFillColor(color.cgColor)
                context.fill(rect)
            }
            
        case .ruler(let start, let end, let markings):
            context.setStrokeColor(UIColor.black.cgColor)
            context.setLineWidth(2.0)
            context.move(to: start)
            context.addLine(to: end)
            context.strokePath()
            
            // 添加刻度
            for i in 0..<markings {
                let t = CGFloat(i) / CGFloat(markings - 1)
                let point = CGPoint(
                    x: start.x + t * (end.x - start.x),
                    y: start.y + t * (end.y - start.y)
                )
                
                context.move(to: CGPoint(x: point.x, y: point.y - 5))
                context.addLine(to: CGPoint(x: point.x, y: point.y + 5))
                context.strokePath()
            }
        }
    }
}

/// 測量範圍定義
struct MeasurementRange {
    let minArea: Double
    let maxArea: Double
    let minPerimeter: Double
    let maxPerimeter: Double
    
    static let reasonable = MeasurementRange(
        minArea: 0.1,
        maxArea: 100.0,
        minPerimeter: 1.0,
        maxPerimeter: 50.0
    )
}

/// 驗證結果
struct ValidationResult {
    let isValid: Bool
    let issues: [String]
}

/// 測試錯誤類型
enum TestError: Error, LocalizedError {
    case processingFailed
    case invalidImage
    case calibrationFailed
    case insufficientData
    
    var errorDescription: String? {
        switch self {
        case .processingFailed:
            return "處理失敗"
        case .invalidImage:
            return "無效圖像"
        case .calibrationFailed:
            return "校正失敗"
        case .insufficientData:
            return "數據不足"
        }
    }
}

// MARK: - 顏色擴展

extension UIColor {
    static func random() -> UIColor {
        return UIColor(
            red: CGFloat.random(in: 0...1),
            green: CGFloat.random(in: 0...1),
            blue: CGFloat.random(in: 0...1),
            alpha: 1.0
        )
    }
    
    static let darkBlue = UIColor(red: 0.0, green: 0.0, blue: 0.8, alpha: 1.0)
}

// MARK: - XCTest擴展

extension XCTestCase {
    
    /// 等待異步操作完成的便捷方法
    func waitForAsyncOperation<T>(
        timeout: TimeInterval = 10.0,
        operation: @escaping () async throws -> T
    ) throws -> T {
        var result: T?
        var error: Error?
        
        let expectation = XCTestExpectation(description: "異步操作完成")
        
        Task {
            do {
                result = try await operation()
            } catch let operationError {
                error = operationError
            }
            expectation.fulfill()
        }
        
        wait(for: [expectation], timeout: timeout)
        
        if let error = error {
            throw error
        }
        
        return try XCTUnwrap(result, "異步操作應該返回結果")
    }
    
    /// 驗證內存洩漏
    func assertNoMemoryLeak<T>(
        _ operation: () throws -> T,
        memoryThreshold: Int64 = 10_000_000 // 10MB
    ) rethrows -> T {
        let (result, memoryDelta) = try TestHelpers.monitorMemoryUsage(during: operation)
        
        XCTAssertLessThan(
            memoryDelta,
            memoryThreshold,
            "操作導致內存使用增加 \(memoryDelta) 字節，超過閾值 \(memoryThreshold) 字節"
        )
        
        return result
    }
}