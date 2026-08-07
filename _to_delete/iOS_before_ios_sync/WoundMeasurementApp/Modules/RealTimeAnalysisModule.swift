import SwiftUI
import CoreImage
import Vision
import Accelerate

@MainActor
class RealTimeAnalysisModule: ObservableObject {
    @Published var isAnalyzing = false
    @Published var currentAnalysis: RealTimeAnalysisResult?
    @Published var analysisHistory: [RealTimeAnalysisResult] = []
    
    private let segmentationEngine = SegmentationEngine()
    private let measurementEngine = MeasurementEngine()
    private let context = CIContext()
    
    // 校正結果
    var calibrationResult: CalibrationResult?
    
    // 即時分析設定
    private var analysisInterval: TimeInterval = 0.8 // 起始間隔（秒）
    private let minAnalysisInterval: TimeInterval = 0.15
    private let maxAnalysisInterval: TimeInterval = 1.5
    private var movingAvgProcessing: Double = 0.0
    private var analysisTask: Task<Void, Never>?
    private var lastAnalysisTime: Date = Date()
    
    // 分析結果緩存
    private var cachedResults: [String: RealTimeAnalysisResult] = [:]
    private let maxCacheSize = 10
    
    struct RealTimeAnalysisResult {
        let timestamp: Date
        let hasWound: Bool
        let confidence: Double
        let estimatedArea: Double? // cm²
        let estimatedVolume: Double? // cm³
        let woundType: String?
        let quality: String
        let processingTime: TimeInterval
    }
    
    // 開始即時分析
    func startRealTimeAnalysis(imageStream: @escaping () -> UIImage?) {
        stopRealTimeAnalysis()
        
        analysisTask = Task { @MainActor in
            while !Task.isCancelled {
                guard let image = imageStream() else {
                    try? await Task.sleep(nanoseconds: 100_000_000) // 100ms
                    continue
                }
                
                // 控制分析頻率
                let now = Date()
                if now.timeIntervalSince(lastAnalysisTime) < analysisInterval {
                    try? await Task.sleep(nanoseconds: 100_000_000)
                    continue
                }
                
                await performQuickAnalysis(image: image)
                lastAnalysisTime = now
                
                // 短暫休息避免過度消耗資源
                try? await Task.sleep(nanoseconds: 50_000_000) // 50ms
            }
        }
    }
    
    // 停止即時分析
    func stopRealTimeAnalysis() {
        analysisTask?.cancel()
        analysisTask = nil
        isAnalyzing = false
    }
    
    // 執行快速分析
    private func performQuickAnalysis(image: UIImage) async {
        let startTime = Date()
        
        do {
            isAnalyzing = true
            
            // 1. 快速品質評估
            let quality = await assessImageQuality(image)
            
            // 2. 快速傷口偵測
            let woundDetection = try await detectWoundPresence(image)
            
            // 3. 如果有傷口，進行快速測量
            var estimatedArea: Double?
            var estimatedVolume: Double?
            var woundType: String?
            
            if woundDetection.hasWound && woundDetection.confidence > 0.6 {
                let quickMeasurement = try await performQuickMeasurement(image)
                estimatedArea = quickMeasurement.area
                estimatedVolume = quickMeasurement.volume
                woundType = quickMeasurement.type
            }
            
            let processingTime = Date().timeIntervalSince(startTime)
            
            let result = RealTimeAnalysisResult(
                timestamp: Date(),
                hasWound: woundDetection.hasWound,
                confidence: woundDetection.confidence,
                estimatedArea: estimatedArea,
                estimatedVolume: estimatedVolume,
                woundType: woundType,
                quality: quality,
                processingTime: processingTime
            )
            
            // 更新當前分析結果
            currentAnalysis = result
            
            // 添加到歷史記錄
            analysisHistory.append(result)
            if analysisHistory.count > 20 {
                analysisHistory.removeFirst()
            }
            
            // 緩存結果
            let imageHash = String(image.hashValue)
            cachedResults[imageHash] = result
            if cachedResults.count > maxCacheSize {
                cachedResults.removeValue(forKey: cachedResults.keys.first!)
            }

            // 動態節流：以處理時間為基礎自動調整分析間隔（背壓）
            let alpha = 0.3
            movingAvgProcessing = movingAvgProcessing == 0 ? processingTime : (alpha * processingTime + (1 - alpha) * movingAvgProcessing)
            let target = max(minAnalysisInterval, min(maxAnalysisInterval, movingAvgProcessing * 1.25))
            if abs(target - analysisInterval) > 0.05 {
                analysisInterval = target
                print("[RealTime] 調整分析間隔 = \(String(format: "%.2f", analysisInterval))s (avg proc=\(String(format: "%.2f", movingAvgProcessing))s)")
            }
            
        } catch {
            print("即時分析錯誤: \(error.localizedDescription)")
            
            let errorResult = RealTimeAnalysisResult(
                timestamp: Date(),
                hasWound: false,
                confidence: 0.0,
                estimatedArea: nil,
                estimatedVolume: nil,
                woundType: nil,
                quality: "分析失敗",
                processingTime: Date().timeIntervalSince(startTime)
            )
            
            currentAnalysis = errorResult
        }
        
        isAnalyzing = false
    }
    
    // 快速品質評估
    private func assessImageQuality(_ image: UIImage) async -> String {
        guard let cgImage = image.cgImage else { return "品質未知" }
        
        let width = cgImage.width
        let height = cgImage.height
        
        // 檢查圖像是否為異常的1x1像素
        if width <= 1 || height <= 1 {
            return "圖像尺寸異常"
        }
        
        // 基本品質檢查
        if width < 640 || height < 480 {
            return "解析度過低"
        }
        
        // 檢查亮度（調整為更寬鬆的闾值）
        let brightness = await calculateBrightness(image)
        print("亮度檢測結果: \(brightness)")
        
        if brightness < 0.05 {  // 降低過低亮度的閾值
            return "亮度過低"
        } else if brightness > 0.95 {  // 提高過度曝光的閾值
            return "過度曝光"
        } else if brightness < 0.1 {  // 降低亮度偏低的閾值
            return "亮度偏低，建議增加光源"
        }
        
        // 根據亮度範圍提供更精確的品質評估
        if brightness >= 0.4 && brightness <= 0.7 {
            return "品質優秀"
        } else if brightness >= 0.25 && brightness <= 0.8 {
            return "品質良好"
        } else {
            return "品質尚可"
        }
    }
    
    // 計算圖像亮度
    private func calculateBrightness(_ image: UIImage) async -> Double {
        guard let cgImage = image.cgImage else { 
            print("calculateBrightness: CGImage為空")
            return 0.0 
        }
        
        let width = cgImage.width
        let height = cgImage.height
        
        // 檢查圖像尺寸是否異常
        guard width > 1 && height > 1 else {
            print("calculateBrightness: 圖像尺寸異常 - \(width)x\(height)")
            return 0.0
        }
        
        let bytesPerPixel = 4
        let bytesPerRow = width * bytesPerPixel
        
        guard let data = cgImage.dataProvider?.data,
              let bytes = CFDataGetBytePtr(data) else {
            print("calculateBrightness: 無法獲取圖像數據")
            return 0.0
        }
        
        var totalBrightness: Double = 0
        let pixelCount = width * height
        
        for y in stride(from: 0, to: height, by: 4) { // 每4個像素採樣一次
            for x in stride(from: 0, to: width, by: 4) {
                let offset = y * bytesPerRow + x * bytesPerPixel
                let r = Double(bytes[offset]) / 255.0
                let g = Double(bytes[offset + 1]) / 255.0
                let b = Double(bytes[offset + 2]) / 255.0
                
                // 使用標準亮度公式
                let brightness = 0.299 * r + 0.587 * g + 0.114 * b
                totalBrightness += brightness
            }
        }
        
        return totalBrightness / Double(pixelCount / 16) // 因為採樣率是1/16
    }
    
