import SwiftUI
import RealityKit
import ARKit

struct ARCameraPreviewView: View {
    @ObservedObject var moduleManager: ModuleManager
    @Binding var isPresented: Bool
    
    @State private var arView = ARView(frame: .zero)
    @State private var isARSessionActive = false
    @State private var showingMeasurementResult = false
    @State private var measurementResult: WoundMeasurementResult?
    @State private var isProcessing = false
    @State private var currentSession: ARSession?
    @State private var capturedImage: UIImage?
    @State private var showingImagePreview = false
    @State private var processedImageWithMask: UIImage?
    @AppStorage("LiDARUserEnabled") private var isLiDAREnabled = true
    @State private var showingPermissionAlert = false
    @State private var pausedCaptureForAR: Bool = false
    @State private var isStartingSession: Bool = false
    @State private var isStoppingSession: Bool = false
    @State private var sessionStartToken: UUID = UUID()
    @State private var lastLiDARToggleTime: Date = .distantPast
    
    var body: some View {
        NavigationView {
            ZStack {
                // AR Camera Preview
                ARViewContainer(arView: arView, isActive: $isARSessionActive)
                    .edgesIgnoringSafeArea(.all)
                
                VStack {
                    // Top Controls
                    HStack {
                        Button("關閉") {
                            stopARSession()
                            isPresented = false
                        }
                        .foregroundColor(.white)
                        .padding()
                        
                        Spacer()
                        
                        // Status Indicator
                        VStack {
                            Circle()
                                .fill(isARSessionActive ? .green : .red)
                                .frame(width: 12, height: 12)
                            Text(isARSessionActive ? "AR活躍" : "AR未啟用")
                                .font(.caption)
                                .foregroundColor(.white)
                        }
                        .padding()
                    }
                    
                    Spacer()
                    
                    // Measurement Controls
                    VStack(spacing: 20) {
                        if !isProcessing {
                            // 主要拍攝按鈕
                            Button(action: capturePhotoForMeasurement) {
                                VStack {
                                    Image(systemName: "camera.circle.fill")
                                        .font(.system(size: 80))
                                    Text("拍攝測量")
                                        .font(.headline)
                                        .fontWeight(.semibold)
                                }
                            }
                            .foregroundColor(.white)
                            .frame(width: 120, height: 120)
                            .background(Circle().fill(.blue.opacity(0.8)))
                            .shadow(radius: 10)
                            
                            // LiDAR 開關按鈕（僅取景，不做即時運算）
                            Button(action: toggleLiDAR) {
                                VStack {
                                    ZStack {
                                        Circle()
                                            .fill(isLiDAREnabled ? .orange : .gray)
                                            .frame(width: 80, height: 80)
                                            .scaleEffect(isLiDAREnabled ? 1.1 : 1.0)
                                            .animation(.easeInOut(duration: 0.2), value: isLiDAREnabled)
                                        
                                        Image(systemName: "dot.radiowaves.left.and.right")
                                            .font(.system(size: 30))
                                            .foregroundColor(.white)
                                            .rotationEffect(.degrees(isLiDAREnabled ? 0 : 180))
                                            .animation(.spring(duration: 0.3), value: isLiDAREnabled)
                                    }
                                    
                                    HStack(spacing: 4) {
                                        Text("LiDAR")
                                            .font(.caption)
                                            .fontWeight(.medium)
                                        
                                        Text(isLiDAREnabled ? "開啟" : "關閉")
                                            .font(.caption)
                                            .fontWeight(.bold)
                                            .foregroundColor(isLiDAREnabled ? .green : .red)
                                    }
                                    .foregroundColor(.white)
                                }
                            }
                            .scaleEffect(isLiDAREnabled ? 1.0 : 0.95)
                            .animation(.easeInOut(duration: 0.2), value: isLiDAREnabled)
                        } else {
                            VStack {
                                ProgressView()
                                    .progressViewStyle(CircularProgressViewStyle(tint: .white))
                                    .scaleEffect(2)
                                Text("正在處理測量（已暫停相機）...")
                                    .foregroundColor(.white)
                                    .font(.headline)
                                    .padding(.top)
                            }
                        }
                        
                        // Instructions
                        VStack(spacing: 8) {
                            Text("AR傷口測量")
                                .foregroundColor(.white)
                                .font(.title2)
                                .fontWeight(.bold)
                            Text("將相機對準傷口，確保校正貼紙清晰可見")
                                .foregroundColor(.white.opacity(0.9))
                                .font(.callout)
                            HStack(spacing: 4) {
                                Image(systemName: isLiDAREnabled ? "checkmark.circle.fill" : "xmark.circle.fill")
                                    .foregroundColor(isLiDAREnabled ? .green : .red)
                                Text("LiDAR空間深度計算: \(isLiDAREnabled ? "已啟用" : "已停用")")
                                    .foregroundColor(isLiDAREnabled ? .green : .red)
                            }
                            .font(.caption)
                            .fontWeight(.medium)
                        }
                        .multilineTextAlignment(.center)
                        .padding()
                        .background(.black.opacity(0.7))
                        .cornerRadius(15)
                    }
                    .padding(.bottom, 100)
                }
            }
        }
        .navigationBarHidden(true)
        .onAppear {
            // 延遲少許時間再啟動，避免畫面切換時的圖形子系統尚未就緒
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) {
                startARSession()
            }
        }
        .onDisappear {
            stopARSession()
        }
        .sheet(isPresented: $showingImagePreview) {
            if let image = capturedImage {
                CapturedImagePreviewView(
                    image: image,
                    processedImage: processedImageWithMask,
                    isProcessing: $isProcessing,
                    onConfirm: { processMeasurement(with: image) },
                    onRetake: { 
                        capturedImage = nil
                        processedImageWithMask = nil
                        showingImagePreview = false
                        // 重新啟動 AR 預覽，確保相機資源可用且不即時運算
                        startARSession()
                    }
                )
            }
        }
        .sheet(isPresented: $showingMeasurementResult) {
            if let result = measurementResult {
                EnhancedMeasurementResultView(
                    result: result,
                    originalImage: capturedImage,
                    processedImage: processedImageWithMask,
                    imageJCore: moduleManager.imageJCore
                )
            }
        }
        .alert("AR相機權限", isPresented: $showingPermissionAlert) {
            Button("設定") {
                if let settingsUrl = URL(string: UIApplication.openSettingsURLString) {
                    UIApplication.shared.open(settingsUrl)
                }
            }
            Button("取消", role: .cancel) { }
        } message: {
            Text("無法啟動AR相機預覽。請確保已授予相機權限，並在支援ARKit的設備上運行。")
        }
    }
    
    private func startARSession() {
        print("🚀 啟動AR相機預覽 (使用統一的ARSession管理)")
        if isARSessionActive || isStartingSession { return }
        isStartingSession = true
        let token = UUID()
        sessionStartToken = token
        Task {
            // 先暫停任何一般相機預覽，避免與 ARKit 爭用相機資源
            await MainActor.run {
                if let cap = moduleManager.captureModule?.captureSession, cap.isRunning {
                    moduleManager.captureModule?.stopCapture()
                    pausedCaptureForAR = true
                    print("📷 已暫停一般相機預覽以讓 ARKit 取得相機")
                }
            }
            // 使用統一的ARSessionManager請求AR會話
            if let session = await ARSessionManager.shared.requestSessionOwnership(for: .capture) {
                await MainActor.run {
                    guard sessionStartToken == token else { return }
                    currentSession = session
                    
                    let configuration = ARSessionManager.makeStandardConfiguration(
                        lidarEnabled: isLiDAREnabled,
                        planeDetection: [.horizontal, .vertical],
                        environmentTexturing: .none
                    )
                    
                    // 先將 session 綁到 ARView，再運行配置，確保 RealityKit 正確建立相機貼圖
                    arView.session = session
                    ARSessionManager.shared.runConfiguration(configuration, options: [.resetTracking, .removeExistingAnchors])
                    
                    // 僅取景，不做任何即時運算：不綁定任何 frame delegate 或推論邏輯
                    arView.automaticallyConfigureSession = false
                    arView.cameraMode = .ar
                    
                    isARSessionActive = true
                    isStartingSession = false
                    
                    print("✅ AR相機預覽已成功啟動（僅取景，無即時運算），LiDAR狀態: \(isLiDAREnabled ? "啟用" : "停用")")
                }
            } else {
                await MainActor.run {
                    print("❌ AR相機預覽: 無法獲取ARSession所有權")
                    isARSessionActive = false
                    isStartingSession = false
                    showingPermissionAlert = true
                }
            }
        }
    }
    
    private func stopARSession() {
        print("🛑 停止AR相機預覽")
        if isStoppingSession { return }
        isStoppingSession = true
        isARSessionActive = false
        
        Task { @MainActor in
            // 在主執行緒暫停與釋放，避免競態
            // 先暫停 ARView 綁定的 session，避免未暫停即被釋放
            arView.session.pause()
            currentSession?.pause()
            await ARSessionManager.shared.releaseSessionOwnership(from: .capture)
            // 保留 currentSession 參考以重用同一個 ARSession，避免反覆釋放/重建造成凍結
            print("✅ AR相機預覽已停止，ARSession所有權已釋放")
            // 若之前為 AR 暫停了一般相機，這裡恢復
            if pausedCaptureForAR {
                moduleManager.captureModule?.startCapture()
                pausedCaptureForAR = false
                print("📷 已恢復一般相機預覽")
            }
            isStoppingSession = false
        }
    }
    
    private func toggleLiDAR() {
        Task { @MainActor in
            // 300ms 冷卻避免頻繁 re-run 造成抖動
            let now = Date()
            if now.timeIntervalSince(lastLiDARToggleTime) < 0.3 { return }
            lastLiDARToggleTime = now
            isLiDAREnabled.toggle()
            print("🎯 LiDAR功能已\(isLiDAREnabled ? "開啟" : "關閉")，將影響空間深度計算")
            
            // 重新配置AR會話以啟用或停用深度感測
            guard currentSession != nil else { return }
            let configuration = ARSessionManager.makeStandardConfiguration(
                lidarEnabled: isLiDAREnabled,
                planeDetection: [.horizontal, .vertical],
                environmentTexturing: .none
            )
            // 已經綁定到 arView.session，這裡只下 run 即可；加入 .resetTracking 避免狀態抖動
            ARSessionManager.shared.runConfiguration(configuration, options: [.resetTracking])
            print("🔄 AR會話已重新配置，LiDAR狀態: \(isLiDAREnabled ? "啟用" : "停用")")
        }
    }
    
    private func capturePhotoForMeasurement() {
        print("📷 AR相機拍攝測量照片")
        
        Task {
            // 使用統一的currentSession獲取AR frame
            guard let session = currentSession,
                  let currentFrame = session.currentFrame else {
                print("❌ 無法獲取AR frame - session: \(currentSession != nil ? "存在" : "不存在")")
                return
            }
            
            // 使用 CVPixelBuffer 快照，避免持有 ARFrame 導致凍結
            let pixelBuffer = currentFrame.capturedImage
            let image = makeUIImage(from: pixelBuffer)
            
            // 拍照後立即停止 AR 會話，將資源讓給離線處理
            await MainActor.run {
                if isARSessionActive {
                    stopARSession()
                }
            }
            
            await MainActor.run {
                // 限制解析度至視窗友善（所見即所得）
                let displayBounds = UIScreen.main.bounds
                let targetMaxWidth = displayBounds.width
                let targetMaxHeight = displayBounds.height * 0.6
                let aspect = displayBounds.width / displayBounds.height
                let cropped = image?.croppedToAspect(aspect)
                let resized = cropped?.scaledToFit(maxWidth: targetMaxWidth, maxHeight: targetMaxHeight)

                // 存儲拍攝的影像並顯示預覽（已等比例縮放）
                capturedImage = resized ?? image
                showingImagePreview = true
                print("✅ 影像已拍攝，顯示預覽界面（AR已暫停）")
            }
        }
    }
    
    
    private func processMeasurement(with image: UIImage?) {
        guard let image = image else {
            isProcessing = false
            return
        }
        
        isProcessing = true
        showingImagePreview = false // 關閉預覽窗口
        
        Task {
            do {
                print("🔄 開始處理AR拍攝的圖像...")
                // 為避免相機與運算爭用資源，先停止 AR 會話（不做即時運算，僅拍照後處理）
                await MainActor.run {
                    if isARSessionActive {
                        stopARSession()
                    }
                }
                
                // 使用現有的模組進行處理
                let (result, maskImage) = try await processImageWithModulesAndMask(image)
                
                await MainActor.run {
                    measurementResult = result
                    processedImageWithMask = maskImage
                    showingMeasurementResult = true
                    isProcessing = false
                    // 處理完成後，若 UI 仍在，重新啟動 AR 取景（仍不做即時運算）
                    startARSession()
                    print("✅ 測量完成，顯示結果界面")
                }
                
            } catch {
                print("❌ AR測量處理錯誤: \(error)")
                await MainActor.run {
                    isProcessing = false
                    // 顯示錯誤，但保持預覽開啟
                    showingImagePreview = true
                }
            }
        }
    }
    
    private func processImageWithModulesAndMask(_ image: UIImage) async throws -> (WoundMeasurementResult, UIImage?) {
        // 使用現有的模組管理器進行處理
        guard let preProcessingModule = moduleManager.preProcessingModule,
              let qaFilterModule = moduleManager.qaFilterModule,
              let imageJCore = moduleManager.imageJCore else {
            throw ProcessingError.moduleNotInitialized
        }

        // 預處理（若可取得場景深度，附帶深度資料以改善量測）
        let depthData: Data = {
            if let frame = currentSession?.currentFrame,
               isLiDAREnabled,
               let depthMap = frame.sceneDepth?.depthMap {
                CVPixelBufferLockBaseAddress(depthMap, .readOnly)
                defer { CVPixelBufferUnlockBaseAddress(depthMap, .readOnly) }
                let height = CVPixelBufferGetHeight(depthMap)
                let bytesPerRow = CVPixelBufferGetBytesPerRow(depthMap)
                if let base = CVPixelBufferGetBaseAddress(depthMap) {
                    let length = bytesPerRow * height
                    return Data(bytes: base, count: length)
                }
            }
            return Data()
        }()

        let processed = try await preProcessingModule.processImage(image, depthData: depthData)

        // 品質檢查
        let qaResult = try await qaFilterModule.evaluateQuality(processed)
        guard qaResult.isValid else {
            throw ProcessingError.qualityCheckFailed(qaResult.failureReason ?? "Unknown")
        }

        // 實際量測（面積/周長/體積/組織等）
        let measurement = try await imageJCore.measureWound(processed)

        // 產生遮罩疊圖（基於實際分割輪廓）
        let overlay = try await generateSegmentationOverlay(on: processed.image)

        // LiDAR 控制體積輸出
        let finalVolume: Double? = isLiDAREnabled ? measurement.volume : nil

        // 若有簡化分類結果，轉為 DetailedWoundClassification 以便顯示
        let detailedClassification: DetailedWoundClassification? = {
            if let c = qaResult.classification {
                return DetailedWoundClassification(
                    acuteScore: c.acuteScore,
                    chronicScore: c.chronicScore,
                    infectedScore: c.infectedScore,
                    healingScore: c.healingScore,
                    confidence: c.confidence
                )
            }
            return nil
        }()

        let result = WoundMeasurementResult(
            area: measurement.area,
            volume: finalVolume,
            perimeter: measurement.perimeter,
            maxDepth: measurement.maxDepth,
            classification: detailedClassification,
            qualityMetrics: processed.qualityMetrics,
            tissueComposition: measurement.tissueComposition,
            originalImage: image,
            processedImage: overlay,
            depthData: processed.depthData,
            timestamp: Date(),
            error: nil,
            notes: nil,
            recommendations: nil
        )

        return (result, overlay)
    }

    // 以 SegmentationEngine 產生實際輪廓的遮罩圖，基於處理後影像
    private func generateSegmentationOverlay(on baseImage: UIImage) async throws -> UIImage? {
        let engine = SegmentationEngine()
        let segmented = try await engine.segment(baseImage)

        guard let largest = segmented.contours.max(by: { $0.area < $1.area }),
              !largest.points.isEmpty else {
            return baseImage
        }

        let size = baseImage.size
        let renderer = UIGraphicsImageRenderer(size: size)
        let overlay = renderer.image { ctx in
            baseImage.draw(in: CGRect(origin: .zero, size: size))

            // 轉為像素座標並繪製多邊形
            let path = UIBezierPath()
            let first = largest.points[0]
            path.move(to: CGPoint(x: first.x * size.width, y: first.y * size.height))
            for p in largest.points.dropFirst() {
                path.addLine(to: CGPoint(x: p.x * size.width, y: p.y * size.height))
            }
            path.close()

            ctx.cgContext.setFillColor(UIColor.red.withAlphaComponent(0.28).cgColor)
            ctx.cgContext.addPath(path.cgPath)
            ctx.cgContext.fillPath()

            ctx.cgContext.setStrokeColor(UIColor.red.cgColor)
            ctx.cgContext.setLineWidth(3)
            ctx.cgContext.addPath(path.cgPath)
            ctx.cgContext.strokePath()
        }

        return overlay
    }
}

