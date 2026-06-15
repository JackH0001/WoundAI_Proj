//
//  MultiAlgorithmDetector.swift
//  WoundMeasurementApp
//
//  Created by Claude on 2025-01-12.
//

import SwiftUI
import UIKit
import CoreImage
import Foundation

// MARK: - 多算法檢測引擎

class MultiAlgorithmDetector: ObservableObject {
    static let shared = MultiAlgorithmDetector()
    
    @Published var detectionResults: [DetectionResult] = []
    @Published var isAnalyzing = false
    
    private init() {}
    
    // MARK: - 主要檢測功能
    
    func analyzeImageType(_ image: UIImage) -> ImageProcessingStrategy {
        let startTime = Date()
        DispatchQueue.main.async {
            self.isAnalyzing = true
        }
        
        print("🔍 開始多算法分析影像類型...")
        
        // 使用多種算法進行檢測並計算信心度
        let detectionResults = performMultiAlgorithmDetection(image)
        
        DispatchQueue.main.async {
            self.detectionResults = detectionResults
            self.isAnalyzing = false
        }
        
        // 顯示所有檢測結果
        print("📊 所有檢測結果:")
        for result in detectionResults.sorted(by: { $0.confidence > $1.confidence }) {
            print("   📈 \(result.strategy.rawValue): \(String(format: "%.3f", result.confidence)) (\(result.method), \(result.qualityScore.rawValue))")
        }
        
        // 選擇信心度最高的策略
        if let bestResult = detectionResults.max(by: { $0.confidence < $1.confidence }) {
            let processingTime = Date().timeIntervalSince(startTime)
            print("✅ 最佳檢測結果: \(bestResult.strategy.rawValue)")
            print("   🎯 信心度: \(String(format: "%.3f", bestResult.confidence)) (\(bestResult.qualityScore.rawValue))")
            print("   ⏱️ 處理時間: \(String(format: "%.3f", processingTime))秒")
            print("   🔬 算法: \(bestResult.method)")
            
            // 記錄檢測共識度
            let consensus = calculateDetectionConsensus(detectionResults, bestStrategy: bestResult.strategy)
            print("   🤝 檢測共識度: \(String(format: "%.3f", consensus))")
            
            return bestResult.strategy
        }
        
        print("⚠️ 所有算法檢測信心度過低，使用備用處理策略")
        return .unknown
    }
    
    private func performMultiAlgorithmDetection(_ image: UIImage) -> [DetectionResult] {
        var results: [DetectionResult] = []
        
        // AR深度影像檢測（3種算法）
        results.append(detectARDepthByMetadata(image))
        results.append(detectARDepthByResolution(image))
        results.append(detectARDepthByColorSpace(image))
        
        // 校正貼紙檢測（2種算法）
        results.append(detectStickerByEdges(image))
        results.append(detectStickerByCircularPattern(image))
        
        // 平面影像檢測（2種算法）
        results.append(detectFlatImageByAspectRatio(image))
        results.append(detectFlatImageByResolution(image))
        
        // 過濾低信心度結果
        let filteredResults = results.filter { $0.confidence >= DetectionConfig.minConfidenceThreshold }
        
        print("🎯 通過信心度門檻的檢測結果: \(filteredResults.count)/\(results.count)")
        
        return filteredResults
    }
    
    private func calculateDetectionConsensus(_ results: [DetectionResult], bestStrategy: ImageProcessingStrategy) -> Double {
        let sameStrategyResults = results.filter { $0.strategy == bestStrategy }
        let averageConfidence = sameStrategyResults.reduce(0.0) { $0 + $1.confidence } / Double(max(sameStrategyResults.count, 1))
        let consensusRatio = Double(sameStrategyResults.count) / Double(max(results.count, 1))
        
        return (averageConfidence + consensusRatio) / 2.0
    }
    
    // MARK: - AR深度影像檢測算法
    