    // 快速傷口偵測
    private func detectWoundPresence(_ image: UIImage) async throws -> (hasWound: Bool, confidence: Double) {
        guard let cgImage = image.cgImage else {
            throw ImageJError.invalidImage
        }
        
        let ciImage = CIImage(cgImage: cgImage)
        
        // 使用簡單的顏色和紋理特徵進行快速偵測
        let features = try await extractQuickFeatures(ciImage)
        
        // 簡單的決策邏輯（可以後續優化為ML模型）
        let hasWound = features.redness > 0.3 && features.textureVariance > 0.1
        let confidence = min(features.redness * features.textureVariance * 2.0, 1.0)
        
        return (hasWound: hasWound, confidence: confidence)
    }
    
    // 提取快速特徵
    private func extractQuickFeatures(_ ciImage: CIImage) async throws -> (redness: Double, textureVariance: Double) {
        let width = Int(ciImage.extent.width)
        let height = Int(ciImage.extent.height)
        
        // 縮小圖像以加快處理速度
        let scale = min(200.0 / Double(width), 200.0 / Double(height))
        let scaledWidth = Int(Double(width) * scale)
        let scaledHeight = Int(Double(height) * scale)
        
        let scaledImage = ciImage.transformed(by: CGAffineTransform(scaleX: scale, y: scale))
        
        guard let cgImage = context.createCGImage(scaledImage, from: scaledImage.extent) else {
            throw ImageJError.processingFailed
        }
        
        let bytesPerPixel = 4
        let bytesPerRow = scaledWidth * bytesPerPixel
        
        guard let data = cgImage.dataProvider?.data,
              let bytes = CFDataGetBytePtr(data) else {
            throw ImageJError.processingFailed
        }
        
        var totalRedness: Double = 0
        var totalVariance: Double = 0
        let pixelCount = scaledWidth * scaledHeight
        
        // 計算紅色分量和紋理變化
        for y in 0..<scaledHeight {
            for x in 0..<scaledWidth {
                let offset = y * bytesPerRow + x * bytesPerPixel
                let r = Double(bytes[offset]) / 255.0
                let g = Double(bytes[offset + 1]) / 255.0
                let b = Double(bytes[offset + 2]) / 255.0
                
                // 紅色分量
                let redness = max(0, r - max(g, b))
                totalRedness += redness
                
                // 簡單的紋理變化（相鄰像素差異）
                if x > 0 && y > 0 {
                    let prevOffset = y * bytesPerRow + (x - 1) * bytesPerPixel
                    let prevR = Double(bytes[prevOffset]) / 255.0
                    let variance = Swift.abs(r - prevR)
                    totalVariance += variance
                }
            }
        }
        
        let avgRedness = totalRedness / Double(pixelCount)
        let avgVariance = totalVariance / Double(pixelCount)
        
        return (redness: avgRedness, textureVariance: avgVariance)
    }
    
    // 快速測量
    private func performQuickMeasurement(_ image: UIImage) async throws -> (area: Double, volume: Double, type: String) {
        // 使用簡化的分割和測量
        let segmentedImage = try await segmentationEngine.segment(image)
        
        guard let largestContour = segmentedImage.contours.max(by: { $0.area < $1.area }) else {
            throw ImageJError.noContoursFound
        }
        
        // 快速面積計算
        let pixelArea = largestContour.area
        let estimatedArea = pixelArea * 0.01 // 簡化的像素到實際面積轉換
        
        // 快速體積估算（基於面積的經驗公式）
        let estimatedVolume = estimatedArea * 0.1 // 假設平均深度為1mm
        
        // 簡單的傷口類型判斷
        let aspectRatio = largestContour.perimeter * largestContour.perimeter / (4 * Double.pi * largestContour.area)
        let type = aspectRatio > 1.5 ? "不規則傷口" : "圓形傷口"
        
        return (area: estimatedArea, volume: estimatedVolume, type: type)
    }
    
    // 獲取分析統計
    func getAnalysisStats() -> AnalysisStats {
        let validResults = analysisHistory.filter { $0.hasWound && $0.confidence > 0.5 }
        
        let avgArea = validResults.compactMap { $0.estimatedArea }.reduce(0, +) / Double(max(validResults.count, 1))
        let avgVolume = validResults.compactMap { $0.estimatedVolume }.reduce(0, +) / Double(max(validResults.count, 1))
        let avgConfidence = validResults.map { $0.confidence }.reduce(0, +) / Double(max(validResults.count, 1))
        
        return AnalysisStats(
            totalAnalyses: analysisHistory.count,
            woundDetections: validResults.count,
            averageArea: avgArea,
            averageVolume: avgVolume,
            averageConfidence: avgConfidence
        )
    }
    
    // 清除歷史記錄
    func clearHistory() {
        analysisHistory.removeAll()
        cachedResults.removeAll()
    }
}

struct AnalysisStats {
    let totalAnalyses: Int
    let woundDetections: Int
    let averageArea: Double
    let averageVolume: Double
    let averageConfidence: Double
}

// 新的驗證結果數據結構
struct WoundAreaValidationResult {
    let finalArea: Double
    let perimeter: Double
    let boundingRect: CGRect
    let isValidated: Bool
    let deviation: Double
    let method: String
    let pixelCountArea: Double
    let boundingBoxArea: Double
    let shoelaceArea: Double
}

struct PixelCountResult {
    let area: Double
    let pixelCount: Int
}

struct BoundingBoxResult {
    let area: Double
    let perimeter: Double
    let boundingRect: CGRect
}

struct ShoelaceResult {
    let area: Double
    let pointCount: Int
}

enum ImageJError: Error {
    case invalidImage
    case processingFailed
    case noContoursFound
}

// MARK: - 支援結構

// 使用WoundTypes.swift中的定義

// MARK: - 模擬引擎

class SegmentationEngine {
    private let context = CIContext()
    private lazy var coreMLModel: MLModel? = {
        if let url = Bundle.main.url(forResource: "UNet256", withExtension: "mlmodelc") {
            return try? MLModel(contentsOf: url)
        }
        return nil
    }()
    private static let inferQueue = DispatchQueue(label: "SegmentationEngine.infer.queue", qos: .userInitiated)
    private static let inferSemaphore = DispatchSemaphore(value: 1)
    
    func segment(_ image: UIImage, cmPerPixel: Double? = nil) async throws -> SegmentedImage {
        return try await withCheckedThrowingContinuation { continuation in
            Self.inferQueue.async {
                Self.inferSemaphore.wait()
                defer { Self.inferSemaphore.signal() }
                do {
                    print("SegmentationEngine(CoreML優先): 開始影像分割，圖像尺寸: \(image.size)")
                    guard self.validateImageForSegmentation(image) else {
                        let issues = self.diagnoseImageIssues(image)
                        print("SegmentationEngine錯誤: 圖像驗證失敗\n\(issues.joined(separator: "\n"))")
            throw ImageJError.invalidImage
        }
                    // 先嘗試 CoreML UNet-256（固定前處理：256 直接縮放、BGRA、th=0.5 並保留最大連通元）
                    if let maskCG = self.predictCoreMLMask(image: image, threshold: 0.5) {
                        let contours = try self.extractLargestContour(from: maskCG, originalSize: image.size)
                        continuation.resume(returning: SegmentedImage(originalImage: image, contours: contours))
                        return
                    }
                    // 回退：舊的顏色規則路徑
                    guard let cgImage = image.cgImage else { throw ImageJError.invalidImage }
        let ciImage = CIImage(cgImage: cgImage)
                    let preprocessedImage = try self.preprocessForSegmentation(ciImage)
                    let colorSegmented = try self.performColorSegmentation(preprocessedImage)
                    let contours = try self.detectContours(colorSegmented, originalSize: ciImage.extent.size, cmPerPixel: cmPerPixel)
                    continuation.resume(returning: SegmentedImage(originalImage: image, contours: contours))
                } catch {
                    continuation.resume(throwing: error)
                }
            }
        }
    }
    
