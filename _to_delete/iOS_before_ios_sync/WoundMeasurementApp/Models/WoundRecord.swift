import Foundation
import CoreData
import SwiftUI

@objc(WoundRecord)
public class WoundRecord: NSManagedObject {
    
}

extension WoundRecord {
    
    @nonobjc public class func fetchRequest() -> NSFetchRequest<WoundRecord> {
        return NSFetchRequest<WoundRecord>(entityName: "WoundRecord")
    }
    
    @NSManaged public var id: UUID
    @NSManaged public var date: Date
    @NSManaged public var area: Double
    @NSManaged public var volume: Double
    @NSManaged public var perimeter: Double
    @NSManaged public var maxDepth: Double
    @NSManaged public var acuteScore: Double
    @NSManaged public var chronicScore: Double
    @NSManaged public var infectedScore: Double
    @NSManaged public var healingScore: Double
    @NSManaged public var confidence: Double
    @NSManaged public var riskLevel: String
    @NSManaged public var healingStage: String
    @NSManaged public var necroticPercentage: Double
    @NSManaged public var granulationPercentage: Double
    @NSManaged public var epithelialPercentage: Double
    @NSManaged public var notes: String?
    @NSManaged public var imageData: Data?
    @NSManaged public var recommendations: String?
    @NSManaged public var qualityScore: Double
    @NSManaged public var snr: Double
    @NSManaged public var blurLevel: Double
    @NSManaged public var depthCoverage: Double
    @NSManaged public var errorMessage: String?
    
    // 醫療合規性追蹤字段
    @NSManaged public var calibrationSource: String? // "sticker", "lidar", "estimated"
    @NSManaged public var cmPerPixel: Double
    @NSManaged public var appVersion: String?
    @NSManaged public var deviceModel: String?
    @NSManaged public var processingTime: Double
    @NSManaged public var medicalDisclaimer: String?
    @NSManaged public var validationWarnings: String? // JSON格式儲存警告列表
    @NSManaged public var isReliable: Bool
    @NSManaged public var pixelArea: Double // 像素域面積，用於核對
    
    var formattedDate: String {
        let formatter = DateFormatter()
        formatter.dateStyle = .medium
        formatter.timeStyle = .short
        formatter.locale = Locale(identifier: "zh_TW")
        return formatter.string(from: date)
    }
    
    var primaryClassification: String {
        let scores = [
            ("急性", acuteScore),
            ("慢性", chronicScore),
            ("感染", infectedScore),
            ("癒合中", healingScore)
        ]
        
        let maxScore = scores.max { $0.1 < $1.1 }
        return maxScore?.0 ?? "未知"
    }
    
    var riskLevelColor: Color {
        switch riskLevel.lowercased() {
        case "low", "低風險":
            return .green
        case "medium", "中等風險":
            return .orange
        case "high", "高風險":
            return .red
        default:
            return .gray
        }
    }
    
    var healingStageColor: Color {
        switch healingStage.lowercased() {
        case "inflammatory", "發炎期":
            return .red
        case "proliferative", "增生期":
            return .orange
        case "maturation", "成熟期":
            return .green
        default:
            return .gray
        }
    }
    
    var image: UIImage? {
        guard let imageData = imageData else { return nil }
        return UIImage(data: imageData)
    }
    
    var recommendationsList: [String] {
        guard let recommendations = recommendations else { return [] }
        return recommendations.components(separatedBy: "||").filter { !$0.isEmpty }
    }
    
    static func createSampleData() -> [WoundMeasurementResult] {
        return [
            WoundMeasurementResult(
                area: 12.5,
                volume: 2.3,
                classification: DetailedWoundClassification(acuteScore: 0.8, chronicScore: 0.2, infectedScore: 0.1, healingScore: 0.7, confidence: 0.85),
                timestamp: Date().addingTimeInterval(-86400)
            ),
            WoundMeasurementResult(
                area: 8.7,
                volume: 1.5,
                classification: DetailedWoundClassification(acuteScore: 0.3, chronicScore: 0.7, infectedScore: 0.2, healingScore: 0.5, confidence: 0.75),
                timestamp: Date().addingTimeInterval(-172800)
            ),
            WoundMeasurementResult(
                area: 15.2,
                volume: 3.1,
                classification: DetailedWoundClassification(acuteScore: 0.9, chronicScore: 0.1, infectedScore: 0.05, healingScore: 0.8, confidence: 0.92),
                timestamp: Date().addingTimeInterval(-259200)
            )
        ]
    }
}

extension WoundRecord: Identifiable {
    
}

struct HistoryView: View {
    @State private var showingAddSample = false
    
    var body: some View {
        NavigationView {
            VStack {
                Text("歷史紀錄功能暫時停用")
                    .font(.title2)
                    .foregroundColor(.secondary)
                    .padding()
                
                Text("Core Data 模型正在修復中")
                    .font(.caption)
                    .foregroundColor(.secondary)
                
                Spacer()
            }
            .navigationTitle("歷史紀錄")
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    Button("添加範例") {
                        addSampleData()
                    }
                }
            }
        }
    }
    
    private func addSampleData() {
        // 暫時停用 Core Data 功能
        print("Core Data 功能暫時停用")
    }
}

struct RecordRowView: View {
    let record: WoundRecord
    
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                VStack(alignment: .leading) {
                    Text(record.formattedDate)
                        .font(.headline)
                    
                    Text(record.primaryClassification)
                        .font(.subheadline)
                        .foregroundColor(.secondary)
                }
                
                Spacer()
                
                VStack(alignment: .trailing) {
                    Text("\(record.area, specifier: "%.1f") cm²")
                        .font(.title3)
                        .fontWeight(.semibold)
                    
                    if record.volume > 0 {
                        Text("\(record.volume, specifier: "%.1f") cm³")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                }
            }
            
