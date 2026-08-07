import Foundation
import UIKit

/// 與後端UWM-Deepskin整合服務的API客戶端
/// 支援雲端雙重模型驗證和LiDAR深度資料整合
class WoundAnalysisAPIService: ObservableObject {
    
    @Published var isConnected: Bool = false
    @Published var lastResponseTime: TimeInterval = 0
    @Published var apiStatus: APIStatus = .unknown
    
    private let baseURL = "http://localhost:5000/api"  // 可設定為遠端伺服器
    private let session: URLSession
    private let encoder = JSONEncoder()
    private let decoder = JSONDecoder()
    
    enum APIStatus {
        case unknown
        case healthy
        case error(String)
        case maintenance
        
        var description: String {
            switch self {
            case .unknown: return "未知"
            case .healthy: return "正常"
            case .error(let message): return "錯誤: \(message)"
            case .maintenance: return "維護中"
            }
        }
    }
    
    init() {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 60.0  // 60秒超時
        config.timeoutIntervalForResource = 120.0  // 2分鐘資源超時
        self.session = URLSession(configuration: config)
        
        Task {
            await checkServerHealth()
        }
    }
    
    // MARK: - 健康檢查
    
    @MainActor
    func checkServerHealth() async {
        do {
            let healthResponse = try await performHealthCheck()
            isConnected = healthResponse.modelsLoaded
            apiStatus = healthResponse.modelsLoaded ? .healthy : .error("模型未載入")
            
        } catch {
            isConnected = false
            apiStatus = .error(error.localizedDescription)
            print("🔴 API健康檢查失敗: \(error)")
        }
    }
    
    private func performHealthCheck() async throws -> HealthResponse {
        guard let url = URL(string: "\(baseURL)/health") else {
            throw APIError.invalidURL
        }
        
        let request = URLRequest(url: url)
        let (data, response) = try await session.data(for: request)
        
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.invalidResponse
        }
        
        guard httpResponse.statusCode == 200 else {
            throw APIError.httpError(httpResponse.statusCode)
        }
        