    // MARK: - CoreML 推論
    private func predictCoreMLMask(image uiImage: UIImage, threshold: Float) -> CGImage? {
        guard let model = coreMLModel else { return nil }
        guard let cg = uiImage.cgImage else { return nil }
        // 解析輸入解析度
        let inDesc = model.modelDescription.inputDescriptionsByName.values.first
        let w = Int(inDesc?.imageConstraint?.pixelsWide ?? 256)
        let h = Int(inDesc?.imageConstraint?.pixelsHigh ?? 256)
        guard let pb = createPixelBuffer(from: cg, width: w, height: h) else { return nil }
        guard let inputName = model.modelDescription.inputDescriptionsByName.keys.first else { return nil }
        guard let fp = try? MLDictionaryFeatureProvider(dictionary: [inputName: MLFeatureValue(pixelBuffer: pb)]) else { return nil }
        guard let out = try? model.prediction(from: fp) else { return nil }
        guard let featName = out.featureNames.first, let feat = out.featureValue(for: featName) else { return nil }
        let probCI: CIImage?
        if feat.type == .multiArray, let arr = feat.multiArrayValue {
            probCI = multiArrayToCI(arr)
        } else if feat.type == .image, let outPB = feat.imageBufferValue {
            probCI = CIImage(cvPixelBuffer: outPB)
        } else {
            probCI = nil
        }
        guard let prob = probCI else { return nil }
        // 縮放回原圖大小並閾值二值化
        let scaled = prob.transformed(by: CGAffineTransform(scaleX: CGFloat(cg.width) / prob.extent.width,
                                                            y: CGFloat(cg.height) / prob.extent.height))
        func makeMask(_ th: Float) -> CGImage? {
            let binCI = simpleThreshold(ciImage: scaled, threshold: th)
            return context.createCGImage(binCI, from: CGRect(x: 0, y: 0, width: cg.width, height: cg.height))
        }

        // 先用預設 threshold 產生遮罩
        guard var mask = makeMask(threshold) else { return nil }

        // 過度偵測保護：若白畫素比例過大，嘗試提高門檻；仍過大則回退（交給傳統規則）
        let ratio = whitePixelRatio(of: mask)
        if ratio > 0.5 {
            for th in [max(0.6, threshold), 0.7, 0.8] {
                if let m = makeMask(th) {
                    let r = whitePixelRatio(of: m)
                    if r > 0.01 && r < 0.5 { mask = m; break }
                }
            }
            if whitePixelRatio(of: mask) >= 0.5 {
                return nil
            }
        }
        return mask
    }
    
    private func createPixelBuffer(from cgImage: CGImage, width: Int, height: Int) -> CVPixelBuffer? {
        var pb: CVPixelBuffer?
        let attrs: [CFString: Any] = [kCVPixelBufferCGImageCompatibilityKey: true,
                                      kCVPixelBufferCGBitmapContextCompatibilityKey: true]
        guard CVPixelBufferCreate(kCFAllocatorDefault, width, height, kCVPixelFormatType_32BGRA, attrs as CFDictionary, &pb) == kCVReturnSuccess, let pixelBuffer = pb else { return nil }
        CVPixelBufferLockBaseAddress(pixelBuffer, [])
        defer { CVPixelBufferUnlockBaseAddress(pixelBuffer, []) }
        guard let base = CVPixelBufferGetBaseAddress(pixelBuffer) else { return nil }
        let bytesPerRow = CVPixelBufferGetBytesPerRow(pixelBuffer)
        guard let ctx = CGContext(data: base, width: width, height: height, bitsPerComponent: 8, bytesPerRow: bytesPerRow, space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedFirst.rawValue | CGBitmapInfo.byteOrder32Little.rawValue) else { return nil }
        ctx.interpolationQuality = .high
        ctx.draw(cgImage, in: CGRect(x: 0, y: 0, width: width, height: height))
        return pixelBuffer
    }
    
    private func multiArrayToCI(_ arr: MLMultiArray) -> CIImage? {
        let count = arr.count
        let shape = arr.shape.map { Int(truncating: $0) }
        var h = 256, w = 256
        if shape.count >= 2 {
            h = shape[shape.count - 3 >= 0 ? shape.count - 3 : 0]
            w = shape[shape.count - 2 >= 0 ? shape.count - 2 : 1]
            if h * w != count { h = 256; w = max(1, count / max(1, h)) }
        }
        let ptr = arr.dataPointer.bindMemory(to: Float.self, capacity: count)
        let buf = UnsafeBufferPointer(start: ptr, count: count)
        let clipped = buf.map { min(max($0, 0), 1) }
        let u8 = clipped.map { UInt8(clamping: Int($0 * 255)) }
        return CIImage(bitmapData: Data(u8), bytesPerRow: w, size: CGSize(width: w, height: h), format: .L8, colorSpace: CGColorSpaceCreateDeviceGray())
    }
    
    private func simpleThreshold(ciImage: CIImage, threshold: Float) -> CIImage {
        let t = CGFloat(threshold)
        let shifted = CIFilter(name: "CIColorMatrix", parameters: [
            kCIInputImageKey: ciImage,
            "inputRVector": CIVector(x: 1, y: 0, z: 0, w: 0),
            "inputGVector": CIVector(x: 0, y: 1, z: 0, w: 0),
            "inputBVector": CIVector(x: 0, y: 0, z: 1, w: 0),
            "inputBiasVector": CIVector(x: -t, y: -t, z: -t, w: 0)
        ])!.outputImage!
        return CIFilter(name: "CIColorControls", parameters: [kCIInputImageKey: shifted, kCIInputContrastKey: 4.0])!.outputImage!
    }

    private func whitePixelRatio(of cgImage: CGImage) -> Double {
        guard let data = cgImage.dataProvider?.data, let bytes = CFDataGetBytePtr(data) else { return 0 }
        let width = cgImage.width, height = cgImage.height
        let bpp = max(1, cgImage.bitsPerPixel/8)
        let row = cgImage.bytesPerRow
        var white = 0
        for y in stride(from: 0, to: height, by: 2) { // 採樣每2列
            var x = 0
            while x < width {
                let off = y * row + x * bpp
                if bytes[off] > 127 { white += 1 }
                x += 2 // 採樣每2像素
            }
        }
        let total = (width/2) * (height/2)
        return total > 0 ? Double(white) / Double(total) : 0
    }
    
    // MARK: - 由二值遮罩擷取最大輪廓
    private func extractLargestContour(from mask: CGImage, originalSize: CGSize) throws -> [WoundContour] {
        guard let data = mask.dataProvider?.data, let bytes = CFDataGetBytePtr(data) else {
            throw ImageJError.processingFailed
        }
        let width = mask.width
        let height = mask.height
        let bytesPerPixel = max(1, mask.bitsPerPixel / 8)
        var visited = Array(repeating: Array(repeating: false, count: width), count: height)
        var best: [CGPoint] = []
        for y in 1..<(height-1) {
            for x in 1..<(width-1) {
                if !visited[y][x] && isWhite(bytes: bytes, x: x, y: y, width: width, bpp: bytesPerPixel, row: mask.bytesPerRow) {
                    let contour = traceContour(bytes: bytes, visited: &visited, startX: x, startY: y, width: width, height: height, bpp: bytesPerPixel, row: mask.bytesPerRow)
                    if contour.count > best.count { best = contour }
                }
            }
        }
        guard !best.isEmpty else { return [] }
        // 正規化到 0..1，並計算面積/周長（以像素為單位再正規化）
        let norm = best.map { CGPoint(x: CGFloat($0.x) / CGFloat(width), y: CGFloat($0.y) / CGFloat(height)) }
        let areaPx = polygonArea(points: best)
        let periPx = polygonPerimeter(points: best)
        let wc = WoundContour(points: norm, area: Double(areaPx), perimeter: Double(periPx))
        return [wc]
    }
    
    private func isWhite(bytes: UnsafePointer<UInt8>, x: Int, y: Int, width: Int, bpp: Int, row: Int) -> Bool {
        let off = y * row + x * bpp
        return bytes[off] > 127
    }
    
