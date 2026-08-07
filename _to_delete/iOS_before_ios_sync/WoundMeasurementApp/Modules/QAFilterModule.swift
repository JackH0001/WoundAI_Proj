import SwiftUI
import CoreML
import Vision
import CoreImage

class QAFilterModule: ObservableObject {
    @Published var isProcessing = false
    
    private var classificationModel: VNCoreMLModel?
    private let qualityAnalyzer = QualityAnalyzer()
    
    init() {
        loadClassificationModel()
    }
    
    private func loadClassificationModel() {
        do {
            guard let modelURL = Bundle.main.url(forResource: "WoundClassification", withExtension: "mlmodelc") else {
                print("分類模型文件未找到，將使用模擬分類")
                return
            }
            
            let model = try MLModel(contentsOf: modelURL)
            classificationModel = try VNCoreMLModel(for: model)
            print("成功載入分類模型")
        } catch {
            print("載入分類模型失敗: \(error.localizedDescription)")
            print("將使用模擬分類作為備用方案")
        }
    }
    
    func evaluateQuality(_ processedImage: ProcessedImage) async throws -> QAResult {
        await MainActor.run {
            isProcessing = true
        }
        
        defer { 
            Task { @MainActor in
                isProcessing = false
            }
        }
        
        let qualityCheck = try await performQualityCheck(processedImage)
        
        guard qualityCheck.isValid else {
            return QAResult(
                isValid: false,
                qualityScore: qualityCheck.score,
                failureReason: qualityCheck.failureReason,
                classification: nil
            )
        }
        
        let classification = try await performInitialClassification(processedImage.image)
        
        return QAResult(
            isValid: true,
            qualityScore: qualityCheck.score,
            failureReason: nil,
            classification: classification
        )
    }
    
    private func performQualityCheck(_ processedImage: ProcessedImage) async throws -> QualityCheckResult {
        let metrics = processedImage.qualityMetrics
        let adaptiveThresholds = QualityThresholds.current.adaptiveThresholds(for: metrics)
        
        var score: Double = 0
        var failureReasons: [String] = []
        
        // 信噪比檢查 (30% 權重)
        let snrThreshold = adaptiveThresholds.minSNR
        let snrThresholdMid = snrThreshold * 0.75
        
        if metrics.snr >= snrThreshold {
            score += 0.3
        } else if metrics.snr >= snrThresholdMid {
            score += 0.15
            failureReasons.append("信噪比偏低 (\(String(format: "%.1f", metrics.snr))dB)")
        } else {
            failureReasons.append("信噪比過低 (\(String(format: "%.1f", metrics.snr))dB < \(String(format: "%.1f", snrThresholdMid))dB)")
        }
        
        // 模糊度檢查 (30% 權重)
        let blurThreshold = adaptiveThresholds.minBlurVariance
        let blurThresholdMid = blurThreshold * 0.7
        
        if metrics.blurLevel >= blurThreshold {
            score += 0.3
        } else if metrics.blurLevel >= blurThresholdMid {
            score += 0.15
            failureReasons.append("影像略為模糊")
        } else {
            failureReasons.append("影像模糊度過高")
        }
        
        // 深度覆蓋檢查 (40% 權重)
        let depthThreshold = adaptiveThresholds.minDepthCoverage
        let depthThresholdMid = depthThreshold * 0.7
        
        if metrics.depthCoverage >= depthThreshold {
            score += 0.4
        } else if metrics.depthCoverage >= depthThresholdMid {
            score += 0.2
            failureReasons.append("深度資料覆蓋偏低 (\(String(format: "%.1f", metrics.depthCoverage * 100))%)")
        } else {
            failureReasons.append("深度資料覆蓋不足 (\(String(format: "%.1f", metrics.depthCoverage * 100))% < \(String(format: "%.1f", depthThresholdMid * 100))%)")
        }
        
        // 使用自適應的總分要求
        let overallThreshold = adaptiveThresholds.minOverallQuality
        let isValid = score >= overallThreshold
        let failureReason = failureReasons.isEmpty ? nil : failureReasons.joined(separator: ", ")
        
        #if targetEnvironment(simulator)
        let environment = "模擬器"
        #else
        let environment = "實機"
        #endif
        print("\(environment) QA 檢查結果 - 總分: \(String(format: "%.2f", score))/\(String(format: "%.2f", overallThreshold)), 通過: \(isValid)")
        print("門檻設定 - SNR: \(String(format: "%.1f", snrThreshold)), 模糊度: \(String(format: "%.1f", blurThreshold)), 深度: \(String(format: "%.2f", depthThreshold))")
        
        return QualityCheckResult(
            isValid: isValid,
            score: score,
            failureReason: failureReason
        )
    }
    
    private func performInitialClassification(_ image: UIImage) async throws -> WoundClassification {
        guard let cgImage = image.cgImage else {
            throw QAFilterError.invalidImage
        }
        
        if let coreMLModel = classificationModel {
            return try await performCoreMLClassification(cgImage, model: coreMLModel)
        } else {
            return performMockClassification(cgImage)
        }
    }
    
