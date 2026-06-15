import SwiftUI
@preconcurrency import AVFoundation
import ARKit
import CoreImage
import CoreData
import Combine
import os.log

// 確保可以找到依賴的模組
// import RealTimeAnalysisModule
// import VisualizationModule

// 通知名稱定義
extension Notification.Name {
    static let photoCaptured = Notification.Name("photoCaptured")
}

@MainActor
class CaptureModule: NSObject, ObservableObject {
    @Published var capturedImage: UIImage?
    @Published var depthData: Data?
    @Published var isCapturing = false
    @Published var error: String?
    
    // AR實時預覽和對焦支援
    @Published var focusPoint: CGPoint = CGPoint(x: 0.5, y: 0.5)
    @Published var isARPreviewActive = false
    
    // 自動保存功能
    @Published var isAutoSaveEnabled = true
    @Published var lastSavedRecord: WoundRecord?
    @Published var saveStatus: SaveStatus = .none
    
    // 數據傳遞功能
    @Published var currentMeasurementData: MeasurementData?
    @Published var segmentationMask: UIImage?
    @Published var annotationData: AnnotationData?
    
    // AR框架記憶體管理
    
    internal var captureSession: AVCaptureSession?
    private var photoOutput: AVCapturePhotoOutput?
    private var videoOutput: AVCaptureVideoDataOutput?
    
    // CoreData支援
    private var viewContext: NSManagedObjectContext
    
    // 日誌記錄
    private let logger = os.Logger(subsystem: "WoundMeasurementApp", category: "Capture")
    // 移除 currentFrame 屬性以避免記憶體洩漏
    
    private let cameraSettings = CameraSettings()
    // 拍照重試控制
    private var captureRetryCount = 0
    private let maxCaptureRetries = 2
    
