@preconcurrency import ARKit
import Foundation
import Combine

/// 統一的 ARSession 管理器，避免多模組競爭資源
class ARSessionManager: NSObject, ObservableObject {
    static let shared = ARSessionManager()
    
    @Published private(set) var currentSession: ARSession?
    @Published private(set) var sessionState: ARSessionState = .idle
    @Published private(set) var currentConfiguration: ARConfiguration?
    @Published private(set) var conflictCount: Int = 0
    @Published private(set) var sessionSwitchCount: Int = 0
    private var isRunningOrStarting: Bool = false
    
    private var sessionOwner: SessionOwner = .none
    private var pendingOwner: SessionOwner?
    private var ownershipQueue = DispatchQueue(label: "ar.session.ownership", qos: .userInitiated)
    
    // 增強的衝突檢測和排隊機制
    private var requestQueue: [SessionRequest] = []
    private var ownershipHistory: [OwnershipRecord] = []
    private var maxHistoryCount = 50
    
    // 性能監控移除以簡化依賴，避免額外耦合
    
    enum ARSessionState {
        case idle
        case initializing
        case running
        case paused
        case failed(Error)
    }
    
    enum SessionOwner: Equatable {
        case none
        case capture
        case lidarCalibration
        case other(String)
        
        var displayName: String {
            switch self {
            case .none: return "無"
            case .capture: return "相機拍攝"
            case .lidarCalibration: return "LiDAR校準"
            case .other(let name): return name
            }
        }
        
        var priority: Int {
            switch self {
            case .none: return 0
            case .lidarCalibration: return 3  // 最高優先級
            case .capture: return 2
            case .other: return 1
            }
        }
    }
    
    // MARK: - 新增資料結構
    struct SessionRequest {
        let id = UUID()
        let owner: SessionOwner
        let configuration: ARConfiguration
        let timestamp: Date
        let priority: Int
        let completion: (ARSession?) -> Void
        
        init(owner: SessionOwner, configuration: ARConfiguration, completion: @escaping (ARSession?) -> Void) {
            self.owner = owner
            self.configuration = configuration
            self.priority = owner.priority
            self.timestamp = Date()
            self.completion = completion
        }
    }
    
    struct OwnershipRecord {
        let previousOwner: SessionOwner
        let newOwner: SessionOwner
        let timestamp: Date
        let switchDuration: TimeInterval?
        let wasConflict: Bool
    }
    
    private override init() {
        super.init()
        setupNotifications()
    }

