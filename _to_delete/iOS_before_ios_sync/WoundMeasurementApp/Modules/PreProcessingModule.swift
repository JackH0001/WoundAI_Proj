import SwiftUI
import CoreImage
import Vision
import Accelerate
import CoreML
import UIKit

class PreProcessingModule: ObservableObject {
    private let context = CIContext()
    private let qualityThresholds = QualityThresholds.current
    private var smartROI: SmartROIModule?
    private var qualityPredictor: MLModel?
    
    init() {
        loadQualityPredictor()
        // SmartROI will be initialized lazily when needed
    }
    
    private func loadQualityPredictor() {
        // 在實際實作中，這裡會載入訓練好的CoreML模型
        // qualityPredictor = try? MLModel(contentsOf: qualityModelURL)
    }
    
    @MainActor
    private func initializeSmartROI() async {
        smartROI = SmartROIModule()
        print("SmartROI模組初始化成功")
    }
    
    func processImage(_ image: UIImage, depthData: Data) async throws -> ProcessedImage {
        // 使用新的圖像驗證系統
        guard validateImageForPreProcessing(image) else {
            let issues = diagnoseImageIssues(image)
            print("PreProcessing錯誤: 圖像驗證失敗")
            print("問題詳情:\n\(issues.joined(separator: "\n"))")
            print(getDiagnosticInfo(image))
            throw PreProcessingError.invalidImage
        }
        
        guard let cgImage = image.cgImage else {
            throw PreProcessingError.invalidImage
        }
        
        var ciImage = CIImage(cgImage: cgImage)
        
        // 第一階段：智慧ROI檢測
        if smartROI == nil {
            print("PreProcessing: 初始化SmartROI模組...")
            await initializeSmartROI()
        }
        
        guard let smartROI = smartROI else {
            print("PreProcessing警告：SmartROI模組初始化失敗，使用默認ROI區域")
            let defaultROI = CGRect(x: 0.1, y: 0.1, width: 0.8, height: 0.8)
            return try await processWithDefaultROI(ciImage, depthData: depthData, roi: defaultROI)
        }
        
        print("PreProcessing: 開始SmartROI檢測...")
        let roiResult: SmartROIResult
        do {
            roiResult = try await smartROI.detectWoundROI(from: image, depthData: depthData)
            print("PreProcessing: SmartROI檢測成功，置信度: \(roiResult.confidence)")
        } catch {
            print("PreProcessing警告：SmartROI檢測失敗 (\(error.localizedDescription))，使用默認ROI區域")
            let defaultROI = CGRect(x: 0.1, y: 0.1, width: 0.8, height: 0.8)
            return try await processWithDefaultROI(ciImage, depthData: depthData, roi: defaultROI)
        }
        
        // 第二階段：基於ROI進行預處理
        ciImage = try performWhiteBalanceCorrection(ciImage)
        ciImage = try performGeometricCorrection(ciImage, depthData: depthData)
        
        // 第三階段：裁切到檢測的ROI
        var roiRect = CGRect(
            x: roiResult.roi.origin.x * ciImage.extent.width,
            y: roiResult.roi.origin.y * ciImage.extent.height,
            width: roiResult.roi.width * ciImage.extent.width,
            height: roiResult.roi.height * ciImage.extent.height
        )
        
        // 繰約驗證和修正ROI區域，防止1x1像素問題
        roiRect = CGRect(
            x: max(0, min(roiRect.origin.x, ciImage.extent.width - 50)),
            y: max(0, min(roiRect.origin.y, ciImage.extent.height - 50)),
            width: max(50, min(roiRect.width, ciImage.extent.width - roiRect.origin.x)),
            height: max(50, min(roiRect.height, ciImage.extent.height - roiRect.origin.y))
        )
        
        print("PreProcessing: ROI裁切詳情 - 原始圖像: \(ciImage.extent), ROI比例: \(roiResult.roi), 原始ROI: \(roiResult.roi.width * ciImage.extent.width)x\(roiResult.roi.height * ciImage.extent.height), 修正ROI: \(roiRect)")
        
        // 驗證ROI是否有效
        guard roiRect.width >= 50 && roiRect.height >= 50 else {
            print("PreProcessing: ROI仍過小，使用默認ROI - \(roiRect)")
            let defaultROI = CGRect(
                x: ciImage.extent.width * 0.1,
                y: ciImage.extent.height * 0.1,
                width: ciImage.extent.width * 0.8,
                height: ciImage.extent.height * 0.8
            )
            let croppedImage = ciImage.cropped(to: defaultROI)
            print("PreProcessing: 使用默認ROI，結果圖像: \(croppedImage.extent)")
            return try await processWithDefaultROI(croppedImage, depthData: depthData, roi: CGRect(x: 0.1, y: 0.1, width: 0.8, height: 0.8))
        }
        
        let croppedImage = ciImage.cropped(to: roiRect)
        print("PreProcessing: ROI裁切後圖像大小: \(croppedImage.extent)")
        
        // 再次驗證裁切結果
        guard croppedImage.extent.width >= 50 && croppedImage.extent.height >= 50 else {
            print("PreProcessing錯誤: 裁切後圖像過小 - \(croppedImage.extent)，使用默認ROI")
            let _ = CGRect(
                x: ciImage.extent.width * 0.1,
                y: ciImage.extent.height * 0.1,
                width: ciImage.extent.width * 0.8,
                height: ciImage.extent.height * 0.8
            )
            return try await processWithDefaultROI(ciImage, depthData: depthData, roi: CGRect(x: 0.1, y: 0.1, width: 0.8, height: 0.8))
        }
        
        // 第四階段：多尺度影像金字塔處理
        let multiScaleImages = try generateImagePyramid(croppedImage)
        
        // 第五階段：機器學習品質預測
        let predictedQuality = try await predictImageQuality(croppedImage, features: roiResult.features)
        
        // 第六階段：綜合品質評估
        let qualityMetrics = try calculateEnhancedQualityMetrics(croppedImage, predictedQuality: predictedQuality)
        
        guard let outputCGImage = context.createCGImage(croppedImage, from: croppedImage.extent) else {
            throw PreProcessingError.processingFailed
        }
        
        let processedUIImage = UIImage(cgImage: outputCGImage)
        
        return ProcessedImage(
            image: processedUIImage,
            depthData: depthData,
            qualityMetrics: qualityMetrics,
            roi: roiRect,
            woundFeatures: roiResult.features,
            multiScaleImages: multiScaleImages,
            roiConfidence: roiResult.confidence
        )
    }
    
