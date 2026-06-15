import Foundation
import UIKit
import SwiftUI
import Combine

// MARK: - 效能監控服務
@MainActor
class EnhancedPerformanceMonitor: ObservableObject {
    static let shared = EnhancedPerformanceMonitor()
    
    // MARK: - 公開屬性
    @Published var currentMemoryUsage: Double = 0.0 // MB
    @Published var peakMemoryUsage: Double = 0.0 // MB
    @Published var averageProcessingTime: Double = 0.0 // 秒
    @Published var lastOperationTime: Double = 0.0 // 秒
    @Published var operationCount: Int = 0
    @Published var errorCount: Int = 0
    @Published var isMonitoring: Bool = false
    
    // MARK: - 效能指標
    @Published var performanceMetrics = PerformanceMetrics()
    
    // MARK: - 私有屬性
    private var monitoringTimer: Timer?
    private var processingTimes: [Double] = []
    private var startTimes: [String: CFAbsoluteTime] = [:]
    private let maxHistoryCount = 100
    
    // MARK: - 記憶體監控
    private var memoryWarningObserver: NSObjectProtocol?
    
    private init() {
        setupMemoryMonitoring()
    }
    
    deinit {
        Task { @MainActor in
            self.stopMonitoring()
        }
        if let observer = memoryWarningObserver {
            NotificationCenter.default.removeObserver(observer)
        }
    }
    
    // MARK: - 監控控制
    func startMonitoring() {
        guard !isMonitoring else { return }
        
        isMonitoring = true
        
        // 每秒更新記憶體使用情況
        monitoringTimer = Timer.scheduledTimer(withTimeInterval: 1.0, repeats: true) { [weak self] _ in
            Task { @MainActor in
                self?.updateMemoryUsage()
            }
        }
        
        print("📊 效能監控已啟動")
    }
    
    func stopMonitoring() {
        isMonitoring = false
        monitoringTimer?.invalidate()
        monitoringTimer = nil
        
        print("📊 效能監控已停止")
    }
    
