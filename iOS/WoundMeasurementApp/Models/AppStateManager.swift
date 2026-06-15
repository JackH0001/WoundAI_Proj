import SwiftUI
import Foundation

// MARK: - 應用狀態管理
enum CalibrationState {
    case notStarted
    case initializing
    case selectingImage
    case processingImage
    case detectingSticker
    case stickerDetected(StickerCalibrationResult)
    case stickerDetectionFailed(String)
    case calibrationComplete
    case error(String)
    
    var displayMessage: String {
        switch self {
        case .notStarted:
            return "點擊開始校正以初始化系統"
        case .initializing:
            return "正在初始化校正模組..."
        case .selectingImage:
            return "請選擇包含校正貼紙的照片"
        case .processingImage:
            return "正在處理圖像..."
        case .detectingSticker:
            return "正在檢測校正貼紙..."
        case .stickerDetected(let result):
            // 將 pixels/mm 轉為 cm/pixel = 1 / (pixelsPerMM * 10)
            let cmPerPixel = 1.0 / max(1e-6, result.pixelsPerMM * 10.0)
            return "檢測到校正貼紙，cm/pixel: \(String(format: "%.4f", cmPerPixel))"
        case .stickerDetectionFailed(let error):
            return "貼紙檢測失敗: \(error)"
        case .calibrationComplete:
            return "校正完成！可以開始傷口測量"
        case .error(let message):
            return "錯誤: \(message)"
        }
    }
    
    var isProcessing: Bool {
        switch self {
        case .initializing, .processingImage, .detectingSticker:
            return true
        default:
            return false
        }
    }
    
    var canSelectImage: Bool {
        switch self {
        case .notStarted, .selectingImage, .stickerDetectionFailed, .error:
            return true
        default:
            return false
        }
    }
    
    var canProceedToMeasurement: Bool {
        switch self {
        case .calibrationComplete:
            return true
        default:
            return false
        }
    }
}

enum MeasurementState {
    case notStarted
    case initializing
    case cameraReady
    case capturing
    case processingImage
    case segmenting
    case calculating
    case completed(MeasurementResult)
    case error(String)
    
    var displayMessage: String {
        switch self {
        case .notStarted:
            return "點擊開始測量以初始化系統"
        case .initializing:
            return "正在初始化測量模組..."
        case .cameraReady:
            return "相機已準備就緒，點擊拍攝"
        case .capturing:
            return "正在拍攝..."
        case .processingImage:
            return "正在處理圖像..."
        case .segmenting:
            return "正在進行傷口分割..."
        case .calculating:
            return "正在計算面積和參數..."
        case .completed(let result):
            return "測量完成！面積: \(String(format: "%.2f", result.areaInCm2)) cm²"
        case .error(let message):
            return "測量錯誤: \(message)"
        }
    }
    
    var isProcessing: Bool {
        switch self {
        case .initializing, .capturing, .processingImage, .segmenting, .calculating:
            return true
        default:
            return false
        }
    }
    
    var canCapture: Bool {
        switch self {
        case .cameraReady:
            return true
        default:
            return false
        }
    }
    
    var canStartNewMeasurement: Bool {
        switch self {
        case .notStarted, .cameraReady, .completed, .error:
            return true
        default:
            return false
        }
    }
}

// MARK: - 測量結果結構
struct MeasurementResult {
    let timestamp: Date
    let areaInCm2: Double
    let areaInPixels: Double
    let volumeInCm3: Double?
    let perimeter: Double
    let cmPerPixel: Double
    let confidence: Double
    let processingTime: TimeInterval
    let imageMetadata: ImageMetadata
    let medicalDisclaimer: String
    
    static let defaultDisclaimer = "此測量結果僅供參考，不可用作醫療診斷依據。如需專業醫療建議，請諮詢合格醫療人員。"
    
    struct ImageMetadata {
        let imageSize: CGSize
        let appVersion: String
        let deviceModel: String
        let calibrationSource: String // "sticker", "lidar", "estimated"
        let cameraSettings: CameraSettings?
        
        struct CameraSettings {
            let iso: Double?
            let shutterSpeed: Double?
            let aperture: Double?
            let focalLength: Double?
            let whiteBalance: String?
        }
    }
}

// MARK: - 狀態管理器
@MainActor
class AppStateManager: ObservableObject {
    @Published var calibrationState: CalibrationState = .notStarted
    @Published var measurementState: MeasurementState = .notStarted
    @Published var currentCalibrationResult: StickerCalibrationResult?
    @Published var measurementHistory: [MeasurementResult] = []
    
