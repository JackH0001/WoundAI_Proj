import SwiftUI
import CoreImage
import Vision
import UIKit

/// 按照技術文件建議的綜合品質驗證模組
class QualityValidationModule: ObservableObject {
    
    @Published var validationResults: ValidationResults?
    @Published var isValidating = false
    
    private let context = CIContext()
    
    struct ValidationResults {
        let imageQuality: ImageQualityResult
        let depthQuality: DepthQualityResult  
        let measurementReliability: MeasurementReliabilityResult
        let overallScore: Double
        let isAcceptableForMedicalUse: Bool
        let recommendations: [String]
        let timestamp: Date
    }
    
    struct ImageQualityResult {
        let sharpness: Double      // 銳利度 (0-1)
        let brightness: Double     // 亮度 (0-1)  
        let contrast: Double       // 對比度 (0-1)
        let colorBalance: Double   // 色彩平衡 (0-1)
        let noiseLevel: Double     // 噪聲水平 (0-1)
        let isAcceptable: Bool
        let issues: [String]
    }
    
    struct DepthQualityResult {
        let coverage: Double       // 深度覆蓋率 (0-1)
        let consistency: Double    // 深度一致性 (0-1) 
        let accuracy: Double       // 深度準確度 (0-1)
        let confidence: Double     // 平均信心度 (0-1)
        let noiseLevel: Double     // 深度噪聲 (0-1)
        let isAcceptable: Bool
        let issues: [String]
    }
    
    struct MeasurementReliabilityResult {
        let geometricConsistency: Double  // 幾何一致性 (0-1)
        let calibrationAccuracy: Double   // 校準準確度 (0-1)  
        let contourQuality: Double        // 輪廓品質 (0-1)
        let roiConfidence: Double         // ROI置信度 (0-1)
        let isReliable: Bool
        let uncertaintyRange: Double      // 不確定性範圍 (百分比)
    }
    
    /// 按照技術文件建議執行全面品質驗證
    func validateWoundMeasurement(
        image: UIImage, 
        depthData: Data, 
        roi: CGRect,
        measurement: WoundMeasurement
    ) async throws -> ValidationResults {
        
        await MainActor.run {
            isValidating = true
        }
        
        defer {
            Task { @MainActor in
                isValidating = false
            }
        }
        
        // 第一階段：圖像品質驗證
        let imageQuality = try await validateImageQuality(image)
        
        // 第二階段：深度數據品質驗證  
        let depthQuality = try await validateDepthQuality(depthData, imageSize: image.size)
        
        // 第三階段：測量可靠性驗證
        let measurementReliability = try await validateMeasurementReliability(
            image: image, 
            depthData: depthData, 
            roi: roi, 
            measurement: measurement
        )
        
        // 綜合評分與建議
        let results = generateFinalValidation(
            imageQuality: imageQuality,
            depthQuality: depthQuality, 
            measurementReliability: measurementReliability
        )
        
        await MainActor.run {
            validationResults = results
        }
        
        return results
    }
    
    // MARK: - 圖像品質驗證
    
    private func validateImageQuality(_ image: UIImage) async throws -> ImageQualityResult {
        guard let cgImage = image.cgImage else {
            throw QualityValidationError.invalidImage
        }
        
        // 按技術文件建議的品質指標
        let sharpness = calculateSharpness(cgImage)
        let brightness = calculateBrightness(cgImage)  
        let contrast = calculateContrast(cgImage)
        let colorBalance = calculateColorBalance(cgImage)
        let noiseLevel = calculateNoiseLevel(cgImage)
        
        var issues: [String] = []
        
        // 按技術文件標準檢查
        if sharpness < 0.6 { issues.append("圖像模糊，建議重新拍攝") }
        if brightness < 0.2 || brightness > 0.8 { issues.append("光照條件不佳，建議調整環境光線") }
        if contrast < 0.3 { issues.append("對比度過低，影響傷口邊界識別") }
        if colorBalance < 0.4 { issues.append("色彩偏差，可能影響組織類型判斷") }
        if noiseLevel > 0.7 { issues.append("圖像噪聲過高，建議穩定設備拍攝") }
        
        let overallQuality = (sharpness + (1.0 - Swift.abs(brightness - 0.5) * 2) + contrast + colorBalance + (1.0 - noiseLevel)) / 5.0
        let isAcceptable = overallQuality >= 0.6 && issues.count <= 1
        
        return ImageQualityResult(
            sharpness: sharpness,
            brightness: brightness,
            contrast: contrast, 
            colorBalance: colorBalance,
            noiseLevel: noiseLevel,
            isAcceptable: isAcceptable,
            issues: issues
        )
    }
    