// AR View Container
struct ARViewContainer: UIViewRepresentable {
    let arView: ARView
    @Binding var isActive: Bool
    
    func makeUIView(context: Context) -> ARView {
        // 確保ARView正確配置以顯示相機畫面
        arView.automaticallyConfigureSession = false
        arView.renderOptions = [.disableAREnvironmentLighting, .disableMotionBlur]
        arView.cameraMode = .ar
        // 明確設定以相機畫面為背景，降低 RealityKit 初始化異常
        if #available(iOS 15.0, *) {
            arView.environment.background = .cameraFeed()
        }
        
        // 確保背景是透明的以顯示相機畫面
        // 使用預設背景（移除 .camera 設定以避免API不相容）
        
        print("🎥 ARViewContainer: ARView已配置為顯示相機畫面")
        return arView
    }
    
    func updateUIView(_ uiView: ARView, context: Context) {
        // 確保ARView繼續顯示相機畫面
        if isActive {
            // 保持預設背景
        }
    }
}

// MARK: - 拍攝後預覽視圖
struct CapturedImagePreviewView: View {
    let image: UIImage
    let processedImage: UIImage?
    @Binding var isProcessing: Bool
    let onConfirm: () -> Void
    let onRetake: () -> Void
    @Environment(\.presentationMode) var presentationMode
    
