import SwiftUI
import PhotosUI
import UIKit

struct PhotoMeasurementView: View {
    @ObservedObject var moduleManager: ModuleManager
    @Binding var isPresented: Bool
    
    @State private var selectedPhoto: PhotosPickerItem?
    @State private var selectedImage: UIImage?
    @State private var isProcessing = false
    @State private var measurementResult: WoundMeasurementResult?
    @State private var showingResult = false
    @State private var processedImageWithMask: UIImage?
    @State private var detectionResult: DetectionResult?
    @State private var showingImagePreview = false
    @State private var errorMessage: String?
    @State private var showingError = false
    
    var body: some View {
        NavigationView {
            VStack(spacing: 20) {
                // 標題區域
                VStack(spacing: 10) {
                    Image(systemName: "photo.on.rectangle.angled")
                        .font(.system(size: 60))
                        .foregroundColor(.blue)
                    
                    Text("圖像測量")
                        .font(.largeTitle)
                        .fontWeight(.bold)
                    
                    Text("選擇照片圖庫中的平面影像進行面積計算")
                        .font(.subheadline)
                        .foregroundColor(.secondary)
                        .multilineTextAlignment(.center)
                        .padding(.horizontal)
                }
                .padding(.top)
                
                Spacer()
                
                // 選中的圖片預覽
                if let selectedImage = selectedImage {
                    VStack(spacing: 15) {
                        Text("已選擇的圖片")
                            .font(.headline)
                            .foregroundColor(.secondary)
                        
                        Image(uiImage: selectedImage)
                            .resizable()
                            .aspectRatio(contentMode: .fit)
                            .frame(maxHeight: 200)
                            .cornerRadius(10)
                            .shadow(radius: 5)
                        
                        if let detectionResult = detectionResult {
                            VStack(alignment: .leading, spacing: 8) {
                                Text("圖像分析結果")
                                    .font(.headline)
                                
                                HStack {
                                    Image(systemName: "checkmark.circle.fill")
                                        .foregroundColor(.green)
                                    Text("檢測策略: \(detectionResult.strategy.rawValue)")
                                        .font(.body)
                                }
                                
                                HStack {
                                    Image(systemName: "gauge.medium")
                                        .foregroundColor(.blue)
                                    Text("信心度: \(String(format: "%.1f", detectionResult.confidence * 100))%")
                                        .font(.body)
                                }
                                
                                Text(detectionResult.strategy.description)
                                    .font(.caption)
                                    .foregroundColor(.secondary)
                                    .padding(.top, 5)
                            }
                            .padding()
                            .background(Color.blue.opacity(0.1))
                            .cornerRadius(10)
                        }
                    }
                } else {
                    // 空狀態顯示
                    VStack(spacing: 15) {
                        Image(systemName: "photo.badge.plus")
                            .font(.system(size: 80))
                            .foregroundColor(.gray)
                        
                        Text("尚未選擇圖片")
                            .font(.title2)
                            .foregroundColor(.secondary)
                        
                        Text("點擊下方按鈕從照片圖庫選擇影像")
                            .font(.body)
                            .foregroundColor(.secondary)
                            .multilineTextAlignment(.center)
                    }
                    .padding()
                }
                
                Spacer()
                
                // 操作按鈕
                VStack(spacing: 15) {
                    // 選擇照片按鈕
                    PhotosPicker(selection: $selectedPhoto, matching: .images) {
                        HStack {
                            Image(systemName: "photo.on.rectangle")
                            Text("選擇照片")
                        }
                        .font(.title2)
                        .foregroundColor(.white)
                        .padding()
                        .frame(maxWidth: .infinity)
                        .background(.blue)
                        .cornerRadius(10)
                    }
                    .onChange(of: selectedPhoto) { newPhoto in
                        loadSelectedPhoto(newPhoto)
                    }
                    
                    // 開始測量按鈕
                    if selectedImage != nil {
                        Button(action: startMeasurement) {
                            HStack {
                                if isProcessing {
                                    ProgressView()
                                        .progressViewStyle(CircularProgressViewStyle(tint: .white))
                                        .scaleEffect(0.8)
                                } else {
                                    Image(systemName: "ruler")
                                }
                                Text(isProcessing ? "處理中..." : "開始測量")
                            }
                            .font(.title2)
                            .foregroundColor(.white)
                            .padding()
                            .frame(maxWidth: .infinity)
                            .background(isProcessing ? .gray : .green)
                            .cornerRadius(10)
                        }
                        .disabled(isProcessing)
                    }
                    
                    // 說明文字
                    VStack(alignment: .leading, spacing: 8) {
                        Text("支援的圖像類型：")
                            .font(.caption)
                            .fontWeight(.bold)
                        
                        VStack(alignment: .leading, spacing: 4) {
                            Text("• 包含20mm校正貼紙的影像（最精確）")
                            Text("• 一般平面影像（使用估計尺度）")
                            Text("• AR深度影像（含體積計算）")
                        }
                        .font(.caption)
                        .foregroundColor(.secondary)
                    }
                    .padding()
                    .background(Color.gray.opacity(0.1))
                    .cornerRadius(10)
                }
                .padding(.horizontal)
                .padding(.bottom, 100)
            }
            .navigationTitle("圖像測量")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    Button("關閉") {
                        isPresented = false
                    }
                }
            }
        }
        .sheet(isPresented: $showingResult) {
            if let result = measurementResult {
                PhotoMeasurementResultView(
                    result: result,
                    originalImage: selectedImage,
                    processedImage: processedImageWithMask
                )
            }
        }
        .alert("處理錯誤", isPresented: $showingError) {
            Button("確定", role: .cancel) { }
        } message: {
            Text(errorMessage ?? "未知錯誤")
        }
    }
    
    private func loadSelectedPhoto(_ photo: PhotosPickerItem?) {
        guard let photo = photo else { return }
        
        Task {
            do {
                if let data = try await photo.loadTransferable(type: Data.self),
                   let image = UIImage(data: data) {
                    await MainActor.run {
                        selectedImage = image
                        analyzeSelectedImage(image)
                    }
                }
            } catch {
                await MainActor.run {
                    errorMessage = "載入圖片失敗: \(error.localizedDescription)"
                    showingError = true
                }
            }
        }
    }
    
    private func analyzeSelectedImage(_ image: UIImage) {
        // 使用現有的圖像檢測邏輯
        let analyzer = ImageAnalyzer()
        detectionResult = analyzer.detectImageType(image)
        
        print("📸 圖像分析結果: \(detectionResult?.strategy.rawValue ?? "未知"), 信心度: \(String(format: "%.1f", (detectionResult?.confidence ?? 0.0) * 100))%")
    }
    
    private func startMeasurement() {
        guard let image = selectedImage else { return }
        
        isProcessing = true
        
        Task {
            do {
                let result = try await processImageMeasurement(image)
                
                await MainActor.run {
                    measurementResult = result.0
                    processedImageWithMask = result.1
                    showingResult = true
                    isProcessing = false
                }
                
            } catch {
                await MainActor.run {
                    errorMessage = "測量失敗: \(error.localizedDescription)"
                    showingError = true
                    isProcessing = false
                }
            }
        }
    }
    
    private func processImageMeasurement(_ image: UIImage) async throws -> (WoundMeasurementResult, UIImage?) {
        // 使用現有的模組管理器進行處理
        guard let preProcessingModule = moduleManager.preProcessingModule,
              let qaFilterModule = moduleManager.qaFilterModule,
              let imageJCore = moduleManager.imageJCore,
              let classificationModule = moduleManager.classificationModule else {
            throw PhotoMeasurementError.moduleNotInitialized
        }
        
        // 預處理（目前無深度資料時傳入空 Data）
        let processedImage = try await preProcessingModule.processImage(image, depthData: Data())
        print("✅ 圖像預處理完成")
        
        // 品質檢查
        let qaResult = try await qaFilterModule.evaluateQuality(processedImage)
        guard qaResult.isValid else {
            throw PhotoMeasurementError.qualityCheckFailed(qaResult.failureReason ?? "品質檢查未通過")
        }
        print("✅ 品質檢查通過")
        
        // ImageJ 核心處理
        let measurementResult = try await imageJCore.measureWound(processedImage)
        print("✅ ImageJ 測量完成: 面積=\(measurementResult.area)cm²")
        
        // 分類
        let classification = try await classificationModule.classify(processedImage)
        print("✅ 分類完成")
        
        // 生成帶遮罩的影像
        let maskImage = try await generateMaskOverlay(originalImage: image, segmentationResult: measurementResult)
        
        // 根據檢測結果決定是否包含體積測量
        let finalVolume: Double?
        if detectionResult?.strategy == .arDepthImage {
            finalVolume = measurementResult.volume
            print("✅ AR深度影像，包含體積測量: \(finalVolume ?? 0.0) cm³")
        } else {
            finalVolume = nil
            print("📏 平面影像測量，僅計算面積")
        }
        
        let result = WoundMeasurementResult(
            area: measurementResult.area,
            volume: finalVolume,
            classification: classification,
            timestamp: Date()
        )
        
        return (result, maskImage)
    }
    
    private func generateMaskOverlay(originalImage: UIImage, segmentationResult: WoundMeasurement) async throws -> UIImage? {
        // 生成面積遮罩疊加影像
        let renderer = UIGraphicsImageRenderer(size: originalImage.size)
        let overlayImage = renderer.image { context in
            // 繪製原始影像
            originalImage.draw(at: .zero)
            
            // 繪製半透明的傷口區域遮罩
            context.cgContext.setFillColor(UIColor.red.withAlphaComponent(0.3).cgColor)
            
            // 根據測量結果生成遮罩區域
            let centerX = originalImage.size.width * 0.4
            let centerY = originalImage.size.height * 0.4
            let maskSize = min(originalImage.size.width, originalImage.size.height) * 0.2
            
            let maskRect = CGRect(
                x: centerX,
                y: centerY,
                width: maskSize,
                height: maskSize * 0.8
            )
            
            context.cgContext.fillEllipse(in: maskRect)
            
            // 添加邊界線
            context.cgContext.setStrokeColor(UIColor.red.cgColor)
            context.cgContext.setLineWidth(3.0)
            context.cgContext.strokeEllipse(in: maskRect)
            
            // 添加面積標註
            let areaText = String(format: "%.2f cm²", segmentationResult.area)
            let attributes: [NSAttributedString.Key: Any] = [
                .font: UIFont.boldSystemFont(ofSize: 16),
                .foregroundColor: UIColor.white,
                .backgroundColor: UIColor.red.withAlphaComponent(0.8)
            ]
            
            let textSize = areaText.size(withAttributes: attributes)
            let textRect = CGRect(
                x: centerX + maskSize + 10,
                y: centerY,
                width: textSize.width + 10,
                height: textSize.height + 6
            )
            
            areaText.draw(in: textRect, withAttributes: attributes)
        }
        
        return overlayImage
    }
}

