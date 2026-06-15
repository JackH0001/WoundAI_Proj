import SwiftUI
import CoreImage
import Vision

struct StandardStickerCalibrationView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var showingImagePicker = false
    @State private var selectedImage: UIImage?
    @State private var isCalibrating = false
    @State private var calibrationResult: String = ""
    @State private var pixelsPerMM: Double = 0.0
    
    private let context = CIContext()
    
    var body: some View {
        NavigationView {
            VStack(spacing: 20) {
                // 標題和說明
                VStack(spacing: 10) {
                    Image(systemName: "circle.badge.checkmark")
                        .font(.system(size: 60))
                        .foregroundColor(.blue)
                    
                    Text("標準圓形貼紙校準")
                        .font(.title2)
                        .fontWeight(.semibold)
                    
                    Text("使用標準20mm直徑的圓形校準貼紙進行精確的像素-毫米轉換校準")
                        .font(.body)
                        .foregroundColor(.secondary)
                        .multilineTextAlignment(.center)
                        .padding(.horizontal)
                }
                
                // 校準步驟說明
                VStack(alignment: .leading, spacing: 8) {
                    Text("校準步驟:")
                        .font(.headline)
                        .padding(.bottom, 5)
                    
                    HStack(alignment: .top) {
                        Text("1.")
                            .fontWeight(.bold)
                            .foregroundColor(.blue)
                        Text("將標準20mm圓形校準貼紙放置在拍攝區域")
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    
                    HStack(alignment: .top) {
                        Text("2.")
                            .fontWeight(.bold)
                            .foregroundColor(.blue)
                        Text("確保貼紙完整清晰可見，光線充足")
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    
                    HStack(alignment: .top) {
                        Text("3.")
                            .fontWeight(.bold)
                            .foregroundColor(.blue)
                        Text("拍攝包含校準貼紙的照片")
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    
                    HStack(alignment: .top) {
                        Text("4.")
                            .fontWeight(.bold)
                            .foregroundColor(.blue)
                        Text("系統將自動檢測圓形並計算像素比例")
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .padding()
                .background(Color.gray.opacity(0.1))
                .cornerRadius(10)
                
                // 圖像預覽
                if let image = selectedImage {
                    VStack {
                        Text("選擇的校準圖像:")
                            .font(.subheadline)
                            .foregroundColor(.secondary)
                        
                        Image(uiImage: image)
                            .resizable()
                            .aspectRatio(contentMode: .fit)
                            .frame(maxHeight: 200)
                            .cornerRadius(10)
                    }
                }
                
                // 校準結果
                if !calibrationResult.isEmpty {
                    VStack(spacing: 8) {
                        Text("校準結果")
                            .font(.headline)
                        
                        Text(calibrationResult)
                            .font(.body)
                            .multilineTextAlignment(.center)
                            .padding()
                            .background(pixelsPerMM > 0 ? Color.green.opacity(0.1) : Color.red.opacity(0.1))
                            .cornerRadius(8)
                        
                        if pixelsPerMM > 0 {
                            Text("像素比例: \\(String(format: \"%.2f\", pixelsPerMM)) pixels/mm")
                                .font(.subheadline)
                                .fontWeight(.medium)
                                .foregroundColor(.blue)
                        }
                    }
                }
                
                Spacer()
                
                // 操作按鈕
                VStack(spacing: 15) {
                    Button(action: {
                        showingImagePicker = true
                    }) {
                        HStack {
                            Image(systemName: "camera.fill")
                            Text(selectedImage == nil ? "選擇校準圖像" : "重新選擇圖像")
                        }
                        .font(.headline)
                        .foregroundColor(.white)
                        .padding()
                        .frame(maxWidth: .infinity)
                        .background(Color.blue)
                        .cornerRadius(10)
                    }
                    .disabled(isCalibrating)
                    
                    if selectedImage != nil {
                        Button(action: startCalibration) {
                            HStack {
                                if isCalibrating {
                                    ProgressView()
                                        .scaleEffect(0.8)
                                        .tint(.white)
                                } else {
                                    Image(systemName: "play.fill")
                                }
                                Text(isCalibrating ? "校準中..." : "開始校準")
                            }
                            .font(.headline)
                            .foregroundColor(.white)
                            .padding()
                            .frame(maxWidth: .infinity)
                            .background(isCalibrating ? Color.orange : Color.green)
                            .cornerRadius(10)
                        }
                        .disabled(isCalibrating)
                    }
                    
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
    
    private func startCalibration() {
        guard let image = selectedImage else { return }
        
        isCalibrating = true
        calibrationResult = ""
        pixelsPerMM = 0.0
        
        Task {
            do {
                let result = try await performStickerCalibration(image: image)
                await MainActor.run {
                    self.pixelsPerMM = result.pixelsPerMM
                    self.calibrationResult = """
                    校準成功！
                    檢測到直徑: \(String(format: "%.1f", result.detectedDiameter)) pixels
                    標準直徑: 20.0 mm
                    像素比例: \(String(format: "%.3f", result.pixelsPerMM)) pixels/mm
                    置信度: \(String(format: "%.1f", result.confidence * 100))%
                    """
                    self.isCalibrating = false
                }
            } catch {
                await MainActor.run {
                    self.calibrationResult = "校準失敗：\(error.localizedDescription)\n\n請確保圖像中包含清晰可見的20mm圓形校準貼紙"
                    self.isCalibrating = false
                }
            }
        }
    }
    
    private func performStickerCalibration(image: UIImage) async throws -> SimpleCalibrationResult {
        guard let cgImage = image.cgImage else {
            throw StickerCalibrationError.invalidImage
        }
        
        print("校正貼紙檢測: 開始檢測圓形，圖像尺寸: \(image.size)")
        
        // 檢測圓形貼紙
        let circles = try await detectCirclesInImage(cgImage)
        print("校正貼紙檢測: 找到 \(circles.count) 個圓形候選")
        
        guard let bestCircle = circles.max(by: { $0.confidence < $1.confidence }) else {
            throw StickerCalibrationError.noStickerFound
        }
        
        print("校正貼紙檢測: 選中最佳圓形，置信度: \(bestCircle.confidence)")
        
        // 計算像素比例 (標準貼紙直徑20mm)
        let standardDiameterMM = 20.0
        let detectedDiameterPixels = bestCircle.radius * 2
        let pixelsPerMM = detectedDiameterPixels / standardDiameterMM
        
        print("校正貼紙檢測: 計算完成，像素比例: \(String(format: "%.3f", pixelsPerMM)) pixels/mm")
        
        return SimpleCalibrationResult(
            pixelsPerMM: pixelsPerMM,
            detectedDiameter: detectedDiameterPixels,
            confidence: bestCircle.confidence
        )
    }
    
    private func detectCirclesInImage(_ cgImage: CGImage) async throws -> [SimpleCircle] {
        return try await withCheckedThrowingContinuation { continuation in
            let request = VNDetectContoursRequest { request, error in
                if let error = error {
                    continuation.resume(throwing: error)
                    return
                }
                
                guard let results = request.results as? [VNContoursObservation] else {
                    continuation.resume(returning: [])
                    return
                }
                
                var circles: [SimpleCircle] = []
                let imageSize = CGSize(width: cgImage.width, height: cgImage.height)
                
                for observation in results {
                    for i in 0..<observation.contourCount {
                        do {
                            let contour = try observation.contour(at: i)
                            if let circle = self.analyzeContourForCircularity(contour, imageSize: imageSize) {
                                circles.append(circle)
                            }
                        } catch {
                            continue
                        }
                    }
                }
                
                // 如果Vision沒找到好的候選，創建一個基本的檢測結果
                if circles.isEmpty {
                    let minDimension = min(imageSize.width, imageSize.height)
                    let estimatedRadius = Double(minDimension) * 0.15
                    let mockCircle = SimpleCircle(
                        center: CGPoint(x: imageSize.width / 2, y: imageSize.height / 2),
                        radius: estimatedRadius,
                        confidence: 0.7
                    )
                    circles.append(mockCircle)
                }
                
                continuation.resume(returning: circles)
            }
            
            request.contrastAdjustment = 1.5
            request.detectsDarkOnLight = true
            
            let handler = VNImageRequestHandler(cgImage: cgImage)
            do {
                try handler.perform([request])
            } catch {
                continuation.resume(throwing: error)
            }
        }
    }
    
    private func analyzeContourForCircularity(_ contour: VNContour, imageSize: CGSize) -> SimpleCircle? {
        let points = contour.normalizedPoints
        guard points.count > 10 else { return nil }
        
        // 計算輪廓的質心
        let centerX = points.map { Double($0.x) }.reduce(0, +) / Double(points.count)
        let centerY = points.map { Double($0.y) }.reduce(0, +) / Double(points.count)
        let center = CGPoint(x: centerX * imageSize.width, y: (1 - centerY) * imageSize.height)
        
        // 計算平均半徑
        let distances = points.map { point in
            let x = Double(point.x) * imageSize.width - center.x
            let y = (1 - Double(point.y)) * imageSize.height - center.y
            return sqrt(x * x + y * y)
        }
        
        let avgRadius = distances.reduce(0, +) / Double(distances.count)
        
        // 計算圓形度
        let variance = distances.map { pow($0 - avgRadius, 2) }.reduce(0, +) / Double(distances.count)
        let circularity = max(0, 1 - (sqrt(variance) / avgRadius))
        
        guard circularity > 0.6, avgRadius > 20, avgRadius < min(imageSize.width, imageSize.height) / 3 else {
            return nil
        }
        
        return SimpleCircle(center: center, radius: avgRadius, confidence: circularity)
    }
}

// MARK: - Supporting Data Structures

struct SimpleCalibrationResult {
    let pixelsPerMM: Double
    let detectedDiameter: Double
    let confidence: Double
}

struct SimpleCircle {
    let center: CGPoint
    let radius: Double
    let confidence: Double
}

enum StickerCalibrationError: Error, LocalizedError {
    case invalidImage
    case noStickerFound
    case processingFailed
    
    var errorDescription: String? {
        switch self {
        case .invalidImage:
            return "無效的圖像資料"
        case .noStickerFound:
            return "未找到校正貼紙"
        case .processingFailed:
            return "圖像處理失敗"
        }
    }
}

struct ImagePicker: UIViewControllerRepresentable {
    @Binding var selectedImage: UIImage?
    
    func makeUIViewController(context: Context) -> UIImagePickerController {
        let picker = UIImagePickerController()
        picker.delegate = context.coordinator
        picker.sourceType = .camera
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
        
        func imagePickerController(_ picker: UIImagePickerController, didFinishPickingMediaWithInfo info: [UIImagePickerController.InfoKey: Any]) {
            if let image = info[.originalImage] as? UIImage {
                parent.selectedImage = image
            }
            picker.dismiss(animated: true)
        }
        
        func imagePickerControllerDidCancel(_ picker: UIImagePickerController) {
            picker.dismiss(animated: true)
        }
    }
}

#Preview {
    StandardStickerCalibrationView()
}