    // 醫療合規性追蹤
    private let deviceInfo = DeviceInfoCollector()
    private let appVersion = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "Unknown"
    
    // MARK: - 校正狀態轉換
    func startCalibration() {
        let canStart: Bool = {
            switch calibrationState {
            case .notStarted:
                return true
            default:
                return calibrationState.canSelectImage
            }
        }()
        guard canStart else { return }
        calibrationState = .initializing
    }
    
    func calibrationInitialized() {
        guard case .initializing = calibrationState else { return }
        calibrationState = .selectingImage
    }
    
    func imageSelected() {
        guard calibrationState.canSelectImage else { return }
        calibrationState = .processingImage
    }
    
    func startStickerDetection() {
        guard case .processingImage = calibrationState else { return }
        calibrationState = .detectingSticker
    }
    
    func stickerDetected(_ result: StickerCalibrationResult) {
        guard case .detectingSticker = calibrationState else { return }
        currentCalibrationResult = result
        calibrationState = .stickerDetected(result)
        
        // 自動完成校正
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) {
            self.completeCalibration()
        }
    }
    
    func stickerDetectionFailed(_ error: String) {
        guard case .detectingSticker = calibrationState else { return }
        calibrationState = .stickerDetectionFailed(error)
    }
    
    func completeCalibration() {
        guard case .stickerDetected = calibrationState else { return }
        calibrationState = .calibrationComplete
    }
    
    func resetCalibration() {
        calibrationState = .notStarted
        currentCalibrationResult = nil
    }
    
    // MARK: - 測量狀態轉換
    func startMeasurement() {
        guard measurementState.canStartNewMeasurement && calibrationState.canProceedToMeasurement else { return }
        measurementState = .initializing
    }
    
    func measurementInitialized() {
        guard case .initializing = measurementState else { return }
        measurementState = .cameraReady
    }
    
    func startCapture() {
        guard measurementState.canCapture else { return }
        measurementState = .capturing
    }
    
    func imageProcessingStarted() {
        guard case .capturing = measurementState else { return }
        measurementState = .processingImage
    }
    
    func segmentationStarted() {
        guard case .processingImage = measurementState else { return }
        measurementState = .segmenting
    }
    
    func calculationStarted() {
        guard case .segmenting = measurementState else { return }
        measurementState = .calculating
    }
    
    func measurementCompleted(_ result: MeasurementResult) {
        guard case .calculating = measurementState else { return }
        measurementHistory.append(result)
        measurementState = .completed(result)
    }
    
    func measurementFailed(_ error: String) {
        measurementState = .error(error)
    }
    
    func resetMeasurement() {
        if currentCalibrationResult != nil {
            measurementState = .cameraReady
        } else {
            measurementState = .notStarted
        }
    }
    
    // MARK: - 醫療合規性輔助方法
    func createMeasurementResult(areaInCm2: Double, areaInPixels: Double, volumeInCm3: Double?, 
                                perimeter: Double, confidence: Double, processingTime: TimeInterval) -> MeasurementResult {
        // 從貼紙結果的 pixelsPerMM 推導 cm/pixel
        let cmPerPixel: Double = {
            if let r = currentCalibrationResult { return 1.0 / max(1e-6, r.pixelsPerMM * 10.0) }
            return 0.0
        }()
        let calibrationSource = "sticker"
        
        let metadata = MeasurementResult.ImageMetadata(
            imageSize: .zero,
            appVersion: appVersion,
            deviceModel: deviceInfo.deviceModel,
            calibrationSource: calibrationSource,
            cameraSettings: nil // 可根據需要實作
        )
        
        return MeasurementResult(
            timestamp: Date(),
            areaInCm2: areaInCm2,
            areaInPixels: areaInPixels,
            volumeInCm3: volumeInCm3,
            perimeter: perimeter,
            cmPerPixel: cmPerPixel,
            confidence: confidence,
            processingTime: processingTime,
            imageMetadata: metadata,
            medicalDisclaimer: MeasurementResult.defaultDisclaimer
        )
    }
}

// MARK: - 設備資訊收集器
private class DeviceInfoCollector {
    let deviceModel: String
    
    init() {
        var systemInfo = utsname()
        uname(&systemInfo)
        let machineMirror = Mirror(reflecting: systemInfo.machine)
        let identifier = machineMirror.children.reduce("") { identifier, element in
            guard let value = element.value as? Int8, value != 0 else { return identifier }
            return identifier + String(UnicodeScalar(UInt8(value)))
        }
        self.deviceModel = identifier.isEmpty ? "Unknown" : identifier
    }
}