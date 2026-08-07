import SwiftUI
#if canImport(Charts)
import Charts
#endif

struct AnalysisHistoryView: View {
    let result: WoundMeasurementResult
    @State private var historicalData: [HistoricalMeasurement] = []
    @State private var selectedTimeRange: TimeRange = .week
    @State private var showingTrendAnalysis = false
    
    enum TimeRange: String, CaseIterable {
        case week = "一週"
        case month = "一個月"
        case threeMonths = "三個月"
        case sixMonths = "六個月"
        
        var days: Int {
            switch self {
            case .week: return 7
            case .month: return 30
            case .threeMonths: return 90
            case .sixMonths: return 180
            }
        }
    }
    
    var body: some View {
        NavigationView {
            ScrollView {
                VStack(spacing: 20) {
                    // 時間範圍選擇器
                    TimeRangeSelector(selectedRange: $selectedTimeRange)
                    
                    // 當前測量與歷史比較
                    CurrentVsHistoryCard(
                        currentResult: result,
                        historicalData: historicalData
                    )
                    
                    // 趨勢圖表
                    if !historicalData.isEmpty {
                        TrendChartsView(
                            data: historicalData,
                            timeRange: selectedTimeRange
                        )
                    }
                    
                    // 統計摘要
                    StatisticsSummaryView(
                        data: historicalData,
                        currentResult: result
                    )
                    
                    // 癒合進度分析
                    HealingProgressView(
                        data: historicalData,
                        currentResult: result
                    )
                    
                    // 建議和警告
                    RecommendationsView(
                        data: historicalData,
                        currentResult: result
                    )
                }
                .padding()
            }
            .navigationTitle("分析歷史")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("趨勢分析") {
                        showingTrendAnalysis = true
                    }
                }
            }
            .sheet(isPresented: $showingTrendAnalysis) {
                TrendAnalysisDetailView(data: historicalData)
            }
            .onAppear {
                loadHistoricalData()
            }
        }
    }
    
    private func loadHistoricalData() {
        // 模擬歷史數據
        let calendar = Calendar.current
        let now = Date()
        
        historicalData = (0..<selectedTimeRange.days).compactMap { dayOffset in
            guard let date = calendar.date(byAdding: .day, value: -dayOffset, to: now) else {
                return nil
            }
            
            // 模擬癒合趋势：面積逐漸減小
            let healingProgress = Double(dayOffset) / Double(selectedTimeRange.days)
            let baseArea = result.area ?? 10.0
            let area = baseArea * (1.0 - healingProgress * 0.3) + Double.random(in: -1.0...1.0)
            let volume = (result.volume ?? 1.0) * (1.0 - healingProgress * 0.4) + Double.random(in: -0.1...0.1)
            
            return HistoricalMeasurement(
                date: date,
                area: max(0.1, area),
                volume: max(0.01, volume),
                acuteScore: 0.8 - healingProgress * 0.3 + Double.random(in: -0.1...0.1),
                chronicScore: 0.2 + healingProgress * 0.1 + Double.random(in: -0.05...0.05),
                confidence: 0.85 + Double.random(in: -0.1...0.1),
                healingStage: determineHealingStage(progress: healingProgress),
                notes: generateRandomNotes()
            )
        }.reversed()
    }
    
    private func determineHealingStage(progress: Double) -> String {
        switch progress {
        case 0.0..<0.3: return "發炎期"
        case 0.3..<0.7: return "增生期"
        default: return "成熟期"
        }
    }
    
    private func generateRandomNotes() -> String? {
        let notes = [
            "傷口清潔良好",
            "有輕微紅腫",
            "癒合進展順利",
            "需要增加護理頻率",
            "傷口邊緣開始收縮",
            nil, nil, nil // 大部分沒有備註
        ]
        return notes.randomElement() ?? nil
    }
}

struct TimeRangeSelector: View {
    @Binding var selectedRange: AnalysisHistoryView.TimeRange
    
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("時間範圍")
                .font(.headline)
            
            Picker("時間範圍", selection: $selectedRange) {
                ForEach(AnalysisHistoryView.TimeRange.allCases, id: \.self) { range in
                    Text(range.rawValue).tag(range)
                }
            }
            .pickerStyle(SegmentedPickerStyle())
        }
        .padding()
        .background(Color.gray.opacity(0.1))
        .cornerRadius(10)
    }
}

struct CurrentVsHistoryCard: View {
    let currentResult: WoundMeasurementResult
    let historicalData: [HistoricalMeasurement]
    
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("當前 vs 歷史對比")
                .font(.headline)
            
