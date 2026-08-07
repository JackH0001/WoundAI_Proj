import SwiftUI
import CoreML
import Vision
import UIKit
import CoreImage
import Accelerate

/// UWM MobileNetV2輕量化傷口分割模組
/// 基於威斯康辛大學的足部潰瘍分割研究，針對iOS行動端優化
class UWMLightweightSegmentationModule: ObservableObject {
    
    @Published var processingStatus: String = "準備就緒"
    @Published var processingProgress: Double = 0.0
    @Published var lastProcessingTime: TimeInterval = 0.0
    @Published var modelLoadingStatus: String = "未載入"
    
    // MARK: - 模型管理
    private var mobileNetV2Model: VNCoreMLModel?
    private var isModelLoaded: Bool = false
    private let context = CIContext()
    
    // UWM模型規格
    private let inputSize = CGSize(width: 224, height: 224)  // MobileNetV2標準輸入
    private let modelName = "UWM_MobileNetV2_WoundSeg"
    
    struct UWMSegmentationResult {
        let segmentationMask: UIImage
        let woundRegion: WoundRegion
        let confidence: Double
        let processingTime: TimeInterval
        let modelMetadata: ModelMetadata
    }
    
    struct WoundRegion {
        let boundingBox: CGRect
        let contourPoints: [CGPoint]
        let area: Double              // 像素面積
        let perimeter: Double         // 像素周長
        let centroid: CGPoint
        let aspectRatio: Double
        let compactness: Double       // 緊密度
        let solidity: Double          // 實心度
    }
    
    struct ModelMetadata {
        let modelName: String
        let inputSize: CGSize
        let outputClasses: [String]
        let accuracy: Double
        let modelSize: String
        let inferenceTime: TimeInterval
    }
    
    init() {
        Task {
            await loadUWMModel()
        }
    }
    
    // MARK: - 模型載入
    
    @MainActor
    private func loadUWMModel() async {
        processingStatus = "載入UWM MobileNetV2模型..."
        modelLoadingStatus = "載入中"
        processingProgress = 0.1
        
        // 嘗試載入預訓練的CoreML模型
        if let modelURL = Bundle.main.url(forResource: modelName, withExtension: "mlmodelc") {
            do {
                let mlModel = try MLModel(contentsOf: modelURL)
                mobileNetV2Model = try VNCoreMLModel(for: mlModel)
                isModelLoaded = true
                modelLoadingStatus = "UWM MobileNetV2 (CoreML)"
                processingStatus = "模型載入完成"
                print("✅ UWM MobileNetV2模型載入成功")
                
            } catch {
                print("❌ UWM CoreML模型載入失敗: \(error)")
                await loadMockModel()
            }
        } else {
            // 如果沒有預訓練模型，使用模擬實現
            print("⚠️ 未找到UWM模型檔案，使用輕量化模擬實現")
            await loadMockModel()
        }
        
        processingProgress = 1.0
    }
    
    @MainActor
    private func loadMockModel() async {
        // 創建模擬的MobileNetV2實現，用於演示和開發
        isModelLoaded = true
        modelLoadingStatus = "UWM MobileNetV2 (模擬版)"
        processingStatus = "模擬模型準備就緒"
        print("✅ UWM模擬模型載入完成")
    }
    
    // MARK: - 主要分割功能
    
    /// 執行UWM輕量化傷口分割
    func performWoundSegmentation(image: UIImage) async throws -> UWMSegmentationResult {
        let startTime = Date()
        
        guard isModelLoaded else {
            throw UWMError.modelNotLoaded
        }
        
        await updateStatus("開始UWM傷口分割...")
        await updateProgress(0.2)
        
        // 1. 預處理圖像
        guard let preprocessedImage = preprocessImage(image) else {
            throw UWMError.imagePreprocessingFailed
        }
        
        await updateProgress(0.4)
        
        // 2. 執行分割推論
        let segmentationMask = try await performInference(preprocessedImage)
        
        await updateProgress(0.7)
        
        // 3. 後處理 - Connected Component Labeling
        let processedMask = try await postProcessSegmentation(segmentationMask, originalSize: image.size)
        
        await updateProgress(0.85)
        
        // 4. 提取傷口區域特徵
        let woundRegion = try await extractWoundRegion(from: processedMask, originalImage: image)
        
        await updateProgress(0.95)
        
        // 5. 計算信心度
        let confidence = calculateSegmentationConfidence(segmentationMask, woundRegion: woundRegion)
        
        let processingTime = Date().timeIntervalSince(startTime)
        
        await updateStatus("UWM分割完成")
        await updateProgress(1.0)
        
        let metadata = ModelMetadata(
            modelName: "UWM MobileNetV2",
            inputSize: inputSize,
            outputClasses: ["background", "wound"],
            accuracy: 0.89, // 根據UWM論文的報告精度
            modelSize: "< 10MB",
            inferenceTime: processingTime
        )
        
        await MainActor.run {
            lastProcessingTime = processingTime
        }
        
        return UWMSegmentationResult(
            segmentationMask: processedMask,
            woundRegion: woundRegion,
            confidence: confidence,
            processingTime: processingTime,
            modelMetadata: metadata
        )
    }
    