    private func processWithDefaultROI(_ ciImage: CIImage, depthData: Data, roi: CGRect) async throws -> ProcessedImage {
        // 備選處理流程，使用簡化的ROI處理
        print("PreProcessing: 執行默認ROI處理，輸入圖像尺寸: \(ciImage.extent)")
        
        // 跳過可能導致問題的影像處理步驟
        let processedImage = ciImage
        print("PreProcessing: 默認ROI處理完成，輸出圖像尺寸: \(processedImage.extent)")
        
        // 裁切到默認ROI區域
        let roiRect = CGRect(
            x: roi.origin.x * processedImage.extent.width,
            y: roi.origin.y * processedImage.extent.height,
            width: roi.width * processedImage.extent.width,
            height: roi.height * processedImage.extent.height
        )
        
        print("PreProcessing: 默認ROI處理 - 原始圖像: \(processedImage.extent), ROI比例: \(roi), 計算ROI: \(roiRect)")
        
        // 驗證並修正ROI區域
        let correctedROI = CGRect(
            x: max(0, min(roiRect.origin.x, processedImage.extent.width - 50)),
            y: max(0, min(roiRect.origin.y, processedImage.extent.height - 50)),
            width: max(50, min(roiRect.width, processedImage.extent.width - roiRect.origin.x)),
            height: max(50, min(roiRect.height, processedImage.extent.height - roiRect.origin.y))
        )
        
        // 驗證ROI是否有效，如果無效使用全圖
        let finalROI: CGRect
        if correctedROI.width >= 50 && correctedROI.height >= 50 {
            finalROI = correctedROI
        } else {
            print("PreProcessing: 默認ROI過小，使用全圖 - 原始: \(roiRect), 修正: \(correctedROI)")
            finalROI = CGRect(x: 0, y: 0, width: processedImage.extent.width, height: processedImage.extent.height)
        }
        
        let croppedImage = processedImage.cropped(to: finalROI)
        print("PreProcessing: 默認ROI處理後圖像大小: \(croppedImage.extent)")
        
        // 終極驗證裁切結果
        guard croppedImage.extent.width >= 10 && croppedImage.extent.height >= 10 else {
            print("PreProcessing錯誤: 終極裁切後圖像過小 - \(croppedImage.extent)，拋棄裁切")
            return try await processRawImage(processedImage, depthData: depthData)
        }
        
        // 生成多尺度影像
        let multiScaleImages = try generateImagePyramid(croppedImage)
        
        // 基本品質評估（不使用ML預測）
        let qualityMetrics = try calculateQualityMetrics(croppedImage, depthData: depthData)
        
        guard let outputCGImage = context.createCGImage(croppedImage, from: croppedImage.extent) else {
            throw PreProcessingError.processingFailed
        }
        
        let processedUIImage = UIImage(cgImage: outputCGImage)
        
        // 創建默認的傷口特徵
        let defaultFeatures = WoundFeatures(
            area: Double(roiRect.width * roiRect.height),
            aspectRatio: roiRect.width / roiRect.height,
            colorDistribution: ColorDistribution(
                redMean: 0.5, greenMean: 0.4, blueMean: 0.3,
                redStd: 0.1, greenStd: 0.1, blueStd: 0.1,
                saturation: 0.5, brightness: 0.5, contrast: 0.5
            ),
            textureHomogeneity: 0.6,
            textureContrast: 0.7,
            edgeRoughness: 0.5,
            symmetryIndex: 0.4,
            centroid: CGPoint(x: roiRect.midX, y: roiRect.midY),
            boundingBox: roiRect,
            perimeter: 2.0 * (roiRect.width + roiRect.height),
            circularity: 0.8,
            compactness: 0.7
        )
        
        return ProcessedImage(
            image: processedUIImage,
            depthData: depthData,
            qualityMetrics: qualityMetrics,
            roi: finalROI,
            woundFeatures: defaultFeatures,
            multiScaleImages: multiScaleImages,
            roiConfidence: 0.5
        )
    }
    
    private func performWhiteBalanceCorrection(_ image: CIImage) throws -> CIImage {
        print("PreProcessing: 執行白平衡校正，輸入圖像尺寸: \(image.extent)")
        
        // 暫時跳過白平衡校正，因為它正在導致圖像尺寸變為1x1
        // 直接返回原始圖像，避免濾鏡處理問題
        print("PreProcessing: 跳過白平衡校正以避免圖像尺寸問題，輸出圖像尺寸: \(image.extent)")
        return image
        
        /* 原始白平衡邏輯 - 暫時禁用
        let histogram = calculateImageHistogram(image)
        let grayWorldGains = calculateGrayWorldGains(histogram)
        
        // 驗證gains值是否合理
        guard grayWorldGains.red > 0.1 && grayWorldGains.red < 3.0,
              grayWorldGains.green > 0.1 && grayWorldGains.green < 3.0,
              grayWorldGains.blue > 0.1 && grayWorldGains.blue < 3.0 else {
            print("PreProcessing警告: 白平衡gains異常，跳過校正")
            return image
        }
        
        guard let whiteBalanceFilter = CIFilter(name: "CIColorMatrix") else {
            print("PreProcessing警告: 無法創建白平衡濾鏡，跳過校正")
            return image
        }
        
        whiteBalanceFilter.setValue(image, forKey: kCIInputImageKey)
        whiteBalanceFilter.setValue(CIVector(x: grayWorldGains.red, y: 0, z: 0, w: 0), forKey: "inputRVector")
        whiteBalanceFilter.setValue(CIVector(x: 0, y: grayWorldGains.green, z: 0, w: 0), forKey: "inputGVector") 
        whiteBalanceFilter.setValue(CIVector(x: 0, y: 0, z: grayWorldGains.blue, w: 0), forKey: "inputBVector")
        whiteBalanceFilter.setValue(CIVector(x: 0, y: 0, z: 0, w: 1), forKey: "inputAVector")
        
        guard let outputImage = whiteBalanceFilter.outputImage else {
            print("PreProcessing警告: 白平衡濾鏡輸出失敗，使用原始圖像")
            return image
        }
        
        print("PreProcessing: 白平衡校正完成，輸出圖像尺寸: \(outputImage.extent)")
        return outputImage
        */
    }
    
