import SwiftUI
import ARKit
import CoreImage
import Vision

class LiDARCalibrationModule: NSObject, ObservableObject, ARSessionDelegate {
    @Published var isCalibrating = false
    @Published var calibrationStatus = "準備中..."
    @Published var measuredDistance: Double?
    @Published var confidence: Double = 0.0
    
    private let arSessionManager = ARSessionManager.shared
    private var currentSession: ARSession?
    private var lastFxPixels: Double? // 從 ARFrame.camera.intrinsics[0,0] 擷取
    private var calibrationTimer: Timer?
    private var distanceReadings: [Double] = []
    private let maxReadings = 8  // 減少采樣數量以加快校準
    private var retryCount = 0
    private let maxRetries = 3
    
    override init() {
        super.init()
        checkLiDARSupport()
    }
    
    deinit {
        stopCalibration()
    }
    
    private func checkLiDARSupport() {
        guard ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth) else {
            print("❌ 設備不支援 LiDAR 場景深度")
            Task { @MainActor in
                calibrationStatus = "設備不支援LiDAR"
            }
            return
        }
        
        print("✅ LiDAR 校準模組已初始化，設備支援深度感測")
    }
    
    func startCalibration() {
        // 確保沒有殘留的計時器或舊數據
        calibrationTimer?.invalidate()
        calibrationTimer = nil
        distanceReadings.removeAll()
        retryCount = 0

        Task { @MainActor in
            isCalibrating = true
            calibrationStatus = "請求ARSession權限..."
            measuredDistance = nil
            confidence = 0.0
        }
        
        // 通知其他模組釋放相機資源 (統一的ARSession管理器會處理)
        NotificationCenter.default.post(name: .lidarCalibrationWillStart, object: nil)
        
        Task {
            // 請求ARSession所有權
            let session = await arSessionManager.requestSessionOwnership(for: .lidarCalibration)
            guard let session = session else {
                await MainActor.run {
                    calibrationStatus = "無法獲取ARSession"
                    isCalibrating = false
                }
                print("❌ 無法獲取ARSession用於LiDAR校準")
                return
            }
            
            currentSession = session
            // 設置delegate以proper處理ARFrame並避免記憶體洩漏
            session.delegate = self
            
            // 配置ARSession（標準化工廠方法）
            let configuration = ARSessionManager.makeStandardConfiguration(
                lidarEnabled: true,
                planeDetection: [],
                environmentTexturing: .none
            )
            
            await MainActor.run {
                arSessionManager.runConfiguration(configuration, options: [.resetTracking])
                calibrationStatus = "AR會話已啟動，等待穩定..."
            }
            
            print("🚀 LiDAR 校準: AR會話已配置並啟動")
            
            // 等待AR會話穩定後開始采樣
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { [weak self] in
                self?.startDataCollection()
            }
        }
    }
    
    private func startDataCollection() {
        Task { @MainActor in
            calibrationStatus = "正在採集深度數據... (0/\(maxReadings))"
            isCalibrating = true // 確保狀態正確
        }
        
        // ARSessionDelegate.session(_:didUpdate:) 方法會自動處理每個新的frame
        // 不再需要Timer，避免重複處理frames和記憶體問題
        print("🎯 開始透過ARSessionDelegate收集LiDAR數據")
        
        // 設定最大校準時間（15秒）
        DispatchQueue.main.asyncAfter(deadline: .now() + 15.0) { [weak self] in
            guard let self = self else { return }
            if self.isCalibrating && self.distanceReadings.count < self.maxReadings {
                print("⏰ LiDAR校準超時，當前收集數據: \(self.distanceReadings.count)/\(self.maxReadings)")
                self.handleCalibrationTimeout()
            }
        }
    }
    
    private func handleCalibrationTimeout() {
        if retryCount < maxRetries {
            retryCount += 1
            Task { @MainActor in
                calibrationStatus = "重試校準 (\(retryCount)/\(maxRetries))..."
            }
            calibrationTimer?.invalidate()
            distanceReadings.removeAll()
            
            // 等待一下再重試
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) { [weak self] in
                self?.startDataCollection()
            }
        } else {
            Task { @MainActor in
                calibrationStatus = "校準失敗：無法獲取穩定的深度數據"
                isCalibrating = false
            }
            stopCalibration()
        }
    }
    
    func stopCalibration() {
        print("🛑 正在停止 LiDAR 校準...")
        
        Task { @MainActor in
            // 將所有與 UI/Timer 相關操作搬到主執行緒
            isCalibrating = false
            calibrationStatus = "已停止"

            calibrationTimer?.invalidate()
            calibrationTimer = nil
            
            // 清空數據
            distanceReadings.removeAll()
            retryCount = 0

            print("🛑 LiDAR 校準已停止")
        }
        
        // 釋放ARSession所有權（異步執行，避免阻塞）
        Task {
            // 清理delegate避免記憶體洩漏
            currentSession?.delegate = nil
            
            await arSessionManager.releaseSessionOwnership(from: .lidarCalibration)
            currentSession = nil
            print("✅ LiDAR 校準已釋放ARSession所有權")
            
            // 通知其他模組可以恢復相機
            NotificationCenter.default.post(name: .lidarCalibrationDidStop, object: nil)
        }
    }
    
    // 強制停止校正（用於緊急情況）
    func forceStopCalibration() {
        print("‼️ 強制停止 LiDAR 校準")
        
        // 立即更新狀態
        isCalibrating = false
        calibrationStatus = "已強制停止"
        
        // 清理所有資源
        calibrationTimer?.invalidate()
        calibrationTimer = nil
        distanceReadings.removeAll()
        retryCount = 0
        measuredDistance = nil
        confidence = 0.0
        
        // 異步釋放ARSession
        Task {
            currentSession?.delegate = nil
            await arSessionManager.releaseSessionOwnership(from: .lidarCalibration)
            currentSession = nil
            
            NotificationCenter.default.post(name: .lidarCalibrationDidStop, object: nil)
            print("✅ LiDAR 校準已強制停止並釋放所有資源")
        }
    }
    
    // 已移除collectDistanceData() - 邏輯已遷移至ARSessionDelegate.session(_:didUpdate:)
    
    private func analyzeCenterDepth(depthMap: CVPixelBuffer, confidenceMap: CVPixelBuffer) -> Double {
        CVPixelBufferLockBaseAddress(depthMap, .readOnly)
        CVPixelBufferLockBaseAddress(confidenceMap, .readOnly)
        
        defer {
            CVPixelBufferUnlockBaseAddress(depthMap, .readOnly)
            CVPixelBufferUnlockBaseAddress(confidenceMap, .readOnly)
        }
        
        let width = CVPixelBufferGetWidth(depthMap)
        let height = CVPixelBufferGetHeight(depthMap)
        
        guard let depthData = CVPixelBufferGetBaseAddress(depthMap),
              let confidenceData = CVPixelBufferGetBaseAddress(confidenceMap) else {
            return 0.0
        }
        
        let depthPointer = depthData.assumingMemoryBound(to: Float32.self)
        let confidencePointer = confidenceData.assumingMemoryBound(to: UInt8.self)
        
        // 分析中心 20% 區域
        let centerXStart = width / 2 - width / 10
        let centerXEnd = width / 2 + width / 10
        let centerYStart = height / 2 - height / 10
        let centerYEnd = height / 2 + height / 10
        
        var validDistances: [Float32] = []
        
        for y in centerYStart..<centerYEnd {
            for x in centerXStart..<centerXEnd {
                let index = y * width + x
                let depth = depthPointer[index]
                let _ = confidencePointer[index]  // 忽略confidence值，因為我們使用自己的置信度計算
                
                // 放寬置信度要求（從1降到0，允許所有有效深度值）
                if depth > 0.05 && depth < 3.0 && depth != Float.infinity && !depth.isNaN {
                    validDistances.append(depth)
                }
            }
        }
        
        // 計算中位數距離（避免異常值影響）
        let totalPixels = (centerXEnd - centerXStart) * (centerYEnd - centerYStart)
        print("LiDAR深度分析: 中心區域像素總數: \(totalPixels), 有效深度像素: \(validDistances.count)")
        
        if !validDistances.isEmpty {
            validDistances.sort()
            let medianIndex = validDistances.count / 2
            let medianDistance = Double(validDistances[medianIndex])
            
            // 計算置信度（基於有效數據比例）
            let calculatedConfidence = Double(validDistances.count) / Double(totalPixels)
            self.confidence = calculatedConfidence
            
            print("LiDAR深度分析: 中位數距離: \(String(format: "%.3f", medianDistance))m, 置信度: \(String(format: "%.3f", calculatedConfidence))")
            return medianDistance
        }
        
        print("LiDAR深度分析: 沒有找到有效的深度數據")
        return 0.0
    }
    
    private func calculateFinalDistance() {
        guard !distanceReadings.isEmpty else { 
            // 沒有任何有效數據，校正失敗
            Task { @MainActor in
                calibrationStatus = "校正失敗：無法獲取有效深度數據"
                isCalibrating = false
                measuredDistance = nil
                confidence = 0.0
            }
            return 
        }
        
        // 移除異常值（使用四分位距方法）
        let sortedReadings = distanceReadings.sorted()
        let q1Index = sortedReadings.count / 4
        let q3Index = 3 * sortedReadings.count / 4
        let q1 = sortedReadings[q1Index]
        let q3 = sortedReadings[q3Index]
        let iqr = q3 - q1
        let lowerBound = q1 - 1.5 * iqr
        let upperBound = q3 + 1.5 * iqr
        
        let filteredReadings = distanceReadings.filter { $0 >= lowerBound && $0 <= upperBound }
        
        // 檢查校正品質 - 必須有足夠的有效數據和合理的置信度
        if !filteredReadings.isEmpty && filteredReadings.count >= 3 {
            let finalDistance = filteredReadings.reduce(0, +) / Double(filteredReadings.count)
            let finalConfidence = Double(filteredReadings.count) / Double(distanceReadings.count)
            
            // 驗證距離合理性（10cm到1m之間）和置信度要求
            if finalDistance >= 0.1 && finalDistance <= 1.0 && finalConfidence >= 0.5 {
                Task { @MainActor in
                    measuredDistance = finalDistance
                    confidence = finalConfidence
                    calibrationStatus = "✅ 校準成功: \(String(format: "%.2f", finalDistance))m (置信度: \(String(format: "%.1f", finalConfidence * 100))%)"
                    
                    print("🎉 LiDAR 校準成功: 距離=\(String(format: "%.2f", finalDistance))m, 置信度=\(String(format: "%.1f", finalConfidence * 100))%")
                    
                    // 校正成功，延遲後自動停止
                    DispatchQueue.main.asyncAfter(deadline: .now() + 2.0) { [weak self] in
                        self?.stopCalibration()
                    }
                }
            } else {
                // 校正品質不符合要求
                Task { @MainActor in
                    calibrationStatus = "❌ 校正失敗：距離(\(String(format: "%.2f", finalDistance))m)或置信度(\(String(format: "%.1f", finalConfidence * 100))%)不符合要求"
                    measuredDistance = nil
                    confidence = 0.0
                    
                    print("❌ LiDAR 校準失敗: 距離=\(String(format: "%.2f", finalDistance))m, 置信度=\(String(format: "%.1f", finalConfidence * 100))% (不符合品質要求)")
                    
                    // 延遲後自動停止
                    DispatchQueue.main.asyncAfter(deadline: .now() + 2.0) { [weak self] in
                        self?.stopCalibration()
                    }
                }
            }
        } else {
            // 有效數據不足
            Task { @MainActor in
                calibrationStatus = "❌ 校正失敗：有效數據不足 (\(filteredReadings.count)/\(distanceReadings.count))"
                measuredDistance = nil
                confidence = 0.0
                
                print("❌ LiDAR 校準失敗: 有效數據不足")
                
                // 延遲後自動停止
                DispatchQueue.main.asyncAfter(deadline: .now() + 2.0) { [weak self] in
                    self?.stopCalibration()
                }
            }
        }
        
        // 清理計時器
        calibrationTimer?.invalidate()
        calibrationTimer = nil
    }
    
    // 獲取校準後的像素比例
    func getCalibratedPixelScale(imageSize: CGSize) -> Double {
        guard let distance = measuredDistance, distance > 0 else {
            // 如果沒有 LiDAR 數據，使用默認值
            return getDefaultPixelScale(imageSize: imageSize)
        }
        
        // 基於 LiDAR 測量的距離計算像素比例
        let calibratedScale = calculatePixelScaleFromDistance(distance: distance, imageSize: imageSize)
        
        print("LiDAR 校準像素比例: 距離=\(distance)m, 像素比例=\(calibratedScale) cm/pixel")
        
        return calibratedScale
    }
    
    private func calculatePixelScaleFromDistance(distance: Double, imageSize: CGSize) -> Double {
        // 優先使用 ARKit 相機內參 fx（像素）推算：cm/pixel = (距離cm) / fx
        if let fx = lastFxPixels, fx > 0 {
            let cmPerPixel = (distance * 100.0) / fx
            return cmPerPixel
        }
        // 後備：使用保守常數（不精確，只作備援）
        let fallbackFx: Double = 1400.0
        return (distance * 100.0) / fallbackFx
    }
    
    private func getDefaultPixelScale(imageSize: CGSize) -> Double {
        // 默認像素比例（基於經驗值）
        let defaultDistance: Double = 0.3 // 30cm
        return calculatePixelScaleFromDistance(distance: defaultDistance, imageSize: imageSize)
    }
}