    private func detectARDepthByMetadata(_ image: UIImage) -> DetectionResult {
        let startTime = Date()
        var confidence: Double = 0.0
        var details: [String: Any] = [:]
        
        guard let cgImage = image.cgImage,
              let imageData = image.pngData(),
              let imageSource = CGImageSourceCreateWithData(imageData as CFData, nil) else {
            return DetectionResult(strategy: .arDepthImage, confidence: 0.0, method: "EXIF元數據檢測", 
                                 details: ["error": "無法讀取影像元數據"], 
                                 processingTime: Date().timeIntervalSince(startTime))
        }
        
        if let properties = CGImageSourceCopyPropertiesAtIndex(imageSource, 0, nil) as? [String: Any] {
            details["hasProperties"] = true
            
            // 檢查EXIF數據中的深度相關資訊
            if let exifDict = properties[kCGImagePropertyExifDictionary as String] as? [String: Any] {
                details["hasEXIF"] = true
                
                // 檢查用戶註釋
                if let userComment = exifDict["UserComment"] as? String {
                    details["userComment"] = userComment
                    if userComment.contains("AR") || userComment.contains("DEPTH") {
                        confidence += 0.4
                    }
                }
                
                // 檢查軟體標識
                if let software = exifDict["Software"] as? String {
                    details["software"] = software
                    if software.contains("ARKit") || software.contains("LiDAR") {
                        confidence += 0.5
                    }
                }
            }
            
            // 檢查色彩空間
            if let colorModel = properties[kCGImagePropertyColorModel as String] as? String {
                details["colorModel"] = colorModel
                if colorModel.contains("RGB") && image.size.width >= DetectionConfig.arMinResolution {
                    confidence += 0.3
                }
            }
        }
        
        let finalConfidence = confidence * DetectionConfig.metadataDetectionWeight
        
        return DetectionResult(strategy: .arDepthImage, confidence: finalConfidence, method: "EXIF元數據檢測", 
                             details: details, processingTime: Date().timeIntervalSince(startTime))
    }
    
    private func detectARDepthByResolution(_ image: UIImage) -> DetectionResult {
        let startTime = Date()
        let imageSize = image.size
        let totalPixels = imageSize.width * imageSize.height
        let aspectRatio = imageSize.width / imageSize.height
        
        var confidence: Double = 0.0
        var details: [String: Any] = [
            "width": imageSize.width,
            "height": imageSize.height,
            "totalPixels": totalPixels,
            "aspectRatio": aspectRatio
        ]
        
        // 檢查是否為AR相機常用解析度
        if totalPixels >= DetectionConfig.arMinResolution {
            confidence += 0.4
        } else if totalPixels >= DetectionConfig.relaxedARResolution {
            confidence += 0.2
        }
        
        // 檢查長寬比是否符合AR相機
        let aspectRatioDiff = abs(aspectRatio - DetectionConfig.arTargetAspectRatio)
        if aspectRatioDiff <= DetectionConfig.arAspectRatioTolerance {
            confidence += 0.3
            if aspectRatioDiff <= 0.05 {
                confidence += 0.2 // 非常精確的比例
            }
        }
        
        details["aspectRatioDiff"] = aspectRatioDiff
        
        let finalConfidence = confidence * DetectionConfig.resolutionDetectionWeight
        
        return DetectionResult(strategy: .arDepthImage, confidence: finalConfidence, method: "解析度與比例檢測", 
                             details: details, processingTime: Date().timeIntervalSince(startTime))
    }
    
    private func detectARDepthByColorSpace(_ image: UIImage) -> DetectionResult {
        let startTime = Date()
        guard let cgImage = image.cgImage else {
            return DetectionResult(strategy: .arDepthImage, confidence: 0.0, method: "色彩空間檢測", 
                                 details: ["error": "無法獲取CGImage"], 
                                 processingTime: Date().timeIntervalSince(startTime))
        }
        
        var confidence: Double = 0.0
        var details: [String: Any] = [:]
        
        let colorSpace = cgImage.colorSpace
        let bitsPerComponent = cgImage.bitsPerComponent
        let bitsPerPixel = cgImage.bitsPerPixel
        
        details["bitsPerComponent"] = bitsPerComponent
        details["bitsPerPixel"] = bitsPerPixel
        
        // AR影像通常使用特定的色彩配置
        if bitsPerComponent >= 8 {
            confidence += 0.3
        }
        
        if bitsPerPixel >= 24 {
            confidence += 0.2
        }
        
        // 檢查色彩空間模型
        if let colorSpace = colorSpace {
            let model = colorSpace.model
            details["colorSpaceModel"] = String(describing: model)
            
            if model == .rgb {
                confidence += 0.4
            }
        }
        
        let finalConfidence = confidence * DetectionConfig.colorSpaceDetectionWeight
        
        return DetectionResult(strategy: .arDepthImage, confidence: finalConfidence, method: "色彩空間檢測", 
                             details: details, processingTime: Date().timeIntervalSince(startTime))
    }
    
    // MARK: - 校正貼紙檢測算法
    
