import Foundation
import UIKit
import CoreGraphics

// MARK: - 傷口標註模型

/// 傷口標註結構
struct WoundAnnotation {
    let id: UUID
    let timestamp: Date
    let imageData: Data
    var annotations: [AnnotationItem]
    var metadata: AnnotationMetadata
}

/// 標註項目
struct AnnotationItem {
    let id: UUID
    let type: AnnotationType
    let region: AnnotationRegion
    let attributes: [String: Any]
    let confidence: Double
}

/// 標註類型
enum AnnotationType: String, CaseIterable {
    case necrosis = "壞死組織"
    case slough = "腐肉"
    case granulation = "肉芽組織"
    case epithelialization = "再上皮化"
    case exudate = "分泌物"
    case woundBoundary = "傷口邊界"
    case periSkin = "周邊皮膚"
    
    var color: UIColor {
        switch self {
        case .necrosis: return UIColor(red: 0.8, green: 0.6, blue: 0.2, alpha: 0.8)
        case .slough: return UIColor(red: 0.9, green: 0.7, blue: 0.3, alpha: 0.8)
        case .granulation: return UIColor(red: 0.2, green: 0.8, blue: 0.2, alpha: 0.8)
        case .epithelialization: return UIColor(red: 0.2, green: 0.6, blue: 0.9, alpha: 0.8)
        case .exudate: return UIColor(red: 0.9, green: 0.9, blue: 0.2, alpha: 0.8)
        case .woundBoundary: return UIColor(red: 0.9, green: 0.2, blue: 0.2, alpha: 0.8)
        case .periSkin: return UIColor(red: 0.6, green: 0.6, blue: 0.6, alpha: 0.8)
        }
    }
}

/// 標註區域
struct AnnotationRegion {
    let type: RegionType
    let coordinates: [CGPoint]
    let boundingBox: CGRect
    let area: Double
}

/// 區域類型
enum RegionType {
    case polygon
    case rectangle
    case circle
    case point
}

/// 標註元數據
struct AnnotationMetadata {
    var bjwatScores: BJWATScores
    var revPWATScores: RevPWATScores
    var imageQuality: ImageQualityMetrics
    var processingTime: TimeInterval
    var annotatorInfo: AnnotatorInfo
}

// MARK: - BJWAT 評分結構

struct BJWATScores {
    var size: Int              // 0-5
    var depth: Int             // 0-4
    var edges: Int             // 0-2
    var necroticType: Int      // 0-3
    var necroticAmount: Int    // 0-4
    var exudateAmount: Int     // 0-4
    var exudateType: Int       // 0-3
    var tissueColor: Int       // 0-3
    var granulation: Int       // 0-3
    var epithelialization: Int // 0-3
    var periSkin: Int          // 0-3
    
    var totalScore: Int {
        return size + depth + edges + necroticType + necroticAmount + 
               exudateAmount + exudateType + tissueColor + granulation + 
               epithelialization + periSkin
    }
    
    var severityLevel: String {
        switch totalScore {
        case 0...13: return "輕微"
        case 14...26: return "中度"
        case 27...39: return "重度"
        default: return "極重度"
        }
    }
}

// MARK: - revPWAT 評分結構

struct RevPWATScores {
    var necrosis: Int      // 1-3
    var slough: Int        // 1-3
    var granulation: Int   // 1-3
    var exudate: Int       // 1-4
    var color: Int         // 0-2
    var depth: Int         // 1-3
    
    var totalScore: Int {
        return necrosis + slough + granulation + exudate + color + depth
    }
    
    var severityLevel: String {
        switch totalScore {
        case 6...10: return "輕微"
        case 11...15: return "中度"
        case 16...18: return "重度"
        default: return "極重度"
        }
    }
}

// MARK: - 影像品質指標

struct ImageQualityMetrics {
    let sharpness: Double      // 0-1
    let lighting: Double       // 0-1
    let colorAccuracy: Double  // 0-1
    let noiseLevel: Double     // 0-1
    let contrast: Double       // 0-1
    