    private func performGeometricCorrection(_ image: CIImage, depthData: Data) throws -> CIImage {
        print("PreProcessing: 執行幾何校正，輸入圖像尺寸: \(image.extent)")
        
        // 暫時跳過幾何校正，因為透視校正可能導致圖像尺寸問題
        print("PreProcessing: 跳過幾何校正以避免圖像尺寸問題，輸出圖像尺寸: \(image.extent)")
        return image
        
        /* 原始幾何校正邏輯 - 暫時禁用
        let planes = try detectPlanes(from: depthData)
        
        guard let dominantPlane = planes.first else {
            print("PreProcessing: 未檢測到平面，跳過透視校正")
            return image
        }
        
        let transform = calculatePerspectiveTransform(for: dominantPlane)
        
        guard let perspectiveFilter = CIFilter(name: "CIPerspectiveCorrection") else {
            print("PreProcessing警告: 無法創建透視校正濾鏡")
            throw PreProcessingError.filterCreationFailed
        }
        
        // 驗證變換點是否在圖像範圍內
        let imageRect = image.extent
        guard imageRect.contains(transform.topLeft) && imageRect.contains(transform.topRight) &&
              imageRect.contains(transform.bottomLeft) && imageRect.contains(transform.bottomRight) else {
            print("PreProcessing警告: 透視變換點超出圖像範圍，跳過校正")
            return image
        }
        
        perspectiveFilter.setValue(image, forKey: kCIInputImageKey)
        perspectiveFilter.setValue(CIVector(cgPoint: transform.topLeft), forKey: "inputTopLeft")
        perspectiveFilter.setValue(CIVector(cgPoint: transform.topRight), forKey: "inputTopRight")
        perspectiveFilter.setValue(CIVector(cgPoint: transform.bottomLeft), forKey: "inputBottomLeft")
        perspectiveFilter.setValue(CIVector(cgPoint: transform.bottomRight), forKey: "inputBottomRight")
        
        guard let correctedImage = perspectiveFilter.outputImage else {
            print("PreProcessing警告: 透視校正輸出失敗，使用原始圖像")
            return image
        }
        
        print("PreProcessing: 幾何校正完成，輸出圖像尺寸: \(correctedImage.extent)")
        return correctedImage
        */
    }
    
    private func performROIDetection(_ image: CIImage, depthData: Data) throws -> CIImage {
        let request = VNDetectRectanglesRequest()
        request.minimumAspectRatio = 0.3
        request.maximumAspectRatio = 3.0
        request.minimumSize = 0.1
        request.maximumObservations = 5
        
        let handler = VNImageRequestHandler(ciImage: image)
        try handler.perform([request])
        
        guard let observations = request.results?.first else {
            let cropRect = CGRect(
                x: image.extent.width * 0.1,
                y: image.extent.height * 0.1,
                width: image.extent.width * 0.8,
                height: image.extent.height * 0.8
            )
            return image.cropped(to: cropRect)
        }
        
        let boundingBox = observations.boundingBox
        let imageRect = CGRect(
            x: boundingBox.origin.x * image.extent.width,
            y: (1 - boundingBox.origin.y - boundingBox.height) * image.extent.height,
            width: boundingBox.width * image.extent.width,
            height: boundingBox.height * image.extent.height
        )
        
        return image.cropped(to: imageRect)
    }
    
    private func calculateQualityMetrics(_ image: CIImage, depthData: Data? = nil) throws -> QualityMetrics {
        let snr = calculateSNR(image)
        let blurVariance = calculateBlurLevel(image)
        let contrastRatio = calculateContrastRatio(image)
        let colorBalance = calculateColorBalance(image)
        let coverage = calculateDepthCoverage(image, depthData: depthData)
        
        // 使用自適應品質門檻
        let preliminaryMetrics = QualityMetrics(
            snr: snr,
            blurVariance: blurVariance,
            contrastRatio: contrastRatio,
            colorBalance: colorBalance,
            overallQuality: (snr + blurVariance + contrastRatio + colorBalance + coverage) / 5.0,
            isAcceptable: false,
            blurLevel: blurVariance,
            depthCoverage: coverage
        )
        
        let adaptiveThresholds = qualityThresholds.adaptiveThresholds(for: preliminaryMetrics)
        
        let overallQuality = (snr + blurVariance + contrastRatio + colorBalance + coverage) / 5.0
        let isAcceptable = snr >= adaptiveThresholds.minSNR && 
                         blurVariance >= adaptiveThresholds.minBlurVariance &&
                         contrastRatio >= adaptiveThresholds.minContrastRatio &&
                         colorBalance >= adaptiveThresholds.minColorBalance &&
                         coverage >= adaptiveThresholds.minDepthCoverage
        
        #if targetEnvironment(simulator)
        let environment = "模擬器"
        #else
        let environment = "實機"
        #endif
        print("\(environment)品質檢查 - SNR: \(String(format: "%.1f", snr))/\(String(format: "%.1f", adaptiveThresholds.minSNR)), 模糊度: \(String(format: "%.1f", blurVariance))/\(String(format: "%.1f", adaptiveThresholds.minBlurVariance)), 對比度: \(String(format: "%.2f", contrastRatio))/\(String(format: "%.2f", adaptiveThresholds.minContrastRatio)), 色彩平衡: \(String(format: "%.2f", colorBalance))/\(String(format: "%.2f", adaptiveThresholds.minColorBalance)), 深度覆蓋: \(String(format: "%.2f", coverage))/\(String(format: "%.2f", adaptiveThresholds.minDepthCoverage)), 通過: \(isAcceptable)")
        
        return QualityMetrics(
            snr: snr,
            blurVariance: blurVariance,
            contrastRatio: contrastRatio,
            colorBalance: colorBalance,
            overallQuality: overallQuality,
            isAcceptable: isAcceptable,
            blurLevel: blurVariance,
            depthCoverage: coverage
        )
    }
    
