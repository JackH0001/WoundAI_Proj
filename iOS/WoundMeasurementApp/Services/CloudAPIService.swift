import Foundation
import UIKit

// MARK: - Cloud API Models

struct CloudUploadRequest {
    let annotationData: Data
    let image: UIImage?
    let doctorId: String
    let patientId: String?
    let annotationId: String
}

struct CloudUploadResponse: Codable {
    let success: Bool
    let message: String
    let annotationId: String
    let qualityScore: Double
    let qualityStatus: String
    let bjwatScore: Int
    let revpwatScore: Int

    enum CodingKeys: String, CodingKey {
        case success
        case message
        case annotationId = "annotation_id"
        case qualityScore = "quality_score"
        case qualityStatus = "quality_status"
        case bjwatScore = "bjwat_score"
        case revpwatScore = "revpwat_score"
    }
}

struct CloudAuthResponse: Codable {
    let accessToken: String
    let tokenType: String
    let expiresIn: Int

    enum CodingKeys: String, CodingKey {
        case accessToken = "access_token"
        case tokenType = "token_type"
        case expiresIn = "expires_in"
    }
}

// MARK: - Sprint S3 / T2: Wound Analysis Response

/// Maps to POST /api/v1/analyze response (AnalysisResponse in FastAPI)
struct WoundAnalysisResponse: Codable {
    let imageId: String
    /// Actual wound area in cm² (nil if no calibration scale provided)
    let woundAreaCm2: Double?
    /// Wound perimeter in cm (nil if no calibration scale)
    let woundPerimeterCm: Double?
    /// Estimated wound volume in cm³ via ellipsoid model (nil if no calibration)
    let woundVolumeCm3: Double?
    let woundType: String?
    let severityScore: Int?
    let tissueComposition: TissueComposition
    /// Model confidence 0–1 (wsm.onnx + TTA, threshold=0.30)
    let confidence: Double
    let modelVersion: String
    let calibrationMethod: String?
    let scaleMmPerPx: Double?

    enum CodingKeys: String, CodingKey {
        case imageId            = "image_id"
        case woundAreaCm2       = "wound_area_cm2"
        case woundPerimeterCm   = "wound_perimeter_cm"
        case woundVolumeCm3     = "wound_volume_cm3"
        case woundType          = "wound_type"
        case severityScore      = "severity_score"
        case tissueComposition  = "tissue_composition"
        case confidence
        case modelVersion       = "model_version"
        case calibrationMethod  = "calibration_method"
        case scaleMmPerPx       = "scale_mm_per_px"
    }
}

struct TissueComposition: Codable {
    let granulation: Double
    let slough: Double
    let necrotic: Double
}

enum CloudAPIError: Error {
    case invalidURL
    case noImage
    case authenticationFailed
    case uploadFailed(String)
    case analyzeFailed(String)
    case networkError(Error)
    case invalidResponse
    case serverError(Int)
}

// MARK: - Cloud API Service

@MainActor
class CloudAPIService: ObservableObject {

    // MARK: - Properties

    @Published var isUploading = false
    @Published var uploadProgress: Double = 0.0
    @Published var lastError: CloudAPIError?

    // Production: Cloud Run URL (Sprint T GCP deployment)
    // Local dev: http://localhost:8000 (uvicorn + REDIS_URL=memory://)
    #if DEBUG
    private let baseURL = "http://localhost:8000"
    #else
    private let baseURL = "https://wound-ai-867037876992.asia-east1.run.app"
    #endif

    private var accessToken: String?
    private var tokenExpiryDate: Date?

    // MARK: - Singleton

    static let shared = CloudAPIService()

    private init() {}

    // MARK: - Authentication

