import Foundation

/// Image pixel centres -> millimetres on the marker plane. Never a 3D surface area.
struct PlanarAreaCalibration: Codable, Equatable {
    var version: Int
    var status: String
    var reason: String
    var h: [Double]
    var bounds: [Double]
    var corner_error_px: Double? = nil
    var relative_sensitivity: Double? = nil
    var profile: String? = nil
    var marker_ids: [Int]? = nil
    var reprojection_rms_px: Double? = nil
    var holdout_max_px: Double? = nil
    var quality_policy: String? = nil
    var reference_marker_edge_px: Double? = nil
    var marker_edge_px: Double? = nil
    var reprojection_max_normalized_px: Double? = nil
    var holdout_max_normalized_px: Double? = nil
    var area_quality: String? = nil
    var quality_reasons: [String]? = nil

    static func parse(_ raw: Any?) -> PlanarAreaCalibration? {
        guard let raw, !(raw is NSNull) else { return nil }
        guard JSONSerialization.isValidJSONObject(raw),
              let data = try? JSONSerialization.data(withJSONObject: raw) else {
            return Self(version: 1, status: "rejected", reason: "invalid_contract", h: [], bounds: [])
        }
        return (try? JSONDecoder().decode(Self.self, from: data)) ??
            Self(version: 1, status: "rejected", reason: "invalid_contract", h: [], bounds: [])
    }
    var json: [String: Any] {
        ["version": version, "status": status, "reason": reason, "h": h, "bounds": bounds,
         "corner_error_px": corner_error_px as Any? ?? NSNull(), "relative_sensitivity": relative_sensitivity as Any? ?? NSNull(),
         "profile": profile as Any? ?? NSNull(), "marker_ids": marker_ids as Any? ?? NSNull(),
         "reprojection_rms_px": reprojection_rms_px as Any? ?? NSNull(), "holdout_max_px": holdout_max_px as Any? ?? NSNull(),
         "quality_policy": quality_policy as Any? ?? NSNull(),
         "reference_marker_edge_px": reference_marker_edge_px as Any? ?? NSNull(),
         "marker_edge_px": marker_edge_px as Any? ?? NSNull(),
         "reprojection_max_normalized_px": reprojection_max_normalized_px as Any? ?? NSNull(),
         "holdout_max_normalized_px": holdout_max_normalized_px as Any? ?? NSNull(),
         "area_quality": area_quality as Any? ?? NSNull(),
         "quality_reasons": quality_reasons as Any? ?? NSNull()]
    }
    /// Human-readable guidance; acceptance is engineering geometry, not clinical accuracy.
    var qualityMessage: String {
        guard version == 1 else { return "目前版本無法核對校正資料。請使用相容的 App 與後台版本。" }
        if status == "accepted" {
            guard h.count == 9, bounds.count == 4, h.allSatisfy(\.isFinite), bounds.allSatisfy(\.isFinite),
                  bounds[0] < bounds[2], bounds[1] < bounds[3] else { return "未能可靠校正面積。請確認完整貼紙、影像與標註；影像及標註仍可保存。" }
            let determinant = h[0]*(h[4]*h[8]-h[5]*h[7])-h[1]*(h[3]*h[8]-h[5]*h[6])+h[2]*(h[3]*h[7]-h[4]*h[6])
            guard determinant.isFinite, abs(determinant) > 1e-16 else { return "未能可靠校正面積。請確認完整貼紙、影像與標註；影像及標註仍可保存。" }
            return (profile == "woundai-sticker-20-candidate-v1" ? "20 mm 研究候選貼紙。" : "") + "貼紙幾何檢查通過；請確認貼紙尺寸正確，並與傷口表面共面。此面積不代表三維表面積。"
        }
        if status == "provisional" {
            guard area_quality == "unverified", !(quality_reasons ?? []).isEmpty,
                  h.count == 9, bounds.count == 4, h.allSatisfy(\.isFinite), bounds.allSatisfy(\.isFinite),
                  bounds[0] < bounds[2], bounds[1] < bounds[3] else {
                return "面積暫估資料不完整；請保留影像及標註，勿將面積視為已驗證。"
            }
            return "面積暫估：角點誤差敏感度超過工程門檻，精度尚未驗證。可保存影像與標註；請確認貼紙清晰、靠近傷口並盡量共面。"
        }
        guard status == "rejected" else { return "未能可靠校正面積。請確認完整貼紙、影像與標註；影像及標註仍可保存。" }
        switch reason {
        case "marker_too_small", "board_marker_too_small_or_degenerate": return "貼紙影像太小，暫不提供校正面積。請靠近拍攝，讓傷口與完整貼紙清晰入鏡。"
        case "corner_extrapolation_unstable", "board_sensitivity_exceeded": return "此範圍的校正對角度或角點偏差太敏感。請讓貼紙貼近傷口、保持共面並盡量正對拍攝。"
        case "board_geometry_inconsistent", "board_holdout_failed": return "四點標靶的幾何核對未通過。請確認紙張平貼硬板、四碼完整清晰；目前不能可靠換算面積。"
        case "four_unique_board_markers_required": return "四點標靶未完整辨識。請讓四個黑白碼完整、清晰入鏡。"
        case "outside_coplanar_region": return "標註超出已檢查的校正範圍。請保留原紀錄，重新取景後量測。"
        case "empty_mask", "empty_area": return "尚無可計算面積的傷口遮罩；請先確認標註範圍。"
        case "unknown_sticker_profile": return "貼紙版本或尺寸尚未支援，暫不提供校正面積；請勿自行猜選尺寸。"
        case "multiple_calibration_markers": return "同一影像有多張校正標記；請只保留本次使用的一張貼紙入鏡。"
        case "client_contract_required", "invalid_contract": return "目前版本無法核對校正資料。請使用相容的 App 與後台版本。"
        default: return "未能可靠校正面積。請確認完整貼紙、影像與標註；影像及標註仍可保存。"
        }
    }
    func area(mask: [UInt8], width: Int, height: Int, x: Double, y: Double, scale: Double) -> Double? {
        guard version == 1, (status == "accepted" || status == "provisional"), h.count == 9, bounds.count == 4,
              h.allSatisfy(\.isFinite), bounds.allSatisfy(\.isFinite),
              width > 0, height > 0, mask.count == width * height,
              x.isFinite, y.isFinite, scale.isFinite, scale > 0 else { return nil }
        let determinant = h[0]*(h[4]*h[8]-h[5]*h[7])-h[1]*(h[3]*h[8]-h[5]*h[6])+h[2]*(h[3]*h[7]-h[4]*h[6])
        guard abs(determinant) > 1e-16, bounds[0] < bounds[2], bounds[1] < bounds[3] else { return nil }
        var denominatorSign: Double?
        func point(_ px: Double, _ py: Double) -> (Double, Double)? {
            guard px >= bounds[0]-1e-7, py >= bounds[1]-1e-7,
                  px <= bounds[2]+1e-7, py <= bounds[3]+1e-7 else { return nil }
            let d = h[6]*px+h[7]*py+h[8]
            guard d.isFinite, abs(d) > 1e-8 else { return nil }
            let sign = d > 0 ? 1.0 : -1.0
            if let previous = denominatorSign, previous != sign { return nil }
            denominatorSign = sign
            let u = (h[0]*px+h[1]*py+h[2])/d, v = (h[3]*px+h[4]*py+h[5])/d
            return u.isFinite && v.isFinite ? (u,v) : nil
        }
        var area = 0.0
        // Exact quadrilateral area for each horizontal run; holes and disjoint wounds survive.
        for row in 0..<height {
            var col = 0
            while col < width {
                if mask[row*width+col] == 0 { col += 1; continue }
                let first = col
                while col < width && mask[row*width+col] != 0 { col += 1 }
                let l=x+(Double(first)-0.5)/scale, r=x+(Double(col)-0.5)/scale
                let t=y+(Double(row)-0.5)/scale, b=y+(Double(row)+0.5)/scale
                guard let p0=point(l,t), let p1=point(r,t), let p2=point(r,b), let p3=point(l,b) else { return nil }
                let p=[p0,p1,p2,p3]
                var cross=0.0
                for i in 0..<4 { cross += p[i].0*p[(i+1)%4].1-p[i].1*p[(i+1)%4].0 }
                area += abs(cross)/200
            }
        }
        return area.isFinite ? area : nil
    }
}
