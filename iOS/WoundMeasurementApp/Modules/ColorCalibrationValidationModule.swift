import SwiftUI
import Foundation
import simd

// MARK: - 色彩校正精度驗證模組
@MainActor
class ColorCalibrationValidationModule: ObservableObject {
    @Published var validationResult: ColorCalibrationValidationResult?
    @Published var isValidating = false
    @Published var validationStatus = "準備進行色彩校正精度驗證"
    
    // 標準色彩值（CIE Lab色彩空間，更適合色差計算）
    private let standardColorsLab: [String: SIMD3<Double>] = [
        "red": SIMD3<Double>(53.2, 80.1, 67.2),      // sRGB(255,0,0) in Lab
        "yellow": SIMD3<Double>(97.1, -21.6, 94.5),  // sRGB(255,255,0) in Lab
        "green": SIMD3<Double>(87.7, -86.2, 83.2),   // sRGB(0,255,0) in Lab
        "blue": SIMD3<Double>(32.3, 79.2, -107.9),   // sRGB(0,0,255) in Lab
        "gray": SIMD3<Double>(20.5, 0.0, 0.0)        // 18% Gray in Lab
    ]
    
    // 色差評級標準
    private let deltaEThresholds = [
        "excellent": 1.0,    // ΔE < 1.0 優秀
        "good": 2.0,         // ΔE < 2.0 良好
        "acceptable": 5.0,   // ΔE < 5.0 可接受
        "poor": 10.0         // ΔE < 10.0 較差
    ]
    
    // MARK: - 主驗證函數
    func validateColorCalibration(
        detectedColors: [DetectedColorPoint],
        correctedColors: [DetectedColorPoint]? = nil,
        colorCorrectionMatrix: [[Double]]
    ) async throws -> ColorCalibrationValidationResult {
        
        isValidating = true
        validationStatus = "正在進行色彩校正精度驗證..."
        
        defer {
            Task { @MainActor in
                isValidating = false
            }
        }
        
        do {
            // 1. 計算校正前色差
            validationStatus = "計算校正前色差..."
            let beforeCorrection = calculateColorAccuracy(detectedColors, isAfterCorrection: false)
            
            // 2. 如果有校正後的顏色，計算校正後色差
            var afterCorrection: ColorAccuracyMetrics?
            if let corrected = correctedColors {
                validationStatus = "計算校正後色差..."
                afterCorrection = calculateColorAccuracy(corrected, isAfterCorrection: true)
            }
            
            // 3. 分析色彩矩陣品質
            validationStatus = "分析色彩校正矩陣..."
            let matrixAnalysis = analyzeColorMatrix(colorCorrectionMatrix)
            
            // 4. 計算整體校正效果
            validationStatus = "計算整體校正效果..."
            let improvement = calculateImprovement(before: beforeCorrection, after: afterCorrection)
            
            // 5. 生成建議
            validationStatus = "生成校正建議..."
            let recommendations = generateRecommendations(
                beforeMetrics: beforeCorrection,
                afterMetrics: afterCorrection,
                matrixAnalysis: matrixAnalysis
            )
            
            // 6. 評估校正等級
            let overallGrade = calculateOverallGrade(
                beforeMetrics: beforeCorrection,
                afterMetrics: afterCorrection,
                improvement: improvement
            )
            
            let result = ColorCalibrationValidationResult(
                beforeCorrection: beforeCorrection,
                afterCorrection: afterCorrection,
                matrixAnalysis: matrixAnalysis,
                improvement: improvement,
                overallGrade: overallGrade,
                recommendations: recommendations,
                timestamp: Date()
            )
            
            validationResult = result
            validationStatus = "驗證完成！"
            
            return result
            
        } catch {
            validationStatus = "驗證失敗：\(error.localizedDescription)"
            throw error
        }
    }
    
