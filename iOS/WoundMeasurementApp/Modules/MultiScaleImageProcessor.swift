import SwiftUI
import CoreImage
import Accelerate
import Vision

/// 按照技術文件建議的多尺度影像金字塔處理模組
/// 實現不同解析度下的傷口分析，提高分割精度和魯棒性
class MultiScaleImageProcessor: ObservableObject {
    
    @Published var processingProgress: Double = 0.0
    @Published var currentScale: String = ""
    @Published var processedPyramid: ImagePyramid?
    
    private let context = CIContext()
    private let maxPyramidLevels = 5
    private let minImageSize: CGFloat = 64.0
    
    struct ImagePyramid {
        let levels: [PyramidLevel]
        let originalSize: CGSize
        let processingTime: TimeInterval
        
        struct PyramidLevel {
            let scale: Double           // 縮放比例 (1.0 = 原圖)
            let image: CIImage         // 該尺度的圖像
            let size: CGSize           // 圖像尺寸
            let features: ImageFeatures? // 提取的特徵
            let segmentation: SegmentationResult? // 分割結果
        }
    }
    
    struct ImageFeatures {
        let edgeMap: CIImage          // 邊緣圖
        let textureMap: CIImage       // 紋理圖  
        let colorFeatures: ColorFeatureMap
        let gradientMagnitude: CIImage // 梯度強度
        let cornerPoints: [CGPoint]    // 角點
    }
    
    struct ColorFeatureMap {
        let hue: CIImage              // 色相
        let saturation: CIImage       // 飽和度
        let brightness: CIImage       // 亮度
        let redness: CIImage          // 紅色特徵 (傷口檢測關鍵)
        let brownness: CIImage        // 棕色特徵 (壞死組織)
        let pinkness: CIImage         // 粉色特徵 (癒合組織)
    }
    
    struct SegmentationResult {
        let woundMask: CIImage        // 傷口遮罩
        let confidenceMap: CIImage    // 置信度圖
        let contours: [WoundContour]  // 輪廓
        let tissueTypes: TissueTypeMap // 組織類型分佈
    }
    
    struct TissueTypeMap {
        let granulation: CIImage      // 肉芽組織
        let necrotic: CIImage         // 壞死組織  
        let epithelial: CIImage       // 上皮組織
        let fibrin: CIImage           // 纖維組織
        let healthy: CIImage          // 健康組織
    }
    
    /// 按照技術文件建議建立多尺度影像金字塔
    func buildImagePyramid(from image: UIImage) async throws -> ImagePyramid {
        let startTime = Date()
        
        guard let cgImage = image.cgImage else {
            throw ProcessingError.invalidImage
        }
        
        let originalCIImage = CIImage(cgImage: cgImage)
        let originalSize = originalCIImage.extent.size
        
        await updateProgress(0.0, scale: "開始建立影像金字塔")
        
        var levels: [ImagePyramid.PyramidLevel] = []
        var currentImage = originalCIImage
        var currentScale: Double = 1.0
        let scaleStep: Double = 0.7071 // √2 的倒數，常用於影像金字塔
        
        // 建立多尺度層級
        for levelIndex in 0..<maxPyramidLevels {
            let levelSize = CGSize(
                width: originalSize.width * currentScale,
                height: originalSize.height * currentScale
            )
            
            // 檢查最小尺寸限制
            guard levelSize.width >= minImageSize && levelSize.height >= minImageSize else {
                print("到達最小影像尺寸限制: \(levelSize)")
                break
            }
            
            await updateProgress(Double(levelIndex) / Double(maxPyramidLevels), 
                               scale: "處理尺度 \(String(format: "%.2f", currentScale))")
            
            // 縮放圖像
            let scaledImage = try await scaleImage(currentImage, to: levelSize)
            
            // 提取特徵
            let features = try await extractImageFeatures(scaledImage)
            
            // 執行分割 (僅在較高解析度層級執行以節省計算)
            let segmentation = (levelIndex <= 2) ? 
                               try await performSegmentation(scaledImage, features: features) : nil
            
            let level = ImagePyramid.PyramidLevel(
                scale: currentScale,
                image: scaledImage,
                size: levelSize,
                features: features,
                segmentation: segmentation
            )
            
            levels.append(level)
            
            // 為下一層級準備
            currentImage = scaledImage
            currentScale *= scaleStep
            
            print("完成金字塔層級 \(levelIndex): 尺度=\(String(format: "%.3f", level.scale)), 尺寸=\(level.size)")
        }
        
        let pyramid = ImagePyramid(
            levels: levels,
            originalSize: originalSize,
            processingTime: Date().timeIntervalSince(startTime)
        )
        
        await MainActor.run {
            self.processedPyramid = pyramid
        }
        
        await updateProgress(1.0, scale: "影像金字塔建立完成")
        
        print("影像金字塔建立完成: \(levels.count)層, 處理時間: \(String(format: "%.2f", pyramid.processingTime))s")
        
        return pyramid
    }
    
