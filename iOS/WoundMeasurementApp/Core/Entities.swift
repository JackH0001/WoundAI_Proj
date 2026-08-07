import Foundation

/**
 個案化醫療紀錄的三層資料模型（對等 Android Room v6）。

 ```
 Patient ──< WoundCase ──< Measurement
             （wdCode）
     └──< Consent
 ```

 **缺中間「傷口」層是時間軸沒有意義的根因**：同一位病患身上的褥瘡與足部潰瘍若都掛在
 病患底下，癒合趨勢圖會把兩個不同的傷口畫成同一條線。

 ## PII 邊界

 姓名與病歷號**加密存本機**；離開這台裝置的只有 `wdCode`。後端的 `code` 欄位有
 `^WD-[A-Za-z0-9_-]{1,32}$` 的白名單，對照表不進雲端、不進版控、不進備份。
 */

// MARK: - Patient

struct Patient: Identifiable, Equatable {
    var id: String = UUID().uuidString
    /// **加密存放**（`enc1:` 前綴）。經 `CaseRepository` 進出時已是明文。
    var name: String
    var birthDate: String = ""
    var gender: String = ""
    /// **加密存放**。顯示一律用 `PhiCrypto.maskMrn()`。
    var medicalRecordNumber: String
    var department: String = ""
    var registrationTime: Date = Date()
    var lastVisitTime: Date?
    var notes: String?
    /**
     病歷號的 **Keystore/Keychain HMAC-SHA256** 指紋（小寫 hex），用來查重。

     ⚠ 不可改成加鹽 SHA-256：病歷號熵極低，而鹽寫在原始碼裡對拿到 App 的人就是公開的
     ——字典攻擊幾秒鐘就能反查回明文。
     */
    var mrnHash: String?
}

// MARK: - WoundCase

struct WoundCase: Identifiable, Equatable {
    var id: Int64 = 0
    var patientId: String
    /// **唯一離開本機的識別子。** 由 `SecureRandom` 產生，DB 層 UNIQUE。
    var wdCode: String
    var bodySite: String
    var woundType: String
    var onsetDate: Date?
    var createdAt: Date = Date()
    var closedAt: Date?
    var notes: String?

    /**
     產生新的 WD 代碼：`"WD-" + 8 碼大寫 hex`。

     ⚠ **不可**用時間戳尾碼（Android 早期版本的 `currentTimeMillis().takeLast(8)`）：
     那是揮發性的、不落地，病患回診時會拿到一個新代碼，於是時間軸永遠串不起來。
     格式須符合後端 `^WD-[A-Za-z0-9_-]{1,32}$`。
     */
    static func newWdCode() -> String {
        var bytes = [UInt8](repeating: 0, count: 4)
        _ = SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes)
        return "WD-" + bytes.map { String(format: "%02X", $0) }.joined()
    }
}

// MARK: - Consent

struct Consent: Identifiable, Equatable {
    var id: Int64 = 0
    var patientId: String
    /// ① 照護同意。**必填**——沒有它連拍照都不該讓按。
    var consentCare: Bool
    /// ② 訓練同意。選填、可撤回。「不同意不影響照護權益」須顯示在同意書上。
    var consentTrain: Bool
    /// **加密存放**的手寫簽名 PNG。**簽名不隨標註上傳。**
    var signaturePng: Data?
    var signedAt: Date = Date()
    var signerRole: String = "patient"        // patient / legal_guardian
    var witnessStaff: String?
    var templateVersion: String = "IRB_consent_v1"
    /// 撤回時間。**留痕不刪除**，與後端 `withdrawn.jsonl` 的墓碑機制一致。
    var withdrawnAt: Date?
    var withdrawReason: String?

    /// 送標註前必須讀這個，不可讀畫面快照——剛撤回的話快照仍是 true。
    var trainEffective: Bool { return consentTrain && withdrawnAt == nil }
}

// MARK: - Measurement

struct Measurement: Identifiable, Equatable {
    var id: Int64 = 0
    var patientId: String?
    var timestamp: Date = Date()
    var hasWound: Bool = true
    var confidence: Double = 0
    /// cm²。`nil` ＝ 未校正，**不是 0**。
    var estimatedArea: Double?
    var woundTypeLabel: String?               // "AI(<route>)"
    var quality: String = "backend"
    var imagePath: String = ""                // LocalImageStore 檔名；"" ＝ 無
    var notes: String?
    var isPatientIdentified: Bool = false

    /// 時間軸分組依據。刻意**不設 FK**——這張是真實病歷，整表重建的風險高於好處。
    var caseId: Int64?
    var wdCode: String?
    /// 後端影像內容雜湊。沒有它就送不了訓練標註（孤兒 GT）。
    var imageId: String?
    var mmPerPx: Double?
    var route: String?
    var source: String?                       // clinical / sample / phantom / external

    /// JSON `[[x,y],…]`，座標空間 ＝ `imageW × imageH`。
    var gtPolygon: String?
    var imageW: Int?
    var imageH: Int?
    var exudate: Int?
    var correctionIou: Double?                // 1.0 ＝ 未改
    var annotationSubmitted: Bool = false
    /// `doctor_verified` 的真值來源。舊列一律 false——**不可回填 true**。
    var doctorVerified: Bool = false

    // 組織比例。**nullable，`nil` ≠ 0**：「沒量到」與「量到 0%」是兩件事。
    var tissueGranulation: Double?
    var tissueSlough: Double?
    var tissueNecrosis: Double?
    var tissueEpithelial: Double?
    var tissueOther: Double?

    /// 加密的修邊柵格 PNG 檔名。**必須與 `rasterMeta` 成對**。
    var rasterPath: String?
    var rasterMeta: String?

    var tissueFrac: [String: Double]? {
        guard let g = tissueGranulation else { return nil }
        return [
            "granulation": g,
            "slough":      tissueSlough ?? 0,
            "necrosis":    tissueNecrosis ?? 0,
            "epithelial":  tissueEpithelial ?? 0,
            "other":       tissueOther ?? 0
        ]
    }

    var polygonPoints: [[Int]] {
        guard let s = gtPolygon, let d = s.data(using: .utf8), let j = JSONAny(data: d) else { return [] }
        return j.intPointList
    }
}

// MARK: - 個案摘要

struct CaseSummary {
    let count: Int
    let lastArea: Double?
    let firstArea: Double?
    let daysSinceLast: Int?

    /// 相對首次的面積變化百分比。樣本數 <2 或首次面積 ≤0 時為 nil（除以 0 沒有意義）。
    var changePct: Double? {
        guard count >= 2, let f = firstArea, f > 0, let l = lastArea else { return nil }
        return (l - f) / f * 100.0
    }
}