    // MARK: - 深度數據品質驗證
    
    private func validateDepthQuality(_ depthData: Data, imageSize: CGSize) async throws -> DepthQualityResult {
        guard !depthData.isEmpty else {
            return DepthQualityResult(
                coverage: 0, consistency: 0, accuracy: 0, 
                confidence: 0, noiseLevel: 1.0, 
                isAcceptable: false,
                issues: ["深度數據缺失"]
            )
        }
        
        let depthValues = depthData.withUnsafeBytes { bytes in
            Array(bytes.bindMemory(to: Float32.self))
        }
        
        // 按技術文件建議的深度品質評估
        let coverage = calculateDepthCoverage(depthValues)
        let consistency = calculateDepthConsistency(depthValues) 
        let accuracy = calculateDepthAccuracy(depthValues)
        let confidence = calculateDepthConfidence(depthValues)
        let noiseLevel = calculateDepthNoise(depthValues)
        
        var issues: [String] = []
        
        // 按技術文件標準：深度覆蓋率 ≥ 80%，信心度 ≥ 0.7
        if coverage < 0.8 { issues.append("深度覆蓋率不足80%，測量精度可能受影響") }
        if confidence < 0.7 { issues.append("深度信心度低於0.7，建議改善拍攝角度") }
        if consistency < 0.6 { issues.append("深度數據不一致，可能存在噪聲干擾") }
        if accuracy < 0.7 { issues.append("深度準確度不足，建議檢查感測器校準") }
        if noiseLevel > 0.5 { issues.append("深度噪聲過高，建議保持設備穩定") }
        
        let isAcceptable = coverage >= 0.8 && confidence >= 0.7 && issues.count <= 1
        
        return DepthQualityResult(
            coverage: coverage,
            consistency: consistency,
            accuracy: accuracy,
            confidence: confidence, 
            noiseLevel: noiseLevel,
            isAcceptable: isAcceptable,
            issues: issues
        )
    }
    
    // MARK: - 測量可靠性驗證
    
    private func validateMeasurementReliability(
        image: UIImage,
        depthData: Data, 
        roi: CGRect,
        measurement: WoundMeasurement
    ) async throws -> MeasurementReliabilityResult {
        
        // 幾何一致性檢查
        let geometricConsistency = validateGeometricConsistency(measurement)
        
        // 校準準確度評估
        let calibrationAccuracy = validateCalibrationAccuracy(measurement)
        
        // 輪廓品質評估
        let contourQuality = validateContourQuality(image, roi: roi)
        
        // ROI置信度
        let roiConfidence = Double(roi.width * roi.height) > 0.01 ? 0.8 : 0.4
        
        // 不確定性範圍計算
        let uncertaintyRange = calculateUncertaintyRange(
            geometricConsistency, calibrationAccuracy, contourQuality
        )
        
        let overallReliability = (geometricConsistency + calibrationAccuracy + contourQuality + roiConfidence) / 4.0
        let isReliable = overallReliability >= 0.7 && uncertaintyRange <= 0.15 // 15%不確定性閾值
        
        return MeasurementReliabilityResult(
            geometricConsistency: geometricConsistency,
            calibrationAccuracy: calibrationAccuracy,
            contourQuality: contourQuality,
            roiConfidence: roiConfidence,
            isReliable: isReliable,
            uncertaintyRange: uncertaintyRange
        )
    }
    
    // MARK: - 品質計算方法
    
    private func calculateSharpness(_ cgImage: CGImage) -> Double {
        // 使用Laplacian變異數檢測銳利度
        guard let data = cgImage.dataProvider?.data,
              let bytes = CFDataGetBytePtr(data) else { return 0.5 }
        
        let width = cgImage.width
        let height = cgImage.height
        let bytesPerPixel = 4
        let bytesPerRow = width * bytesPerPixel
        
        var laplacianVariance: Double = 0
        var pixelCount = 0
        
        // Laplacian核 [-1, -1, -1; -1, 8, -1; -1, -1, -1]
        for y in 1..<(height-1) {
            for x in 1..<(width-1) {
                let centerOffset = y * bytesPerRow + x * bytesPerPixel
                let centerGray = Double(bytes[centerOffset]) * 0.299 + 
                               Double(bytes[centerOffset + 1]) * 0.587 + 
                               Double(bytes[centerOffset + 2]) * 0.114
                
                var sum: Double = centerGray * 8
                
                // 8個鄰居
                let neighbors = [(-1,-1), (-1,0), (-1,1), (0,-1), (0,1), (1,-1), (1,0), (1,1)]
                for (dx, dy) in neighbors {
                    let neighborOffset = (y + dy) * bytesPerRow + (x + dx) * bytesPerPixel
                    let neighborGray = Double(bytes[neighborOffset]) * 0.299 + 
                                     Double(bytes[neighborOffset + 1]) * 0.587 + 
                                     Double(bytes[neighborOffset + 2]) * 0.114
                    sum -= neighborGray
                }
                
                laplacianVariance += sum * sum
                pixelCount += 1
            }
        }
        
        let variance = laplacianVariance / Double(pixelCount)
        return min(1.0, variance / 10000.0) // 正規化到0-1範圍
    }
    