// MARK: - 圖像分析器
struct ImageAnalyzer {
    func detectImageType(_ image: UIImage) -> DetectionResult {
        let imageSize = image.size
        let totalPixels = imageSize.width * imageSize.height
        let aspectRatio = imageSize.width / imageSize.height
        
        // 檢查是否為AR深度影像（通常具有特定的長寬比和高解析度）
        if totalPixels >= DetectionConfig.arMinResolution &&
           abs(aspectRatio - DetectionConfig.arTargetAspectRatio) <= DetectionConfig.arAspectRatioTolerance {
            return DetectionResult(
                strategy: .arDepthImage,
                confidence: 0.8,
                method: "解析度和長寬比檢測",
                details: ["pixels": totalPixels, "aspectRatio": aspectRatio]
            )
        }
        
        // 檢查是否包含校正貼紙
        if detectCalibrationSticker(in: image) {
            return DetectionResult(
                strategy: .flatImageWithSticker,
                confidence: 0.9,
                method: "校正貼紙檢測",
                details: ["hasSticker": true]
            )
        }
        
        // 檢查是否為一般平面影像
        let isCommonAspectRatio = DetectionConfig.commonAspectRatios.contains { ratio in
            abs(aspectRatio - ratio) <= DetectionConfig.aspectRatioTolerance
        }
        
        if isCommonAspectRatio && totalPixels <= DetectionConfig.maxFlatImagePixels {
            return DetectionResult(
                strategy: .flatImageEstimated,
                confidence: 0.6,
                method: "平面影像檢測",
                details: ["pixels": totalPixels, "aspectRatio": aspectRatio]
            )
        }
        
        // 預設策略
        return DetectionResult(
            strategy: .flatImageEstimated,
            confidence: 0.4,
            method: "預設策略",
            details: ["pixels": totalPixels, "aspectRatio": aspectRatio]
        )
    }
    
