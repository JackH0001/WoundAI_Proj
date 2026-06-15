import Foundation
import UIKit
import CoreImage

/// 🔧 面積計算校正統一工具
/// 解決校正貼紙面積計算誤差問題，統一所有模組的計算邏輯
struct AreaCalibrationHelper {
    
    // MARK: - 標準校正貼紙規格
    
    /// 校正貼紙標準直徑（毫米）
    static let standardStickerDiameterMM: Double = 20.0
    
    /// 校正貼紙標準半徑（毫米）
    static let standardStickerRadiusMM: Double = standardStickerDiameterMM / 2.0  // 10.0mm
    
    /// 校正貼紙標準面積（平方厘米）
    static let standardStickerAreaCm2: Double = Double.pi * (standardStickerRadiusMM / 10.0).squared  // π × (1cm)² = 3.1416
    
    // MARK: - 像素比例計算
    
    /// 從檢測到的圓形校正貼紙計算準確的像素比例
    /// - Parameters:
    ///   - detectedRadiusPixels: 檢測到的貼紙半徑（像素）
    ///   - detectedDiameterPixels: 檢測到的貼紙直徑（像素，可選）
    /// - Returns: (pixelsPerMM, confidence, validationResult)
    static func calculatePixelScale(
        detectedRadiusPixels: Double,
        detectedDiameterPixels: Double? = nil
    ) -> (pixelsPerMM: Double, confidence: Double, validation: AreaValidationResult) {
        
        let diameterPixels = detectedDiameterPixels ?? (detectedRadiusPixels * 2.0)
        
        // 核心計算：pixels/mm = detected_diameter_pixels / standard_diameter_mm
        let pixelsPerMM = diameterPixels / standardStickerDiameterMM
        
        // 面積驗證：檢查計算出的像素比例是否產生正確的貼紙面積
        let validation = validateStickerArea(
            detectedRadiusPixels: detectedRadiusPixels,
            pixelsPerMM: pixelsPerMM
        )
        
        // 計算置信度
        let confidence = calculateConfidence(
            pixelsPerMM: pixelsPerMM,
            areaError: validation.areaErrorPercent,
            radiusPixels: detectedRadiusPixels
        )
        
        print("🎯 AreaCalibrationHelper: 像素比例計算結果")
        print("  - 檢測直徑: \(String(format: "%.1f", diameterPixels)) px")
        print("  - 像素比例: \(String(format: "%.3f", pixelsPerMM)) pixels/mm")
        print("  - 面積誤差: \(String(format: "%.1f", validation.areaErrorPercent))%")
        print("  - 置信度: \(String(format: "%.2f", confidence))")
        
        return (pixelsPerMM: pixelsPerMM, confidence: confidence, validation: validation)
    }
    
    // MARK: - 面積驗證
    
    /// 驗證校正貼紙面積計算的準確性
    static func validateStickerArea(
        detectedRadiusPixels: Double,
        pixelsPerMM: Double
    ) -> AreaValidationResult {
        
        // 計算cm/pixel比例
        let cmPerPixel = 1.0 / (pixelsPerMM * 10.0)
        
        // 將像素半徑轉換為厘米
        let radiusCm = detectedRadiusPixels * cmPerPixel
        
        // 計算實際面積（平方厘米）
        let calculatedAreaCm2 = Double.pi * radiusCm * radiusCm
        
        // 計算誤差百分比
        let areaErrorPercent = abs(calculatedAreaCm2 - standardStickerAreaCm2) / standardStickerAreaCm2 * 100.0
        
        // 判斷準確性等級
        let accuracyLevel: AccuracyLevel
        if areaErrorPercent <= 5.0 {
            accuracyLevel = .excellent
        } else if areaErrorPercent <= 10.0 {
            accuracyLevel = .good
        } else if areaErrorPercent <= 20.0 {
            accuracyLevel = .acceptable
        } else {
            accuracyLevel = .poor
        }
        
        return AreaValidationResult(
            expectedAreaCm2: standardStickerAreaCm2,
            calculatedAreaCm2: calculatedAreaCm2,
            areaErrorPercent: areaErrorPercent,
            accuracyLevel: accuracyLevel,
            cmPerPixel: cmPerPixel,
            pixelsPerMM: pixelsPerMM
        )
    }
    
    // MARK: - 傷口面積計算
    
