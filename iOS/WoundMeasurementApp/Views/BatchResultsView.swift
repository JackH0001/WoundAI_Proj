import SwiftUI

struct BatchResultsView: View {
    @Environment(\.presentationMode) var presentationMode
    let results: [BatchProcessingResult]
    let errors: [BatchProcessingError]
    
    @State private var selectedTab: Tab = .success
    @State private var selectedResult: BatchProcessingResult?
    @State private var showingDetailView = false
    
    enum Tab: String, CaseIterable {
        case success = "成功"
        case errors = "錯誤"
        
        var icon: String {
            switch self {
            case .success: return "checkmark.circle"
            case .errors: return "xmark.circle"
            }
        }
        
        var color: Color {
            switch self {
            case .success: return .green
            case .errors: return .red
            }
        }
    }
    
    var body: some View {
        NavigationView {
            VStack(spacing: 0) {
                // 統計概覽
                statisticsHeader
                
                // 分頁控制
                tabSegmentedControl
                
                // 內容區域
                TabView(selection: $selectedTab) {
                    successResultsView
                        .tag(Tab.success)
                    
                    errorResultsView
                        .tag(Tab.errors)
                }
                .tabViewStyle(PageTabViewStyle(indexDisplayMode: .never))
            }
            .navigationTitle("處理結果")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("完成") {
                        presentationMode.wrappedValue.dismiss()
                    }
                }
            }
        }
        .sheet(isPresented: $showingDetailView) {
            if let result = selectedResult {
                BatchResultDetailView(result: result)
            }
        }
    }
    
    // MARK: - 統計概覽
    private var statisticsHeader: some View {
        StatisticsHeaderView(
            totalCount: results.count + errors.count,
            successCount: results.count,
            errorCount: errors.count
        )
        .padding()
        .background(Color.gray.opacity(0.05))
    }
    
    // MARK: - 分頁控制
    private var tabSegmentedControl: some View {
        Picker("結果類型", selection: $selectedTab) {
            ForEach(Tab.allCases, id: \.self) { tab in
                HStack {
                    Image(systemName: tab.icon)
                    Text(tab.rawValue)
                }
                .tag(tab)
            }
        }
        .pickerStyle(SegmentedPickerStyle())
        .padding(.horizontal)
    }
    
    // MARK: - 成功結果視圖
    private var successResultsView: some View {
        List {
            if results.isEmpty {
                Text("無成功處理的圖像")
                    .foregroundColor(.secondary)
                    .frame(maxWidth: .infinity, alignment: .center)
                    .listRowBackground(Color.clear)
            } else {
                ForEach(Array(results.enumerated()), id: \.offset) { index, result in
                    SuccessResultRow(result: result) {
                        selectedResult = result
                        showingDetailView = true
                    }
                }
            }
        }
        .listStyle(PlainListStyle())
    }
    
    // MARK: - 錯誤結果視圖
    private var errorResultsView: some View {
        List {
            if errors.isEmpty {
                Text("無處理錯誤")
                    .foregroundColor(.secondary)
                    .frame(maxWidth: .infinity, alignment: .center)
                    .listRowBackground(Color.clear)
            } else {
                ForEach(Array(errors.enumerated()), id: \.offset) { index, error in
                    ErrorResultRow(error: error)
                }
            }
        }
        .listStyle(PlainListStyle())
    }
}

// MARK: - 統計卡片
struct StatisticCard: View {
    let title: String
    let value: String
    let icon: String
    let color: Color
    
    var body: some View {
        VStack(spacing: 4) {
            Image(systemName: icon)
                .foregroundColor(color)
                .font(.title2)
            
            Text(value)
                .font(.headline)
                .fontWeight(.bold)
            
            Text(title)
                .font(.caption)
                .foregroundColor(.secondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 8)
    }
}

// MARK: - 成功結果行
struct SuccessResultRow: View {
    let result: BatchProcessingResult
    let onTap: () -> Void
    
    var body: some View {
        HStack(spacing: 12) {
            // 縮圖
            Image(uiImage: result.originalImage)
                .resizable()
                .aspectRatio(contentMode: .fill)
                .frame(width: 50, height: 50)
                .clipped()
                .cornerRadius(8)
            
            // 信息
            VStack(alignment: .leading, spacing: 4) {
                Text(result.imageName)
                    .font(.subheadline)
                    .fontWeight(.medium)
                    .lineLimit(1)
                
                HStack {
                    Label("面積", systemImage: "ruler")
                    Text(String(format: "%.2f cm²", result.measurementResult.woundArea))
                    
                    Spacer()
                    
                    Text(String(format: "%.1fs", result.processingTime))
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                .font(.caption)
                
                // 處理狀態指示器
                HStack(spacing: 8) {
                    if result.calibrationResult != nil {
                        StatusBadge(text: "已校正", color: .green)
                    }
                    
                    if result.classificationResult != nil {
                        StatusBadge(text: "已分類", color: .blue)
                    }
                    
                    if result.savedRecord != nil {
                        StatusBadge(text: "已保存", color: .purple)
                    }
                }
            }
            
            Spacer()
            
            Image(systemName: "chevron.right")
                .foregroundColor(.secondary)
                .font(.caption)
        }
        .padding(.vertical, 4)
        .contentShape(Rectangle())
        .onTapGesture {
            onTap()
        }
    }
}

// MARK: - 錯誤結果行
struct ErrorResultRow: View {
    let error: BatchProcessingError
    
    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundColor(.red)
                .font(.title3)
            
