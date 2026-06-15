import SwiftUI
import UIKit
import Foundation

// MARK: - Cloud API Types (Temporary - should be in separate file)

struct CloudUploadResponse: Codable {
    let success: Bool
    let message: String
    let annotationId: String
    let qualityScore: Double
    let qualityStatus: String
    let bjwatScore: Int
    let revpwatScore: Int
    
    enum CodingKeys: String, CodingKey {
        case success
        case message
        case annotationId = "annotation_id"
        case qualityScore = "quality_score"
        case qualityStatus = "quality_status"
        case bjwatScore = "bjwat_score"
        case revpwatScore = "revpwat_score"
    }
}

// Mock Cloud API Service for compilation
class CloudAPIService: ObservableObject {
    static let shared = CloudAPIService()
    @Published var isUploading = false
    @Published var uploadProgress: Double = 0.0
    
    private init() {}
    
    func authenticate(doctorId: String, password: String) async throws {
        // Mock authentication
        try await Task.sleep(nanoseconds: 1_000_000_000)
    }
    
    func uploadAnnotation(annotationData: Data, image: UIImage?, doctorId: String, patientId: String?) async throws -> CloudUploadResponse {
        // Mock upload with progress
        await MainActor.run { isUploading = true }
        
        for progress in [0.2, 0.4, 0.6, 0.8, 1.0] {
            try await Task.sleep(nanoseconds: 500_000_000)
            await MainActor.run { uploadProgress = progress }
        }
        
        await MainActor.run { 
            isUploading = false
            uploadProgress = 0.0
        }
        
        return CloudUploadResponse(
            success: true,
            message: "上傳成功",
            annotationId: UUID().uuidString,
            qualityScore: 0.85,
            qualityStatus: "良好",
            bjwatScore: 15,
            revpwatScore: 65
        )
    }
}

struct AnnotationView: View {
    @StateObject private var annotationManager = WoundAnnotationManager.shared
    @StateObject private var cloudService = CloudAPIService.shared
    @State private var selectedImage: UIImage?
    @State private var selectedAnnotationType: AnnotationType = .woundBoundary
    @State private var showingImagePicker = false
    @State private var showingUploadSheet = false
    @State private var showingAuthSheet = false
    @State private var exportData: Data?
    @State private var showingROIMode = false
    @State private var currentROI: CGRect = .zero
    
    // 認證相關狀態
    @State private var doctorId = ""
    @State private var password = ""
    @State private var patientId = ""
    @State private var isAuthenticated = false
    
    // 上傳結果
    @State private var uploadResult: CloudUploadResponse?
    @State private var showingUploadResult = false
    
    var body: some View {
        NavigationView {
            VStack(spacing: 0) {
                // 工具列
                AnnotationToolbar(
                    selectedType: $selectedAnnotationType,
                    showingROIMode: $showingROIMode,
                    onImagePicker: { showingImagePicker = true },
                    onUpload: uploadToCloud,
                    onROIMode: { showingROIMode.toggle() }
                )
                
                // 主要內容區域
                if let image = selectedImage {
                    // 修復: 包裝在GeometryReader中以保持圖像比例
                    GeometryReader { geometry in
                        if showingROIMode {
                            ImprovedROIDrawingCanvasView(
                                image: image,
                                currentROI: $currentROI,
                                annotationManager: annotationManager,
                                containerSize: geometry.size
                            )
                        } else {
                            ImprovedAnnotationCanvasView(
                                image: image,
                                annotationType: selectedAnnotationType,
                                annotationManager: annotationManager,
                                containerSize: geometry.size
                            )
                        }
                    }
                    .aspectRatio(contentMode: .fit) // 保持圖像比例
                } else {
                    EmptyStateView()
                }
                
                // 增強型底部控制面板 - 始終顯示編輯控件
                EnhancedAnnotationControlPanel(
                    annotationManager: annotationManager,
                    onSave: saveAnnotation,
                    onAutoAnnotation: { runAutoAnnotation() },
                    hasAnnotations: annotationManager.currentAnnotation?.annotations.isEmpty == false
                )
            }
            .navigationTitle("傷口標註")
            .navigationBarTitleDisplayMode(.inline)
            .sheet(isPresented: $showingImagePicker) {
                ImagePicker(selectedImage: $selectedImage)
            }
            .sheet(isPresented: $showingAuthSheet) {
                NavigationView {
                    Form {
                        Section("醫師認證") {
                            TextField("醫師ID", text: $doctorId)
                            SecureField("密碼", text: $password)
                        }
                        
                        Section("病患資訊（選填）") {
                            TextField("病患ID", text: $patientId)
                        }
                        
                        Section {
                            Button("登入雲端平台") {
                                Task {
                                    try? await cloudService.authenticate(doctorId: doctorId, password: password)
                                    isAuthenticated = true
                                    showingAuthSheet = false
                                    showingUploadSheet = true
                                }
                            }
                            .disabled(doctorId.isEmpty || password.isEmpty)
                        }
                    }
                    .navigationTitle("雲端平台認證")
                    .navigationBarTitleDisplayMode(.inline)
                    .toolbar {
                        ToolbarItem(placement: .navigationBarTrailing) {
                            Button("取消") {
                                showingAuthSheet = false
                            }
                        }
                    }
                }
            }
            .sheet(isPresented: $showingUploadSheet) {
                NavigationView {
                    VStack(spacing: 20) {
                        if cloudService.isUploading {
                            VStack(spacing: 15) {
                                ProgressView(value: cloudService.uploadProgress)
                                    .progressViewStyle(LinearProgressViewStyle())
                                
                                Text("正在上傳標註資料至雲端...")
                                    .font(.headline)
                                
                                Text("\(Int(cloudService.uploadProgress * 100))%")
                                    .font(.title2)
                                    .fontWeight(.bold)
                                    .foregroundColor(.blue)
                            }
                            .padding()
                        } else {
                            VStack(alignment: .leading, spacing: 15) {
                                Text("上傳確認")
                                    .font(.title2)
                                    .fontWeight(.bold)
                                
                                Label("醫師ID: \(doctorId)", systemImage: "person.badge.key")
                                
                                if !patientId.isEmpty {
                                    Label("病患ID: \(patientId)", systemImage: "person")
                                }
                                
                                if let annotation = annotationManager.currentAnnotation {
                                    Label("標註項目: \(annotation.annotations.count) 個", systemImage: "doc.text")
                                }
                                
                                if selectedImage != nil {
                                    Label("包含影像檔案", systemImage: "photo")
                                }
                            }
                            .padding()
                            
                            Spacer()
                            
                            Button("確認上傳至雲端") {
                                uploadAnnotation()
                            }
                            .font(.headline)
                            .foregroundColor(.white)
                            .padding()
                            .frame(maxWidth: .infinity)
                            .background(Color.blue)
                            .cornerRadius(12)
                            .padding(.horizontal)
                        }
                    }
                    .padding()
                    .navigationTitle("雲端上傳")
                    .navigationBarTitleDisplayMode(.inline)
                    .toolbar {
                        ToolbarItem(placement: .navigationBarTrailing) {
                            Button("取消") {
                                showingUploadSheet = false
                            }
                            .disabled(cloudService.isUploading)
                        }
                    }
                }
            }
            .alert("上傳結果", isPresented: $showingUploadResult) {
                Button("確定") { }
            } message: {
                if let result = uploadResult {
                    Text("上傳成功！\n品質分數: \(String(format: "%.2f", result.qualityScore))\nBJWAT分數: \(result.bjwatScore)\nrevPWAT分數: \(result.revpwatScore)")
                }
            }
        }
    }
    