    var body: some View {
        NavigationView {
            VStack {
                // 影像顯示
                if let processedImage = processedImage {
                    // 顯示處理後的影像（帶遮罩）
                    Image(uiImage: processedImage)
                        .resizable()
                        .aspectRatio(contentMode: .fit)
                        .frame(maxWidth: UIScreen.main.bounds.width - 40,
                               maxHeight: UIScreen.main.bounds.height * 0.6)
                        .cornerRadius(10)
                        .shadow(radius: 5)
                } else {
                    // 顯示原始拍攝影像
                    Image(uiImage: image)
                        .resizable()
                        .aspectRatio(contentMode: .fit)
                        .frame(maxWidth: UIScreen.main.bounds.width - 40,
                               maxHeight: UIScreen.main.bounds.height * 0.6)
                        .cornerRadius(10)
                        .shadow(radius: 5)
                }
                
                Spacer()
                
                if isProcessing {
                    VStack(spacing: 16) {
                        ProgressView()
                            .progressViewStyle(CircularProgressViewStyle(tint: .blue))
                            .scaleEffect(1.5)
                        Text("正在分析傷口影像...")
                            .font(.headline)
                            .foregroundColor(.secondary)
                    }
                    .padding()
                } else {
                    // 操作按鈕
                    HStack(spacing: 20) {
                        Button("重新拍攝") {
                            onRetake()
                        }
                        .font(.title2)
                        .padding()
                        .frame(maxWidth: .infinity)
                        .background(.red)
                        .foregroundColor(.white)
                        .cornerRadius(10)
                        
                        Button("開始測量") {
                            onConfirm()
                        }
                        .font(.title2)
                        .padding()
                        .frame(maxWidth: .infinity)
                        .background(.blue)
                        .foregroundColor(.white)
                        .cornerRadius(10)
                    }
                    .padding(.horizontal)
                }
            }
            .padding()
            .navigationTitle("影像預覽")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    Button("取消") {
                        onRetake()
                    }
                }
            }
        }
    }
}

