import SwiftUI
import ARKit

struct ARSessionMonitorView: View {
    @StateObject private var arSessionManager = ARSessionManager.shared
    @State private var showingHistory = false
    @State private var isExpanded = false
    
    var body: some View {
        VStack(spacing: 12) {
            // 標題和展開控制
            HStack {
                Text("ARSession 監控")
                    .font(.headline)
                    .fontWeight(.semibold)
                
                Spacer()
                
                Button(action: { isExpanded.toggle() }) {
                    Image(systemName: isExpanded ? "chevron.up" : "chevron.down")
                        .foregroundColor(.blue)
                }
            }
            
            if isExpanded {
                VStack(spacing: 16) {
                    // 當前狀態
                    currentStatusSection
                    
                    // 統計信息
                    statisticsSection
                    
                    // 控制按鈕
                    controlButtonsSection
                }
            } else {
                // 緊湊狀態顯示
                compactStatusSection
            }
        }
        .padding()
        .background(Color.gray.opacity(0.1))
        .cornerRadius(12)
        .sheet(isPresented: $showingHistory) {
            ARSessionHistoryView()
        }
    }
    
    // MARK: - 當前狀態區域
    private var currentStatusSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("當前狀態")
                .font(.subheadline)
                .fontWeight(.medium)
            
            HStack(spacing: 16) {
                // 會話狀態
                StatusBadge(
                    title: "會話",
                    value: sessionStateText,
                    color: sessionStateColor
                )
                
                // 當前擁有者
                StatusBadge(
                    title: "擁有者",
                    value: arSessionManager.currentSession != nil ? getCurrentOwnerName() : "無",
                    color: .blue
                )
            }
            
            // 配置信息
            if let config = arSessionManager.currentConfiguration {
                Text("配置: \(getConfigurationName(config))")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }
    }
    
    // MARK: - 統計信息區域
    private var statisticsSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("統計信息")
                .font(.subheadline)
                .fontWeight(.medium)
            
            HStack(spacing: 12) {
                StatCard(
                    title: "衝突次數",
                    value: "\(arSessionManager.conflictCount)",
                    icon: "exclamationmark.triangle",
                    color: arSessionManager.conflictCount > 0 ? .red : .green
                )
                
                StatCard(
                    title: "切換次數",
                    value: "\(arSessionManager.sessionSwitchCount)",
                    icon: "arrow.triangle.2.circlepath",
                    color: .orange
                )
            }
        }
    }
    
    // MARK: - 緊湊狀態顯示
    private var compactStatusSection: some View {
        HStack {
            // 狀態指示器
            Circle()
                .fill(sessionStateColor)
                .frame(width: 12, height: 12)
            
            Text(sessionStateText)
                .font(.body)
                .fontWeight(.medium)
            
            Spacer()
            
            // 衝突指示器
            if arSessionManager.conflictCount > 0 {
                HStack(spacing: 4) {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .foregroundColor(.red)
                        .font(.caption)
                    Text("\(arSessionManager.conflictCount)")
                        .font(.caption)
                        .fontWeight(.bold)
                        .foregroundColor(.red)
                }
            }
        }
    }
    
    // MARK: - 控制按鈕區域
    private var controlButtonsSection: some View {
        HStack(spacing: 12) {
            Button("查看歷史") {
                showingHistory = true
            }
            .buttonStyle(SecondaryButtonStyle())
            
            Button("重置統計") {
                resetStatistics()
            }
            .buttonStyle(SecondaryButtonStyle())
            
            Spacer()
            
            Button("診斷檢查") {
                performDiagnostics()
            }
            .buttonStyle(PrimaryButtonStyle())
        }
    }
    
    // MARK: - 輔助方法
    private var sessionStateText: String {
        switch arSessionManager.sessionState {
        case .idle:
            return "空閒"
        case .initializing:
            return "初始化中"
        case .running:
            return "運行中"
        case .paused:
            return "已暫停"
        case .failed(_):
            return "失敗"
        }
    }
    
    private var sessionStateColor: Color {
        switch arSessionManager.sessionState {
        case .idle:
            return .gray
        case .initializing:
            return .yellow
        case .running:
            return .green
        case .paused:
            return .orange
        case .failed(_):
            return .red
        }
    }
    
    private func getCurrentOwnerName() -> String {
        // 這裡需要通過反射或其他方式獲取當前擁有者
        // 暫時返回佔位符
        return "相機模組"
    }
    
    private func getConfigurationName(_ config: ARConfiguration) -> String {
        switch config {
        case is ARWorldTrackingConfiguration:
            return "世界追蹤"
        case is ARFaceTrackingConfiguration:
            return "面部追蹤"
        case is ARBodyTrackingConfiguration:
            return "身體追蹤"
        default:
            return "其他配置"
        }
    }
    
    private func resetStatistics() {
        // 重置統計信息
        // 這需要在ARSessionManager中添加相應方法
        print("🔄 重置ARSession統計信息")
    }
    
    private func performDiagnostics() {
        // 執行診斷檢查
        print("🔍 執行ARSession診斷檢查")
        
        // 檢查設備能力
        let worldTracking = ARWorldTrackingConfiguration.isSupported
        let sceneDepth = ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth)
        let smoothedDepth = ARWorldTrackingConfiguration.supportsFrameSemantics(.smoothedSceneDepth)
        
        print("📱 設備能力檢查:")
        print("  - 世界追蹤: \(worldTracking ? "✅" : "❌")")
        print("  - 場景深度: \(sceneDepth ? "✅" : "❌")")
        print("  - 平滑深度: \(smoothedDepth ? "✅" : "❌")")
    }
}