    private func calculateSNR(_ image: CIImage) -> Double {
        guard let cgImage = context.createCGImage(image, from: image.extent) else { return 0 }
        
        let width = cgImage.width
        let height = cgImage.height
        let bytesPerPixel = 4
        let bytesPerRow = width * bytesPerPixel
        let bitsPerComponent = 8
        
        var pixelData = [UInt8](repeating: 0, count: width * height * bytesPerPixel)
        
        let colorSpace = CGColorSpaceCreateDeviceRGB()
        let context = CGContext(
            data: &pixelData,
            width: width,
            height: height,
            bitsPerComponent: bitsPerComponent,
            bytesPerRow: bytesPerRow,
            space: colorSpace,
            bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue
        )
        
        context?.draw(cgImage, in: CGRect(x: 0, y: 0, width: width, height: height))
        
        var sum: Double = 0
        var sumSquared: Double = 0
        let pixelCount = width * height
        
        for i in stride(from: 0, to: pixelData.count, by: 4) {
            let gray = Double(pixelData[i]) * 0.299 + Double(pixelData[i+1]) * 0.587 + Double(pixelData[i+2]) * 0.114
            sum += gray
            sumSquared += gray * gray
        }
        
        let mean = sum / Double(pixelCount)
        let variance = (sumSquared / Double(pixelCount)) - (mean * mean)
        let standardDeviation = sqrt(variance)
        
        return standardDeviation > 0 ? 20 * log10(mean / standardDeviation) : 0
    }
    
    private func calculateBlurLevel(_ image: CIImage) -> Double {
        guard let laplacianFilter = CIFilter(name: "CIConvolution3X3") else { return 0 }
        
        let laplacianKernel = CIVector(values: [0, -1, 0, -1, 4, -1, 0, -1, 0], count: 9)
        
        laplacianFilter.setValue(image, forKey: kCIInputImageKey)
        laplacianFilter.setValue(laplacianKernel, forKey: "inputWeights")
        
        guard let outputImage = laplacianFilter.outputImage,
              let cgImage = context.createCGImage(outputImage, from: outputImage.extent) else {
            return 0
        }
        
        let width = cgImage.width
        let height = cgImage.height
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
        
        context?.draw(cgImage, in: CGRect(x: 0, y: 0, width: width, height: height))
        
        var variance: Double = 0
        var sum: Double = 0
        let pixelCount = width * height
        
        for i in stride(from: 0, to: pixelData.count, by: 4) {
            let gray = Double(pixelData[i]) * 0.299 + Double(pixelData[i+1]) * 0.587 + Double(pixelData[i+2]) * 0.114
            sum += gray
        }
        
        let mean = sum / Double(pixelCount)
        
        for i in stride(from: 0, to: pixelData.count, by: 4) {
            let gray = Double(pixelData[i]) * 0.299 + Double(pixelData[i+1]) * 0.587 + Double(pixelData[i+2]) * 0.114
            variance += pow(gray - mean, 2)
        }
        
        return variance / Double(pixelCount)
    }
    
    private func calculateDepthCoverage(_ image: CIImage, depthData: Data? = nil) -> Double {
        guard let depthData = depthData, !depthData.isEmpty else {
            #if targetEnvironment(simulator)
            return calculateImageBasedDepthCoverage(image)
            #else
            print("警告：深度數據不可用，使用預設值")
            return 0.7
            #endif
        }
        
        // 按照技術文件建議進行深度數據驗證
        let depthQuality = validateDepthData(depthData)
        return depthQuality.coverageInROI
    }
    
    // 新增：按技術文件標準驗證深度數據
    private func validateDepthData(_ depthData: Data) -> DepthQualityInfo {
        // 解析深度數據（ARKit使用Float32）
        let depthValues = depthData.withUnsafeBytes { bytes in
            Array(bytes.bindMemory(to: Float32.self))
        }
        
        guard !depthValues.isEmpty else {
            return DepthQualityInfo(validPixelRatio: 0, averageConfidence: 0, depthConsistency: 0, noiseLevel: 1.0, coverageInROI: 0)
        }
        
        var validPixels = 0
        var totalConfidence: Double = 0
        var depthVariances: [Double] = []
        var noiseSum: Double = 0
        
        // 第一次遍歷：統計基本指標
        for (index, depth) in depthValues.enumerated() {
            let depthValue = Double(depth)
            
            // 按技術文件建議：過濾 0.001-2.0 公尺範圍外的異常值
            if depthValue >= 0.001 && depthValue <= 2.0 {
                validPixels += 1
                
                // 模擬信心值（實際中可能有單獨的信心圖）
                let confidence = calculateDepthConfidence(depthValue, neighbors: getNeighborDepths(depthValues, index: index))
                totalConfidence += confidence
                
                // 計算局部變異性
                let localVariance = calculateLocalVariance(depthValues, index: index)
                depthVariances.append(localVariance)
                
                // 噪聲評估
                if localVariance > 0.05 { // 5cm 以上的變異視為噪聲
                    noiseSum += localVariance
                }
            }
        }
        
        let validPixelRatio = Double(validPixels) / Double(depthValues.count)
        let averageConfidence = validPixels > 0 ? totalConfidence / Double(validPixels) : 0.0
        let depthConsistency = calculateDepthConsistency(depthVariances)
        let noiseLevel = min(noiseSum / Double(max(validPixels, 1)), 1.0)
        
        // 按技術文件標準：深度覆蓋率 ≥ 80%，信心度 ≥ 0.7
        let coverageInROI = validPixelRatio
        
        let qualityInfo = DepthQualityInfo(
            validPixelRatio: validPixelRatio,
            averageConfidence: averageConfidence,
            depthConsistency: depthConsistency,
            noiseLevel: noiseLevel,
            coverageInROI: coverageInROI
        )
        
        print("深度品質驗證: 有效像素比例=\(String(format: "%.2f", validPixelRatio*100))%, 平均信心度=\(String(format: "%.2f", averageConfidence)), 深度一致性=\(String(format: "%.2f", depthConsistency)), 通過標準=\(qualityInfo.isAcceptable)")
        
        return qualityInfo
    }
    
