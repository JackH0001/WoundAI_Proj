import SwiftUI

struct CalibrationSelectionView: View {
    @StateObject private var rulerCalibrationModule = RulerCalibrationModule()
    @Environment(\.dismiss) private var dismiss
    @State private var selectedMethod: RulerCalibrationModule.CalibrationMethod = .sticker
    @State private var showingImagePicker = false
    @State private var selectedImage: UIImage?
    @State private var calibrationResult: CalibrationResult?
    @State private var isCalibrating = false
    @State private var errorMessage: String?
    
    var body: some View {
        NavigationView {
            VStack(spacing: 25) {
                // 標題區域
                VStack(spacing: 10) {
                    Image(systemName: "ruler.fill")
                        .font(.system(size: 60))
                        .foregroundColor(.blue)
                    
                    Text("校準系統")
                        .font(.title2)
                        .fontWeight(.semibold)
                    
                    Text("選擇適合的校準方法來確保測量準確性")
                        .font(.body)
                        .foregroundColor(.secondary)
                        .multilineTextAlignment(.center)
                }
                
                // 校準方法選擇
                VStack(alignment: .leading, spacing: 15) {
                    Text("校準方法")
                        .font(.headline)
                    
                    VStack(spacing: 12) {
                        ForEach(RulerCalibrationModule.CalibrationMethod.allCases, id: \.self) { method in
                            CalibrationMethodRow(
                                method: method,
                                isSelected: selectedMethod == method,
                                action: { selectedMethod = method }
                            )
                        }
                    }
                }
                .padding()
                .background(Color.gray.opacity(0.1))
                .cornerRadius(15)
                
                // 圖片選擇區域
                VStack(spacing: 15) {
                    if let image = selectedImage {
                        Image(uiImage: image)
                            .resizable()
                            .aspectRatio(contentMode: .fit)
                            .frame(maxHeight: 200)
                            .cornerRadius(10)
                            .overlay(
                                RoundedRectangle(cornerRadius: 10)
                                    .stroke(Color.blue, lineWidth: 2)
                            )
                    } else {
                        RoundedRectangle(cornerRadius: 10)
                            .fill(Color.gray.opacity(0.2))
                            .frame(height: 150)
                            .overlay(
                                VStack {
                                    Image(systemName: "photo.badge.plus")
                                        .font(.system(size: 40))
                                        .foregroundColor(.gray)
                                    Text("選擇校準圖像")
                                        .foregroundColor(.gray)
                                }
                            )
                    }
                    
                    Button(action: { showingImagePicker = true }) {
                        HStack {
                            Image(systemName: "photo.fill")
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
                
                // 校準結果顯示
                if let result = calibrationResult {
                    CalibrationResultView(result: result)
                }
                
                // 錯誤訊息
                if let error = errorMessage {
                    Text(error)
                        .foregroundColor(.red)
                        .padding()
                        .background(Color.red.opacity(0.1))
                        .cornerRadius(10)
                }
                
                Spacer()
                
                // 操作按鈕
                VStack(spacing: 15) {
                    Button(action: performCalibration) {
                        HStack {
                            if isCalibrating {
                                ProgressView()
                                    .scaleEffect(0.8)
                                    .foregroundColor(.white)
                            } else {
                                Image(systemName: "play.fill")
                            }
                            Text(isCalibrating ? "校準中..." : "開始校準")
                        }
                        .font(.headline)
                        .foregroundColor(.white)
                        .padding()
                        .frame(maxWidth: .infinity)
                        .background(canStartCalibration ? Color.green : Color.gray)
                        .cornerRadius(10)
                    }
                    .disabled(!canStartCalibration || isCalibrating)
                    
                    Button("完成") {
                        dismiss()
                    }
                    .font(.body)
                    .foregroundColor(.blue)
                }
            }
            .padding()
            .navigationTitle("校準設定")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("關閉") {
                        dismiss()
                    }
                }
            }
        }
        .sheet(isPresented: $showingImagePicker) {
            ImagePicker(selectedImage: $selectedImage)
        }
    }
    
    private var canStartCalibration: Bool {
        switch selectedMethod {
        case .sticker, .ruler:
            return selectedImage != nil
        case .lidar, .manual:
            return true
        }
    }
    
    private func performCalibration() {
        guard !isCalibrating else { return }
        
        Task { @MainActor in
            isCalibrating = true
            errorMessage = nil
            calibrationResult = nil
            
            do {
                let result: CalibrationResult
                
                switch selectedMethod {
                case .sticker, .ruler:
                    guard let image = selectedImage else {
                        throw CalibrationError.invalidImage
                    }
                    result = try await rulerCalibrationModule.performCalibration(from: image, method: selectedMethod)
                    
                case .lidar:
                    // 創建一個模擬圖像用於LiDAR校準
                    let mockImage = UIImage(systemName: "photo.fill") ?? UIImage()
                    result = try await rulerCalibrationModule.performCalibration(from: mockImage, method: .lidar)
                    
                case .manual:
                    // 創建一個模擬圖像用於手動校準
                    let mockImage = UIImage(systemName: "photo.fill") ?? UIImage()
                    result = try await rulerCalibrationModule.performCalibration(from: mockImage, method: .manual)
                }
                
                calibrationResult = result
                
            } catch {
                errorMessage = "校準失敗：\(error.localizedDescription)"
            }
            
            isCalibrating = false
        }
    }
}

struct CalibrationMethodRow: View {
    let method: RulerCalibrationModule.CalibrationMethod
    let isSelected: Bool
    let action: () -> Void
    