    private func detectStickerByEdges(_ image: UIImage) -> DetectionResult {
        let startTime = Date()
        guard let cgImage = image.cgImage else {
            return DetectionResult(strategy: .flatImageWithSticker, confidence: 0.0, method: "邊緣檢測", 
                                 details: ["error": "無法獲取CGImage"], 
                                 processingTime: Date().timeIntervalSince(startTime))
        }
        
        let context = CIContext()
        let ciImage = CIImage(cgImage: cgImage)
        
        var confidence: Double = 0.0
        var details: [String: Any] = [:]
        
        // 應用邊緣檢測濾鏡
        guard let edgeFilter = CIFilter(name: "CIEdges") else {
            return DetectionResult(strategy: .flatImageWithSticker, confidence: 0.0, method: "邊緣檢測", 
                                 details: ["error": "無法創建邊緣濾鏡"], 
                                 processingTime: Date().timeIntervalSince(startTime))
        }
        
        edgeFilter.setValue(ciImage, forKey: kCIInputImageKey)
        edgeFilter.setValue(1.0, forKey: kCIInputIntensityKey)
        
        guard let edgeOutput = edgeFilter.outputImage,
              let edgeCGImage = context.createCGImage(edgeOutput, from: edgeOutput.extent) else {
            return DetectionResult(strategy: .flatImageWithSticker, confidence: 0.0, method: "邊緣檢測", 
                                 details: ["error": "邊緣檢測失敗"], 
                                 processingTime: Date().timeIntervalSince(startTime))
        }
        
        let edgeStrength = analyzeEdgePatterns(edgeCGImage)
        details["edgeStrength"] = edgeStrength
        
        if edgeStrength > DetectionConfig.stickerEdgeThreshold {
            confidence = min(1.0, edgeStrength / 200.0)
        } else if edgeStrength > DetectionConfig.relaxedStickerEdge {
            confidence = edgeStrength / 300.0
        }
        
        let finalConfidence = confidence * DetectionConfig.edgeDetectionWeight
        
        return DetectionResult(strategy: .flatImageWithSticker, confidence: finalConfidence, method: "邊緣檢測", 
                             details: details, processingTime: Date().timeIntervalSince(startTime))
    }
    
    private func detectStickerByCircularPattern(_ image: UIImage) -> DetectionResult {
        let startTime = Date()
        guard let cgImage = image.cgImage else {
            return DetectionResult(strategy: .flatImageWithSticker, confidence: 0.0, method: "圓形圖案檢測", 
                                 details: ["error": "無法獲取CGImage"], 
                                 processingTime: Date().timeIntervalSince(startTime))
        }
        
        var confidence: Double = 0.0
        var details: [String: Any] = [:]
        
        let circularPatternStrength = analyzeCircularPatterns(cgImage)
        details["circularPatternStrength"] = circularPatternStrength
        details["detectionMethod"] = "圓形輪廓分析"
        
        // 基於圓形圖案強度計算信心度
        if circularPatternStrength > 120.0 {
            confidence = min(1.0, circularPatternStrength / 200.0)
        } else if circularPatternStrength > 80.0 {
            confidence = circularPatternStrength / 300.0
        }
        
        let finalConfidence = confidence * DetectionConfig.patternDetectionWeight
        
        return DetectionResult(strategy: .flatImageWithSticker, confidence: finalConfidence, method: "圓形圖案檢測", 
                             details: details, processingTime: Date().timeIntervalSince(startTime))
    }
    
    // MARK: - 平面影像檢測算法
    
    private func detectFlatImageByAspectRatio(_ image: UIImage) -> DetectionResult {
        let startTime = Date()
        let aspectRatio = image.size.width / image.size.height
        
        var confidence: Double = 0.0
        var details: [String: Any] = [
            "aspectRatio": aspectRatio,
            "imageSize": ["width": image.size.width, "height": image.size.height]
        ]
        
        // 檢查是否符合常見的手機拍攝比例
        var bestMatch: (ratio: Double, difference: Double)?
        
        for commonRatio in DetectionConfig.commonAspectRatios {
            let difference = abs(aspectRatio - commonRatio)
            if difference <= DetectionConfig.aspectRatioTolerance {
                if bestMatch == nil || difference < bestMatch!.difference {
                    bestMatch = (commonRatio, difference)
                }
            }
        }
        
        if let match = bestMatch {
            details["matchedRatio"] = match.ratio
            details["ratioDifference"] = match.difference
            
            // 計算信心度（差異越小信心度越高）
            confidence = 1.0 - (match.difference / DetectionConfig.aspectRatioTolerance)
            confidence = max(0.3, confidence) // 最低0.3的信心度
        }
        
        let finalConfidence = confidence * DetectionConfig.resolutionDetectionWeight
        
        return DetectionResult(strategy: .flatImageEstimated, confidence: finalConfidence, method: "長寬比檢測", 
                             details: details, processingTime: Date().timeIntervalSince(startTime))
    }
    
    private func detectFlatImageByResolution(_ image: UIImage) -> DetectionResult {
        let startTime = Date()
        let totalPixels = image.size.width * image.size.height
        
        var confidence: Double = 0.0
        var details: [String: Any] = [
            "totalPixels": totalPixels,
            "maxFlatImagePixels": DetectionConfig.maxFlatImagePixels
        ]
        
        // 檢查解析度是否在平面影像範圍內
        if totalPixels <= DetectionConfig.maxFlatImagePixels {
            // 解析度越接近中等水平，信心度越高
            let idealPixels = 1920.0 * 1080.0
            let pixelDifference = abs(totalPixels - idealPixels)
            confidence = max(0.2, 1.0 - (pixelDifference / idealPixels))
            
            details["pixelDifference"] = pixelDifference
            details["idealPixels"] = idealPixels
        }
        
        let finalConfidence = confidence * DetectionConfig.resolutionDetectionWeight
        
        return DetectionResult(strategy: .flatImageEstimated, confidence: finalConfidence, method: "解析度檢測", 
                             details: details, processingTime: Date().timeIntervalSince(startTime))
    }
    