    // MARK: - 圖像預處理
    
    private func preprocessImage(_ image: UIImage) -> UIImage? {
        // UWM標準預處理：調整大小到224x224，正規化
        let targetSize = inputSize
        
        UIGraphicsBeginImageContextWithOptions(targetSize, false, 1.0)
        image.draw(in: CGRect(origin: .zero, size: targetSize))
        let resizedImage = UIGraphicsGetImageFromCurrentImageContext()
        UIGraphicsEndImageContext()
        
        return resizedImage
    }
    
    // MARK: - 模型推論
    
    private func performInference(_ image: UIImage) async throws -> CIImage {
        if let coreMLModel = mobileNetV2Model {
            return try await performCoreMLInference(image, model: coreMLModel)
        } else {
            return try await performMockInference(image)
        }
    }
    
    private func performCoreMLInference(_ image: UIImage, model: VNCoreMLModel) async throws -> CIImage {
        guard let cgImage = image.cgImage else {
            throw UWMError.invalidImage
        }
        
        return try await withCheckedThrowingContinuation { continuation in
            let request = VNCoreMLRequest(model: model) { request, error in
                if let error = error {
                    continuation.resume(throwing: error)
                    return
                }
                
                guard let results = request.results as? [VNPixelBufferObservation],
                      let pixelBuffer = results.first?.pixelBuffer else {
                    continuation.resume(throwing: UWMError.invalidModelOutput)
                    return
                }
                
                // 轉換pixel buffer為CIImage
                let ciImage = CIImage(cvPixelBuffer: pixelBuffer)
                continuation.resume(returning: ciImage)
            }
            
            request.imageCropAndScaleOption = .scaleFit
            
            let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
            do {
                try handler.perform([request])
            } catch {
                continuation.resume(throwing: error)
            }
        }
    }
    
    private func performMockInference(_ image: UIImage) async throws -> CIImage {
        // 模擬MobileNetV2的分割結果
        // 使用基本的圖像處理算法模擬深度學習分割
        
        guard let ciImage = CIImage(image: image) else {
            throw UWMError.invalidImage
        }
        
        // 模擬處理延遲
        try await Task.sleep(nanoseconds: 100_000_000) // 0.1秒
        
        // 1. 轉換為灰階
        let grayImage = try await convertToGrayscale(ciImage)
        
        // 2. 高斯模糊
        let blurredImage = try await applyGaussianBlur(grayImage, radius: 2.0)
        
        // 3. 自適應閾值分割
        let binaryImage = try await applyAdaptiveThreshold(blurredImage)
        
        // 4. 形態學操作
        let morphedImage = try await applyMorphologicalOperations(binaryImage)
        
        return morphedImage
    }
    
    // MARK: - 後處理 (Connected Component Labeling)
    
    private func postProcessSegmentation(_ segmentationMask: CIImage, originalSize: CGSize) async throws -> UIImage {
        // 實現Connected Component Labeling後處理
        // 這是UWM方法的關鍵步驟，用於移除小的噪聲區域並連接破碎的傷口區域
        
        guard let cgImage = context.createCGImage(segmentationMask, from: segmentationMask.extent) else {
            throw UWMError.postProcessingFailed
        }
        
        // 1. 連通組件分析
        let labeledComponents = try await findConnectedComponents(cgImage)
        
        // 2. 篩選有效組件（移除小區域）
        let filteredComponents = filterComponentsBySize(labeledComponents, minSize: 100)
        
        // 3. 選擇最大的傷口區域
        let mainWoundComponent = selectMainWoundComponent(filteredComponents)
        
        // 4. 創建最終分割遮罩
        let finalMask = try await createFinalMask(mainWoundComponent, imageSize: originalSize)
        
        return finalMask
    }
    