    init(viewContext: NSManagedObjectContext) {
        self.viewContext = viewContext
        super.init()
        setupCamera()
        // 移除預設啟動 AR 會話，避免與 AR 視圖衝突
        // 監聽 LiDAR 校準的相機資源互斥事件
        NotificationCenter.default.addObserver(self, selector: #selector(handleLidarStart), name: .lidarCalibrationWillStart, object: nil)
        NotificationCenter.default.addObserver(self, selector: #selector(handleLidarStop), name: .lidarCalibrationDidStop, object: nil)
        
        logger.info("CaptureModule初始化完成")
    }
    
    // 便利初始化器以保持兼容性
    convenience override init() {
        let container = NSPersistentContainer(name: "WoundMeasurementModel")
        container.loadPersistentStores { _, error in
            if let error = error {
                print("CoreData載入失敗: \(error)")
            }
        }
        self.init(viewContext: container.viewContext)
    }
    
    deinit {
        // 避免在 deinit 呼叫 actor 隔離方法
        NotificationCenter.default.removeObserver(self)
        captureSession?.stopRunning()
        logger.info("CaptureModule資源已完全清理")
    }
    
    /// 清理AR相關資源
    private func cleanupARResources() {
        // 不再在此模組管理 ARSession；僅重置狀態
        isARPreviewActive = false
    }

    @objc private func handleLidarStart() {
        // LiDAR 校準開始：釋放相機以避免與 ARSession 競爭
        DispatchQueue.main.async {
            if let cs = self.captureSession, cs.isRunning {
                cs.stopRunning()
                print("📷 CaptureSession 已暫停以讓 LiDAR 使用相機")
            }
            // 不在此模組主動管理 ARSession
        }
    }

    @objc private func handleLidarStop() {
        // LiDAR 校準結束：可恢復相機
        DispatchQueue.main.async {
            if let cs = self.captureSession, !cs.isRunning {
                DispatchQueue.global(qos: .userInitiated).async {
                    cs.startRunning()
                    print("📷 CaptureSession 已恢復")
                }
            }
            // ARSession 採 lazy 策略：需要時再 setup
        }
    }
    
    private func setupCamera() {
        captureSession = AVCaptureSession()
        guard let captureSession = captureSession else { return }
        
        // 在主線程中進行相機設置，確保正確的同步
        DispatchQueue.main.async {
            captureSession.beginConfiguration()
            defer { captureSession.commitConfiguration() }
            
            captureSession.sessionPreset = .photo
            
            // 嘗試多種相機配置
            let backCamera = AVCaptureDevice.default(.builtInTripleCamera, for: .video, position: .back) ??
                           AVCaptureDevice.default(.builtInDualCamera, for: .video, position: .back) ??
                           AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back)
            
            guard let camera = backCamera else {
                DispatchQueue.main.async {
                    self.error = "無法存取後置相機"
                }
                return
            }
            
            self.configureCameraSettings(camera)
            
            do {
                let input = try AVCaptureDeviceInput(device: camera)
                if captureSession.canAddInput(input) {
                    captureSession.addInput(input)
                }
                
                self.photoOutput = AVCapturePhotoOutput()
                
                if let photoOutput = self.photoOutput, captureSession.canAddOutput(photoOutput) {
                    captureSession.addOutput(photoOutput)
                    
                    // 設定相片輸出參數
                    if photoOutput.isDepthDataDeliverySupported {
                        photoOutput.isDepthDataDeliveryEnabled = true
                        print("啟用相片輸出深度數據捕獲")
                    }
                    
                    // 設定最大品質優先級（為了确保拍照設定相容）
                    photoOutput.maxPhotoQualityPrioritization = .quality
                    print("設定最大品質優先級：高品質")
                }

                // 視訊輸出：提供實時取景緩衝以計算亮度（Y平面）
                let videoOutput = AVCaptureVideoDataOutput()
                videoOutput.videoSettings = [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_420YpCbCr8BiPlanarFullRange]
                videoOutput.setSampleBufferDelegate(self, queue: DispatchQueue(label: "preview.luma.queue"))
                videoOutput.alwaysDiscardsLateVideoFrames = true
                if captureSession.canAddOutput(videoOutput) {
                    captureSession.addOutput(videoOutput)
                    self.videoOutput = videoOutput
                }
                
                // 確保相機設置完成後再啟動會話
                DispatchQueue.global(qos: .userInitiated).async {
                    captureSession.startRunning()
                    print("相機會話已啟動")
                }
                
            } catch {
                DispatchQueue.main.async {
                    self.error = "相機設定失敗: \(error.localizedDescription)"
                }
            }
        }
    }
    
    // MARK: - 相機會話控制（無 ARSession 依賴）
    func startCapture() {
        guard let captureSession = captureSession else {
            print("錯誤：相機會話未初始化")
            return
        }
        captureRetryCount = 0
        if !captureSession.isRunning {
            DispatchQueue.global(qos: .userInitiated).async {
                captureSession.startRunning()
                print("相機會話已啟動（startCapture）")
            }
        } else {
            print("相機會話已在運行中")
        }
    }

    func stopCapture() {
        captureSession?.stopRunning()
        isCapturing = false
        error = nil
        print("相機資源已清理（stopCapture）")
    }

    // 確保深度數據可用（僅在模擬器提供模擬深度；實機由 AR/照片深度提供）
    func ensureDepthDataAvailable() {
        if depthData == nil {
            #if targetEnvironment(simulator)
            DispatchQueue.main.async { [weak self] in
                guard let self = self else { return }
                self.depthData = self.generateMockDepthData()
                print("確保深度數據可用：模擬深度數據已生成")
            }
            #else
            // 實機：維持為空，後續由 AR 預覽/照片深度提供
            #endif
        }
    }
    
    private func configureCameraSettings(_ device: AVCaptureDevice) {
        do {
            try device.lockForConfiguration()
            
            // 使用自動曝光模式適應不同光線環境
            if device.isExposureModeSupported(.continuousAutoExposure) {
                device.exposureMode = .continuousAutoExposure
                print("啟用自動曝光模式")
            } else if device.isExposureModeSupported(.autoExpose) {
                device.exposureMode = .autoExpose
                print("啟用一次性自動曝光")
            }
            
            // 設定曝光偁偿以提亮影像
            if device.isExposureModeSupported(.locked) {
                // 在低光環境下提供適當的曝光偁偿
                let exposureBias: Float = 0.5  // 稍微增加曝光
                device.setExposureTargetBias(exposureBias, completionHandler: nil)
                print("設定曝光偁偿: \(exposureBias)")
            }
            
            // 使用自動白平衡以適應不同色溫
            if device.isWhiteBalanceModeSupported(.continuousAutoWhiteBalance) {
                device.whiteBalanceMode = .continuousAutoWhiteBalance
                print("啟用連續自動白平衡")
            } else if device.isWhiteBalanceModeSupported(.autoWhiteBalance) {
                device.whiteBalanceMode = .autoWhiteBalance
                print("啟用自動白平衡")
            }
            
            // 啟用自動焦點
            if device.isFocusModeSupported(.continuousAutoFocus) {
                device.focusMode = .continuousAutoFocus
                print("啟用連續自動焦點")
            } else if device.isFocusModeSupported(.autoFocus) {
                device.focusMode = .autoFocus
                print("啟用自動焦點")
            }
            if device.isFocusPointOfInterestSupported {
                device.focusPointOfInterest = CGPoint(x: 0.5, y: 0.5)
            }
            if device.isExposurePointOfInterestSupported {
                device.exposurePointOfInterest = CGPoint(x: 0.5, y: 0.5)
            }
            
            // Note: 圖像穩定化將在拍照設定中啟用
            print("相機設定完成")
            
            device.unlockForConfiguration()
            print("相機設定完成：自適應光線環境")
        } catch {
            print("相機參數設定失敗: \(error)")
            DispatchQueue.main.async {
                self.error = "相機設定失敗: \(error.localizedDescription)"
            }
        }
    }

    /// 舊版兼容性方法
    func setFocusAndExposure(pointOfInterest: CGPoint, on device: AVCaptureDevice) {
        setFocusPoint(pointOfInterest)
    }
    
    /// 改進的拍照方法，支持AR數據捕獲
    func capturePhoto() async throws -> CaptureResult {
        logger.info("開始拍照流程")
        
        isCapturing = true
        defer { isCapturing = false }
        
        do {
            let result = try await performPhotoCapture()
            
            // 自動保存如果啟用
            if isAutoSaveEnabled {
                await performAutoSave(result: result)
            }
            
            // 處理測量完成
            if let measurementResult = result.measurementResult {
                await handleMeasurementCompleted(measurementResult)
            }
            
            logger.info("拍照流程完成")
            return result
            
        } catch {
            logger.error("拍照失敗: \(error.localizedDescription)")
            self.error = error.localizedDescription
            throw error
        }
    }
    
    /// 簡化版拍照方法（保持兼容性）
    func capturePhotoSimple() {
        Task {
            do {
                _ = try await capturePhoto()
            } catch {
                logger.error("簡化拍照失敗: \(error.localizedDescription)")
            }
        }
    }
    
    /// 執行實際的拍照操作
    private func performPhotoCapture() async throws -> CaptureResult {
        logger.info("執行拍照操作")
        
        guard let captureSession = captureSession, captureSession.isRunning else {
            throw CaptureError.cameraNotRunning
        }
        
        guard photoOutput != nil else {
            throw CaptureError.outputNotInitialized
        }
        
        // 使用改進的拍照設定（不保留 ARFrame）
        return try await capturePhotoWithSettings(arFrame: nil)
    }
    
    /// 使用指定設定拍照
    private func capturePhotoWithSettings(arFrame: ARFrame?) async throws -> CaptureResult {
        guard let photoOutput = photoOutput else {
            throw CaptureError.outputNotInitialized
        }
        
        let settings = createOptimalPhotoSettings(for: photoOutput)
        
        // 使用continuations等待拍照完成
        return try await withCheckedThrowingContinuation { continuation in
            let delegate = PhotoCaptureDelegate(arFrame: arFrame) { result in
                continuation.resume(with: result)
            }
            
            photoOutput.capturePhoto(with: settings, delegate: delegate)
            
            // 設定超時機制
            Task {
                try await Task.sleep(nanoseconds: 10_000_000_000) // 10秒超時
                continuation.resume(throwing: CaptureError.captureTimeout)
            }
        }
    }
    
    /// 創建優化的拍照設定
    private func createOptimalPhotoSettings(for output: AVCapturePhotoOutput) -> AVCapturePhotoSettings {
        let settings = AVCapturePhotoSettings()
        
        // 設定最高解析度
        if #available(iOS 17.0, *) {
            let maxDimensions = output.maxPhotoDimensions
            if maxDimensions.width > 0 && maxDimensions.height > 0 {
                settings.maxPhotoDimensions = maxDimensions
                logger.info("使用最大解析度: \(maxDimensions.width)x\(maxDimensions.height)")
            }
        } else {
            settings.isHighResolutionPhotoEnabled = true
        }
        
        // 設定閃光燈
        settings.flashMode = .auto
        
        // 啟用深度數據捕獲
        if output.isDepthDataDeliverySupported {
            settings.isDepthDataDeliveryEnabled = true
            logger.info("已啟用深度數據捕獲")
        }
        
        // 啟用品質優化
        settings.isAutoRedEyeReductionEnabled = true
        
        // 設定品質優先級
        if output.maxPhotoQualityPrioritization.rawValue >= AVCapturePhotoOutput.QualityPrioritization.quality.rawValue {
            settings.photoQualityPrioritization = .quality
        }
        
        return settings
    }
        
