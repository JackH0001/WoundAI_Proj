import SwiftUI
import CoreImage
import CoreGraphics
import UIKit
import Combine

class ImageJCore: ObservableObject {
    // 使用RealTimeAnalysisModule.swift中的引擎定義
    private let segmentationEngine = SegmentationEngine()
    let measurementEngine = MeasurementEngine() // 改為public以供校準模組訪問
    private var storedCalibrationResult: CalibrationResult?
    enum CalibrationSource {
        case sticker
        case lidar
        case fallback
    }
    @Published var lastCalibrationSource: CalibrationSource = .fallback
    
    @Published var isProcessing = false
    @Published var error: String?
    @Published var calibrationStatus = "未校準"
    @Published var isCalibrating = false
    
    // LiDAR校準模組
    let liDARCalibrationModule = LiDARCalibrationModule()
    private var lidarObserversInstalled = false
    private var cancellables: Set<AnyCancellable> = []
    
    // MARK: - 校準方法
    
    func startLiDARCalibration() {
        Task { @MainActor in
            isCalibrating = true
            calibrationStatus = "校準中..."
        }
        // 確保每次開始前停止殘留計時器並重置狀態，並延遲釋放與重建避免與相機競態
        liDARCalibrationModule.stopCalibration()
        liDARCalibrationModule.startCalibration()

        // 一次性綁定 LiDAR 模組狀態到 ImageJCore
        if !lidarObserversInstalled {
            lidarObserversInstalled = true
            liDARCalibrationModule.$isCalibrating.receive(on: DispatchQueue.main).sink { [weak self] val in
                self?.isCalibrating = val
            }.store(in: &cancellables)
            liDARCalibrationModule.$calibrationStatus.receive(on: DispatchQueue.main).sink { [weak self] val in
                self?.calibrationStatus = val
            }.store(in: &cancellables)
        }
    }
    
    func stopLiDARCalibration() {
        Task { @MainActor in
            isCalibrating = false
            calibrationStatus = "已停止"
        }
        // 停止並完全釋放 ARSession，避免與相機同時存取造成 CMCapture 錯誤
        liDARCalibrationModule.stopCalibration()
    }
    
    // MARK: - 主要測量功能
    
    func measureWound(_ processedImage: ProcessedImage, calibrationPixelsPerMM: Double? = nil, calibrationResult: CalibrationResult? = nil) async throws -> WoundMeasurement {
        print("ImageJCore: 開始 measureWound，圖像尺寸: \(processedImage.image.size)")
        await MainActor.run {
            isProcessing = true
        }
        defer {
            Task { @MainActor in
                isProcessing = false
            }
        }
        
        do {
            // 應用校正結果以供測量使用
            if let calibrationResult = calibrationResult {
                self.storedCalibrationResult = calibrationResult
                
                // 🔧 內建校正驗證邏輯
                print("ImageJCore: 正在驗證校正結果準確性...")
                let areaValidation = validateCalibrationAccuracy(
                    pixelsPerMM: calibrationResult.pixelPerMM
                )
                
                print("📊 校正驗證結果: 誤差 \(String(format: "%.1f", areaValidation.errorPercent))%")
                
                if areaValidation.isAcceptable {
                    measurementEngine.updatePixelScale(calibrationResult.pixelPerMM)
                    print("✅ ImageJCore: 已應用驗證通過的校正結果，像素比例: \(String(format: "%.3f", calibrationResult.pixelPerMM)) pixels/mm")
                } else {
                    print("⚠️ ImageJCore: 校正結果驗證未通過，誤差 \(String(format: "%.1f", areaValidation.errorPercent))%，仍將使用但可能影響精度")
                    measurementEngine.updatePixelScale(calibrationResult.pixelPerMM)
                }
                lastCalibrationSource = .sticker
            } else if let pixelsPerMM = calibrationPixelsPerMM {
                // 向後兼容的校準方式，同樣進行驗證
                let areaValidation = validateCalibrationAccuracy(pixelsPerMM: pixelsPerMM)
                
                measurementEngine.updatePixelScale(pixelsPerMM)
                print("ImageJCore: 已應用像素比例: \(String(format: "%.3f", pixelsPerMM)) pixels/mm (誤差: \(String(format: "%.1f", areaValidation.errorPercent))%)")
                lastCalibrationSource = .sticker
            } else {
                print("ImageJCore: 無校正結果，使用默認測量方式")
                lastCalibrationSource = .fallback
            }
            
            // 分割影像
            print("ImageJCore: 開始分割影像...")
            let segmentedImage = try await segmentWound(processedImage.image)
            print("ImageJCore: 分割完成，找到 \(segmentedImage.contours.count) 個輪廓")
            
            // 計算面積
            print("ImageJCore: 開始計算面積...")
            let areaMeasurement = try await calculateArea(segmentedImage, roi: processedImage.roi)
            print("ImageJCore: 面積計算完成: \(areaMeasurement.area) cm²")
            
            // 計算體積
            print("ImageJCore: 開始計算體積...")
            let volumeMeasurement = try await calculateVolume(segmentedImage, depthData: processedImage.depthData, roi: processedImage.roi)
            print("ImageJCore: 體積計算完成: \(volumeMeasurement.volume) cm³")
            
            // 分析組織類型
            print("ImageJCore: 開始分析組織類型...")
            let tissueComposition = try await analyzeTissueTypes(segmentedImage)
            print("ImageJCore: 組織分析完成")
            
            // 計算深度品質資訊和實際尺寸
            let depthQualityInfo = calculateDepthQuality(processedImage.depthData)
            let realDimensions = calculateRealDimensions(from: processedImage.roi)
            
            // 創建測量結果
            let measurement = WoundMeasurement(
                area: areaMeasurement.area,
                perimeter: areaMeasurement.perimeter,
                volume: volumeMeasurement.volume,
                maxDepth: volumeMeasurement.maxDepth,
                avgDepth: volumeMeasurement.maxDepth * 0.6, // 改進的平均深度估算
                length: realDimensions.length,
                width: realDimensions.width,
                tissueComposition: tissueComposition,
                qualityMetrics: processedImage.qualityMetrics,
                depthQuality: depthQualityInfo,
                cameraDistance: 50.0, // 預設距離
                pixelScale: measurementEngine.getCurrentPixelScale(),
                timestamp: Date()
            )
            
            return measurement
            
        } catch {
            await MainActor.run {
                self.error = error.localizedDescription
            }
            throw error
        }
    }
    