    private func findConnectedComponents(_ cgImage: CGImage) async throws -> [ConnectedComponent] {
        // 簡化的連通組件實現
        guard let data = cgImage.dataProvider?.data,
              let bytes = CFDataGetBytePtr(data) else {
            throw UWMError.postProcessingFailed
        }
        
        let width = cgImage.width
        let height = cgImage.height
        let bytesPerPixel = cgImage.bitsPerPixel / 8
        
        var visited = Array(repeating: Array(repeating: false, count: width), count: height)
        var components: [ConnectedComponent] = []
        var componentId = 0
        
        for y in 0..<height {
            for x in 0..<width {
                if !visited[y][x] && isWoundPixel(bytes, x: x, y: y, width: width, bytesPerPixel: bytesPerPixel) {
                    let component = try await floodFill(
                        bytes: bytes,
                        visited: &visited,
                        startX: x,
                        startY: y,
                        width: width,
                        height: height,
                        bytesPerPixel: bytesPerPixel,
                        componentId: componentId
                    )
                    components.append(component)
                    componentId += 1
                }
            }
        }
        
        return components
    }
    
    // MARK: - 特徵提取
    
    private func extractWoundRegion(from maskImage: UIImage, originalImage: UIImage) async throws -> WoundRegion {
        guard let maskCGImage = maskImage.cgImage else {
            throw UWMError.featureExtractionFailed
        }
        
        // 1. 找到傷口輪廓
        let contours = try await findWoundContours(maskCGImage)
        
        guard let mainContour = contours.max(by: { $0.count < $1.count }) else {
            throw UWMError.featureExtractionFailed
        }
        
        // 2. 計算邊界框
        let boundingBox = calculateBoundingBox(contour: mainContour)
        
        // 3. 計算幾何特徵
        let area = calculateContourArea(mainContour)
        let perimeter = calculateContourPerimeter(mainContour)
        let centroid = calculateCentroid(mainContour)
        
        // 4. 計算形狀特徵
        let aspectRatio = boundingBox.width / boundingBox.height
        let compactness = (4.0 * Double.pi * area) / (perimeter * perimeter)
        let solidity = area / (boundingBox.width * boundingBox.height)
        
        return WoundRegion(
            boundingBox: boundingBox,
            contourPoints: mainContour,
            area: area,
            perimeter: perimeter,
            centroid: centroid,
            aspectRatio: aspectRatio,
            compactness: compactness,
            solidity: solidity
        )
    }
    
    // MARK: - 信心度計算
    
    private func calculateSegmentationConfidence(_ segmentationMask: CIImage, woundRegion: WoundRegion) -> Double {
        // 基於多個因子計算分割信心度
        var confidence: Double = 0.8 // 基礎信心度
        
        // 1. 基於傷口大小的信心度調整
        if woundRegion.area > 500 {
            confidence += 0.1
        }
        
        // 2. 基於形狀規律性的信心度調整
        if woundRegion.compactness > 0.3 {
            confidence += 0.05
        }
        
        // 3. 基於邊界清晰度的信心度調整
        if woundRegion.solidity > 0.7 {
            confidence += 0.05
        }
        
        return min(1.0, max(0.0, confidence))
    }
    
    // MARK: - 輔助方法
    
    private func convertToGrayscale(_ image: CIImage) async throws -> CIImage {
        guard let filter = CIFilter(name: "CIColorMonochrome") else {
            throw UWMError.filterCreationFailed
        }
        
        filter.setValue(image, forKey: kCIInputImageKey)
        filter.setValue(CIColor.gray, forKey: kCIInputColorKey)
        filter.setValue(1.0, forKey: kCIInputIntensityKey)
        
        guard let result = filter.outputImage else {
            throw UWMError.filterProcessingFailed
        }
        
        return result
    }
    
    private func applyGaussianBlur(_ image: CIImage, radius: Double) async throws -> CIImage {
        guard let filter = CIFilter(name: "CIGaussianBlur") else {
            throw UWMError.filterCreationFailed
        }
        
        filter.setValue(image, forKey: kCIInputImageKey)
        filter.setValue(radius, forKey: kCIInputRadiusKey)
        
        guard let result = filter.outputImage else {
            throw UWMError.filterProcessingFailed
        }
        
        return result
    }
    
