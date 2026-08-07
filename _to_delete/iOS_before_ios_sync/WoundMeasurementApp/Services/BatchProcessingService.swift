import Foundation
import UIKit
import SwiftUI
import CoreData
import Combine
import UniformTypeIdentifiers

/// 批量處理服務 - 支持多圖像傷口測量處理
@MainActor
class BatchProcessingService: ObservableObject {
    static let shared = BatchProcessingService()
    
    // MARK: - 公開屬性
    @Published var isProcessing: Bool = false
    @Published var currentProgress: Double = 0.0
    @Published var processedCount: Int = 0
    @Published var totalCount: Int = 0
    @Published var currentImageName: String = ""
    @Published var results: [BatchProcessingResult] = []
    @Published var errors: [BatchProcessingError] = []
    
    // MARK: - 處理狀態
    @Published var processingState: BatchProcessingState = .idle
    
    enum BatchProcessingState {
        case idle
        case preparing
        case processing
        case completed
        case cancelled
        case error(String)
        
        var description: String {
            switch self {
            case .idle: return "待機"
            case .preparing: return "準備中"
            case .processing: return "處理中"
            case .completed: return "完成"
            case .cancelled: return "已取消"
            case .error(let message): return "錯誤: \(message)"
            }
        }
    }
    
    // MARK: - 私有屬性
    private var processingTask: Task<Void, Never>?
    private let performanceMonitor = EnhancedPerformanceMonitor.shared
    private let calibrationModule = CalibrationStickerModule()
    private let imageJCore = ImageJCore()
    private let classificationModule = ClassificationModule()
    
    // 處理設定
    private var batchConfig = BatchProcessingConfig()
    
    private init() {
        setupNotifications()
    }
    
    deinit {
        NotificationCenter.default.removeObserver(self)
    }
    
    // MARK: - 公開方法
    
    /// 開始批量處理
    /// - Parameters:
    ///   - images: 要處理的圖像陣列
    ///   - config: 處理設定
    func startBatchProcessing(images: [BatchImageInput], config: BatchProcessingConfig = BatchProcessingConfig()) async {
        guard !isProcessing else {
            print("⚠️ 批量處理已在進行中")
            return
        }
        
        print("🚀 開始批量處理 \(images.count) 張圖像")
        
        // 重置狀態
        resetState()
        self.batchConfig = config
        self.totalCount = images.count
        self.isProcessing = true
        self.processingState = .preparing
        
        // 性能監控
        performanceMonitor.startOperation("Batch-Processing-\(images.count)")
        
        // 開始處理任務
        processingTask = Task {
            await processBatchInternal(images: images, config: config)
        }
    }
    
    /// 取消批量處理
    func cancelProcessing() {
        guard isProcessing else { return }
        
        print("⏹️ 使用者取消批量處理")
        processingTask?.cancel()
        processingState = .cancelled
        isProcessing = false
        
        performanceMonitor.endOperation("Batch-Processing-\(totalCount)")
    }
    
    /// 重置處理狀態
    func resetState() {
        currentProgress = 0.0
        processedCount = 0
        totalCount = 0
        currentImageName = ""
        results.removeAll()
        errors.removeAll()
        processingState = .idle
    }
    
    /// 匯出處理結果
    func exportResults() -> BatchProcessingReport {
        return BatchProcessingReport(
            timestamp: Date(),
            totalImages: totalCount,
            successfulProcessing: results.count,
            failedProcessing: errors.count,
            results: results,
            errors: errors,
            processingDuration: performanceMonitor.lastOperationTime,
            averageProcessingTime: results.isEmpty ? 0 : results.map { $0.processingTime }.reduce(0, +) / Double(results.count)
        )
    }
    
    // MARK: - 私有方法
    
    private func setupNotifications() {
        NotificationCenter.default.addObserver(
            self,
            selector: #selector(handleMemoryWarning),
            name: UIApplication.didReceiveMemoryWarningNotification,
            object: nil
        )
    }
    
    @objc private func handleMemoryWarning() {
        print("⚠️ 批量處理收到記憶體警告")
        if isProcessing && batchConfig.pauseOnMemoryWarning {
            print("⏸️ 因記憶體警告暫停批量處理")
            processingTask?.cancel()
        }
    }
    