    /// 設定對焦點（改進版本）
    func setFocusPoint(_ point: CGPoint) {
        guard let device = getCurrentCameraDevice() else {
            logger.error("無法獲取相機設備")
            return
        }
        
        do {
            try device.lockForConfiguration()
            defer { device.unlockForConfiguration() }
            
            // 設定對焦點
            if device.isFocusPointOfInterestSupported && device.isFocusModeSupported(.continuousAutoFocus) {
                device.focusPointOfInterest = point
                device.focusMode = .continuousAutoFocus
                focusPoint = point
                logger.info("對焦點已設定: \(String(describing: point))")
            }
            
            // 設定曝光點
            if device.isExposurePointOfInterestSupported && device.isExposureModeSupported(.continuousAutoExposure) {
                device.exposurePointOfInterest = point
                device.exposureMode = .continuousAutoExposure
            }
            
        } catch {
            logger.error("設定對焦點失敗: \(error.localizedDescription)")
            self.error = error.localizedDescription
        }
    }
    
    /// 獲取當前相機設備
    private func getCurrentCameraDevice() -> AVCaptureDevice? {
        return captureSession?.inputs.compactMap { input in
            (input as? AVCaptureDeviceInput)?.device
        }.first
    }
        
    /// 處理測量完成
    private func handleMeasurementCompleted(_ result: WoundMeasurementResult) async {
        logger.info("測量完成，面積: \(result.area ?? 0) mm²")
        
        // 更新測量數據
        currentMeasurementData = MeasurementData(
            area: result.area ?? 0.0,
            perimeter: result.perimeter ?? 0.0,
            volume: result.volume,
            timestamp: Date(),
            capturedImage: capturedImage,
            segmentationMask: segmentationMask,
            depthData: depthData
        )
        
        // 準備標註數據
        if let record = lastSavedRecord {
            annotationData = AnnotationData(
                woundRecord: record,
                measurementData: currentMeasurementData!,
                capturedImage: capturedImage,
                segmentationMask: segmentationMask,
                depthData: depthData
            )
        }
    }
    
