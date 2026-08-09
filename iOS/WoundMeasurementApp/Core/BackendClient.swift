import Foundation

/**
 後端 HTTP 客戶端（對接 `Backend/Flask/app.py`）。**逐欄位對齊 Android `BackendClient.kt`。**

 端點（以已部署的 Flask 為準，不是 `openapi/` 底下那兩份 yaml——它們描述的是別的服務）：

 | 方法 | 路徑 | 用途 |
 |---|---|---|
 | POST | `/api/auth/login`              | 取 JWT（24h）與角色權限 |
 | POST | `/api/auth/exchange`           | 一次性碼換正式 token |
 | POST | `/api/v1/auth/onetime`         | 發一次性碼給瀏覽器開主控台 |
 | GET  | `/api/health`                  | 服務健康度（免認證） |
 | POST | `/api/v1/classify`             | 五階段：分割／校正／色準／組織／PUSH |
 | POST | `/api/v1/annotation`           | 送訓練標註進飛輪佇列 |
 | POST | `/api/v1/consent/withdraw`     | 撤回訓練同意 |
 | POST | `/api/v1/consent/restore`      | 重新取得同意（沒有它撤回就是死局） |
 | GET  | `/api/v1/flywheel/stats`       | 佇列健康度與收案進度 |

 ⚠ **iOS 舊有的 `CloudAPIService` / `WoundAnalysisAPIService` 打的路徑在這個後端上全部是
 404**（`/api/v1/auth/login`、`/api/v1/analyze`、`/api/v1/upload/annotation`、
 `/segment`、`/annotations`、`/annotation-tasks`）。本類別取代它們。
 */

// MARK: - 登入身分

/**
 登入後拿到的身分。**這只是給 UI 用的**——真正的閘門在伺服器端。

 ⚠ 依 `perms` 隱藏或停用功能，是為了讓人看得懂自己能做什麼，不是存取控制。
 App 二進位可以被改、HTTP 請求可以直接偽造，所以每一項權限在後端都有對應檢查。
 */
struct LoginIdentity: Equatable {
    let identity: String        // "<org>:<user>"
    let org: String
    let user: String
    let role: String            // physician / nurse / assistant / engineer / admin
    let roleZh: String
    let displayName: String?
    let perms: Set<String>

    func can(_ perm: String) -> Bool { return perms.contains(perm) }

    /// 標題列顯示用。共用裝置時最常見的錯誤是用上一個人的登入做事——看得到才會發現。
    func label() -> String { return "\(displayName ?? user)（\(roleZh)）" }
}

// MARK: - classify 結果

struct ClassifyResult {
    var areaCm2: Double?
    var tissueFrac: [String: Double]
    var pushPartial: Int?
    var pushFull: Int?
    var confidence: Double
    var route: String
    var escalated: Bool = false

    /// 最大的那一個輪廓（相容用）。座標空間＝`imageW × imageH`。
    var woundPolygon: [[Int]] = []

    /**
     **所有**傷口輪廓（由大到小）。同一肢體多處傷口是臨床常態。

     ⚠ 只用 `woundPolygon` 的話，AI 分割到的第二個傷口在修邊畫面就不會有初始輪廓，
     醫師得自己補畫；沒注意到的話**那個傷口在訓練集裡會被標成背景**——
     沒有錯誤、沒有警告，只有訓練資料悄悄變錯。
     */
    var woundPolygons: [[[Int]]] = []

    /// ArUco 尺度（mm／影像 px）。修邊面積＝像素數 ×(mm/px)² ／100。
    var mmPerPx: Double?

    /**
     ArUco 標記四角（TL, TR, BR, BL），與 `imageW/H` 同座標空間。

     存在的理由不是好看：**ArUco 偵測沒有「認錯了」這個錯誤狀態**——它要嘛回一個四邊形、
     要嘛回 nil。認錯目標（反光、地磚接縫、其他方形印刷）時 `mmPerPx` 就是錯的，
     而**每一筆面積都會安靜地錯**：服務照回 200、畫面照顯示一個合理的數字。
     程式判斷不了，只能讓人看一眼——所以這個欄位必須畫在照片上。
     */
    var markerQuad: [[Int]]?
    var markerMm: Double?
    var calibMethod: String?