    private func saveAnnotation() {
        annotationManager.saveAnnotation()
        selectedImage = nil
    }
    
    private func uploadToCloud() {
        if isAuthenticated {
            showingUploadSheet = true
        } else {
            showingAuthSheet = true
        }
    }
    
    private func uploadAnnotation() {
        guard let annotationData = annotationManager.exportAnnotationAsCOCO() else {
            return
        }
        
        Task {
            do {
                let response = try await cloudService.uploadAnnotation(
                    annotationData: annotationData,
                    image: selectedImage,
                    doctorId: doctorId,
                    patientId: patientId.isEmpty ? nil : patientId
                )
                
                await MainActor.run {
                    uploadResult = response
                    showingUploadResult = true
                    showingUploadSheet = false
                }
            } catch {
                // Handle error
                print("上傳失敗: \(error)")
            }
        }
    }
    
    private func runAutoAnnotation() {
        guard let image = selectedImage else { return }
        
        print("🤖 開始AI自動標註...")
        
        Task {
            await performAutoAnnotation(image: image)
        }
    }
    
    private func performAutoAnnotation(image: UIImage) async {
        // 模擬AI標註過程
        let imageSize = image.size
        
        // 創建模擬的AI檢測結果
        let mockDetections = [
            (type: AnnotationType.woundBoundary, confidence: 0.95, rect: CGRect(x: imageSize.width * 0.2, y: imageSize.height * 0.3, width: imageSize.width * 0.6, height: imageSize.height * 0.4)),
            (type: AnnotationType.granulation, confidence: 0.87, rect: CGRect(x: imageSize.width * 0.3, y: imageSize.height * 0.4, width: imageSize.width * 0.4, height: imageSize.height * 0.2)),
            (type: AnnotationType.necrosis, confidence: 0.78, rect: CGRect(x: imageSize.width * 0.45, y: imageSize.height * 0.35, width: imageSize.width * 0.1, height: imageSize.height * 0.1))
        ]
        
        await MainActor.run {
            // 如果沒有當前標註，創建一個
            if annotationManager.currentAnnotation == nil {
                let _ = annotationManager.createAnnotation(image: image)
            }
            
            for detection in mockDetections {
                let coordinates = [
                    CGPoint(x: detection.rect.minX, y: detection.rect.minY),
                    CGPoint(x: detection.rect.maxX, y: detection.rect.minY),
                    CGPoint(x: detection.rect.maxX, y: detection.rect.maxY),
                    CGPoint(x: detection.rect.minX, y: detection.rect.maxY)
                ]
                
                let region = AnnotationRegion(
                    type: .rectangle,
                    coordinates: coordinates,
                    boundingBox: detection.rect,
                    area: Double(detection.rect.width * detection.rect.height)
                )
                
                let annotationItem = AnnotationItem(
                    id: UUID(),
                    type: detection.type,
                    region: region,
                    attributes: ["method": "ai", "confidence": detection.confidence],
                    confidence: detection.confidence
                )
                
                annotationManager.addAnnotationItem(annotationItem)
            }
            
            print("✅ AI自動標註完成，檢測到 \(mockDetections.count) 個區域")
        }
    }
}

// MARK: - 工具列

struct AnnotationToolbar: View {
    @Binding var selectedType: AnnotationType
    @Binding var showingROIMode: Bool
    let onImagePicker: () -> Void
    let onUpload: () -> Void
    let onROIMode: () -> Void
    
    var body: some View {
        VStack(spacing: 10) {
            // 標註類型選擇器
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 10) {
                    ForEach(AnnotationType.allCases, id: \.self) { type in
                        AnnotationTypeButton(
                            type: type,
                            isSelected: selectedType == type,
                            action: { selectedType = type }
                        )
                    }
                }
                .padding(.horizontal)
            }
            
            // 操作按鈕
            VStack(spacing: 10) {
                HStack(spacing: 15) {
                    Button(action: onImagePicker) {
                        Label("選擇影像", systemImage: "photo")
                            .font(.headline)
                            .foregroundColor(.white)
                            .padding()
                            .frame(maxWidth: .infinity)
                            .background(Color.blue)
                            .cornerRadius(10)
                    }
                    
                    Button(action: onROIMode) {
                        Label(showingROIMode ? "標註模式" : "ROI模式", systemImage: showingROIMode ? "pencil" : "viewfinder")
                            .font(.headline)
                            .foregroundColor(.white)
                            .padding()
                            .frame(maxWidth: .infinity)
                            .background(showingROIMode ? Color.orange : Color.purple)
                            .cornerRadius(10)
                    }
                }
                
                HStack(spacing: 15) {
                    Button(action: onUpload) {
                        Label("上傳", systemImage: "icloud.and.arrow.up")
                            .font(.headline)
                            .foregroundColor(.white)
                            .padding()
                            .frame(maxWidth: .infinity)
                            .background(Color.green)
                            .cornerRadius(10)
                    }
                }
            }
            .padding(.horizontal)
        }
        .padding(.vertical, 10)
        .background(Color.gray.opacity(0.1))
    }
}

// MARK: - 標註類型按鈕

struct AnnotationTypeButton: View {
    let type: AnnotationType
    let isSelected: Bool
    let action: () -> Void
    
    var body: some View {
        Button(action: action) {
            VStack(spacing: 5) {
                Circle()
                    .fill(Color(type.color))
                    .frame(width: 20, height: 20)
                
                Text(type.rawValue)
                    .font(.caption)
                    .foregroundColor(isSelected ? .blue : .primary)
            }
            .padding(.vertical, 8)
            .padding(.horizontal, 12)
            .background(
                RoundedRectangle(cornerRadius: 8)
                    .fill(isSelected ? Color.blue.opacity(0.1) : Color.clear)
            )
        }
    }
}