    // MARK: - 色彩精度計算
    private func calculateColorAccuracy(_ colorPoints: [DetectedColorPoint], isAfterCorrection: Bool) -> ColorAccuracyMetrics {
        var colorDeltas: [String: Double] = [:]
        var totalDeltaE: Double = 0
        var validColors = 0
        
        for point in colorPoints {
            guard let standardLab = standardColorsLab[point.colorName.lowercased()] else { continue }
            
            // 將sRGB轉換為Lab
            let actualLab = srgbToLab(point.actualColor)
            
            // 計算ΔE (CIE1976)
            let deltaE = calculateDeltaE76(lab1: standardLab, lab2: actualLab)
            
            colorDeltas[point.colorName] = deltaE
            totalDeltaE += deltaE
            validColors += 1
            
            print("\(isAfterCorrection ? "校正後" : "校正前") \(point.colorName) 色差: ΔE = \(String(format: "%.2f", deltaE))")
        }
        
        let averageDeltaE = validColors > 0 ? totalDeltaE / Double(validColors) : 999.0
        let grade = gradeColorAccuracy(averageDeltaE)
        
        // 計算色彩一致性（標準差）
        let deltaValues = Array(colorDeltas.values)
        let consistency = calculateConsistency(deltaValues)
        
        return ColorAccuracyMetrics(
            individualDeltas: colorDeltas,
            averageDeltaE: averageDeltaE,
            maxDeltaE: deltaValues.max() ?? 0,
            minDeltaE: deltaValues.min() ?? 0,
            consistency: consistency,
            grade: grade,
            colorCount: validColors
        )
    }
    
    // MARK: - 色彩空間轉換
    private func srgbToLab(_ rgb: SIMD3<Double>) -> SIMD3<Double> {
        // 簡化的sRGB到Lab轉換（實際應用中建議使用專業色彩管理庫）
        
        // Step 1: sRGB to Linear RGB
        let linearRGB = SIMD3<Double>(
            srgbGammaCorrection(rgb.x),
            srgbGammaCorrection(rgb.y),  
            srgbGammaCorrection(rgb.z)
        )
        
        // Step 2: Linear RGB to XYZ (using sRGB matrix)
        let xyz = SIMD3<Double>(
            0.4124564 * linearRGB.x + 0.3575761 * linearRGB.y + 0.1804375 * linearRGB.z,
            0.2126729 * linearRGB.x + 0.7151522 * linearRGB.y + 0.0721750 * linearRGB.z,
            0.0193339 * linearRGB.x + 0.1191920 * linearRGB.y + 0.9503041 * linearRGB.z
        )
        
        // Step 3: XYZ to Lab (using D65 illuminant)
        let xn = 0.95047, yn = 1.0, zn = 1.08883 // D65 values
        
        let fx = labF(xyz.x / xn)
        let fy = labF(xyz.y / yn)
        let fz = labF(xyz.z / zn)
        
        let L = 116.0 * fy - 16.0
        let a = 500.0 * (fx - fy)
        let b = 200.0 * (fy - fz)
        
        return SIMD3<Double>(L, a, b)
    }
    
    private func srgbGammaCorrection(_ value: Double) -> Double {
        if value <= 0.04045 {
            return value / 12.92
        } else {
            return pow((value + 0.055) / 1.055, 2.4)
        }
    }
    
    private func labF(_ t: Double) -> Double {
        let delta = 6.0/29.0
        if t > pow(delta, 3) {
            return pow(t, 1.0/3.0)
        } else {
            return t / (3.0 * delta * delta) + 4.0/29.0
        }
    }
    
    // MARK: - 色差計算
    private func calculateDeltaE76(lab1: SIMD3<Double>, lab2: SIMD3<Double>) -> Double {
        // CIE1976 ΔE*ab 色差公式
        let deltaL = lab1.x - lab2.x
        let deltaA = lab1.y - lab2.y
        let deltaB = lab1.z - lab2.z
        
        return sqrt(deltaL * deltaL + deltaA * deltaA + deltaB * deltaB)
    }
    
    // MARK: - 色彩矩陣分析
    private func analyzeColorMatrix(_ matrix: [[Double]]) -> ColorMatrixAnalysis {
        guard matrix.count == 3, matrix.allSatisfy({ $0.count == 3 }) else {
            return ColorMatrixAnalysis(
                determinant: 0,
                condition: 999,
                isWellConditioned: false,
                hasNegativeValues: false,
                diagonalDominance: 0,
                matrixType: "invalid"
            )
        }
        
        // 計算行列式
        let det = calculateDeterminant3x3(matrix)
        
        // 計算條件數（簡化版）
        let condition = estimateConditionNumber(matrix)
        
        // 檢查負值
        let hasNegative = matrix.flatMap { $0 }.contains { $0 < 0 }
        
        // 計算對角線優勢度
        let diagonalDominance = calculateDiagonalDominance(matrix)
        
        // 判斷矩陣類型
        let matrixType = classifyMatrixType(matrix)
        
        let isWellConditioned = condition < 10.0 && abs(det) > 0.001
        
        return ColorMatrixAnalysis(
            determinant: det,
            condition: condition,
            isWellConditioned: isWellConditioned,
            hasNegativeValues: hasNegative,
            diagonalDominance: diagonalDominance,
            matrixType: matrixType
        )
    }
    