    /// 多尺度傷口檢測與融合
    func detectWoundMultiScale(pyramid: ImagePyramid) async throws -> MultiScaleWoundResult {
        await updateProgress(0.0, scale: "多尺度傷口檢測")
        
        var scaleResults: [ScaleWoundResult] = []
        
        // 在不同尺度下檢測傷口
        for (index, level) in pyramid.levels.enumerated() {
            guard let segmentation = level.segmentation else { continue }
            
            await updateProgress(Double(index) / Double(pyramid.levels.count), 
                               scale: "檢測尺度 \(String(format: "%.2f", level.scale))")
            
            let result = try await analyzeWoundAtScale(level, segmentation: segmentation)
            scaleResults.append(result)
        }
        
        // 融合多尺度結果
        await updateProgress(0.8, scale: "融合多尺度結果")
        let fusedResult = try await fuseMultiScaleResults(scaleResults, originalSize: pyramid.originalSize)
        
        await updateProgress(1.0, scale: "多尺度檢測完成")
        
        return fusedResult
    }
    
    // MARK: - 私有處理方法
    
    private func scaleImage(_ image: CIImage, to size: CGSize) async throws -> CIImage {
        let scaleX = size.width / image.extent.width
        let scaleY = size.height / image.extent.height
        
        let transform = CGAffineTransform(scaleX: scaleX, y: scaleY)
        let scaledImage = image.transformed(by: transform)
        
        return scaledImage
    }
    
    private func extractImageFeatures(_ image: CIImage) async throws -> ImageFeatures {
        // 1. 邊緣檢測 - Canny算法
        let edgeMap = try await applyCanny(image)
        
        // 2. 紋理分析 - 局部二元模式 (LBP)
        let textureMap = try await calculateTexture(image)
        
        // 3. 色彩特徵提取
        let colorFeatures = try await extractColorFeatures(image)
        
        // 4. 梯度計算
        let gradientMagnitude = try await calculateGradientMagnitude(image)
        
        // 5. 角點檢測 - Harris角點
        let cornerPoints = try await detectCornerPoints(image)
        
        return ImageFeatures(
            edgeMap: edgeMap,
            textureMap: textureMap,
            colorFeatures: colorFeatures,
            gradientMagnitude: gradientMagnitude,
            cornerPoints: cornerPoints
        )
    }
    
    private func applyCanny(_ image: CIImage) async throws -> CIImage {
        // 1. 高斯模糊
        guard let gaussianFilter = CIFilter(name: "CIGaussianBlur") else {
            throw ProcessingError.filterCreationFailed
        }
        gaussianFilter.setValue(image, forKey: kCIInputImageKey)
        gaussianFilter.setValue(1.0, forKey: kCIInputRadiusKey)
        
        guard let blurred = gaussianFilter.outputImage else {
            throw ProcessingError.filterProcessingFailed
        }
        
        // 2. 梯度計算 (Sobel算子)
        let sobelX = try await applySobelX(blurred)
        let sobelY = try await applySobelY(blurred)
        
        // 3. 梯度強度
        let gradientMagnitude = try await combineGradients(sobelX, sobelY)
        
        // 4. 非極大值抑制 (簡化實現)
        let suppressedImage = try await nonMaximumSuppression(gradientMagnitude)
        
        return suppressedImage
    }
    