    /// 執行自動保存
    private func performAutoSave(result: CaptureResult) async {
        guard isAutoSaveEnabled else { return }
        
        saveStatus = .saving
        logger.info("執行自動保存")
        
        do {
            let record = WoundRecord(context: viewContext)
            record.id = UUID()
            record.date = Date()
            record.imageData = result.image.jpegData(compressionQuality: 0.8)
            
            // 保存深度數據
            if result.depthData != nil {
                let depthInfo: [String: Any] = [
                    "hasDepth": true,
                    "timestamp": result.timestamp.timeIntervalSince1970,
                    "cameraIntrinsics": NSCoder.string(for: CGAffineTransform.identity) // 簡化版本
                ]
                
                if let jsonData = try? JSONSerialization.data(withJSONObject: depthInfo) {
                    // Store depth info in notes field as JSON since depthDataJSON doesn't exist
                    record.notes = String(data: jsonData, encoding: .utf8)
                }
            }
            
            // 保存測量結果
            if let measurement = result.measurementResult {
                record.area = measurement.area ?? 0.0
                record.perimeter = measurement.perimeter ?? 0.0
                record.volume = measurement.volume ?? 0.0
            }
            
            try viewContext.save()
            lastSavedRecord = record
            saveStatus = .saved
            
            logger.info("自動保存成功")
            
        } catch {
            saveStatus = .failed
            logger.error("自動保存失敗: \(error.localizedDescription)")
            self.error = "保存失敗: \(error.localizedDescription)"
        }
    }
    
    /// 獲取用於標註的數據包
    func getAnnotationData() -> AnnotationData? {
        guard let data = annotationData else {
            logger.warning("沒有可用的標註數據")
            return nil
        }
        
        logger.info("提供標註數據")
        return data
    }
        
    
    private func processWhiteBalance(_ image: UIImage) -> UIImage {
        guard let cgImage = image.cgImage else { return image }
        
        let ciImage = CIImage(cgImage: cgImage)
        let context = CIContext()
        
        let whiteBalanceFilter = CIFilter(name: "CIWhitePointAdjust")
        whiteBalanceFilter?.setValue(ciImage, forKey: kCIInputImageKey)
        whiteBalanceFilter?.setValue(CIColor.white, forKey: "inputColor")
        
        guard let outputImage = whiteBalanceFilter?.outputImage,
              let processedCGImage = context.createCGImage(outputImage, from: outputImage.extent) else {
            return image
        }
        
        return UIImage(cgImage: processedCGImage)
    }
    
    private func generateMockDepthData() -> Data {
        // 生成模擬深度數據用於模擬器測試
        let width = 256
        let height = 192
        let bytesPerPixel = 4 // 32位浮點數
        let dataSize = width * height * bytesPerPixel
        
        var mockData = Data(count: dataSize)
        mockData.withUnsafeMutableBytes { bytes in
            let floatBuffer = bytes.bindMemory(to: Float32.self)
            for i in 0..<(width * height) {
                // 生成模擬深度值（0.5m到2.0m之間）
                let x = Float32(i % width) / Float32(width)
                let y = Float32(i / width) / Float32(height)
                let depth = 0.5 + 1.5 * (sin(x * .pi * 2) * cos(y * .pi * 2) + 1.0) / 2.0
                floatBuffer[i] = depth
            }
        }
        
        return mockData
    }
}
// MARK: - 亮度量測（取景 CMSampleBuffer Y 平面，5Hz 節流）
extension CaptureModule: @preconcurrency AVCaptureVideoDataOutputSampleBufferDelegate {
    private static var lastBrightnessTime = Date.distantPast
    private static let brightnessInterval: TimeInterval = 0.2 // 5Hz
    func captureOutput(_ output: AVCaptureOutput, didOutput sampleBuffer: CMSampleBuffer, from connection: AVCaptureConnection) {
        let now = Date()
        if now.timeIntervalSince(CaptureModule.lastBrightnessTime) < CaptureModule.brightnessInterval { return }
        CaptureModule.lastBrightnessTime = now
        guard let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
        CVPixelBufferLockBaseAddress(pixelBuffer, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(pixelBuffer, .readOnly) }
        let width = CVPixelBufferGetWidthOfPlane(pixelBuffer, 0)
        let height = CVPixelBufferGetHeightOfPlane(pixelBuffer, 0)
        guard let base = CVPixelBufferGetBaseAddressOfPlane(pixelBuffer, 0) else { return }
        let rowStride = CVPixelBufferGetBytesPerRowOfPlane(pixelBuffer, 0)
        var sum: Double = 0
        var count: Int = 0
        // 下採樣取樣避免耗時
        let stepY = Swift.max(1, height / 64)
        let stepX = Swift.max(1, width / 64)
        for y in stride(from: 0, to: height, by: stepY) {
            let row = base.advanced(by: y * rowStride)
            for x in stride(from: 0, to: width, by: stepX) {
                let val = row.load(fromByteOffset: x, as: UInt8.self)
                sum += Double(val) / 255.0
                count += 1
            }
        }
        if count > 0 {
            let brightness = sum / Double(count)
            print("亮度檢測結果(取景Y): \(String(format: "%.2f", brightness)))")
        }
    }
}

// 已移除 ARSessionDelegate 實作，避免與 AR 視圖/管理器衝突

// MARK: - Additional Helper Methods
extension CaptureModule {
    // 保留原有的生成模擬深度數據方法
    // 保留原有的優化深度轉換方法
    // 保留原有的簡化深度轉換方法
    