            if let lastMeasurement = historicalData.last,
               let currentArea = currentResult.area {
                
                HStack {
                    VStack(alignment: .leading) {
                        Text("面積變化")
                            .font(.subheadline)
                            .foregroundColor(.secondary)
                        
                        let change = currentArea - lastMeasurement.area
                        let changePercent = (change / lastMeasurement.area) * 100
                        
                        HStack {
                            Text("\(String(format: "%.2f", currentArea)) cm²")
                                .font(.title3)
                                .fontWeight(.semibold)
                            
                            Spacer()
                            
                            HStack {
                                Image(systemName: change < 0 ? "arrow.down" : "arrow.up")
                                    .foregroundColor(change < 0 ? .green : .red)
                                Text("\(String(format: "%.1f", Swift.abs(changePercent)))%")
                                    .foregroundColor(change < 0 ? .green : .red)
                            }
                            .font(.caption)
                        }
                    }
                    
                    Divider()
                        .frame(height: 40)
                    
                    VStack(alignment: .leading) {
                        Text("癒合趨勢")
                            .font(.subheadline)
                            .foregroundColor(.secondary)
                        
                        let trend = calculateHealingTrend()
                        
                        HStack {
                            Text(trend.description)
                                .font(.title3)
                                .fontWeight(.semibold)
                            
                            Spacer()
                            
                            Image(systemName: trend.icon)
                                .foregroundColor(trend.color)
                                .font(.title2)
                        }
                    }
                }
            } else {
                Text("無歷史數據對比")
                    .foregroundColor(.secondary)
            }
        }
        .padding()
        .background(Color.blue.opacity(0.1))
        .cornerRadius(10)
    }
    
    private func calculateHealingTrend() -> (description: String, icon: String, color: Color) {
        guard historicalData.count >= 3 else {
            return ("數據不足", "questionmark.circle", .gray)
        }
        
        let recentData = Array(historicalData.suffix(3))
        let areaChanges = zip(recentData.dropFirst(), recentData).map { current, previous in
            current.area - previous.area
        }
        
        let averageChange = areaChanges.reduce(0, +) / Double(areaChanges.count)
        
        if averageChange < -0.5 {
            return ("快速癒合", "heart.fill", .green)
        } else if averageChange < -0.1 {
            return ("緩慢癒合", "heart", .orange)
        } else if averageChange < 0.1 {
            return ("穩定狀態", "minus.circle", .blue)
        } else {
            return ("需要關注", "exclamationmark.triangle.fill", .red)
        }
    }
}

struct TrendChartsView: View {
    let data: [HistoricalMeasurement]
    let timeRange: AnalysisHistoryView.TimeRange
    @State private var selectedChart: ChartType = .area
    
    enum ChartType: String, CaseIterable {
        case area = "面積"
        case volume = "體積"
        case classification = "分類"
        case confidence = "信心度"
    }
    
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("趨勢圖表")
                    .font(.headline)
                
                Spacer()
                
                Picker("圖表類型", selection: $selectedChart) {
                    ForEach(ChartType.allCases, id: \.self) { type in
                        Text(type.rawValue).tag(type)
                    }
                }
                .pickerStyle(MenuPickerStyle())
            }
            
            Chart(data, id: \.date) { measurement in
                switch selectedChart {
                case .area:
                    LineMark(
                        x: .value("日期", measurement.date),
                        y: .value("面積", measurement.area)
                    )
                    .foregroundStyle(.blue)
                    .symbol(Circle())
                    
                case .volume:
                    LineMark(
                        x: .value("日期", measurement.date),
                        y: .value("體積", measurement.volume)
                    )
                    .foregroundStyle(.green)
                    .symbol(Circle())
                    
                case .classification:
                    LineMark(
                        x: .value("日期", measurement.date),
                        y: .value("急性", measurement.acuteScore)
                    )
                    .foregroundStyle(.red)
                    .symbol(Circle())
                    
                    LineMark(
                        x: .value("日期", measurement.date),
                        y: .value("慢性", measurement.chronicScore)
                    )
                    .foregroundStyle(.orange)
                    .symbol(Circle())
                    
                case .confidence:
                    LineMark(
                        x: .value("日期", measurement.date),
                        y: .value("信心度", measurement.confidence)
                    )
                    .foregroundStyle(.purple)
                    .symbol(Circle())
                }
            }
            .frame(height: 200)
            .chartXAxis {
                AxisMarks(values: .stride(by: .day, count: max(1, timeRange.days / 7))) { _ in
                    AxisGridLine()
                    AxisValueLabel(format: .dateTime.month().day())
                }
            }
            .chartYAxis {
                AxisMarks { _ in
                    AxisGridLine()
                    AxisValueLabel()
                }
            }
        }
        .padding()
        .background(Color.gray.opacity(0.1))
        .cornerRadius(10)
    }
}

struct StatisticsSummaryView: View {
    let data: [HistoricalMeasurement]
    let currentResult: WoundMeasurementResult
    
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("統計摘要")
                .font(.headline)
            
