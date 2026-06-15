import XCTest
import Vision
import CoreImage
@testable import WoundMeasurementApp

/// Vision基礎校正貼紙檢測器單元測試
final class VisionBasedStickerDetectorTests: XCTestCase {
    
    // MARK: - 測試屬性
    var detector: VisionBasedStickerDetector!
    
    // MARK: - 測試生命週期
    override func setUpWithError() throws {
        super.setUp()
        detector = VisionBasedStickerDetector()
    }
    
    override func tearDownWithError() throws {
        detector = nil
        super.tearDown()
    }
    
    // MARK: - 校正貼紙檢測測試
    
    /// 測試單個校正貼紙檢測
    func testSingleStickerDetection() throws {
        // Given
        let testImage = createImageWithSingleSticker()
        
        // When
        let expectation = XCTestExpectation(description: "單個貼紙檢測")
        var detectionResults: [VisionBasedStickerDetector.StickerDetectionResult] = []
        
        Task {
            do {
                detectionResults = try await detector.detectCalibrationStickers(in: testImage)
                expectation.fulfill()
            } catch {
                XCTFail("檢測失敗: \(error)")
                expectation.fulfill()
            }
        }
        
        wait(for: [expectation], timeout: 10.0)
        
        // Then
        XCTAssertGreaterThan(detectionResults.count, 0, "應該檢測到至少一個校正貼紙")
        
        let bestResult = detectionResults.first!
        XCTAssertGreaterThan(bestResult.confidence, 0.5, "檢測信心度應該大於0.5")
        XCTAssertTrue(bestResult.boundingBox.width > 0, "邊界框寬度應該大於0")
        XCTAssertTrue(bestResult.boundingBox.height > 0, "邊界框高度應該大於0")
    }
    
    /// 測試多個校正貼紙檢測
    func testMultipleStickerDetection() throws {
        // Given
        let testImage = createImageWithMultipleStickers(count: 3)
        
        // When
        let expectation = XCTestExpectation(description: "多個貼紙檢測")
        var detectionResults: [VisionBasedStickerDetector.StickerDetectionResult] = []
        
        Task {
            do {
                detectionResults = try await detector.detectCalibrationStickers(in: testImage)
                expectation.fulfill()
            } catch {
                XCTFail("檢測失敗: \(error)")
                expectation.fulfill()
            }
        }
        
        wait(for: [expectation], timeout: 10.0)
        
        // Then
        XCTAssertGreaterThanOrEqual(detectionResults.count, 2, "應該檢測到至少2個校正貼紙")
        
        // 驗證結果按信心度排序
        for i in 1..<detectionResults.count {
            XCTAssertGreaterThanOrEqual(
                detectionResults[i-1].confidence,
                detectionResults[i].confidence,
                "結果應該按信心度降序排列"
            )
        }
    }
    
    /// 測試平面貼紙檢測（無凸點）
    func testFlatStickerDetection() throws {
        // Given
        let testImage = createImageWithFlatSticker()
        
        // When
        let expectation = XCTestExpectation(description: "平面貼紙檢測")
        var detectionResults: [VisionBasedStickerDetector.StickerDetectionResult] = []
        
        Task {
            do {
                detectionResults = try await detector.detectCalibrationStickers(in: testImage)
                expectation.fulfill()
            } catch {
                XCTFail("檢測失敗: \(error)")
                expectation.fulfill()
            }
        }
        
        wait(for: [expectation], timeout: 10.0)
        
        // Then
        XCTAssertGreaterThan(detectionResults.count, 0, "應該能檢測到平面校正貼紙")
        
        let result = detectionResults.first!
        XCTAssertGreaterThan(result.confidence, 0.4, "平面貼紙檢測信心度應該大於0.4")
        
        // 驗證檢測到的是圓形物體
        let aspectRatio = result.boundingBox.width / result.boundingBox.height
        XCTAssertLessThan(abs(aspectRatio - 1.0), 0.3, "檢測到的物體應該接近圓形")
    }
    
    /// 測試無校正貼紙圖像
    func testNoStickerImage() throws {
        // Given
        let testImage = createImageWithoutSticker()
        
        // When
        let expectation = XCTestExpectation(description: "無貼紙圖像檢測")
        var detectionResults: [VisionBasedStickerDetector.StickerDetectionResult] = []
        
        Task {
            do {
                detectionResults = try await detector.detectCalibrationStickers(in: testImage)
                expectation.fulfill()
            } catch {
                XCTFail("檢測失敗: \(error)")
                expectation.fulfill()
            }
        }
        
        wait(for: [expectation], timeout: 10.0)
        
        // Then
        XCTAssertEqual(detectionResults.count, 0, "沒有校正貼紙的圖像不應該有檢測結果")
    }
    
    // MARK: - 檢測精度測試
    
