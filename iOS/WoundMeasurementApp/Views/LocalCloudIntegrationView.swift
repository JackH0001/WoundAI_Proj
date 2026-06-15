import SwiftUI
import PhotosUI

/// 本地端與雲端整合驗證介面
struct LocalCloudIntegrationView: View {
    
    @StateObject private var integrationController = LocalCloudIntegrationController()
    @State private var selectedImages: [UIImage] = []
    @State private var showingImagePicker = false
    @State private var isRunningIntegration = false
    @State private var showingResults = false
    
    var body: some View {
        NavigationView {
            ScrollView {
                VStack(spacing: 20) {
                    
                    // 標題與狀態
                    headerSection
                    
                    // 整合狀態顯示
                    integrationStatusSection
                    
                    // 測試圖像選擇
                    imageSelectionSection
                    
                    // 執行整合驗證按鈕
                    integrationControlSection
                    
                    // 結果顯示
                    if integrationController.integrationResults.count > 0 {
                        resultsSection
                    }
                    
                    // 醫療級狀態
                    medicalGradeStatusSection
                    
                    // 系統建議
                    recommendationsSection
                }
                .padding()
            }
            .navigationTitle("本地端雲端整合驗證")
            .sheet(isPresented: $showingImagePicker) {
                ImagePickerView(selectedImages: $selectedImages)
            }
            .sheet(isPresented: $showingResults) {
                IntegrationResultsDetailView(controller: integrationController)
            }
        }
    }
    
    // MARK: - View Sections
    
    private var headerSection: some View {
        VStack(spacing: 10) {
            Text("iOS App 圖像計算驗證系統")
                .font(.title2)
                .fontWeight(.bold)
            
            Text("模擬行動端處理，對比雲端模型結果，優化算法達到醫療級精度")
                .font(.subheadline)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
        }
        .padding()
        .background(Color.blue.opacity(0.1))
        .cornerRadius(12)
    }
    
    private var integrationStatusSection: some View {
        VStack(alignment: .leading, spacing: 15) {
            Text("整合狀態")
                .font(.headline)
            
            HStack {
                VStack(alignment: .leading) {
                    Text("當前狀態")
                        .font(.caption)
                        .foregroundColor(.secondary)
                    Text(integrationStateText)
                        .fontWeight(.medium)
                        .foregroundColor(integrationStateColor)
                }
                
                Spacer()
                
                if integrationController.integrationState != .idle {
                    VStack(alignment: .trailing) {
                        Text("進度")
                            .font(.caption)
                            .foregroundColor(.secondary)
                        Text("\(Int(integrationController.overallProgress * 100))%")
                            .fontWeight(.medium)
                    }
                }
            }
            
            if integrationController.overallProgress > 0 {
                ProgressView(value: integrationController.overallProgress)
                    .progressViewStyle(LinearProgressViewStyle(tint: .blue))
            }
            
            // 關鍵指標
            LazyVGrid(columns: Array(repeating: GridItem(.flexible()), count: 3), spacing: 10) {
                MetricCard(
                    title: "系統準確度",
                    value: "\(String(format: "%.1f%%", integrationController.systemAccuracy * 100))",
                    color: accuracyColor
                )
                
                MetricCard(
                    title: "優化程度",
                    value: "\(String(format: "%.1f%%", integrationController.optimizationLevel * 100))",
                    color: .orange
                )
                
                MetricCard(
                    title: "醫療級評分",
                    value: medicalGradeText,
                    color: medicalGradeColor
                )
            }
        }
        .padding()
        .background(Color.gray.opacity(0.05))
        .cornerRadius(12)
    }
    