    /// Authenticate with the WoundAI FastAPI server.
    /// FastAPI OAuth2PasswordRequestForm expects `username` (not `doctor_id`).
    func authenticate(doctorId: String, password: String) async throws {
        guard let url = URL(string: "\(baseURL)/api/v1/auth/login") else {
            throw CloudAPIError.invalidURL
        }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        // FastAPI OAuth2 form: application/x-www-form-urlencoded
        request.addValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")

        // FastAPI OAuth2PasswordRequestForm uses `username` field
        let formBody = "username=\(doctorId.urlEncoded)&password=\(password.urlEncoded)"
        request.httpBody = formBody.data(using: .utf8)

        do {
            let (data, response) = try await URLSession.shared.data(for: request)

            guard let httpResponse = response as? HTTPURLResponse else {
                throw CloudAPIError.invalidResponse
            }

            if httpResponse.statusCode == 200 {
                let authResponse = try JSONDecoder().decode(CloudAuthResponse.self, from: data)
                self.accessToken = authResponse.accessToken
                self.tokenExpiryDate = Date().addingTimeInterval(TimeInterval(authResponse.expiresIn))
                print("🔐 Cloud API 認證成功，Token 有效期至: \(tokenExpiryDate?.description ?? "未知")")
            } else {
                throw CloudAPIError.authenticationFailed
            }
        } catch let error as CloudAPIError {
            throw error
        } catch {
            print("❌ Cloud API 認證失敗: \(error)")
            throw CloudAPIError.networkError(error)
        }
    }

    private func isTokenValid() -> Bool {
        guard let token = accessToken,
              let expiryDate = tokenExpiryDate else {
            return false
        }
        return !token.isEmpty && Date() < expiryDate
    }

    // MARK: - Wound Analysis (Sprint S3 / T2)

    /// POST /api/v1/analyze — wound segmentation + area/volume measurement.
    ///
    /// - Parameters:
    ///   - image: RGB wound photo (JPEG compressed at 0.85 quality)
    ///   - scaleMmPerPx: calibration scale in mm/px; pass nil for pixel-only output
    ///   - calibrationMethod: "ruler" | "qr_code" | "reference_object" | nil
    /// - Returns: `WoundAnalysisResponse` with optional cm² / cm / cm³ values
    func analyzeWound(
        image: UIImage,
        scaleMmPerPx: Double? = nil,
        calibrationMethod: String? = "ruler"
    ) async throws -> WoundAnalysisResponse {
        guard isTokenValid() else {
            throw CloudAPIError.authenticationFailed
        }
        guard let imageData = image.jpegData(compressionQuality: 0.85) else {
            throw CloudAPIError.noImage
        }
        guard let url = URL(string: "\(baseURL)/api/v1/analyze") else {
            throw CloudAPIError.invalidURL
        }

        await MainActor.run {
            isUploading = true
            uploadProgress = 0.0
            lastError = nil
        }
        defer {
            Task { @MainActor in
                isUploading = false
                uploadProgress = 0.0
            }
        }

        let boundary = "WoundAI-\(UUID().uuidString)"
        var urlRequest = URLRequest(url: url)
        urlRequest.httpMethod = "POST"
        urlRequest.addValue("Bearer \(accessToken!)", forHTTPHeaderField: "Authorization")
        urlRequest.addValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")

        var body = Data()

        // Field: image (required)
        body.appendFormField(boundary: boundary,
                             name: "image",
                             filename: "wound_\(UUID().uuidString).jpg",
                             mimeType: "image/jpeg",
                             data: imageData)

        // Field: calibration_method (optional)
        if let method = calibrationMethod {
            body.appendFormField(boundary: boundary, name: "calibration_method", value: method)
        }

        // Field: scale_mm_per_px (optional — enables cm² / cm / cm³ output)
        if let scale = scaleMmPerPx {
            body.appendFormField(boundary: boundary, name: "scale_mm_per_px", value: String(scale))
        }

        body.append("--\(boundary)--\r\n".data(using: .utf8)!)
        urlRequest.httpBody = body

        await MainActor.run { uploadProgress = 0.4 }

        do {
            let (data, response) = try await URLSession.shared.data(for: urlRequest)

            await MainActor.run { uploadProgress = 1.0 }

            guard let httpResponse = response as? HTTPURLResponse else {
                throw CloudAPIError.invalidResponse
            }

            print("📡 /api/v1/analyze 回應 HTTP \(httpResponse.statusCode)")

            if httpResponse.statusCode == 200 {
                let decoded = try JSONDecoder().decode(WoundAnalysisResponse.self, from: data)
                print("✅ 傷口分析完成:")
                if let area = decoded.woundAreaCm2 {
                    print("   面積: \(String(format: "%.2f", area)) cm²")
                }
                if let perim = decoded.woundPerimeterCm {
                    print("   周長: \(String(format: "%.2f", perim)) cm")
                }
                if let vol = decoded.woundVolumeCm3 {
                    print("   體積: \(String(format: "%.3f", vol)) cm³")
                }
                print("   信心度: \(String(format: "%.1f", decoded.confidence * 100))%")
                return decoded
            } else {
                if let errorBody = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                   let detail = errorBody["detail"] as? String {
                    throw CloudAPIError.analyzeFailed(detail)
                }
                throw CloudAPIError.serverError(httpResponse.statusCode)
            }
        } catch let error as CloudAPIError {
            await MainActor.run { lastError = error }
            throw error
        } catch {
            let apiError = CloudAPIError.networkError(error)
            await MainActor.run { lastError = apiError }
            throw apiError
        }
    }

