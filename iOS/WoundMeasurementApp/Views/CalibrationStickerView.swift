import SwiftUI
import UIKit

struct CalibrationStickerView: View {
    @StateObject private var stickerModule = CalibrationStickerModule()
    @ObservedObject var imageJCore: ImageJCore
    @Environment(\.dismiss) private var dismiss
    
    @State private var selectedImage: UIImage?
    @State private var showingImagePicker = false
    @State private var showingROIEditor = false
    @State private var detectedWoundROI: CGRect = .zero
    @State private var manualROI: CGRect = .zero
    @State private var useManualROI = false
    @State private var showingCalibrationResult = false
    
    var body: some View {
        NavigationView {
            ScrollView {
                VStack(spacing: 12) {
                    // 校準貼紙說明
                    CalibrationInstructionCard()
                        .frame(maxWidth: 800)
                    
                    // 圖像選擇區域
                    ImageSelectionSection(
                        selectedImage: $selectedImage,
                        showingImagePicker: $showingImagePicker
                    )
                    .frame(maxWidth: 800)
                    
                    if selectedImage != nil {
                        // 校準檢測控制
                        CalibrationControlSection(
                            stickerModule: stickerModule,
                            selectedImage: $selectedImage
                        )
                        .frame(maxWidth: 800)
                        
                        // 檢測結果顯示
                        if let result = stickerModule.detectionResult {
                            CalibrationResultSection(result: result)
                                .frame(maxWidth: 800)
                        }
                        
                        // ROI評估和編輯
                        ROIEvaluationSection(
                            stickerModule: stickerModule,
                            selectedImage: $selectedImage,
                            detectedWoundROI: $detectedWoundROI,
                            manualROI: $manualROI,
                            useManualROI: $useManualROI,
                            showingROIEditor: $showingROIEditor
                        )
                        .frame(maxWidth: 800)
                        
                        // LiDAR整合控制
                        LiDARIntegrationSection(
                            stickerModule: stickerModule,
                            imageJCore: imageJCore
                        )
                        .frame(maxWidth: 800)
                    }
                    
                    Spacer(minLength: 40)
                }
                .padding()
            }
            .navigationTitle("校準貼紙檢測")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    Button("關閉") {
                        dismiss()
                    }
                }
                
                ToolbarItem(placement: .navigationBarTrailing) {
                    if stickerModule.detectionResult != nil {
                        Button("應用校準") {
                            applyCalibration()
                        }
                        .disabled(stickerModule.shouldUseManualROI && !useManualROI)
                    }
                }
            }
            .safeAreaInset(edge: .bottom) {
                HStack(spacing: 12) {
                    Button {
                        showingImagePicker = true
                    } label: {
                        Label(selectedImage == nil ? "選擇含貼紙圖像" : "更換圖像", systemImage: "photo.on.rectangle")
                    }
                    .buttonStyle(.borderedProminent)

                    if selectedImage != nil {
                        Button {
                            Task {
                                guard let img = selectedImage else { return }
                                try? await stickerModule.detectCalibrationSticker(from: img)
                            }
                        } label: {
                            Label("檢測貼紙並校準", systemImage: "scope")
                        }
                        .buttonStyle(.bordered)
                    }
                }
                .padding(.horizontal)
                .padding(.vertical, 8)
                .background(.ultraThinMaterial)
            }
        }
        .sheet(isPresented: $showingImagePicker) {
            ImagePicker(selectedImage: $selectedImage)
        }
        .sheet(isPresented: $showingROIEditor) {
            if let image = selectedImage {
                ManualROIEditor(
                    image: image,
                    initialROI: detectedWoundROI,
                    onROIChanged: { roi in
                        manualROI = roi
                        useManualROI = true
                    }
                )
            }
        }
        .alert("校準結果", isPresented: $showingCalibrationResult) {
            Button("確定") { }
        } message: {
            Text(getCalibrationStatusMessage())
        }
    }
    
    private func applyCalibration() {
        Task {
            guard let result = stickerModule.detectionResult else { return }
            
            // 應用校準到ImageJ核心
            await imageJCore.measurementEngine.updatePixelScale(result.pixelsPerMM)
            
            // 顯示結果
            await MainActor.run {
                showingCalibrationResult = true
            }
        }
    }
    
    private func getCalibrationStatusMessage() -> String {
        guard let result = stickerModule.detectionResult else {
            return "校準檢測失敗"
        }
        
        // 轉換為 cm/pixel 供 UI 顯示與一致性檢查
        let cmPerPixel = 1.0 / (result.pixelsPerMM * 10.0)
        let rangeOK = result.pixelsPerMM >= 1.0 && result.pixelsPerMM <= 50.0 && cmPerPixel >= 0.002 && cmPerPixel <= 0.1
        
        return """
        校準已成功應用：
        
        像素比例: \(String(format: "%.3f", result.pixelsPerMM)) pixels/mm (\(String(format: "%.5f", cmPerPixel)) cm/pixel)
        檢測信心度: \(String(format: "%.1f", result.confidence * 100))%
        \(rangeOK ? "參數有效 ✅" : "參數可能不合理 ⚠️ 請重新校準或調整拍攝條件")
        
        系統現在會使用此校準參數進行精確測量。
        """
    }
}