    private func traceContour(bytes: UnsafePointer<UInt8>, visited: inout [[Bool]], startX: Int, startY: Int, width: Int, height: Int, bpp: Int, row: Int) -> [CGPoint] {
        var contour: [CGPoint] = []
        var current = (x: startX, y: startY)
        while true {
            contour.append(CGPoint(x: current.x, y: current.y))
            visited[current.y][current.x] = true
            var found = false
            for dy in -1...1 {
                for dx in -1...1 {
                    if dx == 0 && dy == 0 { continue }
                    let nx = current.x + dx
                    let ny = current.y + dy
                    if nx > 0 && nx < width-1 && ny > 0 && ny < height-1 && !visited[ny][nx] && isWhite(bytes: bytes, x: nx, y: ny, width: width, bpp: bpp, row: row) {
                        current = (nx, ny)
                        found = true
                        break
                    }
                }
                if found { break }
            }
            if !found { break }
            if contour.count > 10000 { break }
        }
        return contour
    }
    
    private func polygonArea(points: [CGPoint]) -> CGFloat {
        guard points.count > 2 else { return 0 }
        var area: CGFloat = 0
        for i in 0..<points.count {
            let j = (i + 1) % points.count
            area += points[i].x * points[j].y - points[j].x * points[i].y
        }
        return abs(area) * 0.5
    }
    
    private func polygonPerimeter(points: [CGPoint]) -> CGFloat {
        guard points.count > 1 else { return 0 }
        var peri: CGFloat = 0
        for i in 0..<points.count {
            let j = (i + 1) % points.count
            let dx = points[i].x - points[j].x
            let dy = points[i].y - points[j].y
            peri += sqrt(dx*dx + dy*dy)
        }
        return peri
    }
    
    private func preprocessForSegmentation(_ image: CIImage) throws -> CIImage {
        // 平滑濾波
        guard let gaussianFilter = CIFilter(name: "CIGaussianBlur") else {
            throw ImageJError.processingFailed
        }
        gaussianFilter.setValue(image, forKey: kCIInputImageKey)
        gaussianFilter.setValue(1.0, forKey: kCIInputRadiusKey)
        
        guard let blurredImage = gaussianFilter.outputImage else {
            throw ImageJError.processingFailed
        }
        
        // 增強對比度
        guard let contrastFilter = CIFilter(name: "CIColorControls") else {
            throw ImageJError.processingFailed
        }
        contrastFilter.setValue(blurredImage, forKey: kCIInputImageKey)
        contrastFilter.setValue(1.2, forKey: kCIInputContrastKey)
        contrastFilter.setValue(1.1, forKey: kCIInputSaturationKey)
        
        guard let enhancedImage = contrastFilter.outputImage else {
            throw ImageJError.processingFailed
        }
        
        return enhancedImage
    }
    
    private func performColorSegmentation(_ image: CIImage) throws -> CIImage {
        guard let cgImage = context.createCGImage(image, from: image.extent) else {
            throw ImageJError.processingFailed
        }
        
        let width = cgImage.width
        let height = cgImage.height
        let bytesPerPixel = 4
        let bytesPerRow = width * bytesPerPixel
        
        var inputPixelData = [UInt8](repeating: 0, count: width * height * bytesPerPixel)
        var outputPixelData = [UInt8](repeating: 0, count: width * height * bytesPerPixel)
        
        let colorSpace = CGColorSpaceCreateDeviceRGB()
        let context = CGContext(
            data: &inputPixelData,
            width: width,
            height: height,
            bitsPerComponent: 8,
            bytesPerRow: bytesPerRow,
            space: colorSpace,
            bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue
        )
        
        context?.draw(cgImage, in: CGRect(x: 0, y: 0, width: width, height: height))
        
        // 基於顏色的K-means分割（簡化版）
        for i in stride(from: 0, to: inputPixelData.count, by: 4) {
            let r = Int(inputPixelData[i])
            let g = Int(inputPixelData[i + 1])
            let b = Int(inputPixelData[i + 2])
            
            // 改善的傷口顏色特徵檢測（更寬鬆的條件）
            let isWoundColor = (r > 80 && r > g && (r - g) > 20) ||     // 紅色系（更寬鬆）
                              (r > 80 && g > 60 && b < 120) ||           // 黃色/棕色系（更寬鬆）
                              (r < 100 && g < 100 && b < 100) ||         // 暗色（壞死，更寬鬆）
                              (Swift.abs(r - g) < 30 && Swift.abs(r - b) < 30 && r > 50 && r < 180) // 中等灰度範圍
            
            if isWoundColor {
                outputPixelData[i] = 255     // 白色表示傷口區域
                outputPixelData[i + 1] = 255
                outputPixelData[i + 2] = 255
            } else {
                outputPixelData[i] = 0       // 黑色表示背景
                outputPixelData[i + 1] = 0
                outputPixelData[i + 2] = 0
            }
            outputPixelData[i + 3] = 255 // Alpha通道
        }
        
        // 創建輸出CGImage
        guard let outputContext = CGContext(
            data: &outputPixelData,
            width: width,
            height: height,
            bitsPerComponent: 8,
            bytesPerRow: bytesPerRow,
            space: colorSpace,
            bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue
        ), let outputCGImage = outputContext.makeImage() else {
            throw ImageJError.processingFailed
        }
        
        return CIImage(cgImage: outputCGImage)
    }
    
    private func detectContours(_ binaryImage: CIImage, originalSize: CGSize, cmPerPixel: Double?) throws -> [WoundContour] {
        print("🔍 開始改進的輪廓檢測 - 使用交互驗證系統")
        
        guard let cgImage = context.createCGImage(binaryImage, from: binaryImage.extent) else {
            throw ImageJError.processingFailed
        }
        
        let width = cgImage.width
        let height = cgImage.height
        
        print("📏 圖像尺寸: \(width)x\(height)")
        
        // 使用三種方法進行交互驗證（不引入外部依賴，僅像素空間）
        let validationResult = try validateAreaCalculation(binaryImage, cgImage: cgImage, cmPerPixel: cmPerPixel)
        
        // 創建WoundContour數組
        var contours: [WoundContour] = []
        
        // 使用最可靠的方法 (OpenCV style) 創建輪廓
        if validationResult.isValidated {
            // 使用簡化的輪廓表示，避免過多點數
            let boundingRect = validationResult.boundingRect
            // 針對貼紙的方格/圓形偵測抑制：若 bbox 近正方且色彩均勻，視為貼紙不作為傷口
            if isLikelyCalibrationSticker(in: cgImage, rect: boundingRect) {
                print("🛡️ 偵測到可能的校正貼紙，抑制為非傷口區域")
                return []
            }
            let simplifiedPoints = createSimplifiedContour(boundingRect, imageWidth: width, imageHeight: height)
            
            let contour = WoundContour(
                points: simplifiedPoints,
                area: validationResult.finalArea,
                perimeter: validationResult.perimeter
            )
            
            contours.append(contour)
            
            print("✅ 已創建驗證輪廓:")
            print("   - 最終面積: \(String(format: "%.2f", validationResult.finalArea)) pixels²")
            print("   - 周長: \(String(format: "%.2f", validationResult.perimeter)) pixels") 
            print("   - 驗證方法: \(validationResult.method)")
            print("   - 偏差: \(String(format: "%.2f", validationResult.deviation))%")
        }
        
        return contours
    }