    /**
     後端由**校正貼紙中性色塊**算出的白平衡增益 `[R, G, B]`（已乘上曝光係數）。

     ⚠ 端上一定要用這一組，不可自行算灰世界。後端的組織比例已經是用它算的；端上若走
     另一套白平衡，結果欄與修邊畫面會顯示**兩個不同的答案**——而醫師的修正是在修邊畫面
     上做的，那份 GT 會進訓練集。

     `nil` ＝ 這張沒有可用的色卡（貼紙沒入鏡／過曝／過暗）。此時退回灰世界，而灰世界在
     以傷口為主體的近拍照上會把紅色壓掉約 22%（肉芽被低估）。
     */
    var wbGains: [Double]?

    /// 色準校正的診斷訊息。`nil` ＝ 校正成功且無警告。
    var colorCalNote: String?

    // 飛輪資料鏈：image_id ＝ 後端已存影像的內容雜湊；imageW/H ＝ polygon 與修邊 GT 的座標空間。
    // 送標註時必須帶回，否則後端收到的是無影像、無尺寸的孤兒 GT（不可訓練）。
    var imageId: String?
    var imageW: Int = 0
    var imageH: Int = 0
    var segModel: String?

    /// true ＝ AI 沒抓到但色彩分割抓得到 → 幾乎確定把印刷模擬圖誤選成了臨床／範例。
    var phantomHint: Bool = false

    /**
     這批完全相同的位元組後端先前已收過。

     真實回診照片不可能與上次逐位元相同（光線、角度、時間戳都會變），所以臨床模式下
     出現 true 幾乎必然是**重複量測同一張範例／示範圖**——會讓癒合曲線出現假的下降。
     */
    var imageReused: Bool = false

    /// 模擬圖模式實際走了哪一段：`strict` / `gray_world_wb`。
    var phantomPass: String?

    /// 影像品質指標（對焦、過曝、marker 大小與傾斜）。缺遮罩時後端可能整個省略。
    var quality: [String: Double] = [:]

    /// 組織分類用了哪一種白平衡。含 `gray-world` 代表沒吃到色卡。
    var tissueMethod: String?

    /// `tissueMethod` 含 gray-world ＝ 貼紙沒完整入鏡，肉芽會被系統性低估。
    var usedGrayWorldWB: Bool {
        guard let m = tissueMethod else { return false }
        return m.lowercased().contains("gray-world") || m.lowercased().contains("gray_world")
    }
}

// MARK: - 送出標註的結果

enum AnnotationOutcome: Equatable {
    case enqueued(note: String?)
    /// 後端以 200 回覆但實際未入列。**不是錯誤**，但一定要讓醫師看到，
    /// 否則他會以為剛才的修改已經存進去了。
    case duplicateSkipped(note: String)
    case rejected(message: String)
}

// MARK: - 錯誤

enum BackendError: Error, LocalizedError {
    case notLoggedIn
    case http(status: Int, message: String)
    case badResponse(String)
    case missingImageBinding

    var errorDescription: String? {
        switch self {
        case .notLoggedIn:
            return "尚未登入後端"
        case .http(let s, let m):
            return m.isEmpty ? "後端回應 HTTP \(s)" : m
        case .badResponse(let m):
            return "回應解析失敗：\(m)"
        case .missingImageBinding:
            return "缺影像綁定（image_id／尺寸）；請重新以後端模式量測一次再送出"
        }
    }
}

// MARK: - 客戶端

