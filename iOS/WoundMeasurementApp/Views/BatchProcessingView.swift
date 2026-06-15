import SwiftUI
import PhotosUI
import UIKit

struct BatchProcessingView: View {
    @StateObject private var batchService = BatchProcessingService.shared
    @State private var selectedImages: [PhotosPickerItem] = []
    @State private var loadedImages: [BatchImageInput] = []
    @State private var showingConfig = false
    @State private var config = BatchProcessingConfig()
    @State private var showingResults = false
    @State private var showingReport = false
    
    var body: some View {
        NavigationView {
            VStack(spacing: 20) {
                // 圖像選擇區域
                imageSelectionSection
                
                // 處理設定
                configurationSection
                
                // 處理狀態和進度
                if batchService.isProcessing {
                    processingStatusSection
                } else if !batchService.results.isEmpty || !batchService.errors.isEmpty {
                    resultsSection
                }
                
                // 控制按鈕
                controlButtonsSection
                
                // Debug 專用：資料夾批次驗證工具
                if _isDebugAssertConfiguration() {
                    Button {
                        presentFolderPickersAndRun()
                    } label: {
                        Label("批次驗證工具 (Debug)", systemImage: "wrench.and.screwdriver")
                    }
                    .buttonStyle(.borderedProminent)
                }
                
                Spacer()
            }
            .padding()
            .navigationTitle("批量處理")
            .navigationBarTitleDisplayMode(.large)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Menu {
                        Button("設定") {
                            showingConfig = true
                        }
                        Button("重置") {
                            resetAll()
                        }
                    } label: {
                        Image(systemName: "ellipsis.circle")
                    }
                }
            }
        }
        .sheet(isPresented: $showingConfig) {
            BatchConfigurationView(config: $config)
        }
        .sheet(isPresented: $showingResults) {
            BatchResultsView(results: batchService.results, errors: batchService.errors)
        }
        .sheet(isPresented: $showingReport) {
            BatchReportView(report: batchService.exportResults())
        }
    }

    private func presentFolderPickersAndRun() {
        guard let root = UIApplication.shared.connectedScenes.compactMap({ ($0 as? UIWindowScene)?.keyWindow }).first?.rootViewController else { return }
        let svc = BatchValidationService.shared
        svc.pickFolder(from: root) { dataset in
            guard let dataset else { return }
            svc.pickFolder(from: root) { sticker in
                Task { await svc.runValidation(datasetFolder: dataset, stickerFolder: sticker, loadCloudComparisons: true) }
            }
        }
    }
    
    // MARK: - 圖像選擇區域
    private var imageSelectionSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("選擇圖像")
                .font(.headline)
            
            PhotosPicker(
                selection: $selectedImages,
                maxSelectionCount: 50,
                matching: .images
            ) {
                VStack {
                    Image(systemName: "photo.on.rectangle.angled")
                        .font(.system(size: 40))
                        .foregroundColor(.blue)
                    Text("選擇圖像 (\(loadedImages.count))")
                        .font(.body)
                }
                .frame(maxWidth: .infinity)
                .frame(height: 100)
                .background(Color.blue.opacity(0.1))
                .cornerRadius(12)
            }
            
            if !loadedImages.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(Array(loadedImages.enumerated()), id: \.offset) { index, imageInput in
                            VStack {
                                Image(uiImage: imageInput.image)
                                    .resizable()
                                    .aspectRatio(contentMode: .fill)
                                    .frame(width: 60, height: 60)
                                    .clipped()
                                    .cornerRadius(8)
                                
                                Text(imageInput.name)
                                    .font(.caption2)
                                    .lineLimit(1)
                            }
                            .frame(width: 70)
                        }
                    }
                    .padding(.horizontal)
                }
            }
        }
        .onChange(of: selectedImages) { newItems in
            Task {
                await loadSelectedImages(newItems)
            }
        }
    }
    
    // MARK: - 設定區域
    private var configurationSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("處理設定")
                .font(.headline)
            
            LazyVGrid(columns: Array(repeating: GridItem(.flexible()), count: 2), spacing: 12) {
                SettingToggle(
                    title: "校正檢測",
                    subtitle: "自動檢測校正貼紙",
                    isOn: $config.enableCalibration
                )
                
                SettingToggle(
                    title: "傷口分類",
                    subtitle: "AI自動分類傷口類型",
                    isOn: $config.enableClassification
                )
                
                SettingToggle(
                    title: "保存記錄",
                    subtitle: "保存到測量歷史",
                    isOn: $config.saveToDatabase
                )
                
                SettingToggle(
                    title: "錯誤續行",
                    subtitle: "遇到錯誤繼續處理",
                    isOn: $config.continueOnError
                )
            }
        }
    }
    
    // MARK: - 處理狀態區域
    private var processingStatusSection: some View {
        VStack(spacing: 16) {
            // 狀態標題
            HStack {
                Text("處理狀態")
                    .font(.headline)
                Spacer()
                Text(batchService.processingState.description)
                    .font(.subheadline)
                    .foregroundColor(.secondary)
            }
            
            // 進度條
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Text("總進度")
                    Spacer()
                    Text("\(batchService.processedCount)/\(batchService.totalCount)")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                
                ProgressView(value: batchService.currentProgress)
                    .progressViewStyle(LinearProgressViewStyle())
                
                if !batchService.currentImageName.isEmpty {
                    Text("正在處理: \(batchService.currentImageName)")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
            }
            
            // 即時統計
            HStack(spacing: 20) {
                StatItem(title: "成功", value: "\(batchService.results.count)", color: .green)
                StatItem(title: "失敗", value: "\(batchService.errors.count)", color: .red)
                if batchService.totalCount > 0 {
                    StatItem(
                        title: "進度",
                        value: "\(Int(batchService.currentProgress * 100))%",
                        color: .blue
                    )
                }
            }
        }
        .padding()
        .background(Color.gray.opacity(0.1))
        .cornerRadius(12)
    }
    
    // MARK: - 結果區域
    private var resultsSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("處理結果")
                .font(.headline)
            
            HStack(spacing: 20) {
                BatchResultCard(
                    title: "成功處理",
                    count: batchService.results.count,
                    icon: "checkmark.circle.fill",
                    color: .green
                )
                
                BatchResultCard(
                    title: "處理失敗",
                    count: batchService.errors.count,
                    icon: "xmark.circle.fill",
                    color: .red
                )
            }
            
            HStack(spacing: 12) {
                Button("查看結果") {
                    showingResults = true
                }
                .buttonStyle(SecondaryButtonStyle())
                
                Button("生成報告") {
                    showingReport = true
                }
                .buttonStyle(SecondaryButtonStyle())
            }
        }
    }
    
    // MARK: - 控制按鈕區域
    private var controlButtonsSection: some View {
        HStack(spacing: 16) {
            if batchService.isProcessing {
                Button("取消處理") {
                    batchService.cancelProcessing()
                }
                .buttonStyle(DangerButtonStyle())
            } else {
                Button("開始處理") {
                    Task {
                        await batchService.startBatchProcessing(images: loadedImages, config: config)
                    }
                }
                .buttonStyle(PrimaryButtonStyle())
                .disabled(loadedImages.isEmpty)
            }
        }
    }
    
    // MARK: - 輔助方法
    private func loadSelectedImages(_ items: [PhotosPickerItem]) async {
        var newImages: [BatchImageInput] = []
        
        for (index, item) in items.enumerated() {
            if let data = try? await item.loadTransferable(type: Data.self),
               let uiImage = UIImage(data: data) {
                let imageName = item.itemIdentifier ?? "Image_\(index + 1)"
                let imageInput = BatchImageInput(name: imageName, image: uiImage)
                newImages.append(imageInput)
            }
        }
        
        await MainActor.run {
            loadedImages = newImages
        }
    }
    
    private func resetAll() {
        selectedImages.removeAll()
        loadedImages.removeAll()
        batchService.resetState()
        config = BatchProcessingConfig()
    }
}