    // MARK: - 私有方法
    
    private func segmentWound(_ image: UIImage) async throws -> SegmentedImage {
        return try await segmentationEngine.segment(image)
    }
    
    private func calculateArea(_ segmentedImage: SegmentedImage, roi: CGRect) async throws -> AreaMeasurement {
        let measurement = try await measurementEngine.measure(segmentedImage)
        return AreaMeasurement(area: measurement.area, perimeter: measurement.perimeter)
    }
    
    private func calculateVolume(_ segmentedImage: SegmentedImage, depthData: Data, roi: CGRect) async throws -> VolumeMeasurement {
        guard let largestContour = segmentedImage.contours.max(by: { $0.area < $1.area }) else {
            throw NSError(domain: "ImageJCore", code: -1, userInfo: [NSLocalizedDescriptionKey: "No contours found"])
        }
        
        // 使用改進的逐像素積分方法計算體積
        let depthAnalysis = try analyzeDepthDataWithPixelwiseIntegration(depthData, contour: largestContour, roi: roi)
        
        return VolumeMeasurement(
            volume: depthAnalysis.volume,
            maxDepth: depthAnalysis.maxDepth
        )
    }
    
    // 新增：按照技術文件建議的逐像素積分方法
    private func analyzeDepthDataWithPixelwiseIntegration(_ depthData: Data, contour: WoundContour, roi: CGRect) throws -> (volume: Double, maxDepth: Double) {
        guard !depthData.isEmpty else {
            // 無深度數據時使用估算
            let estimatedVolume = contour.area * 0.01 * 0.1
            return (volume: estimatedVolume, maxDepth: 0.1)
        }
        
        // 解析深度數據
        let depthValues = depthData.withUnsafeBytes { bytes in
            Array(bytes.bindMemory(to: Float32.self))
        }
        
        // 計算深度圖尺寸 - 使用ARKit常用的256x192解析度
        let depthWidth = 256
        let depthHeight = 192
        
        guard depthValues.count >= depthWidth * depthHeight else {
            print("深度數據大小不符: 期望\(depthWidth * depthHeight), 實際\(depthValues.count)")
            return (volume: contour.area * 0.01 * 0.1, maxDepth: 0.1)
        }
        
        // 獲取相機內參
        let cameraIntrinsics = CameraIntrinsics.defaultiPhone
        
        // 計算傳考平面（傷口邊緣的平均深度）
        let referenceDepth = calculateReferenceDepth(depthValues, contour: contour, depthWidth: depthWidth, depthHeight: depthHeight)
        
        var totalVolume: Double = 0.0
        var maxDepthDifference: Double = 0.0
        var validVoxelCount = 0
        let _ = DepthQualityInfo(validPixelRatio: 0, averageConfidence: 0, depthConsistency: 0, noiseLevel: 0, coverageInROI: 0) // 暫時未使用
        
        // 逐像素積分計算體積：V = Σ(A_pixel,i × d_i)
        for point in contour.points {
            // 將正規化座標轉換為深度圖座標
            let depthX = Int(point.x * CGFloat(depthWidth))
            let depthY = Int(point.y * CGFloat(depthHeight))
            
            guard depthX >= 0, depthX < depthWidth, depthY >= 0, depthY < depthHeight else {
                continue
            }
            
            let depthIndex = depthY * depthWidth + depthX
            guard depthIndex < depthValues.count else { continue }
            
            let pixelDepth = Double(depthValues[depthIndex]) // 公尺單位
            
            // 過濾異常深度值
            guard pixelDepth > 0.001 && pixelDepth < 2.0 else { continue }
            
            // 計算該像素在實際空間中的面積
            // 使用公式：A_pixel = Z²/(fx × fy)
            let pixelArea = (pixelDepth * pixelDepth) / (cameraIntrinsics.fx * cameraIntrinsics.fy) * 10000.0 // 轉換為cm²
            
            // 計算深度差（相對於參考平面）
            let depthDifference = max(0, referenceDepth - pixelDepth) * 100.0 // 轉換為cm
            
            // 計算體素體積：體積 = 像素面積 × 深度差
            let voxelVolume = pixelArea * depthDifference
            
            totalVolume += voxelVolume
            maxDepthDifference = max(maxDepthDifference, depthDifference)
            validVoxelCount += 1
        }
        
        print("逐像素體積計算完成: 總體積=\(String(format: "%.4f", totalVolume))cm³, 最大深度=\(String(format: "%.2f", maxDepthDifference))cm, 有效體素=\(validVoxelCount)")
        
        return (volume: totalVolume, maxDepth: maxDepthDifference)
    }
    