    private func applySobelX(_ image: CIImage) async throws -> CIImage {
        // Sobel X 算子: [-1 0 1; -2 0 2; -1 0 1]
        let kernel = CIVector(values: [-1, 0, 1, -2, 0, 2, -1, 0, 1], count: 9)
        
        guard let filter = CIFilter(name: "CIConvolution3X3") else {
            throw ProcessingError.filterCreationFailed
        }
        
        filter.setValue(image, forKey: kCIInputImageKey)
        filter.setValue(kernel, forKey: "inputWeights")
        
        guard let output = filter.outputImage else {
            throw ProcessingError.filterProcessingFailed
        }
        
        return output
    }
    
    private func applySobelY(_ image: CIImage) async throws -> CIImage {
        // Sobel Y 算子: [-1 -2 -1; 0 0 0; 1 2 1]
        let kernel = CIVector(values: [-1, -2, -1, 0, 0, 0, 1, 2, 1], count: 9)
        
        guard let filter = CIFilter(name: "CIConvolution3X3") else {
            throw ProcessingError.filterCreationFailed
        }
        
        filter.setValue(image, forKey: kCIInputImageKey)
        filter.setValue(kernel, forKey: "inputWeights")
        
        guard let output = filter.outputImage else {
            throw ProcessingError.filterProcessingFailed
        }
        
        return output
    }
    
    private func combineGradients(_ sobelX: CIImage, _ sobelY: CIImage) async throws -> CIImage {
        // 梯度強度 = sqrt(Gx² + Gy²)
        
        // X方向梯度平方
        guard let squareXFilter = CIFilter(name: "CIMultiplyCompositing") else {
            throw ProcessingError.filterCreationFailed
        }
        squareXFilter.setValue(sobelX, forKey: kCIInputImageKey)
        squareXFilter.setValue(sobelX, forKey: kCIInputBackgroundImageKey)
        
        guard let squaredX = squareXFilter.outputImage else {
            throw ProcessingError.filterProcessingFailed
        }
        
        // Y方向梯度平方
        guard let squareYFilter = CIFilter(name: "CIMultiplyCompositing") else {
            throw ProcessingError.filterCreationFailed
        }
        squareYFilter.setValue(sobelY, forKey: kCIInputImageKey)
        squareYFilter.setValue(sobelY, forKey: kCIInputBackgroundImageKey)
        
        guard let squaredY = squareYFilter.outputImage else {
            throw ProcessingError.filterProcessingFailed
        }
        
        // 相加
        guard let addFilter = CIFilter(name: "CIAdditionCompositing") else {
            throw ProcessingError.filterCreationFailed
        }
        addFilter.setValue(squaredX, forKey: kCIInputImageKey)
        addFilter.setValue(squaredY, forKey: kCIInputBackgroundImageKey)
        
        guard let sum = addFilter.outputImage else {
            throw ProcessingError.filterProcessingFailed
        }
        
        // 開方 (近似)
        guard let sqrtFilter = CIFilter(name: "CIGammaAdjust") else {
            throw ProcessingError.filterCreationFailed
        }
        sqrtFilter.setValue(sum, forKey: kCIInputImageKey)
        sqrtFilter.setValue(0.5, forKey: "inputPower") // 相當於開方
        
        guard let magnitude = sqrtFilter.outputImage else {
            throw ProcessingError.filterProcessingFailed
        }
        
        return magnitude
    }
    
    private func nonMaximumSuppression(_ gradientMagnitude: CIImage) async throws -> CIImage {
        // 簡化的非極大值抑制實現
        // 實際應用中需要考慮梯度方向
        
        let morphologyFilter = CIFilter(name: "CIMorphologyMaximum")
        morphologyFilter?.setValue(gradientMagnitude, forKey: kCIInputImageKey)
        morphologyFilter?.setValue(1.0, forKey: kCIInputRadiusKey)
        
        return morphologyFilter?.outputImage ?? gradientMagnitude
    }
    
    private func calculateTexture(_ image: CIImage) async throws -> CIImage {
        // 簡化的紋理計算 - 使用標準差濾波
        let mean = try await calculateLocalMean(image)
        let variance = try await calculateLocalVariance(image, mean: mean)
        
        return variance
    }
    