// MARK: - 支援組件
struct SettingToggle: View {
    let title: String
    let subtitle: String
    @Binding var isOn: Bool
    
    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text(title)
                        .font(.subheadline)
                        .fontWeight(.medium)
                    Text(subtitle)
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                Spacer()
                Toggle("", isOn: $isOn)
                    .labelsHidden()
            }
        }
        .padding()
        .background(Color.gray.opacity(0.05))
        .cornerRadius(8)
    }
}

struct StatItem: View {
    let title: String
    let value: String
    let color: Color
    
    var body: some View {
        VStack(spacing: 4) {
            Text(value)
                .font(.title2)
                .fontWeight(.bold)
                .foregroundColor(color)
            Text(title)
                .font(.caption)
                .foregroundColor(.secondary)
        }
    }
}

struct BatchResultCard: View {
    let title: String
    let count: Int
    let icon: String
    let color: Color
    
    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: icon)
                .foregroundColor(color)
                .font(.title2)
            
            VStack(alignment: .leading, spacing: 2) {
                Text("\(count)")
                    .font(.title2)
                    .fontWeight(.bold)
                Text(title)
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
            
            Spacer()
        }
        .padding()
        .background(color.opacity(0.1))
        .cornerRadius(10)
    }
}

// MARK: - 按鈕樣式
struct PrimaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .foregroundColor(.white)
            .padding()
            .frame(maxWidth: .infinity)
            .background(.blue)
            .cornerRadius(10)
            .scaleEffect(configuration.isPressed ? 0.95 : 1.0)
    }
}

struct SecondaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .foregroundColor(.blue)
            .padding()
            .frame(maxWidth: .infinity)
            .background(Color.blue.opacity(0.1))
            .cornerRadius(10)
            .scaleEffect(configuration.isPressed ? 0.95 : 1.0)
    }
}

struct DangerButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .foregroundColor(.white)
            .padding()
            .frame(maxWidth: .infinity)
            .background(.red)
            .cornerRadius(10)
            .scaleEffect(configuration.isPressed ? 0.95 : 1.0)
    }
}

#Preview {
    BatchProcessingView()
}