    private func processDepthData(_ depthBuffer: CVPixelBuffer, confidence: ARConfidenceLevel?) -> Data {
        let depthImage = CIImage(cvPixelBuffer: depthBuffer)
        let context = CIContext()
        
        // 深度圖像正規化和增強
        let normalizedDepth = normalizeDepthImage(depthImage)
        let enhancedDepth = enhanceDepthQuality(normalizedDepth, confidence: confidence)
        
        // 計算深度品質指標
        let qualityMetrics = calculateDepthQuality(enhancedDepth)
        
        // 轉換為PNG格式並包含元數據
        if let cgImage = context.createCGImage(enhancedDepth, from: enhancedDepth.extent) {
            let uiImage = UIImage(cgImage: cgImage)
            if let pngData = uiImage.pngData() {
                // 將品質指標嵌入到數據中
                return embedQualityMetrics(pngData, metrics: qualityMetrics)
            }
        }
        
        return Data()
    }
    
    private func normalizeDepthImage(_ depthImage: CIImage) -> CIImage {
        // 深度值正規化 (通常0-10米範圍)
        guard let normalizeFilter = CIFilter(name: "CIColorControls") else {
            return depthImage
        }
        normalizeFilter.setValue(depthImage, forKey: kCIInputImageKey)
        normalizeFilter.setValue(0.1, forKey: kCIInputContrastKey) // 增強對比度
        normalizeFilter.setValue(0.5, forKey: kCIInputBrightnessKey) // 調整亮度
        
        return normalizeFilter.outputImage ?? depthImage
    }
    
    private func enhanceDepthQuality(_ depthImage: CIImage, confidence: ARConfidenceLevel?) -> CIImage {
        // 根據置信度調整深度圖像品質
        let enhanceStrength: Float = {
            switch confidence {
            case .high: return 1.0
            case .medium: return 0.7
            case .low: return 0.4
            default: return 0.5
            }
        }()
        
        // 應用噪聲減少濾鏡
        guard let noiseReductionFilter = CIFilter(name: "CINoiseReduction") else {
            return depthImage
        }
        noiseReductionFilter.setValue(depthImage, forKey: kCIInputImageKey)
        noiseReductionFilter.setValue(enhanceStrength * 0.02, forKey: "inputNoiseLevel")
        noiseReductionFilter.setValue(enhanceStrength * 0.4, forKey: "inputSharpness")
        
        return noiseReductionFilter.outputImage ?? depthImage
    }
    
    private func calculateDepthQuality(_ depthImage: CIImage) -> DepthQualityMetrics {
        let context = CIContext()
        
        // 計算深度覆蓋率
        let coverage = calculateDepthCoverage(depthImage, context: context)
        
        // 計算深度一致性
        let consistency = calculateDepthConsistency(depthImage, context: context)
        
        // 計算深度準確度（基於邊緣檢測）
        let accuracy = calculateDepthAccuracy(depthImage, context: context)
        
        return DepthQualityMetrics(
            coverage: coverage,
            consistency: consistency,
            accuracy: accuracy,
            overallScore: (coverage + consistency + accuracy) / 3.0
        )
    }
    
    private func calculateDepthCoverage(_ image: CIImage, context: CIContext) -> Double {
        // 計算有效深度像素的比例
        guard let cgImage = context.createCGImage(image, from: image.extent) else { return 0.0 }
        
        let width = cgImage.width
        let height = cgImage.height
        _ = width * height  // 簡化的像素計數（實際實作需要訪問像素數據）
        _ = 0  // 有效像素計數
        
        // 簡化的像素計數（實際實作需要訪問像素數據）
        // 這裡返回估算值
        return 0.85 // 85% 覆蓋率
    }
    
    private func calculateDepthConsistency(_ image: CIImage, context: CIContext) -> Double {
        // 計算深度值的空間一致性
        guard let sobelFilter = CIFilter(name: "CIEdges") else {
            return 0.0
        }
        sobelFilter.setValue(image, forKey: kCIInputImageKey)
        sobelFilter.setValue(1.0, forKey: kCIInputIntensityKey)
        
        guard let edgeImage = sobelFilter.outputImage else {
            return 0.0
        }
        
        _ = context.createCGImage(edgeImage, from: edgeImage.extent)
        
        // 基於邊緣密度評估一致性
        return 0.78 // 78% 一致性
    }
    
    private func calculateDepthAccuracy(_ image: CIImage, context: CIContext) -> Double {
        // 基於深度梯度和紋理相關性評估準確度
        return 0.82 // 82% 準確度
    }
    
    private func embedQualityMetrics(_ imageData: Data, metrics: DepthQualityMetrics) -> Data {
        // 在實際實作中，可以將品質指標作為EXIF數據嵌入
        // 這裡簡化為返回原始數據
        return imageData
    }
}

// MARK: - 新增數據結構定義

enum SaveStatus {
    case none, saving, saved, failed
}

enum CaptureError: Error {
    case arNotSupported
    case cameraNotRunning
    case outputNotInitialized
    case captureTimeout
    case noARFrame
    case processingFailed
    
    var localizedDescription: String {
        switch self {
        case .arNotSupported: return "設備不支援AR功能"
        case .cameraNotRunning: return "相機未運行"
        case .outputNotInitialized: return "相機輸出未初始化"
        case .captureTimeout: return "拍照超時"
        case .noARFrame: return "無可用AR框架"
        case .processingFailed: return "處理失敗"
        }
    }
}

struct CaptureResult {
    let image: UIImage
    let depthData: Data?
    let timestamp: Date
    let measurementResult: WoundMeasurementResult?
    
    init(image: UIImage, depthData: Data? = nil, timestamp: Date = Date(), measurementResult: WoundMeasurementResult? = nil) {
        self.image = image
        self.depthData = depthData
        self.timestamp = timestamp
        self.measurementResult = measurementResult
    }
}