    private func calculateBrightness(_ cgImage: CGImage) -> Double {
        guard let data = cgImage.dataProvider?.data,
              let bytes = CFDataGetBytePtr(data) else { return 0.5 }
        
        let width = cgImage.width
        let height = cgImage.height
        let bytesPerPixel = 4
        
        var totalBrightness: Double = 0
        let pixelCount = width * height
        let sampleRate = 4 // 每4個像素採樣一次
        
        for y in stride(from: 0, to: height, by: sampleRate) {
            for x in stride(from: 0, to: width, by: sampleRate) {
                let offset = y * width * bytesPerPixel + x * bytesPerPixel
                let r = Double(bytes[offset])
                let g = Double(bytes[offset + 1]) 
                let b = Double(bytes[offset + 2])
                
                let brightness = (r * 0.299 + g * 0.587 + b * 0.114) / 255.0
                totalBrightness += brightness
            }
        }
        
        return totalBrightness / Double(pixelCount / (sampleRate * sampleRate))
    }
    
    private func calculateContrast(_ cgImage: CGImage) -> Double {
        guard let data = cgImage.dataProvider?.data,
              let bytes = CFDataGetBytePtr(data) else { return 0.5 }
        
        let width = cgImage.width
        let height = cgImage.height
        let bytesPerPixel = 4
        
        var minLuminance: Double = 255.0
        var maxLuminance: Double = 0.0
        let sampleRate = 4
        
        for y in stride(from: 0, to: height, by: sampleRate) {
            for x in stride(from: 0, to: width, by: sampleRate) {
                let offset = y * width * bytesPerPixel + x * bytesPerPixel
                let r = Double(bytes[offset])
                let g = Double(bytes[offset + 1])
                let b = Double(bytes[offset + 2])
                
                let luminance = r * 0.299 + g * 0.587 + b * 0.114
                minLuminance = min(minLuminance, luminance)
                maxLuminance = max(maxLuminance, luminance)
            }
        }
        
        guard maxLuminance > minLuminance else { return 0.0 }
        return (maxLuminance - minLuminance) / (maxLuminance + minLuminance)
    }
    
    private func calculateColorBalance(_ cgImage: CGImage) -> Double {
        guard let data = cgImage.dataProvider?.data,
              let bytes = CFDataGetBytePtr(data) else { return 0.5 }
        
        let width = cgImage.width  
        let height = cgImage.height
        let bytesPerPixel = 4
        
        var rSum: Double = 0, gSum: Double = 0, bSum: Double = 0
        let sampleRate = 4
        let sampleCount = (width / sampleRate) * (height / sampleRate)
        
        for y in stride(from: 0, to: height, by: sampleRate) {
            for x in stride(from: 0, to: width, by: sampleRate) {
                let offset = y * width * bytesPerPixel + x * bytesPerPixel
                rSum += Double(bytes[offset])
                gSum += Double(bytes[offset + 1])
                bSum += Double(bytes[offset + 2])
            }
        }
        
        let rMean = rSum / Double(sampleCount)
        let gMean = gSum / Double(sampleCount)
        let bMean = bSum / Double(sampleCount)
        
        let maxChannel = max(rMean, max(gMean, bMean))
        let minChannel = min(rMean, min(gMean, bMean))
        
        return minChannel / max(maxChannel, 1.0)
    }
    