struct CalibrationInstructionCard: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Image(systemName: "info.circle.fill")
                    .foregroundColor(.blue)
                    .font(.title2)
                Text("20mm 校準貼紙使用說明")
                    .font(.headline)
                    .fontWeight(.semibold)
            }
            
            VStack(alignment: .leading, spacing: 8) {
                InstructionRow(
                    icon: "1.circle.fill",
                    text: "將20mm直徑的RGB校準貼紙放置在傷口附近",
                    color: .blue
                )
                InstructionRow(
                    icon: "2.circle.fill",
                    text: "確保貼紙完整清晰可見，包含RGB色塊和3D凸點",
                    color: .green
                )
                InstructionRow(
                    icon: "3.circle.fill",
                    text: "拍攝或選擇包含貼紙和傷口的照片",
                    color: .orange
                )
                InstructionRow(
                    icon: "4.circle.fill",
                    text: "系統將自動檢測貼紙並校準測量精度",
                    color: .purple
                )
            }
        }
        .padding()
        .background(Color.gray.opacity(0.1))
        .cornerRadius(12)
    }
}

struct InstructionRow: View {
    let icon: String
    let text: String
    let color: Color
    
    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: icon)
                .foregroundColor(color)
                .font(.body)
                .frame(width: 20)
            
            Text(text)
                .font(.body)
                .foregroundColor(.primary)
        }
    }
}

struct ImageSelectionSection: View {
    @Binding var selectedImage: UIImage?
    @Binding var showingImagePicker: Bool
    
    var body: some View {
        VStack(spacing: 12) {
            Text("選擇包含校準貼紙的圖像")
                .font(.headline)
            
            if let image = selectedImage {
                Image(uiImage: image)
                    .resizable()
                    .aspectRatio(contentMode: .fit)
                    .frame(maxHeight: 300)
                    .cornerRadius(12)
                    .shadow(radius: 4)
            }
            
            Button(action: {
                showingImagePicker = true
            }) {
                HStack {
                    Image(systemName: selectedImage == nil ? "photo.badge.plus" : "photo.badge.arrow.down")
                    Text(selectedImage == nil ? "選擇圖像" : "更換圖像")
                }
                .font(.headline)
                .foregroundColor(.white)
                .padding()
                .frame(maxWidth: .infinity)
                .background(Color.blue)
                .cornerRadius(10)
            }
        }
    }
}

struct CalibrationControlSection: View {
    @ObservedObject var stickerModule: CalibrationStickerModule
    @Binding var selectedImage: UIImage?
    