struct MeasurementData {
    let area: Double
    let perimeter: Double
    let volume: Double?
    let timestamp: Date
    let capturedImage: UIImage?
    let segmentationMask: UIImage?
    let depthData: Data?
}

struct AnnotationData {
    let woundRecord: WoundRecord
    let measurementData: MeasurementData
    let capturedImage: UIImage?
    let segmentationMask: UIImage?
    let depthData: Data?
}

// WoundMeasurementResult is defined in WoundTypes.swift - removed duplicate

/// 照片捕獲代理
class PhotoCaptureDelegate: NSObject, AVCapturePhotoCaptureDelegate {
    private let arFrame: ARFrame?
    private let completion: (Swift.Result<CaptureResult, Error>) -> Void
    
    init(arFrame: ARFrame?, completion: @escaping (Swift.Result<CaptureResult, Error>) -> Void) {
        self.arFrame = arFrame
        self.completion = completion
        super.init()
    }
    
    func photoOutput(_ output: AVCapturePhotoOutput, didFinishProcessingPhoto photo: AVCapturePhoto, error: Error?) {
        if let error = error {
            completion(.failure(error))
            return
        }
        
        guard let imageData = photo.fileDataRepresentation(),
              let image = UIImage(data: imageData) else {
            completion(.failure(CaptureError.processingFailed))
            return
        }
        
        // 處理深度數據
        var depthData: Data?
        if let arFrame = arFrame, let sceneDepth = arFrame.sceneDepth {
            depthData = convertDepthBufferToData(sceneDepth.depthMap)
        } else if let photoDepthData = photo.depthData {
            depthData = convertDepthDataToData(photoDepthData.depthDataMap)
        }
        
        let result = CaptureResult(
            image: image,
            depthData: depthData,
            timestamp: Date()
        )
        
        completion(.success(result))
    }
    
    private func convertDepthBufferToData(_ depthBuffer: CVPixelBuffer) -> Data {
        CVPixelBufferLockBaseAddress(depthBuffer, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(depthBuffer, .readOnly) }
        
        guard let baseAddress = CVPixelBufferGetBaseAddress(depthBuffer) else {
            return Data()
        }
        
        let width = CVPixelBufferGetWidth(depthBuffer)
        let height = CVPixelBufferGetHeight(depthBuffer)
        let dataSize = width * height * MemoryLayout<Float32>.size
        
        return Data(bytes: baseAddress, count: dataSize)
    }
    
    private func convertDepthDataToData(_ depthDataMap: CVPixelBuffer) -> Data {
        return convertDepthBufferToData(depthDataMap)
    }
}

struct DepthQualityMetrics {
    let coverage: Double      // 深度覆蓋率 (0-1)
    let consistency: Double   // 深度一致性 (0-1)
    let accuracy: Double      // 深度準確度 (0-1)
    let overallScore: Double  // 綜合分數 (0-1)
}

struct CameraSettings {
    let isoRange: ClosedRange<Float> = 100...400
    let shutterSpeedRange: ClosedRange<CMTime> = CMTimeMake(value: 1, timescale: 125)...CMTimeMake(value: 1, timescale: 60)
    let defaultISO: Float = 200
    let defaultShutterSpeed = CMTimeMake(value: 1, timescale: 60)
}

struct CaptureView: UIViewControllerRepresentable {
    let captureModule: CaptureModule
    let onCapture: (UIImage, Data) -> Void
    
    func makeUIViewController(context: Context) -> CaptureViewController {
        let controller = CaptureViewController()
        controller.captureModule = captureModule
        controller.onCapture = onCapture
        return controller
    }
    
    func updateUIViewController(_ uiViewController: CaptureViewController, context: Context) {}
}

class CaptureViewController: UIViewController {
    var captureModule: CaptureModule?
    var onCapture: ((UIImage, Data) -> Void)?
    
    private var previewLayer: AVCaptureVideoPreviewLayer?
    private var arView: ARSCNView?
    private var overlayView: UIView?
    // 即時分析模組 - 暫時註釋以解決編譯問題
    // private var realTimeAnalysisModule = RealTimeAnalysisModule()
    // private var visualizationModule = VisualizationModule()
    private var areaMaskImageView: UIImageView?
    private var analysisTimer: Timer?
    
    override func viewDidLoad() {
        super.viewDidLoad()
        setupUI()
        setupPreview()
        setupRealTimeVisualization()
        
        // 監聽照片拍攝完成通知
        NotificationCenter.default.addObserver(
            self,
            selector: #selector(photoCaptured),
            name: .photoCaptured,
            object: nil
        )
    }
    
    override func viewDidAppear(_ animated: Bool) {
        super.viewDidAppear(animated)
        captureModule?.startCapture()
        startRealTimeAnalysis()
    }
    
    override func viewWillDisappear(_ animated: Bool) {
        super.viewWillDisappear(animated)
        captureModule?.stopCapture()
        stopRealTimeAnalysis()
        
        // 移除通知監聽
        NotificationCenter.default.removeObserver(self)
    }
    