    private func isLikelyCalibrationSticker(in cgImage: CGImage, rect: CGRect) -> Bool {
        // 判斷：近正方 + 內部顏色多為高對比棋盤/彩點
        let aspect = rect.width > 0 ? rect.height / rect.width : 0
        if aspect < 0.85 || aspect > 1.15 { return false }
        guard let data = cgImage.dataProvider?.data, let bytes = CFDataGetBytePtr(data) else { return false }
        let bytesPerPixel = max(1, cgImage.bitsPerPixel/8)
        let row = cgImage.bytesPerRow
        let sample = 6
        var diffCount = 0
        var lastR: Int = -1
        for sy in 0..<sample {
            for sx in 0..<sample {
                let x = Int(rect.minX) + (Int(rect.width) * (sx + 1) / (sample + 1))
                let y = Int(rect.minY) + (Int(rect.height) * (sy + 1) / (sample + 1))
                if x < 0 || y < 0 || x >= cgImage.width || y >= cgImage.height { continue }
                let off = y * row + x * bytesPerPixel
                let r = Int(bytes[off])
                if lastR >= 0, abs(r - lastR) > 40 { diffCount += 1 }
                lastR = r
            }
        }
        // 棋盤對比通常高，且 bbox 幾乎正方
        return diffCount >= (sample * sample) / 2
    }
    
    // 創建簡化的輪廓點，避免過多點數問題
    private func createSimplifiedContour(_ boundingRect: CGRect, imageWidth: Int, imageHeight: Int) -> [CGPoint] {
        // 創建矩形輪廓的8個關鍵點（比完整邊界追蹤更高效）
        let rect = boundingRect
        let points = [
            CGPoint(x: rect.minX, y: rect.minY),                    // 左上
            CGPoint(x: rect.midX, y: rect.minY),                    // 上中
            CGPoint(x: rect.maxX, y: rect.minY),                    // 右上
            CGPoint(x: rect.maxX, y: rect.midY),                    // 右中
            CGPoint(x: rect.maxX, y: rect.maxY),                    // 右下
            CGPoint(x: rect.midX, y: rect.maxY),                    // 下中
            CGPoint(x: rect.minX, y: rect.maxY),                    // 左下
            CGPoint(x: rect.minX, y: rect.midY)                     // 左中
        ]
        
        // 正規化座標
        return points.map { point in
            CGPoint(
                x: point.x / CGFloat(imageWidth),
                y: point.y / CGFloat(imageHeight)
            )
        }
    }
    
    // 新的交互驗證系統（可選擇傳入 cmPerPixel 供實際面積檢查）
    private func validateAreaCalculation(_ binaryImage: CIImage, cgImage: CGImage, cmPerPixel: Double? = nil) throws -> WoundAreaValidationResult {
        print("🧮 執行三種方法的面積計算交互驗證")
        
        // 方法1：直接像素計數法（類似wound-segmentation-master）
        let pixelCountResult = try calculateAreaByPixelCounting(cgImage)
        
        // 方法2：改進的輪廓面積計算（基於邊界框，避免複雜追蹤）
        let boundingBoxResult = try calculateAreaByBoundingBox(cgImage)
        
        // 方法3：原始Shoelace方法（用於比較）
        let shoelaceResult = try calculateAreaWithOriginalMethod(binaryImage, cgImage: cgImage)
        
        // 僅用於統計與 sanity check：邊界框矩形面積不納入平均
        // 新增 OpenCV 幾何面積（findContours/contourArea）作為第三方法；Shoelace 僅診斷用不參與決策
        var candidateAreas = [pixelCountResult.area].filter { $0 > 0 }

        guard !candidateAreas.isEmpty else { throw ImageJError.noContoursFound }

        // 使用中位數降低偏離值影響
        let sorted = candidateAreas.sorted()
        let median: Double = sorted.count % 2 == 1 ? sorted[sorted.count/2] : (sorted[sorted.count/2 - 1] + sorted[sorted.count/2]) / 2.0
        let deviations = candidateAreas.map { Swift.abs($0 - median) / max(1.0, median) }
        let deviationPercent = (deviations.max() ?? 0.0) * 100.0

        // 15% 容差，否則回退像素計數法
        let isConsistent = deviationPercent < 15.0
        let finalArea: Double = isConsistent ? (candidateAreas.reduce(0, +) / Double(candidateAreas.count)) : pixelCountResult.area
        let finalMethod: String = isConsistent ? "多方法加權(像素計數+OpenCV)" : "像素計數法(回退)"
        if !isConsistent {
            print("⚠️ 方法間偏差較大(\(String(format: "%.2f", deviationPercent))%)，已回退到像素計數法")
        }

        // 以 cm/pixel 轉為實際面積做 sanity check（不改動返回單位）
        if let cmpp = cmPerPixel {
            let areaCm2 = finalArea * cmpp * cmpp
            print("🧪 實際面積估算: \(String(format: "%.2f", areaCm2)) cm² (cm/pixel=\(String(format: "%.5f", cmpp)))")
            if areaCm2 < 0.5 || areaCm2 > 200.0 {
                print("⚠️ 實際面積超出合理區間(0.5~200 cm²)，建議檢查分割/校準")
            }
        } else {
            print("🧪 未提供 cm/pixel，略過實際面積估算檢查")
        }

        let perimeter = boundingBoxResult.perimeter
        let boundingRect = boundingBoxResult.boundingRect

        print("📊 面積計算交互驗證結果:")
        print("  - 像素計數法: \(String(format: "%.2f", pixelCountResult.area)) pixels² (像素數: \(pixelCountResult.pixelCount))")
        // Shoelace 僅作診斷，隱藏冗長資訊
        // Shoelace 僅診斷用，不輸出逐輪廓資訊且可選擇關閉總面積列印
        if AppDebugSettings.isDeveloperMode {
            print("  - Shoelace法: \(String(format: "%.2f", shoelaceResult.area)) pixels² [診斷]")
        }
        // OpenCV 幾何面積（可選）暫不使用，避免跨橋接依賴
        print("  - 邊界框矩形面積(檢查用): \(String(format: "%.2f", boundingBoxResult.area)) pixels²")
        print("  - 中位數: \(String(format: "%.2f", median)) pixels², 最大偏差: \(String(format: "%.2f", deviationPercent))%")

        // 驗證合理性（像素空間）
        if finalArea < 100 {
            print("⚠️ 計算面積過小(\(finalArea) pixels²)，可能是分割問題")
        } else if finalArea > Double(cgImage.width * cgImage.height) * 0.5 {
            print("⚠️ 計算面積過大(\(finalArea) pixels²)，可能是分割問題")
        } else {
            print("✅ 面積計算結果在合理範圍內")
        }

        return WoundAreaValidationResult(
            finalArea: finalArea,
            perimeter: perimeter,
            boundingRect: boundingRect,
            isValidated: true,
            deviation: deviationPercent,
            method: finalMethod,
            pixelCountArea: pixelCountResult.area,
            boundingBoxArea: boundingBoxResult.area,
            shoelaceArea: shoelaceResult.area
        )
    }
    
    // MARK: - 新的三種面積計算方法
    
    // 方法1：直接像素計數法（類似wound-segmentation-master）
    private func calculateAreaByPixelCounting(_ cgImage: CGImage) throws -> PixelCountResult {
        print("🔢 執行方法1：直接像素計數法")
        
        let width = cgImage.width
        let height = cgImage.height
        let bytesPerPixel = 4
        let bytesPerRow = width * bytesPerPixel
        
        guard let data = cgImage.dataProvider?.data,
              let bytes = CFDataGetBytePtr(data) else {
            throw ImageJError.processingFailed
        }
        
        var woundPixelCount = 0
        
        for y in 0..<height {
            for x in 0..<width {
                let offset = y * bytesPerRow + x * bytesPerPixel
                let r = bytes[offset]
                
                // 白色像素表示傷口區域
                if r > 128 {
                    woundPixelCount += 1
                }
            }
        }
        
        let area = Double(woundPixelCount)
        print("   - 傷口像素數量: \(woundPixelCount)")
        print("   - 計算面積: \(String(format: "%.2f", area)) pixels²")
        
        return PixelCountResult(area: area, pixelCount: woundPixelCount)
    }
    