    /// 內部批量處理邏輯
    private func processBatchInternal(images: [BatchImageInput], config: BatchProcessingConfig) async {
        processingState = .processing
        
        for (index, imageInput) in images.enumerated() {
            // 檢查是否被取消
            if Task.isCancelled {
                processingState = .cancelled
                isProcessing = false
                return
            }
            
            currentImageName = imageInput.name
            currentProgress = Double(index) / Double(images.count)
            
            print("📸 處理圖像 \(index + 1)/\(images.count): \(imageInput.name)")
            
            do {
                let result = try await processSingleImage(imageInput, config: config)
                results.append(result)
                processedCount = results.count
                
                print("✅ 圖像處理成功: \(imageInput.name)")
                
                // 可選的處理間隔延遲
                if config.delayBetweenImages > 0 {
                    try await Task.sleep(nanoseconds: UInt64(config.delayBetweenImages * 1_000_000_000))
                }
                
            } catch {
                let batchError = BatchProcessingError(
                    imageName: imageInput.name,
                    error: error,
                    timestamp: Date()
                )
                errors.append(batchError)
                
                print("❌ 圖像處理失敗: \(imageInput.name) - \(error.localizedDescription)")
                
                // 根據設定決定是否繼續處理
                if !config.continueOnError {
                    processingState = .error(error.localizedDescription)
                    isProcessing = false
                    performanceMonitor.recordError(error, in: "Batch-Processing")
                    return
                }
            }
        }
        
        // 處理完成
        currentProgress = 1.0
        processingState = .completed
        isProcessing = false
        
        performanceMonitor.endOperation("Batch-Processing-\(totalCount)")
        
        print("🎉 批量處理完成 - 成功: \(results.count), 失敗: \(errors.count)")
        
        // 發送完成通知
        NotificationCenter.default.post(
            name: .batchProcessingCompleted,
            object: exportResults()
        )
    }
    
    /// 處理單一圖像
    private func processSingleImage(_ imageInput: BatchImageInput, config: BatchProcessingConfig) async throws -> BatchProcessingResult {
        let startTime = Date()
        performanceMonitor.startOperation("Single-Image-\(imageInput.name)")
        
        defer {
            performanceMonitor.endOperation("Single-Image-\(imageInput.name)")
        }
        
        // 1. 校正貼紙檢測
        var calibrationResult: StickerCalibrationResult?
        if config.enableCalibration {
            do {
                calibrationResult = try await calibrationModule.detectCalibrationSticker(from: imageInput.image)
                print("📏 校正貼紙檢測成功: \(imageInput.name)")
            } catch {
                if config.requireCalibration {
                    throw BatchProcessingError.CalibrationError.calibrationRequired
                }
                print("⚠️ 校正貼紙檢測失敗，繼續處理: \(error.localizedDescription)")
            }
        }
        
        // 2. 傷口區域檢測和測量
        let measurementResult = try await performWoundMeasurement(
            image: imageInput.image,
            calibration: calibrationResult,
            config: config
        )
        
        // 3. 可選的分類
        var classificationResult: DetailedWoundClassification?
        if config.enableClassification {
            // 簡化：暫不在批量流程執行分類（避免依賴 ProcessedImage 類型），保留欄位為 nil
            classificationResult = nil
            print("ℹ️ 批量處理暫略過分類: \(imageInput.name)")
        }
        
        // 4. 可選的數據保存
        var savedRecord: WoundRecord?
        if config.saveToDatabase {
            savedRecord = try await saveToDatabase(
                imageInput: imageInput,
                calibration: calibrationResult,
                measurement: measurementResult,
                classification: classificationResult
            )
        }
        
        let processingTime = Date().timeIntervalSince(startTime)
        
        return BatchProcessingResult(
            imageName: imageInput.name,
            originalImage: imageInput.image,
            calibrationResult: calibrationResult,
            measurementResult: measurementResult,
            classificationResult: classificationResult,
            savedRecord: savedRecord,
            processingTime: processingTime,
            timestamp: Date()
        )
    }
    