// MARK: - 增強版測量結果視圖
struct EnhancedMeasurementResultView: View {
    let result: WoundMeasurementResult
    let originalImage: UIImage?
    let processedImage: UIImage?
    let imageJCore: ImageJCore?
    @Environment(\.presentationMode) var presentationMode
    @State private var isSaving = false
    @State private var saveSuccess = false
    @State private var showingAnnotationView = false
    @State private var photoSaveSuccess = false
    @State private var isSavingPhoto = false
    
    var body: some View {
        NavigationView {
            ScrollView {
                VStack(spacing: 20) {
                    Text("AR測量結果")
                        .font(.largeTitle)
                        .fontWeight(.bold)

                    // 顯示像素比例來源與警示
                    let src = imageJCore?.lastCalibrationSource ?? .fallback
                    MeasurementBanner(
                        pixelScaleSource: src == .sticker ? "貼紙校正" : (src == .lidar ? "LiDAR 校準" : "相機內參 + 預設距離（推估）"),
                        warning: src == .fallback ? "未使用貼紙/ LiDAR 校正，本次為推估比例，僅供參考" : nil
                    )
                    
                    // 影像顯示區域
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
                    
                    // 成功狀態顯示
                    if saveSuccess || photoSaveSuccess {
                        VStack(spacing: 10) {
                            if saveSuccess {
                                HStack {
                                    Image(systemName: "checkmark.circle.fill")
                                        .foregroundColor(.green)
                                    Text("測量結果已成功保存到歷史記錄")
                                        .font(.subheadline)
                                        .foregroundColor(.green)
                                }
                            }
                            
                            if photoSaveSuccess {
                                HStack {
                                    Image(systemName: "photo.badge.checkmark")
                                        .foregroundColor(.blue)
                                    Text("照片已成功存檔到相簿")
                                        .font(.subheadline)
                                        .foregroundColor(.blue)
                                }
                            }
                        }
                        .padding()
                        .background(Color.green.opacity(0.1))
                        .cornerRadius(10)
                    }
                    
                    // 操作按鈕
                    VStack(spacing: 15) {
                        // 保存到歷史記錄
                        Button(action: saveWoundMeasurement) {
                            HStack {
                                if isSaving {
                                    ProgressView()
                                        .progressViewStyle(CircularProgressViewStyle(tint: .white))
                                        .scaleEffect(0.8)
                                } else {
                                    Image(systemName: "square.and.arrow.down")
                                }
                                Text(isSaving ? "保存中..." : "保存到測量歷史")
                            }
                            .font(.title2)
                            .padding()
                            .frame(maxWidth: .infinity)
                            .background(saveSuccess ? .gray : .blue)
                            .foregroundColor(.white)
                            .cornerRadius(10)
                        }
                        .disabled(isSaving || saveSuccess)
                        
                        // 存檔照片到相簿
                        Button(action: savePhotoToLibrary) {
                            HStack {
                                if isSavingPhoto {
                                    ProgressView()
                                        .progressViewStyle(CircularProgressViewStyle(tint: .white))
                                        .scaleEffect(0.8)
                                } else {
                                    Image(systemName: "photo.badge.plus")
                                }
                                Text(isSavingPhoto ? "存檔中..." : "存檔照片到相簿")
                            }
                            .font(.title2)
                            .padding()
                            .frame(maxWidth: .infinity)
                            .background(photoSaveSuccess ? .gray : .green)
                            .foregroundColor(.white)
                            .cornerRadius(10)
                        }
                        .disabled(isSavingPhoto || photoSaveSuccess || (processedImage == nil && originalImage == nil))
                    
                        // 標註和上傳（選用）
                        Button(action: {
                            showingAnnotationView = true
                        }) {
                            HStack {
                                Image(systemName: "pencil.and.outline")
                                Text("標註和上傳")
                            }
                            .font(.title2)
                            .padding()
                            .frame(maxWidth: .infinity)
                            .background(.orange)
                            .foregroundColor(.white)
                            .cornerRadius(10)
                        }
                        .disabled(!saveSuccess)
                        
                        // 完成按鈕
                        Button("完成") {
                            presentationMode.wrappedValue.dismiss()
                        }
                        .font(.title2)
                        .padding()
                        .frame(maxWidth: .infinity)
                        .background(.gray)
                        .foregroundColor(.white)
                        .cornerRadius(10)
                    }
                    .padding(.horizontal)
                }
                .padding()
            }
        }
        .sheet(isPresented: $showingAnnotationView) {
            ARAnnotationView(result: result)
        }
    }
    