    var body: some View {
        VStack(spacing: 12) {
            Text("校準檢測")
                .font(.headline)
            
            // 狀態顯示
            HStack {
                if stickerModule.isDetecting {
                    ProgressView()
                        .scaleEffect(0.8)
                }
                
                Text(stickerModule.calibrationStatus)
                    .font(.body)
                    .foregroundColor(stickerModule.isDetecting ? .orange : 
                                    stickerModule.detectionResult != nil ? .green : .primary)
                
                Spacer()
            }
            .padding()
            .background(Color.gray.opacity(0.1))
            .cornerRadius(8)
            
            // 檢測按鈕
            Button(action: {
                detectCalibrationSticker()
            }) {
                HStack {
                    Image(systemName: "target")
                    Text(stickerModule.isDetecting ? "檢測中..." : "開始檢測校準貼紙")
                }
                .font(.headline)
                .foregroundColor(.white)
                .padding()
                .frame(maxWidth: .infinity)
                .background(stickerModule.isDetecting ? Color.gray : Color.green)
                .cornerRadius(10)
            }
            .disabled(stickerModule.isDetecting || selectedImage == nil)
            
            // 錯誤顯示
            if let error = stickerModule.detectionError {
                Text(error)
                    .font(.caption)
                    .foregroundColor(.red)
                    .padding(.horizontal)
            }
        }
    }
    
    private func detectCalibrationSticker() {
        guard let image = selectedImage else { return }
        
        Task {
            do {
                let _ = try await stickerModule.detectCalibrationSticker(from: image)
                // 檢測成功後自動檢測傷口ROI
                await detectWoundROI()
            } catch {
                print("校準貼紙檢測失敗: \(error.localizedDescription)")
            }
        }
    }
    
    private func detectWoundROI() async {
        // 這裡應該調用傷口ROI檢測模組
        // 暫時使用模擬ROI
        let mockROI = CGRect(x: 0.3, y: 0.4, width: 0.4, height: 0.3)
        
        // 評估ROI信心度
        if let image = selectedImage {
            let _ = await stickerModule.evaluateWoundROIConfidence(
                woundROI: mockROI,
                in: image,
                withSticker: stickerModule.detectionResult
            )
        }
    }
}

struct CalibrationResultSection: View {
    let result: StickerCalibrationResult
    
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("檢測結果")
                .font(.headline)
            
            VStack(alignment: .leading, spacing: 8) {
                ResultRow(
                    title: "檢測位置",
                    value: "(\(Int(result.circle.center.x)), \(Int(result.circle.center.y)))",
                    icon: "location.fill",
                    color: .blue
                )
                
                ResultRow(
                    title: "貼紙半徑",
                    value: "\(String(format: "%.1f", result.circle.radius)) pixels",
                    icon: "circle.dashed",
                    color: .green
                )
                
                ResultRow(
                    title: "像素比例",
                    value: "\(String(format: "%.3f", result.pixelsPerMM)) pixels/mm",
                    icon: "ruler.fill",
                    color: .orange
                )
                
                ResultRow(
                    title: "檢測信心度",
                    value: "\(String(format: "%.1f", result.confidence * 100))%",
                    icon: "checkmark.seal.fill",
                    color: result.confidence > 0.8 ? .green : result.confidence > 0.6 ? .orange : .red
                )
                
                ResultRow(
                    title: "3D標記點",
                    value: "\(result.internalStructure.dots3D.count) 個",
                    icon: "cube.fill",
                    color: .purple
                )
            }
        }
        .padding()
        .background(Color.green.opacity(0.1))
        .cornerRadius(12)
    }
}

struct ResultRow: View {
    let title: String
    let value: String
    let icon: String
    let color: Color
    
    var body: some View {
        HStack {
            Image(systemName: icon)
                .foregroundColor(color)
                .frame(width: 20)
            
            Text(title)
                .font(.body)
            
            Spacer()
            
            Text(value)
                .font(.body)
                .fontWeight(.semibold)
        }
    }
}

struct ROIEvaluationSection: View {
    @ObservedObject var stickerModule: CalibrationStickerModule
    @Binding var selectedImage: UIImage?
    @Binding var detectedWoundROI: CGRect
    @Binding var manualROI: CGRect
    @Binding var useManualROI: Bool
    @Binding var showingROIEditor: Bool
    
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("ROI 評估")
                .font(.headline)
            
