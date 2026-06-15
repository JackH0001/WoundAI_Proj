import Foundation
import SwiftUI

// MARK: - 視圖支持類型定義

/// 歷史測量記錄
struct HistoricalMeasurement: Identifiable, Codable {
    let id = UUID()
    let date: Date
    let area: Double? // cm²
    let volume: Double? // cm³
    let perimeter: Double? // cm
    let maxDepth: Double? // cm
    let classification: String
    let confidence: Double
    let notes: String?
    let imageURL: String?
    
    init(date: Date = Date(), area: Double? = nil, volume: Double? = nil, perimeter: Double? = nil,
         maxDepth: Double? = nil, classification: String = "未知", confidence: Double = 0.0,
         notes: String? = nil, imageURL: String? = nil) {
        self.date = date
        self.area = area
        self.volume = volume
        self.perimeter = perimeter
        self.maxDepth = maxDepth
        self.classification = classification
        self.confidence = confidence
        self.notes = notes
        self.imageURL = imageURL
    }
}

/// 校準結果
struct CalibrationResult: Identifiable {
    let id = UUID()
    let method: String
    let pixelsPerMM: Double
    let confidence: Double
    let timestamp: Date
    let referenceSize: Double? // 參考物件大小 (mm)
    let notes: String?
    
    init(method: String, pixelsPerMM: Double, confidence: Double = 1.0, 
         timestamp: Date = Date(), referenceSize: Double? = nil, notes: String? = nil) {
        self.method = method
        self.pixelsPerMM = pixelsPerMM
        self.confidence = confidence
        self.timestamp = timestamp
        self.referenceSize = referenceSize
        self.notes = notes
    }
}

/// 雲端分析結果
struct CloudAnalysisResult: Codable {
    let analysisId: String
    let timestamp: Date
    let qualityScore: Double
    let bjwatScore: Int?
    let revpwatScore: Int?
    let tissueAnalysis: TissueAnalysis?
    let recommendations: [String]
    let confidence: Double
    
    struct TissueAnalysis: Codable {
        let granulationPercentage: Double
        let necroticPercentage: Double
        let epithelialPercentage: Double
        let healthyPercentage: Double
    }
}

/// 標註數據
struct AnnotationData: Identifiable, Codable {
    let id = UUID()
    let timestamp: Date
    let imageData: Data?
    let annotations: [Annotation]
    let measurements: MeasurementAnnotations
    let notes: String?
    
    struct Annotation: Codable {
        let type: AnnotationType
        let points: [CGPoint]
        let label: String?
        let confidence: Double?
        
        enum AnnotationType: String, CaseIterable, Codable {
            case wound = "傷口邊界"
            case healthy = "健康組織"
            case necrotic = "壞死組織"
            case granulation = "肉芽組織"
            case measurement = "測量線"
            case reference = "參考物件"
        }
    }
    
    struct MeasurementAnnotations: Codable {
        let area: Double?
        let perimeter: Double?
        let maxLength: Double?
        let maxWidth: Double?
        let depth: Double?
        let pixelsPerMM: Double?
    }
}

/// 趨勢分析數據
struct TrendAnalysis {
    let timeRange: TimeInterval
    let measurements: [HistoricalMeasurement]
    let trend: TrendDirection
    let changeRate: Double // 變化率 (% per day)
    let significance: TrendSignificance
    
    enum TrendDirection: String, CaseIterable {
        case improving = "改善中"
        case stable = "穩定"
        case worsening = "惡化中"
        case insufficient = "數據不足"
    }
    
    enum TrendSignificance: String, CaseIterable {
        case significant = "顯著"
        case moderate = "中等"
        case minimal = "輕微"
        case none = "無"
    }
    
    var description: String {
        switch trend {
        case .improving:
            return "傷口正在康復，面積縮小 \(String(format: "%.1f", abs(changeRate)))% 每天"
        case .stable:
            return "傷口狀態穩定，無明顯變化"
        case .worsening:
            return "傷口擴大，面積增加 \(String(format: "%.1f", changeRate))% 每天"
        case .insufficient:
            return "需要更多數據點進行趨勢分析"
        }
    }
}

/// 3D 視覺化數據
struct Wound3DData {
    let depthMap: [[Double]]
    let textureImage: UIImage?
    let meshVertices: [SIMD3<Float>]
    let meshIndices: [UInt32]
    let boundingBox: BoundingBox3D
    let volume: Double
    let surfaceArea: Double
    
    struct BoundingBox3D {
        let min: SIMD3<Float>
        let max: SIMD3<Float>
        
        var center: SIMD3<Float> {
            return (min + max) / 2
        }
        
        var size: SIMD3<Float> {
            return max - min
        }
    }
}

/// 雲端認證狀態
enum CloudAuthenticationStatus {
    case notAuthenticated
    case authenticating
    case authenticated(userId: String)
    case failed(Error)
}

/// 上傳進度
struct UploadProgress {
    let bytesUploaded: Int64
    let totalBytes: Int64
    let percentage: Double
    let estimatedTimeRemaining: TimeInterval?
    
    init(bytesUploaded: Int64, totalBytes: Int64) {
        self.bytesUploaded = bytesUploaded
        self.totalBytes = totalBytes
        self.percentage = totalBytes > 0 ? Double(bytesUploaded) / Double(totalBytes) * 100.0 : 0.0
        self.estimatedTimeRemaining = nil // 簡化實現
    }
}