    /// 執行傷口測量
    private func performWoundMeasurement(
        image: UIImage,
        calibration: StickerCalibrationResult?,
        config: BatchProcessingConfig
    ) async throws -> ModelsWoundMeasurementResult {
        // 實際量測流程：PreProcessing → QA → ImageJCore
        let pixelsPerMM = calibration?.pixelsPerMM ?? config.defaultPixelsPerMM

        // 1) 預處理（批次處理沒有 AR 深度，傳空 Data）
        let preprocessed = try await PreProcessingModule().processImage(image, depthData: Data())

        // 2) 品質檢查
        let qaResult = try await QAFilterModule().evaluateQuality(preprocessed)
        guard qaResult.isValid else {
            throw QAFilterError.insufficientQuality
        }

        // 3) ImageJCore 測量，套用像素比例（若有校正）
        let measurement = try await imageJCore.measureWound(
            preprocessed,
            calibrationPixelsPerMM: pixelsPerMM
        )

        return ModelsWoundMeasurementResult(
            woundArea: measurement.area,
            woundPerimeter: measurement.perimeter,
            pixelsPerMM: pixelsPerMM,
            confidence: qaResult.qualityScore,
            roiRect: preprocessed.roi,
            processedImage: preprocessed.image
        )
    }
    
    /// 保存到數據庫
    private func saveToDatabase(
        imageInput: BatchImageInput,
        calibration: StickerCalibrationResult?,
        measurement: ModelsWoundMeasurementResult,
        classification: DetailedWoundClassification?
    ) async throws -> WoundRecord {
        // 實現數據庫保存邏輯
        // 這裡應該使用Core Data保存記錄
        
        // 暫時返回模擬的WoundRecord
        // 實際實現中應該創建真實的Core Data實體
        return WoundRecord() // 需要在Core Data模型中實現
    }
}

// MARK: - Debug 專用批次驗證工具服務
@MainActor
class BatchValidationService: ObservableObject {
    static let shared = BatchValidationService()
    
    @Published var isProcessing = false
    @Published var progress: Double = 0
    @Published var results: [BatchValidationResult] = []
    @Published var errors: [BatchValidationError] = []
    
    private let pre = PreProcessingModule()
    private let roi = SmartROIModule()
    private let imageJ = ImageJCore()
    
    struct BatchValidationResult: Identifiable {
        let id = UUID()
        let filename: String
        let isSticker: Bool
        let roiPixels: Int
        let areaCm2: Double?
        let perimeterCm: Double?
        let segmentationMethod: String
        let quality: String
        let usedFallback: Bool
        let processingMs: Int
        let errorPercentVsCloud: Double?
        let flagged: Bool
        let comparisonImageURL: URL?
    }
    
    struct BatchValidationError: Identifiable {
        let id = UUID()
        let filename: String
        let message: String
    }
    
    func pickFolder(from viewController: UIViewController, completion: @escaping (URL?) -> Void) {
        let picker = UIDocumentPickerViewController(forOpeningContentTypes: [UTType.folder], asCopy: false)
        picker.allowsMultipleSelection = false
        picker.delegate = FolderPickerDelegate { url in
            completion(url)
        }
        viewController.present(picker, animated: true)
    }
    