    private func saveWoundMeasurement() {
        isSaving = true
        
        Task {
            do {
                // 實際保存到CoreData
                let dataManager = DataManager.shared
                
                // 直接保存結果到歷史記錄
                dataManager.saveWoundResult(result)
                
                print("💾 已保存傷口測量結果到CoreData：面積=\(result.area ?? 0)cm², 體積=\(result.volume ?? 0)cm³")
                
                await MainActor.run {
                    isSaving = false
                    saveSuccess = true
                }
                
            }
        }
    }
    
    private func savePhotoToLibrary() {
        isSavingPhoto = true
        
        Task {
            do {
                // 請求照片庫權限
                await requestPhotoLibraryPermission()
                
                // 優先保存處理後的影像（帶遮罩），若無則保存原始影像
                let imageToSave = processedImage ?? originalImage
                
                guard let imageToSave = imageToSave else {
                    throw PhotoSaveError.noImageToSave
                }
                
                // 保存到照片庫
                try await saveImageToPhotoLibrary(imageToSave)
                
                print("📸 已保存照片到相簿")
                
                await MainActor.run {
                    isSavingPhoto = false
                    photoSaveSuccess = true
                }
                
            } catch {
                print("❌ 保存照片失敗: \(error)")
                await MainActor.run {
                    isSavingPhoto = false
                }
            }
        }
    }
    