            // ROI信心度顯示
            if stickerModule.roiDetectionConfidence > 0 {
                VStack(alignment: .leading, spacing: 8) {
                    HStack {
                        Text("ROI檢測信心度:")
                            .font(.body)
                        
                        Spacer()
                        
                        Text("\(String(format: "%.1f", stickerModule.roiDetectionConfidence * 100))%")
                            .font(.body)
                            .fontWeight(.semibold)
                            .foregroundColor(stickerModule.roiDetectionConfidence > 0.7 ? .green : .orange)
                    }
                    
                    ProgressView(value: stickerModule.roiDetectionConfidence)
                        .progressViewStyle(LinearProgressViewStyle(tint: stickerModule.roiDetectionConfidence > 0.7 ? .green : .orange))
                }
                .padding()
                .background(Color.gray.opacity(0.1))
                .cornerRadius(8)
            }
            
            // 手動ROI建議
            if stickerModule.shouldUseManualROI {
                VStack(alignment: .leading, spacing: 8) {
                    HStack {
                        Image(systemName: "exclamationmark.triangle.fill")
                            .foregroundColor(.orange)
                        Text("建議手動調整ROI")
                            .font(.subheadline)
                            .fontWeight(.medium)
                    }
                    
                    Text("自動檢測的ROI信心度較低，建議手動繪製傷口區域以獲得更準確的測量結果。")
                        .font(.caption)
                        .foregroundColor(.secondary)
                    
                    Button(action: {
                        showingROIEditor = true
                    }) {
                        HStack {
                            Image(systemName: "scribble.variable")
                            Text("手動繪製ROI")
                        }
                        .font(.subheadline)
                        .foregroundColor(.white)
                        .padding(.horizontal, 12)
                        .padding(.vertical, 8)
                        .background(Color.orange)
                        .cornerRadius(8)
                    }
                }
                .padding()
                .background(Color.orange.opacity(0.1))
                .cornerRadius(8)
            }
            
            // ROI狀態指示器
            HStack {
                Image(systemName: useManualROI ? "hand.draw.fill" : "wand.and.rays")
                    .foregroundColor(useManualROI ? .orange : .blue)
                
                Text(useManualROI ? "使用手動ROI" : "使用自動檢測ROI")
                    .font(.subheadline)
                
                Spacer()
            }
            .padding(.horizontal)
        }
    }
}

struct LiDARIntegrationSection: View {
    @ObservedObject var stickerModule: CalibrationStickerModule
    @ObservedObject var imageJCore: ImageJCore
    
    @State private var integrationResult: IntegratedCalibrationResult?
    @State private var showingIntegrationResult = false
    
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("LiDAR 整合")
                .font(.headline)
            
            Text("結合校準貼紙和LiDAR數據，獲得最高精度的校準結果。")
                .font(.caption)
                .foregroundColor(.secondary)
            
            Button(action: {
                integrateLiDARCalibration()
            }) {
                HStack {
                    Image(systemName: "dot.radiowaves.left.and.right")
                    Text("整合LiDAR校準")
                }
                .font(.headline)
                .foregroundColor(.white)
                .padding()
                .frame(maxWidth: .infinity)
                .background(Color.purple)
                .cornerRadius(10)
            }
            .disabled(stickerModule.detectionResult == nil)
            
            if let result = integrationResult {
                IntegrationResultCard(result: result)
            }
        }
        .alert("整合結果", isPresented: $showingIntegrationResult) {
            Button("確定") { }
        } message: {
            Text(integrationResult?.description ?? "整合失敗")
        }
    }
    
    private func integrateLiDARCalibration() {
        Task {
            let result = await stickerModule.integrateWithLiDAR(imageJCore.liDARCalibrationModule)
            
            await MainActor.run {
                integrationResult = result
                showingIntegrationResult = true
            }
        }
    }
}