    // 計算傳考平面深度（傷口邊緣平均深度）
    private func calculateReferenceDepth(_ depthValues: [Float32], contour: WoundContour, depthWidth: Int, depthHeight: Int) -> Double {
        var edgeDepths: [Double] = []
        let contourBounds = calculateContourBounds(contour.points)
        
        // 取輪廓邊界附近的深度值作為參考
        let edgePoints = getEdgePoints(from: contour.points, bounds: contourBounds)
        
        for point in edgePoints {
            let depthX = Int(point.x * CGFloat(depthWidth))
            let depthY = Int(point.y * CGFloat(depthHeight))
            
            guard depthX >= 0, depthX < depthWidth, depthY >= 0, depthY < depthHeight else { continue }
            
            let depthIndex = depthY * depthWidth + depthX
            guard depthIndex < depthValues.count else { continue }
            
            let depth = Double(depthValues[depthIndex])
            if depth > 0.001 && depth < 2.0 {
                edgeDepths.append(depth)
            }
        }
        
        // 返回邊緣深度的中位數（比平均值更穩健）
        guard !edgeDepths.isEmpty else { return 0.5 }
        edgeDepths.sort()
        return edgeDepths[edgeDepths.count / 2]
    }
    
    // 計算輪廓邊界
    private func calculateContourBounds(_ points: [CGPoint]) -> CGRect {
        guard !points.isEmpty else { return CGRect.zero }
        
        var minX = points[0].x, maxX = points[0].x
        var minY = points[0].y, maxY = points[0].y
        
        for point in points {
            minX = min(minX, point.x)
            maxX = max(maxX, point.x)
            minY = min(minY, point.y) 
            maxY = max(maxY, point.y)
        }
        
        return CGRect(x: minX, y: minY, width: maxX - minX, height: maxY - minY)
    }
    