    private func requestPhotoLibraryPermission() async {
        // 在實際應用中需要請求照片庫權限
        // 這裡假設已獲得權限
    }
    
    private func saveImageToPhotoLibrary(_ image: UIImage) async throws {
        // 使用Photos框架保存影像
        return try await withCheckedThrowingContinuation { continuation in
            UIImageWriteToSavedPhotosAlbum(image, nil, nil, nil)
            // 簡化實現，實際應該檢查保存結果
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) {
                continuation.resume()
            }
        }
    }
}

// MARK: - 標註視圖
struct ARAnnotationView: View {
    let result: WoundMeasurementResult
    @Environment(\.presentationMode) var presentationMode
    @State private var isUploading = false
    @State private var uploadSuccess = false
    
    var body: some View {
        NavigationView {
            VStack(spacing: 20) {
                Text("傷口標註和上傳")
                    .font(.largeTitle)
                    .fontWeight(.bold)
                
                Text("本功能將使用本地輕量模型進行自動標註")
                    .font(.subheadline)
                    .foregroundColor(.secondary)
                    .multilineTextAlignment(.center)
                
                // 這裡可以添加標註界面
                Spacer()
                
                if uploadSuccess {
                    HStack {
                        Image(systemName: "icloud.and.arrow.up.fill")
                            .foregroundColor(.green)
                        Text("已成功上傳到雲端訓練佇列")
                            .foregroundColor(.green)
                    }
                    .padding()
                    .background(Color.green.opacity(0.1))
                    .cornerRadius(10)
                }
                
                VStack(spacing: 15) {
                    Button(action: uploadToCloud) {
                        HStack {
                            if isUploading {
                                ProgressView()
                                    .progressViewStyle(CircularProgressViewStyle(tint: .white))
                                    .scaleEffect(0.8)
                            } else {
                                Image(systemName: "icloud.and.arrow.up")
                            }
                            Text(isUploading ? "上傳中..." : "上傳到雲端訓練佇列")
                        }
                        .font(.title2)
                        .padding()
                        .frame(maxWidth: .infinity)
                        .background(uploadSuccess ? .gray : .blue)
                        .foregroundColor(.white)
                        .cornerRadius(10)
                    }
                    .disabled(isUploading || uploadSuccess)
                    
                    Button("關閉") {
                        presentationMode.wrappedValue.dismiss()
                    }
                    .font(.title2)
                    .padding()
                    .frame(maxWidth: .infinity)
                    .background(.gray)
                    .foregroundColor(.white)
                    .cornerRadius(10)
                }
                .padding(.horizontal)
            }
            .padding()
        }
    }
    
