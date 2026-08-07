import SwiftUI

struct BatchReportView: View {
    @Environment(\.presentationMode) var presentationMode
    let report: BatchProcessingReport
    @State private var showingShareSheet = false
    
    var body: some View {
        NavigationView {
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    // 報告標題
                    reportHeader
                    
                    // 處理統計
                    processingStatistics
                    
                    // 性能分析
                    performanceAnalysis
                    
                    // 成功率分析
                    successRateAnalysis
                    
                    // 詳細結果摘要
                    if !report.results.isEmpty {
                        resultsSummary
                    }
                    
                    // 錯誤分析
                    if !report.errors.isEmpty {
                        errorAnalysis
                    }
                    
                    // 建議
                    recommendations
                }
                .padding()
            }
            .navigationTitle("批量處理報告")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    Button("關閉") {
                        presentationMode.wrappedValue.dismiss()
                    }
                }
                
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("分享") {
                        showingShareSheet = true
                    }
                }
            }
        }
        .sheet(isPresented: $showingShareSheet) {
            ActivityViewController(activityItems: [generateShareableReport()])
        }
    }
    
    // MARK: - 報告標題
    private var reportHeader: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("批量處理報告")
                .font(.largeTitle)
                .fontWeight(.bold)
            
            Text("生成時間: \(DateFormatter.localizedString(from: report.timestamp, dateStyle: .full, timeStyle: .medium))")
                .font(.subheadline)
                .foregroundColor(.secondary)
            
            Divider()
        }
    }
    
    // MARK: - 處理統計
    private var processingStatistics: some View {
        ReportSection(title: "處理統計", icon: "chart.bar.fill", color: .blue) {
            LazyVGrid(columns: Array(repeating: GridItem(.flexible()), count: 2), spacing: 16) {
                BatchStatCard(title: "總圖像數", value: "\(report.totalImages)", icon: "photo.stack", color: .blue)
                BatchStatCard(title: "成功處理", value: "\(report.successfulProcessing)", icon: "checkmark.circle", color: .green)
                BatchStatCard(title: "處理失敗", value: "\(report.failedProcessing)", icon: "xmark.circle", color: .red)
                BatchStatCard(title: "成功率", value: "\(Int(report.successRate * 100))%", icon: "percent", color: .orange)
            }
        }
    }
    
    // MARK: - 性能分析
    private var performanceAnalysis: some View {
        ReportSection(title: "性能分析", icon: "speedometer", color: .purple) {
            VStack(spacing: 12) {
                PerformanceRow(
                    label: "總處理時間",
                    value: formatDuration(report.processingDuration),
                    icon: "clock"
                )
                
                PerformanceRow(
                    label: "平均處理時間",
                    value: String(format: "%.2f 秒/圖像", report.averageProcessingTime),
                    icon: "timer"
                )
                
                if report.totalImages > 0 {
                    PerformanceRow(
                        label: "處理速度",
                        value: String(format: "%.1f 圖像/分鐘", Double(report.totalImages) / report.processingDuration),
                        icon: "gauge"
                    )
                }
                
                // 性能等級
                performanceGrade
            }
        }
    }
    
    // MARK: - 成功率分析
    private var successRateAnalysis: some View {
        ReportSection(title: "成功率分析", icon: "chart.pie", color: .green) {
            VStack(spacing: 16) {
                // 成功率圓環圖（簡化版）
                HStack {
                    VStack {
                        ZStack {
                            Circle()
                                .stroke(Color.gray.opacity(0.3), lineWidth: 8)
                                .frame(width: 80, height: 80)
                            
                            Circle()
                                .trim(from: 0, to: CGFloat(report.successRate))
                                .stroke(
                                    report.successRate > 0.9 ? Color.green :
                                    report.successRate > 0.7 ? Color.orange : Color.red,
                                    style: StrokeStyle(lineWidth: 8, lineCap: .round)
                                )
                                .rotationEffect(.degrees(-90))
                                .frame(width: 80, height: 80)
                            
                            Text("\(Int(report.successRate * 100))%")
                                .font(.headline)
                                .fontWeight(.bold)
                        }
                        
                        Text("成功率")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                    
                    Spacer()
                    
                    VStack(alignment: .leading, spacing: 8) {
                        RateItem(label: "成功", count: report.successfulProcessing, total: report.totalImages, color: .green)
                        RateItem(label: "失敗", count: report.failedProcessing, total: report.totalImages, color: .red)
                    }
                }
            }
        }
    }
    
    // MARK: - 結果摘要
    private var resultsSummary: some View {
        ReportSection(title: "成功結果摘要", icon: "checkmark.seal", color: .green) {
            VStack(alignment: .leading, spacing: 12) {
                if !report.results.isEmpty {
                    let avgArea = report.results.map { $0.measurementResult.woundArea }.reduce(0, +) / Double(report.results.count)
                    let avgPerimeter = report.results.map { $0.measurementResult.woundPerimeter }.reduce(0, +) / Double(report.results.count)
                    let avgConfidence = report.results.map { $0.measurementResult.confidence }.reduce(0, +) / Double(report.results.count)
                    
                    InfoCard(title: "平均傷口面積", value: String(format: "%.2f cm²", avgArea), color: .blue)
                    InfoCard(title: "平均傷口周長", value: String(format: "%.2f cm", avgPerimeter), color: .cyan)
                    InfoCard(title: "平均測量信心度", value: String(format: "%.1f%%", avgConfidence * 100), color: .indigo)
                    
                    // 校正統計
                    let calibratedCount = report.results.compactMap { $0.calibrationResult }.count
                    if calibratedCount > 0 {
                        InfoCard(title: "校正成功率", value: "\(Int(Double(calibratedCount) / Double(report.results.count) * 100))%", color: .purple)
                    }
                }
            }
        }
    }
    
    // MARK: - 錯誤分析
    private var errorAnalysis: some View {
        ReportSection(title: "錯誤分析", icon: "exclamationmark.triangle", color: .red) {
            VStack(alignment: .leading, spacing: 12) {
                // 錯誤統計
                let errorTypes = Dictionary(grouping: report.errors) { error in
                    String(describing: type(of: error.error))
                }
                
                ForEach(Array(errorTypes.keys.sorted()), id: \.self) { errorType in
                    let count = errorTypes[errorType]?.count ?? 0
                    let percentage = Double(count) / Double(report.errors.count) * 100
                    
                    HStack {
                        Text(simplifyErrorType(errorType))
                            .font(.subheadline)
                        
                        Spacer()
                        
                        Text("\(count) 次 (") + Text(String(format: "%.1f", percentage)) + Text("%)")
                            .font(.subheadline)
                            .foregroundColor(.secondary)
                    }
                    .padding(.vertical, 4)
                }
            }
        }
    }
    
    // MARK: - 建議
    private var recommendations: some View {
        ReportSection(title: "優化建議", icon: "lightbulb", color: .yellow) {
            VStack(alignment: .leading, spacing: 12) {
                ForEach(generateRecommendations(), id: \.self) { recommendation in
                    HStack(alignment: .top, spacing: 8) {
                        Image(systemName: "checkmark.circle.fill")
                            .foregroundColor(.green)
                            .font(.caption)
                            .padding(.top, 2)
                        
                        Text(recommendation)
                            .font(.subheadline)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
    }
    
    // MARK: - 性能等級
    private var performanceGrade: some View {
        HStack {
            Text("性能等級:")
                .font(.subheadline)
                .foregroundColor(.secondary)
            
            Spacer()
            
            Text(getPerformanceGrade())
                .font(.subheadline)
                .fontWeight(.bold)
                .foregroundColor(getPerformanceGradeColor())
                .padding(.horizontal, 12)
                .padding(.vertical, 4)
                .background(getPerformanceGradeColor().opacity(0.2))
                .cornerRadius(8)
        }
    }
    
    // MARK: - 輔助方法
    private func formatDuration(_ duration: TimeInterval) -> String {
        let minutes = Int(duration) / 60
        let seconds = Int(duration) % 60
        return minutes > 0 ? "\(minutes)分\(seconds)秒" : "\(seconds)秒"
    }
    
    private func getPerformanceGrade() -> String {
        if report.averageProcessingTime < 2.0 {
            return "優秀"
        } else if report.averageProcessingTime < 5.0 {
            return "良好"
        } else if report.averageProcessingTime < 10.0 {
            return "一般"
        } else {
            return "需改進"
        }
    }
    
    private func getPerformanceGradeColor() -> Color {
        if report.averageProcessingTime < 2.0 {
            return .green
        } else if report.averageProcessingTime < 5.0 {
            return .blue
        } else if report.averageProcessingTime < 10.0 {
            return .orange
        } else {
            return .red
        }
    }
    
    private func simplifyErrorType(_ errorType: String) -> String {
        if errorType.contains("CalibrationError") {
            return "校正錯誤"
        } else if errorType.contains("ProcessingError") {
            return "處理錯誤"
        } else if errorType.contains("ImageError") {
            return "圖像錯誤"
        } else {
            return "其他錯誤"
        }
    }
    
    private func generateRecommendations() -> [String] {
        var recommendations: [String] = []
        
        if report.successRate < 0.8 {
            recommendations.append("成功率偏低，建議檢查圖像品質和校正設定")
        }
        
        if report.averageProcessingTime > 5.0 {
            recommendations.append("處理時間較長，建議調整圖像尺寸或處理參數")
        }
        
        if report.failedProcessing > 0 {
            recommendations.append("有處理失敗的圖像，建議檢查錯誤原因並調整設定")
        }
        
        if report.successRate > 0.9 && report.averageProcessingTime < 3.0 {
            recommendations.append("處理效果良好，可以考慮增加更多功能")
        }
        
        return recommendations.isEmpty ? ["處理結果正常，無特別建議"] : recommendations
    }
    
    private func generateShareableReport() -> String {
        return """
        傷口測量批量處理報告
        ===================
        
        \(report.summary)
        
        詳細統計:
        - 平均處理時間: \(String(format: "%.2f", report.averageProcessingTime)) 秒/圖像
        - 性能等級: \(getPerformanceGrade())
        
        建議:
        \(generateRecommendations().joined(separator: "\n"))
        
        生成時間: \(DateFormatter.localizedString(from: report.timestamp, dateStyle: .full, timeStyle: .medium))
        """
    }
}

// MARK: - 支援組件

struct ReportSection<Content: View>: View {
    let title: String
    let icon: String
    let color: Color
    @ViewBuilder let content: Content
    
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Image(systemName: icon)
                    .foregroundColor(color)
                    .font(.title2)
                
                Text(title)
                    .font(.headline)
                    .fontWeight(.semibold)
            }
            
            content
        }
        .padding()
        .background(Color.gray.opacity(0.05))
        .cornerRadius(12)
    }
}

struct BatchStatCard: View {
    let title: String
    let value: String
    let icon: String
    let color: Color
    
    var body: some View {
        VStack(spacing: 8) {
            Image(systemName: icon)
                .foregroundColor(color)
                .font(.title2)
            
            Text(value)
                .font(.title2)
                .fontWeight(.bold)
            
            Text(title)
                .font(.caption)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity)
        .padding()
        .background(color.opacity(0.1))
        .cornerRadius(8)
    }
}

struct PerformanceRow: View {
    let label: String
    let value: String
    let icon: String
    
    var body: some View {
        HStack {
            Image(systemName: icon)
                .foregroundColor(.purple)
                .frame(width: 20)
            
            Text(label)
                .font(.subheadline)
            
            Spacer()
            
            Text(value)
                .font(.subheadline)
                .fontWeight(.medium)
        }
    }
}

struct RateItem: View {
    let label: String
    let count: Int
    let total: Int
    let color: Color
    
    var body: some View {
        HStack {
            Circle()
                .fill(color)
                .frame(width: 8, height: 8)
            
            Text(label)
                .font(.subheadline)
            
            Spacer()
            
            Text("\(count)/\(total)")
                .font(.subheadline)
                .fontWeight(.medium)
        }
    }
}

struct InfoCard: View {
    let title: String
    let value: String
    let color: Color
    
    var body: some View {
        HStack {
            Text(title)
                .font(.subheadline)
                .foregroundColor(.secondary)
            
            Spacer()
            
            Text(value)
                .font(.subheadline)
                .fontWeight(.bold)
                .foregroundColor(color)
        }
        .padding()
        .background(color.opacity(0.1))
        .cornerRadius(8)
    }
}

// MARK: - 分享功能
struct ActivityViewController: UIViewControllerRepresentable {
    let activityItems: [Any]
    
    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: activityItems, applicationActivities: nil)
    }
    
    func updateUIViewController(_ uiViewController: UIActivityViewController, context: Context) {}
}

#Preview {
    BatchReportView(
        report: BatchProcessingReport(
            timestamp: Date(),
            totalImages: 10,
            successfulProcessing: 8,
            failedProcessing: 2,
            results: [],
            errors: [],
            processingDuration: 45.6,
            averageProcessingTime: 4.56
        )
    )
}