    // MARK: - 標準化 AR 設定（精簡重複程式碼）
    static func makeStandardConfiguration(
        lidarEnabled: Bool,
        planeDetection: ARWorldTrackingConfiguration.PlaneDetection = [],
        environmentTexturing: ARWorldTrackingConfiguration.EnvironmentTexturing = .none
    ) -> ARWorldTrackingConfiguration {
        let configuration = ARWorldTrackingConfiguration()
        if lidarEnabled {
            if ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth) {
                configuration.frameSemantics.insert(.sceneDepth)
            }
            if ARWorldTrackingConfiguration.supportsFrameSemantics(.smoothedSceneDepth) {
                configuration.frameSemantics.insert(.smoothedSceneDepth)
            }
        }
        configuration.planeDetection = planeDetection
        configuration.environmentTexturing = environmentTexturing
        return configuration
    }
    
    deinit {
        Task { @MainActor in
            self.cleanupSession()
        }
        NotificationCenter.default.removeObserver(self)
    }
    
    private func setupNotifications() {
        NotificationCenter.default.addObserver(
            self,
            selector: #selector(handleLiDARCalibrationWillStart),
            name: .lidarCalibrationWillStart,
            object: nil
        )
        
        NotificationCenter.default.addObserver(
            self,
            selector: #selector(handleLiDARCalibrationDidStop),
            name: .lidarCalibrationDidStop,
            object: nil
        )
    }
    
    @objc private func handleLiDARCalibrationWillStart() {
        print("🔄 ARSessionManager: 接收到LiDAR校準開始通知")
        Task {
            await requestSessionOwnership(for: .lidarCalibration)
        }
    }
    
    @objc private func handleLiDARCalibrationDidStop() {
        print("🔄 ARSessionManager: 接收到LiDAR校準停止通知")
        Task {
            await releaseSessionOwnership(from: .lidarCalibration)
        }
    }
    
    /// 增強的 ARSession 所有權請求方法
    @MainActor
    func requestSessionOwnership(for owner: SessionOwner, configuration: ARConfiguration = ARWorldTrackingConfiguration()) async -> ARSession? {
        return await withCheckedContinuation { continuation in
            ownershipQueue.async {
                let request = SessionRequest(
                    owner: owner,
                    configuration: configuration
                ) { session in
                    continuation.resume(returning: session)
                }

                Task { @MainActor in
                    self.processSessionRequest(request)
                }
            }
        }
    }
    
    /// 處理會話請求（包含衝突檢測和排隊）
    @MainActor
    private func processSessionRequest(_ request: SessionRequest) {
        print("📋 ARSession所有權請求: \(request.owner.displayName) (當前擁有者: \(sessionOwner.displayName))")
        
        // 檢測衝突
        let isConflict = sessionOwner != .none && sessionOwner != request.owner
        if isConflict {
            conflictCount += 1
            print("⚠️ ARSession衝突檢測: \(sessionOwner.displayName) vs \(request.owner.displayName)")
        }
        
        // 如果當前沒有擁有者，直接分配
        if sessionOwner == .none {
            // 在主執行緒同步建立並運行 session，避免競態導致 currentSession 為 nil
            Task { @MainActor in
                self.sessionOwner = request.owner
                self.currentConfiguration = request.configuration
                let session = self.getOrCreateSession()
                if !self.isRunningOrStarting {
                    self.isRunningOrStarting = true
                    session.run(request.configuration)
                    self.isRunningOrStarting = false
                }
                self.sessionState = .running
                print("✅ ARSession所有權已分配並啟動: \(request.owner.displayName)")
                request.completion(session)
            }
            return
        }
        
        // 如果是同一個擁有者，返回現有session
        if sessionOwner == request.owner {
            request.completion(currentSession)
            return
        }
        
        // 檢查優先級決定是否搶佔或排隊
        if request.priority > sessionOwner.priority {
            // 高優先級請求，立即搶佔
            print("🔄 高優先級搶佔: \(request.owner.displayName) 搶佔 \(sessionOwner.displayName)")
            forceTransferOwnership(to: request.owner, with: request.configuration)
            let session = getOrCreateSession()
            request.completion(session)
        } else {
            // 低優先級請求，加入排隊
            print("⏳ 加入排隊: \(request.owner.displayName) 等待 \(sessionOwner.displayName)")
            requestQueue.append(request)
            
            // 設置超時機制（5秒後強制返回nil）
            DispatchQueue.global().asyncAfter(deadline: .now() + 5.0) {
                if let index = self.requestQueue.firstIndex(where: { $0.id == request.id }) {
                    self.requestQueue.remove(at: index)
                    print("⏰ 請求超時: \(request.owner.displayName)")
                    request.completion(nil)
                }
            }
        }
    }
    
    /// 強制轉移所有權（用於優先級搶佔）
    private func forceTransferOwnership(to newOwner: SessionOwner, with configuration: ARConfiguration) {
        let previousOwner = sessionOwner
        _ = Date() // 保留時間點以便日後擴充
        
        // 記錄所有權變更
        recordOwnershipChange(from: previousOwner, to: newOwner, wasConflict: true)
        
        // 暫停當前session
        currentSession?.pause()
        
        // 切換所有權
        sessionOwner = newOwner
        currentConfiguration = configuration
        sessionSwitchCount += 1
        
        // 重新配置session
        DispatchQueue.main.async {
            guard !self.isRunningOrStarting else { return }
            self.isRunningOrStarting = true
            self.sessionState = .initializing
            self.currentSession?.run(configuration)
            self.sessionState = .running
            self.isRunningOrStarting = false
        }
        
        print("✅ 強制轉移完成: \(previousOwner.displayName) → \(newOwner.displayName)")
    }
    
    /// 記錄所有權變更歷史
    private func recordOwnershipChange(from previous: SessionOwner, to new: SessionOwner, wasConflict: Bool) {
        let record = OwnershipRecord(
            previousOwner: previous,
            newOwner: new,
            timestamp: Date(),
            switchDuration: nil,
            wasConflict: wasConflict
        )
        
        ownershipHistory.append(record)
        
        // 保持歷史記錄數量在限制內
        if ownershipHistory.count > maxHistoryCount {
            ownershipHistory.removeFirst()
        }
        
        // 通知所有權變更
        DispatchQueue.main.async {
            NotificationCenter.default.post(
                name: .arSessionOwnershipChanged,
                object: record
            )
        }
    }
    
    /// 處理排隊中的請求
    @MainActor
    private func processQueuedRequests() {
        guard !requestQueue.isEmpty else { return }
        
        // 按優先級排序
        requestQueue.sort { $0.priority > $1.priority }
        
        // 處理第一個請求
        if let nextRequest = requestQueue.first {
            requestQueue.removeFirst()
            processSessionRequest(nextRequest)
        }
    }
    
    
    /// 釋放 ARSession 所有權
    @MainActor
    func releaseSessionOwnership(from owner: SessionOwner) async {
        return await withCheckedContinuation { continuation in
            ownershipQueue.async {
                guard self.sessionOwner == owner else {
                    print("⚠️ ARSession所有權釋放失敗: \(owner.displayName) 不是當前擁有者 (\(self.sessionOwner.displayName))")
                    continuation.resume()
                    return
                }
                
                print("✅ ARSession所有權已釋放: \(owner.displayName)")
                
                // 記錄所有權變更
                self.recordOwnershipChange(from: owner, to: .none, wasConflict: false)
                
                // 釋放所有權
                self.sessionOwner = .none
                self.currentConfiguration = nil
                
                // 暫停session
                DispatchQueue.main.async {
                    self.currentSession?.pause()
                    self.sessionState = .paused
                }
                
                // 處理排隊中的請求
                self.processQueuedRequests()
                
                continuation.resume()
            }
        }
    }

    /// 分配所有權並運行指定配置
    private func assignOwnership(to owner: SessionOwner, with configuration: ARConfiguration) {
        sessionOwner = owner
        currentConfiguration = configuration
        print("✅ ARSession所有權已分配給: \(owner.displayName)")
        
        Task { @MainActor in
            let session = self.getOrCreateSession()
            session.run(configuration)
            self.sessionState = .running
        }
    }
    
    private func assignOwnership(to owner: SessionOwner) {
        sessionOwner = owner
        print("✅ ARSession所有權已分配給: \(owner.displayName)")
        
        Task { @MainActor in
            self.sessionState = .running
        }
    }
    
    private func transferOwnership(from currentOwner: SessionOwner, to newOwner: SessionOwner) {
        print("🔄 ARSession所有權轉移: \(currentOwner.displayName) → \(newOwner.displayName)")
        
        // 暫停當前session
        Task { @MainActor in
            self.currentSession?.pause()
            self.sessionState = .paused
        }
        
        // 短暫延遲後分配給新擁有者
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
            self.assignOwnership(to: newOwner)
        }
    }
    
    @MainActor private func getOrCreateSession() -> ARSession {
        if currentSession == nil {
            let session = ARSession()
            currentSession = session
            sessionState = .initializing
            print("🆕 創建新的ARSession")
        }
        return currentSession!
    }
    
    /// 運行指定配置
    @MainActor
    func runConfiguration(_ configuration: ARConfiguration, options: ARSession.RunOptions = []) {
        guard let session = currentSession else {
            print("❌ 無法運行配置: 沒有活躍的ARSession")
            return
        }
        
        currentConfiguration = configuration
        session.run(configuration, options: options)
        sessionState = .running
        
        print("🚀 ARSession配置已啟動: \(type(of: configuration)) (擁有者: \(sessionOwner.displayName))")
    }
    
    /// 暫停當前session
    @MainActor
    func pauseSession() {
        currentSession?.pause()
        sessionState = .paused
        print("⏸ ARSession已暫停")
    }
    
    /// 清理session
    @MainActor
    func cleanupSession() {
        currentSession?.pause()
        currentSession?.delegate = nil
        currentSession = nil
        currentConfiguration = nil
        sessionState = .idle
        sessionOwner = .none
        pendingOwner = nil
        print("🧹 ARSession已清理")
    }
    
    /// 獲取當前session的狀態信息
    var debugInfo: String {
        return """
        ARSession狀態:
        - 狀態: \(sessionState)
        - 擁有者: \(sessionOwner.displayName)
        - 待處理擁有者: \(pendingOwner?.displayName ?? "無")
        - Session存在: \(currentSession != nil)
        - 配置: \(currentConfiguration != nil ? String(describing: type(of: currentConfiguration!)) : "無")
        """
    }
}