        return try decoder.decode(HealthResponse.self, from: data)
    }
    
    // MARK: - 主要分析API
    
    /// 使用UWM + Deepskin雙重模型分析傷口
    func analyzeWoundWithDualModels(image: UIImage, depthData: Data? = nil) async throws -> CloudAnalysisResult {
        let startTime = Date()
        
        guard isConnected else {
            throw APIError.serverUnavailable
        }
        
        guard let url = URL(string: "\(baseURL)/analyze_wound") else {
            throw APIError.invalidURL
        }
        
        // 創建多部分表單請求
        let boundary = "Boundary-\(UUID().uuidString)"
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        
        // 構建請求體
        var body = Data()
        
        // 添加圖像
        if let imageData = image.jpegData(compressionQuality: 0.8) {
            body.append("--\(boundary)\r\n".data(using: .utf8)!)
            body.append("Content-Disposition: form-data; name=\"image\"; filename=\"wound.jpg\"\r\n".data(using: .utf8)!)
            body.append("Content-Type: image/jpeg\r\n\r\n".data(using: .utf8)!)
            body.append(imageData)
            body.append("\r\n".data(using: .utf8)!)
        }
        
        // 可選：添加深度資料
        if let depthData = depthData {
            body.append("--\(boundary)\r\n".data(using: .utf8)!)
            body.append("Content-Disposition: form-data; name=\"depth_data\"; filename=\"depth.bin\"\r\n".data(using: .utf8)!)
            body.append("Content-Type: application/octet-stream\r\n\r\n".data(using: .utf8)!)
            body.append(depthData)
            body.append("\r\n".data(using: .utf8)!)
        }
        
        // 添加分析選項
        let analysisOptions = AnalysisOptions(
            includeTissueAnalysis: true,
            includeVolumeCalculation: depthData != nil,
            includePWATScore: true,
            useModelConsensus: true
        )
        
        if let optionsData = try? encoder.encode(analysisOptions),
           let optionsString = String(data: optionsData, encoding: .utf8) {
            body.append("--\(boundary)\r\n".data(using: .utf8)!)
            body.append("Content-Disposition: form-data; name=\"options\"\r\n\r\n".data(using: .utf8)!)
            body.append(optionsString.data(using: .utf8)!)
            body.append("\r\n".data(using: .utf8)!)
        }
        
        body.append("--\(boundary)--\r\n".data(using: .utf8)!)
        request.httpBody = body
        
        // 執行請求
        let (data, response) = try await session.data(for: request)
        
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.invalidResponse
        }
        
        guard httpResponse.statusCode == 200 else {
            let errorMessage = String(data: data, encoding: .utf8) ?? "Unknown error"
            throw APIError.analysisError(errorMessage)
        }
        
        let analysisResponse = try decoder.decode(AnalysisResponse.self, from: data)
        
        // 記錄響應時間
        let responseTime = Date().timeIntervalSince(startTime)
        await MainActor.run {
            lastResponseTime = responseTime
        }
        
        return CloudAnalysisResult(
            analysisId: analysisResponse.analysisId,
            timestamp: analysisResponse.timestamp,
            processingTime: analysisResponse.processingTime,
            uwmResult: analysisResponse.uwmResult,
            deepskinResult: analysisResponse.deepskinResult,
            consensusResult: analysisResponse.consensusResult,
            tissueAnalysis: analysisResponse.tissueAnalysis,
            confidenceScore: analysisResponse.confidenceScore,
            pwatScore: analysisResponse.pwatScore,
            volumeAnalysis: analysisResponse.volumeAnalysis,
            recommendations: analysisResponse.recommendations
        )
    }
    
    /// 獲取模型資訊
    func getModelsInfo() async throws -> ModelsInfoResponse {
        guard let url = URL(string: "\(baseURL)/models_info") else {
            throw APIError.invalidURL
        }
        
        let request = URLRequest(url: url)
        let (data, response) = try await session.data(for: request)
        
        guard let httpResponse = response as? HTTPURLResponse,
              httpResponse.statusCode == 200 else {
            throw APIError.httpError((response as? HTTPURLResponse)?.statusCode ?? -1)
        }
        
        return try decoder.decode(ModelsInfoResponse.self, from: data)
    }
    
    /// 獲取分析歷史
    func getAnalysisHistory() async throws -> HistoryResponse {
        guard let url = URL(string: "\(baseURL)/analysis_history") else {
            throw APIError.invalidURL
        }
        
        let request = URLRequest(url: url)
        let (data, response) = try await session.data(for: request)
        
        guard let httpResponse = response as? HTTPURLResponse,
              httpResponse.statusCode == 200 else {
            throw APIError.httpError((response as? HTTPURLResponse)?.statusCode ?? -1)
        }
        
        return try decoder.decode(HistoryResponse.self, from: data)
    }
    
    // MARK: - LiDAR深度資料整合體積計算
    
    /// 整合LiDAR深度資料進行精確體積計算
    func calculateVolumeWithDepthIntegration(
        segmentationMask: UIImage,
        depthData: Data,
        cameraIntrinsics: CameraIntrinsics,
        pixelScale: Double
    ) async -> VolumeCalculationResult {
        
        // 1. 本地預處理深度資料
        let processedDepthData = preprocessDepthData(depthData, mask: segmentationMask)
        
        // 2. 準備雲端計算請求
        let volumeRequest = VolumeCalculationRequest(
            depthData: processedDepthData,
            maskData: segmentationMask.pngData(),
            cameraIntrinsics: cameraIntrinsics,
            pixelScale: pixelScale,
            calculationMethod: .pixelwiseIntegration
        )
        
        do {
            // 3. 發送到雲端進行高精度體積計算
            let cloudVolumeResult = try await performCloudVolumeCalculation(volumeRequest)
            
            // 4. 本地驗證和後處理
            let validatedResult = validateVolumeResult(cloudVolumeResult, localDepthData: processedDepthData)
            
            return validatedResult
            
        } catch {
            print("⚠️ 雲端體積計算失敗，使用本地算法: \(error)")
            
            // 降級到本地體積計算
            return calculateVolumeLocally(
                depthData: processedDepthData,
                mask: segmentationMask,
                cameraIntrinsics: cameraIntrinsics,
                pixelScale: pixelScale
            )
        }
    }
    
    private func preprocessDepthData(_ depthData: Data, mask: UIImage) -> ProcessedDepthData {
        // 1) 解析深度資料（ARKit: 公尺）
        let rawDepthValues = depthData.withUnsafeBytes { bytes in
            Array(bytes.bindMemory(to: Float32.self))
        }
        let depthWidth = 256
        let depthHeight = 192
        
        // 2) 將遮罩重採樣到深度解析度，避免索引不一致
        let resizedMask = resizeMask(mask, width: depthWidth, height: depthHeight)
        guard let maskCGImage = resizedMask.cgImage else {
            // 仍將深度轉換為公分，確保與像素面積(cm²)一致
            let cmDepthValues = rawDepthValues.map { Float32($0 * 100.0) }
            return ProcessedDepthData(values: cmDepthValues, width: depthWidth, height: depthHeight, filteredCount: 0)
        }
        
        // 3) 萃取灰度遮罩（白色區域為有效）
        let maskData = extractMaskPixels(maskCGImage)
        let minCount = min(maskData.count, rawDepthValues.count)
        
        // 4) 過濾並轉換單位: 將 m → cm
        var filteredDepthValues = [Float32](repeating: 0, count: depthWidth * depthHeight)
        var validCount = 0
        for i in 0..<minCount {
            if maskData[i] > 128 {
                let depthM = rawDepthValues[i]
                if depthM > 0 {
                    filteredDepthValues[i] = depthM * 100.0 // 轉為公分
                    validCount += 1
                }
            }
        }
        
        return ProcessedDepthData(
            values: filteredDepthValues,
            width: depthWidth,
            height: depthHeight,
            filteredCount: validCount
        )
    }

    // 重採樣遮罩到指定解析度
    private func resizeMask(_ image: UIImage, width: Int, height: Int) -> UIImage {
        let size = CGSize(width: width, height: height)
        let renderer = UIGraphicsImageRenderer(size: size)
        return renderer.image { _ in
            UIColor.black.setFill()
            UIBezierPath(rect: CGRect(origin: .zero, size: size)).fill()
            image.draw(in: CGRect(origin: .zero, size: size))
        }
    }
    
    private func extractMaskPixels(_ cgImage: CGImage) -> [UInt8] {
        let width = cgImage.width
        let height = cgImage.height
        let bytesPerPixel = 4
        let bytesPerRow = width * bytesPerPixel
        
        var pixelData = [UInt8](repeating: 0, count: width * height * bytesPerPixel)
        
        let colorSpace = CGColorSpaceCreateDeviceRGB()
        guard let context = CGContext(
            data: &pixelData,
            width: width,
            height: height,
            bitsPerComponent: 8,
            bytesPerRow: bytesPerRow,
            space: colorSpace,
            bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue
        ) else {
            return []
        }
        
        context.draw(cgImage, in: CGRect(x: 0, y: 0, width: width, height: height))
        
        // 提取灰度值（使用R通道）
        var grayPixels: [UInt8] = []
        for i in stride(from: 0, to: pixelData.count, by: 4) {
            grayPixels.append(pixelData[i])  // R通道
        }
        
        return grayPixels
    }
    
    private func performCloudVolumeCalculation(_ request: VolumeCalculationRequest) async throws -> CloudVolumeResult {
        guard let url = URL(string: "\(baseURL)/calculate_volume") else {
            throw APIError.invalidURL
        }
        
        var httpRequest = URLRequest(url: url)
        httpRequest.httpMethod = "POST"
        httpRequest.setValue("application/json", forHTTPHeaderField: "Content-Type")
        
        let requestData = try encoder.encode(request)
        httpRequest.httpBody = requestData
        
        let (data, response) = try await session.data(for: httpRequest)
        
        guard let httpResponse = response as? HTTPURLResponse,
              httpResponse.statusCode == 200 else {
            throw APIError.volumeCalculationFailed
        }
        
        return try decoder.decode(CloudVolumeResult.self, from: data)
    }
    
    private func validateVolumeResult(_ cloudResult: CloudVolumeResult, localDepthData: ProcessedDepthData) -> VolumeCalculationResult {
        // 基本合理性檢查
        let isReasonable = cloudResult.volume > 0 && 
                          cloudResult.volume < 1000000 &&  // 不超過1立方米
                          cloudResult.confidence > 0.5
        
        return VolumeCalculationResult(
            volume: cloudResult.volume,
            surfaceArea: cloudResult.surfaceArea,
            averageDepth: cloudResult.averageDepth,
            maxDepth: cloudResult.maxDepth,
            confidence: cloudResult.confidence,
            depthCoverage: cloudResult.depthCoverage,
            calculationMethod: cloudResult.method,
            isValidated: isReasonable,
            validationNotes: isReasonable ? [] : ["體積數值異常，建議重新測量"]
        )
    }
    
    private func calculateVolumeLocally(
        depthData: ProcessedDepthData,
        mask: UIImage,
        cameraIntrinsics: CameraIntrinsics,
        pixelScale: Double
    ) -> VolumeCalculationResult {
        
        // 簡化的本地體積計算（像素積分法）
        var totalVolume: Double = 0.0
        var validPixelCount = 0
        var totalDepth: Double = 0.0
        var maxDepth: Float32 = 0.0
        
        for (index, depthValue) in depthData.values.enumerated() {
            if depthValue > 0 {
                // 計算像素面積
                // pixelScale 單位: cm/pixel → 面積: cm²
                let pixelArea = pixelScale * pixelScale
                
                // 體積 = 面積 × 深度
                // depthValue 已轉為公分
                let pixelVolume = pixelArea * Double(depthValue)
                totalVolume += pixelVolume
                
                totalDepth += Double(depthValue)
                maxDepth = max(maxDepth, depthValue)
                validPixelCount += 1
            }
        }
        
        let avgDepth = validPixelCount > 0 ? totalDepth / Double(validPixelCount) : 0.0
        
        print("📊 本地體積計算結果:")
        print("  - pixelScale: \(String(format: "%.5f", pixelScale)) cm/pixel")
        print("  - 有效像素數: \(validPixelCount)/\(depthData.values.count)")
        print("  - 總體積: \(String(format: "%.3f", totalVolume)) cm³")
        print("  - 平均深度: \(String(format: "%.2f", avgDepth)) cm")
        print("  - 最大深度: \(String(format: "%.2f", Double(maxDepth))) cm")
        let depthCoverage = Double(validPixelCount) / Double(depthData.values.count)
        
        // 計算表面積（簡化）
        let surfaceArea = Double(validPixelCount) * pixelScale * pixelScale
        
        return VolumeCalculationResult(
            volume: totalVolume,
            surfaceArea: surfaceArea,
            averageDepth: avgDepth,
            maxDepth: Double(maxDepth),
            confidence: min(0.8, depthCoverage),  // 本地計算信心度較低
            depthCoverage: depthCoverage,
            calculationMethod: "local_pixelwise_integration",
            isValidated: depthCoverage > 0.5,
            validationNotes: depthCoverage > 0.5 ? [] : ["深度覆蓋率不足，建議重新拍攝"]
        )
    }
}

