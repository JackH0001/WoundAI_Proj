import SwiftUI
import Charts

struct PerformanceDashboardView: View {
    @StateObject private var performanceMonitor = EnhancedPerformanceMonitor.shared
    @State private var showingDetailReport = false
    @State private var selectedTimeRange: TimeRange = .last24Hours
    
    enum TimeRange: String, CaseIterable {
        case last5Minutes = "最近5分鐘"
        case last1Hour = "最近1小時"
        case last24Hours = "最近24小時"
    }
    
    var body: some View {
        NavigationView {
            ScrollView {
                VStack(spacing: 20) {
                    // 監控狀態切換
                    monitoringControlSection
                    
                    // 關鍵指標概覽
                    keyMetricsSection
                    
                    // 記憶體使用圖表
                    memoryUsageSection
                    
                    // 處理時間統計
                    processingTimeSection
                    
                    // 錯誤和警告
                    errorsAndWarningsSection
                    
                    // 建議和優化
                    recommendationsSection
                }
                .padding()
            }
            .navigationTitle("效能監控")
            .navigationBarTitleDisplayMode(.large)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Menu {
                        Button("匯出報告") {
                            showingDetailReport = true
                        }
                        Button("重置數據") {
                            performanceMonitor.resetMetrics()
                        }
                    } label: {
                        Image(systemName: "ellipsis.circle")
                    }
                }
            }
        }
        .onAppear {
            if !performanceMonitor.isMonitoring {
                performanceMonitor.startMonitoring()
            }
        }
        .sheet(isPresented: $showingDetailReport) {
            PerformanceReportView(report: performanceMonitor.generateReport())
        }
    }
    
    // MARK: - 監控控制區域
    private var monitoringControlSection: some View {
        VStack(spacing: 12) {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text("監控狀態")
                        .font(.headline)
                    Text(performanceMonitor.isMonitoring ? "運行中" : "已停止")
                        .font(.subheadline)
                        .foregroundColor(performanceMonitor.isMonitoring ? .green : .red)
                }
                
                Spacer()
                
                Button(action: toggleMonitoring) {
                    HStack {
                        Image(systemName: performanceMonitor.isMonitoring ? "pause.fill" : "play.fill")
                        Text(performanceMonitor.isMonitoring ? "停止" : "開始")
                    }
                    .foregroundColor(.white)
                    .padding(.horizontal, 16)
                    .padding(.vertical, 8)
                    .background(performanceMonitor.isMonitoring ? .red : .blue)
                    .cornerRadius(8)
                }
            }
            
            // 時間範圍選擇
            Picker("時間範圍", selection: $selectedTimeRange) {
                ForEach(TimeRange.allCases, id: \.self) { range in
                    Text(range.rawValue).tag(range)
                }
            }
            .pickerStyle(SegmentedPickerStyle())
        }
        .padding()
        .background(Color.gray.opacity(0.1))
        .cornerRadius(12)
    }
    
    // MARK: - 關鍵指標
    private var keyMetricsSection: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("關鍵指標")
                .font(.headline)
            
            LazyVGrid(columns: Array(repeating: GridItem(.flexible()), count: 2), spacing: 16) {
                MetricCard(
                    title: "當前記憶體",
                    value: String(format: "%.1f MB", performanceMonitor.currentMemoryUsage),
                    icon: "memorychip",
                    color: memoryColor(performanceMonitor.currentMemoryUsage)
                )
                
                MetricCard(
                    title: "峰值記憶體",
                    value: String(format: "%.1f MB", performanceMonitor.peakMemoryUsage),
                    icon: "chart.line.uptrend.xyaxis",
                    color: memoryColor(performanceMonitor.peakMemoryUsage)
                )
                
                MetricCard(
                    title: "平均處理時間",
                    value: String(format: "%.2f 秒", performanceMonitor.averageProcessingTime),
                    icon: "clock",
                    color: timeColor(performanceMonitor.averageProcessingTime)
                )
                
                MetricCard(
                    title: "操作總數",
                    value: "\(performanceMonitor.operationCount)",
                    icon: "number",
                    color: .blue
                )
            }
        }
    }
    
    // MARK: - 記憶體使用圖表
    private var memoryUsageSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("記憶體使用趨勢")
                .font(.headline)
            
            // 簡化的記憶體使用顯示
            HStack {
                VStack(alignment: .leading) {
                    Text("當前: \(String(format: "%.1f", performanceMonitor.currentMemoryUsage)) MB")
                        .font(.caption)
                    Text("峰值: \(String(format: "%.1f", performanceMonitor.peakMemoryUsage)) MB")
                        .font(.caption)
                }
                
                Spacer()
                
                // 記憶體使用進度條
                ProgressView(value: performanceMonitor.currentMemoryUsage, total: 400.0) {
                    Text("記憶體使用")
                        .font(.caption2)
                } currentValueLabel: {
                    Text("\(Int(performanceMonitor.currentMemoryUsage))/400 MB")
                        .font(.caption2)
                }
                .frame(width: 150)
            }
            .padding()
            .background(Color.blue.opacity(0.1))
            .cornerRadius(8)
        }
    }
    
    // MARK: - 處理時間統計
    private var processingTimeSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("處理效能")
                .font(.headline)
            
            HStack(spacing: 20) {
                VStack(alignment: .leading) {
                    Text("最後操作")
                        .font(.caption)
                        .foregroundColor(.secondary)
                    Text("\(String(format: "%.3f", performanceMonitor.lastOperationTime)) 秒")
                        .font(.title3)
                        .fontWeight(.semibold)
                        .foregroundColor(timeColor(performanceMonitor.lastOperationTime))
                }
                
                Divider()
                
                VStack(alignment: .leading) {
                    Text("平均時間")
                        .font(.caption)
                        .foregroundColor(.secondary)
                    Text("\(String(format: "%.3f", performanceMonitor.averageProcessingTime)) 秒")
                        .font(.title3)
                        .fontWeight(.semibold)
                        .foregroundColor(timeColor(performanceMonitor.averageProcessingTime))
                }
                
                Spacer()
            }
            .padding()
            .background(Color.green.opacity(0.1))
            .cornerRadius(8)
        }
    }
    
    // MARK: - 錯誤和警告
    private var errorsAndWarningsSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("系統狀態")
                .font(.headline)
            
            HStack(spacing: 16) {
                StatusIndicator(
                    title: "錯誤",
                    count: performanceMonitor.errorCount,
                    icon: "exclamationmark.triangle",
                    color: .red
                )
                
                StatusIndicator(
                    title: "記憶體警告",
                    count: performanceMonitor.performanceMetrics.memoryWarnings,
                    icon: "memorychip.fill",
                    color: .orange
                )
                
                StatusIndicator(
                    title: "慢操作",
                    count: performanceMonitor.performanceMetrics.slowOperations,
                    icon: "clock.fill",
                    color: .yellow
                )
            }
        }
    }
    
    // MARK: - 建議和優化
    private var recommendationsSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("優化建議")
                .font(.headline)
            
            let recommendations = performanceMonitor.generateReport().recommendations
            
            if recommendations.isEmpty {
                HStack {
                    Image(systemName: "checkmark.circle.fill")
                        .foregroundColor(.green)
                    Text("系統運行良好，無需優化建議")
                        .font(.body)
                }
                .padding()
                .background(Color.green.opacity(0.1))
                .cornerRadius(8)
            } else {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(recommendations, id: \.self) { recommendation in
                        HStack(alignment: .top) {
                            Image(systemName: "lightbulb.fill")
                                .foregroundColor(.orange)
                                .font(.caption)
                            Text(recommendation)
                                .font(.caption)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
                .padding()
                .background(Color.orange.opacity(0.1))
                .cornerRadius(8)
            }
        }
    }
    
    // MARK: - 輔助方法
    private func toggleMonitoring() {
        if performanceMonitor.isMonitoring {
            performanceMonitor.stopMonitoring()
        } else {
            performanceMonitor.startMonitoring()
        }
    }
    
    private func memoryColor(_ memory: Double) -> Color {
        if memory > 300.0 {
            return .red
        } else if memory > 200.0 {
            return .orange
        } else {
            return .green
        }
    }
    
    private func timeColor(_ time: Double) -> Color {
        if time > 5.0 {
            return .red
        } else if time > 2.0 {
            return .orange
        } else {
            return .green
        }
    }
}