            LazyVGrid(columns: [
                GridItem(.flexible()),
                GridItem(.flexible()),
                GridItem(.flexible())
            ], spacing: 12) {
                if let avgArea = calculateAverageArea() {
                    StatCard(
                        title: "平均面積",
                        value: String(format: "%.2f cm²", avgArea),
                        trend: calculateAreaTrend(),
                        color: .blue
                    )
                }
                
                if let maxArea = data.map(\.area).max() {
                    StatCard(
                        title: "最大面積",
                        value: String(format: "%.2f cm²", maxArea),
                        trend: .stable,
                        color: .red
                    )
                }
                
                if let minArea = data.map(\.area).min() {
                    StatCard(
                        title: "最小面積",
                        value: String(format: "%.2f cm²", minArea),
                        trend: .stable,
                        color: .green
                    )
                }
                
                StatCard(
                    title: "測量次數",
                    value: "\(data.count)",
                    trend: .stable,
                    color: .purple
                )
                
                if let avgConfidence = calculateAverageConfidence() {
                    StatCard(
                        title: "平均信心度",
                        value: String(format: "%.1f%%", avgConfidence * 100),
                        trend: .stable,
                        color: .orange
                    )
                }
                
                StatCard(
                    title: "癒合天數",
                    value: "\(data.count)",
                    trend: .improving,
                    color: .teal
                )
            }
        }
        .padding()
        .background(Color.gray.opacity(0.1))
        .cornerRadius(10)
    }
    
    private func calculateAverageArea() -> Double? {
        guard !data.isEmpty else { return nil }
        return data.map(\.area).reduce(0, +) / Double(data.count)
    }
    
    private func calculateAverageConfidence() -> Double? {
        guard !data.isEmpty else { return nil }
        return data.map(\.confidence).reduce(0, +) / Double(data.count)
    }
    
    private func calculateAreaTrend() -> StatCard.Trend {
        guard data.count >= 2 else { return .stable }
        
        let recent = Array(data.suffix(3))
        let older = Array(data.prefix(3))
        
        let recentAvg = recent.map(\.area).reduce(0, +) / Double(recent.count)
        let olderAvg = older.map(\.area).reduce(0, +) / Double(older.count)
        
        let change = (recentAvg - olderAvg) / olderAvg
        
        if change < -0.1 {
            return .improving
        } else if change > 0.1 {
            return .declining
        } else {
            return .stable
        }
    }
}

struct StatCard: View {
    let title: String
    let value: String
    let trend: Trend
    let color: Color
    
    enum Trend {
        case improving, declining, stable
        
        var icon: String {
            switch self {
            case .improving: return "arrow.down.right"
            case .declining: return "arrow.up.right"
            case .stable: return "minus"
            }
        }
        
        var color: Color {
            switch self {
            case .improving: return .green
            case .declining: return .red
            case .stable: return .gray
            }
        }
    }
    
    var body: some View {
        VStack(spacing: 8) {
            HStack {
                Text(title)
                    .font(.caption)
                    .foregroundColor(.secondary)
                
                Spacer()
                
                Image(systemName: trend.icon)
                    .font(.caption2)
                    .foregroundColor(trend.color)
            }
            
            Text(value)
                .font(.headline)
                .fontWeight(.semibold)
                .foregroundColor(color)
        }
        .padding(12)
        .background(Color.white)
        .cornerRadius(8)
        .shadow(radius: 2)
    }
}

struct HealingProgressView: View {
    let data: [HistoricalMeasurement]
    let currentResult: WoundMeasurementResult
    
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("癒合進度")
                .font(.headline)
            
            if let progress = calculateHealingProgress() {
                VStack(alignment: .leading, spacing: 8) {
                    HStack {
                        Text("整體進度")
                            .font(.subheadline)
                        
                        Spacer()
                        
                        Text("\(Int(progress * 100))%")
                            .font(.subheadline)
                            .fontWeight(.semibold)
                    }
                    
                    ProgressView(value: progress)
                        .tint(.green)
                    
                    Text(getProgressDescription(progress))
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                
                // 癒合階段時間線
                HealingTimelineView(data: data)
            } else {
                Text("需要更多數據來評估癒合進度")
                    .foregroundColor(.secondary)
            }
        }
        .padding()
        .background(Color.green.opacity(0.1))
        .cornerRadius(10)
    }
    
    private func calculateHealingProgress() -> Double? {
        guard let firstMeasurement = data.first,
              let currentArea = currentResult.area else { return nil }
        
        let initialArea = firstMeasurement.area
        let areaReduction = (initialArea - currentArea) / initialArea
        
        return max(0, min(1, areaReduction))
    }
    
    private func getProgressDescription(_ progress: Double) -> String {
        switch progress {
        case 0.0..<0.3: return "初期癒合階段，繼續保持現有護理"
        case 0.3..<0.7: return "癒合進展良好，可以考慮調整護理計劃"
        case 0.7..<0.9: return "接近完全癒合，繼續監測"
        default: return "癒合狀況優良"
        }
    }
}