// MARK: - 標註畫布

struct AnnotationCanvasView: UIViewRepresentable {
    let image: UIImage
    let annotationType: AnnotationType
    @ObservedObject var annotationManager: WoundAnnotationManager
    
    func makeUIView(context: Context) -> AnnotationCanvas {
        let canvas = AnnotationCanvas()
        canvas.image = image
        canvas.annotationType = annotationType
        canvas.annotationManager = annotationManager
        return canvas
    }
    
    func updateUIView(_ uiView: AnnotationCanvas, context: Context) {
        uiView.annotationType = annotationType
    }
}

// MARK: - 標註畫布 UIView

class AnnotationCanvas: UIView {
    var image: UIImage? {
        didSet {
            setNeedsDisplay()
        }
    }
    
    var annotationType: AnnotationType = .woundBoundary {
        didSet {
            setNeedsDisplay()
        }
    }
    
    weak var annotationManager: WoundAnnotationManager?
    
    private var currentPath: UIBezierPath?
    private var annotationPaths: [UIBezierPath] = []
    private var annotationTypes: [AnnotationType] = []
    
    override init(frame: CGRect) {
        super.init(frame: frame)
        setupCanvas()
    }
    
    required init?(coder: NSCoder) {
        super.init(coder: coder)
        setupCanvas()
    }
    
    private func setupCanvas() {
        backgroundColor = .clear
        isMultipleTouchEnabled = false
        
        let panGesture = UIPanGestureRecognizer(target: self, action: #selector(handlePan(_:)))
        addGestureRecognizer(panGesture)
    }
    
    @objc private func handlePan(_ gesture: UIPanGestureRecognizer) {
        let point = gesture.location(in: self)
        
        switch gesture.state {
        case .began:
            currentPath = UIBezierPath()
            currentPath?.move(to: point)
            
        case .changed:
            currentPath?.addLine(to: point)
            setNeedsDisplay()
            
        case .ended:
            if let path = currentPath {
                annotationPaths.append(path)
                annotationTypes.append(annotationType)
                
                // 創建標註項目
                let coordinates = path.cgPath.points()
                let boundingBox = path.bounds
                let area = calculateArea(for: coordinates)
                
                let region = AnnotationRegion(
                    type: .polygon,
                    coordinates: coordinates,
                    boundingBox: boundingBox,
                    area: area
                )
                
                let annotationItem = AnnotationItem(
                    id: UUID(),
                    type: annotationType,
                    region: region,
                    attributes: [:],
                    confidence: 1.0
                )
                
                annotationManager?.addAnnotationItem(annotationItem)
            }
            
            currentPath = nil
            setNeedsDisplay()
            
        default:
            break
        }
    }
    
    private func calculateArea(for coordinates: [CGPoint]) -> Double {
        // 簡化的多邊形面積計算
        guard coordinates.count >= 3 else { return 0 }
        
        var area: Double = 0
        for i in 0..<coordinates.count {
            let j = (i + 1) % coordinates.count
            area += Double(coordinates[i].x * coordinates[j].y)
            area -= Double(coordinates[j].x * coordinates[i].y)
        }
        
        return Swift.abs(area) / 2.0
    }
    
    override func draw(_ rect: CGRect) {
        guard UIGraphicsGetCurrentContext() != nil else { return }
        
        // 繪製背景影像
        if let image = image {
            image.draw(in: bounds)
        }
        
        // 繪製已完成的標註
        for (index, path) in annotationPaths.enumerated() {
            let type = annotationTypes[index]
            type.color.setStroke()
            path.lineWidth = 3
            path.stroke()
            
            // 填充半透明
            type.color.withAlphaComponent(0.3).setFill()
            path.fill()
        }
        
        // 繪製當前正在繪製的路徑
        if let currentPath = currentPath {
            annotationType.color.setStroke()
            currentPath.lineWidth = 3
            currentPath.stroke()
        }
    }
}

// MARK: - 控制面板

struct AnnotationControlPanel: View {
    @ObservedObject var annotationManager: WoundAnnotationManager
    let onSave: () -> Void
    
    var body: some View {
        VStack(spacing: 15) {
            // 統計資訊
            if let annotation = annotationManager.currentAnnotation {
                AnnotationStatsView(annotation: annotation)
            }
            
            // 操作按鈕
            HStack(spacing: 15) {
                Button("清除") {
                    annotationManager.currentAnnotation = nil
                }
                .font(.headline)
                .foregroundColor(.red)
                .padding()
                .frame(maxWidth: .infinity)
                .background(Color.red.opacity(0.1))
                .cornerRadius(10)
                
                Button("儲存") {
                    onSave()
                }
                .font(.headline)
                .foregroundColor(.white)
                .padding()
                .frame(maxWidth: .infinity)
                .background(Color.blue)
                .cornerRadius(10)
            }
        }
        .padding()
        .background(Color.gray.opacity(0.1))
    }
}

// MARK: - 統計視圖

struct AnnotationStatsView: View {
    let annotation: WoundAnnotation
    
    var body: some View {
        VStack(spacing: 10) {
            Text("標註統計")
                .font(.headline)
            
            HStack {
                VStack {
                    Text("\(annotation.annotations.count)")
                        .font(.title2)
                        .fontWeight(.bold)
                    Text("標註項目")
                        .font(.caption)
                }
                
                Spacer()
                
                VStack {
                    Text(annotation.metadata.bjwatScores.severityLevel)
                        .font(.title2)
                        .fontWeight(.bold)
                    Text("BJWAT 嚴重度")
                        .font(.caption)
                }
                
                Spacer()
                
                VStack {
                    Text(annotation.metadata.revPWATScores.severityLevel)
                        .font(.title2)
                        .fontWeight(.bold)
                    Text("revPWAT 嚴重度")
                        .font(.caption)
                }
            }
        }
        .padding()
        .background(Color.white)
        .cornerRadius(10)
    }
}

// MARK: - 空狀態視圖

struct EmptyStateView: View {
    var body: some View {
        VStack(spacing: 20) {
            Image(systemName: "photo.badge.plus")
                .font(.system(size: 60))
                .foregroundColor(.gray)
            
            Text("選擇傷口影像開始標註")
                .font(.title2)
                .fontWeight(.medium)
            
            Text("支援多種標註類型，包括壞死組織、肉芽組織、分泌物等")
                .font(.body)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
        }
        .padding()
    }
}

// MARK: - 影像選擇器

struct ImagePicker: UIViewControllerRepresentable {
    @Binding var selectedImage: UIImage?
    @Environment(\.presentationMode) var presentationMode
    
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
            parent.presentationMode.wrappedValue.dismiss()
        }
        