    /// 使用校正後的像素比例計算傷口面積
    /// - Parameters:
    ///   - areaPixels: 傷口區域面積（像素平方）
    ///   - pixelsPerMM: 校正得到的像素比例
    /// - Returns: 傷口實際面積（平方厘米）
    static func calculateWoundArea(
        areaPixels: Double,
        pixelsPerMM: Double
    ) -> Double {
        // 面積轉換公式：area_cm² = area_pixels × (cm/pixel)²
        let cmPerPixel = 1.0 / (pixelsPerMM * 10.0)
        let areaCm2 = areaPixels * cmPerPixel * cmPerPixel
        
        return areaCm2
    }
    
    // MARK: - 置信度計算
    
    private static func calculateConfidence(
        pixelsPerMM: Double,
        areaError: Double,
        radiusPixels: Double
    ) -> Double {
        // 像素比例合理性 (3-60 pixels/mm)
        let rangeScore: Double
        if pixelsPerMM >= 8.0 && pixelsPerMM <= 40.0 {
            rangeScore = 1.0  // 最佳範圍
        } else if pixelsPerMM >= 5.0 && pixelsPerMM <= 50.0 {
            rangeScore = 0.8  // 良好範圍
        } else if pixelsPerMM >= 3.0 && pixelsPerMM <= 60.0 {
            rangeScore = 0.6  // 可接受範圍
        } else {
            rangeScore = 0.3  // 範圍外
        }
        
        // 面積誤差分數
        let areaScore: Double
        if areaError <= 5.0 {
            areaScore = 1.0
        } else if areaError <= 10.0 {
            areaScore = 0.9
        } else if areaError <= 20.0 {
            areaScore = 0.7
        } else if areaError <= 30.0 {
            areaScore = 0.5
        } else {
            areaScore = 0.3
        }
        
        // 檢測尺寸分數（貼紙在圖像中的合理大小）
        let sizeScore: Double
        if radiusPixels >= 20.0 && radiusPixels <= 200.0 {
            sizeScore = 1.0
        } else if radiusPixels >= 10.0 && radiusPixels <= 300.0 {
            sizeScore = 0.8
        } else {
            sizeScore = 0.5
        }
        
        // 綜合置信度
        let confidence = (rangeScore + areaScore + sizeScore) / 3.0
        return min(1.0, max(0.0, confidence))
    }
    
    // MARK: - 工具方法
    
    /// 驗證像素比例是否在合理範圍內
    static func isPixelScaleReasonable(_ pixelsPerMM: Double) -> Bool {
        return pixelsPerMM >= 3.0 && pixelsPerMM <= 60.0
    }
    
    /// 獲取像素比例的描述性評估
    static func getPixelScaleAssessment(_ pixelsPerMM: Double) -> String {
        switch pixelsPerMM {
        case 8.0...40.0:
            return "最佳範圍"
        case 5.0...50.0:
            return "良好範圍"
        case 3.0...60.0:
            return "可接受範圍"
        default:
            return "超出建議範圍"
        }
    }
}

// MARK: - 支援結構

/// 面積驗證結果
struct AreaValidationResult {
    let expectedAreaCm2: Double      // 預期面積
    let calculatedAreaCm2: Double    // 計算面積
    let areaErrorPercent: Double     // 誤差百分比
    let accuracyLevel: AccuracyLevel // 準確性等級
    let cmPerPixel: Double          // cm/像素比例
    let pixelsPerMM: Double         // 像素/毫米比例
    
    var isAcceptable: Bool {
        return areaErrorPercent <= 30.0 && accuracyLevel != .poor
    }
    
    var description: String {
        return """
        面積驗證結果:
        - 預期面積: \(String(format: "%.4f", expectedAreaCm2)) cm²
        - 計算面積: \(String(format: "%.4f", calculatedAreaCm2)) cm²
        - 誤差: \(String(format: "%.1f", areaErrorPercent))%
        - 等級: \(accuracyLevel.rawValue)
        """
    }
}

/// 準確性等級
enum AccuracyLevel: String, CaseIterable {
    case excellent = "優秀"     // ≤5%誤差
    case good = "良好"          // ≤10%誤差
    case acceptable = "可接受"   // ≤20%誤差
    case poor = "較差"          // >20%誤差
}

// MARK: - 數學工具擴展

private extension Double {
    var squared: Double {
        return self * self
    }
}