import Foundation
import SwiftUI
import os.log

// MARK: - 錯誤類型
// WoundMeasurementError已移至ContentView.swift中統一定義以解決編譯衝突

// MARK: - 錯誤處理器

class ErrorHandler: ObservableObject {
    static let shared = ErrorHandler()
    
    @Published var currentError: WoundMeasurementError?
    @Published var showingErrorAlert = false
    @Published var errorLog: [ErrorLogEntry] = []
    
    private let logger = Logger(subsystem: "com.woundmeasurement.app", category: "error")
    
    private init() {}
    
    func handleError(_ error: Error, context: String = "") {
        let woundError: WoundMeasurementError
        
        if let woundMeasurementError = error as? WoundMeasurementError {
            woundError = woundMeasurementError
        } else {
            // 將其他錯誤轉換為通用錯誤
            woundError = .unknown("未知錯誤")
        }
        
        // 記錄錯誤
        logError(woundError, context: context, originalError: error)
        
        // 更新UI
        DispatchQueue.main.async {
            self.currentError = woundError
            self.showingErrorAlert = true
        }
    }
    
    func handleError(_ error: WoundMeasurementError, context: String = "") {
        logError(error, context: context)
        
        DispatchQueue.main.async {
            self.currentError = error
            self.showingErrorAlert = true
        }
    }
    
    func clearError() {
        currentError = nil
        showingErrorAlert = false
    }
    
    private func logError(_ error: WoundMeasurementError, context: String = "", originalError: Error? = nil) {
        let entry = ErrorLogEntry(
            timestamp: Date(),
            error: error,
            context: context,
            originalError: originalError?.localizedDescription
        )
        
        errorLog.append(entry)
        
        // 系統日誌
        logger.error("錯誤: \(error.localizedDescription), 上下文: \(context)")
        
        // 限制日誌數量
        if errorLog.count > 100 {
            errorLog.removeFirst(50)
        }
    }
    
    func exportErrorLog() -> Data? {
        let logData = errorLog.map { entry in
            [
                "timestamp": entry.timestamp.timeIntervalSince1970,
                "error": entry.error.localizedDescription,
                "context": entry.context,
                "originalError": entry.originalError ?? ""
            ]
        }
        
        return try? JSONSerialization.data(withJSONObject: logData, options: .prettyPrinted)
    }
}

// MARK: - 錯誤日誌條目

struct ErrorLogEntry: Identifiable {
    let id = UUID()
    let timestamp: Date
    let error: WoundMeasurementError
    let context: String
    let originalError: String?
}

// MARK: - 錯誤警報視圖

struct ErrorAlertView: View {
    @ObservedObject var errorHandler = ErrorHandler.shared
    
    var body: some View {
        EmptyView()
            .alert("錯誤", isPresented: $errorHandler.showingErrorAlert) {
                Button("確定") {
                    errorHandler.clearError()
                }
                
                if let error = errorHandler.currentError,
                   error.recoverySuggestion != nil {
                    Button("查看解決方案") {
                        showRecoveryGuide(for: error)
                    }
                }
            } message: {
                if let error = errorHandler.currentError {
                    Text(error.localizedDescription)
                }
            }
    }
    
    private func showRecoveryGuide(for error: WoundMeasurementError) {
        // 顯示詳細的解決方案指南
        print("顯示解決方案指南: \(error.recoverySuggestion ?? "")")
    }
}

// MARK: - 錯誤日誌視圖

struct ErrorLogView: View {
    @ObservedObject var errorHandler = ErrorHandler.shared
    @State private var showingExportSheet = false
    @State private var exportData: Data?
    
    var body: some View {
        NavigationView {
            List {
                ForEach(errorHandler.errorLog.reversed()) { entry in
                    ErrorLogEntryView(entry: entry)
                }
            }
            .navigationTitle("錯誤日誌")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("匯出") {
                        exportData = errorHandler.exportErrorLog()
                        showingExportSheet = true
                    }
                }
            }
            .sheet(isPresented: $showingExportSheet) {
                if let data = exportData {
                    ActivityView(activityItems: [data])
                }
            }
        }
    }
}

// MARK: - 錯誤日誌條目視圖

struct ErrorLogEntryView: View {
    let entry: ErrorLogEntry
    
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(entry.error.localizedDescription)
                    .font(.headline)
                    .foregroundColor(.red)
                
                Spacer()
                
                Text(entry.timestamp, style: .time)
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
            
            if !entry.context.isEmpty {
                Text("上下文: \(entry.context)")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
            
            if let originalError = entry.originalError {
                Text("原始錯誤: \(originalError)")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
            
            if let suggestion = entry.error.recoverySuggestion {
                Text("建議: \(suggestion)")
                    .font(.caption)
                    .foregroundColor(.blue)
            }
        }
        .padding(.vertical, 4)
    }
}

// MARK: - 分享表單（通用替代，避免相依其他檔案）
struct ActivityView: UIViewControllerRepresentable {
    let activityItems: [Any]
    let applicationActivities: [UIActivity]? = nil
    
    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: activityItems, applicationActivities: applicationActivities)
    }
    
    func updateUIViewController(_ uiViewController: UIActivityViewController, context: Context) {}
}

// MARK: - 錯誤處理擴展

extension View {
    func handleErrors() -> some View {
        self.modifier(ErrorHandlingModifier())
    }
}

struct ErrorHandlingModifier: ViewModifier {
    @StateObject private var errorHandler = ErrorHandler.shared
    
    func body(content: Content) -> some View {
        content
            .alert("錯誤", isPresented: $errorHandler.showingErrorAlert) {
                Button("確定") {
                    errorHandler.clearError()
                }
            } message: {
                if let error = errorHandler.currentError {
                    Text(error.localizedDescription)
                }
            }
    }
}

// MARK: - 結果類型

enum Result<T> {
    case success(T)
    case failure(WoundMeasurementError)
    
    var isSuccess: Bool {
        switch self {
        case .success: return true
        case .failure: return false
        }
    }
    
    var value: T? {
        switch self {
        case .success(let value): return value
        case .failure: return nil
        }
    }
    
    var error: WoundMeasurementError? {
        switch self {
        case .success: return nil
        case .failure(let error): return error
        }
    }
}

// MARK: - 異步錯誤處理

extension Task where Success == Never, Failure == Never {
    static func handleErrors<T>(_ operation: @escaping () async throws -> T) async -> Result<T> {
        do {
            let result = try await operation()
            return .success(result)
        } catch {
            if let woundError = error as? WoundMeasurementError {
                return .failure(woundError)
            } else {
                return .failure(.unknown("未知錯誤"))
            }
        }
    }
} 