    /// 測試不同尺寸貼紙檢測
    func testDifferentStickerSizes() throws {
        let sizes: [CGFloat] = [20, 40, 60, 80] // 不同像素大小的貼紙
        
        for size in sizes {
            // Given
            let testImage = createImageWithSticker(radius: size)
            
            // When
            let expectation = XCTestExpectation(description: "貼紙尺寸\(size)檢測")
            var detectionResults: [VisionBasedStickerDetector.StickerDetectionResult] = []
            
            Task {
                do {
                    detectionResults = try await detector.detectCalibrationStickers(in: testImage)
                    expectation.fulfill()
                } catch {
                    XCTFail("尺寸\(size)檢測失敗: \(error)")
                    expectation.fulfill()
                }
            }
            
            wait(for: [expectation], timeout: 10.0)
            
            // Then
            XCTAssertGreaterThan(detectionResults.count, 0, "尺寸\(size)的貼紙應該被檢測到")
            
            if let result = detectionResults.first {
                XCTAssertGreaterThan(result.confidence, 0.3, "尺寸\(size)的檢測信心度應該合理")
            }
        }
    }
    
    /// 測試不同光照條件下的檢測
    func testDifferentLightingConditions() throws {
        let lightingLevels: [Float] = [0.3, 0.5, 0.7, 0.9] // 不同亮度級別
        
        for level in lightingLevels {
            // Given
            let testImage = createImageWithSticker(brightness: level)
            
            // When
            let expectation = XCTestExpectation(description: "亮度\(level)檢測")
            var detectionResults: [VisionBasedStickerDetector.StickerDetectionResult] = []
            
            Task {
                do {
                    detectionResults = try await detector.detectCalibrationStickers(in: testImage)
                    expectation.fulfill()
                } catch {
                    XCTFail("亮度\(level)檢測失敗: \(error)")
                    expectation.fulfill()
                }
            }
            
            wait(for: [expectation], timeout: 10.0)
            
            // Then
            // 在極低或極高亮度下可能檢測不到，但中等亮度下應該能檢測到
            if level >= 0.4 && level <= 0.8 {
                XCTAssertGreaterThan(detectionResults.count, 0, "亮度\(level)下應該檢測到貼紙")
            }
        }
    }
    
    // MARK: - 錯誤處理測試
    
    /// 測試無效圖像處理
    func testInvalidImageHandling() throws {
        // Given
        let invalidImage = UIImage() // 空圖像
        
        // When & Then
        let expectation = XCTestExpectation(description: "無效圖像處理")
        
        Task {
            do {
                let results = try await detector.detectCalibrationStickers(in: invalidImage)
                XCTAssertEqual(results.count, 0, "無效圖像應該返回空結果")
            } catch {
                // 期望拋出錯誤
                XCTAssertTrue(error is VisionBasedStickerDetector.DetectionError)
            }
            expectation.fulfill()
        }
        
        wait(for: [expectation], timeout: 5.0)
    }
    
    /// 測試檢測配置邊界值
    func testDetectionConfigurationBoundaries() throws {
        // 測試檢測配置的邊界值是否合理
        let config = VisionBasedStickerDetector.DetectionConfig.self
        
        // 驗證配置值在合理範圍內
        XCTAssertGreaterThan(config.circularityThreshold, 0.0)
        XCTAssertLessThan(config.circularityThreshold, 1.0)
        XCTAssertGreaterThan(config.minContourArea, 0.0)
        XCTAssertGreaterThan(config.colorVarianceThreshold, 0.0)
        XCTAssertGreaterThanOrEqual(config.flatStickerBonus, 0.0)
    }
    
    // MARK: - 性能測試
    
    /// 測試檢測性能
    func testDetectionPerformance() throws {
        // Given
        let testImage = createImageWithMultipleStickers(count: 5)
        
        // When & Then
        measure {
            let expectation = XCTestExpectation(description: "性能測試")
            
            Task {
                do {
                    _ = try await detector.detectCalibrationStickers(in: testImage)
                } catch {
                    XCTFail("性能測試中檢測失敗: \(error)")
                }
                expectation.fulfill()
            }
            
            wait(for: [expectation], timeout: 5.0)
        }
    }
    
    // MARK: - 輔助方法
    
    /// 創建包含單個校正貼紙的測試圖像
    private func createImageWithSingleSticker() -> UIImage {
        return createImageWithSticker(radius: 50, position: CGPoint(x: 200, y: 200))
    }
    