// ARSession 錯誤/中斷通知由下方 ARSessionDelegate extension 的
// session(_:didFailWithError:)、sessionWasInterrupted(_:)、sessionInterruptionEnded(_:) 處理

// MARK: - ARSessionDelegate實現
extension LiDARCalibrationModule {
    func session(_ session: ARSession, didUpdate frame: ARFrame) {
        // 只有在校準過程中才處理ARFrame，避免不必要的記憶體使用
        guard isCalibrating else { return }
        
        // 使用自動釋放池確保ARFrame不被保留
        autoreleasepool {
            processARFrame(frame)
        }
    }
    
    private func processARFrame(_ frame: ARFrame) {
        // 確認校準狀態
        guard isCalibrating else {
            print("🛑 LiDAR校準已停止，跳過ARFrame處理")
            return
        }
        
        // 優先使用平滑深度數據，若不可用則回退到原始深度數據
        var depthData: ARDepthData
        if let smoothedDepthData = frame.smoothedSceneDepth {
            depthData = smoothedDepthData
            print("🎯 使用平滑深度數據進行校準")
        } else if let sceneDepthData = frame.sceneDepth {
            depthData = sceneDepthData  
            print("📡 使用原始深度數據進行校準")
        } else {
            print("❌ LiDAR校準: 無法獲取任何深度數據 - 可能設備不支援或權限不足")
            
            // 如果連續無法獲取深度數據，停止校準
            Task { @MainActor in
                calibrationStatus = "錯誤：無法獲取深度數據，請檢查設備支援"
                isCalibrating = false
            }
            return
        }
        
        // 更新相機內參 fx（像素）
        let intrinsics = frame.camera.intrinsics
        lastFxPixels = Double(intrinsics.columns.0.x)
        
        let depthMap = depthData.depthMap
        guard let confidenceMap = depthData.confidenceMap else {
            print("❌ LiDAR校準: 無法獲取置信度地圖")
            return
        }
        
        // 分析中心區域的深度數據
        let centerDistance = analyzeCenterDepth(depthMap: depthMap, confidenceMap: confidenceMap)
        print("📏 LiDAR校準: 測量到中心距離: \(String(format: "%.3f", centerDistance))m")
        
        if centerDistance > 0 {
            distanceReadings.append(centerDistance)
            print("✅ LiDAR校準: 添加有效距離讀數，當前總數: \(distanceReadings.count)/\(maxReadings)")
            
            // 實時更新進度
            Task { @MainActor in
                let progress = Double(distanceReadings.count) / Double(maxReadings) * 100
                calibrationStatus = "採集深度數據中... \(distanceReadings.count)/\(maxReadings) (\(Int(progress))%)"
                isCalibrating = true // 確保狀態始終正確
            }
            
            // 當收集到足夠的數據時，計算平均值
            if distanceReadings.count >= maxReadings {
                print("🎯 LiDAR校準: 收集完成，開始計算最終距離")
                calculateFinalDistance()
            }
        } else {
            print("⚠️ LiDAR校準: 中心距離無效，跳過此次測量")
        }
    }
    