            HStack {
                Label(record.riskLevel, systemImage: "exclamationmark.triangle.fill")
                    .font(.caption)
                    .foregroundColor(record.riskLevelColor)
                
                Spacer()
                
                Label(record.healingStage, systemImage: "heart.fill")
                    .font(.caption)
                    .foregroundColor(record.healingStageColor)
            }
        }
        .padding(.vertical, 4)
    }
}

struct RecordDetailView: View {
    let record: WoundRecord
    
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                
                if let image = record.image {
                    Image(uiImage: image)
                        .resizable()
                        .aspectRatio(contentMode: .fit)
                        .frame(maxHeight: 300)
                        .cornerRadius(10)
                }
                
                MeasurementSection(record: record)
                
                ClassificationSection(record: record)
                
                QualitySection(record: record)
                
                TissueCompositionSection(record: record)
                
                RecommendationsSection(record: record)
                
                NotesSection(record: record)
            }
            .padding()
        }
        .navigationTitle("測量詳情")
        .navigationBarTitleDisplayMode(.inline)
    }
}

struct MeasurementSection: View {
    let record: WoundRecord
    
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("測量數據")
                .font(.headline)
            
            VStack(spacing: 8) {
                MeasurementRow(label: "面積", value: String(format: "%.2f cm²", record.area))
                MeasurementRow(label: "周長", value: String(format: "%.2f cm", record.perimeter))
                MeasurementRow(label: "體積", value: String(format: "%.2f cm³", record.volume))
                MeasurementRow(label: "最大深度", value: String(format: "%.2f mm", record.maxDepth))
            }
        }
        .padding()
        .background(Color.gray.opacity(0.1))
        .cornerRadius(10)
    }
}

struct ClassificationSection: View {
    let record: WoundRecord
    
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("分類結果")
                .font(.headline)
            
            VStack(spacing: 8) {
                ClassificationRow(label: "急性", score: record.acuteScore)
                ClassificationRow(label: "慢性", score: record.chronicScore)
                ClassificationRow(label: "感染", score: record.infectedScore)
                ClassificationRow(label: "癒合", score: record.healingScore)
            }
            
            HStack {
                Text("信心度:")
                Spacer()
                Text("\(record.confidence * 100, specifier: "%.1f")%")
                    .fontWeight(.semibold)
            }
        }
        .padding()
        .background(Color.gray.opacity(0.1))
        .cornerRadius(10)
    }
}

struct QualitySection: View {
    let record: WoundRecord
    
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("品質指標")
                .font(.headline)
            
            VStack(spacing: 8) {
                MeasurementRow(label: "總體品質", value: String(format: "%.1f%%", record.qualityScore * 100))
                MeasurementRow(label: "信噪比", value: String(format: "%.1f dB", record.snr))
                MeasurementRow(label: "清晰度", value: String(format: "%.1f", record.blurLevel))
                MeasurementRow(label: "深度覆蓋", value: String(format: "%.1f%%", record.depthCoverage * 100))
            }
        }
        .padding()
        .background(Color.gray.opacity(0.1))
        .cornerRadius(10)
    }
}

struct TissueCompositionSection: View {
    let record: WoundRecord
    
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("組織組成")
                .font(.headline)
            
            VStack(spacing: 8) {
                TissueRow(label: "壞死組織", percentage: record.necroticPercentage, color: .black)
                TissueRow(label: "肉芽組織", percentage: record.granulationPercentage, color: .red)
                TissueRow(label: "上皮組織", percentage: record.epithelialPercentage, color: .pink)
            }
        }
        .padding()
        .background(Color.gray.opacity(0.1))
        .cornerRadius(10)
    }
}

struct RecommendationsSection: View {
    let record: WoundRecord
    
    var body: some View {
        if !record.recommendationsList.isEmpty {
            VStack(alignment: .leading, spacing: 10) {
                Text("建議")
                    .font(.headline)
                
                ForEach(record.recommendationsList, id: \.self) { recommendation in
                    HStack(alignment: .top) {
                        Image(systemName: "checkmark.circle.fill")
                            .foregroundColor(.blue)
                            .font(.caption)
                        
                        Text(recommendation)
                            .font(.body)
                    }
                }
            }
            .padding()
            .background(Color.blue.opacity(0.1))
            .cornerRadius(10)
        }
    }
}

struct NotesSection: View {
    let record: WoundRecord
    
    var body: some View {
        if let notes = record.notes, !notes.isEmpty {
            VStack(alignment: .leading, spacing: 10) {
                Text("備註")
                    .font(.headline)
                
                Text(notes)
                    .font(.body)
            }
            .padding()
            .background(Color.gray.opacity(0.1))
            .cornerRadius(10)
        }
    }
}

struct MeasurementRow: View {
    let label: String
    let value: String
    
    var body: some View {
        HStack {
            Text(label)
            Spacer()
            Text(value)
                .fontWeight(.semibold)
        }
    }
}

struct ClassificationRow: View {
    let label: String
    let score: Double
    
    var body: some View {
        HStack {
            Text(label)
            Spacer()
            
            ProgressView(value: score)
                .frame(width: 80)
            
            Text("\(score * 100, specifier: "%.0f")%")
                .fontWeight(.semibold)
                .frame(width: 40, alignment: .trailing)
        }
    }
}

struct TissueRow: View {
    let label: String
    let percentage: Double
    let color: Color
    
    var body: some View {
        HStack {
            HStack {
                Circle()
                    .fill(color)
                    .frame(width: 12, height: 12)
                
                Text(label)
            }
            
            Spacer()
            
            Text("\(percentage * 100, specifier: "%.1f")%")
                .fontWeight(.semibold)
        }
    }
}