    private func performCoreMLClassification(_ image: CGImage, model: VNCoreMLModel) async throws -> WoundClassification {
        return try await withCheckedThrowingContinuation { continuation in
            let request = VNCoreMLRequest(model: model) { request, error in
                if let error = error {
                    continuation.resume(throwing: error)
                    return
                }
                
                guard let results = request.results as? [VNClassificationObservation],
                      let acuteResult = results.first(where: { $0.identifier == "acute" }),
                      let chronicResult = results.first(where: { $0.identifier == "chronic" }) else {
                    continuation.resume(throwing: QAFilterError.classificationFailed)
                    return
                }
                
                let classification = WoundClassification(
                    acuteScore: Double(acuteResult.confidence),
                    chronicScore: Double(chronicResult.confidence),
                    confidence: Double(max(acuteResult.confidence, chronicResult.confidence))
                )
                
                continuation.resume(returning: classification)
            }
            
            let handler = VNImageRequestHandler(cgImage: image)
            do {
                try handler.perform([request])
            } catch {
                continuation.resume(throwing: error)
            }
        }
    }
    
    private func performMockClassification(_ image: CGImage) -> WoundClassification {
        let brightness = calculateImageBrightness(image)
        let redness = calculateImageRedness(image)
        
        var acuteScore = 0.5
        var chronicScore = 0.5
        
        if redness > 0.6 {
            acuteScore += 0.3
            chronicScore -= 0.2
        }
        
        if brightness < 0.4 {
            chronicScore += 0.2
            acuteScore -= 0.1
        }
        
        acuteScore = max(0.0, min(1.0, acuteScore))
        chronicScore = max(0.0, min(1.0, chronicScore))
        
        let confidence = max(acuteScore, chronicScore)
        
        return WoundClassification(
            acuteScore: acuteScore,
            chronicScore: chronicScore,
            confidence: confidence
        )
    }
    
    private func calculateImageBrightness(_ image: CGImage) -> Double {
        let width = image.width
        let height = image.height
        let bytesPerPixel = 4
        let bytesPerRow = width * bytesPerPixel
        
        var pixelData = [UInt8](repeating: 0, count: width * height * bytesPerPixel)
        
        let colorSpace = CGColorSpaceCreateDeviceRGB()
        let context = CGContext(
            data: &pixelData,
            width: width,
            height: height,
            bitsPerComponent: 8,
            bytesPerRow: bytesPerRow,
            space: colorSpace,
            bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue
        )
        
        context?.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
        
        var totalBrightness: Double = 0
        let pixelCount = width * height
        
        for i in stride(from: 0, to: pixelData.count, by: 4) {
            let r = Double(pixelData[i])
            let g = Double(pixelData[i + 1])
            let b = Double(pixelData[i + 2])
            
            let brightness = (r * 0.299 + g * 0.587 + b * 0.114) / 255.0
            totalBrightness += brightness
        }
        
        return totalBrightness / Double(pixelCount)
    }
    
    private func calculateImageRedness(_ image: CGImage) -> Double {
        let width = image.width
        let height = image.height
        let bytesPerPixel = 4
        let bytesPerRow = width * bytesPerPixel
        
        var pixelData = [UInt8](repeating: 0, count: width * height * bytesPerPixel)
        
        let colorSpace = CGColorSpaceCreateDeviceRGB()
        let context = CGContext(
            data: &pixelData,
            width: width,
            height: height,
            bitsPerComponent: 8,
            bytesPerRow: bytesPerRow,
            space: colorSpace,
            bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue
        )
        
        context?.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
        
        var totalRedness: Double = 0
        let pixelCount = width * height
        
        for i in stride(from: 0, to: pixelData.count, by: 4) {
            let r = Double(pixelData[i])
            let g = Double(pixelData[i + 1])
            let b = Double(pixelData[i + 2])
            
            let redness = r / (r + g + b + 1)
            totalRedness += redness
        }
        
        return totalRedness / Double(pixelCount)
    }
}

class QualityAnalyzer {
    private let thresholds = QualityThresholds.current
    
    func analyzeDepthQuality(_ depthData: Data) -> DepthQualityResult {
        let coverage = calculateDepthCoverage(depthData)
        let confidence = calculateDepthConfidence(depthData)
        
        return DepthQualityResult(
            coverage: coverage,
            confidence: confidence,
            isAcceptable: coverage >= thresholds.minDepthCoverage && confidence >= 0.7
        )
    }
    
    private func calculateDepthCoverage(_ depthData: Data) -> Double {
        return 0.85
    }
    
    private func calculateDepthConfidence(_ depthData: Data) -> Double {
        return 0.9
    }
}

struct QAResult {
    let isValid: Bool
    let qualityScore: Double
    let failureReason: String?
    let classification: WoundClassification?
}

struct QualityCheckResult {
    let isValid: Bool
    let score: Double
    let failureReason: String?
}

struct DepthQualityResult {
    let coverage: Double
    let confidence: Double
    let isAcceptable: Bool
}

enum QAFilterError: Error, LocalizedError {
    case invalidImage
    case classificationFailed
    case modelLoadFailed
    case insufficientQuality
    
    var errorDescription: String? {
        switch self {
        case .invalidImage:
            return "無效的影像資料"
        case .classificationFailed:
            return "初步分類失敗"
        case .modelLoadFailed:
            return "模型載入失敗"
        case .insufficientQuality:
            return "影像品質不符合標準"
        }
    }
}