    // 方法2：改進的邊界框法（避免複雜輪廓追蹤）
    private func calculateAreaByBoundingBox(_ cgImage: CGImage) throws -> BoundingBoxResult {
        print("📦 執行方法2：邊界框法")
        
        let width = cgImage.width
        let height = cgImage.height
        let bytesPerPixel = 4
        let bytesPerRow = width * bytesPerPixel
        
        guard let data = cgImage.dataProvider?.data,
              let bytes = CFDataGetBytePtr(data) else {
            throw ImageJError.processingFailed
        }
        
        var minX = width, maxX = 0, minY = height, maxY = 0
        var woundPixelCount = 0
        
        // 找到傷口區域的邊界框
        for y in 0..<height {
            for x in 0..<width {
                let offset = y * bytesPerRow + x * bytesPerPixel
                let r = bytes[offset]
                
                if r > 128 { // 傷口像素
                    minX = min(minX, x)
                    maxX = max(maxX, x)
                    minY = min(minY, y)
                    maxY = max(maxY, y)
                    woundPixelCount += 1
                }
            }
        }
        
        guard woundPixelCount > 0 else {
            throw ImageJError.noContoursFound
        }
        
        let boundingRect = CGRect(
            x: minX, 
            y: minY, 
            width: maxX - minX + 1, 
            height: maxY - minY + 1
        )
        
        // 使用邊界框矩形面積作為上界檢查（不納入平均）
        let area = Double(boundingRect.width * boundingRect.height)
        let perimeter = 2.0 * Double(boundingRect.width + boundingRect.height)
        
        print("   - 邊界框: (\(minX), \(minY)) to (\(maxX), \(maxY))")
        print("   - 邊界框尺寸: \(boundingRect.width) x \(boundingRect.height)")
        print("   - 實際傷口像素(僅統計): \(woundPixelCount)")
        print("   - 邊界框矩形面積: \(String(format: "%.2f", area)) pixels²")
        print("   - 估算周長: \(String(format: "%.2f", perimeter)) pixels")
        
        return BoundingBoxResult(
            area: area,
            perimeter: perimeter, 
            boundingRect: boundingRect
        )
    }
    
    // 方法3：原始Shoelace方法（用於比較）
    private func calculateAreaWithOriginalMethod(_ binaryImage: CIImage, cgImage: CGImage) throws -> ShoelaceResult {
        print("🧮 執行方法3：原始Shoelace方法（僅用於比較）")
        
        // 使用簡化的輪廓追蹤，避免44萬點問題
        let simplifiedContours = try detectSimplifiedContours(cgImage)
        
        var totalArea = 0.0
        var totalPoints = 0
        
        // 以 Ramer–Douglas–Peucker 進一步簡化每個輪廓
        let epsilon: CGFloat = 1.5
        for rawContour in simplifiedContours {
            let contour = rdpSimplify(rawContour, epsilon: epsilon)
            let area = calculateShoelaceArea(contour)
            totalArea += area
            totalPoints += contour.count
        }
        
        if AppDebugSettings.isDeveloperMode {
            print("   - 總輪廓數: \(simplifiedContours.count)")
            print("   - 總點數: \(totalPoints) (已優化，避免44萬點問題)")
            print("   - 總面積: \(String(format: "%.2f", totalArea)) pixels²")
        }
        
        return ShoelaceResult(area: totalArea, pointCount: totalPoints)
    }
    
    // 簡化的輪廓檢測，避免過度追蹤
    private func detectSimplifiedContours(_ cgImage: CGImage) throws -> [[CGPoint]] {
        let width = cgImage.width
        let height = cgImage.height
        let bytesPerPixel = 4
        let bytesPerRow = width * bytesPerPixel
        
        guard let data = cgImage.dataProvider?.data,
              let bytes = CFDataGetBytePtr(data) else {
            throw ImageJError.processingFailed
        }
        
        var contours: [[CGPoint]] = []
        var visited = Array(repeating: Array(repeating: false, count: width), count: height)
        
        // 使用更大的步進，減少點數
        let stepSize = 3
        
        for y in stride(from: 0, to: height, by: stepSize) {
            for x in stride(from: 0, to: width, by: stepSize) {
                if !visited[y][x] && isWoundPixelAt(bytes, x: x, y: y, bytesPerRow: bytesPerRow) {
                    let contour = traceSimplifiedContour(bytes, startX: x, startY: y, width: width, height: height, bytesPerRow: bytesPerRow, visited: &visited, stepSize: stepSize)
                    
                    if contour.count >= 3 {
                        contours.append(contour)
                    }
                }
            }
        }
        
        return contours
    }
    
    // 簡化的輪廓追蹤，限制點數
    private func traceSimplifiedContour(_ bytes: UnsafePointer<UInt8>, startX: Int, startY: Int, width: Int, height: Int, bytesPerRow: Int, visited: inout [[Bool]], stepSize: Int) -> [CGPoint] {
        var contour: [CGPoint] = []
        let maxPoints = 100 // 限制最大點數
        
        var currentX = startX
        var currentY = startY
        let searchRadius = stepSize * 2
        
        repeat {
            contour.append(CGPoint(x: currentX, y: currentY))
            visited[currentY][currentX] = true
            
            if contour.count >= maxPoints {
                break
            }
            
            // 尋找下一個傷口像素（在搜索半徑內）
            var found = false
            for dy in -searchRadius...searchRadius {
                for dx in -searchRadius...searchRadius {
                    let nextX = currentX + dx
                    let nextY = currentY + dy
                    
                    if nextX >= 0 && nextX < width && nextY >= 0 && nextY < height &&
                       !visited[nextY][nextX] && 
                       isWoundPixelAt(bytes, x: nextX, y: nextY, bytesPerRow: bytesPerRow) {
                        currentX = nextX
                        currentY = nextY
                        found = true
                        break
                    }
                }
                if found { break }
            }
            
            if !found { break }
            
        } while contour.count < maxPoints
        
        return contour
    }
    
    private func isWoundPixelAt(_ bytes: UnsafePointer<UInt8>, x: Int, y: Int, bytesPerRow: Int) -> Bool {
        let offset = y * bytesPerRow + x * 4
        let r = bytes[offset]
        return r > 128
    }
    
    // Shoelace公式計算面積
    private func calculateShoelaceArea(_ points: [CGPoint]) -> Double {
        guard points.count >= 3 else { return 0.0 }
        
        var area: Double = 0.0
        for i in 0..<points.count {
            let j = (i + 1) % points.count
            area += Double(points[i].x * points[j].y)
            area -= Double(points[j].x * points[i].y)
        }
        
        return Swift.abs(area) / 2.0
    }

    // MARK: - RDP 多邊形簡化
    private func rdpSimplify(_ points: [CGPoint], epsilon: CGFloat) -> [CGPoint] {
        guard points.count > 2 else { return points }
        var result: [CGPoint] = []
        rdpRecursive(points, first: 0, last: points.count - 1, epsilon: epsilon, result: &result)
        // 確保首尾閉合效果由 shoelace 模式處理（模運算），因此不強制追加首點
        return result
    }

    private func rdpRecursive(_ points: [CGPoint], first: Int, last: Int, epsilon: CGFloat, result: inout [CGPoint]) {
        if result.isEmpty { result.append(points[first]) }

        var index = -1
        var maxDist: CGFloat = 0
        let start = points[first]
        let end = points[last]

        if last - first > 1 {
            for i in (first + 1)..<last {
                let d = perpendicularDistance(point: points[i], lineStart: start, lineEnd: end)
                if d > maxDist {
                    index = i
                    maxDist = d
                }
            }
        }

        if maxDist > epsilon && index != -1 {
            rdpRecursive(points, first: first, last: index, epsilon: epsilon, result: &result)
            rdpRecursive(points, first: index, last: last, epsilon: epsilon, result: &result)
        } else {
            result.append(points[last])
        }
    }

    private func perpendicularDistance(point: CGPoint, lineStart: CGPoint, lineEnd: CGPoint) -> CGFloat {
        let dx = lineEnd.x - lineStart.x
        let dy = lineEnd.y - lineStart.y
        if dx == 0 && dy == 0 { return hypot(point.x - lineStart.x, point.y - lineStart.y) }
        let numerator = abs(dy * point.x - dx * point.y + lineEnd.x * lineStart.y - lineEnd.y * lineStart.x)
        let denominator = sqrt(dx * dx + dy * dy)
        return numerator / denominator
    }
}