        func imagePickerControllerDidCancel(_ picker: UIImagePickerController) {
            parent.presentationMode.wrappedValue.dismiss()
        }
    }
}

// MARK: - 分享表單

struct ShareSheet: UIViewControllerRepresentable {
    let items: [Any]
    
    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: items, applicationActivities: nil)
    }
    
    func updateUIViewController(_ uiViewController: UIActivityViewController, context: Context) {}
}

// MARK: - 擴展

// MARK: - ROI繪製畫布視圖

struct ROIDrawingCanvasView: UIViewRepresentable {
    let image: UIImage
    @Binding var currentROI: CGRect
    @ObservedObject var annotationManager: WoundAnnotationManager
    
    func makeUIView(context: Context) -> ROIDrawingCanvas {
        let canvas = ROIDrawingCanvas()
        canvas.image = image
        canvas.currentROI = currentROI
        canvas.annotationManager = annotationManager
        canvas.onROIUpdate = { roi in
            currentROI = roi
        }
        return canvas
    }
    
    func updateUIView(_ uiView: ROIDrawingCanvas, context: Context) {
        if uiView.currentROI != currentROI {
            uiView.currentROI = currentROI
            uiView.setNeedsDisplay()
        }
    }
}

// MARK: - ROI繪製畫布 UIView

class ROIDrawingCanvas: UIView {
    var image: UIImage? {
        didSet {
            setNeedsDisplay()
        }
    }
    
    var currentROI: CGRect = .zero {
        didSet {
            setNeedsDisplay()
        }
    }
    
    weak var annotationManager: WoundAnnotationManager?
    var onROIUpdate: ((CGRect) -> Void)?
    
    private var drawingROI = false
    private var startPoint: CGPoint = .zero
    private var endPoint: CGPoint = .zero
    
    override init(frame: CGRect) {
        super.init(frame: frame)
        setupCanvas()
    }
    
    required init?(coder: NSCoder) {
        super.init(coder: coder)
        setupCanvas()
    }
    
    private func setupCanvas() {
        backgroundColor = .clear
        isMultipleTouchEnabled = false
        
        let panGesture = UIPanGestureRecognizer(target: self, action: #selector(handlePan(_:)))
        addGestureRecognizer(panGesture)
    }
    
    @objc private func handlePan(_ gesture: UIPanGestureRecognizer) {
        let point = gesture.location(in: self)
        
        switch gesture.state {
        case .began:
            drawingROI = true
            startPoint = point
            endPoint = point
            
        case .changed:
            endPoint = point
            setNeedsDisplay()
            
        case .ended:
            drawingROI = false
            
            // 計算標準化的ROI座標
            let normalizedROI = CGRect(
                x: min(startPoint.x, endPoint.x) / bounds.width,
                y: min(startPoint.y, endPoint.y) / bounds.height,
                width: Swift.abs(endPoint.x - startPoint.x) / bounds.width,
                height: Swift.abs(endPoint.y - startPoint.y) / bounds.height
            )
            
            // 確保ROI有效
            if normalizedROI.width > 0.02 && normalizedROI.height > 0.02 {
                currentROI = normalizedROI
                onROIUpdate?(normalizedROI)
                
                // 創建ROI標註項目
                createROIAnnotationItem(normalizedROI)
            }
            
            setNeedsDisplay()
            
        default:
            break
        }
    }
    
    private func createROIAnnotationItem(_ roi: CGRect) {
        guard let annotationManager = annotationManager,
              let image = image else { return }
        
        // 如果沒有當前標註，創建一個
        if annotationManager.currentAnnotation == nil {
            let _ = annotationManager.createAnnotation(image: image)
        }
        
        // 計算實際像素座標
        let imageSize = image.size
        let actualROI = CGRect(
            x: roi.origin.x * imageSize.width,
            y: roi.origin.y * imageSize.height,
            width: roi.width * imageSize.width,
            height: roi.height * imageSize.height
        )
        
        // 創建ROI的四個角點
        let coordinates = [
            CGPoint(x: actualROI.minX, y: actualROI.minY),
            CGPoint(x: actualROI.maxX, y: actualROI.minY),
            CGPoint(x: actualROI.maxX, y: actualROI.maxY),
            CGPoint(x: actualROI.minX, y: actualROI.maxY)
        ]
        
        let region = AnnotationRegion(
            type: .rectangle,
            coordinates: coordinates,
            boundingBox: actualROI,
            area: Double(actualROI.width * actualROI.height)
        )
        
        let annotationItem = AnnotationItem(
            id: UUID(),
            type: .woundBoundary,
            region: region,
            attributes: ["isROI": true, "method": "manual"],
            confidence: 1.0
        )
        
        annotationManager.addAnnotationItem(annotationItem)
    }
    
    override func draw(_ rect: CGRect) {
        guard let context = UIGraphicsGetCurrentContext() else { return }
        
        // 繪製背景影像
        if let image = image {
            image.draw(in: bounds)
        }
        
        // 繪製當前ROI
        if currentROI != .zero && !drawingROI {
            let roiRect = CGRect(
                x: currentROI.origin.x * bounds.width,
                y: currentROI.origin.y * bounds.height,
                width: currentROI.width * bounds.width,
                height: currentROI.height * bounds.height
            )
            
            context.setStrokeColor(UIColor.blue.cgColor)
            context.setLineWidth(3)
            context.setFillColor(UIColor.blue.withAlphaComponent(0.2).cgColor)
            context.addRect(roiRect)
            context.drawPath(using: .fillStroke)
            
            // 繪製ROI標籤
            let label = "ROI"
            let attributes: [NSAttributedString.Key: Any] = [
                .foregroundColor: UIColor.white,
                .font: UIFont.boldSystemFont(ofSize: 16),
                .backgroundColor: UIColor.blue
            ]
            label.draw(at: CGPoint(x: roiRect.minX + 5, y: roiRect.minY + 5), withAttributes: attributes)
        }
        
        // 繪製正在繪製的ROI
        if drawingROI {
            let roiRect = CGRect(
                x: min(startPoint.x, endPoint.x),
                y: min(startPoint.y, endPoint.y),
                width: Swift.abs(endPoint.x - startPoint.x),
                height: Swift.abs(endPoint.y - startPoint.y)
            )
            
            context.setStrokeColor(UIColor.red.cgColor)
            context.setLineWidth(2)
            context.setFillColor(UIColor.red.withAlphaComponent(0.1).cgColor)
            context.addRect(roiRect)
            context.drawPath(using: .fillStroke)
        }
        
        // 繪製提示信息
        if currentROI == .zero && !drawingROI {
            let hintText = "拖拽以選擇傷口區域"
            let attributes: [NSAttributedString.Key: Any] = [
                .foregroundColor: UIColor.gray,
                .font: UIFont.systemFont(ofSize: 18),
                .backgroundColor: UIColor.white.withAlphaComponent(0.8)
            ]
            
            let textSize = hintText.size(withAttributes: attributes)
            let textPoint = CGPoint(
                x: (bounds.width - textSize.width) / 2,
                y: bounds.height - textSize.height - 20
            )
            
            hintText.draw(at: textPoint, withAttributes: attributes)
        }
    }
}

extension CGPath {
    func points() -> [CGPoint] {
        var resultPoints: [CGPoint] = []
        self.applyWithBlock { element in
            let pathPoints = element.pointee.points
            switch element.pointee.type {
            case .moveToPoint:
                resultPoints.append(pathPoints[0])
            case .addLineToPoint:
                resultPoints.append(pathPoints[0])
            case .addQuadCurveToPoint:
                resultPoints.append(pathPoints[1])
            case .addCurveToPoint:
                resultPoints.append(pathPoints[2])
            case .closeSubpath:
                break
            @unknown default:
                break
            }
        }
        return resultPoints
    }
}

// MARK: - 改進的標註畫布視圖

struct ImprovedAnnotationCanvasView: UIViewRepresentable {
    let image: UIImage
    let annotationType: AnnotationType
    @ObservedObject var annotationManager: WoundAnnotationManager
    let containerSize: CGSize
    