    private func calculateNoiseLevel(_ cgImage: CGImage) -> Double {
        // 使用局部標準差評估噪聲
        guard let data = cgImage.dataProvider?.data,
              let bytes = CFDataGetBytePtr(data) else { return 0.5 }
        
        let width = cgImage.width
        let height = cgImage.height
        let bytesPerPixel = 4
        let windowSize = 5
        
        var totalVariance: Double = 0
        var windowCount = 0
        
        for y in stride(from: windowSize/2, to: height - windowSize/2, by: windowSize) {
            for x in stride(from: windowSize/2, to: width - windowSize/2, by: windowSize) {
                var windowSum: Double = 0
                var windowSumSquared: Double = 0
                
                for dy in -(windowSize/2)...(windowSize/2) {
                    for dx in -(windowSize/2)...(windowSize/2) {
                        let offset = (y + dy) * width * bytesPerPixel + (x + dx) * bytesPerPixel
                        let gray = Double(bytes[offset]) * 0.299 + 
                                 Double(bytes[offset + 1]) * 0.587 + 
                                 Double(bytes[offset + 2]) * 0.114
                        windowSum += gray
                        windowSumSquared += gray * gray
                    }
                }
                
                let windowPixels = Double(windowSize * windowSize)
                let mean = windowSum / windowPixels
                let variance = (windowSumSquared / windowPixels) - (mean * mean)
                totalVariance += sqrt(variance)
                windowCount += 1
            }
        }
        
        let avgStdDev = totalVariance / Double(windowCount)
        return min(1.0, avgStdDev / 50.0) // 正規化
    }
    
    // MARK: - 深度品質計算
    
    private func calculateDepthCoverage(_ depthValues: [Float32]) -> Double {
        let validCount = depthValues.filter { $0 >= 0.001 && $0 <= 2.0 }.count
        return Double(validCount) / Double(depthValues.count)
    }
    
    private func calculateDepthConsistency(_ depthValues: [Float32]) -> Double {
        let validDepths = depthValues.filter { $0 >= 0.001 && $0 <= 2.0 }
        guard validDepths.count > 1 else { return 0.0 }
        
        let mean = validDepths.reduce(0, +) / Float(validDepths.count)
        let variance = validDepths.map { pow($0 - mean, 2) }.reduce(0, +) / Float(validDepths.count)
        let stdDev = sqrt(variance)
        
        // 標準差越小，一致性越高
        return max(0.0, min(1.0, 1.0 - Double(stdDev) / 0.5))
    }
    
    private func calculateDepthAccuracy(_ depthValues: [Float32]) -> Double {
        // 基於深度分佈合理性評估準確度
        let validDepths = depthValues.filter { $0 >= 0.001 && $0 <= 2.0 }
        guard !validDepths.isEmpty else { return 0.0 }
        
        let sortedDepths = validDepths.sorted()
        let median = sortedDepths[sortedDepths.count / 2]
        let q1 = sortedDepths[sortedDepths.count / 4]
        let q3 = sortedDepths[sortedDepths.count * 3 / 4]
        let iqr = q3 - q1
        
        // IQR越合理（不太大不太小），準確度越高
        let reasonableIQR: Float = 0.2 // 20cm的IQR被認為是合理的
        let accuracy = 1.0 - min(1.0, Swift.abs(iqr - reasonableIQR) / reasonableIQR)
        
        return Double(accuracy)
    }
    
    private func calculateDepthConfidence(_ depthValues: [Float32]) -> Double {
        // 基於有效深度像素的比例計算信心度
        return calculateDepthCoverage(depthValues)
    }
    
    private func calculateDepthNoise(_ depthValues: [Float32]) -> Double {
        // 計算深度梯度變化來評估噪聲
        let width = Int(sqrt(Double(depthValues.count)))
        guard width > 2 else { return 0.5 }
        
        var gradientSum: Double = 0
        var gradientCount = 0
        
        for y in 1..<(width-1) {
            for x in 1..<(width-1) {
                let center = depthValues[y * width + x]
                guard center >= 0.001 && center <= 2.0 else { continue }
                
                let right = depthValues[y * width + (x + 1)]
                let bottom = depthValues[(y + 1) * width + x]
                
                if right >= 0.001 && right <= 2.0 {
                    gradientSum += Double(Swift.abs(center - right))
                    gradientCount += 1
                }
                
                if bottom >= 0.001 && bottom <= 2.0 {
                    gradientSum += Double(Swift.abs(center - bottom))
                    gradientCount += 1
                }
            }
        }
        
        guard gradientCount > 0 else { return 0.5 }
        let avgGradient = gradientSum / Double(gradientCount)
        return min(1.0, avgGradient / 0.1) // 10cm梯度視為高噪聲
    }
    
    // MARK: - 測量可靠性計算
    