    // 計算深度信心值
    private func calculateDepthConfidence(_ depth: Double, neighbors: [Double]) -> Double {
        guard !neighbors.isEmpty else { return 0.5 }
        
        let avgNeighbor = neighbors.reduce(0, +) / Double(neighbors.count)
        let difference = Swift.abs(depth - avgNeighbor)
        
        // 如果跟鄰近值差異小，信心度高
        return max(0.0, min(1.0, 1.0 - difference / 0.1)) // 10cm差異內視為高信心度
    }
    
    // 獲取鄰近像素深度值
    private func getNeighborDepths(_ depthValues: [Float32], index: Int) -> [Double] {
        let width = Int(sqrt(Double(depthValues.count))) // 假設是正方形
        guard width > 0 else { return [] }
        
        let row = index / width
        let col = index % width
        var neighbors: [Double] = []
        
        // 3x3鄰近區域
        for dr in -1...1 {
            for dc in -1...1 {
                if dr == 0 && dc == 0 { continue }
                let newRow = row + dr
                let newCol = col + dc
                if newRow >= 0 && newRow < width && newCol >= 0 && newCol < width {
                    let neighborIndex = newRow * width + newCol
                    if neighborIndex < depthValues.count {
                        let neighborDepth = Double(depthValues[neighborIndex])
                        if neighborDepth >= 0.001 && neighborDepth <= 2.0 {
                            neighbors.append(neighborDepth)
                        }
                    }
                }
            }
        }
        
        return neighbors
    }
    
    // 計算局部變異性
    private func calculateLocalVariance(_ depthValues: [Float32], index: Int) -> Double {
        let neighbors = getNeighborDepths(depthValues, index: index)
        guard neighbors.count > 1 else { return 0.0 }
        
        let mean = neighbors.reduce(0, +) / Double(neighbors.count)
        let variance = neighbors.map { pow($0 - mean, 2) }.reduce(0, +) / Double(neighbors.count)
        
        return sqrt(variance) // 標準差
    }
    
    // 計算深度一致性
    private func calculateDepthConsistency(_ variances: [Double]) -> Double {
        guard !variances.isEmpty else { return 0.0 }
        
        let avgVariance = variances.reduce(0, +) / Double(variances.count)
        // 變異性越低，一致性越高
        return max(0.0, min(1.0, 1.0 - avgVariance / 0.1)) // 10cm以下變異視為高一致性
    }
    
    private func calculateImageBasedDepthCoverage(_ image: CIImage) -> Double {
        // 在沒有深度數據時，基於圖像特徵估算深度覆蓋率
        guard let cgImage = context.createCGImage(image, from: image.extent) else { return 0.6 }
        
        // 使用圖像的銳利度和對比度作為深度品質的指標
        let sharpness = calculateImageSharpness(cgImage)
        let contrast = calculateBasicContrast(cgImage)
        
        // 結合銳利度和對比度計算估算深度覆蓋率
        let estimatedCoverage = (sharpness * 0.6 + contrast * 0.4)
        
        return min(max(estimatedCoverage, 0.3), 0.9)
    }
    
    private func calculateImageSharpness(_ cgImage: CGImage) -> Double {
        let width = cgImage.width
        let height = cgImage.height
        
        guard let data = cgImage.dataProvider?.data,
              let bytes = CFDataGetBytePtr(data) else {
            return 0.5
        }
        
        let bytesPerPixel = 4
        let bytesPerRow = width * bytesPerPixel
        
        var gradientSum: Double = 0
        var pixelCount = 0
        
        // 計算Sobel梯度
        for y in 1..<(height-1) {
            for x in 1..<(width-1) {
                let offset = y * bytesPerRow + x * bytesPerPixel
                
                // 獲取當前像素和相鄰像素的灰度值
                let current = Double(bytes[offset]) * 0.299 + Double(bytes[offset+1]) * 0.587 + Double(bytes[offset+2]) * 0.114
                let right = Double(bytes[offset + bytesPerPixel]) * 0.299 + Double(bytes[offset + bytesPerPixel + 1]) * 0.587 + Double(bytes[offset + bytesPerPixel + 2]) * 0.114
                let bottom = Double(bytes[offset + bytesPerRow]) * 0.299 + Double(bytes[offset + bytesPerRow + 1]) * 0.587 + Double(bytes[offset + bytesPerRow + 2]) * 0.114
                
                // 計算梯度
                let gradientX = Swift.abs(right - current)
                let gradientY = Swift.abs(bottom - current)
                let gradient = sqrt(gradientX * gradientX + gradientY * gradientY)
                
                gradientSum += gradient
                pixelCount += 1
            }
        }
        
        let averageGradient = gradientSum / Double(pixelCount)
        return min(averageGradient / 255.0 * 4.0, 1.0) // 正規化到0-1範圍
    }
    