// MARK: - 資料結構

struct HealthResponse: Codable {
    let status: String
    let modelsLoaded: Bool
    let timestamp: String
    let service: String
}

struct AnalysisOptions: Codable {
    let includeTissueAnalysis: Bool
    let includeVolumeCalculation: Bool
    let includePWATScore: Bool
    let useModelConsensus: Bool
}

struct AnalysisResponse: Codable {
    let analysisId: String
    let timestamp: String
    let processingTime: Double
    let uwmResult: UWMModelResult?
    let deepskinResult: DeepskinModelResult?
    let consensusResult: ConsensusResult?
    let tissueAnalysis: TissueAnalysisResult?
    let confidenceScore: Double
    let pwatScore: Double?
    let volumeAnalysis: VolumeAnalysisResult?
    let recommendations: [String]
}

struct UWMModelResult: Codable {
    let area: Double
    let confidence: Double
    let model: String
}

struct DeepskinModelResult: Codable {
    let area: Double
    let confidence: Double
    let pwatScore: Double
    let model: String
}

struct ConsensusResult: Codable {
    let area: Double
    let confidence: Double
    let iou: Double
    let areaAgreement: Double
    let fusionMethod: String
    let modelAgreement: Double
}

struct TissueAnalysisResult: Codable {
    let tissueAreas: [String: Double]
    let tissuePercentages: [String: Double]
    let healingStage: String
    let riskScore: Double
    let totalWoundArea: Double
}

