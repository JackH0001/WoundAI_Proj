import Foundation

/// Storage evidence only. Never turn these fields into a geometric quality claim.
struct LiteStorageReceipt: Codable, Equatable {
    enum Status: String, Codable {
        case stored, rejected
        case notRequested = "not_requested"
        case notProvided = "not_provided"
    }
    let schemaVersion: Int
    let image: Status
    let metadata: Status
    let depth: Status
    let validityMask: Status
    let rgbdValidation: String
    var rawDepthReceipt: LiteRawDepthReceipt? = nil

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case image, metadata, depth
        case validityMask = "validity_mask"
        case rgbdValidation = "rgbd_validation"
        case rawDepthReceipt = "raw_depth_receipt"
    }

    static func parse(_ raw: Any?, rawPacket: LiteRawDepthPacket? = nil,
                      installationID: String? = nil, captureID: String? = nil) -> Self? {
        guard let raw, JSONSerialization.isValidJSONObject(raw),
              let data = try? JSONSerialization.data(withJSONObject: raw),
              var result = try? JSONDecoder().decode(Self.self, from: data),
              result.schemaVersion == 1, result.rgbdValidation == "not_performed" else { return nil }
        if let receipt = result.rawDepthReceipt {
            if let rawPacket, let installationID, let captureID,
               let encoded = try? JSONEncoder().encode(receipt),
               rawPacket.verifiesReceipt(encoded, installationID: installationID, captureID: captureID) {
                // Only a receipt matched to the exact submitted bytes is persisted.
            } else {
                result.rawDepthReceipt = nil
            }
        }
        return result
    }

    static func message(imageStored: Bool, receipt: Self?) -> String? {
        guard imageStored else { return nil }
        let unknown = "影像已上傳供研究；深度資料是否完整保存尚未確認。"
        guard let receipt, receipt.schemaVersion == 1,
              receipt.image == .stored, receipt.metadata == .stored,
              receipt.rgbdValidation == "not_performed" else { return unknown }
        if receipt.rawDepthReceipt != nil {
            return "影像與原始深度已保存，資料雜湊已核對；影像與深度對齊、精確度及建模適用性尚未驗證。"
        }
        if receipt.depth == .rejected || receipt.validityMask == .rejected {
            return "影像已保存，但深度或有效值遮罩未通過後端檢查；這筆不能視為完整的 3D 研究資料。"
        }
        guard receipt.depth == .stored else {
            return "影像已保存；後端未確認保存深度圖，這筆不能視為完整的 3D 研究資料。"
        }
        guard receipt.validityMask == .stored else {
            return "後端回報影像與深度圖已保存，但缺少有效值遮罩；配準與精確度尚未驗證。"
        }
        return "後端回報影像、深度圖與有效值遮罩已保存；配準與精確度尚未驗證。"
    }
}

/// Immutable storage proof; no claim of calibration or training eligibility.
struct LiteRawDepthReceipt: Codable, Equatable {
    let schema: String
    let installation_id: String
    let capture_id: String
    let bundle_sha256: String
    let bundle_bytes: Int
    let metadata_sha256: String
    let rgb_sha256: String
    let depth_sha256: String
    let status: String
    let registration: String
    let training_admission: String
}