            VStack(alignment: .leading, spacing: 4) {
                Text(error.imageName)
                    .font(.subheadline)
                    .fontWeight(.medium)
                    .lineLimit(1)
                
                Text(error.error.localizedDescription)
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .lineLimit(2)
                
                Text(DateFormatter.localizedString(from: error.timestamp, dateStyle: .none, timeStyle: .short))
                    .font(.caption2)
                    .foregroundColor(.secondary)
            }
            
            Spacer()
        }
        .padding(.vertical, 4)
    }
}

// MARK: - 狀態徽章
struct StatusBadge: View {
    let text: String
    let color: Color
    
    var body: some View {
        Text(text)
            .font(.caption2)
            .fontWeight(.medium)
            .foregroundColor(.white)
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(color)
            .cornerRadius(4)
    }
}

// MARK: - 詳細結果視圖
struct BatchResultDetailView: View {
    @Environment(\.presentationMode) var presentationMode
    let result: BatchProcessingResult
    
    var body: some View {
        NavigationView {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    // 圖像顯示
                    imageSection
                    
                    // 測量結果
                    measurementSection
                    
                    // 校正結果
                    if let calibration = result.calibrationResult {
                        calibrationSection(calibration)
                    }
                    
                    // 分類結果
                    if let classification = result.classificationResult {
                        classificationSection(classification)
                    }
                    
                    // 處理信息
                    processingInfoSection
                }
                .padding()
            }
            .navigationTitle(result.imageName)
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
    
    private var imageSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("原始圖像")
                .font(.headline)
            
            Image(uiImage: result.originalImage)
                .resizable()
                .aspectRatio(contentMode: .fit)
                .frame(maxHeight: 200)
                .cornerRadius(12)
        }
    }
    
    private var measurementSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("測量結果")
                .font(.headline)
            
            VStack(spacing: 8) {
                InfoRow(label: "傷口面積", value: String(format: "%.2f cm²", result.measurementResult.woundArea))
                InfoRow(label: "傷口周長", value: String(format: "%.2f cm", result.measurementResult.woundPerimeter))
                InfoRow(label: "像素比例", value: String(format: "%.2f px/mm", result.measurementResult.pixelsPerMM))
                InfoRow(label: "測量信心度", value: String(format: "%.1f%%", result.measurementResult.confidence * 100))
            }
            .padding()
            .background(Color.gray.opacity(0.1))
            .cornerRadius(8)
        }
    }
    
    private func calibrationSection(_ calibration: StickerCalibrationResult) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("校正結果")
                .font(.headline)
            
            VStack(spacing: 8) {
                InfoRow(label: "檢測信心度", value: String(format: "%.1f%%", calibration.confidence * 100))
                InfoRow(label: "貼紙半徑", value: String(format: "%.1f px", calibration.circle.radius))
                InfoRow(label: "像素比例", value: String(format: "%.2f px/mm", calibration.pixelsPerMM))
            }
            .padding()
            .background(Color.green.opacity(0.1))
            .cornerRadius(8)
        }
    }
    
    private func classificationSection(_ classification: DetailedWoundClassification) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("分類結果")
                .font(.headline)
            
            VStack(spacing: 8) {
                InfoRow(label: "急性分數", value: String(format: "%.2f", classification.acuteScore))
                InfoRow(label: "慢性分數", value: String(format: "%.2f", classification.chronicScore))
                InfoRow(label: "感染風險", value: String(format: "%.2f", classification.infectedScore))
                InfoRow(label: "癒合分數", value: String(format: "%.2f", classification.healingScore))
                InfoRow(label: "信心度", value: String(format: "%.1f%%", classification.confidence * 100))
            }
            .padding()
            .background(Color.blue.opacity(0.1))
            .cornerRadius(8)
        }
    }
    
    private var processingInfoSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("處理信息")
                .font(.headline)
            
            VStack(spacing: 8) {
                InfoRow(label: "處理時間", value: String(format: "%.2f 秒", result.processingTime))
                InfoRow(label: "處理時間", value: DateFormatter.localizedString(from: result.timestamp, dateStyle: .medium, timeStyle: .medium))
                if result.savedRecord != nil {
                    InfoRow(label: "保存狀態", value: "已保存到資料庫")
                }
            }
            .padding()
            .background(Color.gray.opacity(0.1))
            .cornerRadius(8)
        }
    }
}

// MARK: - 信息行
struct InfoRow: View {
    let label: String
    let value: String
    
    var body: some View {
        HStack {
            Text(label)
                .foregroundColor(.secondary)
            Spacer()
            Text(value)
                .fontWeight(.medium)
        }
        .font(.subheadline)
    }
}

#Preview {
    BatchResultsView(
        results: [],
        errors: []
    )
}

// MARK: - 獨立統計標頭，降低型別推斷複雜度
struct StatisticsHeaderView: View {
    let totalCount: Int
    let successCount: Int
    let errorCount: Int
    
    var body: some View {
        VStack(spacing: 12) {
            HStack(spacing: 20) {
                StatisticCard(
                    title: "總數",
                    value: "\(totalCount)",
                    icon: "photo",
                    color: .blue
                )
                
                StatisticCard(
                    title: "成功",
                    value: "\(successCount)",
                    icon: "checkmark.circle.fill",
                    color: .green
                )
            }
            HStack(spacing: 20) {
                StatisticCard(
                    title: "失敗",
                    value: "\(errorCount)",
                    icon: "xmark.circle.fill",
                    color: .red
                )
                
                StatisticCard(
                    title: "成功率",
                    value: successRateString,
                    icon: "chart.pie.fill",
                    color: .orange
                )
            }
        }
    }
    
    private var successRateString: String {
        let denom = totalCount
        guard denom > 0 else { return "—" }
        let rate = Int((Double(successCount) / Double(denom)) * 100)
        return "\(rate)%"
    }
}