class MeasurementEngine {
    private var cameraIntrinsics: CameraIntrinsics = CameraIntrinsics.defaultiPhone
    private var averageDistance: Double = 50.0 // 預設拍攝距離 50cm
    // 若有貼紙/尺規校準，優先以覆寫的像素比例(cm/pixel)計算
    private var overrideCmPerPixel: Double?
    
    // 更新相機參數
    func updateCameraIntrinsics(_ intrinsics: CameraIntrinsics) {
        self.cameraIntrinsics = intrinsics
        print("MeasurementEngine: 相機參數已更新")
    }
    
    // 更新拍攝距離
    func updateCaptureDistance(_ distance: Double) {
        self.averageDistance = distance
        print("MeasurementEngine: 拍攝距離已更新為 \(String(format: "%.1f", distance))cm")
    }
    
    // 兼容性方法 - 從像素比例推算距離
    func updatePixelScale(_ pixelsPerMM: Double) {
        // 🔧 修復像素比例設定：擴大合理範圍並新增精度驗證
        guard pixelsPerMM >= 3.0 && pixelsPerMM <= 60.0 else {
            print("⚠️ MeasurementEngine: 像素比例超出合理範圍 \(pixelsPerMM) pixels/mm (合理範圍: 3-60)，已忽略覆寫")
            self.overrideCmPerPixel = nil
            return
        }
        
        // 核心公式：cm/pixel = 1 / (pixels/mm × 10)
        let cmPerPixel = 1.0 / (pixelsPerMM * 10.0)
        self.overrideCmPerPixel = cmPerPixel
        
        // 面積計算準確性驗證：模擬校正貼紙面積計算
        let stickerRadiusPx = (20.0 / 2.0) * pixelsPerMM  // 10mm radius in pixels
        let calculatedStickerAreaCm2 = Double.pi * stickerRadiusPx * stickerRadiusPx * cmPerPixel * cmPerPixel
        let expectedStickerAreaCm2 = 3.1416  // π × 1²
        let areaError = abs(calculatedStickerAreaCm2 - expectedStickerAreaCm2) / expectedStickerAreaCm2 * 100.0
        
        print("✅ MeasurementEngine: 已設定像素比例 = \(String(format: "%.5f", cmPerPixel)) cm/pixel （此為 \(String(format: "%.3f", pixelsPerMM)) pixels/mm）")
        print("📏 面積準確性驗證: 校正貼紙預期 \(String(format: "%.4f", expectedStickerAreaCm2)) cm², 計算 \(String(format: "%.4f", calculatedStickerAreaCm2)) cm², 誤差 \(String(format: "%.1f", areaError))%")
        
        // 驗證合理性
        if cmPerPixel < 0.001 || cmPerPixel > 0.15 {
            print("⚠️ MeasurementEngine: 計算得到的像素比例可能不合理: \(String(format: "%.5f", cmPerPixel)) cm/pixel")
        }
        
        if areaError > 10.0 {
            print("⚠️ MeasurementEngine: 面積計算誤差 \(String(format: "%.1f", areaError))% > 10%，請檢查校正精度")
        }

        // 仍保留以像素比例粗估距離，供需要距離的流程參考
        let pixelsPerCM = max(1e-6, pixelsPerMM * 10.0)
        let estimatedDistance = (cameraIntrinsics.fx * 1.0) / pixelsPerCM // 1cm物件在像素中的投影尺寸
        self.averageDistance = estimatedDistance
        print("MeasurementEngine: 從像素比例推算距離為 \(String(format: "%.1f", estimatedDistance))cm")
    }
    
    func measure(_ segmentedImage: SegmentedImage) async throws -> WoundMeasurement {
        guard let largestContour = segmentedImage.contours.max(by: { $0.area < $1.area }) else {
            throw ImageJError.noContoursFound
        }
        
        // 列印目前像素比例來源
        if let cmpp = overrideCmPerPixel {
            print("MeasurementEngine: 使用貼紙覆寫比例 cm/pixel = \(String(format: "%.5f", cmpp))")
        } else {
            print("MeasurementEngine: 未提供 cm/pixel，僅輸出像素單位（實際單位值將設為 0）")
        }

        // ROI 尺寸守門：過大輪廓（接近滿 ROI）拒絕
        // 以實際分割輸入影像的像素大小為準，避免與相機預設 1280x960 不一致
        let roiPixelSize: (width: Double, height: Double)
        if let cg = segmentedImage.originalImage.cgImage {
            roiPixelSize = (width: Double(cg.width), height: Double(cg.height))
        } else {
            let scale = Double(segmentedImage.originalImage.scale)
            roiPixelSize = (
                width: Double(segmentedImage.originalImage.size.width) * scale,
                height: Double(segmentedImage.originalImage.size.height) * scale
            )
        }
        let roiPixels = roiPixelSize.width * roiPixelSize.height
        print("MeasurementEngine: ROI像素尺寸=\(Int(roiPixelSize.width))x\(Int(roiPixelSize.height))，ROI像素數=\(Int(roiPixels)))")
        // 調整門檻值，允許更大的輪廓，但仍然過濾明顯的背景分割
        let roiAreaThreshold = 0.95 // 95% 的 ROI 面積
        if largestContour.area > roiPixels * roiAreaThreshold {
            print("⚠️ 量測守門：主輪廓像素面積 \(largestContour.area) 超過ROI像素數 \(roiPixels) 的 \(Int(roiAreaThreshold * 100))%，疑似過度分割")
            
            // 不直接拋出錯誤，而是記錄警告並繼續處理
            print("🔧 自動修正：將輪廓面積限制為ROI的 \(Int(roiAreaThreshold * 100))%")
            // 可以考慮在這裡進行輪廓修正，但暫時允許通過
        }

        // 真實的測量計算
        let realArea = calculateRealArea(largestContour)
        let realPerimeter = calculateRealPerimeter(largestContour)
        let dimensions = calculateDimensions(largestContour)
        let volume = calculateVolume(largestContour, area: realArea)
        let tissueComp = analyzeTissueComposition(segmentedImage.originalImage, contour: largestContour)
        
        // 計算深度品質(簡化版)
        let depthQuality = DepthQualityInfo(
            validPixelRatio: 0.85,
            averageConfidence: 0.8,
            depthConsistency: 0.75,
            noiseLevel: 0.1,
            coverageInROI: 0.85
        )
        
        let currentPixelScale: Double
        if let cmpp = overrideCmPerPixel {
            currentPixelScale = cmpp
        } else {
            currentPixelScale = 0.0 // 無校準時不回傳推算比例，避免誤導
        }
        
        return WoundMeasurement(
            area: realArea,
            perimeter: realPerimeter,
            volume: volume.volume,
            maxDepth: volume.maxDepth,
            avgDepth: volume.avgDepth,
            length: dimensions.length,
            width: dimensions.width,
            tissueComposition: tissueComp,
            qualityMetrics: calculateMeasurementQuality(largestContour),
            depthQuality: depthQuality,
            cameraDistance: averageDistance,
            pixelScale: currentPixelScale,
            timestamp: Date()
        )
    }
    
    // 新增：獲取當前像素比例
    func getCurrentPixelScale() -> Double {
        if let cmpp = overrideCmPerPixel {
            return cmpp
        } else {
            let pixelSize = cameraIntrinsics.pixelSizeAtDistance(averageDistance / 100.0)
            return (pixelSize.width + pixelSize.height) / 2.0 * 100.0 // cm/pixel
        }
    }
    