    func makeUIView(context: Context) -> ImprovedAnnotationCanvas {
        let canvas = ImprovedAnnotationCanvas()
        canvas.image = image
        canvas.annotationType = annotationType
        canvas.annotationManager = annotationManager
        canvas.containerSize = containerSize
        return canvas
    }
    
    func updateUIView(_ uiView: ImprovedAnnotationCanvas, context: Context) {
        uiView.annotationType = annotationType
        uiView.containerSize = containerSize
        uiView.setNeedsDisplay()
    }
}

class ImprovedAnnotationCanvas: UIView {
    var image: UIImage? {
        didSet {
            updateImageLayout()
        }
    }
    
    var annotationType: AnnotationType = .woundBoundary {
        didSet {
            setNeedsDisplay()
        }
    }
    
    var containerSize: CGSize = .zero {
        didSet {
            updateImageLayout()
        }
    }
    
    weak var annotationManager: WoundAnnotationManager?
    
    private var currentPath: UIBezierPath?
    private var imageDisplayRect: CGRect = .zero
    private var imageScale: CGFloat = 1.0
    private var selectedAnnotationID: UUID?
    
    override init(frame: CGRect) {
        super.init(frame: frame)
        setupCanvas()
    }
    
    required init?(coder: NSCoder) {
        super.init(coder: coder)
        setupCanvas()
    }
    