    // MARK: - 記憶體監控
    private func setupMemoryMonitoring() {
        // 監聽記憶體警告
        memoryWarningObserver = NotificationCenter.default.addObserver(
            forName: UIApplication.didReceiveMemoryWarningNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor in
                self?.handleMemoryWarning()
            }
        }
    }
    
    private func updateMemoryUsage() {
        let memoryUsage = getMemoryUsage()
        currentMemoryUsage = memoryUsage
        
        if memoryUsage > peakMemoryUsage {
            peakMemoryUsage = memoryUsage
        }
        
        // 更新效能指標
        performanceMetrics.currentMemory = memoryUsage
        performanceMetrics.peakMemory = peakMemoryUsage
        
        // 記憶體警告檢查
        if memoryUsage > 300.0 { // 300MB
            print("⚠️ 記憶體使用量過高: \(String(format: "%.1f", memoryUsage))MB")
            performanceMetrics.memoryWarnings += 1
        }
    }
    
    private func getMemoryUsage() -> Double {
        var info = mach_task_basic_info()
        var count = mach_msg_type_number_t(MemoryLayout<mach_task_basic_info>.size)/4
        
        let kerr: kern_return_t = withUnsafeMutablePointer(to: &info) {
            $0.withMemoryRebound(to: integer_t.self, capacity: 1) {
                task_info(mach_task_self_,
                         task_flavor_t(MACH_TASK_BASIC_INFO),
                         $0,
                         &count)
            }
        }
        
        if kerr == KERN_SUCCESS {
            return Double(info.resident_size) / 1024.0 / 1024.0 // 轉換為MB
        } else {
            return 0.0
        }
    }
    
    private func handleMemoryWarning() {
        print("⚠️ 收到記憶體警告，當前使用: \(String(format: "%.1f", currentMemoryUsage))MB")
        performanceMetrics.memoryWarnings += 1
        
        // 觸發記憶體清理
        NotificationCenter.default.post(name: .performanceMemoryWarning, object: nil)
    }
    
    // MARK: - 處理時間監控
    func startOperation(_ operationName: String) {
        startTimes[operationName] = CFAbsoluteTimeGetCurrent()
        print("⏱️ 開始操作: \(operationName)")
    }
    
    func endOperation(_ operationName: String) {
        guard let startTime = startTimes[operationName] else {
            print("❌ 無法找到操作開始時間: \(operationName)")
            return
        }
        
        let duration = CFAbsoluteTimeGetCurrent() - startTime
        lastOperationTime = duration
        
        // 更新統計
        processingTimes.append(duration)
        if processingTimes.count > maxHistoryCount {
            processingTimes.removeFirst()
        }
        
        averageProcessingTime = processingTimes.reduce(0, +) / Double(processingTimes.count)
        operationCount += 1
        
        // 更新效能指標
        performanceMetrics.lastOperationTime = duration
        performanceMetrics.averageProcessingTime = averageProcessingTime
        performanceMetrics.totalOperations = operationCount
        
        startTimes.removeValue(forKey: operationName)
        
        print("✅ 完成操作: \(operationName), 耗時: \(String(format: "%.3f", duration))秒")
        
        // 檢查是否超時
        if duration > 10.0 { // 10秒警告閾值
            print("⚠️ 操作耗時過長: \(operationName) - \(String(format: "%.3f", duration))秒")
            performanceMetrics.slowOperations += 1
        }
    }
    
    // MARK: - 錯誤監控
    func recordError(_ error: Error, in operation: String) {
        errorCount += 1
        performanceMetrics.errorCount += 1
        performanceMetrics.lastError = ErrorRecord(
            error: error,
            operation: operation,
            timestamp: Date()
        )
        
        print("❌ 錯誤記錄: \(operation) - \(error.localizedDescription)")
    }
    
    // MARK: - 報告生成
    func generateReport() -> PerformanceReport {
        return PerformanceReport(
            timestamp: Date(),
            metrics: performanceMetrics,
            processingTimeHistory: processingTimes,
            recommendations: generateRecommendations()
        )
    }
    
    private func generateRecommendations() -> [String] {
        var recommendations: [String] = []
        
        if peakMemoryUsage > 250.0 {
            recommendations.append("建議優化記憶體使用，峰值已達 \(String(format: "%.1f", peakMemoryUsage))MB")
        }
        
        if averageProcessingTime > 5.0 {
            recommendations.append("建議優化處理算法，平均耗時 \(String(format: "%.2f", averageProcessingTime))秒")
        }
        
        if operationCount > 0 && Double(performanceMetrics.errorCount) > Double(operationCount) * 0.1 {
            recommendations.append("錯誤率較高(\(performanceMetrics.errorCount)/\(operationCount))，建議檢查錯誤處理")
        }
        
        if performanceMetrics.slowOperations > 5 {
            recommendations.append("發現 \(performanceMetrics.slowOperations) 次慢操作，建議性能優化")
        }
        
        return recommendations
    }
    
    // MARK: - 重置功能
    func resetMetrics() {
        performanceMetrics = PerformanceMetrics()
        processingTimes.removeAll()
        startTimes.removeAll()
        
        currentMemoryUsage = 0.0
        peakMemoryUsage = 0.0
        averageProcessingTime = 0.0
        lastOperationTime = 0.0
        operationCount = 0
        errorCount = 0
        
        print("📊 效能指標已重置")
    }
}

// MARK: - 效能指標數據結構
struct PerformanceMetrics {
    var currentMemory: Double = 0.0
    var peakMemory: Double = 0.0
    var averageProcessingTime: Double = 0.0
    var lastOperationTime: Double = 0.0
    var totalOperations: Int = 0
    var errorCount: Int = 0
    var memoryWarnings: Int = 0
    var slowOperations: Int = 0
    var lastError: ErrorRecord?
}

struct ErrorRecord {
    let error: Error
    let operation: String
    let timestamp: Date
}

struct PerformanceReport {
    let timestamp: Date
    let metrics: PerformanceMetrics
    let processingTimeHistory: [Double]
    let recommendations: [String]
}

// MARK: - 通知擴展
extension Notification.Name {
    static let performanceMemoryWarning = Notification.Name("PerformanceMemoryWarning")
    static let performanceSlowOperation = Notification.Name("PerformanceSlowOperation")
}