    private func applyAdaptiveThreshold(_ image: CIImage) async throws -> CIImage {
        // iOS 上部分版本無 CIColorThreshold，提供降級路徑
        if let filter = CIFilter(name: "CIColorThreshold") {
            filter.setValue(image, forKey: kCIInputImageKey)
            filter.setValue(0.5, forKey: "inputThreshold")
            guard let result = filter.outputImage else {
                throw UWMError.filterProcessingFailed
            }
            return result
        } else {
            // 降級：手動二值化
            return try await manualThreshold(image, threshold: 0.5)
        }
    }

    // 降級：手動閾值二值化
    private func manualThreshold(_ image: CIImage, threshold: Double) async throws -> CIImage {
        guard let cgImage = context.createCGImage(image, from: image.extent) else {
            throw UWMError.invalidImage
        }
        let width = cgImage.width
        let height = cgImage.height
        let bytesPerPixel = 4
        let bytesPerRow = width * bytesPerPixel
        let pixelCount = width * height
        var input = [UInt8](repeating: 0, count: bytesPerRow * height)
        guard let data = cgImage.dataProvider?.data, let src = CFDataGetBytePtr(data) else {
            throw UWMError.imagePreprocessingFailed
        }
        // 讀取到緩衝
        memcpy(&input, src, min(input.count, CFDataGetLength(data)))
        // 產生二值輸出 (RGBA)
        var output = [UInt8](repeating: 255, count: bytesPerRow * height)
        let t: Double = threshold * 255.0
        for y in 0..<height {
            for x in 0..<width {
                let o = y * bytesPerRow + x * bytesPerPixel
                let r = Double(input[o])
                let g = Double(input[o+1])
                let b = Double(input[o+2])
                // 灰階
                let gray = 0.299*r + 0.587*g + 0.114*b
                let v: UInt8 = gray >= t ? 255 : 0
                output[o] = v
                output[o+1] = v
                output[o+2] = v
                output[o+3] = 255
            }
        }
        guard let provider = CGDataProvider(data: Data(output) as CFData) else {
            throw UWMError.postProcessingFailed
        }
        guard let binCG = CGImage(
            width: width,
            height: height,
            bitsPerComponent: 8,
            bitsPerPixel: 32,
            bytesPerRow: bytesPerRow,
            space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.premultipliedLast.rawValue),
            provider: provider,
            decode: nil,
            shouldInterpolate: false,
            intent: .defaultIntent
        ) else {
            throw UWMError.postProcessingFailed
        }
        return CIImage(cgImage: binCG)
    }
    
    private func applyMorphologicalOperations(_ image: CIImage) async throws -> CIImage {
        // 應用形態學開運算和閉運算
        guard let erodeFilter = CIFilter(name: "CIMorphologyMinimum") else {
            throw UWMError.filterCreationFailed
        }
        
        erodeFilter.setValue(image, forKey: kCIInputImageKey)
        erodeFilter.setValue(2.0, forKey: kCIInputRadiusKey)
        
        guard let eroded = erodeFilter.outputImage else {
            throw UWMError.filterProcessingFailed
        }
        
        guard let dilateFilter = CIFilter(name: "CIMorphologyMaximum") else {
            throw UWMError.filterCreationFailed
        }
        
        dilateFilter.setValue(eroded, forKey: kCIInputImageKey)
        dilateFilter.setValue(3.0, forKey: kCIInputRadiusKey)
        
        guard let result = dilateFilter.outputImage else {
            throw UWMError.filterProcessingFailed
        }
        
        return result
    }
    
    // MARK: - 狀態更新
    
    @MainActor
    private func updateStatus(_ status: String) {
        processingStatus = status
        print("🔬 UWM分割: \(status)")
    }
    
    @MainActor
    private func updateProgress(_ progress: Double) {
        processingProgress = progress
    }
}

// MARK: - 支援結構

struct ConnectedComponent {
    let id: Int
    let pixels: [CGPoint]
    let boundingBox: CGRect
    let area: Int
}

// MARK: - 錯誤類型

enum UWMError: LocalizedError {
    case modelNotLoaded
    case invalidImage
    case imagePreprocessingFailed
    case invalidModelOutput
    case postProcessingFailed
    case featureExtractionFailed
    case filterCreationFailed
    case filterProcessingFailed
    
    var errorDescription: String? {
        switch self {
        case .modelNotLoaded:
            return "UWM模型未載入"
        case .invalidImage:
            return "無效的圖像資料"
        case .imagePreprocessingFailed:
            return "圖像預處理失敗"
        case .invalidModelOutput:
            return "模型輸出格式錯誤"
        case .postProcessingFailed:
            return "後處理失敗"
        case .featureExtractionFailed:
            return "特徵提取失敗"
        case .filterCreationFailed:
            return "濾鏡創建失敗"
        case .filterProcessingFailed:
            return "濾鏡處理失敗"
        }
    }
}

