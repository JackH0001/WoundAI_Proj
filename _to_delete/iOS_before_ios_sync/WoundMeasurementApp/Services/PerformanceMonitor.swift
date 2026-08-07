import Foundation
import SwiftUI
import Combine

// MARK: - 性能監控服務
class PerformanceMonitor: ObservableObject {
    @Published var processingTimes: [String: TimeInterval] = [:]
    @Published var memoryUsage: Double = 0.0
    @Published var cpuUsage: Double = 0.0
    @Published var batteryLevel: Float = 0.0
    
    private var memoryMonitorTimer: Timer?
    private var performanceTimer: Timer?
    
    init() {
        startMonitoring()
    }
    
    deinit {
        stopMonitoring()
    }
    
    func startMonitoring() {
        // 記憶體監控
        memoryMonitorTimer = Timer.scheduledTimer(withTimeInterval: 2.0, repeats: true) { [weak self] _ in
            self?.updateMemoryUsage()
        }
        
        // 性能監控
        performanceTimer = Timer.scheduledTimer(withTimeInterval: 5.0, repeats: true) { [weak self] _ in
            self?.updatePerformanceMetrics()
        }
    }
    
    func stopMonitoring() {
        memoryMonitorTimer?.invalidate()
        memoryMonitorTimer = nil
        performanceTimer?.invalidate()
        performanceTimer = nil
    }
    
    private func updateMemoryUsage() {
        var info = mach_task_basic_info()
        var count = mach_msg_type_number_t(MemoryLayout<mach_task_basic_info>.size)/4
        
        let kerr: kern_return_t = withUnsafeMutablePointer(to: &info) {
            $0.withMemoryRebound(to: integer_t.self, capacity: 1) {
                task_info(mach_task_self(),
                         task_flavor_t(MACH_TASK_BASIC_INFO),
                         $0,
                         &count)
            }
        }
        
        if kerr == KERN_SUCCESS {
            let usedMB = Double(info.resident_size) / 1024.0 / 1024.0
            DispatchQueue.main.async {
                self.memoryUsage = usedMB
            }
        }
    }
    
    private func updatePerformanceMetrics() {
        // 更新電池狀態
        UIDevice.current.isBatteryMonitoringEnabled = true
        batteryLevel = UIDevice.current.batteryLevel
        
        // 更新CPU使用率（簡化版本）
        updateCPUUsage()
    }
    
    private func updateCPUUsage() {
        // 簡化的CPU使用率計算
        // 在實際應用中，這需要更複雜的系統調用
        let randomUsage = Double.random(in: 0.1...0.3) // 模擬值
        DispatchQueue.main.async {
            self.cpuUsage = randomUsage
        }
    }
    
    // 記錄處理時間
    func recordProcessingTime(for operation: String, duration: TimeInterval) {
        DispatchQueue.main.async {
            self.processingTimes[operation] = duration
        }
    }
    
    // 獲取性能報告
    func getPerformanceReport() -> String {
        let memoryMB = String(format: "%.1f", memoryUsage)
        let cpuPercent = String(format: "%.1f", cpuUsage * 100)
        let batteryPercent = String(format: "%.0f", batteryLevel * 100)
        
        var report = """
        性能監控報告
        ================
        記憶體使用: \(memoryMB) MB
        CPU使用率: \(cpuPercent)%
        電池電量: \(batteryPercent)%
        
        處理時間記錄:
        """
        
        for (operation, time) in processingTimes {
            report += "\n- \(operation): \(String(format: "%.2f", time))s"
        }
        
        return report
    }
    
    // 檢查是否需要性能警告
    func shouldShowPerformanceWarning() -> Bool {
        return memoryUsage > 800 || cpuUsage > 0.8 || batteryLevel < 0.2
    }
    
    // 獲取性能警告消息
    func getPerformanceWarningMessage() -> String? {
        var warnings: [String] = []
        
        if memoryUsage > 800 {
            warnings.append("記憶體使用過高 (\(String(format: "%.1f", memoryUsage)) MB)")
        }
        
        if cpuUsage > 0.8 {
            warnings.append("CPU使用率過高 (\(String(format: "%.1f", cpuUsage * 100))%)")
        }
        
        if batteryLevel < 0.2 {
            warnings.append("電池電量過低 (\(String(format: "%.0f", batteryLevel * 100))%)")
        }
        
        return warnings.isEmpty ? nil : warnings.joined(separator: ", ")
    }
}

// MARK: - 性能警告視圖
struct PerformanceWarningView: View {
    @ObservedObject var performanceMonitor: PerformanceMonitor
    
    var body: some View {
        if performanceMonitor.shouldShowPerformanceWarning() {
            HStack {
                Image(systemName: "exclamationmark.triangle.fill")
                    .foregroundColor(.orange)
                
                Text(performanceMonitor.getPerformanceWarningMessage() ?? "")
                    .font(.caption)
                    .foregroundColor(.orange)
                
                Spacer()
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .background(Color.orange.opacity(0.1))
            .cornerRadius(8)
            .padding(.horizontal, 12)
        }
    }
}