    private func processRawImage(_ ciImage: CIImage, depthData: Data) async throws -> ProcessedImage {
        // 當ROI完全失敗時的最後備選方案 - 使用完整圖像不進行裁切
        print("PreProcessing: 執行原始圖像處理（無ROI裁切），輸入圖像尺寸: \(ciImage.extent)")
        
        // 跳過可能導致問題的影像處理，直接使用原始圖像
        let processedImage = ciImage
        print("PreProcessing: 原始圖像處理完成，輸出圖像尺寸: \(processedImage.extent)")
        
        // 驗證圖像尺寸
        guard processedImage.extent.width > 100 && processedImage.extent.height > 100 else {
            print("PreProcessing錯誤: 原始圖像尺寸異常 - \(processedImage.extent)")
            throw PreProcessingError.processingFailed
        }
        
        // 生成多尺度影像
        let multiScaleImages = try generateImagePyramid(processedImage)
        
        // 基本品質評估
        let qualityMetrics = try calculateQualityMetrics(processedImage, depthData: depthData)
        
        guard let outputCGImage = context.createCGImage(processedImage, from: processedImage.extent) else {
            throw PreProcessingError.processingFailed
        }
        
        let processedUIImage = UIImage(cgImage: outputCGImage)
        
        // 創建全圖的傷口特徵
        let fullImageFeatures = WoundFeatures(
            area: Double(processedImage.extent.width * processedImage.extent.height),
            aspectRatio: processedImage.extent.width / processedImage.extent.height,
            colorDistribution: ColorDistribution(
                redMean: 0.5, greenMean: 0.4, blueMean: 0.3,
                redStd: 0.1, greenStd: 0.1, blueStd: 0.1,
                saturation: 0.5, brightness: 0.5, contrast: 0.5
            ),
            textureHomogeneity: 0.6,
            textureContrast: 0.7,
            edgeRoughness: 0.5,
            symmetryIndex: 0.4,
            centroid: CGPoint(x: processedImage.extent.midX, y: processedImage.extent.midY),
            boundingBox: CGRect(x: 0, y: 0, width: 1, height: 1),
            perimeter: 2.0 * (processedImage.extent.width + processedImage.extent.height),
            circularity: 0.8,
            compactness: 0.7
        )
        
        return ProcessedImage(
            image: processedUIImage,
            depthData: depthData,
            qualityMetrics: qualityMetrics,
            roi: processedImage.extent,
            woundFeatures: fullImageFeatures,
            multiScaleImages: multiScaleImages,
            roiConfidence: 0.3
        )
    }
    
    private func calculateBasicContrast(_ cgImage: CGImage) -> Double {
        guard let data = cgImage.dataProvider?.data,
              let bytes = CFDataGetBytePtr(data) else {
            return 0.5
        }
        
        let width = cgImage.width
        let height = cgImage.height
        let bytesPerPixel = 4
        let bytesPerRow = width * bytesPerPixel
        
        var minGray: Double = 255
        var maxGray: Double = 0
        
        for y in 0..<height {
            for x in 0..<width {
                let offset = y * bytesPerRow + x * bytesPerPixel
                let gray = Double(bytes[offset]) * 0.299 + Double(bytes[offset+1]) * 0.587 + Double(bytes[offset+2]) * 0.114
                minGray = min(minGray, gray)
                maxGray = max(maxGray, gray)
            }
        }
        
        guard maxGray > minGray else { return 0.0 }
        return (maxGray - minGray) / (maxGray + minGray)
    }
    


    private func calculateImageHistogram(_ image: CIImage) -> ImageHistogram {
        guard let cgImage = context.createCGImage(image, from: image.extent) else {
            return ImageHistogram(red: [], green: [], blue: [])
        }
        
        let width = cgImage.width
        let height = cgImage.height
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
        
        context?.draw(cgImage, in: CGRect(x: 0, y: 0, width: width, height: height))
        
        var redHist = [Int](repeating: 0, count: 256)
        var greenHist = [Int](repeating: 0, count: 256)
        var blueHist = [Int](repeating: 0, count: 256)
        
        for i in stride(from: 0, to: pixelData.count, by: 4) {
            redHist[Int(pixelData[i])] += 1
            greenHist[Int(pixelData[i+1])] += 1
            blueHist[Int(pixelData[i+2])] += 1
        }
        
        return ImageHistogram(red: redHist, green: greenHist, blue: blueHist)
    }
    
    private func calculateGrayWorldGains(_ histogram: ImageHistogram) -> ColorGains {
        let redMean = Double(histogram.red.enumerated().reduce(0) { $0 + $1.0 * $1.1 }) / Double(histogram.red.reduce(0, +))
        let greenMean = Double(histogram.green.enumerated().reduce(0) { $0 + $1.0 * $1.1 }) / Double(histogram.green.reduce(0, +))
        let blueMean = Double(histogram.blue.enumerated().reduce(0) { $0 + $1.0 * $1.1 }) / Double(histogram.blue.reduce(0, +))
        
        let grayValue = (redMean + greenMean + blueMean) / 3.0
        
        return ColorGains(
            red: grayValue / redMean,
            green: grayValue / greenMean,
            blue: grayValue / blueMean
        )
    }
    
    private func detectPlanes(from depthData: Data) throws -> [DetectedPlane] {
        return [DetectedPlane(
            center: CGPoint(x: 0.5, y: 0.5),
            normal: SIMD3<Float>(0, 0, 1),
            confidence: 0.8
        )]
    }
    
    private func calculatePerspectiveTransform(for plane: DetectedPlane) -> PerspectiveTransform {
        return PerspectiveTransform(
            topLeft: CGPoint(x: 0.1, y: 0.1),
            topRight: CGPoint(x: 0.9, y: 0.1),
            bottomLeft: CGPoint(x: 0.1, y: 0.9),
            bottomRight: CGPoint(x: 0.9, y: 0.9)
        )
    }
    
    // MARK: - 新增的進階功能
    