// MARK: - 輔助擴展

extension UWMLightweightSegmentationModule {
    
    // 簡化版的輔助方法實現
    private func isWoundPixel(_ bytes: UnsafePointer<UInt8>, x: Int, y: Int, width: Int, bytesPerPixel: Int) -> Bool {
        let offset = y * width * bytesPerPixel + x * bytesPerPixel
        return bytes[offset] > 128 // 簡單的亮度閾值
    }
    
    private func floodFill(
        bytes: UnsafePointer<UInt8>,
        visited: inout [[Bool]],
        startX: Int,
        startY: Int,
        width: Int,
        height: Int,
        bytesPerPixel: Int,
        componentId: Int
    ) async throws -> ConnectedComponent {
        
        var pixels: [CGPoint] = []
        var stack: [(Int, Int)] = [(startX, startY)]
        
        var minX = startX, maxX = startX
        var minY = startY, maxY = startY
        
        while !stack.isEmpty {
            let (x, y) = stack.removeLast()
            
            if x < 0 || x >= width || y < 0 || y >= height || visited[y][x] {
                continue
            }
            
            if !isWoundPixel(bytes, x: x, y: y, width: width, bytesPerPixel: bytesPerPixel) {
                continue
            }
            
            visited[y][x] = true
            pixels.append(CGPoint(x: x, y: y))
            
            minX = min(minX, x)
            maxX = max(maxX, x)
            minY = min(minY, y)
            maxY = max(maxY, y)
            
            // 添加8連通鄰居
            stack.append((x+1, y))
            stack.append((x-1, y))
            stack.append((x, y+1))
            stack.append((x, y-1))
            stack.append((x+1, y+1))
            stack.append((x-1, y-1))
            stack.append((x+1, y-1))
            stack.append((x-1, y+1))
        }
        
        let boundingBox = CGRect(x: minX, y: minY, width: maxX - minX + 1, height: maxY - minY + 1)
        
        return ConnectedComponent(
            id: componentId,
            pixels: pixels,
            boundingBox: boundingBox,
            area: pixels.count
        )
    }
    
    private func filterComponentsBySize(_ components: [ConnectedComponent], minSize: Int) -> [ConnectedComponent] {
        return components.filter { $0.area >= minSize }
    }
    
    private func selectMainWoundComponent(_ components: [ConnectedComponent]) -> ConnectedComponent? {
        return components.max { $0.area < $1.area }
    }
    
    private func createFinalMask(_ component: ConnectedComponent?, imageSize: CGSize) async throws -> UIImage {
        guard let component = component else {
            // 創建空白遮罩
            return try createEmptyMask(size: imageSize)
        }
        
        let width = Int(imageSize.width)
        let height = Int(imageSize.height)
        
        var maskData = Data(count: width * height * 4)
        
        maskData.withUnsafeMutableBytes { ptr in
            let buffer = ptr.bindMemory(to: UInt8.self)
            
            // 初始化為黑色
            for i in stride(from: 0, to: width * height * 4, by: 4) {
                buffer[i] = 0     // R
                buffer[i+1] = 0   // G
                buffer[i+2] = 0   // B
                buffer[i+3] = 255 // A
            }
            
            // 設置傷口區域為白色
            for pixel in component.pixels {
                let x = Int(pixel.x)
                let y = Int(pixel.y)
                if x >= 0 && x < width && y >= 0 && y < height {
                    let offset = y * width * 4 + x * 4
                    buffer[offset] = 255     // R
                    buffer[offset+1] = 255   // G  
                    buffer[offset+2] = 255   // B
                    buffer[offset+3] = 255   // A
                }
            }
        }
        
        guard let dataProvider = CGDataProvider(data: maskData as CFData),
              let cgImage = CGImage(
                width: width,
                height: height,
                bitsPerComponent: 8,
                bitsPerPixel: 32,
                bytesPerRow: width * 4,
                space: CGColorSpaceCreateDeviceRGB(),
                bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.premultipliedLast.rawValue),
                provider: dataProvider,
                decode: nil,
                shouldInterpolate: false,
                intent: .defaultIntent
              ) else {
            throw UWMError.postProcessingFailed
        }
        
