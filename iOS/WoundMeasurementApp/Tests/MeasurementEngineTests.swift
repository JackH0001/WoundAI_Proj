import XCTest
@testable import WoundMeasurementApp

final class MeasurementEngineTests: XCTestCase {
    
    var measurementEngine: MeasurementEngine!
    
    override func setUpWithError() throws {
        measurementEngine = MeasurementEngine()
    }
    
    override func tearDownWithError() throws {
        measurementEngine = nil
    }
    
    func testPixelScaleConversion() throws {
        // 測試案例 1: 正常的貼紙校準 (20mm = 50 pixels)
        // 50 pixels/20mm = 2.5 pixels/mm
        measurementEngine.updatePixelScale(2.5)
        let scale1 = measurementEngine.getCurrentPixelScale()
        
        // 期望值: 1/(2.5*10) = 0.04 cm/pixel
        let expected1 = 0.04
        XCTAssertEqual(scale1, expected1, accuracy: 0.0001, "2.5 pixels/mm 應轉換為 0.04 cm/pixel")
        
        // 測試案例 2: 高解析度情況 (20mm = 100 pixels)
        // 100 pixels/20mm = 5.0 pixels/mm
        measurementEngine.updatePixelScale(5.0)
        let scale2 = measurementEngine.getCurrentPixelScale()
        
        // 期望值: 1/(5.0*10) = 0.02 cm/pixel
        let expected2 = 0.02
        XCTAssertEqual(scale2, expected2, accuracy: 0.0001, "5.0 pixels/mm 應轉換為 0.02 cm/pixel")
        
        // 測試案例 3: 異常值測試 - 太小
        measurementEngine.updatePixelScale(0.5)
        let scale3 = measurementEngine.getCurrentPixelScale()
        
        // 應該拒絕並回退到相機參數
        XCTAssertNotEqual(scale3, 0.2, "異常小值應被拒絕")
        
        // 測試案例 4: 異常值測試 - 太大
        measurementEngine.updatePixelScale(100.0)
        let scale4 = measurementEngine.getCurrentPixelScale()
        
        // 應該拒絕並回退到相機參數
        XCTAssertNotEqual(scale4, 0.001, "異常大值應被拒絕")
    }
    
    func testAreaCalculationWithPixelScale() throws {
        // 設定 5 pixels/mm 的校準
        measurementEngine.updatePixelScale(5.0) // 0.02 cm/pixel
        
        // 創建一個 100x100 像素的正方形輪廓
        let squarePoints = [
            CGPoint(x: 0.0, y: 0.0),
            CGPoint(x: 0.1, y: 0.0),  // 正規化坐標，實際是 100 pixels (假設 1000px 圖片)
            CGPoint(x: 0.1, y: 0.1),
            CGPoint(x: 0.0, y: 0.1)
        ]
        
        let contour = WoundContour(
            points: squarePoints,
            area: 100 * 100, // 像素面積
            perimeter: 100 * 4 // 像素周長
        )
        
        // 期望面積: 10000 pixels² × (0.02 cm/pixel)² = 10000 × 0.0004 = 4 cm²
        // 實際測試需要模擬 calculateRealArea 函數的行為
        // 這裡只是驗證概念
    }
    
    func testCalibrationConsistencyValidation() throws {
        let lidarScale = 0.025  // cm/pixel
        let stickerScale = 0.024 // cm/pixel
        
        let result = measurementEngine.validateCalibrationConsistency(
            lidarCmPerPixel: lidarScale,
            stickerCmPerPixel: stickerScale
        )
        
        // 差異: |0.025-0.024|/0.024 * 100 = 4.17% < 8%
        XCTAssertTrue(result.isConsistent, "4.17% 的差異應該被認為是一致的")
        
        let bigDifference = measurementEngine.validateCalibrationConsistency(
            lidarCmPerPixel: 0.03,
            stickerCmPerPixel: 0.02
        )
        
        // 差異: |0.03-0.02|/0.02 * 100 = 50% > 8%
        XCTAssertFalse(bigDifference.isConsistent, "50% 的差異應該被標記為不一致")
    }
}