    private func calculateDeterminant3x3(_ matrix: [[Double]]) -> Double {
        let m = matrix
        return m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1]) -
               m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0]) +
               m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])
    }
    
    private func estimateConditionNumber(_ matrix: [[Double]]) -> Double {
        // 簡化的條件數估計：最大對角元素/最小對角元素
        let diagonals = [matrix[0][0], matrix[1][1], matrix[2][2]]
        let maxDiag = diagonals.max() ?? 1.0
        let minDiag = diagonals.min() ?? 1.0
        
        return minDiag > 0.001 ? maxDiag / minDiag : 999.0
    }
    
    private func calculateDiagonalDominance(_ matrix: [[Double]]) -> Double {
        var dominance = 0.0
        
        for i in 0..<3 {
            let diagonal: Double = abs(matrix[i][i])
            let offDiagonalSum: Double = (0..<3).reduce(0.0) { partialSum, j in
                i != j ? partialSum + abs(matrix[i][j]) : partialSum
            }
            
            if diagonal > 0.001 {
                dominance += diagonal / (diagonal + offDiagonalSum)
            }
        }
        
        return dominance / 3.0
    }
    
    private func classifyMatrixType(_ matrix: [[Double]]) -> String {
        let isDiagonal = isNearlyDiagonal(matrix)
        let isIdentity = isNearlyIdentity(matrix)
        
        if isIdentity {
            return "identity"
        } else if isDiagonal {
            return "diagonal"
        } else {
            return "general"
        }
    }
    
    private func isNearlyDiagonal(_ matrix: [[Double]], tolerance: Double = 0.01) -> Bool {
        for i in 0..<3 {
            for j in 0..<3 {
                if i != j && abs(matrix[i][j]) > tolerance {
                    return false
                }
            }
        }
        return true
    }
    
    private func isNearlyIdentity(_ matrix: [[Double]], tolerance: Double = 0.05) -> Bool {
        for i in 0..<3 {
            for j in 0..<3 {
                let expected: Double = i == j ? 1.0 : 0.0
                if abs(matrix[i][j] - expected) > tolerance {
                    return false
                }
            }
        }
        return true
    }
    
    // MARK: - 輔助函數
    private func gradeColorAccuracy(_ deltaE: Double) -> String {
        if deltaE <= deltaEThresholds["excellent"]! {
            return "excellent"
        } else if deltaE <= deltaEThresholds["good"]! {
            return "good"
        } else if deltaE <= deltaEThresholds["acceptable"]! {
            return "acceptable"
        } else if deltaE <= deltaEThresholds["poor"]! {
            return "poor"
        } else {
            return "unacceptable"
        }
    }
    
    private func calculateConsistency(_ values: [Double]) -> Double {
        guard values.count > 1 else { return 0.0 }
        
        let mean = values.reduce(0, +) / Double(values.count)
        let variance = values.reduce(0) { sum, value in
            sum + pow(value - mean, 2)
        } / Double(values.count - 1)
        
        return sqrt(variance) // 標準差
    }
    
    private func calculateImprovement(before: ColorAccuracyMetrics, after: ColorAccuracyMetrics?) -> ColorImprovementMetrics {
        guard let after = after else {
            return ColorImprovementMetrics(
                deltaEImprovement: 0,
                consistencyImprovement: 0,
                gradeImprovement: 0,
                percentImprovement: 0,
                isImproved: false
            )
        }
        
        let deltaEImprovement = before.averageDeltaE - after.averageDeltaE
        let consistencyImprovement = before.consistency - after.consistency
        let percentImprovement = before.averageDeltaE > 0 ? (deltaEImprovement / before.averageDeltaE) * 100 : 0
        
        // 等級改進計算
        let gradeValues = ["unacceptable": 0, "poor": 1, "acceptable": 2, "good": 3, "excellent": 4]
        let beforeGradeValue = gradeValues[before.grade] ?? 0
        let afterGradeValue = gradeValues[after.grade] ?? 0
        let gradeImprovement = afterGradeValue - beforeGradeValue
        
        return ColorImprovementMetrics(
            deltaEImprovement: deltaEImprovement,
            consistencyImprovement: consistencyImprovement,
            gradeImprovement: gradeImprovement,
            percentImprovement: percentImprovement,
            isImproved: deltaEImprovement > 0.5 // 色差改善超過0.5才算有意義的改進
        )
    }
    
    private func generateRecommendations(
        beforeMetrics: ColorAccuracyMetrics,
        afterMetrics: ColorAccuracyMetrics?,
        matrixAnalysis: ColorMatrixAnalysis
    ) -> [String] {
        var recommendations: [String] = []
        
        // 校正前建議
        if beforeMetrics.averageDeltaE > 10 {
            recommendations.append("原始圖像色彩偏差較大，建議改善拍攝條件")
        }
        
        if beforeMetrics.consistency > 5 {
            recommendations.append("各色彩點檢測一致性較低，建議檢查光照均勻性")
        }
        
        // 色彩矩陣建議
        if !matrixAnalysis.isWellConditioned {
            recommendations.append("色彩校正矩陣條件數過大，建議增加色彩點數量")
        }
        
        if matrixAnalysis.hasNegativeValues {
            recommendations.append("色彩矩陣包含負值，可能存在色域映射問題")
        }
        
        if matrixAnalysis.diagonalDominance < 0.7 {
            recommendations.append("建議使用對角矩陣校正以提高穩定性")
        }
        
        // 校正後建議
        if let after = afterMetrics {
            if after.averageDeltaE > 5 {
                recommendations.append("校正後色差仍然較大，建議重新校正或更換校正貼紙")
            }
            
            if after.averageDeltaE > beforeMetrics.averageDeltaE {
                recommendations.append("校正效果不佳，建議檢查校正貼紙品質或拍攝條件")
            }
        }
        
        // 特定顏色建議
        for (color, delta) in beforeMetrics.individualDeltas {
            if delta > 15 {
                recommendations.append("\(color)色偏差過大(ΔE=\(String(format: "%.1f", delta)))，建議重新檢測該顏色點")
            }
        }
        
        if recommendations.isEmpty {
            recommendations.append("色彩校正品質良好，無需額外調整")
        }
        
        return recommendations
    }
    
    private func calculateOverallGrade(
        beforeMetrics: ColorAccuracyMetrics,
        afterMetrics: ColorAccuracyMetrics?,
        improvement: ColorImprovementMetrics
    ) -> String {
        
        if let after = afterMetrics {
            // 有校正後結果，基於改善效果評級
            if improvement.isImproved && after.grade == "excellent" {
                return "A+" // 優秀改善
            } else if improvement.isImproved && after.grade == "good" {
                return "A"  // 良好改善
            } else if improvement.isImproved {
                return "B"  // 有改善
            } else {
                return "C"  // 改善有限
            }
        } else {
            // 只有校正前結果，基於原始品質評級
            switch beforeMetrics.grade {
            case "excellent": return "A"
            case "good": return "B"
            case "acceptable": return "C"
            default: return "D"
            }
        }
    }
}