    private func generateImagePyramid(_ image: CIImage) throws -> [CIImage] {
        var pyramid: [CIImage] = [image]
        let currentImage = image
        
        // 生成4級金字塔 (原始, 0.75x, 0.5x, 0.25x)
        let scales: [CGFloat] = [0.75, 0.5, 0.25]
        
        for scale in scales {
            let transform = CGAffineTransform(scaleX: scale, y: scale)
            let scaledImage = currentImage.transformed(by: transform)
            pyramid.append(scaledImage)
        }
        
        return pyramid
    }
    
    private func predictImageQuality(_ image: CIImage, features: WoundFeatures) async throws -> PredictedQuality {
        // 如果有ML模型，使用模型預測
        if let predictor = qualityPredictor {
            return try await predictWithMLModel(image, features: features, model: predictor)
        }
        
        // 否則使用基於規則的預測
        return predictWithHeuristics(image, features: features)
    }
    
    private func predictWithMLModel(_ image: CIImage, features: WoundFeatures, model: MLModel) async throws -> PredictedQuality {
        // 準備模型輸入特徵
        _ = prepareMLFeatures(image, woundFeatures: features)
        
        // 在實際實作中會進行ML模型推理
        // let prediction = try model.prediction(from: inputFeatures)
        
        // 暫時返回模擬結果
        return PredictedQuality(
            overallScore: 0.87,
            sharpnessScore: 0.89,
            lightingScore: 0.85,
            colorAccuracyScore: 0.88,
            noiseLevel: 0.12,
            confidenceInterval: (0.82, 0.92)
        )
    }
    
    private func predictWithHeuristics(_ image: CIImage, features: WoundFeatures) -> PredictedQuality {
        // 基於傷口特徵的品質預測
        let colorQuality = evaluateColorQuality(features.colorDistribution)
        let textureQuality = evaluateTextureQuality(features.textureHomogeneity, features.textureContrast)
        let morphologyQuality = evaluateMorphologyQuality(features.edgeRoughness, features.symmetryIndex)
        
        let overallScore = (colorQuality + textureQuality + morphologyQuality) / 3.0
        
        return PredictedQuality(
            overallScore: overallScore,
            sharpnessScore: textureQuality,
            lightingScore: colorQuality,
            colorAccuracyScore: colorQuality,
            noiseLevel: 1.0 - textureQuality,
            confidenceInterval: (overallScore - 0.1, overallScore + 0.1)
        )
    }
    
    private func prepareMLFeatures(_ image: CIImage, woundFeatures: WoundFeatures) -> [String: Any] {
        // 準備ML模型所需的特徵向量
        return [
            "area": woundFeatures.area,
            "aspect_ratio": woundFeatures.aspectRatio,
            "red_mean": woundFeatures.colorDistribution.redMean,
            "green_mean": woundFeatures.colorDistribution.greenMean,
            "blue_mean": woundFeatures.colorDistribution.blueMean,
            "texture_homogeneity": woundFeatures.textureHomogeneity,
            "texture_contrast": woundFeatures.textureContrast,
            "edge_roughness": woundFeatures.edgeRoughness,
            "symmetry_index": woundFeatures.symmetryIndex
        ]
    }
    
    private func evaluateColorQuality(_ colorDist: ColorDistribution) -> Double {
        // 評估色彩品質 - 基於色彩分布的標準差和平均值
        let colorBalance = 1.0 - Swift.abs(colorDist.redMean - colorDist.greenMean) - Swift.abs(colorDist.greenMean - colorDist.blueMean)
        let colorStability = 1.0 - (colorDist.redStd + colorDist.greenStd + colorDist.blueStd) / 3.0
        
        return (colorBalance + colorStability) / 2.0
    }
    
    private func evaluateTextureQuality(_ homogeneity: Double, _ contrast: Double) -> Double {
        // 適度的對比度和同質性表示良好的紋理品質
        let idealContrast = 0.7
        let idealHomogeneity = 0.6
        
        let contrastScore = 1.0 - Swift.abs(contrast - idealContrast)
        let homogeneityScore = 1.0 - Swift.abs(homogeneity - idealHomogeneity)
        
        return (contrastScore + homogeneityScore) / 2.0
    }
    
    private func evaluateMorphologyQuality(_ edgeRoughness: Double, _ symmetry: Double) -> Double {
        // 適度的邊緣粗糙度和低對稱性是傷口的特徵
        let edgeScore = edgeRoughness // 較高的邊緣粗糙度表示傷口邊界清晰
        let asymmetryScore = 1.0 - symmetry // 傷口通常不對稱
        
        return (edgeScore + asymmetryScore) / 2.0
    }
    
    private func calculateEnhancedQualityMetrics(_ image: CIImage, predictedQuality: PredictedQuality) throws -> QualityMetrics {
        // 結合傳統品質指標和ML預測結果
        let traditionalSNR = calculateSNR(image)
        let traditionalBlur = calculateBlurLevel(image)
        let traditionalContrast = calculateContrastRatio(image)
        let traditionalColorBalance = calculateColorBalance(image)
        
        // 加權平均傳統指標和ML預測
        let enhancedSNR = (traditionalSNR + predictedQuality.sharpnessScore * 50) / 2.0
        let enhancedBlur = (traditionalBlur + predictedQuality.sharpnessScore * 200) / 2.0
        let enhancedContrast = (traditionalContrast + predictedQuality.colorAccuracyScore) / 2.0
        let enhancedColorBalance = (traditionalColorBalance + predictedQuality.lightingScore) / 2.0
        
        let overallQuality = (enhancedSNR + enhancedBlur + enhancedContrast + enhancedColorBalance) / 4.0
        
        let preliminaryMetrics = QualityMetrics(
            snr: enhancedSNR,
            blurVariance: enhancedBlur,
            contrastRatio: enhancedContrast,
            colorBalance: enhancedColorBalance,
            overallQuality: overallQuality,
            isAcceptable: false,
            blurLevel: enhancedBlur,
            depthCoverage: predictedQuality.overallScore
        )
        
        let adaptiveThresholds = qualityThresholds.adaptiveThresholds(for: preliminaryMetrics)
        
        let isAcceptable = predictedQuality.overallScore >= adaptiveThresholds.minOverallQuality &&
                          enhancedSNR >= adaptiveThresholds.minSNR &&
                          enhancedBlur >= adaptiveThresholds.minBlurVariance &&
                          enhancedContrast >= adaptiveThresholds.minContrastRatio &&
                          enhancedColorBalance >= adaptiveThresholds.minColorBalance
        
        return QualityMetrics(
            snr: enhancedSNR,
            blurVariance: enhancedBlur,
            contrastRatio: enhancedContrast,
            colorBalance: enhancedColorBalance,
            overallQuality: overallQuality,
            isAcceptable: isAcceptable,
            blurLevel: enhancedBlur,
            depthCoverage: predictedQuality.overallScore
        )
    }
    