    // MARK: - Legacy Upload (kept for backwards-compat)

    func uploadAnnotation(_ request: CloudUploadRequest) async throws -> CloudUploadResponse {
        guard isTokenValid() else {
            throw CloudAPIError.authenticationFailed
        }

        await MainActor.run {
            isUploading = true
            uploadProgress = 0.0
            lastError = nil
        }

        defer {
            Task { @MainActor in
                isUploading = false
                uploadProgress = 0.0
            }
        }

        guard let url = URL(string: "\(baseURL)/api/v1/upload/annotation") else {
            throw CloudAPIError.invalidURL
        }

        let boundary = "WoundAI-\(UUID().uuidString)"
        var urlRequest = URLRequest(url: url)
        urlRequest.httpMethod = "POST"
        urlRequest.addValue("Bearer \(accessToken!)", forHTTPHeaderField: "Authorization")
        urlRequest.addValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")

        var body = Data()

        body.appendFormField(boundary: boundary, name: "annotation_data", value: String(data: request.annotationData, encoding: .utf8) ?? "")
        body.appendFormField(boundary: boundary, name: "doctor_id", value: request.doctorId)
        body.appendFormField(boundary: boundary, name: "annotation_id", value: request.annotationId)

        if let patientId = request.patientId {
            body.appendFormField(boundary: boundary, name: "patient_id", value: patientId)
        }

        if let image = request.image {
            await MainActor.run { uploadProgress = 0.3 }
            guard let imageData = image.jpegData(compressionQuality: 0.8) else {
                throw CloudAPIError.noImage
            }
            body.appendFormField(boundary: boundary,
                                 name: "image",
                                 filename: "wound_\(request.annotationId).jpg",
                                 mimeType: "image/jpeg",
                                 data: imageData)
            await MainActor.run { uploadProgress = 0.7 }
        }

        body.append("--\(boundary)--\r\n".data(using: .utf8)!)
        urlRequest.httpBody = body

        do {
            await MainActor.run { uploadProgress = 0.8 }
            let (data, response) = try await URLSession.shared.data(for: urlRequest)
            await MainActor.run { uploadProgress = 1.0 }

            guard let httpResponse = response as? HTTPURLResponse else {
                throw CloudAPIError.invalidResponse
            }

            print("📤 Cloud API 回應狀態碼: \(httpResponse.statusCode)")

            if httpResponse.statusCode == 200 {
                let uploadResponse = try JSONDecoder().decode(CloudUploadResponse.self, from: data)
                print("✅ 標註資料上傳成功 — 標註ID: \(uploadResponse.annotationId)")
                return uploadResponse
            } else {
                if let errorData = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                   let detail = errorData["detail"] as? String {
                    throw CloudAPIError.uploadFailed(detail)
                }
                throw CloudAPIError.serverError(httpResponse.statusCode)
            }
        } catch let error as CloudAPIError {
            await MainActor.run { lastError = error }
            throw error
        } catch {
            let apiError = CloudAPIError.networkError(error)
            await MainActor.run { lastError = apiError }
            throw apiError
        }
    }

    // MARK: - Helper Methods