    private func calculateLocalMean(_ image: CIImage) async throws -> CIImage {
        // 使用均值濾波計算局部均值
        let kernel = CIVector(values: Array(repeating: 1.0/9.0, count: 9), count: 9)
        
        guard let filter = CIFilter(name: "CIConvolution3X3") else {
            throw ProcessingError.filterCreationFailed
        }
        
        filter.setValue(image, forKey: kCIInputImageKey)
        filter.setValue(kernel, forKey: "inputWeights")
        
        guard let output = filter.outputImage else {
            throw ProcessingError.filterProcessingFailed
        }
        
        return output
    }
    
    private func calculateLocalVariance(_ image: CIImage, mean: CIImage) async throws -> CIImage {
        // variance = E[(I - mean)²]
        
        // 計算差值
        guard let subtractFilter = CIFilter(name: "CISubtractBlendMode") else {
            throw ProcessingError.filterCreationFailed
        }
        subtractFilter.setValue(image, forKey: kCIInputImageKey)
        subtractFilter.setValue(mean, forKey: kCIInputBackgroundImageKey)
        
        guard let difference = subtractFilter.outputImage else {
            throw ProcessingError.filterProcessingFailed
        }
        
        // 平方
        guard let squareFilter = CIFilter(name: "CIMultiplyCompositing") else {
            throw ProcessingError.filterCreationFailed
        }
        squareFilter.setValue(difference, forKey: kCIInputImageKey)
        squareFilter.setValue(difference, forKey: kCIInputBackgroundImageKey)
        
        guard let squared = squareFilter.outputImage else {
            throw ProcessingError.filterProcessingFailed
        }
        
        // 局部均值
        return try await calculateLocalMean(squared)
    }
    
    private func extractColorFeatures(_ image: CIImage) async throws -> ColorFeatureMap {
        // 1. 轉換到HSV色彩空間
        let hsvImage = try await convertToHSV(image)
        
        // 2. 分離HSV通道
        let (hue, saturation, brightness) = try await separateHSVChannels(hsvImage)
        
        // 3. 計算特定色彩特徵
        let redness = try await calculateRedness(image)
        let brownness = try await calculateBrownness(image)  
        let pinkness = try await calculatePinkness(image)
        
        return ColorFeatureMap(
            hue: hue,
            saturation: saturation,
            brightness: brightness,
            redness: redness,
            brownness: brownness,
            pinkness: pinkness
        )
    }
    
    private func convertToHSV(_ image: CIImage) async throws -> CIImage {
        // 使用Core Image的色彩空間轉換
        guard let filter = CIFilter(name: "CIHueAdjust") else {
            throw ProcessingError.filterCreationFailed
        }
        filter.setValue(image, forKey: kCIInputImageKey)
        filter.setValue(0.0, forKey: kCIInputAngleKey)
        
        return filter.outputImage ?? image
    }
    
    private func separateHSVChannels(_ hsvImage: CIImage) async throws -> (CIImage, CIImage, CIImage) {
        // 使用色彩矩陣分離HSV通道
        
        // 色相通道
        let hueFilter = CIFilter(name: "CIColorMatrix")
        hueFilter?.setValue(hsvImage, forKey: kCIInputImageKey)
        hueFilter?.setValue(CIVector(x: 1, y: 0, z: 0, w: 0), forKey: "inputRVector")
        hueFilter?.setValue(CIVector(x: 0, y: 0, z: 0, w: 0), forKey: "inputGVector")
        hueFilter?.setValue(CIVector(x: 0, y: 0, z: 0, w: 0), forKey: "inputBVector")
        let hue = hueFilter?.outputImage ?? hsvImage
        
        // 飽和度通道
        let saturationFilter = CIFilter(name: "CIColorMatrix")
        saturationFilter?.setValue(hsvImage, forKey: kCIInputImageKey)
        saturationFilter?.setValue(CIVector(x: 0, y: 0, z: 0, w: 0), forKey: "inputRVector")
        saturationFilter?.setValue(CIVector(x: 0, y: 1, z: 0, w: 0), forKey: "inputGVector")
        saturationFilter?.setValue(CIVector(x: 0, y: 0, z: 0, w: 0), forKey: "inputBVector")
        let saturation = saturationFilter?.outputImage ?? hsvImage
        
        // 亮度通道
        let brightnessFilter = CIFilter(name: "CIColorMatrix")
        brightnessFilter?.setValue(hsvImage, forKey: kCIInputImageKey)
        brightnessFilter?.setValue(CIVector(x: 0, y: 0, z: 0, w: 0), forKey: "inputRVector")
        brightnessFilter?.setValue(CIVector(x: 0, y: 0, z: 0, w: 0), forKey: "inputGVector")
        brightnessFilter?.setValue(CIVector(x: 0, y: 0, z: 1, w: 0), forKey: "inputBVector")
        let brightness = brightnessFilter?.outputImage ?? hsvImage
        
        return (hue, saturation, brightness)
    }
    