struct VolumeAnalysisResult: Codable {
    let volume: Double
    let surfaceArea: Double
    let averageDepth: Double
    let maxDepth: Double
    let confidence: Double
    let method: String
}

struct CloudAnalysisResult {
    let analysisId: String
    let timestamp: String
    let processingTime: Double
    let uwmResult: UWMModelResult?
    let deepskinResult: DeepskinModelResult?
    let consensusResult: ConsensusResult?
    let tissueAnalysis: TissueAnalysisResult?
    let confidenceScore: Double
    let pwatScore: Double?
    let volumeAnalysis: VolumeAnalysisResult?
    let recommendations: [String]
}

struct ModelsInfoResponse: Codable {
    let models: [ModelInfo]
    let integrationFeatures: [String]
}

struct ModelInfo: Codable {
    let name: String
    let description: String
    let architecture: String
    let inputSize: String?
    let accuracy: String?
    let features: [String]?
    let loaded: Bool
}

struct HistoryResponse: Codable {
    let history: [HistoryItem]
    let totalCount: Int
}

struct HistoryItem: Codable {
    let id: String
    let timestamp: String
    let pwatScore: Double?
    let confidenceScore: Double
    let processingTime: Double
}

struct VolumeCalculationRequest: Codable {
    let depthData: Data
    let maskData: Data?
    let cameraIntrinsics: CameraIntrinsics
    let pixelScale: Double
    let calculationMethod: String
}