    func runValidation(datasetFolder: URL, stickerFolder: URL?, loadCloudComparisons: Bool = false) async {
        isProcessing = true
        progress = 0
        results.removeAll()
        errors.removeAll()
        
        let fm = FileManager.default
        guard let enumerator = fm.enumerator(at: datasetFolder, includingPropertiesForKeys: nil) else {
            isProcessing = false
            return
        }
        let allFiles = (enumerator.allObjects as? [URL])?.filter { ["jpg","jpeg","png"].contains($0.pathExtension.lowercased()) } ?? []
        let total = max(1, allFiles.count)
        
        let docs = try? fm.url(for: .documentDirectory, in: .userDomainMask, appropriateFor: nil, create: true)
        let outputDir = docs?.appendingPathComponent("BatchValidation_Outputs", isDirectory: true)
        if let outputDir, !(fm.fileExists(atPath: outputDir.path)) {
            try? fm.createDirectory(at: outputDir, withIntermediateDirectories: true)
        }
        
        let startAll = Date()
        for (idx, fileURL) in allFiles.enumerated() {
            let start = Date()
            do {
                guard let uiImage = UIImage(contentsOfFile: fileURL.path) else { throw NSError(domain: "Batch", code: -1) }
                let depth = Data()
                let processed = try await pre.processImage(uiImage, depthData: depth)
                let _ = try? await roi.detectWoundROI(from: processed.image, depthData: processed.depthData)
                let qa = try await QAFilterModule().evaluateQuality(processed)
                guard qa.isValid else { throw NSError(domain: "Batch", code: -2, userInfo: [NSLocalizedDescriptionKey:"品質不佳"]) }
                
                // 偵測是否含貼紙（簡化：以檔名或路徑判斷）
                let isSticker = fileURL.lastPathComponent.contains("sticker") || (stickerFolder != nil && fileURL.path.contains(stickerFolder!.lastPathComponent))
                if isSticker {
                    // 估算像素/毫米（若已有貼紙偵測模組，可置換）
                    imageJ.measurementEngine.updatePixelScale(10.0) // 預設 10 px/mm 暫時值
                }
                
                // 分割與量測
                let segmented = try await SegmentationEngine().segment(processed.image, cmPerPixel: nil)
                let roiPixels = segmented.contours.max(by: { $0.area < $1.area })?.area ?? 0
                let measurement = try await imageJ.measureWound(processed)
                let areaCm2 = measurement.area
                let perimeterCm = measurement.perimeter
                
                // 合成對照圖
                var comparisonURL: URL? = nil
                if let outputDir {
                    let comp = try await self.drawComparison(base: processed.image, contours: segmented.contours)
                    let out = outputDir.appendingPathComponent(fileURL.deletingPathExtension().lastPathComponent + "_cmp.jpg")
                    try comp?.jpegData(compressionQuality: 0.9)?.write(to: out)
                    comparisonURL = out
                }
                
                // 雲端對照（如有）
                var errPct: Double? = nil
                var flagged = false
                if loadCloudComparisons {
                    let area = areaCm2
                    if let cloudArea = self.loadCloudArea(for: fileURL) {
                        errPct = abs(area - cloudArea) / max(1e-6, cloudArea) * 100.0
                        flagged = (errPct ?? 0) > 15.0
                    }
                }
                
                let elapsed = Int(Date().timeIntervalSince(start) * 1000)
                let item = BatchValidationResult(
                    filename: fileURL.lastPathComponent,
                    isSticker: isSticker,
                    roiPixels: Int(roiPixels),
                    areaCm2: areaCm2,
                    perimeterCm: perimeterCm,
                    segmentationMethod: "Color+Contours",
                    quality: qa.isValid ? "OK" : (qa.failureReason ?? "Unknown"),
                    usedFallback: imageJ.lastCalibrationSource == .fallback,
                    processingMs: elapsed,
                    errorPercentVsCloud: errPct,
                    flagged: flagged,
                    comparisonImageURL: comparisonURL
                )
                results.append(item)
            } catch {
                errors.append(BatchValidationError(filename: fileURL.lastPathComponent, message: error.localizedDescription))
            }
            progress = Double(idx + 1) / Double(total)
        }
        isProcessing = false
        // 輸出 CSV
        if let outputDir {
            let csv = makeCSV()
            let csvURL = outputDir.appendingPathComponent("report_\(Int(startAll.timeIntervalSince1970)).csv")
            try? csv.data(using: .utf8)?.write(to: csvURL)
        }
    }
    
    private func drawComparison(base: UIImage, contours: [WoundContour]) async throws -> UIImage? {
        let size = base.size
        return UIGraphicsImageRenderer(size: size).image { ctx in
            base.draw(in: CGRect(origin: .zero, size: size))
            ctx.cgContext.setStrokeColor(UIColor.red.cgColor)
            ctx.cgContext.setLineWidth(2)
            for contour in contours {
                guard !contour.points.isEmpty else { continue }
                let path = UIBezierPath()
                let first = contour.points[0]
                path.move(to: CGPoint(x: first.x * size.width, y: first.y * size.height))
                for p in contour.points.dropFirst() {
                    path.addLine(to: CGPoint(x: p.x * size.width, y: p.y * size.height))
                }
                path.close()
                ctx.cgContext.addPath(path.cgPath)
                ctx.cgContext.strokePath()
            }
        }
    }
    
    private func loadCloudArea(for file: URL) -> Double? {
        // 嘗試同名 .json 內的 areaCm2
        let candidate = file.deletingPathExtension().appendingPathExtension("json")
        guard let data = try? Data(contentsOf: candidate) else { return nil }
        if let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any], let val = obj["areaCm2"] as? Double {
            return val
        }
        return nil
    }
    
    private func makeCSV() -> String {
        var lines = ["filename,is_sticker,roi_pixels,area_cm2,perimeter_cm,seg_method,quality,used_fallback,ms,cloud_err_pct,flagged"]
        for r in results {
            let row = [
                r.filename,
                r.isSticker ? "1" : "0",
                "\(r.roiPixels)",
                r.areaCm2 != nil ? String(format: "%.4f", r.areaCm2!) : "",
                r.perimeterCm != nil ? String(format: "%.4f", r.perimeterCm!) : "",
                r.segmentationMethod,
                r.quality,
                r.usedFallback ? "1" : "0",
                "\(r.processingMs)",
                r.errorPercentVsCloud != nil ? String(format: "%.2f", r.errorPercentVsCloud!) : "",
                r.flagged ? "1" : "0"
            ].joined(separator: ",")
            lines.append(row)
        }
        return lines.joined(separator: "\n")
    }
}