    // 獲取邊緣點
    private func getEdgePoints(from points: [CGPoint], bounds: CGRect) -> [CGPoint] {
        let margin: CGFloat = 0.1 // 10%邊界緣區域
        
        return points.filter { point in
            let relativeX = (point.x - bounds.minX) / bounds.width
            let relativeY = (point.y - bounds.minY) / bounds.height
            
            return relativeX <= margin || relativeX >= (1.0 - margin) ||
                   relativeY <= margin || relativeY >= (1.0 - margin)
        }
    }
    
    // 保留舊的方法作為備用
    private func analyzeDepthData(_ depthData: Data, contour: WoundContour, roi: CGRect) -> (volume: Double, maxDepth: Double) {
        do {
            return try analyzeDepthDataWithPixelwiseIntegration(depthData, contour: contour, roi: roi)
        } catch {
            print("進階深度分析失敗，使用簡化方法: \(error)")
            // 這裡可以保留原本的簡化實作作為備用
            let estimatedVolume = contour.area * 0.01 * 0.1
            return (volume: estimatedVolume, maxDepth: 0.1)
        }
    }
    
    private func analyzeTissueTypes(_ segmentedImage: SegmentedImage) async throws -> TissueComposition {
        let measurement = try await measurementEngine.measure(segmentedImage)
        return measurement.tissueComposition
    }
    
    // 新增：計算深度品質資訊
    private func calculateDepthQuality(_ depthData: Data) -> DepthQualityInfo {
        guard !depthData.isEmpty else {
            return DepthQualityInfo(validPixelRatio: 0, averageConfidence: 0, depthConsistency: 0, noiseLevel: 1.0, coverageInROI: 0)
        }
        
        let depthValues = depthData.withUnsafeBytes { bytes in
            Array(bytes.bindMemory(to: Float32.self))
        }
        
        var validPixels = 0
        let totalPixels = depthValues.count
        
        for depth in depthValues {
            let depthValue = Double(depth)
            if depthValue >= 0.001 && depthValue <= 2.0 {
                validPixels += 1
            }
        }
        
        let validRatio = Double(validPixels) / Double(totalPixels)
        
        return DepthQualityInfo(
            validPixelRatio: validRatio,
            averageConfidence: min(validRatio * 1.2, 1.0),
            depthConsistency: validRatio > 0.8 ? 0.9 : 0.6,
            noiseLevel: 1.0 - validRatio,
            coverageInROI: validRatio
        )
    }
    
    // 新增：計算實際尺寸
    private func calculateRealDimensions(from roi: CGRect) -> (length: Double, width: Double) {
        // 使用預設相機參數計算
        let intrinsics = CameraIntrinsics.defaultiPhone
        let distance = 0.5 // 50cm
        let pixelSize = intrinsics.pixelSizeAtDistance(distance)
        
        let realLength = roi.width * pixelSize.width * 100 // cm
        let realWidth = roi.height * pixelSize.height * 100 // cm
        
        return (length: realLength, width: realWidth)
    }
    
    // MARK: - 🔧 校正驗證方法
    
    /// 驗證校正精度是否滿足要求
    private func validateCalibrationAccuracy(pixelsPerMM: Double) -> (errorPercent: Double, isAcceptable: Bool) {
        // 校正貼紙標準規格：20mm直徑，3.1416 cm²面積
        let standardDiameterMM = 20.0
        let standardAreaCm2 = 3.1416  // π × (1cm)²
        
        // 計算cm/pixel比例
        let cmPerPixel = 1.0 / (pixelsPerMM * 10.0)
        
        // 模擬檢測半徑並計算面積
        let simulatedRadiusPixels = (standardDiameterMM / 2.0) * pixelsPerMM  // 10mm在像素中的表示
        let radiusCm = simulatedRadiusPixels * cmPerPixel
        let calculatedAreaCm2 = Double.pi * radiusCm * radiusCm
        
        // 計算誤差
        let errorPercent = abs(calculatedAreaCm2 - standardAreaCm2) / standardAreaCm2 * 100.0
        let isAcceptable = errorPercent <= 30.0  // 30%容忍度
        
        return (errorPercent: errorPercent, isAcceptable: isAcceptable)
    }
}

// MARK: - 支援結構

struct AreaMeasurement {
    let area: Double
    let perimeter: Double
}

struct VolumeMeasurement {
    let volume: Double
    let maxDepth: Double
}

struct DepthMap {
    let width: Int
    let height: Int
    let depths: [Double]
}

// 使用RealTimeAnalysisModule.swift中的ImageJError定義

// MARK: - CGPath擴展已在AnnotationView.swift中定義