struct CloudVolumeResult: Codable {
    let volume: Double
    let surfaceArea: Double
    let averageDepth: Double
    let maxDepth: Double
    let confidence: Double
    let depthCoverage: Double
    let method: String
}

struct ProcessedDepthData {
    let values: [Float32]
    let width: Int
    let height: Int
    let filteredCount: Int
}

struct VolumeCalculationResult {
    let volume: Double
    let surfaceArea: Double
    let averageDepth: Double
    let maxDepth: Double
    let confidence: Double
    let depthCoverage: Double
    let calculationMethod: String
    let isValidated: Bool
    let validationNotes: [String]
}

enum APIError: LocalizedError {
    case invalidURL
    case invalidResponse
    case httpError(Int)
    case serverUnavailable
    case analysisError(String)
    case volumeCalculationFailed
    case networkError(Error)
    
    var errorDescription: String? {
        switch self {
        case .invalidURL:
            return "無效的URL"
        case .invalidResponse:
            return "無效的伺服器回應"
        case .httpError(let code):
            return "HTTP錯誤: \(code)"
        case .serverUnavailable:
            return "伺服器不可用"
        case .analysisError(let message):
            return "分析錯誤: \(message)"
        case .volumeCalculationFailed:
            return "體積計算失敗"
        case .networkError(let error):
            return "網路錯誤: \(error.localizedDescription)"
        }
    }
}