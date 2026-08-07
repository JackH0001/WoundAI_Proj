import Foundation
import UIKit

// MARK: - DTOs（對齊 openapi/annotation_segmentation.yaml；CodingKeys 之 raw value 必須等於 schema 欄位名）

/// GET 自 POST /segment → SegmentationResult
struct SegmentationResult: Codable {
    let status: String            // ai_assistive | manual_fallback | unavailable
    let maskPngB64: String?
    let confidence: Double?
    let modelId: String?
    enum CodingKeys: String, CodingKey {
        case status
        case maskPngB64 = "mask_png_b64"
        case confidence
        case modelId = "model_id"
    }
}

/// POST /annotations 之 body → AnnotationSubmit
struct AnnotationSubmit: Codable {
    let imageId: String
    let editedMaskPngB64: String
    let editorId: String
    let modelId: String?
    let pxPerMm: Double?
    enum CodingKeys: String, CodingKey {
        case imageId = "image_id"
        case editedMaskPngB64 = "edited_mask_png_b64"
        case editorId = "editor_id"
        case modelId = "model_id"
        case pxPerMm = "px_per_mm"
    }
}

/// POST /annotations 之 201 回應 → AnnotationRecord
struct AnnotationRecord: Codable {
    let schemaVersion: String
    let imageId: String
    let source: String?
    let areaPx: Int
    let areaMm2: Double?
    let correctionIou: Double?
    let pixelsChanged: Int?
    let status: String
    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case imageId = "image_id"
        case source
        case areaPx = "area_px"
        case areaMm2 = "area_mm2"
        case correctionIou = "correction_iou"
        case pixelsChanged = "pixels_changed"
        case status
    }
}

// MARK: - Service（資料飛輪：半自動分割初稿 → 醫師修邊 → 上傳標註）

enum FlywheelError: Error { case badURL, http(Int), noData }

final class AnnotationFlywheelService {
    /// 本機開發：FastAPI/Flask (engineering/phase2/app.py) 預設 http://localhost:8000
    private let baseURL: String
    private let session: URLSession
    init(baseURL: String = "http://localhost:8000", session: URLSession = .shared) {
        self.baseURL = baseURL; self.session = session
    }

    /// POST /segment（multipart）：取得 AI 分割初稿（輔助、附信心；缺模型回 503 → manual_fallback）
    func segment(image: UIImage, modelId: String? = nil, imageId: String? = nil) async throws -> SegmentationResult {
        guard let url = URL(string: "\(baseURL)/segment"),
              let jpeg = image.jpegData(compressionQuality: 0.9) else { throw FlywheelError.badURL }
        let boundary = "Boundary-\(UUID().uuidString)"
        var req = URLRequest(url: url); req.httpMethod = "POST"
        req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        var body = Data()
        func field(_ name: String, _ value: String) {
            body.append("--\(boundary)\r\n".data(using: .utf8)!)
            body.append("Content-Disposition: form-data; name=\"\(name)\"\r\n\r\n\(value)\r\n".data(using: .utf8)!)
        }
        body.append("--\(boundary)\r\n".data(using: .utf8)!)
        body.append("Content-Disposition: form-data; name=\"image\"; filename=\"w.jpg\"\r\n".data(using: .utf8)!)
        body.append("Content-Type: image/jpeg\r\n\r\n".data(using: .utf8)!)
        body.append(jpeg); body.append("\r\n".data(using: .utf8)!)
        if let m = modelId { field("model_id", m) }
        if let i = imageId { field("image_id", i) }
        body.append("--\(boundary)--\r\n".data(using: .utf8)!)
        req.httpBody = body
        let (data, resp) = try await session.data(for: req)
        // 503 = 模型不可用（graceful degrade）；仍解析 body 以取得 status
        if let http = resp as? HTTPURLResponse, !(200...503).contains(http.statusCode) {
            throw FlywheelError.http(http.statusCode)
        }
        return try JSONDecoder().decode(SegmentationResult.self, from: data)
    }

    /// POST /annotations：提交醫師修正後遮罩（修邊即標註，進訓練佇列）
    func submitAnnotation(_ submit: AnnotationSubmit) async throws -> AnnotationRecord {
        guard let url = URL(string: "\(baseURL)/annotations") else { throw FlywheelError.badURL }
        var req = URLRequest(url: url); req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(submit)
        let (data, resp) = try await session.data(for: req)
        guard let http = resp as? HTTPURLResponse, http.statusCode == 201 else {
            throw FlywheelError.http((resp as? HTTPURLResponse)?.statusCode ?? -1)
        }
        return try JSONDecoder().decode(AnnotationRecord.self, from: data)
    }

    /// GET /annotation-tasks：待標註/待品管任務清單
    func annotationTasks() async throws -> Data {
        guard let url = URL(string: "\(baseURL)/annotation-tasks") else { throw FlywheelError.badURL }
        let (data, _) = try await session.data(from: url)
        return data
    }
}