    private func setupCanvas() {
        backgroundColor = .clear
        isMultipleTouchEnabled = false
        
        let panGesture = UIPanGestureRecognizer(target: self, action: #selector(handlePan(_:)))
        let tapGesture = UITapGestureRecognizer(target: self, action: #selector(handleTap(_:)))
        
        addGestureRecognizer(panGesture)
        addGestureRecognizer(tapGesture)
    }
    
    private func updateImageLayout() {
        guard let image = image, containerSize != .zero else { return }
        
        // 計算圖像在容器中的顯示位置和大小，保持比例
        let imageAspectRatio = image.size.width / image.size.height
        let containerAspectRatio = containerSize.width / containerSize.height
        
        if imageAspectRatio > containerAspectRatio {
            // 圖像較寬，以容器寬度為準
            let displayHeight = containerSize.width / imageAspectRatio
            imageDisplayRect = CGRect(
                x: 0,
                y: (containerSize.height - displayHeight) / 2,
                width: containerSize.width,
                height: displayHeight
            )
        } else {
            // 圖像較高，以容器高度為準
            let displayWidth = containerSize.height * imageAspectRatio
            imageDisplayRect = CGRect(
                x: (containerSize.width - displayWidth) / 2,
                y: 0,
                width: displayWidth,
                height: containerSize.height
            )
        }
        
        imageScale = imageDisplayRect.width / image.size.width
        setNeedsDisplay()
    }
    
    override func layoutSubviews() {
        super.layoutSubviews()
        if containerSize != bounds.size {
            containerSize = bounds.size
        }
    }
    
    @objc private func handlePan(_ gesture: UIPanGestureRecognizer) {
        let point = gesture.location(in: self)
        
        // 確保點在圖像顯示區域內
        guard imageDisplayRect.contains(point) else { return }
        
        switch gesture.state {
        case .began:
            currentPath = UIBezierPath()
            currentPath?.move(to: point)
            
        case .changed:
            currentPath?.addLine(to: point)
            setNeedsDisplay()
            
        case .ended:
            if let path = currentPath {
                createAnnotationFromPath(path)
            }
            currentPath = nil
            setNeedsDisplay()
            
        default:
            break
        }
    }
    
    @objc private func handleTap(_ gesture: UITapGestureRecognizer) {
        let point = gesture.location(in: self)
        
        // 檢查是否點擊了現有標註以便選中編輯
        if let tappedID = findAnnotationAt(point) {
            selectedAnnotationID = (selectedAnnotationID == tappedID) ? nil : tappedID
            setNeedsDisplay()
        }
    }
    
    private func findAnnotationAt(_ point: CGPoint) -> UUID? {
        guard let annotation = annotationManager?.currentAnnotation else { return nil }
        
        for item in annotation.annotations.reversed() {
            let path = createUIBezierPath(from: item.region)
            if path.contains(point) {
                return item.id
            }
        }
        return nil
    }
    
    private func createUIBezierPath(from region: AnnotationRegion) -> UIBezierPath {
        let path = UIBezierPath()
        guard !region.coordinates.isEmpty else { return path }
        
        let firstViewPoint = convertImageToViewCoordinates(region.coordinates[0])
        path.move(to: firstViewPoint)
        
        for coordinate in region.coordinates.dropFirst() {
            let viewPoint = convertImageToViewCoordinates(coordinate)
            path.addLine(to: viewPoint)
        }
        
        path.close()
        return path
    }
    
    private func convertImageToViewCoordinates(_ imagePoint: CGPoint) -> CGPoint {
        guard let image = image else { return imagePoint }
        
        let relativeX = imagePoint.x / image.size.width
        let relativeY = imagePoint.y / image.size.height
        
        return CGPoint(
            x: imageDisplayRect.minX + relativeX * imageDisplayRect.width,
            y: imageDisplayRect.minY + relativeY * imageDisplayRect.height
        )
    }
    
    private func convertViewToImageCoordinates(_ viewPoint: CGPoint) -> CGPoint {
        guard let image = image else { return viewPoint }
        
        let relativeX = (viewPoint.x - imageDisplayRect.minX) / imageDisplayRect.width
        let relativeY = (viewPoint.y - imageDisplayRect.minY) / imageDisplayRect.height
        
        return CGPoint(
            x: relativeX * image.size.width,
            y: relativeY * image.size.height
        )
    }
    
    private func createAnnotationFromPath(_ path: UIBezierPath) {
        guard let image = image,
              let annotationManager = annotationManager else { return }
        
        // 如果沒有當前標註，創建一個
        if annotationManager.currentAnnotation == nil {
            let _ = annotationManager.createAnnotation(image: image)
        }
        
        let imageCoordinates = path.cgPath.points().map { convertViewToImageCoordinates($0) }
        let viewBoundingBox = path.bounds
        let imageBoundingBox = CGRect(
            x: convertViewToImageCoordinates(viewBoundingBox.origin).x,
            y: convertViewToImageCoordinates(viewBoundingBox.origin).y,
            width: viewBoundingBox.width / imageScale,
            height: viewBoundingBox.height / imageScale
        )
        
        let region = AnnotationRegion(
            type: .polygon,
            coordinates: imageCoordinates,
            boundingBox: imageBoundingBox,
            area: calculatePolygonArea(imageCoordinates)
        )
        
        let annotationItem = AnnotationItem(
            id: UUID(),
            type: annotationType,
            region: region,
            attributes: ["method": "manual"],
            confidence: 1.0
        )
        
        annotationManager.addAnnotationItem(annotationItem)
    }
    
    private func calculatePolygonArea(_ coordinates: [CGPoint]) -> Double {
        guard coordinates.count >= 3 else { return 0 }
        
        var area: Double = 0
        for i in 0..<coordinates.count {
            let j = (i + 1) % coordinates.count
            area += Double(coordinates[i].x * coordinates[j].y)
            area -= Double(coordinates[j].x * coordinates[i].y)
        }
        
        return abs(area) / 2.0
    }
    
    override func draw(_ rect: CGRect) {
        guard let context = UIGraphicsGetCurrentContext() else { return }
        
        context.clear(rect)
        
        // 繪製圖像
        if let image = image {
            image.draw(in: imageDisplayRect)
        }
        
        // 繪製現有標註
        drawExistingAnnotations(context)
        
        // 繪製當前繪製路徑
        if let currentPath = currentPath {
            context.setStrokeColor(annotationType.color.cgColor)
            context.setLineWidth(3)
            context.addPath(currentPath.cgPath)
            context.strokePath()
        }
    }
    
    private func drawExistingAnnotations(_ context: CGContext) {
        guard let annotation = annotationManager?.currentAnnotation else { return }
        
        for item in annotation.annotations {
            let path = createUIBezierPath(from: item.region)
            let isSelected = item.id == selectedAnnotationID
            
            // 填充
            let fillColor = isSelected ? item.type.color.withAlphaComponent(0.5) : item.type.color.withAlphaComponent(0.3)
            context.setFillColor(fillColor.cgColor)
            context.addPath(path.cgPath)
            context.fillPath()
            
            // 描邊
            let strokeColor = isSelected ? UIColor.yellow : item.type.color
            context.setStrokeColor(strokeColor.cgColor)
            context.setLineWidth(isSelected ? 4 : 2)
            context.addPath(path.cgPath)
            context.strokePath()
            
            // 繪製標籤
            drawAnnotationLabel(context, for: item, at: path.bounds.origin)
        }
    }
    
    private func drawAnnotationLabel(_ context: CGContext, for item: AnnotationItem, at point: CGPoint) {
        let confidence = item.attributes["confidence"] as? Double ?? 1.0
        let method = item.attributes["method"] as? String ?? "manual"
        let label = "\(item.type.rawValue) (\(String(format: "%.1f", confidence * 100))%)"
        
        let attributes: [NSAttributedString.Key: Any] = [
            .foregroundColor: UIColor.white,
            .font: UIFont.systemFont(ofSize: 12),
            .backgroundColor: item.type.color
        ]
        
        let labelSize = label.size(withAttributes: attributes)
        let labelRect = CGRect(
            x: point.x,
            y: point.y - labelSize.height - 2,
            width: labelSize.width + 8,
            height: labelSize.height + 4
        )
        
        context.setFillColor(item.type.color.cgColor)
        context.fill(labelRect)
        
        label.draw(in: labelRect.insetBy(dx: 4, dy: 2), withAttributes: attributes)
        
        // AI標註標識
        if method == "ai" {
            let aiIcon = "🤖"
            let iconPoint = CGPoint(x: labelRect.maxX + 2, y: labelRect.minY)
            aiIcon.draw(at: iconPoint, withAttributes: [.font: UIFont.systemFont(ofSize: 12)])
        }
    }
}

// MARK: - 改進的ROI畫布視圖

struct ImprovedROIDrawingCanvasView: UIViewRepresentable {
    let image: UIImage
    @Binding var currentROI: CGRect
    @ObservedObject var annotationManager: WoundAnnotationManager
    let containerSize: CGSize
    
    func makeUIView(context: Context) -> ImprovedROIDrawingCanvas {
        let canvas = ImprovedROIDrawingCanvas()
        canvas.image = image
        canvas.currentROI = currentROI
        canvas.annotationManager = annotationManager
        canvas.containerSize = containerSize
        canvas.onROIUpdate = { roi in
            currentROI = roi
        }
        return canvas
    }
    
    func updateUIView(_ uiView: ImprovedROIDrawingCanvas, context: Context) {
        uiView.containerSize = containerSize
        if uiView.currentROI != currentROI {
            uiView.currentROI = currentROI
            uiView.setNeedsDisplay()
        }
    }
}

class ImprovedROIDrawingCanvas: UIView {
    var image: UIImage? {
        didSet {
            updateImageLayout()
        }
    }
    
    var currentROI: CGRect = .zero {
        didSet {
            setNeedsDisplay()
        }
    }
    
    var containerSize: CGSize = .zero {
        didSet {
            updateImageLayout()
        }
    }
    
    weak var annotationManager: WoundAnnotationManager?
    var onROIUpdate: ((CGRect) -> Void)?
    
    private var drawingROI = false
    private var startPoint: CGPoint = .zero
    private var endPoint: CGPoint = .zero
    private var imageDisplayRect: CGRect = .zero
    private var imageScale: CGFloat = 1.0
    
    override init(frame: CGRect) {
        super.init(frame: frame)
        setupCanvas()
    }
    
    required init?(coder: NSCoder) {
        super.init(coder: coder)
        setupCanvas()
    }
    
    private func setupCanvas() {
        backgroundColor = .clear
        isMultipleTouchEnabled = false
        
        let panGesture = UIPanGestureRecognizer(target: self, action: #selector(handlePan(_:)))
        addGestureRecognizer(panGesture)
    }
    
    private func updateImageLayout() {
        guard let image = image, containerSize != .zero else { return }
        
        // 計算圖像在容器中的顯示位置和大小，保持比例 - 修復圖像扭曲問題
        let imageAspectRatio = image.size.width / image.size.height
        let containerAspectRatio = containerSize.width / containerSize.height
        
        if imageAspectRatio > containerAspectRatio {
            // 圖像較寬
            let displayHeight = containerSize.width / imageAspectRatio
            imageDisplayRect = CGRect(
                x: 0,
                y: (containerSize.height - displayHeight) / 2,
                width: containerSize.width,
                height: displayHeight
            )
        } else {
            // 圖像較高
            let displayWidth = containerSize.height * imageAspectRatio
            imageDisplayRect = CGRect(
                x: (containerSize.width - displayWidth) / 2,
                y: 0,
                width: displayWidth,
                height: containerSize.height
            )
        }
        
        imageScale = imageDisplayRect.width / image.size.width
        setNeedsDisplay()
    }
    
    override func layoutSubviews() {
        super.layoutSubviews()
        if containerSize != bounds.size {
            containerSize = bounds.size
        }
    }
    
    @objc private func handlePan(_ gesture: UIPanGestureRecognizer) {
        let point = gesture.location(in: self)
        
        // 確保點在圖像顯示區域內
        guard imageDisplayRect.contains(point) else { return }
        
        switch gesture.state {
        case .began:
            drawingROI = true
            startPoint = point
            endPoint = point
            
        case .changed:
            endPoint = point
            setNeedsDisplay()
            
        case .ended:
            drawingROI = false
            
            // 轉換為圖像座標並創建ROI
            let imageStart = convertViewToImageCoordinates(startPoint)
            let imageEnd = convertViewToImageCoordinates(endPoint)
            
            let normalizedROI = CGRect(
                x: min(imageStart.x, imageEnd.x) / image!.size.width,
                y: min(imageStart.y, imageEnd.y) / image!.size.height,
                width: abs(imageEnd.x - imageStart.x) / image!.size.width,
                height: abs(imageEnd.y - imageStart.y) / image!.size.height
            )
            
            // 確保ROI有效
            if normalizedROI.width > 0.02 && normalizedROI.height > 0.02 {
                currentROI = normalizedROI
                onROIUpdate?(normalizedROI)
                createROIAnnotationItem(normalizedROI)
            }
            
            setNeedsDisplay()
            
        default:
            break
        }
    }
    
    private func convertViewToImageCoordinates(_ viewPoint: CGPoint) -> CGPoint {
        guard let image = image else { return viewPoint }
        
        let relativeX = (viewPoint.x - imageDisplayRect.minX) / imageDisplayRect.width
        let relativeY = (viewPoint.y - imageDisplayRect.minY) / imageDisplayRect.height
        
        return CGPoint(
            x: relativeX * image.size.width,
            y: relativeY * image.size.height
        )
    }
    
    private func createROIAnnotationItem(_ roi: CGRect) {
        guard let annotationManager = annotationManager,
              let image = image else { return }
        
        // 如果沒有當前標註，創建一個
        if annotationManager.currentAnnotation == nil {
            let _ = annotationManager.createAnnotation(image: image)
        }
        
        let imageROI = CGRect(
            x: roi.origin.x * image.size.width,
            y: roi.origin.y * image.size.height,
            width: roi.width * image.size.width,
            height: roi.height * image.size.height
        )
        
        let coordinates = [
            CGPoint(x: imageROI.minX, y: imageROI.minY),
            CGPoint(x: imageROI.maxX, y: imageROI.minY),
            CGPoint(x: imageROI.maxX, y: imageROI.maxY),
            CGPoint(x: imageROI.minX, y: imageROI.maxY)
        ]
        
        let region = AnnotationRegion(
            type: .rectangle,
            coordinates: coordinates,
            boundingBox: imageROI,
            area: Double(imageROI.width * imageROI.height)
        )
        
        let annotationItem = AnnotationItem(
            id: UUID(),
            type: .woundBoundary,
            region: region,
            attributes: ["isROI": true, "method": "manual"],
            confidence: 1.0
        )
        
        annotationManager.addAnnotationItem(annotationItem)
    }
    
    override func draw(_ rect: CGRect) {
        guard let context = UIGraphicsGetCurrentContext() else { return }
        
        context.clear(rect)
        
        // 繪製圖像
        if let image = image {
            image.draw(in: imageDisplayRect)
        }
        
        // 繪製現有ROI
        if currentROI != .zero && !drawingROI {
            drawExistingROI(context)
        }
        
        // 繪製正在繪製的ROI
        if drawingROI {
            drawCurrentROI(context)
        }
        
        // 繪製提示信息
        if currentROI == .zero && !drawingROI {
            drawHintText(context)
        }
    }
    
    private func drawExistingROI(_ context: CGContext) {
        guard let image = image else { return }
        
        let roiRect = CGRect(
            x: imageDisplayRect.minX + currentROI.origin.x * imageDisplayRect.width,
            y: imageDisplayRect.minY + currentROI.origin.y * imageDisplayRect.height,
            width: currentROI.width * imageDisplayRect.width,
            height: currentROI.height * imageDisplayRect.height
        )
        
        context.setStrokeColor(UIColor.blue.cgColor)
        context.setLineWidth(3)
        context.setFillColor(UIColor.blue.withAlphaComponent(0.2).cgColor)
        context.addRect(roiRect)
        context.drawPath(using: .fillStroke)
        
        // 繪製ROI標籤
        let label = "ROI區域"
        let attributes: [NSAttributedString.Key: Any] = [
            .foregroundColor: UIColor.white,
            .font: UIFont.boldSystemFont(ofSize: 16),
            .backgroundColor: UIColor.blue
        ]
        
        let labelSize = label.size(withAttributes: attributes)
        let labelRect = CGRect(
            x: roiRect.minX + 8,
            y: roiRect.minY + 8,
            width: labelSize.width + 12,
            height: labelSize.height + 8
        )
        
        context.setFillColor(UIColor.blue.cgColor)
        context.fill(labelRect)
        
        label.draw(in: labelRect.insetBy(dx: 6, dy: 4), withAttributes: attributes)
    }
    
    private func drawCurrentROI(_ context: CGContext) {
        let roiRect = CGRect(
            x: min(startPoint.x, endPoint.x),
            y: min(startPoint.y, endPoint.y),
            width: abs(endPoint.x - startPoint.x),
            height: abs(endPoint.y - startPoint.y)
        )
        
        context.setStrokeColor(UIColor.orange.cgColor)
        context.setLineWidth(3)
        context.setFillColor(UIColor.orange.withAlphaComponent(0.2).cgColor)
        context.addRect(roiRect)
        context.drawPath(using: .fillStroke)
    }
    
    private func drawHintText(_ context: CGContext) {
        let hintText = "拖拽以選擇感興趣區域 (ROI)"
        let attributes: [NSAttributedString.Key: Any] = [
            .foregroundColor: UIColor.darkGray,
            .font: UIFont.systemFont(ofSize: 18, weight: .medium),
            .backgroundColor: UIColor.white.withAlphaComponent(0.9)
        ]
        
        let textSize = hintText.size(withAttributes: attributes)
        let textRect = CGRect(
            x: (bounds.width - textSize.width - 20) / 2,
            y: bounds.height - textSize.height - 30,
            width: textSize.width + 20,
            height: textSize.height + 10
        )
        
        context.setFillColor(UIColor.white.withAlphaComponent(0.9).cgColor)
        context.fill(textRect)
        
        hintText.draw(in: textRect.insetBy(dx: 10, dy: 5), withAttributes: attributes)
    }
}

// MARK: - 增強型控制面板

struct EnhancedAnnotationControlPanel: View {
    @ObservedObject var annotationManager: WoundAnnotationManager
    let onSave: () -> Void
    let onAutoAnnotation: () -> Void
    let hasAnnotations: Bool
    
    @State private var selectedAnnotationID: UUID?
    @State private var showingDeleteConfirmation = false
    
    var body: some View {
        VStack(spacing: 15) {
            // 統計資訊
            HStack(spacing: 20) {
                if let annotation = annotationManager.currentAnnotation {
                    Label("\(annotation.annotations.count)", systemImage: "tag.fill")
                        .font(.headline)
                        .foregroundColor(.blue)
                    
                    Label("\(annotation.metadata.bjwatScores.severityLevel)", systemImage: "heart.text.square")
                        .font(.headline)
                        .foregroundColor(.orange)
                    
                    Label("\(annotation.metadata.revPWATScores.severityLevel)", systemImage: "cross.case")
                        .font(.headline)
                        .foregroundColor(.red)
                } else {
                    Label("無標註", systemImage: "doc.text")
                        .font(.headline)
                        .foregroundColor(.gray)
                }
                
                Spacer()
                
                // 操作提示
                Text("點擊標註可選中編輯")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
            
            // 操作按鈕組 - 始終顯示編輯控件
            HStack(spacing: 10) {
                Button("AI自動標註") {
                    onAutoAnnotation()
                }
                .buttonStyle(AIButtonStyle())
                
                Button("清除全部") {
                    clearAllAnnotations()
                }
                .buttonStyle(ClearButtonStyle())
                .disabled(!hasAnnotations)
                
                Button("儲存結果") {
                    onSave()
                }
                .buttonStyle(SaveButtonStyle())
                .disabled(!hasAnnotations)
            }
            
            // 標註清單（如果有標註）
            if let annotation = annotationManager.currentAnnotation, !annotation.annotations.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 10) {
                        ForEach(Array(annotation.annotations.enumerated()), id: \.element.id) { index, item in
                            AnnotationItemChip(
                                item: item,
                                index: index,
                                isSelected: selectedAnnotationID == item.id,
                                onSelect: { selectedAnnotationID = item.id },
                                onDelete: { deleteAnnotation(item.id) }
                            )
                        }
                    }
                    .padding(.horizontal)
                }
            }
        }
        .padding()
        .background(Color.gray.opacity(0.05))
        .cornerRadius(15)
    }
    
    private func clearAllAnnotations() {
        guard var annotation = annotationManager.currentAnnotation else { return }
        annotation.annotations.removeAll()
        annotationManager.currentAnnotation = annotation
        selectedAnnotationID = nil
    }
    
    private func deleteAnnotation(_ id: UUID) {
        guard var annotation = annotationManager.currentAnnotation else { return }
        annotation.annotations.removeAll { $0.id == id }
        annotationManager.currentAnnotation = annotation
        
        if selectedAnnotationID == id {
            selectedAnnotationID = nil
        }
    }
}

// MARK: - 標註項目晶片

struct AnnotationItemChip: View {
    let item: AnnotationItem
    let index: Int
    let isSelected: Bool
    let onSelect: () -> Void
    let onDelete: () -> Void
    