    private func calculateRedness(_ image: CIImage) async throws -> CIImage {
        // 紅色特徵 = max(0, R - max(G, B))
        let redFilter = CIFilter(name: "CIColorMatrix")
        redFilter?.setValue(image, forKey: kCIInputImageKey)
        redFilter?.setValue(CIVector(x: 1, y: -0.5, z: -0.5, w: 0), forKey: "inputRVector")
        redFilter?.setValue(CIVector(x: 0, y: 0, z: 0, w: 0), forKey: "inputGVector")
        redFilter?.setValue(CIVector(x: 0, y: 0, z: 0, w: 0), forKey: "inputBVector")
        
        return redFilter?.outputImage ?? image
    }
    
    private func calculateBrownness(_ image: CIImage) async throws -> CIImage {
        // 棕色特徵 = R*0.6 + G*0.3 + B*0.1 (壞死組織特徵)
        let brownFilter = CIFilter(name: "CIColorMatrix")
        brownFilter?.setValue(image, forKey: kCIInputImageKey)
        brownFilter?.setValue(CIVector(x: 0.6, y: 0.3, z: 0.1, w: 0), forKey: "inputRVector")
        brownFilter?.setValue(CIVector(x: 0, y: 0, z: 0, w: 0), forKey: "inputGVector")
        brownFilter?.setValue(CIVector(x: 0, y: 0, z: 0, w: 0), forKey: "inputBVector")
        
        return brownFilter?.outputImage ?? image
    }
    
    private func calculatePinkness(_ image: CIImage) async throws -> CIImage {
        // 粉色特徵 = (R + B)*0.5 - G (癒合組織特徵)
        let pinkFilter = CIFilter(name: "CIColorMatrix")
        pinkFilter?.setValue(image, forKey: kCIInputImageKey)
        pinkFilter?.setValue(CIVector(x: 0.5, y: -1, z: 0.5, w: 0), forKey: "inputRVector")
        pinkFilter?.setValue(CIVector(x: 0, y: 0, z: 0, w: 0), forKey: "inputGVector")
        pinkFilter?.setValue(CIVector(x: 0, y: 0, z: 0, w: 0), forKey: "inputBVector")
        
        return pinkFilter?.outputImage ?? image
    }
    
    private func calculateGradientMagnitude(_ image: CIImage) async throws -> CIImage {
        let sobelX = try await applySobelX(image)
        let sobelY = try await applySobelY(image)
        return try await combineGradients(sobelX, sobelY)
    }
    
    private func detectCornerPoints(_ image: CIImage) async throws -> [CGPoint] {
        return try await withCheckedThrowingContinuation { continuation in
            // 使用Vision框架檢測角點
            let request = VNDetectRectanglesRequest { request, error in
                if let error = error {
                    continuation.resume(throwing: error)
                    return
                }
                
                var corners: [CGPoint] = []
                if let results = request.results as? [VNRectangleObservation] {
                    for rectangle in results {
                        corners.append(rectangle.topLeft)
                        corners.append(rectangle.topRight)
                        corners.append(rectangle.bottomLeft)
                        corners.append(rectangle.bottomRight)
                    }
                }
                
                continuation.resume(returning: corners)
            }
            
            guard let cgImage = context.createCGImage(image, from: image.extent) else {
                continuation.resume(throwing: ProcessingError.imageConversionFailed)
                return
            }
            
            let handler = VNImageRequestHandler(cgImage: cgImage)
            do {
                try handler.perform([request])
            } catch {
                continuation.resume(throwing: error)
            }
        }
    }
    