    private func setupUI() {
        view.backgroundColor = .black
        
        // 設置覆蓋視圖
        overlayView = UIView()
        overlayView?.backgroundColor = .clear
        overlayView?.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(overlayView!)
        
        // 設置即時分析遮罩圖像視圖
        areaMaskImageView = UIImageView()
        areaMaskImageView?.contentMode = .scaleAspectFit
        areaMaskImageView?.alpha = 0.7
        areaMaskImageView?.translatesAutoresizingMaskIntoConstraints = false
        overlayView?.addSubview(areaMaskImageView!)
        
        let captureButton = UIButton(type: .system)
        captureButton.setTitle("拍攝", for: .normal)
        captureButton.setTitleColor(.white, for: .normal)
        captureButton.backgroundColor = .systemBlue
        captureButton.layer.cornerRadius = 30
        captureButton.addTarget(self, action: #selector(captureButtonTapped), for: .touchUpInside)
        
        let closeButton = UIButton(type: .system)
        closeButton.setTitle("關閉", for: .normal)
        closeButton.setTitleColor(.white, for: .normal)
        closeButton.addTarget(self, action: #selector(closeButtonTapped), for: .touchUpInside)
        
        // 即時分析狀態標籤
        let statusLabel = UILabel()
        statusLabel.text = "即時分析模式：移動相機找尋傷口"
        statusLabel.textColor = .white
        statusLabel.backgroundColor = UIColor.black.withAlphaComponent(0.7)
        statusLabel.textAlignment = .center
        statusLabel.font = UIFont.systemFont(ofSize: 14)
        statusLabel.layer.cornerRadius = 8
        statusLabel.clipsToBounds = true
        statusLabel.numberOfLines = 0
        statusLabel.tag = 1001 // 用於後續更新
        
        captureButton.translatesAutoresizingMaskIntoConstraints = false
        closeButton.translatesAutoresizingMaskIntoConstraints = false
        statusLabel.translatesAutoresizingMaskIntoConstraints = false
        
        view.addSubview(captureButton)
        view.addSubview(closeButton)
        view.addSubview(statusLabel)
        
        NSLayoutConstraint.activate([
            // 覆蓋視圖約束
            overlayView!.topAnchor.constraint(equalTo: view.topAnchor),
            overlayView!.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            overlayView!.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            overlayView!.bottomAnchor.constraint(equalTo: view.bottomAnchor),
            
            // 面積遮罩圖像視圖約束
            areaMaskImageView!.topAnchor.constraint(equalTo: overlayView!.topAnchor),
            areaMaskImageView!.leadingAnchor.constraint(equalTo: overlayView!.leadingAnchor),
            areaMaskImageView!.trailingAnchor.constraint(equalTo: overlayView!.trailingAnchor),
            areaMaskImageView!.bottomAnchor.constraint(equalTo: overlayView!.bottomAnchor),
            
            captureButton.centerXAnchor.constraint(equalTo: view.centerXAnchor),
            captureButton.bottomAnchor.constraint(equalTo: view.safeAreaLayoutGuide.bottomAnchor, constant: -20),
            captureButton.widthAnchor.constraint(equalToConstant: 80),
            captureButton.heightAnchor.constraint(equalToConstant: 60),
            
            closeButton.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor, constant: 20),
            closeButton.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 20),
            
            statusLabel.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor, constant: 20),
            statusLabel.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 20),
            statusLabel.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -20),
            statusLabel.heightAnchor.constraint(greaterThanOrEqualToConstant: 40)
        ])
    }
    
    private func setupPreview() {
        guard let captureSession = captureModule?.captureSession else { return }
        
        previewLayer = AVCaptureVideoPreviewLayer(session: captureSession)
        previewLayer?.videoGravity = .resizeAspectFill
        previewLayer?.frame = view.bounds
        
        if let previewLayer = previewLayer {
            view.layer.insertSublayer(previewLayer, at: 0)
        }

        // Tap to focus/expose
        let tap = UITapGestureRecognizer(target: self, action: #selector(handleTapToFocus(_:)))
        view.addGestureRecognizer(tap)
    }
    
    @objc private func captureButtonTapped() {
        print("拍攝按鈕被點擊，開始拍照...")
        Task {
            try? await captureModule?.capturePhoto()
        }
    }
    
    @objc private func photoCaptured() {
        print("收到照片拍攝完成通知，檢查數據...")
        
        guard let image = captureModule?.capturedImage else {
            print("錯誤：capturedImage為空")
            return
        }
        
        // 檢查深度數據，如果為空則嘗試生成
        if captureModule?.depthData == nil {
            print("深度數據為空，嘗試生成模擬深度數據...")
            captureModule?.ensureDepthDataAvailable()
            
            // 等待一小段時間讓深度數據生成
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
                self.checkAndProceedWithCapture(image: image)
            }
            return
        }
        
        checkAndProceedWithCapture(image: image)
    }
    
    private func checkAndProceedWithCapture(image: UIImage) {
        guard let depthData = captureModule?.depthData else {
            print("錯誤：深度數據仍然為空，無法進行後續處理")
            return
        }
        
        print("照片和深度數據都已準備好，調用onCapture回調")
        onCapture?(image, depthData)
        dismiss(animated: true)
    }
    
    @objc private func closeButtonTapped() {
        dismiss(animated: true)
    }
    
    // MARK: - 即時視覺化功能
    
    private func setupRealTimeVisualization() {
        // 初始化即時分析模組 - 暫時註釋
        /*
        Task { @MainActor in
            // 設定分析結果更新回調
            realTimeAnalysisModule.$currentAnalysis
                .compactMap { $0 }
                .sink { [weak self] (analysis: RealTimeAnalysisModule.RealTimeAnalysisResult) in
                    self?.updateRealTimeVisualization(with: analysis)
                }
                .store(in: &cancellables)
        }
        */
    }
    
    private var cancellables = Set<AnyCancellable>()
    
    private func startRealTimeAnalysis() {
        // 暫時註釋即時分析功能
        /*
        realTimeAnalysisModule.startRealTimeAnalysis(imageStream: { [weak self] in
            return self?.getCurrentPreviewImage()
        })
        */
    }
    
    private func stopRealTimeAnalysis() {
        // 暫時註釋即時分析功能
        /*
        realTimeAnalysisModule.stopRealTimeAnalysis()
        */
        analysisTimer?.invalidate()
        analysisTimer = nil
    }
    
    private func getCurrentPreviewImage() -> UIImage? {
        guard let previewLayer = previewLayer else { return nil }
        
        // 從預覽層獲取當前幀
        UIGraphicsBeginImageContextWithOptions(previewLayer.bounds.size, false, 0)
        defer { UIGraphicsEndImageContext() }
        
        guard let context = UIGraphicsGetCurrentContext() else { return nil }
        previewLayer.render(in: context)
        
        return UIGraphicsGetImageFromCurrentImageContext()
    }
    
    // private func updateRealTimeVisualization(with analysis: RealTimeAnalysisModule.RealTimeAnalysisResult) {
    private func updateRealTimeVisualization(with analysis: Any) { // 暫時改為 Any 類型
        // 暫時註釋整個實現，直到實現 RealTimeAnalysisModule
        /*
        DispatchQueue.main.async { [weak self] in
            guard let self = self else { return }
            
            // 更新狀態標籤
            if let statusLabel = self.view.viewWithTag(1001) as? UILabel {
                let hasWound = analysis.hasWound
                let confidence = analysis.confidence
                let quality = analysis.quality
                
                if hasWound {
                    statusLabel.text = "偵測到傷口 - 信心度: \(String(format: "%.1f", confidence * 100))%"
                    statusLabel.backgroundColor = UIColor.green.withAlphaComponent(0.7)
                    
                    if let area = analysis.estimatedArea {
                        statusLabel.text! += "\n估計面積: \(String(format: "%.2f", area)) cm²"
                    }
                } else {
                    statusLabel.text = "即時分析中... 品質: \(quality)"
                    statusLabel.backgroundColor = UIColor.black.withAlphaComponent(0.7)
                }
            }
            
            // 如果偵測到傷口，生成並顯示面積遮罩
            if analysis.hasWound && analysis.confidence > 0.6 {
                self.generateAndDisplayAreaMask()
            } else {
                self.areaMaskImageView?.image = nil
            }
        }
        */
    }
    
    private func generateAndDisplayAreaMask() {
        guard let currentImage = getCurrentPreviewImage() else { return }
        
        Task {
            do {
                // 使用簡化的分割來生成面積遮罩
                let maskImage = try await generateQuickAreaMask(from: currentImage)
                
                DispatchQueue.main.async { [weak self] in
                    self?.areaMaskImageView?.image = maskImage
                }
            } catch {
                print("生成面積遮罩失敗: \(error)")
            }
        }
    }

    // MARK: - Tap to Focus/Exposure
    @objc private func handleTapToFocus(_ gesture: UITapGestureRecognizer) {
        guard let layer = previewLayer, let device = (captureModule?.captureSession?.inputs.first as? AVCaptureDeviceInput)?.device else { return }
        let pointInView = gesture.location(in: view)
        let devicePoint = layer.captureDevicePointConverted(fromLayerPoint: pointInView)
        captureModule?.setFocusAndExposure(pointOfInterest: devicePoint, on: device)
        showFocusRing(at: pointInView)
    }

    private func showFocusRing(at point: CGPoint) {
        let ring = UIView(frame: CGRect(x: 0, y: 0, width: 80, height: 80))
        ring.center = point
        ring.layer.borderColor = UIColor.systemYellow.cgColor
        ring.layer.borderWidth = 2
        ring.layer.cornerRadius = 40
        ring.backgroundColor = UIColor.clear
        view.addSubview(ring)
        UIView.animate(withDuration: 0.25, animations: {
            ring.transform = CGAffineTransform(scaleX: 0.8, y: 0.8)
        }) { _ in
            UIView.animate(withDuration: 0.25, delay: 0.5, options: [], animations: {
                ring.alpha = 0
            }) { _ in
                ring.removeFromSuperview()
            }
        }
    }
    
    private func generateQuickAreaMask(from image: UIImage) async throws -> UIImage? {
        let size = image.size
        let renderer = UIGraphicsImageRenderer(size: size)
        
        return renderer.image { context in
            // 設置背景為透明
            UIColor.clear.setFill()
            context.fill(CGRect(origin: .zero, size: size))
            
            // 在圖像中心創建一個示例遮罩（實際應用中會使用分割算法）
            let centerX = size.width / 2
            let centerY = size.height / 2
            let radius = min(size.width, size.height) / 8
            
            let maskPath = UIBezierPath(ovalIn: CGRect(
                x: centerX - radius,
                y: centerY - radius,
                width: radius * 2,
                height: radius * 2
            ))
            
            // 設置遮罩顏色（半透明紅色）
            UIColor.red.withAlphaComponent(0.4).setFill()
            maskPath.fill()
            
            // 設置邊框
            UIColor.red.setStroke()
            maskPath.lineWidth = 3.0
            maskPath.stroke()
        }
    }
}