private final class FolderPickerDelegate: NSObject, UIDocumentPickerDelegate {
    private let completion: (URL?) -> Void
    init(completion: @escaping (URL?) -> Void) { self.completion = completion }
    func documentPicker(_ controller: UIDocumentPickerViewController, didPickDocumentsAt urls: [URL]) {
        completion(urls.first)
    }
    func documentPickerWasCancelled(_ controller: UIDocumentPickerViewController) {
        completion(nil)
    }
}
// MARK: - 批量處理配置
struct BatchProcessingConfig {
    var enableCalibration: Bool = true
    var requireCalibration: Bool = false
    var enableClassification: Bool = true
    var saveToDatabase: Bool = true
    var continueOnError: Bool = true
    var pauseOnMemoryWarning: Bool = true
    var delayBetweenImages: TimeInterval = 0.1  // 秒
    var defaultPixelsPerMM: Double = 10.0
    var maxConcurrentProcessing: Int = 1  // 目前不支持並行處理
}

// MARK: - 批量處理輸入
struct BatchImageInput {
    let name: String
    let image: UIImage
    let metadata: [String: Any]?
    
    init(name: String, image: UIImage, metadata: [String: Any]? = nil) {
        self.name = name
        self.image = image
        self.metadata = metadata
    }
}

// MARK: - 批量處理結果
struct BatchProcessingResult {
    let imageName: String
    let originalImage: UIImage
    let calibrationResult: StickerCalibrationResult?
    let measurementResult: ModelsWoundMeasurementResult
    let classificationResult: DetailedWoundClassification?
    let savedRecord: WoundRecord?
    let processingTime: TimeInterval
    let timestamp: Date
}

// MARK: - 傷口測量結果
// 與 Models/WoundTypes.swift 中名稱衝突，改為別名結構
struct ModelsWoundMeasurementResult {
    let woundArea: Double      // cm²
    let woundPerimeter: Double // cm
    let pixelsPerMM: Double
    let confidence: Double
    let roiRect: CGRect
    let processedImage: UIImage
}

// MARK: - 批量處理錯誤
struct BatchProcessingError {
    let imageName: String
    let error: Error
    let timestamp: Date
    
    enum CalibrationError: Error, LocalizedError {
        case calibrationRequired
        case calibrationFailed
        
        var errorDescription: String? {
            switch self {
            case .calibrationRequired:
                return "需要校正貼紙但檢測失敗"
            case .calibrationFailed:
                return "校正貼紙檢測失敗"
            }
        }
    }
}

// MARK: - 批量處理報告
struct BatchProcessingReport {
    let timestamp: Date
    let totalImages: Int
    let successfulProcessing: Int
    let failedProcessing: Int
    let results: [BatchProcessingResult]
    let errors: [BatchProcessingError]
    let processingDuration: Double
    let averageProcessingTime: Double
    
    var successRate: Double {
        guard totalImages > 0 else { return 0.0 }
        return Double(successfulProcessing) / Double(totalImages)
    }
    
    var summary: String {
        return """
        批量處理報告
        ==============
        處理時間: \(DateFormatter.localizedString(from: timestamp, dateStyle: .medium, timeStyle: .short))
        總圖像數: \(totalImages)
        成功處理: \(successfulProcessing)
        處理失敗: \(failedProcessing)
        成功率: \(String(format: "%.1f", successRate * 100))%
        總耗時: \(String(format: "%.2f", processingDuration))秒
        平均處理時間: \(String(format: "%.2f", averageProcessingTime))秒/圖像
        """
    }
}

// MARK: - 通知擴展
extension Notification.Name {
    static let batchProcessingCompleted = Notification.Name("BatchProcessingCompleted")
    static let batchProcessingProgress = Notification.Name("BatchProcessingProgress")
}