    private func performSegmentation(_ image: CIImage, features: ImageFeatures) async throws -> SegmentationResult {
        // 基於多特徵的傷口分割
        
        // 1. 結合邊緣和顏色特徵
        let woundProbability = try await combineFeatures(features)
        
        // 2. 閾值化生成遮罩
        let woundMask = try await thresholdImage(woundProbability, threshold: 0.5)
        
        // 3. 形態學處理
        let cleanedMask = try await morphologicalOperations(woundMask)
        
        // 4. 生成置信度圖
        let confidenceMap = woundProbability // 簡化：直接使用概率作為置信度
        
        // 5. 輪廓提取
        let contours = try await extractContours(cleanedMask)
        
        // 6. 組織類型分類
        let tissueTypes = try await classifyTissueTypes(image, mask: cleanedMask, features: features)
        
        return SegmentationResult(
            woundMask: cleanedMask,
            confidenceMap: confidenceMap,
            contours: contours,
            tissueTypes: tissueTypes
        )
    }
    
    private func combineFeatures(_ features: ImageFeatures) async throws -> CIImage {
        // 加權組合多種特徵
        let rednessWeight: Float = 0.4
        let edgeWeight: Float = 0.3
        let textureWeight: Float = 0.3
        
        // 正規化特徵圖
        let normalizedRedness = try await normalizeImage(features.colorFeatures.redness)
        let normalizedEdges = try await normalizeImage(features.edgeMap)
        let normalizedTexture = try await normalizeImage(features.textureMap)
        
        // 加權相加
        guard let combineFilter1 = CIFilter(name: "CIAdditionCompositing") else {
            throw ProcessingError.filterCreationFailed
        }
        
        // 先組合紅色特徵和邊緣
        let weightedRedness = try await multiplyImage(normalizedRedness, by: rednessWeight)
        let weightedEdges = try await multiplyImage(normalizedEdges, by: edgeWeight)
        
        combineFilter1.setValue(weightedRedness, forKey: kCIInputImageKey)
        combineFilter1.setValue(weightedEdges, forKey: kCIInputBackgroundImageKey)
        
        guard let intermediate = combineFilter1.outputImage else {
            throw ProcessingError.filterProcessingFailed
        }
        
        // 再加入紋理特徵
        guard let combineFilter2 = CIFilter(name: "CIAdditionCompositing") else {
            throw ProcessingError.filterCreationFailed
        }
        
        let weightedTexture = try await multiplyImage(normalizedTexture, by: textureWeight)
        
        combineFilter2.setValue(intermediate, forKey: kCIInputImageKey)
        combineFilter2.setValue(weightedTexture, forKey: kCIInputBackgroundImageKey)
        
        guard let combined = combineFilter2.outputImage else {
            throw ProcessingError.filterProcessingFailed
        }
        
        return combined
    }
    
    private func normalizeImage(_ image: CIImage) async throws -> CIImage {
        // 將圖像值正規化到 0-1 範圍
        guard let filter = CIFilter(name: "CIExposureAdjust") else {
            throw ProcessingError.filterCreationFailed
        }
        filter.setValue(image, forKey: kCIInputImageKey)
        filter.setValue(0.0, forKey: kCIInputEVKey)
        
        return filter.outputImage ?? image
    }
    
    private func multiplyImage(_ image: CIImage, by factor: Float) async throws -> CIImage {
        guard let filter = CIFilter(name: "CIColorMatrix") else {
            throw ProcessingError.filterCreationFailed
        }
        filter.setValue(image, forKey: kCIInputImageKey)
        filter.setValue(CIVector(x: CGFloat(factor), y: 0, z: 0, w: 0), forKey: "inputRVector")
        filter.setValue(CIVector(x: 0, y: CGFloat(factor), z: 0, w: 0), forKey: "inputGVector")  
        filter.setValue(CIVector(x: 0, y: 0, z: CGFloat(factor), w: 0), forKey: "inputBVector")
        
        return filter.outputImage ?? image
    }
    