    var body: some View {
        Button(action: action) {
            HStack(spacing: 15) {
                Image(systemName: iconForMethod(method))
                    .font(.title2)
                    .foregroundColor(isSelected ? .white : .blue)
                    .frame(width: 30)
                
                VStack(alignment: .leading, spacing: 4) {
                    Text(method.rawValue)
                        .font(.headline)
                        .foregroundColor(isSelected ? .white : .primary)
                    
                    Text(descriptionForMethod(method))
                        .font(.caption)
                        .foregroundColor(isSelected ? .white.opacity(0.8) : .secondary)
                }
                
                Spacer()
                
                if isSelected {
                    Image(systemName: "checkmark.circle.fill")
                        .foregroundColor(.white)
                }
            }
            .padding()
            .background(isSelected ? Color.blue : Color.clear)
            .cornerRadius(10)
            .overlay(
                RoundedRectangle(cornerRadius: 10)
                    .stroke(Color.blue, lineWidth: isSelected ? 0 : 1)
            )
        }
        .buttonStyle(PlainButtonStyle())
    }
    
    private func iconForMethod(_ method: RulerCalibrationModule.CalibrationMethod) -> String {
        switch method {
        case .ruler:
            return "ruler.fill"
        case .sticker:
            return "circle.badge.checkmark"
        case .lidar:
            return "dot.radiowaves.left.and.right"
        case .manual:
            return "hand.tap.fill"
        }
    }
    
    private func descriptionForMethod(_ method: RulerCalibrationModule.CalibrationMethod) -> String {
        switch method {
        case .ruler:
            return "使用標準尺規進行校準"
        case .sticker:
            return "使用校正貼紙進行精確校準"
        case .lidar:
            return "使用LiDAR深度感測器校準"
        case .manual:
            return "手動輸入校準參數"
        }
    }
}

struct CalibrationResultView: View {
    let result: CalibrationResult
    
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Image(systemName: "checkmark.circle.fill")
                    .foregroundColor(.green)
                Text("校準結果")
                    .font(.headline)
            }
            
            VStack(spacing: 8) {
                HStack {
                    Text("校準方法:")
                    Spacer()
                    Text(result.methodDescription)
                        .fontWeight(.semibold)
                }
                
                HStack {
                    Text("像素比例:")
                    Spacer()
                    let cmPerPixel = 1.0 / (result.pixelPerMM * 10.0)
                    Text("\(String(format: "%.3f", result.pixelPerMM)) pixels/mm (\(String(format: "%.5f", cmPerPixel)) cm/pixel)")
                        .fontWeight(.semibold)
                }
                
                HStack {
                    Text("置信度:")
                    Spacer()
                    Text("\(String(format: "%.1f", result.confidence * 100))%")
                        .fontWeight(.semibold)
                        .foregroundColor(result.confidence > 0.8 ? .green : .orange)
                }
                
                HStack {
                    Text("準確度估算:")
                    Spacer()
                    Text(result.accuracyEstimate)
                        .fontWeight(.semibold)
                }
                
                HStack {
                    Text("狀態:")
                    Spacer()
                    Text(result.isReliable ? "可靠" : "需要重新校準")
                        .fontWeight(.semibold)
                        .foregroundColor(result.isReliable ? .green : .red)
                }
            }
        }
        .padding()
        .background(Color.green.opacity(0.1))
        .cornerRadius(15)
    }
}

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

#Preview {
    CalibrationSelectionView()
}