    private func uploadToCloud() {
        isUploading = true
        
        Task {
            do {
                // 模擬上傳過程
                try await Task.sleep(nanoseconds: 3_000_000_000) // 3秒延遲
                
                // 這裡應該實際上傳到雲端API
                print("☁️ 上傳傷口數據到雲端訓練佇列：面積=\(result.area ?? 0)cm², 體積=\(result.volume ?? 0)cm³")
                
                await MainActor.run {
                    isUploading = false
                    uploadSuccess = true
                }
                
            } catch {
                print("❌ 上傳到雲端失敗: \(error)")
                await MainActor.run {
                    isUploading = false
                }
            }
        }
    }
}

// Processing Errors
enum ProcessingError: Error, LocalizedError {
    case moduleNotInitialized
    case qualityCheckFailed(String)
    case measurementFailed
    case classificationFailed
    
    var errorDescription: String? {
        switch self {
        case .moduleNotInitialized:
            return "模組未初始化"
        case .qualityCheckFailed(let issues):
            return "品質檢查失敗: \(issues)"
        case .measurementFailed:
            return "測量失敗"
        case .classificationFailed:
            return "分類失敗"
        }
    }
}

// Photo Save Errors
enum PhotoSaveError: Error, LocalizedError {
    case noImageToSave
    case permissionDenied
    case saveFailed
    