struct HealingTimelineView: View {
    let data: [HistoricalMeasurement]
    
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("癒合階段")
                .font(.subheadline)
                .fontWeight(.medium)
            
            HStack {
                ForEach(getHealingStages(), id: \.stage) { stage in
                    VStack(spacing: 4) {
                        Circle()
                            .fill(stage.isActive ? .green : .gray.opacity(0.3))
                            .frame(width: 12, height: 12)
                        
                        Text(stage.stage)
                            .font(.caption2)
                            .multilineTextAlignment(.center)
                    }
                    
                    if stage.stage != "成熟期" {
                        Rectangle()
                            .fill(.gray.opacity(0.3))
                            .frame(height: 2)
                    }
                }
            }
        }
    }
    
    private func getHealingStages() -> [(stage: String, isActive: Bool)] {
        let stages = ["發炎期", "增生期", "成熟期"]
        let currentStage = data.last?.healingStage ?? "發炎期"
        let currentIndex = stages.firstIndex(of: currentStage) ?? 0
        
        return stages.enumerated().map { index, stage in
            (stage: stage, isActive: index <= currentIndex)
        }
    }
}

struct RecommendationsView: View {
    let data: [HistoricalMeasurement]
    let currentResult: WoundMeasurementResult
    
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("建議與注意事項")
                .font(.headline)
            
            LazyVStack(alignment: .leading, spacing: 8) {
                ForEach(generateRecommendations(), id: \.self) { recommendation in
                    HStack(alignment: .top, spacing: 8) {
                        Image(systemName: "checkmark.circle.fill")
                            .foregroundColor(.blue)
                            .font(.caption)
                        
                        Text(recommendation)
                            .font(.caption)
                            .multilineTextAlignment(.leading)
                    }
                }
            }
        }
        .padding()
        .background(Color.orange.opacity(0.1))
        .cornerRadius(10)
    }
    
    private func generateRecommendations() -> [String] {
        var recommendations: [String] = []
        
        // 基於趨勢的建議
        if let trend = analyzeHealingTrend() {
            switch trend {
            case .improving:
                recommendations.append("癒合進展良好，繼續當前的護理方案")
            case .stable:
                recommendations.append("考慮調整護理頻率或方法以促進癒合")
            case .declining:
                recommendations.append("建議尋求專業醫療建議，傷口可能需要特別護理")
            }
        }
        
        // 基於數據品質的建議
        let avgConfidence = data.map(\.confidence).reduce(0, +) / Double(max(data.count, 1))
        if avgConfidence < 0.7 {
            recommendations.append("建議改善拍攝條件以提高測量精度")
        }
        
        // 基於測量頻率的建議
        if data.count < 7 {
            recommendations.append("建議增加測量頻率以更好地追蹤癒合進展")
        }
        
        return recommendations
    }
    
    private func analyzeHealingTrend() -> StatCard.Trend? {
        guard data.count >= 3 else { return nil }
        
        let recentData = Array(data.suffix(3))
        let changes = zip(recentData.dropFirst(), recentData).map { current, previous in
            current.area - previous.area
        }
        
        let averageChange = changes.reduce(0, +) / Double(changes.count)
        
        if averageChange < -0.3 {
            return .improving
        } else if averageChange > 0.1 {
            return .declining
        } else {
            return .stable
        }
    }
}

struct TrendAnalysisDetailView: View {
    let data: [HistoricalMeasurement]
    
    var body: some View {
        NavigationView {
            ScrollView {
                VStack(spacing: 20) {
                    Text("詳細趨勢分析")
                        .font(.title2)
                        .fontWeight(.bold)
                    
                    // 詳細圖表和分析內容
                    Text("功能開發中...")
                        .foregroundColor(.secondary)
                }
                .padding()
            }
            .navigationTitle("趨勢分析")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("關閉") {
                        // 關閉視圖
                    }
                }
            }
        }
    }
}

struct HistoricalMeasurement {
    let date: Date
    let area: Double
    let volume: Double
    let acuteScore: Double
    let chronicScore: Double
    let confidence: Double
    let healingStage: String
    let notes: String?
}

#Preview {
    AnalysisHistoryView(
        result: WoundMeasurementResult(
            area: 12.5,
            volume: 2.3,
            classification: DetailedWoundClassification(
                acuteScore: 0.8,
                chronicScore: 0.2,
                infectedScore: 0.1,
                healingScore: 0.7,
                confidence: 0.85
            ),
            timestamp: Date()
        )
    )
}