    var overallQuality: Double {
        return (sharpness + lighting + colorAccuracy + (1 - noiseLevel) + contrast) / 5.0
    }
    
    var qualityLevel: String {
        switch overallQuality {
        case 0.8...1.0: return "優秀"
        case 0.6..<0.8: return "良好"
        case 0.4..<0.6: return "一般"
        default: return "不佳"
        }
    }
}

// MARK: - 標註者資訊

struct AnnotatorInfo {
    let id: String
    let name: String
    let role: String
    let timestamp: Date
    let version: String
}

// MARK: - 標註管理器

class WoundAnnotationManager: ObservableObject {
    static let shared = WoundAnnotationManager()
    
    @Published var currentAnnotation: WoundAnnotation?
    @Published var annotationHistory: [WoundAnnotation] = []
    @Published var isProcessing = false
    
    private init() {}
    
    // MARK: - 標註操作
    
    func createAnnotation(image: UIImage) -> WoundAnnotation {
        let annotation = WoundAnnotation(
            id: UUID(),
            timestamp: Date(),
            imageData: image.jpegData(compressionQuality: 0.8) ?? Data(),
            annotations: [],
            metadata: AnnotationMetadata(
                bjwatScores: BJWATScores(size: 0, depth: 0, edges: 0, necroticType: 0, necroticAmount: 0, exudateAmount: 0, exudateType: 0, tissueColor: 0, granulation: 0, epithelialization: 0, periSkin: 0),
                revPWATScores: RevPWATScores(necrosis: 1, slough: 1, granulation: 1, exudate: 1, color: 0, depth: 1),
                imageQuality: ImageQualityMetrics(sharpness: 0, lighting: 0, colorAccuracy: 0, noiseLevel: 0, contrast: 0),
                processingTime: 0,
                annotatorInfo: AnnotatorInfo(id: "system", name: "AI系統", role: "自動標註", timestamp: Date(), version: "1.0")
            )
        )
        
        currentAnnotation = annotation
        return annotation
    }
    
    func addAnnotationItem(_ item: AnnotationItem) {
        guard var annotation = currentAnnotation else { return }
        annotation.annotations.append(item)
        currentAnnotation = annotation
    }
    
    func updateBJWATScores(_ scores: BJWATScores) {
        guard var annotation = currentAnnotation else { return }
        annotation.metadata.bjwatScores = scores
        currentAnnotation = annotation
    }
    
    func updateRevPWATScores(_ scores: RevPWATScores) {
        guard var annotation = currentAnnotation else { return }
        annotation.metadata.revPWATScores = scores
        currentAnnotation = annotation
    }
    
    func saveAnnotation() {
        guard let annotation = currentAnnotation else { return }
        annotationHistory.append(annotation)
        currentAnnotation = nil
    }
    
    // MARK: - 匯出功能
    
    func exportAnnotationAsCOCO() -> Data? {
        guard let annotation = currentAnnotation else { return nil }
        
        let cocoFormat = [
            "images": [
                [
                    "id": 1,
                    "file_name": "wound_\(annotation.id.uuidString).jpg",
                    "width": 1920,
                    "height": 1080
                ]
            ],
            "annotations": annotation.annotations.enumerated().map { index, item in
                [
                    "id": index + 1,
                    "image_id": 1,
                    "category_id": AnnotationType.allCases.firstIndex(of: item.type) ?? 0,
                    "segmentation": [item.region.coordinates.flatMap { [$0.x, $0.y] }],
                    "area": item.region.area,
                    "bbox": [item.region.boundingBox.minX, item.region.boundingBox.minY, item.region.boundingBox.width, item.region.boundingBox.height],
                    "attributes": item.attributes
                ]
            }
        ]
        
        return try? JSONSerialization.data(withJSONObject: cocoFormat, options: .prettyPrinted)
    }
} 