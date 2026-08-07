import SwiftUI
import UIKit
import Vision
import CoreML
import Combine

// MARK: - 增強型傷口標註視圖
struct EnhancedAnnotationView: View {
    @StateObject private var annotationManager = WoundAnnotationManager.shared
    @StateObject private var aiAssistant = AIAnnotationAssistant()
    @State private var selectedImage: UIImage?
    @State private var selectedAnnotationType: AnnotationType = .woundBoundary
    @State private var showingImagePicker = false
    @State private var annotationMode: AnnotationMode = .manual
    @State private var showingROISelector = false
    @State private var currentROI: CGRect = .zero
    @State private var showingEditPanel = false
    @State private var isProcessingAI = false
    @State private var aiProgress: Double = 0.0
    
    // 編輯狀態
    @State private var selectedAnnotationID: UUID?
    @State private var showingConfirmDelete = false
    @State private var showingExportOptions = false
    
    var body: some View {
        NavigationView {
            GeometryReader { geometry in
                VStack(spacing: 0) {
                    // 頂部工具列
                    enhancedToolbar
                    
                    // 主要內容區域 - 修復圖像比例扭曲問題
                    if let image = selectedImage {
                        ZStack {
                            // 背景
                            Color.black.opacity(0.1)
                                .ignoresSafeArea()
                            
                            // 圖像和標註畫布
                            EnhancedAnnotationCanvas(
                                image: image,
                                annotationType: selectedAnnotationType,
                                annotationMode: annotationMode,
                                currentROI: $currentROI,
                                selectedAnnotationID: $selectedAnnotationID,
                                showingROISelector: $showingROISelector,
                                annotationManager: annotationManager,
                                aiAssistant: aiAssistant
                            )
                            .aspectRatio(contentMode: .fit) // 修復: 保持圖像比例
                            .clipped()
                            
                            // AI處理進度覆蓋
                            if isProcessingAI {
                                aiProcessingOverlay
                            }
                        }
                    } else {
                        // 空狀態
                        emptyStateView
                    }
                    
                    // 底部控制面板 - 始終顯示編輯控件
                    enhancedControlPanel
                }
            }
            .navigationTitle("智能傷口標註")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                toolbarItems
            }
        }
        .sheet(isPresented: $showingImagePicker) {
            PhotoLibraryImagePicker(selectedImage: $selectedImage)
        }
        .sheet(isPresented: $showingEditPanel) {
            if let selectedID = selectedAnnotationID,
               let annotation = annotationManager.currentAnnotation?.annotations.first(where: { $0.id == selectedID }) {
                AnnotationEditPanel(annotation: annotation, annotationManager: annotationManager)
            }
        }
        .alert("刪除標註", isPresented: $showingConfirmDelete) {
            Button("取消", role: .cancel) { }
            Button("刪除", role: .destructive) {
                deleteSelectedAnnotation()
            }
        } message: {
            Text("確定要刪除選中的標註嗎？")
        }
        .onChange(of: selectedImage) { image in
            if let image = image {
                initializeAnnotationForImage(image)
            }
        }
    }
    
    // MARK: - 增強型工具列
    private var enhancedToolbar: some View {
        VStack(spacing: 12) {
            // 模式切換
            HStack(spacing: 15) {
                ForEach(AnnotationMode.allCases, id: \.self) { mode in
                    Button(action: { 
                        annotationMode = mode
                        if mode == .autoAI {
                            runAIAnnotation()
                        }
                    }) {
                        HStack(spacing: 6) {
                            Image(systemName: mode.icon)
                            Text(mode.displayName)
                        }
                        .font(.caption)
                        .foregroundColor(annotationMode == mode ? .white : .primary)
                        .padding(.horizontal, 12)
                        .padding(.vertical, 6)
                        .background(
                            RoundedRectangle(cornerRadius: 8)
                                .fill(annotationMode == mode ? Color.blue : Color.gray.opacity(0.2))
                        )
                    }
                }
            }
            
            // 標註類型選擇器
            if annotationMode == .manual {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 10) {
                        ForEach(AnnotationType.allCases, id: \.self) { type in
                            AnnotationTypeChip(
                                type: type,
                                isSelected: selectedAnnotationType == type,
                                action: { selectedAnnotationType = type }
                            )
                        }
                    }
                    .padding(.horizontal)
                }
            }
            
            // 操作按鈕組
            HStack(spacing: 10) {
                Button("選擇圖像") {
                    showingImagePicker = true
                }
                .buttonStyle(PrimaryButtonStyle())
                
                Button("ROI選區") {
                    showingROISelector.toggle()
                }
                .buttonStyle(SecondaryButtonStyle())
                .foregroundColor(showingROISelector ? .white : .blue)
                .background(showingROISelector ? Color.orange : Color.blue.opacity(0.1))
                
                if selectedImage != nil {
                    Button("自動標註") {
                        runAIAnnotation()
                    }
                    .buttonStyle(AccentButtonStyle())
                    .disabled(isProcessingAI)
                }
            }
        }
        .padding()
        .background(Color.gray.opacity(0.05))
    }
    
    // MARK: - AI處理進度覆蓋
    private var aiProcessingOverlay: some View {
        VStack(spacing: 20) {
            ProgressView(value: aiProgress)
                .progressViewStyle(LinearProgressViewStyle(tint: .blue))
                .frame(maxWidth: 200)
            
            Text("AI正在分析傷口組織...")
                .font(.headline)
                .foregroundColor(.primary)
            
            Text("\(Int(aiProgress * 100))%")
                .font(.title2)
                .fontWeight(.bold)
                .foregroundColor(.blue)
        }
        .padding(30)
        .background(
            RoundedRectangle(cornerRadius: 15)
                .fill(Color.white)
                .shadow(radius: 10)
        )
    }
    
    // MARK: - 空狀態視圖
    private var emptyStateView: some View {
        VStack(spacing: 25) {
            Image(systemName: "photo.badge.plus")
                .font(.system(size: 80))
                .foregroundColor(.gray)
            
            Text("智能傷口標註系統")
                .font(.title2)
                .fontWeight(.bold)
            
            VStack(spacing: 8) {
                Text("• 自動AI組織識別")
                Text("• 精確ROI區域選擇")
                Text("• 多種標註工具")
                Text("• 即時編輯和調整")
            }
            .font(.body)
            .foregroundColor(.secondary)
        }
        .padding()
    }
    
    // MARK: - 增強型控制面板
    private var enhancedControlPanel: some View {
        VStack(spacing: 15) {
            // 當前標註統計
            if let annotation = annotationManager.currentAnnotation {
                HStack {
                    Label("\(annotation.annotations.count) 個標註", systemImage: "tag.fill")
                        .font(.headline)
                    
                    Spacer()
                    
                    if let selectedID = selectedAnnotationID {
                        Label("已選中", systemImage: "checkmark.circle.fill")
                            .foregroundColor(.blue)
                    }
                }
            }
            
            // 編輯工具按鈕組
            HStack(spacing: 12) {
                // 編輯按鈕
                Button("編輯") {
                    showingEditPanel = true
                }
                .buttonStyle(SecondaryButtonStyle())
                .disabled(selectedAnnotationID == nil)
                
                // 刪除按鈕
                Button("刪除") {
                    showingConfirmDelete = true
                }
                .buttonStyle(DangerButtonStyle())
                .disabled(selectedAnnotationID == nil)
                
                // 清除全部按鈕
                Button("清除全部") {
                    clearAllAnnotations()
                }
                .buttonStyle(WarningButtonStyle())
                .disabled(annotationManager.currentAnnotation?.annotations.isEmpty ?? true)
                
                // 保存按鈕
                Button("保存") {
                    saveAnnotation()
                }
                .buttonStyle(PrimaryButtonStyle())
                .disabled(annotationManager.currentAnnotation?.annotations.isEmpty ?? true)
            }
        }
        .padding()
        .background(Color.gray.opacity(0.05))
    }
    
    // MARK: - 工具列項目
    private var toolbarItems: some ToolbarContent {
        Group {
            ToolbarItem(placement: .navigationBarTrailing) {
                Menu {
                    Button("匯出COCO格式", action: exportCOCO)
                    Button("匯出JSON格式", action: exportJSON)
                    Button("分享標註結果", action: shareAnnotation)
                } label: {
                    Image(systemName: "square.and.arrow.up")
                }
            }
        }
    }
    
    // MARK: - 私有方法
    
    private func initializeAnnotationForImage(_ image: UIImage) {
        let _ = annotationManager.createAnnotation(image: image)
        currentROI = .zero
        selectedAnnotationID = nil
        showingROISelector = false
    }
    
    private func runAIAnnotation() {
        guard let image = selectedImage else { return }
        
        isProcessingAI = true
        aiProgress = 0.0
        
        Task {
            await aiAssistant.performAutoAnnotation(
                image: image,
                annotationManager: annotationManager,
                progressCallback: { progress in
                    DispatchQueue.main.async {
                        aiProgress = progress
                    }
                }
            )
            
            DispatchQueue.main.async {
                isProcessingAI = false
                aiProgress = 0.0
            }
        }
    }
    
    private func deleteSelectedAnnotation() {
        guard let selectedID = selectedAnnotationID,
              var annotation = annotationManager.currentAnnotation else { return }
        
        annotation.annotations.removeAll { $0.id == selectedID }
        annotationManager.currentAnnotation = annotation
        selectedAnnotationID = nil
    }
    
    private func clearAllAnnotations() {
        guard var annotation = annotationManager.currentAnnotation else { return }
        annotation.annotations.removeAll()
        annotationManager.currentAnnotation = annotation
        selectedAnnotationID = nil
    }
    
    private func saveAnnotation() {
        annotationManager.saveAnnotation()
        selectedImage = nil
        selectedAnnotationID = nil
    }
    
    private func exportCOCO() {
        // 實現COCO格式匯出
    }
    
    private func exportJSON() {
        // 實現JSON格式匯出
    }
    
    private func shareAnnotation() {
        // 實現分享功能
    }
}