struct IntegrationResultCard: View {
    let result: IntegratedCalibrationResult
    
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Image(systemName: result.isSuccess ? "checkmark.circle.fill" : "xmark.circle.fill")
                    .foregroundColor(result.isSuccess ? .green : .red)
                Text("整合結果")
                    .font(.subheadline)
                    .fontWeight(.medium)
                Spacer()
            }
            
            if result.isSuccess {
                VStack(alignment: .leading, spacing: 4) {
                    Text("最終像素比例: \(String(format: "%.3f", result.pixelsPerMM)) pixels/mm")
                        .font(.caption)
                    Text("整合信心度: \(String(format: "%.1f", result.confidence * 100))%")
                        .font(.caption)
                    Text("校準來源: \(result.calibrationSource.description)")
                        .font(.caption)
                }
            } else {
                Text(result.error ?? "未知錯誤")
                    .font(.caption)
                    .foregroundColor(.red)
            }
        }
        .padding()
        .background(result.isSuccess ? Color.green.opacity(0.1) : Color.red.opacity(0.1))
        .cornerRadius(8)
    }
}

// MARK: - 輔助視圖

struct ImagePicker: UIViewControllerRepresentable {
    @Binding var selectedImage: UIImage?
    @Environment(\.dismiss) private var dismiss
    
    func makeUIViewController(context: Context) -> UIImagePickerController {
        let picker = UIImagePickerController()
        picker.delegate = context.coordinator
        picker.sourceType = .photoLibrary
        return picker
    }
    
    func updateUIViewController(_ uiViewController: UIImagePickerController, context: Context) {}
    
    func makeCoordinator() -> Coordinator {
        Coordinator(self)
    }
    
    class Coordinator: NSObject, UIImagePickerControllerDelegate, UINavigationControllerDelegate {
        let parent: ImagePicker
        
        init(_ parent: ImagePicker) {
            self.parent = parent
        }
        
        func imagePickerController(_ picker: UIImagePickerController, didFinishPickingMediaWithInfo info: [UIImagePickerController.InfoKey : Any]) {
            if let image = info[.originalImage] as? UIImage {
                parent.selectedImage = image
            }
            parent.dismiss()
        }
        
        func imagePickerControllerDidCancel(_ picker: UIImagePickerController) {
            parent.dismiss()
        }
    }
}

struct ManualROIEditor: View {
    let image: UIImage
    let initialROI: CGRect
    let onROIChanged: (CGRect) -> Void
    
    @Environment(\.dismiss) private var dismiss
    @State private var currentROI: CGRect
    
    init(image: UIImage, initialROI: CGRect, onROIChanged: @escaping (CGRect) -> Void) {
        self.image = image
        self.initialROI = initialROI
        self.onROIChanged = onROIChanged
        self._currentROI = State(initialValue: initialROI.isEmpty ? CGRect(x: 0.25, y: 0.25, width: 0.5, height: 0.5) : initialROI)
    }
    
    var body: some View {
        NavigationView {
            VStack {
                Text("手動繪製傷口ROI區域")
                    .font(.headline)
                    .padding()
                
                // 這裡應該實現一個可交互的ROI編輯視圖
                // 為了簡化，暫時顯示佔位符
                ZStack {
                    Image(uiImage: image)
                        .resizable()
                        .aspectRatio(contentMode: .fit)
                    
                    Rectangle()
                        .stroke(Color.red, lineWidth: 2)
                        .frame(
                            width: 200,
                            height: 150
                        )
                        .overlay(
                            Text("拖拽調整ROI")
                                .foregroundColor(.red)
                                .background(Color.white.opacity(0.8))
                        )
                }
                .padding()
                
                Button("完成") {
                    onROIChanged(currentROI)
                    dismiss()
                }
                .font(.headline)
                .foregroundColor(.white)
                .padding()
                .frame(maxWidth: .infinity)
                .background(Color.blue)
                .cornerRadius(10)
                .padding()
            }
            .navigationTitle("ROI 編輯")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    Button("取消") {
                        dismiss()
                    }
                }
            }
        }
    }
}

#Preview {
    CalibrationStickerView(imageJCore: ImageJCore())
}