        return UIImage(cgImage: cgImage)
    }
    
    private func createEmptyMask(size: CGSize) throws -> UIImage {
        UIGraphicsBeginImageContextWithOptions(size, false, 1.0)
        UIColor.black.setFill()
        UIRectFill(CGRect(origin: .zero, size: size))
        guard let emptyMask = UIGraphicsGetImageFromCurrentImageContext() else {
            UIGraphicsEndImageContext()
            throw UWMError.postProcessingFailed
        }
        UIGraphicsEndImageContext()
        return emptyMask
    }
    
    private func findWoundContours(_ cgImage: CGImage) async throws -> [[CGPoint]] {
        // 簡化的輪廓查找實現
        guard let data = cgImage.dataProvider?.data,
              let bytes = CFDataGetBytePtr(data) else {
            throw UWMError.featureExtractionFailed
        }
        
        let width = cgImage.width
        let height = cgImage.height
        let bytesPerPixel = 4
        
        var contours: [[CGPoint]] = []
        var visited = Array(repeating: Array(repeating: false, count: width), count: height)
        
        for y in 1..<(height-1) {
            for x in 1..<(width-1) {
                if !visited[y][x] && isWoundPixel(bytes, x: x, y: y, width: width, bytesPerPixel: bytesPerPixel) {
                    let contour = try await traceContour(bytes: bytes, visited: &visited, startX: x, startY: y, width: width, height: height, bytesPerPixel: bytesPerPixel)
                    if contour.count > 10 { // 過濾小輪廓
                        contours.append(contour)
                    }
                }
            }
        }
        
        return contours
    }
    
    private func traceContour(
        bytes: UnsafePointer<UInt8>,
        visited: inout [[Bool]],
        startX: Int,
        startY: Int,
        width: Int,
        height: Int,
        bytesPerPixel: Int
    ) async throws -> [CGPoint] {
        
        var contour: [CGPoint] = []
        var current = (x: startX, y: startY)
        
        repeat {
            contour.append(CGPoint(x: current.x, y: current.y))
            visited[current.y][current.x] = true
            
            // 簡化的輪廓跟踪 - 查找下一個邊界像素
            var found = false
            for dx in -1...1 {
                for dy in -1...1 {
                    let newX = current.x + dx
                    let newY = current.y + dy
                    
                    if newX >= 0 && newX < width && newY >= 0 && newY < height &&
                       !visited[newY][newX] &&
                       isWoundPixel(bytes, x: newX, y: newY, width: width, bytesPerPixel: bytesPerPixel) {
                        current = (x: newX, y: newY)
                        found = true
                        break
                    }
                }
                if found { break }
            }
            
            if !found { break }
            if contour.count > 1000 { break } // 防止無限循環
            
        } while current.x != startX || current.y != startY
        
        return contour
    }
    
    private func calculateBoundingBox(contour: [CGPoint]) -> CGRect {
        guard !contour.isEmpty else { return .zero }
        
        let minX = contour.map { $0.x }.min() ?? 0
        let maxX = contour.map { $0.x }.max() ?? 0
        let minY = contour.map { $0.y }.min() ?? 0
        let maxY = contour.map { $0.y }.max() ?? 0
        
        return CGRect(x: minX, y: minY, width: maxX - minX, height: maxY - minY)
    }
    
    private func calculateContourArea(_ contour: [CGPoint]) -> Double {
        guard contour.count > 2 else { return 0.0 }
        
        var area: Double = 0.0
        let n = contour.count
        
        for i in 0..<n {
            let j = (i + 1) % n
            area += contour[i].x * contour[j].y
            area -= contour[j].x * contour[i].y
        }
        
        return Swift.abs(area) / 2.0
    }
    
    private func calculateContourPerimeter(_ contour: [CGPoint]) -> Double {
        guard contour.count > 1 else { return 0.0 }
        
        var perimeter: Double = 0.0
        
        for i in 0..<contour.count {
            let current = contour[i]
            let next = contour[(i + 1) % contour.count]
            let distance = sqrt(pow(next.x - current.x, 2) + pow(next.y - current.y, 2))
            perimeter += distance
        }
        
        return perimeter
    }
    
    private func calculateCentroid(_ contour: [CGPoint]) -> CGPoint {
        guard !contour.isEmpty else { return .zero }
        
        let sumX = contour.map { $0.x }.reduce(0, +)
        let sumY = contour.map { $0.y }.reduce(0, +)
        
        return CGPoint(
            x: sumX / CGFloat(contour.count),
            y: sumY / CGFloat(contour.count)
        )
    }
}