    private var imageSelectionSection: some View {
        VStack(alignment: .leading, spacing: 15) {
            Text("測試圖像")
                .font(.headline)
            
            Button(action: {
                showingImagePicker = true
            }) {
                HStack {
                    Image(systemName: "photo.on.rectangle.angled")
                    Text("選擇測試圖像 (\(selectedImages.count))")
                }
                .foregroundColor(.blue)
                .padding()
                .frame(maxWidth: .infinity)
                .background(Color.blue.opacity(0.1))
                .cornerRadius(8)
            }
            
            if !selectedImages.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 10) {
                        ForEach(Array(selectedImages.enumerated()), id: \.offset) { index, image in
                            Image(uiImage: image)
                                .resizable()
                                .aspectRatio(contentMode: .fill)
                                .frame(width: 80, height: 80)
                                .clipShape(RoundedRectangle(cornerRadius: 8))
                                .overlay(
                                    Button(action: {
                                        selectedImages.remove(at: index)
                                    }) {
                                        Image(systemName: "xmark.circle.fill")
                                            .foregroundColor(.red)
                                            .background(Color.white)
                                            .clipShape(Circle())
                                    }
                                    .offset(x: 8, y: -8),
                                    alignment: .topTrailing
                                )
                        }
                    }
                    .padding(.horizontal)
                }
            }
        }
        .padding()
        .background(Color.gray.opacity(0.05))
        .cornerRadius(12)
    }
    
    private var integrationControlSection: some View {
        VStack(spacing: 15) {
            Button(action: {
                runIntegrationTest()
            }) {
                HStack {
                    if isRunningIntegration {
                        ProgressView()
                            .scaleEffect(0.8)
                    } else {
                        Image(systemName: "play.circle.fill")
                    }
                    Text(isRunningIntegration ? "執行中..." : "開始整合驗證")
                }
                .foregroundColor(.white)
                .padding()
                .frame(maxWidth: .infinity)
                .background(canRunIntegration ? Color.blue : Color.gray)
                .cornerRadius(12)
            }
            .disabled(!canRunIntegration || isRunningIntegration)
            
            if integrationController.integrationResults.count > 0 {
                Button(action: {
                    showingResults = true
                }) {
                    HStack {
                        Image(systemName: "chart.bar.doc.horizontal")
                        Text("查看詳細結果")
                    }
                    .foregroundColor(.blue)
                    .padding()
                    .frame(maxWidth: .infinity)
                    .background(Color.blue.opacity(0.1))
                    .cornerRadius(12)
                }
            }
        }
    }
    
    private var resultsSection: some View {
        VStack(alignment: .leading, spacing: 15) {
            Text("驗證結果歷史")
                .font(.headline)
            
            ForEach(Array(integrationController.integrationResults.suffix(3).enumerated()), id: \.offset) { index, result in
                HStack {
                    VStack(alignment: .leading) {
                        Text("測試 #\(integrationController.integrationResults.count - 2 + index)")
                            .fontWeight(.medium)
                        Text("\(result.testCount) 張圖像")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                    
                    Spacer()
                    
                    VStack(alignment: .trailing) {
                        Text("\(String(format: "%.1f%%", result.systemAccuracy * 100))")
                            .fontWeight(.semibold)
                            .foregroundColor(result.systemAccuracy > 0.9 ? .green : result.systemAccuracy > 0.8 ? .orange : .red)
                        Text(result.medicalGradeLevel.displayName)
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                }
                .padding()
                .background(Color.white)
                .cornerRadius(8)
                .shadow(radius: 1)
            }
        }
        .padding()
        .background(Color.gray.opacity(0.05))
        .cornerRadius(12)
    }
    
    private var medicalGradeStatusSection: some View {
        VStack(alignment: .leading, spacing: 15) {
            Text("醫療級認證狀態")
                .font(.headline)
            
            VStack(spacing: 10) {
                HStack {
                    Text("醫療級別")
                    Spacer()
                    Text(integrationController.medicalGradeStatus.level.displayName)
                        .fontWeight(.medium)
                        .foregroundColor(medicalGradeColor)
                }
                
                HStack {
                    Text("認證等級")
                    Spacer()
                    Text(integrationController.medicalGradeStatus.certificationLevel.displayName)
                        .fontWeight(.medium)
                }
                
                HStack {
                    Text("合規評分")
                    Spacer()
                    Text("\(String(format: "%.1f%%", integrationController.medicalGradeStatus.complianceScore * 100))")
                        .fontWeight(.medium)
                        .foregroundColor(integrationController.medicalGradeStatus.complianceScore > 0.8 ? .green : .orange)
                }
                
                HStack {
                    Text("臨床就緒")
                    Spacer()
                    Image(systemName: integrationController.medicalGradeStatus.isReadyForClinicalUse ? "checkmark.circle.fill" : "xmark.circle.fill")
                        .foregroundColor(integrationController.medicalGradeStatus.isReadyForClinicalUse ? .green : .red)
                }
            }
        }
        .padding()
        .background(Color.gray.opacity(0.05))
        .cornerRadius(12)
    }
    
    private var recommendationsSection: some View {
        if !integrationController.systemRecommendations.isEmpty {
            VStack(alignment: .leading, spacing: 15) {
                Text("系統建議")
                    .font(.headline)
                
                ForEach(Array(integrationController.systemRecommendations.enumerated()), id: \.offset) { index, recommendation in
                    RecommendationCard(recommendation: recommendation)
                }
            }
            .padding()
            .background(Color.gray.opacity(0.05))
            .cornerRadius(12)
        }
    }
    
    // MARK: - Computed Properties
    
    private var canRunIntegration: Bool {
        !selectedImages.isEmpty && !isRunningIntegration
    }
    
    private var integrationStateText: String {
        switch integrationController.integrationState {
        case .idle:
            return "待機中"
        case .initializing:
            return "初始化中"
        case .simulatingMobileProcessing:
            return "模擬行動端處理"
        case .validatingWithCloudData:
            return "與雲端數據驗證"
        case .optimizingMobileAlgorithms:
            return "優化行動端算法"
        case .validatingMedicalGrade:
            return "醫療級驗證"
        case .analyzingResults:
            return "分析結果"
        case .completed:
            return "完成"
        case .failed(_):
            return "失敗"
        }
    }
    
    private var integrationStateColor: Color {
        switch integrationController.integrationState {
        case .idle:
            return .secondary
        case .completed:
            return .green
        case .failed(_):
            return .red
        default:
            return .blue
        }
    }
    
    private var accuracyColor: Color {
        let accuracy = integrationController.systemAccuracy
        if accuracy >= 0.95 {
            return .green
        } else if accuracy >= 0.85 {
            return .orange
        } else {
            return .red
        }
    }
    
    private var medicalGradeText: String {
        switch integrationController.medicalGradeStatus.level {
        case .developmentGrade:
            return "開發級"
        case .researchGrade:
            return "研究級"
        case .medicalGrade:
            return "醫療級"
        case .clinicalGrade:
            return "臨床級"
        }
    }
    
    private var medicalGradeColor: Color {
        switch integrationController.medicalGradeStatus.level {
        case .developmentGrade:
            return .red
        case .researchGrade:
            return .orange
        case .medicalGrade:
            return .blue
        case .clinicalGrade:
            return .green
        }
    }
    
    // MARK: - Actions
    
    private func runIntegrationTest() {
        isRunningIntegration = true
        
        Task {
            do {
                let result = try await integrationController.executeFullIntegration(testImages: selectedImages)
                
                await MainActor.run {
                    isRunningIntegration = false
                    // 可以顯示成功訊息或自動導航到結果頁面
                }
                
            } catch {
                await MainActor.run {
                    isRunningIntegration = false
                    // 顯示錯誤訊息
                    print("Integration failed: \(error)")
                }
            }
        }
    }
}

// MARK: - Supporting Views

struct MetricCard: View {
    let title: String
    let value: String
    let color: Color
    
    var body: some View {
        VStack {
            Text(title)
                .font(.caption)
                .foregroundColor(.secondary)
            Text(value)
                .font(.headline)
                .fontWeight(.bold)
                .foregroundColor(color)
        }
        .padding(8)
        .background(Color.white)
        .cornerRadius(8)
        .shadow(radius: 1)
    }
}

struct RecommendationCard: View {
    let recommendation: SystemRecommendation
    
    var body: some View {
        HStack {
            VStack(alignment: .leading) {
                HStack {
                    priorityIcon
                    Text(recommendation.title)
                        .fontWeight(.medium)
                }
                
                Text(recommendation.description)
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .multilineTextAlignment(.leading)
            }
            
            Spacer()
            
            Text("+\(String(format: "%.1f%%", recommendation.expectedImprovement * 100))")
                .font(.caption)
                .fontWeight(.semibold)
                .foregroundColor(.green)
        }
        .padding()
        .background(Color.white)
        .cornerRadius(8)
        .shadow(radius: 1)
    }
    
    private var priorityIcon: some View {
        let (iconName, color) = priorityIconData
        return Image(systemName: iconName)
            .foregroundColor(color)
    }
    
    private var priorityIconData: (String, Color) {
        switch recommendation.priority {
        case .critical:
            return ("exclamationmark.triangle.fill", .red)
        case .high:
            return ("exclamationmark.circle.fill", .orange)
        case .medium:
            return ("info.circle.fill", .blue)
        case .low:
            return ("info.circle", .gray)
        }
    }
}

// MARK: - Extensions

extension MedicalGradeLevel {
    var displayName: String {
        switch self {
        case .developmentGrade:
            return "開發級"
        case .researchGrade:
            return "研究級"
        case .medicalGrade:
            return "醫療級"
        case .clinicalGrade:
            return "臨床級"
        }
    }
}

extension CertificationLevel {
    var displayName: String {
        switch self {
        case .none:
            return "未認證"
        case .research:
            return "研究認證"
        case .medical:
            return "醫療認證"
        case .clinical:
            return "臨床認證"
        case .fdaApproved:
            return "FDA核准"
        }
    }
}

// MARK: - Image Picker

struct ImagePickerView: UIViewControllerRepresentable {
    @Binding var selectedImages: [UIImage]
    @Environment(\.presentationMode) var presentationMode
    
    func makeUIViewController(context: Context) -> PHPickerViewController {
        var configuration = PHPickerConfiguration()
        configuration.filter = .images
        configuration.selectionLimit = 10 // 最多選擇10張圖片
        
        let picker = PHPickerViewController(configuration: configuration)
        picker.delegate = context.coordinator
        return picker
    }
    
    func updateUIViewController(_ uiViewController: PHPickerViewController, context: Context) {}
    
    func makeCoordinator() -> Coordinator {
        Coordinator(self)
    }
    
    class Coordinator: NSObject, PHPickerViewControllerDelegate {
        let parent: ImagePickerView
        
        init(_ parent: ImagePickerView) {
            self.parent = parent
        }
        
        func picker(_ picker: PHPickerViewController, didFinishPicking results: [PHPickerResult]) {
            parent.presentationMode.wrappedValue.dismiss()
            
            Task {
                var images: [UIImage] = []
                
                for result in results {
                    if result.itemProvider.canLoadObject(ofClass: UIImage.self) {
                        if let image = try? await result.itemProvider.loadObject(ofClass: UIImage.self) as? UIImage {
                            images.append(image)
                        }
                    }
                }
                
                await MainActor.run {
                    parent.selectedImages = images
                }
            }
        }
    }
}

struct IntegrationResultsDetailView: View {
    @ObservedObject var controller: LocalCloudIntegrationController
    @Environment(\.presentationMode) var presentationMode
    
    var body: some View {
        NavigationView {
            ScrollView {
                LazyVStack(spacing: 20) {
                    Text("詳細整合結果")
                        .font(.title2)
                        .fontWeight(.bold)
                        .padding()
                    
                    // 這裡可以添加更詳細的結果顯示
                    // 包括圖表、詳細指標、比較分析等
                    
                    ForEach(Array(controller.integrationResults.enumerated()), id: \.offset) { index, result in
                        VStack(alignment: .leading, spacing: 10) {
                            Text("測試 #\(index + 1)")
                                .font(.headline)
                            
                            Text("測試時間: \(result.timestamp.formatted())")
                                .font(.caption)
                                .foregroundColor(.secondary)
                            
                            Text("圖像數量: \(result.testCount)")
                            Text("系統準確度: \(String(format: "%.2f%%", result.systemAccuracy * 100))")
                            Text("優化程度: \(String(format: "%.2f%%", result.optimizationLevel * 100))")
                            Text("醫療級別: \(result.medicalGradeLevel.displayName)")
                        }
                        .padding()
                        .background(Color.gray.opacity(0.1))
                        .cornerRadius(12)
                    }
                }
                .padding()
            }
            .navigationBarTitleDisplayMode(.inline)
            .navigationBarItems(trailing: Button("關閉") {
                presentationMode.wrappedValue.dismiss()
            })
        }
    }
}

#Preview {
    LocalCloudIntegrationView()
}