    private func thresholdImage(_ image: CIImage, threshold: Double) async throws -> CIImage {
        // 二值化處理
        guard let filter = CIFilter(name: "CIColorThreshold") else {
            throw ProcessingError.filterCreationFailed
        }
        filter.setValue(image, forKey: kCIInputImageKey)
        filter.setValue(Float(threshold), forKey: "inputThreshold")
        
        return filter.outputImage ?? image
    }
    
    private func morphologicalOperations(_ image: CIImage) async throws -> CIImage {
        // 形態學開運算和閉運算
        
        // 1. 侵蝕
        guard let erodeFilter = CIFilter(name: "CIMorphologyMinimum") else {
            throw ProcessingError.filterCreationFailed
        }
        erodeFilter.setValue(image, forKey: kCIInputImageKey)
        erodeFilter.setValue(2.0, forKey: kCIInputRadiusKey)
        
        guard let eroded = erodeFilter.outputImage else {
            throw ProcessingError.filterProcessingFailed
        }
        
        // 2. 膨脹
        guard let dilateFilter = CIFilter(name: "CIMorphologyMaximum") else {
            throw ProcessingError.filterCreationFailed
        }
        dilateFilter.setValue(eroded, forKey: kCIInputImageKey)
        dilateFilter.setValue(3.0, forKey: kCIInputRadiusKey)
        
        return dilateFilter.outputImage ?? image
    }
    
    private func extractContours(_ maskImage: CIImage) async throws -> [WoundContour] {
        // 輪廓提取（簡化實現）
        guard let cgImage = context.createCGImage(maskImage, from: maskImage.extent) else {
            throw ProcessingError.imageConversionFailed
        }
        
        // 這裡應該實現真正的輪廓跟蹤算法，如 Suzuki-Abe 算法
        // 目前返回空陣列作為佔位符
        return []
    }
    
    private func classifyTissueTypes(_ image: CIImage, mask: CIImage, features: ImageFeatures) async throws -> TissueTypeMap {
        // 基於顏色特徵分類組織類型
        
        // 肉芽組織 (紅色)
        let granulation = features.colorFeatures.redness
        
        // 壞死組織 (棕色/黑色)
        let necrotic = features.colorFeatures.brownness
        
        // 上皮組織 (粉色)
        let epithelial = features.colorFeatures.pinkness
        
        // 纖維組織 (黃色/白色)
        let fibrin = try await calculateFibrinFeature(image)
        
        // 健康組織 (正常膚色)
        let healthy = try await calculateHealthyTissueFeature(image)
        
        return TissueTypeMap(
            granulation: granulation,
            necrotic: necrotic,
            epithelial: epithelial,
            fibrin: fibrin,
            healthy: healthy
        )
    }
    
    private func calculateFibrinFeature(_ image: CIImage) async throws -> CIImage {
        // 纖維組織特徵 (黃白色)
        let fibrinFilter = CIFilter(name: "CIColorMatrix")
        fibrinFilter?.setValue(image, forKey: kCIInputImageKey)
        fibrinFilter?.setValue(CIVector(x: 0.7, y: 0.7, z: 0.1, w: 0), forKey: "inputRVector")
        
        return fibrinFilter?.outputImage ?? image
    }
    
    private func calculateHealthyTissueFeature(_ image: CIImage) async throws -> CIImage {
        // 健康組織特徵 (正常膚色)
        let healthyFilter = CIFilter(name: "CIColorMatrix") 
        healthyFilter?.setValue(image, forKey: kCIInputImageKey)
        healthyFilter?.setValue(CIVector(x: 0.6, y: 0.4, z: 0.2, w: 0), forKey: "inputRVector")
        
        return healthyFilter?.outputImage ?? image
    }
    
    // MARK: - 多尺度結果融合
    