// MARK: - 標註模式
enum AnnotationMode: String, CaseIterable {
    case manual = "手動標註"
    case autoAI = "AI自動"
    case hybrid = "混合模式"
    
    var displayName: String { rawValue }
    
    var icon: String {
        switch self {
        case .manual: return "hand.draw"
        case .autoAI: return "brain"
        case .hybrid: return "wand.and.stars"
        }
    }
}

// MARK: - 增強型標註畫布
struct EnhancedAnnotationCanvas: UIViewRepresentable {
    let image: UIImage
    let annotationType: AnnotationType
    let annotationMode: AnnotationMode
    @Binding var currentROI: CGRect
    @Binding var selectedAnnotationID: UUID?
    @Binding var showingROISelector: Bool
    @ObservedObject var annotationManager: WoundAnnotationManager
    @ObservedObject var aiAssistant: AIAnnotationAssistant
    
    func makeUIView(context: Context) -> EnhancedAnnotationCanvasView {
        let canvas = EnhancedAnnotationCanvasView()
        canvas.delegate = context.coordinator
        canvas.image = image
        canvas.annotationType = annotationType
        canvas.annotationMode = annotationMode
        canvas.annotationManager = annotationManager
        canvas.aiAssistant = aiAssistant
        return canvas
    }
    