// MARK: - ARSessionDelegate
extension ARSessionManager: ARSessionDelegate {
    func session(_ session: ARSession, didFailWithError error: Error) {
        Task { @MainActor in
            self.sessionState = .failed(error)
            print("❌ ARSession失敗: \(error.localizedDescription)")
        }
        
        // 通知當前擁有者session失敗
        NotificationCenter.default.post(
            name: .arSessionDidFail,
            object: nil,
            userInfo: ["error": error, "owner": sessionOwner.displayName]
        )
    }
    
    func sessionWasInterrupted(_ session: ARSession) {
        Task { @MainActor in
            self.sessionState = .paused
            print("⚠️ ARSession被中斷")
        }
        
        // 通知當前擁有者session被中斷
        NotificationCenter.default.post(
            name: .arSessionWasInterrupted,
            object: nil,
            userInfo: ["owner": sessionOwner.displayName]
        )
    }
    
    func sessionInterruptionEnded(_ session: ARSession) {
        Task { @MainActor in
            if let config = currentConfiguration {
                session.run(config)
                self.sessionState = .running
                print("🔄 ARSession中斷結束，恢復運行")
            }
        }
        
        // 通知當前擁有者session恢復
        NotificationCenter.default.post(
            name: .arSessionInterruptionEnded,
            object: nil,
            userInfo: ["owner": sessionOwner.displayName]
        )
    }
}

// MARK: - Notification Extensions
extension Notification.Name {
    static let arSessionDidFail = Notification.Name("arSessionDidFail")
    static let arSessionWasInterrupted = Notification.Name("arSessionWasInterrupted")
    static let arSessionInterruptionEnded = Notification.Name("arSessionInterruptionEnded")
    static let arSessionOwnershipChanged = Notification.Name("arSessionOwnershipChanged")
    static let arSessionConflictDetected = Notification.Name("arSessionConflictDetected")
}

// MARK: - 錯誤類型定義
struct ARSessionConflictError: Error, LocalizedError {
    let currentOwner: ARSessionManager.SessionOwner
    let requestedOwner: ARSessionManager.SessionOwner
    
    var errorDescription: String? {
        return "ARSession衝突: \(currentOwner.displayName) 與 \(requestedOwner.displayName)"
    }
    
    var failureReason: String? {
        return "多個模組同時請求ARSession使用權"
    }
    
    var recoverySuggestion: String? {
        return "請等待當前操作完成或檢查模組間的協調機制"
    }
}