    func formatAnnotationDataForCloud(_ annotationData: Data) throws -> Data {
        guard let cocoData = try? JSONSerialization.jsonObject(with: annotationData) as? [String: Any] else {
            throw CloudAPIError.invalidResponse
        }

        var cloudFormat: [String: Any] = [:]

        if let images = cocoData["images"] as? [[String: Any]], let firstImage = images.first {
            cloudFormat["image_width"] = firstImage["width"] ?? 0
            cloudFormat["image_height"] = firstImage["height"] ?? 0
            cloudFormat["file_name"] = firstImage["file_name"] ?? ""
        }

        if let annotations = cocoData["annotations"] as? [[String: Any]] {
            var segmentationMasks: [[Double]] = []
            var boundingBoxes: [[Double]] = []

            for annotation in annotations {
                if let segmentation = annotation["segmentation"] as? [[Double]], !segmentation.isEmpty {
                    segmentationMasks.append(segmentation[0])
                }
                if let bbox = annotation["bbox"] as? [Double] {
                    boundingBoxes.append(bbox)
                }
            }

            cloudFormat["segmentation_mask"] = segmentationMasks
            cloudFormat["bounding_box"] = boundingBoxes.first ?? [0, 0, 0, 0]
        }

        cloudFormat["bjwat_size"] = 2
        cloudFormat["bjwat_depth"] = 1
        cloudFormat["bjwat_edges"] = 2
        cloudFormat["bjwat_necrotic_type"] = 1
        cloudFormat["bjwat_necrotic_amount"] = 2
        cloudFormat["bjwat_exudate_amount"] = 1
        cloudFormat["bjwat_exudate_type"] = 1
        cloudFormat["bjwat_tissue_color"] = 2
        cloudFormat["bjwat_granulation"] = 2
        cloudFormat["bjwat_epithelialization"] = 1
        cloudFormat["bjwat_peri_skin"] = 2
        cloudFormat["revpwat_necrosis"] = 25
        cloudFormat["revpwat_slough"] = 30
        cloudFormat["revpwat_granulation"] = 35
        cloudFormat["revpwat_exudate"] = 10
        cloudFormat["revpwat_color"] = 0
        cloudFormat["revpwat_depth"] = 1
        cloudFormat["additional_notes"] = "從iOS應用程式上傳的標註資料"

        return try JSONSerialization.data(withJSONObject: cloudFormat, options: .prettyPrinted)
    }

    func clearAuthentication() {
        accessToken = nil
        tokenExpiryDate = nil
    }
}

// MARK: - Multipart Form-Data Helpers

private extension Data {
    /// Append a plain-text form field.
    mutating func appendFormField(boundary: String, name: String, value: String) {
        append("--\(boundary)\r\n".data(using: .utf8)!)
        append("Content-Disposition: form-data; name=\"\(name)\"\r\n\r\n".data(using: .utf8)!)
        append((value + "\r\n").data(using: .utf8)!)
    }

    /// Append a binary file form field.
    mutating func appendFormField(
        boundary: String,
        name: String,
        filename: String,
        mimeType: String,
        data fileData: Data
    ) {
        append("--\(boundary)\r\n".data(using: .utf8)!)
        append("Content-Disposition: form-data; name=\"\(name)\"; filename=\"\(filename)\"\r\n".data(using: .utf8)!)
        append("Content-Type: \(mimeType)\r\n\r\n".data(using: .utf8)!)
        append(fileData)
        append("\r\n".data(using: .utf8)!)
    }
}

// MARK: - String URL Encoding

private extension String {
    var urlEncoded: String {
        addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? self
    }
}

// MARK: - Error Extensions

extension CloudAPIError: LocalizedError {
    var errorDescription: String? {
        switch self {
        case .invalidURL:
            return "無效的 API 網址"
        case .noImage:
            return "無法處理影像檔案"
        case .authenticationFailed:
            return "身份驗證失敗，請重新登入"
        case .uploadFailed(let message):
            return "上傳失敗: \(message)"
        case .analyzeFailed(let message):
            return "傷口分析失敗: \(message)"
        case .networkError(let error):
            return "網路錯誤: \(error.localizedDescription)"
        case .invalidResponse:
            return "伺服器回應格式錯誤"
        case .serverError(let code):
            return "伺服器錯誤 (狀態碼: \(code))"
        }
    }
}