    func updateUIView(_ uiView: EnhancedAnnotationCanvasView, context: Context) {
        uiView.annotationType = annotationType
        uiView.annotationMode = annotationMode
        uiView.showingROISelector = showingROISelector
        uiView.setNeedsDisplay()
    }
    
    func makeCoordinator() -> Coordinator {
        Coordinator(self)
    }
    
    class Coordinator: NSObject, EnhancedAnnotationCanvasDelegate {
        let parent: EnhancedAnnotationCanvas
        
        init(_ parent: EnhancedAnnotationCanvas) {
            self.parent = parent
        }
        
        func didSelectAnnotation(_ annotationID: UUID) {
            parent.selectedAnnotationID = annotationID
        }
        
        func didUpdateROI(_ roi: CGRect) {
            parent.currentROI = roi
        }
    }
}

// MARK: - 增強型標註畫布視圖
protocol EnhancedAnnotationCanvasDelegate: AnyObject {
    func didSelectAnnotation(_ annotationID: UUID)
    func didUpdateROI(_ roi: CGRect)
}

class EnhancedAnnotationCanvasView: UIView {
    weak var delegate: EnhancedAnnotationCanvasDelegate?
    
    var image: UIImage? {
        didSet {
            updateImageDisplay()
        }
    }
    
    var annotationType: AnnotationType = .woundBoundary
    var annotationMode: AnnotationMode = .manual
    var showingROISelector = false {
        didSet {
            setNeedsDisplay()
        }
    }
    