// MARK: - 支援組件
struct StatusBadge: View {
    let title: String
    let value: String
    let color: Color
    
    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title)
                .font(.caption2)
                .foregroundColor(.secondary)
            Text(value)
                .font(.caption)
                .fontWeight(.semibold)
                .foregroundColor(color)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
        .background(color.opacity(0.1))
        .cornerRadius(6)
    }
}

struct StatCard: View {
    let title: String
    let value: String
    let icon: String
    let color: Color
    
    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: icon)
                .foregroundColor(color)
                .font(.caption)
            
            VStack(alignment: .leading, spacing: 2) {
                Text(value)
                    .font(.caption)
                    .fontWeight(.bold)
                Text(title)
                    .font(.caption2)
                    .foregroundColor(.secondary)
            }
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 6)
        .background(Color.gray.opacity(0.1))
        .cornerRadius(8)
    }
}

// MARK: - 按鈕樣式
struct PrimaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.caption)
            .foregroundColor(.white)
            .padding(.horizontal, 12)
            .padding(.vertical, 6)
            .background(.blue)
            .cornerRadius(6)
            .opacity(configuration.isPressed ? 0.8 : 1.0)
    }
}

struct SecondaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.caption)
            .foregroundColor(.blue)
            .padding(.horizontal, 12)
            .padding(.vertical, 6)
            .background(Color.blue.opacity(0.1))
            .cornerRadius(6)
            .opacity(configuration.isPressed ? 0.8 : 1.0)
    }
}

// MARK: - ARSession歷史記錄視圖
struct ARSessionHistoryView: View {
    @Environment(\.presentationMode) var presentationMode
    @StateObject private var arSessionManager = ARSessionManager.shared
    
    var body: some View {
        NavigationView {
            List {
                Section("會話統計") {
                    HStack {
                        Text("總衝突次數")
                        Spacer()
                        Text("\(arSessionManager.conflictCount)")
                            .fontWeight(.semibold)
                            .foregroundColor(arSessionManager.conflictCount > 0 ? .red : .green)
                    }
                    
                    HStack {
                        Text("總切換次數")
                        Spacer()
                        Text("\(arSessionManager.sessionSwitchCount)")
                            .fontWeight(.semibold)
                            .foregroundColor(.orange)
                    }
                }
                
                Section("設備能力") {
                    CapabilityRow(
                        title: "世界追蹤",
                        supported: ARWorldTrackingConfiguration.isSupported
                    )
                    
                    CapabilityRow(
                        title: "場景深度",
                        supported: ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth)
                    )
                    
                    CapabilityRow(
                        title: "平滑深度",
                        supported: ARWorldTrackingConfiguration.supportsFrameSemantics(.smoothedSceneDepth)
                    )
                    
                    CapabilityRow(
                        title: "平面檢測",
                        supported: ARWorldTrackingConfiguration.supportsUserFaceTracking // 示例
                    )
                }
                
                Section("會話狀態") {
                    HStack {
                        Text("當前狀態")
                        Spacer()
                        SessionStateBadge(state: arSessionManager.sessionState)
                    }
                    
                    if arSessionManager.currentSession != nil {
                        Text("會話已初始化")
                            .foregroundColor(.green)
                    } else {
                        Text("會話未初始化")
                            .foregroundColor(.orange)
                    }
                }
            }
            .navigationTitle("ARSession 歷史")
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

struct CapabilityRow: View {
    let title: String
    let supported: Bool
    
    var body: some View {
        HStack {
            Text(title)
            Spacer()
            Image(systemName: supported ? "checkmark.circle.fill" : "xmark.circle.fill")
                .foregroundColor(supported ? .green : .red)
        }
    }
}

struct SessionStateBadge: View {
    let state: ARSessionManager.ARSessionState
    
    var body: some View {
        Text(stateText)
            .font(.caption)
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(stateColor.opacity(0.2))
            .foregroundColor(stateColor)
            .cornerRadius(4)
    }
    
    private var stateText: String {
        switch state {
        case .idle: return "空閒"
        case .initializing: return "初始化"
        case .running: return "運行中"
        case .paused: return "暫停"
        case .failed(_): return "失敗"
        }
    }
    
    private var stateColor: Color {
        switch state {
        case .idle: return .gray
        case .initializing: return .yellow
        case .running: return .green
        case .paused: return .orange
        case .failed(_): return .red
        }
    }
}

#Preview {
    ARSessionMonitorView()
}