    func session(_ session: ARSession, didFailWithError error: Error) {
        print("❌ ARSession錯誤: \(error.localizedDescription)")
        Task { @MainActor in
            calibrationStatus = "ARSession錯誤: \(error.localizedDescription)"
            isCalibrating = false
        }
    }
    
    func sessionWasInterrupted(_ session: ARSession) {
        print("⚠️ ARSession被中斷")
        Task { @MainActor in
            calibrationStatus = "ARSession被中斷，請重新開始"
            isCalibrating = false
        }
    }
    
    func sessionInterruptionEnded(_ session: ARSession) {
        print("✅ ARSession中斷結束")
        // 可以選擇重新開始校準或等待用戶手動重新開始
    }
}

// MARK: - 校準結果結構
struct LiDARCalibrationResult {
    let distance: Double // 米
    let confidence: Double // 0-1
    let pixelScale: Double // cm/pixel
    let timestamp: Date
}

// MARK: - 通知名稱定義
extension Notification.Name {
    static let lidarCalibrationWillStart = Notification.Name("LiDARCalibrationWillStart")
    static let lidarCalibrationDidStop = Notification.Name("LiDARCalibrationDidStop")
    static let lidarCalibrationDidComplete = Notification.Name("LiDARCalibrationDidComplete")
}