    // MARK: - 輔助分析函數
    
    private func analyzeEdgePatterns(_ cgImage: CGImage) -> Double {
        let width = cgImage.width
        let height = cgImage.height
        let centerX = width / 2
        let centerY = height / 2
        let searchRadius = min(width, height) / 6
        
        guard let dataProvider = cgImage.dataProvider,
              let data = dataProvider.data,
              let buffer = CFDataGetBytePtr(data) else {
            return 0.0
        }
        
        var edgeStrength: Double = 0.0
        let sampleCount = 64
        
        // 在影像中心區域進行圓形採樣
        for i in 0..<sampleCount {
            let angle = Double(i) * 2.0 * Double.pi / Double(sampleCount)
            let x = centerX + Int(cos(angle) * Double(searchRadius))
            let y = centerY + Int(sin(angle) * Double(searchRadius))
            
            if x >= 0 && x < width && y >= 0 && y < height {
                let pixelIndex = (y * width + x) * 4 // RGBA
                if pixelIndex < width * height * 4 {
                    let pixelValue = Double(buffer[pixelIndex])
                    edgeStrength += pixelValue
                }
            }
        }
        
        return edgeStrength / Double(sampleCount)
    }
    
    private func analyzeCircularPatterns(_ cgImage: CGImage) -> Double {
        let width = cgImage.width
        let height = cgImage.height
        let centerX = width / 2
        let centerY = height / 2
        
        guard let dataProvider = cgImage.dataProvider,
              let data = dataProvider.data,
              let buffer = CFDataGetBytePtr(data) else {
            return 0.0
        }
        
        var circularStrength: Double = 0.0
        
        // 檢測多個半徑的圓形圖案
        let radii = [min(width, height) / 8, min(width, height) / 6, min(width, height) / 4]
        
        for radius in radii {
            let sampleCount = 32
            var radiusStrength: Double = 0.0
            
            for i in 0..<sampleCount {
                let angle = Double(i) * 2.0 * Double.pi / Double(sampleCount)
                let x = centerX + Int(cos(angle) * Double(radius))
                let y = centerY + Int(sin(angle) * Double(radius))
                
                if x >= 0 && x < width && y >= 0 && y < height {
                    let pixelIndex = (y * width + x) * 4
                    if pixelIndex < width * height * 4 {
                        let pixelValue = Double(buffer[pixelIndex])
                        radiusStrength += pixelValue
                    }
                }
            }
            
            circularStrength += radiusStrength / Double(sampleCount)
        }
        
        return circularStrength / Double(radii.count)
    }
}

// MARK: - 檢測結果統計視圖

struct DetectionResultsView: View {
    @StateObject private var detector = MultiAlgorithmDetector.shared
    let image: UIImage?
    
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("多算法檢測結果")
                .font(.headline)
                .foregroundColor(.primary)
            
            if detector.isAnalyzing {
                HStack {
                    ProgressView()
                        .scaleEffect(0.8)
                    Text("正在分析影像...")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
            } else if !detector.detectionResults.isEmpty {
                LazyVStack(alignment: .leading, spacing: 8) {
                    ForEach(detector.detectionResults.sorted(by: { $0.confidence > $1.confidence }), id: \.method) { result in
                        DetectionResultRow(result: result)
                    }
                }
            } else {
                Text("尚未進行檢測")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }
        .padding()
        .background(Color.gray.opacity(0.1))
        .cornerRadius(8)
        .onAppear {
            if let image = image {
                let _ = detector.analyzeImageType(image)
            }
        }
    }
}

struct DetectionResultRow: View {
    let result: DetectionResult
    
    var body: some View {
        HStack {
            Circle()
                .fill(result.qualityScore.color)
                .frame(width: 8, height: 8)
            
            VStack(alignment: .leading, spacing: 2) {
                HStack {
                    Text(result.strategy.rawValue)
                        .font(.caption)
                        .fontWeight(.medium)
                    
                    Spacer()
                    
                    Text("\(String(format: "%.1f%%", result.confidence * 100))")
                        .font(.caption)
                        .fontWeight(.bold)
                        .foregroundColor(result.qualityScore.color)
                }
                
                Text(result.method)
                    .font(.caption2)
                    .foregroundColor(.secondary)
            }
        }
        .padding(.vertical, 2)
    }
}