    var body: some View {
        VStack(spacing: 4) {
            HStack(spacing: 6) {
                Circle()
                    .fill(Color(item.type.color))
                    .frame(width: 10, height: 10)
                
                Text("#\(index + 1)")
                    .font(.caption2)
                    .fontWeight(.bold)
                
                if item.attributes["method"] as? String == "ai" {
                    Text("🤖")
                        .font(.caption2)
                }
            }
            
            Text(item.type.rawValue)
                .font(.caption2)
                .lineLimit(1)
            
            if let confidence = item.attributes["confidence"] as? Double {
                Text("\(String(format: "%.0f", confidence * 100))%")
                    .font(.caption2)
                    .foregroundColor(.secondary)
            }
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 6)
        .background(
            RoundedRectangle(cornerRadius: 8)
                .fill(isSelected ? Color.blue.opacity(0.2) : Color.gray.opacity(0.1))
                .overlay(
                    RoundedRectangle(cornerRadius: 8)
                        .stroke(isSelected ? Color.blue : Color.clear, lineWidth: 2)
                )
        )
        .onTapGesture {
            onSelect()
        }
        .onLongPressGesture {
            onDelete()
        }
    }
}

// MARK: - 按鈕樣式

struct AIButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.headline)
            .foregroundColor(.white)
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
            .background(.purple)
            .cornerRadius(10)
            .scaleEffect(configuration.isPressed ? 0.95 : 1.0)
    }
}

struct ClearButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.headline)
            .foregroundColor(.white)
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
            .background(.orange)
            .cornerRadius(10)
            .scaleEffect(configuration.isPressed ? 0.95 : 1.0)
    }
}

struct SaveButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.headline)
            .foregroundColor(.white)
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
            .background(.green)
            .cornerRadius(10)
            .scaleEffect(configuration.isPressed ? 0.95 : 1.0)
    }
} 