    private func validateGeometricConsistency(_ measurement: WoundMeasurement) -> Double {
        // 檢查面積、周長、長度、寬度的幾何一致性
        let area = measurement.area
        let perimeter = measurement.perimeter
        let length = measurement.length
        let width = measurement.width
        
        guard area > 0, perimeter > 0, length > 0, width > 0 else { return 0.0 }
        
        // 計算圓形度 4πA/P²
        let circularity = (4 * Double.pi * area) / (perimeter * perimeter)
        
        // 檢查長寬比是否合理
        let aspectRatio = length / width
        let aspectRatioScore = aspectRatio > 0.5 && aspectRatio < 5.0 ? 1.0 : 0.5
        
        // 檢查面積與長寬乘積的一致性
        let rectangularArea = length * width
        let areaConsistency = 1.0 - min(1.0, Swift.abs(area - rectangularArea * 0.785) / area) // 橢圓係數約0.785
        
        return (circularity + aspectRatioScore + areaConsistency) / 3.0
    }
    
    private func validateCalibrationAccuracy(_ measurement: WoundMeasurement) -> Double {
        // 基於像素比例和距離的合理性評估校準準確度
        let pixelScale = measurement.pixelScale // cm/pixel
        let distance = measurement.cameraDistance // cm
        
        // 典型手機相機在50cm距離的像素比例約為0.01-0.05 cm/pixel
        let expectedPixelScale = distance * 0.0008 // 經驗公式
        let scaleAccuracy = 1.0 - min(1.0, Swift.abs(pixelScale - expectedPixelScale) / expectedPixelScale)
        
        return max(0.3, scaleAccuracy) // 最低30%準確度
    }
    
    private func validateContourQuality(_ image: UIImage, roi: CGRect) -> Double {
        // 評估ROI區域的邊界清晰度和完整性
        guard let cgImage = image.cgImage else { return 0.5 }
        
        let roiArea = roi.width * roi.height
        let imageArea = CGFloat(cgImage.width * cgImage.height)
        let roiRatio = roiArea / imageArea
        
        // ROI應該佔圖像的合理比例（5%-50%）
        let sizeScore = (roiRatio >= 0.05 && roiRatio <= 0.5) ? 1.0 : 0.6
        
        // 檢查ROI的完整性（非零尺寸）
        let completenessScore = (roi.width > 0 && roi.height > 0) ? 1.0 : 0.0
        
        return (sizeScore + completenessScore) / 2.0
    }
    
    private func calculateUncertaintyRange(
        _ geometric: Double, 
        _ calibration: Double, 
        _ contour: Double
    ) -> Double {
        let overallReliability = (geometric + calibration + contour) / 3.0
        
        // 可靠性越低，不確定性越高
        return max(0.05, min(0.3, 1.0 - overallReliability))
    }
    
    // MARK: - 最終驗證結果生成
    
    private func generateFinalValidation(
        imageQuality: ImageQualityResult,
        depthQuality: DepthQualityResult,
        measurementReliability: MeasurementReliabilityResult
    ) -> ValidationResults {
        
        let overallScore = (
            (imageQuality.sharpness + imageQuality.contrast + imageQuality.colorBalance) / 3.0 * 0.3 +
            (depthQuality.coverage + depthQuality.consistency + depthQuality.confidence) / 3.0 * 0.4 +
            (measurementReliability.geometricConsistency + measurementReliability.calibrationAccuracy + measurementReliability.contourQuality) / 3.0 * 0.3
        )
        
        let isAcceptableForMedicalUse = overallScore >= 0.7 && 
                                       imageQuality.isAcceptable && 
                                       depthQuality.isAcceptable && 
                                       measurementReliability.isReliable
        
        var recommendations: [String] = []
        recommendations.append(contentsOf: imageQuality.issues)
        recommendations.append(contentsOf: depthQuality.issues)
        
        if !measurementReliability.isReliable {
            recommendations.append("測量可靠性不足，不確定性範圍為 ±\(String(format: "%.1f", measurementReliability.uncertaintyRange * 100))%")
        }
        
        if overallScore < 0.7 {
            recommendations.append("整體品質評分低於醫療使用標準，建議重新測量")
        }
        
        if recommendations.isEmpty {
            recommendations.append("品質驗證通過，測量結果可用於醫療參考")
        }
        
        return ValidationResults(
            imageQuality: imageQuality,
            depthQuality: depthQuality,
            measurementReliability: measurementReliability,
            overallScore: overallScore,
            isAcceptableForMedicalUse: isAcceptableForMedicalUse,
            recommendations: recommendations,
            timestamp: Date()
        )
    }
}

enum QualityValidationError: Error {
    case invalidImage
    case invalidDepthData
    case processingFailed
    case validationFailed
}