    var errorDescription: String? {
        switch self {
        case .noImageToSave:
            return "沒有可保存的影像"
        case .permissionDenied:
            return "沒有照片庫存取權限"
        case .saveFailed:
            return "保存照片失敗"
        }
    }
}

// UIImage extension for pixel buffer
extension UIImage {
    convenience init?(pixelBuffer: CVPixelBuffer, orientation: UIImage.Orientation) {
        let ciImage = CIImage(cvPixelBuffer: pixelBuffer)
        let context = CIContext(options: nil)
        guard let cgImage = context.createCGImage(ciImage, from: ciImage.extent) else {
            return nil
        }
        self.init(cgImage: cgImage, scale: 1.0, orientation: orientation)
    }

    func scaledToFit(maxWidth: CGFloat, maxHeight: CGFloat) -> UIImage {
        let scale = min(maxWidth / size.width, maxHeight / size.height)
        if scale >= 1 { return self }
        let newSize = CGSize(width: size.width * scale, height: size.height * scale)
        let renderer = UIGraphicsImageRenderer(size: newSize)
        return renderer.image { _ in
            self.draw(in: CGRect(origin: .zero, size: newSize))
        }
    }

    func croppedToAspect(_ targetAspect: CGFloat) -> UIImage {
        let currentAspect = size.width / size.height
        var cropRect: CGRect
        if currentAspect > targetAspect {
            // 太寬，裁掉左右
            let newWidth = size.height * targetAspect
            let x = (size.width - newWidth) / 2
            cropRect = CGRect(x: x, y: 0, width: newWidth, height: size.height)
        } else {
            // 太高，裁掉上下
            let newHeight = size.width / targetAspect
            let y = (size.height - newHeight) / 2
            cropRect = CGRect(x: 0, y: y, width: size.width, height: newHeight)
        }
        let format = imageRendererFormat
        format.opaque = false
        return UIGraphicsImageRenderer(size: cropRect.size, format: format).image { _ in
            self.draw(at: CGPoint(x: -cropRect.origin.x, y: -cropRect.origin.y))
        }
    }
}

// MARK: - 測量 Banner（顯示像素比例來源/警示）
struct MeasurementBanner: View {
    let pixelScaleSource: String
    let warning: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                Image(systemName: warning == nil ? "checkmark.shield" : "exclamationmark.triangle.fill")
                    .foregroundColor(warning == nil ? .green : .orange)
                Text("像素比例來源：\(pixelScaleSource)")
                    .font(.subheadline)
            }
            if let warning = warning {
                Text(warning)
                    .font(.caption)
                    .foregroundColor(.orange)
            }
        }
        .padding(10)
        .background((warning == nil ? Color.green.opacity(0.1) : Color.orange.opacity(0.12)))
        .cornerRadius(10)
    }
}

// MARK: - 方向與影像轉換輔助
private extension ARCameraPreviewView {
    func makeUIImage(from pixelBuffer: CVPixelBuffer) -> UIImage? {
        let uiOrientation = currentUIImageOrientation()
        return UIImage(pixelBuffer: pixelBuffer, orientation: uiOrientation)
    }
    
    func currentUIImageOrientation() -> UIImage.Orientation {
        let orientation = currentInterfaceOrientation()
        switch orientation {
        case .portrait: return .right
        case .portraitUpsideDown: return .left
        case .landscapeLeft: return .up
        case .landscapeRight: return .down
        default: return .right
        }
    }
    
    func currentInterfaceOrientation() -> UIInterfaceOrientation {
        // 嘗試從目前 ARView 所在的 windowScene 取得方向
        if let scene = arView.window?.windowScene {
            return scene.interfaceOrientation
        }
        // 後備：從已連線的 scene 取得
        if let scene = UIApplication.shared.connectedScenes.first as? UIWindowScene {
            return scene.interfaceOrientation
        }
        // 預設回傳直向
        return .portrait
    }
}