// MARK: - 資料結構
struct ColorCalibrationValidationResult {
    let beforeCorrection: ColorAccuracyMetrics
    let afterCorrection: ColorAccuracyMetrics?
    let matrixAnalysis: ColorMatrixAnalysis
    let improvement: ColorImprovementMetrics
    let overallGrade: String
    let recommendations: [String]
    let timestamp: Date
}

struct ColorAccuracyMetrics {
    let individualDeltas: [String: Double] // 各顏色的ΔE值
    let averageDeltaE: Double              // 平均色差
    let maxDeltaE: Double                  // 最大色差
    let minDeltaE: Double                  // 最小色差
    let consistency: Double                // 一致性（標準差）
    let grade: String                      // 等級評定
    let colorCount: Int                    // 檢測到的顏色數量
}

struct ColorMatrixAnalysis {
    let determinant: Double        // 行列式
    let condition: Double          // 條件數
    let isWellConditioned: Bool    // 是否良態
    let hasNegativeValues: Bool    // 是否包含負值
    let diagonalDominance: Double  // 對角線優勢度
    let matrixType: String         // 矩陣類型
}

struct ColorImprovementMetrics {
    let deltaEImprovement: Double      // ΔE改善量
    let consistencyImprovement: Double // 一致性改善
    let gradeImprovement: Int          // 等級提升
    let percentImprovement: Double     // 百分比改善
    let isImproved: Bool              // 是否有意義的改善
}