actor BackendClient {

    private let baseUrl: String
    private var jwt: String
    private(set) var identity: LoginIdentity?

    private let session: URLSession

    /**
     - Parameter baseUrl: 例如 `https://wound-ai-…run.app`，尾端斜線會被去掉。

     逾時設定對齊 Android 的 OkHttp：
     - `timeoutIntervalForRequest = 90s` — escalate 的 classify 要跑 student ＋ A∪U 兩個模型，
       Cloud Run 冷啟動時實測超過 10 秒；用預設的 60 秒會在冷啟動當下逾時，
       而那正是醫師第一次按下去的時刻。
     - `timeoutIntervalForResource = 120s` — 對應 OkHttp 的 callTimeout。
     */
    init(baseUrl: String, jwt: String = "") {
        self.baseUrl = BackendClient.normalize(baseUrl)
        self.jwt = jwt
        let cfg = URLSessionConfiguration.ephemeral
        cfg.timeoutIntervalForRequest = 90
        cfg.timeoutIntervalForResource = 120
        cfg.waitsForConnectivity = false
        cfg.httpAdditionalHeaders = ["Accept": "application/json"]
        self.session = URLSession(configuration: cfg)
    }

    static func normalize(_ raw: String) -> String {
        var s = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if s.isEmpty { return s }
        if !s.lowercased().hasPrefix("http://") && !s.lowercased().hasPrefix("https://") {
            s = "https://" + s
        }
        while s.hasSuffix("/") { s.removeLast() }
        return s
    }

    var isLoggedIn: Bool { return !jwt.isEmpty }

    func currentIdentity() -> LoginIdentity? { return identity }

    // MARK: - 請求組裝

    private func request(_ method: String, _ path: String, auth: Bool = true) throws -> URLRequest {
        guard let url = URL(string: baseUrl + path) else {
            throw BackendError.badResponse("網址不合法：\(baseUrl + path)")
        }
        var r = URLRequest(url: url)
        r.httpMethod = method
        if auth {
            guard !jwt.isEmpty else { throw BackendError.notLoggedIn }
            r.setValue("Bearer \(jwt)", forHTTPHeaderField: "Authorization")
        }
        return r
    }

    private func send(_ req: URLRequest) async throws -> (Int, Data) {
        let (data, resp) = try await session.data(for: req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? -1
        return (code, data)
    }

    /**
     把後端回應整理成人看得懂的一行字；解析失敗才退回原文。

     ⚠ 直接把 raw JSON 顯示給醫師，中文會變成一整串 `\uXXXX` 逃脫碼（Android 端實機截圖
     確認過，完全不可讀）。後端的 `issues[]` 本來就是寫給臨床人員看的中文，逐條攤開即可。
     */
    private func summarize(_ data: Data) -> String {
        guard let j = JSONAny(data: data) else {
            return String(data: data.prefix(200), encoding: .utf8) ?? ""
        }
        let issues = j["issues"].array.compactMap { $0.string }
        if !issues.isEmpty { return issues.joined(separator: "；") }
        if let e = j["error"].string { return e }
        if j["status"].string == "duplicate_skipped" {
            return "duplicate:" + j["note"].string("")
        }
        if let n = j["note"].nonBlankString { return n }
        if let s = j["status"].string { return s }
        return String(data: data.prefix(200), encoding: .utf8) ?? ""
    }

    // MARK: - ① 登入

    /// `POST /api/auth/login`。成功回 true 並保存 token 與身分。
    @discardableResult
    func login(username: String, password: String) async throws -> Bool {
        var r = try request("POST", "/api/auth/login", auth: false)
        r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        r.httpBody = try JSONSerialization.data(
            withJSONObject: ["username": username, "password": password])

        let (code, data) = try await send(r)
        guard code == 200, let j = JSONAny(data: data) else {
            identity = nil
            return false
        }
        let tok = j["access_token"].string("")
        if tok.isEmpty { identity = nil; return false }
        jwt = tok
        identity = LoginIdentity(
            identity:    j["identity"].string(username),
            org:         j["org"].string("default"),
            user:        j["user"].string(username),
            role:        j["role"].string(""),
            roleZh:      j["role_zh"].string(j["role"].string("")),
            displayName: j["display_name"].string,     // JSON null → nil
            perms:       Set(j["perms"].array.compactMap { $0.string })
        )
        return true
    }

    func logout() {
        jwt = ""
        identity = nil
    }

    // MARK: - ② 健康度

    struct Health {
        let status: String              // healthy | degraded
        let degraded: Bool
        let degradedReason: String?
        let classifyModules: Bool
        let colorCalibration: Bool
        let segmentationModel: Bool
        let version: String?
    }

    /// `GET /api/health`（免認證，Cloud Run 探針也用它）。
    func health() async throws -> Health {
        let r = try request("GET", "/api/health", auth: false)
        let (_, data) = try await send(r)
        guard let j = JSONAny(data: data) else { throw BackendError.badResponse("health") }
        let status = j["status"].string("unknown")
        return Health(
            status: status,
            degraded: status == "degraded",
            degradedReason: j["degraded_reason"].nonBlankString,
            classifyModules:   j["services"]["classify_modules"].bool(false),
            colorCalibration:  j["services"]["color_calibration"].bool(false),
            segmentationModel: j["services"]["segmentation_model"].bool(false),
            version: j["version"].string
        )
    }

    // MARK: - ③ 一次性登入碼

    /**
     取一個一次性登入碼，把「已登入的身分」交給同一台裝置上的瀏覽器。

     ⚠ **不可改成直接把 jwt 放進網址。** Cloud Run 會把完整請求 URL 寫進 Cloud Logging，
     查詢字串裡的 token 就以明文躺在日誌裡，任何具 log viewer 權限的人都能複製它冒用
     這位醫師的身分，而效期還有 24 小時。

     一次性碼要放在 URL **fragment**（`#c=…`）——fragment 不送到伺服器，因此不進任何
     伺服器日誌；效期 60 秒、用過即失效，且後端擋住它去打任何一般端點。

     回 `nil` 表示拿不到（後端太舊、網路失敗、token 過期）；呼叫端應退回
     「開網址但要手動登入」。
     */
    func oneTimeCode() async -> String? {
        do {
            var r = try request("POST", "/api/v1/auth/onetime")
            r.setValue("application/json", forHTTPHeaderField: "Content-Type")
            r.httpBody = Data("{}".utf8)
            let (code, data) = try await send(r)
            guard code == 200, let j = JSONAny(data: data) else { return nil }
            return j["code"].nonBlankString
        } catch {
            return nil
        }
    }

    /// 主控台網址。碼放 fragment，見 `oneTimeCode()`。
    func consoleURL(oneTimeCode code: String?) -> URL? {
        guard let c = code else { return URL(string: baseUrl + "/console") }
        guard let esc = c.addingPercentEncoding(withAllowedCharacters: .alphanumerics) else {
            return URL(string: baseUrl + "/console")
        }
        return URL(string: baseUrl + "/console#c=" + esc)
    }

    // MARK: - ④ 飛輪佇列健康度

    struct FlywheelStats {
        let total: Int
        let trainable: Int
        let withdrawn: Int
        let superseded: Int
        let orphanNoImage: Int
        let imageFileMissing: Int
        let bySource: [String: Int]

        /// **收案進度看這個**，不是 `trainable` 總數——範例／模擬圖走同一條管線收進來，
        /// 混在一起看會讓臨床樣本數被灌水。
        var clinical: Int { return bySource["clinical"] ?? 0 }
    }

    /// `GET /api/v1/flywheel/stats[?source=…]`。回 (成功, 給人看的摘要, 結構化統計)。
    func flywheelStats(source: String? = nil) async -> (Bool, String, FlywheelStats?) {
        do {
            var path = "/api/v1/flywheel/stats"
            if let s = source, !s.isEmpty { path += "?source=\(s)" }
            let r = try request("GET", path)
            let (code, data) = try await send(r)
            guard code == 200 else { return (false, summarize(data), nil) }
            guard let root = JSONAny(data: data) else {
                return (false, "回應解析失敗", nil)
            }
            // 後端目前把統計放在頂層；舊版曾包在 "stats" 底下。兩者都吃。
            let s = root["stats"].exists ? root["stats"] : root
            var by: [String: Int] = [:]
            for k in ["clinical", "sample", "phantom", "external"] {
                by[k] = s["by_source"][k].int(0)
            }
            let stats = FlywheelStats(
                total:            s["total"].int(0),
                trainable:        s["trainable"].int(0),
                withdrawn:        s["withdrawn"].int(0),
                superseded:       s["superseded"].int(0),
                orphanNoImage:    s["orphan_no_image"].int(0),
                imageFileMissing: s["image_file_missing"].int(0),
                bySource: by
            )
            let text = """
            可訓練 \(stats.trainable) 筆／佇列共 \(stats.total) 筆
            臨床 \(by["clinical"] ?? 0)・範例 \(by["sample"] ?? 0)・模擬圖 \(by["phantom"] ?? 0)・外部 \(by["external"] ?? 0)
            已撤回 \(stats.withdrawn)・被取代 \(stats.superseded)・孤兒GT \(stats.orphanNoImage)・影像遺失 \(stats.imageFileMissing)
            """
            return (true, text, stats)
        } catch {
            return (false, error.localizedDescription, nil)
        }
    }

    // MARK: - ⑤ classify（五階段主端點）

    /**
     `POST /api/v1/classify`。

     - Parameter seg: `nil`/`"auto"` ＝ AI 分割（student → 難例自動 A∪U 集成）；
       `"color"` ＝ 印刷模擬圖走決定性 HSV 色彩分割，**完全不碰模型**。
       印刷色塊是分布外樣本，模型實測回空遮罩；而驗證量測鏈本來也不該拿 AI 當量尺。

     ⚠ **一律上傳原始影像，不要先自行套白平衡。** 後端會在收到的影像上再做一次
     gray-world；兩層白平衡疊起來的結果沒有人推得出來，而且它不會報錯。
     */
    func classify(jpeg: Data, cmPerPixel: Double? = nil, seg: String? = nil,
                  escalate: Bool = true) async throws -> ClassifyResult {
        var fields: [String: String] = [:]
        if let c = cmPerPixel { fields["cm_per_pixel"] = String(c) }
        if let s = seg { fields["seg"] = s }
        if !escalate { fields["escalate"] = "off" }

        var r = try request("POST", "/api/v1/classify")
        let boundary = "----WoundAI\(UUID().uuidString)"
        r.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        r.httpBody = Self.multipartBody(boundary: boundary, fields: fields,
                                        fileField: "image", fileName: "wound.jpg",
                                        mime: "image/jpeg", fileData: jpeg)

        let (code, data) = try await send(r)
        guard code == 200 else {
            throw BackendError.http(status: code, message: summarize(data))
        }
        guard let j = JSONAny(data: data) else {
            throw BackendError.badResponse("classify")
        }
        return Self.parseClassify(j)
    }

    /// 拆出來是為了讓契約測試可以在沒有網路的情況下直接餵 JSON 驗證解析。
    static func parseClassify(_ j: JSONAny) -> ClassifyResult {
        let s2 = j["stage2_segment"]
        let s3 = j["stage3_calibrate"]
        let s4 = j["stage4_tissue"]
        let s5 = j["stage5_severity"]
        let cc = j["stage3b_colorcal"]

        var tissue: [String: Double] = [:]
        for k in ["necrosis", "slough", "granulation", "epithelial", "other"] {
            tissue[k] = s4["tissue_frac"][k].double(0.0)
        }

        // 色準校正（stage3b）。舊版後端沒有這個鍵——這裡必須容忍缺席，
        // 否則 App 一連上舊後端 classify 就整支失敗。
        var gains: [Double]?
        if cc.exists, cc["ok"].bool(false) {
            let e = cc["exposure"].double(1.0)
            gains = [cc["gain_r"].double(1.0) * e,
                     cc["gain_g"].double(1.0) * e,
                     cc["gain_b"].double(1.0) * e]
        }
        var ccNote: String?
        if cc.exists {
            ccNote = cc["reason"].nonBlankString ?? (cc["ok"].bool(false) ? nil : "色準校正未執行")
        }

        var quality: [String: Double] = [:]
        if let q = j["quality"].raw as? [String: Any] {
            for (k, v) in q {
                if let n = v as? NSNumber { quality[k] = n.doubleValue }
            }
        }

        // 舊後端沒有 `wound_polygons` 這個鍵 → 退回單一輪廓。
        // 這裡必須容忍缺席，否則 App 一連上舊後端 classify 就整支失敗。
        let polyMain = s2["wound_polygon"].intPointList
        let polyAll: [[[Int]]] = {
            let arr = s2["wound_polygons"].array.map { $0.intPointList }.filter { $0.count >= 3 }
            if !arr.isEmpty { return arr }
            return polyMain.count >= 3 ? [polyMain] : []
        }()

        let quad: [[Int]]? = {
            guard s3["marker_quad"].exists else { return nil }
            let pts = s3["marker_quad"].intPointList
            return pts.count == 4 ? pts : nil
        }()

        return ClassifyResult(
            areaCm2:      s3["area_cm2"].double,
            tissueFrac:   tissue,
            pushPartial:  s5["total_partial_img"].int,
            pushFull:     s5["total_full"].int,
            confidence:   s2["confidence"].double(0.0),
            // 後端雙軌路由：student（端上主力）或 cloud_escalated(AU)（難例自動上雲集成）
            route:        s2["route"].string("cloud"),
            escalated:    s2["escalated"].bool(false),
            woundPolygon: polyMain,
            woundPolygons: polyAll,
            mmPerPx:      s3["mm_per_px"].double,
            markerQuad:   quad,
            markerMm:     s3["marker_mm"].double,
            calibMethod:  s3["method"].string,
            wbGains:      gains,
            colorCalNote: ccNote,
            imageId:      j["image_id"].string,        // JSON null → nil（＝不得送訓練標註）
            imageW:       j["image_w"].int(0),
            imageH:       j["image_h"].int(0),
            segModel:     s2["model"].string,
            phantomHint:  j["phantom_hint"].bool(false),
            imageReused:  j["image_reused"].bool(false),
            phantomPass:  j["phantom_pass"].string,
            quality:      quality,
            tissueMethod: s4["method"].string
        )
    }

    // MARK: - ⑥ 送出訓練標註

    /**
     `POST /api/v1/annotation`。

     後端守門：`code` 需符合 `^WD-…$`、`gt_polygon` ≥3 點且在影像範圍內、
     `doctor_verified` / `deidentified` / `consent_train` 皆為 true、`image_id` 為 16 位小寫 hex
     且後端存有該影像。

     - Parameter consentTrain: ②訓練同意的**真值**，來自本機 `Consent.trainEffective`。
       ⚠ 這個欄位在 Android 上曾經硬編碼 `true`——等於每一筆送出都謊稱已取得訓練同意，
       而實際上從沒有人勾過。iOS 不可重蹈。
     - Parameter doctorVerified: 醫師是否**真的完成過修邊確認**。
       ⚠ 同一類缺陷：醫師在修邊頁按「取消」後仍可送出，於是一筆從未被人看過的 AI 輸出
       會以「醫師已驗證」的身分進入訓練集。飛輪的整個前提是 GT 來自人的判斷。
     */
    func submitAnnotation(
        code: String,
        gtPolygon: [[Int]],
        /// 全部輪廓。多於一個時另外送 `gt_polygons`；`gt_polygon` 保留最大的那個供舊後端。
        allPolygons: [[[Int]]] = [],
        exudate: Int?,
        /// 修邊後的面積（以遮罩像素為真值）。與 Android 的 `area_cm2` 同義。
        areaCm2: Double? = nil,
        imageId: String?,
        imageW: Int,
        imageH: Int,
        mmPerPx: Double? = nil,
        route: String? = nil,
        segModel: String? = nil,
        tissueFrac: [String: Double]? = nil,
        tissueMaskPngBase64: String? = nil,
        tissueRaster: EditRaster? = nil,
        correctionIou: Double? = nil,
        careNote: String? = nil,
        source: String? = nil,
        quality: [String: Double]? = nil,
        consentTrain: Bool,
        doctorVerified: Bool
    ) async throws -> AnnotationOutcome {

        // 沒有 image_id／尺寸就送出 ＝ 產生孤兒 GT（後端會 400）。提早在端上擋下並給明確訊息。
        guard let imageId = imageId, !imageId.isEmpty, imageW > 0, imageH > 0 else {
            throw BackendError.missingImageBinding
        }

        var obj: [String: Any] = [
            "code":            code,
            "gt_polygon":      gtPolygon.map { [$0.count > 0 ? $0[0] : 0, $0.count > 1 ? $0[1] : 0] },
            "exudate":         exudate as Any? ?? NSNull(),
            "doctor_verified": doctorVerified,
            "deidentified":    true,
            "consent_train":   consentTrain,
            "image_id":        imageId,
            "image_w":         imageW,
            "image_h":         imageH
        ]
        // 多輪廓。`gt_polygon` 保留最大的那一個供舊後端使用——新舊後端都收得下。
        let polysOut = allPolygons.filter { $0.count >= 3 }
        if polysOut.count > 1 {
            obj["gt_polygons"] = polysOut.map { poly in
                poly.map { [$0.count > 0 ? $0[0] : 0, $0.count > 1 ? $0[1] : 0] }
            }
        }
        if let v = areaCm2       { obj["area_cm2"] = v }
        if let v = mmPerPx       { obj["mm_per_px"] = v }
        if let v = route         { obj["route"] = v }
        if let v = segModel      { obj["seg_model"] = v }
        if let v = correctionIou { obj["correction_iou"] = v }
        if let v = careNote      { obj["care_note"] = v }
        if let v = source        { obj["source"] = v }
        if let v = quality, !v.isEmpty { obj["quality"] = v }

        // WoundAI3D 預留：iOS 有 LiDAR，但在深度擷取鏈通過驗證之前一律送 none。
        // **明確標記**比欄位缺席好——日後分析時「沒拍深度」與「拍了但沒存」要分得出來。
        obj["depth_source"] = "none"
        obj["capture_device"] = Self.deviceModelString()

        // 醫師修邊後的組織比例（含「其他」）。不是分割 GT，是未來訓練組織分類的種子——
        // 影像會依保存政策清除，事後無法回溯重算，所以現在就收。
        if let tf = tissueFrac, !tf.isEmpty { obj["tissue_frac"] = tf }

        // 組織分割 GT。**tissue_edited 一定要跟著送**——後端靠它決定這張遮罩能不能進訓練集。
        // 少了它，未經醫師修正的啟發式輸出會安靜地混進去，而模型就在學習複製它自己已經
        // 會做的事。
        if let png = tissueMaskPngBase64, let r = tissueRaster {
            obj["tissue_mask_png"] = png
            obj["tissue_raster"] = [
                "rx0": Double(r.rx0), "ry0": Double(r.ry0),
                "mw": r.mw, "mh": r.mh, "m_scale": Double(r.mScale)
            ]
            obj["tissue_edited"]     = r.tissueEdited
            obj["tissue_edit_px"]    = r.tissueEditedPx
            obj["tissue_edit_ratio"] = r.tissueEditRatio
        }

        var req = try request("POST", "/api/v1/annotation")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONSerialization.data(withJSONObject: obj)

        let (httpCode, data) = try await send(req)
        let msg = summarize(data)
        guard httpCode == 200 else { return .rejected(message: msg) }

        guard let j = JSONAny(data: data) else { return .enqueued(note: nil) }
        // ⚠ duplicate_skipped 是 **HTTP 200**，而且回應裡**沒有** image_id / queue 欄位。
        //   解析時不可假設欄位存在。
        if j["status"].string == "duplicate_skipped" {
            return .duplicateSkipped(note: j["note"].string(
                "此筆與佇列中既有樣本的影像、傷口輪廓、組織遮罩三者皆相同，已自動略過。"))
        }
        return .enqueued(note: j["note"].nonBlankString)
    }

    // MARK: - ⑦ 同意：撤回／重新取得

    /// `POST /api/v1/consent/withdraw`。
    func withdrawConsent(code: String, imageId: String? = nil) async -> Bool {
        return await consentCall(path: "/api/v1/consent/withdraw", code: code, imageId: imageId)
    }

    /**
     `POST /api/v1/consent/restore`。

     ⚠ 沒有這一支，撤回就是**死局**：病患改變主意重新簽署後，App 顯示「訓練同意 ✓」，
     而雲端仍以「已撤回訓練同意」擋下每一次送出——錯誤訊息還會把內部端點路徑直接印給
     醫師看，而他沒有辦法自己呼叫它。
     */
    func restoreConsent(code: String, imageId: String? = nil) async -> Bool {
        return await consentCall(path: "/api/v1/consent/restore", code: code, imageId: imageId)
    }

    private func consentCall(path: String, code: String, imageId: String?) async -> Bool {
        do {
            var body: [String: Any] = ["code": code]
            if let i = imageId { body["image_id"] = i }
            var r = try request("POST", path)
            r.setValue("application/json", forHTTPHeaderField: "Content-Type")
            r.httpBody = try JSONSerialization.data(withJSONObject: body)
            let (code, _) = try await send(r)
            return code == 200
        } catch {
            return false
        }
    }

    // MARK: - 工具

    static func multipartBody(boundary: String, fields: [String: String],
                              fileField: String, fileName: String,
                              mime: String, fileData: Data) -> Data {
        var body = Data()
        func append(_ s: String) { body.append(Data(s.utf8)) }

        // 欄位順序固定（排序），讓失敗時的封包在不同執行之間可比對。
        for (k, v) in fields.sorted(by: { $0.key < $1.key }) {
            append("--\(boundary)\r\n")
            append("Content-Disposition: form-data; name=\"\(k)\"\r\n\r\n")
            append("\(v)\r\n")
        }
        append("--\(boundary)\r\n")
        append("Content-Disposition: form-data; name=\"\(fileField)\"; filename=\"\(fileName)\"\r\n")
        append("Content-Type: \(mime)\r\n\r\n")
        body.append(fileData)
        append("\r\n--\(boundary)--\r\n")
        return body
    }

    /// `capture_device`：日後查「某機型面積系統性偏高」的唯一依據。
    static func deviceModelString() -> String {
        var sysinfo = utsname()
        uname(&sysinfo)
        let machine = withUnsafeBytes(of: &sysinfo.machine) { raw -> String in
            let bytes = raw.bindMemory(to: CChar.self)
            return String(decodingCString: bytes.baseAddress!, as: UTF8.self)
        }
        return "Apple \(machine)"
    }
}