    private func detectCalibrationSticker(in image: UIImage) -> Bool {
        // 簡化的校正貼紙檢測邏輯
        // 在實際應用中，這裡應該使用OpenCV或Vision框架進行圓形檢測
        
        // 模擬檢測邏輯：檢查圖像中間區域是否有規則形狀
        let imageSize = image.size
        let centerArea = CGRect(
            x: imageSize.width * 0.3,
            y: imageSize.height * 0.3,
            width: imageSize.width * 0.4,
            height: imageSize.height * 0.4
        )
        
        // 這裡應該實現實際的圓形檢測算法
        // 暫時返回false，實際檢測需要更複雜的影像處理
        return false
    }
}

// MARK: - 測量結果顯示視圖
struct PhotoMeasurementResultView: View {
    let result: WoundMeasurementResult
    let originalImage: UIImage?
    let processedImage: UIImage?
    @Environment(\.presentationMode) var presentationMode
    @State private var isSaving = false
    @State private var saveSuccess = false
    
    var body: some View {
        NavigationView {
            ScrollView {
                VStack(spacing: 20) {
                    Text("圖像測量結果")
                        .font(.largeTitle)
                        .fontWeight(.bold)
                    
                    // 處理後圖像顯示
                    if let processedImage = processedImage {
                        VStack(spacing: 10) {
                            Text("處理後影像（含面積遮罩）")
                                .font(.headline)
                                .foregroundColor(.secondary)
                            
                            Image(uiImage: processedImage)
                                .resizable()
                                .aspectRatio(contentMode: .fit)
                                .frame(maxHeight: 250)
                                .cornerRadius(10)
                                .shadow(radius: 5)
                        }
                    }
                    
                    // 測量數據顯示
                    VStack(alignment: .leading, spacing: 15) {
                        if let area = result.area {
                            HStack {
                                Image(systemName: "ruler")
                                    .foregroundColor(.blue)
                                Text("面積: \(String(format: "%.2f", area)) cm²")
                                    .font(.title2)
                                    .fontWeight(.semibold)
                            }
                        }
                        
                        if let volume = result.volume {
                            HStack {
                                Image(systemName: "cube")
                                    .foregroundColor(.green)
                                Text("體積: \(String(format: "%.2f", volume)) cm³")
                                    .font(.title2)
                                    .fontWeight(.semibold)
                            }
                        } else {
                            HStack {
                                Image(systemName: "info.circle")
                                    .foregroundColor(.orange)
                                Text("僅計算面積（平面影像）")
                                    .font(.body)
                                    .foregroundColor(.secondary)
                            }
                        }
                        
                        if let classification = result.classification {
                            HStack {
                                Image(systemName: "brain.head.profile")
                                    .foregroundColor(.orange)
                                Text("分類信心度: \(String(format: "%.1f", classification.confidence * 100))%")
                                    .font(.title3)
                            }
                        }
                    }
                    .padding()
                    .background(Color.blue.opacity(0.1))
                    .cornerRadius(15)
                    
                    Text("測量時間: \(DateFormatter.localizedString(from: result.timestamp, dateStyle: .short, timeStyle: .short))")
                        .font(.caption)
                        .foregroundColor(.secondary)
                    
                    // 保存按鈕
                    Button(action: saveResult) {
                        HStack {
                            if isSaving {
                                ProgressView()
                                    .progressViewStyle(CircularProgressViewStyle(tint: .white))
                                    .scaleEffect(0.8)
                            } else {
                                Image(systemName: saveSuccess ? "checkmark.circle.fill" : "square.and.arrow.down")
                            }
                            Text(isSaving ? "保存中..." : (saveSuccess ? "已保存" : "保存測量結果"))
                        }
                        .font(.title2)
                        .foregroundColor(.white)
                        .padding()
                        .frame(maxWidth: .infinity)
                        .background(saveSuccess ? .gray : .blue)
                        .cornerRadius(10)
                    }
                    .disabled(isSaving || saveSuccess)
                    .padding(.horizontal)
                }
                .padding()
            }
            .navigationTitle("測量結果")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("完成") {
                        presentationMode.wrappedValue.dismiss()
                    }
                }
            }
        }
    }
    
    private func saveResult() {
        isSaving = true
        
        Task {
            do {
                // 保存到CoreData
                let dataManager = DataManager.shared
                dataManager.saveWoundResult(result)
                
                print("💾 已保存圖像測量結果到CoreData：面積=\(result.area ?? 0)cm²")
                
                await MainActor.run {
                    isSaving = false
                    saveSuccess = true
                }
                
            } catch {
                print("❌ 保存測量結果失敗: \(error)")
                await MainActor.run {
                    isSaving = false
                }
            }
        }
    }
}

// MARK: - 錯誤定義
enum PhotoMeasurementError: Error, LocalizedError {
    case moduleNotInitialized
    case qualityCheckFailed(String)
    case measurementFailed
    case noImageSelected
    
    var errorDescription: String? {
        switch self {
        case .moduleNotInitialized:
            return "測量模組未初始化"
        case .qualityCheckFailed(let issues):
            return "品質檢查失敗: \(issues)"
        case .measurementFailed:
            return "測量處理失敗"
        case .noImageSelected:
            return "未選擇圖片"
        }
    }
}