    private func analyzeWoundAtScale(_ level: ImagePyramid.PyramidLevel, segmentation: SegmentationResult) async throws -> ScaleWoundResult {
        // 在特定尺度下分析傷口
        
        let measurements = calculateMeasurements(from: segmentation, scale: level.scale)
        let confidence = calculateScaleConfidence(level: level, segmentation: segmentation)
        
        return ScaleWoundResult(
            scale: level.scale,
            measurements: measurements,
            confidence: confidence,
            contours: segmentation.contours,
            tissueComposition: segmentation.tissueTypes
        )
    }
    
    private func fuseMultiScaleResults(_ results: [ScaleWoundResult], originalSize: CGSize) async throws -> MultiScaleWoundResult {
        // 融合多尺度結果
        
        // 1. 按信心度加權平均測量結果
        let weightedMeasurements = calculateWeightedMeasurements(results)
        
        // 2. 融合輪廓 (選擇最高解析度的結果)
        let fusedContours = results.first?.contours ?? []
        
        // 3. 計算整體置信度
        let overallConfidence = results.map { $0.confidence * $0.scale }.reduce(0, +) / results.map { $0.scale }.reduce(0, +)
        
        return MultiScaleWoundResult(
            measurements: weightedMeasurements,
            contours: fusedContours,
            overallConfidence: overallConfidence,
            scaleResults: results,
            processingMethod: .multiScale
        )
    }
    
    // MARK: - 輔助方法
    
    private func updateProgress(_ progress: Double, scale: String) async {
        await MainActor.run {
            self.processingProgress = progress
            self.currentScale = scale
        }
    }
    
    private func calculateMeasurements(from segmentation: SegmentationResult, scale: Double) -> WoundMeasurements {
        // 計算該尺度下的測量結果
        let contourArea = segmentation.contours.map { $0.area }.reduce(0, +)
        let scaledArea = contourArea / (scale * scale) // 還原到原始尺度
        
        return WoundMeasurements(
            area: scaledArea,
            perimeter: 0.0, // 簡化
            aspectRatio: 1.0 // 簡化
        )
    }
    
    private func calculateScaleConfidence(level: ImagePyramid.PyramidLevel, segmentation: SegmentationResult) -> Double {
        // 計算該尺度下的分割信心度
        let sizeScore = min(level.size.width, level.size.height) / 256.0 // 尺度評分
        let contourScore = Double(segmentation.contours.count > 0 ? 1 : 0) // 是否找到輪廓
        
        return min(1.0, (sizeScore + contourScore) / 2.0)
    }
    
    private func calculateWeightedMeasurements(_ results: [ScaleWoundResult]) -> WoundMeasurements {
        let totalWeight = results.map { $0.confidence }.reduce(0, +)
        
        guard totalWeight > 0 else {
            return WoundMeasurements(area: 0, perimeter: 0, aspectRatio: 1)
        }
        
        let weightedArea = results.map { $0.measurements.area * $0.confidence }.reduce(0, +) / totalWeight
        let weightedPerimeter = results.map { $0.measurements.perimeter * $0.confidence }.reduce(0, +) / totalWeight
        let weightedAspectRatio = results.map { $0.measurements.aspectRatio * $0.confidence }.reduce(0, +) / totalWeight
        
        return WoundMeasurements(
            area: weightedArea,
            perimeter: weightedPerimeter,
            aspectRatio: weightedAspectRatio
        )
    }
}

// MARK: - 結果結構

struct MultiScaleWoundResult {
    let measurements: WoundMeasurements
    let contours: [WoundContour]
    let overallConfidence: Double
    let scaleResults: [ScaleWoundResult]
    let processingMethod: ProcessingMethod
    
    enum ProcessingMethod {
        case singleScale
        case multiScale
    }
}

struct ScaleWoundResult {
    let scale: Double
    let measurements: WoundMeasurements
    let confidence: Double
    let contours: [WoundContour]
    let tissueComposition: TissueTypeMap
}

struct WoundMeasurements {
    let area: Double
    let perimeter: Double
    let aspectRatio: Double
}

enum ProcessingError: Error {
    case invalidImage
    case filterCreationFailed
    case filterProcessingFailed
    case imageConversionFailed
    case segmentationFailed
}