// MARK: - 支援視圖組件
struct MetricCard: View {
    let title: String
    let value: String
    let icon: String
    let color: Color
    
    var body: some View {
        VStack(spacing: 8) {
            HStack {
                Image(systemName: icon)
                    .foregroundColor(color)
                    .font(.title2)
                Spacer()
            }
            
            VStack(alignment: .leading, spacing: 4) {
                Text(title)
                    .font(.caption)
                    .foregroundColor(.secondary)
                Text(value)
                    .font(.headline)
                    .fontWeight(.semibold)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding()
        .background(Color.gray.opacity(0.1))
        .cornerRadius(12)
    }
}

struct StatusIndicator: View {
    let title: String
    let count: Int
    let icon: String
    let color: Color
    
    var body: some View {
        VStack(spacing: 4) {
            Image(systemName: icon)
                .foregroundColor(color)
                .font(.title2)
            
            Text("\(count)")
                .font(.headline)
                .fontWeight(.bold)
            
            Text(title)
                .font(.caption2)
                .foregroundColor(.secondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 8)
        .background(color.opacity(0.1))
        .cornerRadius(8)
    }
}

// MARK: - 詳細報告視圖
struct PerformanceReportView: View {
    let report: PerformanceReport
    @Environment(\.presentationMode) var presentationMode
    
    var body: some View {
        NavigationView {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    // 報告標題
                    VStack(alignment: .leading, spacing: 8) {
                        Text("效能分析報告")
                            .font(.largeTitle)
                            .fontWeight(.bold)
                        
                        Text("生成時間: \(DateFormatter.localizedString(from: report.timestamp, dateStyle: .medium, timeStyle: .short))")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                    
                    Divider()
                    
                    // 詳細指標
                    VStack(alignment: .leading, spacing: 12) {
                        Text("詳細指標")
                            .font(.headline)
                        
                        MetricRow(label: "當前記憶體", value: "\(String(format: "%.1f", report.metrics.currentMemory)) MB")
                        MetricRow(label: "峰值記憶體", value: "\(String(format: "%.1f", report.metrics.peakMemory)) MB")
                        MetricRow(label: "平均處理時間", value: "\(String(format: "%.3f", report.metrics.averageProcessingTime)) 秒")
                        MetricRow(label: "總操作數", value: "\(report.metrics.totalOperations)")
                        MetricRow(label: "錯誤數量", value: "\(report.metrics.errorCount)")
                        MetricRow(label: "記憶體警告", value: "\(report.metrics.memoryWarnings)")
                        MetricRow(label: "慢操作", value: "\(report.metrics.slowOperations)")
                    }
                    
                    Divider()
                    
                    // 建議
                    if !report.recommendations.isEmpty {
                        VStack(alignment: .leading, spacing: 12) {
                            Text("優化建議")
                                .font(.headline)
                            
                            ForEach(report.recommendations, id: \.self) { recommendation in
                                Text("• \(recommendation)")
                                    .font(.body)
                            }
                        }
                    }
                }
                .padding()
            }
            .navigationTitle("效能報告")
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
}

struct MetricRow: View {
    let label: String
    let value: String
    
    var body: some View {
        HStack {
            Text(label)
                .font(.body)
            Spacer()
            Text(value)
                .font(.body)
                .fontWeight(.semibold)
        }
        .padding(.vertical, 4)
    }
}

#Preview {
    PerformanceDashboardView()
}