    /// 創建包含多個校正貼紙的測試圖像
    private func createImageWithMultipleStickers(count: Int) -> UIImage {
        let imageSize = CGSize(width: 800, height: 600)
        let rect = CGRect(origin: .zero, size: imageSize)
        
        UIGraphicsBeginImageContext(imageSize)
        defer { UIGraphicsEndImageContext() }
        
        let context = UIGraphicsGetCurrentContext()!
        
        // 背景
        context.setFillColor(UIColor.systemBackground.cgColor)
        context.fill(rect)
        
        // 添加多個貼紙
        let positions = [
            CGPoint(x: 150, y: 150),
            CGPoint(x: 400, y: 200),
            CGPoint(x: 600, y: 350),
            CGPoint(x: 200, y: 450),
            CGPoint(x: 650, y: 150)
        ]
        
        for i in 0..<min(count, positions.count) {
            let stickerRect = CGRect(
                x: positions[i].x - 25,
                y: positions[i].y - 25,
                width: 50,
                height: 50
            )
            
            context.setFillColor(UIColor.systemBlue.cgColor)
            context.fillEllipse(in: stickerRect)
        }
        
        return UIGraphicsGetImageFromCurrentImageContext() ?? UIImage()
    }
    
    /// 創建包含平面校正貼紙的測試圖像
    private func createImageWithFlatSticker() -> UIImage {
        let imageSize = CGSize(width: 400, height: 400)
        let rect = CGRect(origin: .zero, size: imageSize)
        
        UIGraphicsBeginImageContext(imageSize)
        defer { UIGraphicsEndImageContext() }
        
        let context = UIGraphicsGetCurrentContext()!
        
        // 背景
        context.setFillColor(UIColor.systemBackground.cgColor)
        context.fill(rect)
        
        // 平面校正貼紙（均勻顏色，邊緣稍微模糊）
        let stickerCenter = CGPoint(x: 200, y: 200)
        let stickerRadius: CGFloat = 40
        
        let stickerRect = CGRect(
            x: stickerCenter.x - stickerRadius,
            y: stickerCenter.y - stickerRadius,
            width: stickerRadius * 2,
            height: stickerRadius * 2
        )
        
        // 使用漸變創建更真實的平面貼紙效果
        let colorSpace = CGColorSpaceCreateDeviceRGB()
        let colors = [UIColor.systemBlue.cgColor, UIColor.systemBlue.withAlphaComponent(0.8).cgColor]
        let gradient = CGGradient(colorsSpace: colorSpace, colors: colors as CFArray, locations: nil)!
        
        context.saveGState()
        context.addEllipse(in: stickerRect)
        context.clip()
        context.drawRadialGradient(
            gradient,
            startCenter: stickerCenter,
            startRadius: 0,
            endCenter: stickerCenter,
            endRadius: stickerRadius,
            options: []
        )
        context.restoreGState()
        
        return UIGraphicsGetImageFromCurrentImageContext() ?? UIImage()
    }
    
    /// 創建不包含校正貼紙的測試圖像
    private func createImageWithoutSticker() -> UIImage {
        let imageSize = CGSize(width: 400, height: 400)
        let rect = CGRect(origin: .zero, size: imageSize)
        
        UIGraphicsBeginImageContext(imageSize)
        defer { UIGraphicsEndImageContext() }
        
        let context = UIGraphicsGetCurrentContext()!
        
        // 只有背景和一些噪聲
        context.setFillColor(UIColor.systemBackground.cgColor)
        context.fill(rect)
        
        // 添加一些隨機形狀作為噪聲
        for _ in 0..<10 {
            let randomRect = CGRect(
                x: CGFloat.random(in: 0...imageSize.width-20),
                y: CGFloat.random(in: 0...imageSize.height-20),
                width: CGFloat.random(in: 10...30),
                height: CGFloat.random(in: 10...30)
            )
            
            context.setFillColor(UIColor.random().cgColor)
            context.fill(randomRect)
        }
        
        return UIGraphicsGetImageFromCurrentImageContext() ?? UIImage()
    }
    
    /// 創建指定參數的校正貼紙圖像
    private func createImageWithSticker(
        radius: CGFloat = 40,
        position: CGPoint = CGPoint(x: 200, y: 200),
        brightness: Float = 0.7
    ) -> UIImage {
        let imageSize = CGSize(width: 400, height: 400)
        let rect = CGRect(origin: .zero, size: imageSize)
        
        UIGraphicsBeginImageContext(imageSize)
        defer { UIGraphicsEndImageContext() }
        
        let context = UIGraphicsGetCurrentContext()!
        
        // 背景（根據亮度調整）
        let bgColor = UIColor(white: CGFloat(brightness), alpha: 1.0)
        context.setFillColor(bgColor.cgColor)
        context.fill(rect)
        
        // 校正貼紙
        let stickerRect = CGRect(
            x: position.x - radius,
            y: position.y - radius,
            width: radius * 2,
            height: radius * 2
        )
        
        let stickerColor = brightness > 0.5 ? UIColor.systemBlue : UIColor.white
        context.setFillColor(stickerColor.cgColor)
        context.fillEllipse(in: stickerRect)
        
        return UIGraphicsGetImageFromCurrentImageContext() ?? UIImage()
    }
}

// MARK: - 測試輔助擴展

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