    private func calculateContrastRatio(_ image: CIImage) -> Double {
        guard let cgImage = context.createCGImage(image, from: image.extent) else { return 0.5 }
        
        let width = cgImage.width
        let height = cgImage.height
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
        
        context?.draw(cgImage, in: CGRect(x: 0, y: 0, width: width, height: height))
        
        var minLuminance: Double = 255.0
        var maxLuminance: Double = 0.0
        
        // 計算圖像的最小和最大亮度值
        for i in stride(from: 0, to: pixelData.count, by: 4) {
            let r = Double(pixelData[i])
            let g = Double(pixelData[i+1])
            let b = Double(pixelData[i+2])
            
            // 使用標準亮度公式
            let luminance = r * 0.299 + g * 0.587 + b * 0.114
            minLuminance = min(minLuminance, luminance)
            maxLuminance = max(maxLuminance, luminance)
        }
        
        // Weber對比度公式
        guard minLuminance > 0 else { return 0.0 }
        return (maxLuminance - minLuminance) / (maxLuminance + minLuminance)
    }
    
    private func calculateColorBalance(_ image: CIImage) -> Double {
        guard let cgImage = context.createCGImage(image, from: image.extent) else { return 0.8 }
        
        let width = cgImage.width
        let height = cgImage.height
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
        
        context?.draw(cgImage, in: CGRect(x: 0, y: 0, width: width, height: height))
        
        var redSum: Double = 0
        var greenSum: Double = 0
        var blueSum: Double = 0
        let pixelCount = width * height
        
        // 計算各顏色通道的平均值
        for i in stride(from: 0, to: pixelData.count, by: 4) {
            redSum += Double(pixelData[i])
            greenSum += Double(pixelData[i+1])
            blueSum += Double(pixelData[i+2])
        }
        
        let redMean = redSum / Double(pixelCount)
        let greenMean = greenSum / Double(pixelCount)
        let blueMean = blueSum / Double(pixelCount)
        
        // 計算顏色通道間的平衡度
        let maxChannel = max(redMean, max(greenMean, blueMean))
        let minChannel = min(redMean, min(greenMean, blueMean))
        
        guard maxChannel > 0 else { return 0.0 }
        return minChannel / maxChannel
    }

}

// MARK: - 輔助結構
struct ImageHistogram {
    let red: [Int]
    let green: [Int] 
    let blue: [Int]
}

struct ColorGains {
    let red: Double
    let green: Double
    let blue: Double
}

struct DetectedPlane {
    let center: CGPoint
    let normal: SIMD3<Float>
    let confidence: Double
}

struct PerspectiveTransform {
    let topLeft: CGPoint
    let topRight: CGPoint
    let bottomLeft: CGPoint
    let bottomRight: CGPoint
}

// MARK: - PreProcessing Validation Methods (inline to avoid extension conflicts)
extension PreProcessingModule {
    private func validateImageForPreProcessing(_ image: UIImage) -> Bool {
        // 檢查基本屬性
        guard let cgImage = image.cgImage else {
            print("PreProcessing圖像驗證失敗: CGImage為空")
            return false
        }
        
        // PreProcessing需要更大的最小尺寸以進行ROI檢測
        let minWidth = 200.0
        let minHeight = 200.0
        
        guard image.size.width >= minWidth && image.size.height >= minHeight else {
            print("PreProcessing圖像驗證失敗: 尺寸過小 - \(image.size)，最小要求: \(minWidth)x\(minHeight)")
            return false
        }
        
        // 檢查異常的1x1像素情況
        if image.size.width <= 1.0 || image.size.height <= 1.0 {
            print("PreProcessing圖像驗證失敗: 檢測到1x1像素異常 - \(image.size)")
            return false
        }
        
        // 檢查CGImage尺寸
        guard cgImage.width > 0 && cgImage.height > 0 else {
            print("PreProcessing圖像驗證失敗: CGImage尺寸無效 - \(cgImage.width)x\(cgImage.height)")
            return false
        }
        
        return true
    }
    
    private func diagnoseImageIssues(_ image: UIImage) -> [String] {
        var issues: [String] = []
        
        // 檢查UIImage尺寸異常
        if image.size.width <= 1.0 || image.size.height <= 1.0 {
            issues.append("UIImage尺寸異常: \(image.size)")
        }
        
        // 檢查CGImage尺寸異常
        if let cgImage = image.cgImage {
            if cgImage.width <= 1 || cgImage.height <= 1 {
                issues.append("CGImage尺寸異常: \(cgImage.width)x\(cgImage.height)")
            }
        } else {
            issues.append("CGImage為空")
        }
        
        return issues
    }
    
    private func getDiagnosticInfo(_ image: UIImage) -> String {
        var info = ["PreProcessing圖像診斷:"]
        
        if let cgImage = image.cgImage {
            info.append("- UIImage尺寸: \(image.size)")
            info.append("- CGImage尺寸: \(cgImage.width)x\(cgImage.height)")
            info.append("- 像素總數: \(Int(image.size.width * image.size.height))")
        } else {
            info.append("- CGImage: 無效")
        }
        
        return info.joined(separator: "\n")
    }
}