    weak var annotationManager: WoundAnnotationManager?
    weak var aiAssistant: AIAnnotationAssistant?
    
    // 繪製狀態
    private var currentPath: UIBezierPath?
    private var isDrawingROI = false
    private var roiStartPoint: CGPoint = .zero
    private var roiEndPoint: CGPoint = .zero
    
    // 圖像顯示
    private var displayRect: CGRect = .zero
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
        contentMode = .scaleAspectFit
        
        let panGesture = UIPanGestureRecognizer(target: self, action: #selector(handlePan(_:)))
        let tapGesture = UITapGestureRecognizer(target: self, action: #selector(handleTap(_:)))
        
        addGestureRecognizer(panGesture)
        addGestureRecognizer(tapGesture)
    }
    
    private func updateImageDisplay() {
        guard let image = image else { return }
        
        // 計算圖像在視圖中的顯示矩形，保持比例
        let imageAspectRatio = image.size.width / image.size.height
        let viewAspectRatio = bounds.width / bounds.height
        
        if imageAspectRatio > viewAspectRatio {
            // 圖像較寬，以寬度為準
            let displayHeight = bounds.width / imageAspectRatio
            displayRect = CGRect(
                x: 0,
                y: (bounds.height - displayHeight) / 2,
                width: bounds.width,
                height: displayHeight
            )
        } else {
            // 圖像較高，以高度為準
            let displayWidth = bounds.height * imageAspectRatio
            displayRect = CGRect(
                x: (bounds.width - displayWidth) / 2,
                y: 0,
                width: displayWidth,
                height: bounds.height
            )
        }
        
        imageScale = displayRect.width / image.size.width
        setNeedsDisplay()
    }
    
    override func layoutSubviews() {
        super.layoutSubviews()
        updateImageDisplay()
    }
    
    @objc private func handlePan(_ gesture: UIPanGestureRecognizer) {
        let point = gesture.location(in: self)
        
        // 確保點擊在圖像區域內
        guard displayRect.contains(point) else { return }
        
        if showingROISelector {
            handleROIPan(gesture, at: point)
        } else {
            handleAnnotationPan(gesture, at: point)
        }
    }
    
    @objc private func handleTap(_ gesture: UITapGestureRecognizer) {
        let point = gesture.location(in: self)
        
        // 檢查是否點擊了現有標註
        if let tappedAnnotationID = findAnnotationAt(point) {
            delegate?.didSelectAnnotation(tappedAnnotationID)
        }
    }
    
    private func handleROIPan(_ gesture: UIPanGestureRecognizer, at point: CGPoint) {
        switch gesture.state {
        case .began:
            isDrawingROI = true
            roiStartPoint = point
            roiEndPoint = point
            
        case .changed:
            roiEndPoint = point
            setNeedsDisplay()
            
        case .ended:
            isDrawingROI = false
            
            // 轉換為圖像座標系
            let imagePoint1 = convertViewPointToImage(roiStartPoint)
            let imagePoint2 = convertViewPointToImage(roiEndPoint)
            
            let roi = CGRect(
                x: min(imagePoint1.x, imagePoint2.x) / image!.size.width,
                y: min(imagePoint1.y, imagePoint2.y) / image!.size.height,
                width: abs(imagePoint2.x - imagePoint1.x) / image!.size.width,
                height: abs(imagePoint2.y - imagePoint1.y) / image!.size.height
            )
            
            delegate?.didUpdateROI(roi)
            createROIAnnotation(roi)
            setNeedsDisplay()
            
        default:
            break
        }
    }
    
    private func handleAnnotationPan(_ gesture: UIPanGestureRecognizer, at point: CGPoint) {
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
    
    private func convertViewPointToImage(_ viewPoint: CGPoint) -> CGPoint {
        let relativeX = (viewPoint.x - displayRect.minX) / displayRect.width
        let relativeY = (viewPoint.y - displayRect.minY) / displayRect.height
        
        return CGPoint(
            x: relativeX * image!.size.width,
            y: relativeY * image!.size.height
        )
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
        
        // 轉換座標到視圖系統
        let firstPoint = convertImagePointToView(region.coordinates[0])
        path.move(to: firstPoint)
        
        for coordinate in region.coordinates.dropFirst() {
            let viewPoint = convertImagePointToView(coordinate)
            path.addLine(to: viewPoint)
        }
        
        path.close()
        return path
    }
    
    private func convertImagePointToView(_ imagePoint: CGPoint) -> CGPoint {
        let relativeX = imagePoint.x / image!.size.width
        let relativeY = imagePoint.y / image!.size.height
        
        return CGPoint(
            x: displayRect.minX + relativeX * displayRect.width,
            y: displayRect.minY + relativeY * displayRect.height
        )
    }
    
    private func createROIAnnotation(_ roi: CGRect) {
        guard let image = image,
              let annotationManager = annotationManager else { return }
        
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
    
    private func createAnnotationFromPath(_ path: UIBezierPath) {
        guard let image = image,
              let annotationManager = annotationManager else { return }
        
        let coordinates = path.cgPath.points().map { convertViewPointToImage($0) }
        let boundingBox = path.bounds
        let imageBoundingBox = CGRect(
            x: convertViewPointToImage(boundingBox.origin).x,
            y: convertViewPointToImage(boundingBox.origin).y,
            width: boundingBox.width / imageScale,
            height: boundingBox.height / imageScale
        )
        
        let region = AnnotationRegion(
            type: .polygon,
            coordinates: coordinates,
            boundingBox: imageBoundingBox,
            area: calculatePolygonArea(coordinates)
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
        
        // 清除背景
        context.clear(rect)
        
        // 繪製圖像
        if let image = image {
            image.draw(in: displayRect)
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
        
        // 繪製ROI選擇
        if isDrawingROI {
            drawROISelection(context)
        }
    }
    
    private func drawExistingAnnotations(_ context: CGContext) {
        guard let annotation = annotationManager?.currentAnnotation else { return }
        
        for item in annotation.annotations {
            let path = createUIBezierPath(from: item.region)
            
            // 設置顏色
            let isSelected = item.id == delegate?.didSelectAnnotation
            let strokeColor = isSelected ? UIColor.yellow : item.type.color
            let fillColor = strokeColor.withAlphaComponent(0.3)
            
            // 填充
            context.setFillColor(fillColor.cgColor)
            context.addPath(path.cgPath)
            context.fillPath()
            
            // 描邊
            context.setStrokeColor(strokeColor.cgColor)
            context.setLineWidth(isSelected ? 4 : 2)
            context.addPath(path.cgPath)
            context.strokePath()
            
            // 繪製標籤
            drawAnnotationLabel(context, for: item, at: path.bounds.origin)
        }
    }
    
    private func drawROISelection(_ context: CGContext) {
        let roiRect = CGRect(
            x: min(roiStartPoint.x, roiEndPoint.x),
            y: min(roiStartPoint.y, roiEndPoint.y),
            width: abs(roiEndPoint.x - roiStartPoint.x),
            height: abs(roiEndPoint.y - roiStartPoint.y)
        )
        
        context.setStrokeColor(UIColor.orange.cgColor)
        context.setFillColor(UIColor.orange.withAlphaComponent(0.2).cgColor)
        context.setLineWidth(3)
        context.addRect(roiRect)
        context.drawPath(using: .fillStroke)
        
        // ROI標籤
        let label = "ROI"
        let attributes: [NSAttributedString.Key: Any] = [
            .foregroundColor: UIColor.white,
            .font: UIFont.boldSystemFont(ofSize: 14),
            .backgroundColor: UIColor.orange
        ]
        label.draw(at: CGPoint(x: roiRect.minX + 5, y: roiRect.minY + 5), withAttributes: attributes)
    }
    
    private func drawAnnotationLabel(_ context: CGContext, for item: AnnotationItem, at point: CGPoint) {
        let label = item.type.rawValue
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
    }
}

// MARK: - AI標註助手
class AIAnnotationAssistant: ObservableObject {
    @Published var isProcessing = false
    
    func performAutoAnnotation(
        image: UIImage,
        annotationManager: WoundAnnotationManager,
        progressCallback: @escaping (Double) -> Void
    ) async {
        await MainActor.run {
            isProcessing = true
        }
        
        // 模擬AI處理過程
        for progress in stride(from: 0.0, through: 1.0, by: 0.1) {
            progressCallback(progress)
            try? await Task.sleep(nanoseconds: 200_000_000) // 0.2秒
        }
        
        // 創建模擬的AI標註結果
        await createMockAIAnnotations(image: image, annotationManager: annotationManager)
        
        await MainActor.run {
            isProcessing = false
        }
    }
    
    private func createMockAIAnnotations(image: UIImage, annotationManager: WoundAnnotationManager) async {
        let imageSize = image.size
        
        // 模擬檢測到的不同組織類型
        let mockAnnotations = [
            (type: AnnotationType.woundBoundary, confidence: 0.95, area: CGRect(x: imageSize.width * 0.2, y: imageSize.height * 0.3, width: imageSize.width * 0.6, height: imageSize.height * 0.4)),
            (type: AnnotationType.granulation, confidence: 0.87, area: CGRect(x: imageSize.width * 0.3, y: imageSize.height * 0.4, width: imageSize.width * 0.4, height: imageSize.height * 0.2)),
            (type: AnnotationType.necrosis, confidence: 0.78, area: CGRect(x: imageSize.width * 0.45, y: imageSize.height * 0.35, width: imageSize.width * 0.1, height: imageSize.height * 0.1))
        ]
        
        await MainActor.run {
            for mock in mockAnnotations {
                let coordinates = [
                    CGPoint(x: mock.area.minX, y: mock.area.minY),
                    CGPoint(x: mock.area.maxX, y: mock.area.minY),
                    CGPoint(x: mock.area.maxX, y: mock.area.maxY),
                    CGPoint(x: mock.area.minX, y: mock.area.maxY)
                ]
                
                let region = AnnotationRegion(
                    type: .rectangle,
                    coordinates: coordinates,
                    boundingBox: mock.area,
                    area: Double(mock.area.width * mock.area.height)
                )
                
                let annotationItem = AnnotationItem(
                    id: UUID(),
                    type: mock.type,
                    region: region,
                    attributes: ["method": "ai", "confidence": mock.confidence],
                    confidence: mock.confidence
                )
                
                annotationManager.addAnnotationItem(annotationItem)
            }
        }
    }
}

// MARK: - 標註編輯面板
struct AnnotationEditPanel: View {
    let annotation: AnnotationItem
    @ObservedObject var annotationManager: WoundAnnotationManager
    @Environment(\.presentationMode) var presentationMode
    
    @State private var editedType: AnnotationType
    @State private var editedConfidence: Double
    @State private var editedAttributes: [String: String] = [:]
    
    init(annotation: AnnotationItem, annotationManager: WoundAnnotationManager) {
        self.annotation = annotation
        self.annotationManager = annotationManager
        self._editedType = State(initialValue: annotation.type)
        self._editedConfidence = State(initialValue: annotation.confidence)
        
        // 轉換attributes
        let stringAttrs = annotation.attributes.compactMapValues { value in
            if let stringValue = value as? String {
                return stringValue
            } else if let boolValue = value as? Bool {
                return boolValue ? "true" : "false"
            } else if let numberValue = value as? NSNumber {
                return numberValue.stringValue
            }
            return nil
        }
        self._editedAttributes = State(initialValue: stringAttrs)
    }
    
    var body: some View {
        NavigationView {
            Form {
                Section("標註信息") {
                    HStack {
                        Text("ID")
                        Spacer()
                        Text(annotation.id.uuidString.prefix(8))
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                    
                    HStack {
                        Text("面積")
                        Spacer()
                        Text("\(String(format: "%.1f", annotation.region.area)) px²")
                    }
                }
                
                Section("編輯標註") {
                    Picker("組織類型", selection: $editedType) {
                        ForEach(AnnotationType.allCases, id: \.self) { type in
                            Text(type.rawValue).tag(type)
                        }
                    }
                    
                    VStack(alignment: .leading) {
                        Text("信心度: \(String(format: "%.2f", editedConfidence))")
                        Slider(value: $editedConfidence, in: 0...1)
                    }
                }
                
                Section("屬性") {
                    ForEach(Array(editedAttributes.keys), id: \.self) { key in
                        HStack {
                            Text(key)
                            Spacer()
                            TextField("值", text: Binding(
                                get: { editedAttributes[key] ?? "" },
                                set: { editedAttributes[key] = $0 }
                            ))
                            .textFieldStyle(RoundedBorderTextFieldStyle())
                        }
                    }
                }
            }
            .navigationTitle("編輯標註")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    Button("取消") {
                        presentationMode.wrappedValue.dismiss()
                    }
                }
                
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("保存") {
                        saveChanges()
                    }
                }
            }
        }
    }
    
    private func saveChanges() {
        // 更新標註項目
        guard var currentAnnotation = annotationManager.currentAnnotation else { return }
        
        if let index = currentAnnotation.annotations.firstIndex(where: { $0.id == annotation.id }) {
            let convertedAttributes: [String: Any] = editedAttributes.reduce(into: [:]) { result, pair in
                if let doubleValue = Double(pair.value) {
                    result[pair.key] = doubleValue
                } else if pair.value.lowercased() == "true" || pair.value.lowercased() == "false" {
                    result[pair.key] = pair.value.lowercased() == "true"
                } else {
                    result[pair.key] = pair.value
                }
            }
            
            let updatedItem = AnnotationItem(
                id: annotation.id,
                type: editedType,
                region: annotation.region,
                attributes: convertedAttributes,
                confidence: editedConfidence
            )
            
            currentAnnotation.annotations[index] = updatedItem
            annotationManager.currentAnnotation = currentAnnotation
        }
        
        presentationMode.wrappedValue.dismiss()
    }
}

// MARK: - 標註類型按鈕
struct AnnotationTypeChip: View {
    let type: AnnotationType
    let isSelected: Bool
    let action: () -> Void
    
    var body: some View {
        Button(action: action) {
            HStack(spacing: 6) {
                Circle()
                    .fill(Color(type.color))
                    .frame(width: 12, height: 12)
                
                Text(type.rawValue)
                    .font(.caption)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 6)
            .background(
                RoundedRectangle(cornerRadius: 20)
                    .fill(isSelected ? Color.blue.opacity(0.2) : Color.gray.opacity(0.1))
                    .overlay(
                        RoundedRectangle(cornerRadius: 20)
                            .stroke(isSelected ? Color.blue : Color.clear, lineWidth: 2)
                    )
            )
        }
        .foregroundColor(isSelected ? .blue : .primary)
    }
}

// MARK: - 按鈕樣式
struct AccentButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.headline)
            .foregroundColor(.white)
            .padding(.horizontal, 16)
            .padding(.vertical, 8)
            .background(.purple)
            .cornerRadius(8)
            .scaleEffect(configuration.isPressed ? 0.95 : 1.0)
    }
}

struct WarningButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.headline)
            .foregroundColor(.white)
            .padding(.horizontal, 16)
            .padding(.vertical, 8)
            .background(.orange)
            .cornerRadius(8)
            .scaleEffect(configuration.isPressed ? 0.95 : 1.0)
    }
}

#Preview {
    EnhancedAnnotationView()
}