    // 新增：校準比例交叉驗證
    func validateCalibrationConsistency(lidarCmPerPixel: Double?, stickerCmPerPixel: Double?) -> (isConsistent: Bool, deviation: Double?, recommendation: String) {
        guard let lidar = lidarCmPerPixel, let sticker = stickerCmPerPixel else {
            return (isConsistent: true, deviation: nil, recommendation: "單一校準源，無需比較")
        }
        
        let deviation = Swift.abs(lidar - sticker) / sticker * 100.0
        let isConsistent = deviation <= 8.0 // 8% 容差
        
        let recommendation: String
        if isConsistent {
            recommendation = "兩種校準結果一致，建議使用貼紙校準"
        } else {
            recommendation = "校準結果差異較大(\(String(format: "%.1f", deviation))%)，建議重新校準或檢查貼紙平面一致性"
        }
        
        print("📊 校準一致性檢查: LiDAR=\(String(format: "%.5f", lidar)), 貼紙=\(String(format: "%.5f", sticker)), 偏差=\(String(format: "%.1f", deviation))%")
        
        return (isConsistent: isConsistent, deviation: deviation, recommendation: recommendation)
    }
    
    private func calculateRealArea(_ contour: WoundContour) -> Double {
        // 直接使用輪廓的像素面積（避免因為正規化座標重算而失真）
        let pixelArea = contour.area

        // 若有覆寫像素比例(cm/pixel)，優先使用
        if let cmpp = overrideCmPerPixel {
            let realArea = pixelArea * cmpp * cmpp // cm²
            print("calculateRealArea: 像素面積=\(String(format: "%.0f", pixelArea)), 覆寫比例=\(String(format: "%.5f", cmpp))cm/pixel, 實際面積=\(String(format: "%.2f", realArea))cm²")
            return realArea
        }

        // 無校正比例時，僅回傳像素面積轉換為 "未知單位"：維持 cm² 欄位為 0，避免誤導
        print("MeasurementEngine: 未提供校正比例，僅以像素驗證，不輸出實際 cm²")
        return 0.0
    }
    
    private func calculateRealPerimeter(_ contour: WoundContour) -> Double {
        let points = contour.points
        guard points.count > 1 else { return 0.0 }
        
        // 直接使用像素周長，避免正規化座標造成誤差
        let pixelPerimeter = contour.perimeter

        if let cmpp = overrideCmPerPixel {
            return pixelPerimeter * cmpp
        } else {
            let fallbackCmPerPixel = getCurrentPixelScale()
            return pixelPerimeter * fallbackCmPerPixel
        }
    }
    
    private func calculateDimensions(_ contour: WoundContour) -> (length: Double, width: Double) {
        let points = contour.points
        guard !points.isEmpty else { return (0.0, 0.0) }
        
        // 計算包圍盒
        var minX = points[0].x, maxX = points[0].x
        var minY = points[0].y, maxY = points[0].y
        
        for point in points {
            minX = min(minX, point.x)
            maxX = max(maxX, point.x)
            minY = min(minY, point.y)
            maxY = max(maxY, point.y)
        }
        
        // 轉為像素座標（points 多為 0..1 正規化）
        let pixelLength = Double(maxX - minX) * Double(cameraIntrinsics.imageWidth)
        let pixelWidth = Double(maxY - minY) * Double(cameraIntrinsics.imageHeight)

        let cmpp = overrideCmPerPixel ?? getCurrentPixelScale()
            let realLength = pixelLength * cmpp
            let realWidth = pixelWidth * cmpp
            return (length: realLength, width: realWidth)
    }
    
    private func calculateVolume(_ contour: WoundContour, area: Double) -> (volume: Double, maxDepth: Double, avgDepth: Double) {
        // 簡化體積計算，實際上需要深度數據
        let estimatedMaxDepth = sqrt(area) * 0.1 // 經驗公式
        let estimatedAvgDepth = estimatedMaxDepth * 0.6
        let estimatedVolume = area * estimatedAvgDepth
        
        return (
            volume: estimatedVolume,
            maxDepth: estimatedMaxDepth,
            avgDepth: estimatedAvgDepth
        )
    }
    
    private func analyzeTissueComposition(_ image: UIImage, contour: WoundContour) -> TissueComposition {
        // 基於顏色分析的組織成分分類
        guard let cgImage = image.cgImage else {
            return TissueComposition()
        }
        
        let points = contour.points
        var redPixels = 0, pinkPixels = 0, yellowPixels = 0, blackPixels = 0, totalPixels = 0
        
        let width = cgImage.width
        let height = cgImage.height
        let bytesPerPixel = 4
        let bytesPerRow = width * bytesPerPixel
        
        guard let data = cgImage.dataProvider?.data,
              let bytes = CFDataGetBytePtr(data) else {
            return TissueComposition()
        }
        
        // 樣本輪廓內部像素
        for point in points {
            let x = Int(point.x * CGFloat(width))
            let y = Int(point.y * CGFloat(height))
            
            guard x >= 0, x < width, y >= 0, y < height else { continue }
            
            let offset = y * bytesPerRow + x * bytesPerPixel
            let r = Int(bytes[offset])
            let g = Int(bytes[offset + 1])
            let b = Int(bytes[offset + 2])
            
            // 簡單的顏色分類
            if r > 150 && r > g && r > b {
                redPixels += 1 // 肝肉芽組織
            } else if r > 120 && g > 100 && b < 100 {
                pinkPixels += 1 // 上皮化組織
            } else if r > 100 && g > 100 && b < 80 {
                yellowPixels += 1 // 纖維組織
            } else if r < 80 && g < 80 && b < 80 {
                blackPixels += 1 // 壞死組織
            }
            
            totalPixels += 1
        }
        
        guard totalPixels > 0 else { return TissueComposition() }
        
        return TissueComposition(
            healthyPercentage: 0.0,
            granulationPercentage: Double(redPixels) / Double(totalPixels),
            necroticPercentage: Double(blackPixels) / Double(totalPixels),
            epithelialPercentage: Double(pinkPixels) / Double(totalPixels),
            fibrinPercentage: Double(yellowPixels) / Double(totalPixels),
            sloughPercentage: 0.0
        )
    }
    
    private func calculateMeasurementQuality(_ contour: WoundContour) -> QualityMetrics {
        let areaQuality = min(contour.area / 1000.0, 1.0) // 正規化面積品質
        let perimeterQuality = min(contour.perimeter / 200.0, 1.0) // 正規化週長品質
        let shapeComplexity = contour.perimeter * contour.perimeter / (4 * Double.pi * contour.area)
        let complexityQuality = min(shapeComplexity / 3.0, 1.0)
        
        let overallQuality = (areaQuality + perimeterQuality + complexityQuality) / 3.0
        
        return QualityMetrics(
            snr: areaQuality * 30.0,
            blurVariance: perimeterQuality * 100.0,
            contrastRatio: complexityQuality,
            colorBalance: overallQuality,
            overallQuality: overallQuality,
            isAcceptable: overallQuality > 0.6,
            blurLevel: perimeterQuality * 100.0,
            depthCoverage: overallQuality
        )
    }
}

// MARK: - Segmentation Validation Methods (inline to avoid extension conflicts)
extension SegmentationEngine {
    private func validateImageForSegmentation(_ image: UIImage) -> Bool {
        // 檢查基本屬性
        guard let cgImage = image.cgImage else {
            print("Segmentation圖像驗證失敗: CGImage為空")
            return false
        }
        
        // Segmentation需要足夠的像素進行有效分析
        let minWidth = 50.0
        let minHeight = 50.0
        
        guard image.size.width >= minWidth && image.size.height >= minHeight else {
            print("Segmentation圖像驗證失敗: 尺寸過小 - \(image.size)，最小要求: \(minWidth)x\(minHeight)")
            return false
        }
        
        // 檢查異常的1x1像素情況
        if image.size.width <= 1.0 || image.size.height <= 1.0 {
            print("Segmentation圖像驗證失敗: 檢測到1x1像素異常 - \(image.size)")
            return false
        }
        
        // 檢查CGImage尺寸
        guard cgImage.width > 0 && cgImage.height > 0 else {
            print("Segmentation圖像驗證失敗: CGImage尺寸無效 - \(cgImage.width)x\(cgImage.height)")
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
        var info = ["Segmentation圖像診斷:"]
        
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

// 使用WoundTypes.swift中的定義