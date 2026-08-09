import SwiftUI
import PhotosUI
import UIKit   // UIGraphicsImageRenderer（normalizeForBackend 的縮圖與方向烘焙）
import UniformTypeIdentifiers   // fileImporter 的 UTType.image

/**
 量測主流程（對等 Android `MeasureValidationEntry` + `SamplePickerScreen` + `MeasureScreen`）。

 流程：登入 → 選圖／拍照 → `POST /api/v1/classify` → **校正框目視複核** → 滲液輸入
 → 存入時間軸 →（修邊後）送訓練標註。
 */

@MainActor
final class MeasureViewModel: ObservableObject {
    @Published var loading = false
    @Published var error: String?
    @Published var result: ClassifyResult?
    @Published var image: UIImage?
    @Published var exudate: Int?
    @Published var source: String = "clinical"
    @Published var statusNote: String?

    /// 醫師是否真的完成過修邊確認。**只有修邊畫面按下「完成」才可設為 true。**
    /// 預設 false 是刻意的 fail-closed：從未被人看過的 AI 輸出不得以「醫師已驗證」送出。
    @Published private(set) var doctorVerified = false

    @Published var raster: EditRaster?

    /// 醫師修邊後的**所有**輪廓（取代 AI 的 `result.woundPolygons`）。nil ＝ 沒修過。
    @Published private(set) var editedPolygons: [[[Int]]]?
    /// 修邊相對 AI 原始遮罩的 IoU（1.0 ＝ 沒改）。隨標註送出，供評估模型修正幅度。
    @Published private(set) var correctionIou: Double?
    /// 目前有效的輪廓集合：修邊過用醫師的，否則用 AI 的。畫參照圖與送標註都讀這個。
    var effectivePolygons: [[[Int]]] { return editedPolygons ?? result?.woundPolygons ?? [] }

    /// 本輪量測已經寫進 `measurements` 的那一列。**非 nil ＝ 再存一次要走 UPDATE。**
    /// 換影像時必須清掉（見 `analyze`），否則下一張照片會覆蓋上一張的病歷列。
    @Published private(set) var lastSavedId: Int64?
    @Published private(set) var saving = false
    @Published var saveNote: String?

    /// 修邊畫面開關（fullScreenCover）。
    @Published var editing = false
    /// 送訓練標註的狀態。✅／ℹ️／⚠️ 開頭的會被彈窗攔下強制確認（一行綠字實測會被錯過）。
    @Published var submitStatus: String?

    private var backend: BackendClient?

    func ensureLogin() async -> Bool {
        let user = AppSettings.backendUser(), pass = AppSettings.backendPassword()
        guard !user.isEmpty, !pass.isEmpty else {
            error = "尚未設定後端帳號密碼，請先到「設定」填寫。"
            return false
        }
        let c = BackendClient(baseUrl: AppSettings.backendURL())
        guard (try? await c.login(username: user, password: pass)) == true else {
            error = "後端登入失敗，請檢查位址與帳號密碼。"
            return false
        }
        backend = c
        return true
    }

    /**
     長邊縮到 ≤2048 並**把方向烘進像素**（輸出一律 `.up`）。

     這一步不是省流量的最佳化，是座標空間的正確性前提（對齊 Android `analyzeViaBackend`）：

     1. `gtPolygon`／`imageW`／`imageH`／修邊柵格全部綁「後端看到的那張圖」的座標。
        送原圖讓後端自己縮的話，本機顯示用的圖與座標空間是兩張不同的圖。
     2. iPhone 直拍的 JPEG 靠 EXIF 方向旗標表示旋轉；後端若沒套 exif_transpose，
        輪廓會轉 90°。烘進像素之後不存在這個歧義——Android 的 Bitmap 本來就是像素直立的。
     3. 5712 寬的原圖 ≈ 70MB ARGB；反覆編修會 OOM。

     ⚠ `format.scale = 1` 不可省：預設是螢幕倍率（3x），輸出會是要求尺寸的 9 倍像素。
     */
    static func normalizeForBackend(_ img: UIImage, maxDim: CGFloat = 2048) -> UIImage {
        let pw = CGFloat(img.cgImage?.width ?? Int(img.size.width))
        let ph = CGFloat(img.cgImage?.height ?? Int(img.size.height))
        // 方向旗標非 .up 時就算尺寸夠小也要重畫（烘方向）。
        let needRedraw = img.imageOrientation != .up || max(pw, ph) > maxDim
        if !needRedraw { return img }
        // size 是「顯示方向」下的點尺寸；直拍時 pw/ph 是轉置前的，用 size 才對。
        let dw = img.size.width, dh = img.size.height
        let s = min(1, maxDim / max(dw, dh))
        let target = CGSize(width: (dw * s).rounded(), height: (dh * s).rounded())
        let fmt = UIGraphicsImageRendererFormat()
        fmt.scale = 1
        return UIGraphicsImageRenderer(size: target, format: fmt).image { _ in
            img.draw(in: CGRect(origin: .zero, size: target))
        }
    }

    /// 本輪影像的 LiDAR 深度（WoundAI3D）。nil＝這張不是相機拍的或裝置無 LiDAR。
    /// 換影像時由 `analyze` 一併重設——沿用上一張的深度會把幾何綁到錯的影像。
    @Published private(set) var pendingDepth: DepthCapture?

    /// - Parameter phantom: 印刷模擬圖 → `seg=color`，走決定性 HSV 色彩分割，完全不碰 AI 模型。
    /// - Parameter depth: 相機拍攝當下的深度圖（相簿／檔案來源傳 nil）。
    @Published var loadingHint = "分析中…"

    func analyze(_ img: UIImage, phantom: Bool, depth: DepthCapture? = nil) async {
        // ⚠ 轉圈圈必須在 **ensureLogin 之前**開始：冷啟動吃在登入那一次呼叫上，
        //   之前 loading 是 classify 才開——喚醒的 10–30 秒畫面看起來像當機。
        loading = true; error = nil; statusNote = nil
        loadingHint = AppSettings.backendLikelyCold()
            ? "喚醒雲端中…（閒置會自動縮容，首次約需 10–30 秒，照片已就緒請稍候）"
            : "分析中…（約數秒）"
        defer { loading = false }
        guard await ensureLogin(), let backend else { return }
        AppSettings.markBackendOk()
        loadingHint = "分析中…（上傳與辨識約數秒）"
        let work = Self.normalizeForBackend(img)
        // ⚠ 一律送原始像素、不自行套白平衡。先套的話，後端會在已校正的圖上再做一次
        //   gray-world，兩層疊起來的結果沒有人推得出來，而且它不會報錯。
        guard let jpeg = work.jpegData(compressionQuality: 0.92) else {
            error = "影像編碼失敗"; return
        }
        do {
            var r = try await backend.classify(jpeg: jpeg, seg: phantom ? "color" : nil)
            AppSettings.markBackendOk()
            // ArUco 認到貼紙時，把「大部分落在貼紙上的傷口輪廓」自動排除——
            // 印刷黑白方塊常被分割當成壞死組織（實機 2026-08-09 發生：貼紙被當成多傷口之一）。
            // 排除必須**說出來**且可挽回：門檻取「≥70% 的點落在貼紙外擴 15% 框內」
            // 才剔（只沾到邊的不動），並提示醫師可在修邊畫面手動補畫。
            var markerNote: String?
            if let quad = r.markerQuad, quad.count == 4, !r.woundPolygons.isEmpty {
                let xs = quad.map { $0[0] }, ys = quad.map { $0[1] }
                let x0 = Double(xs.min()!), x1 = Double(xs.max()!)
                let y0 = Double(ys.min()!), y1 = Double(ys.max()!)
                let ex = (x1 - x0) * 0.15, ey = (y1 - y0) * 0.15
                func onMarker(_ poly: [[Int]]) -> Bool {
                    guard !poly.isEmpty else { return false }
                    let inside = poly.filter {
                        Double($0[0]) >= x0 - ex && Double($0[0]) <= x1 + ex &&
                        Double($0[1]) >= y0 - ey && Double($0[1]) <= y1 + ey
                    }.count
                    return Double(inside) / Double(poly.count) >= 0.7
                }
                let kept = r.woundPolygons.filter { !onMarker($0) }
                let dropped = r.woundPolygons.count - kept.count
                if dropped > 0 {
                    r.woundPolygons = kept
                    r.woundPolygon = PolygonJson.largest(kept)
                    markerNote = "⚠ 已自動排除 \(dropped) 個與校正貼紙重疊的辨識區域"
                        + "（AI 把貼紙認成了傷口）。若貼紙旁確有傷口，請在「醫師確認・修邊」手動補畫。"
                }
            }
            // 存 work 而非原圖：畫面顯示、修邊畫布、加密落地的都必須是
            // 「與 gtPolygon 同座標空間的那張圖」。存原圖的話，座標會對到另一張圖，
            // 面積仍是一個合理的數字（Android 修過這個 bug：實測差 3.8 倍）。
            image = work
            result = r
            // 深度隨影像成對更新；記下 RGB work 尺寸讓深度圖與輪廓共用同一座標宣告。
            var d = depth
            d?.rgbWidth = r.imageW
            d?.rgbHeight = r.imageH
            pendingDepth = d
            // 換了影像就作廢上一輪的修邊與醫師背書。
            raster = nil
            doctorVerified = false
            editedPolygons = nil
            correctionIou = nil
            // ⚠ `lastSavedId` 一定要一起清。留著的話，下一張照片按「存入時間軸」
            //   會走 UPDATE **覆蓋掉上一張的病歷列**——回診紀錄就這樣被吃掉一筆，
            //   而畫面上只會顯示「已更新」。
            lastSavedId = nil
            saveNote = nil
            var notes = Self.advisories(r, clinical: source == "clinical")
            if let n = markerNote { notes.insert(n, at: 0) }
            statusNote = notes.joined(separator: "\n")
        } catch {
            self.error = error.localizedDescription
        }
    }

    /// 必須讓醫師看到的提示。每一條都對應一種「服務回 200 但數字是錯的」的情形。
    static func advisories(_ r: ClassifyResult, clinical: Bool) -> [String] {
        var out: [String] = []
        if r.imageId == nil {
            out.append("⚠ 這張影像未取得 image_id（可能已撤回訓練同意，或後端存檔失敗）。"
                     + "本次量測可以存入病歷，但**不能**送出訓練標註。")
        }
        if r.imageReused && clinical {
            // 真實回診照片不可能與上次逐位元相同——出現這個幾乎必然是重複選了同一張示範圖，
            // 而它會在癒合曲線上畫出一段假的下降。
            out.append("⚠ 後端先前已收過完全相同的影像。臨床模式下這通常代表選錯了照片"
                     + "（重複使用範例／示範圖）。請確認這是本次回診拍攝的影像。")
        }
        if r.markerQuad == nil {
            out.append("ℹ 未偵測到校正貼紙，面積為未校正狀態。請確認貼紙完整入鏡且清晰。")
        }
        if r.usedGrayWorldWB {
            out.append("⚠ 沒有吃到色卡，白平衡退回 gray-world：紅色會被系統性壓抑（實測約 ×0.78），"
                     + "肉芽比例可能被低估。請確認校正貼紙完整入鏡。")
        }
        if r.phantomHint {
            out.append("⚠ AI 得到空遮罩但色彩分割抓得到——這幾乎確定是把印刷模擬圖選成了臨床／範例影像。")
        }
        if r.confidence < 0.70 {
            out.append("ℹ 信心度偏低（\(Int(r.confidence * 100))%），請務必人工複核輪廓。")
        }
        return out
    }

    /**
     修邊完成（對等 Android `applyEditedPolygon`）：覆寫 GT 輪廓、記 IoU、
     以修邊後「面積＋組織」重算 PUSH → 更新結果卡。**取消不會走到這裡**——
     只有這一條路徑能把 `doctorVerified` 設成 true。
     */
    func applyEdited(polygon: [[Int]], all: [[[Int]]], iou: Double?, newArea: Double?,
                     tissue: [String: Double], raster: EditRaster) {
        self.raster = raster
        self.editedPolygons = all.isEmpty
            ? (polygon.count >= 3 ? [polygon] : nil)
            : all
        self.correctionIou = iou
        self.doctorVerified = true
        if var r = result {
            let area = newArea ?? r.areaCm2
            r.areaCm2 = area
            r.tissueFrac = tissue
            // 也要覆寫單輪廓欄位：AnalysisPreview 的舊路徑讀它。
            r.woundPolygon = polygon
            r.woundPolygons = self.editedPolygons ?? r.woundPolygons
            let p = WoundPipeline.push(cm2: area, frac: tissue, exudate: exudate)
            r.pushPartial = p.partial
            r.pushFull = p.full
            result = r
        }
        saveNote = String(format: "已套用修邊（面積 %@ cm²，修正 IoU %@）",
                          newArea.map { String(format: "%.2f", $0) } ?? "—",
                          iou.map { String(format: "%.2f", $0) } ?? "—")
    }

    // MARK: - 存入時間軸

    /// 時間軸列表上那一行摘要。格式與 Android `saveToTimeline` 對齊，兩端的紀錄要能互相看懂。
    static func timelineNote(push: Int?, frac: [String: Double], exudate: Int?,
                             route: String, iou: Double?) -> String {
        func pct(_ v: Double?) -> String { return "\(Int(((v ?? 0) * 100).rounded()))%" }
        var s = "PUSH \(push.map(String.init) ?? "—")"
        s += "; 肉芽 \(pct(frac["granulation"]))"
        s += " 腐肉 \(pct(frac["slough"]))"
        s += " 壞死 \(pct(frac["necrosis"]))"
        s += "; 滲液 \(exudate.map(String.init) ?? "未填")"
        s += "; route \(route)"
        if let i = iou { s += String(format: "; 修邊IoU %.2f", i) }
        return s
    }

    /**
     把本輪量測寫進病歷，並把影像加密落地。

     ## 這一支之前不存在的後果

     `insertMeasurement` 與 `LocalImageStore` 都寫好了，但整個 build 裡沒有任何呼叫端——
     所以 `measurements` 表從安裝到現在一直是空的，傷口照片一張都沒有落地，
     分析結果只活在記憶體裡，選下一張照片的瞬間就沒了。App 能告訴你面積和 PUSH，
     然後把答案丟掉。**那是示範工具，不是病歷系統。**

     ## 三個不可調換的順序／取捨

     1. **影像一律從目前畫面重存，絕不沿用舊檔。** `gtPolygon` / `imageW` / `imageH`
        的座標空間綁的是「這一份影像」；沿用舊檔會讓座標對到另一張圖，
        面積算出來仍是一個合理的數字（Android 實測曾差 3.8 倍），沒有任何報錯。
     2. **先寫資料庫、再刪舊檔。** 反過來的話，刪檔成功而寫入失敗時，
        資料庫留下指向不存在檔案的死路徑，畫面只顯示「載入失敗」。
     3. **修邊過就以柵格為準**（面積、組織比例、IoU）。存 AI 原始輸出的話，
        醫師會以為自己的修正被記下來了，而時間軸上是沒改過的數字。
     */
    @discardableResult
    func saveToTimeline(repo: CaseRepository, imageStore: LocalImageStore,
                        woundCase: WoundCase?) async -> Bool {
        guard let r = result, let img = image else {
            saveNote = "尚未完成量測，沒有可存的結果。"
            return false
        }
        saving = true
        defer { saving = false }

        // (1) 影像重存。失敗時 imagePath 留空字串而非中止——量測數字本身仍有病歷價值，
        //     沒有照片的紀錄比沒有紀錄好。
        let newImagePath = imageStore.save(image: img, quality: 0.9) ?? ""

        // WoundAI3D：深度 sidecar 跟著影像落地（加密、與影像同保存政策）。
        // 失敗只記到 saveNote 尾巴，不擋量測存檔——深度是研究資料，不是病歷必要件。
        var depthNote = ""
        if let d = pendingDepth, !newImagePath.isEmpty {
            if DepthStore.attach(imagePath: newImagePath, capture: d, store: imageStore) {
                depthNote = "（含 LiDAR 深度）"
            } else {
                depthNote = "（⚠ 深度存檔失敗，本筆無深度）"
            }
        }

        // (3) 修邊過就以柵格為準。
        let frac = raster?.tissueFrac() ?? r.tissueFrac
        let area = raster?.areaCm2 ?? r.areaCm2
        var rasterPath: String?
        var rasterMeta: String?
        if let ras = raster, let enc = EditRasterCodec.encode(ras) {
            let (png, meta) = enc
            rasterPath = imageStore.saveRaw(png)
            // `rasterPath` 與 `rasterMeta` 必須成對。只有 meta 沒有 PNG 的話，
            // decode 會回 nil（畫面顯示「沒有修邊紀錄」），而 correctionIou 又顯示
            // 醫師改過——兩個說法互相矛盾，且沒有辦法判斷哪一個是對的。
            rasterMeta = rasterPath == nil ? nil : meta
        }

        var m = Measurement()
        m.patientId = woundCase?.patientId
        m.timestamp = Date()
        // nil 面積代表未校正（無貼紙），此時只能靠組織比例判斷有沒有傷口。
        m.hasWound = (area ?? 0) > 0 || frac.values.contains(where: { $0 > 0 })
        m.confidence = r.confidence
        m.estimatedArea = area
        m.woundTypeLabel = "AI(\(r.route))"
        m.quality = "backend"
        m.imagePath = newImagePath
        m.notes = Self.timelineNote(push: r.pushPartial, frac: frac, exudate: exudate,
                                    route: r.route, iou: raster?.correctionIou)
        m.isPatientIdentified = woundCase?.patientId != nil
        m.caseId = woundCase?.id
        m.wdCode = woundCase?.wdCode
        m.imageId = r.imageId
        m.mmPerPx = r.mmPerPx
        m.route = r.route
        m.source = source
        // 多輪廓 JSON（PolygonJson 會過濾 <3 點的退化輪廓；全退化回 nil——
        // `pendingAnnotationCount` 用 `gtPolygon IS NOT NULL` 判斷可否送標註，
        // 寫進退化多邊形等於把算不出面積的 GT 排進佇列）。
        // 修邊過就存醫師的輪廓，沒修過才存 AI 的。
        m.gtPolygon = PolygonJson.toJson(editedPolygons ?? r.woundPolygons)
        m.imageW = r.imageW
        m.imageH = r.imageH
        m.exudate = exudate
        m.correctionIou = correctionIou ?? raster?.correctionIou
        m.doctorVerified = doctorVerified
        // 組織比例存**當下**的值。影像 90 天後會被清除，之後就再也算不回來了。
        m.tissueGranulation = frac["granulation"]
        m.tissueSlough      = frac["slough"]
        m.tissueNecrosis    = frac["necrosis"]
        m.tissueEpithelial  = frac["epithelial"]
        m.tissueOther       = frac["other"]
        m.rasterPath = rasterPath
        m.rasterMeta = rasterMeta

        if let id = lastSavedId, let old = await repo.measurement(id: id) {
            m.id = id
            // 保留**原始**量測時間。補個滲液就把時間往後推的話，時間軸上的間隔會失真。
            m.timestamp = old.timestamp
            m.annotationSubmitted = old.annotationSubmitted
            // 醫師背書只增不減：這一輪沒重新修邊，不代表上一輪的修邊不算數。
            m.doctorVerified = doctorVerified || old.doctorVerified
            if rasterPath == nil {
                m.rasterPath = old.rasterPath
                m.rasterMeta = old.rasterMeta
                m.correctionIou = old.correctionIou
            }
            // (2) 先寫資料庫，再刪舊檔。舊影像的深度 sidecar 一併清，否則變孤兒密文。
            await repo.updateMeasurement(m)
            if old.imagePath != m.imagePath {
                imageStore.delete(old.imagePath)
                DepthStore.purge(imagePath: old.imagePath, store: imageStore)
            }
            if let op = old.rasterPath, op != m.rasterPath { imageStore.delete(op) }
            saveNote = "已更新這一筆時間軸紀錄。"
            return true
        }

        guard let newId = await repo.insertMeasurement(m) else {
            // 寫入失敗就把剛落地的檔刪掉，否則沙箱裡會留下沒有任何列指得到的孤兒密文。
            imageStore.delete(newImagePath)
            if let p = rasterPath { imageStore.delete(p) }
            saveNote = "存檔失敗，請重試。"
            return false
        }
        lastSavedId = newId
        saveNote = "已存入時間軸。" + depthNote
        return true
    }

    /// 送出條件。任何一項不成立都不該讓按鈕可按——而且要說得出是哪一項。
    var submitBlockedReason: String? {
        guard let r = result else { return "尚未完成量測" }
        if r.imageId == nil { return "缺 image_id，無法綁定影像（見上方提示）" }
        if exudate == nil { return "請先輸入滲液量（0–3）" }
        if !doctorVerified { return "需先完成「醫師確認・修邊」才能送出訓練標註" }
        return nil
    }

    /**
     醫師確認・送出訓練標註（飛輪閉環，對等 Android `MeasureViewModel.submitAnnotation`
     ＋ `DoctorFlywheelSubmit` 的閘門）。

     ⚠ 臨床樣本的②訓練同意在**按下的當下重讀**，不用畫面快照——醫師可能剛在個案頁撤回，
     快照仍是舊的 true，那就等於回到「宣稱已同意但其實沒有」的原始缺陷。

     成功後**刻意不**更新本機 `annotationSubmitted`（與 Android 量測流程一致）：
     那個旗標由時間軸複核頁的補送流程維護，後端本來就會對同影像同遮罩去重。
     */
    func submitTraining(repo: CaseRepository, woundCase: WoundCase?, clinicalMode: Bool) async {
        guard let r = result else { submitStatus = "⚠️ 尚未完成量測"; return }
        let polys = effectivePolygons
        guard let _ = polys.first(where: { $0.count >= 3 }) else {
            submitStatus = "⚠️ 無傷口輪廓可送（請先量測）"; return
        }
        // 醫師沒按過「完成修邊」就送出＝把 AI 原始輸出當人工 GT 灌進訓練集。
        // 後端也會擋（doctor_verified=false → 400），但在這裡擋才給得出有用的訊息。
        guard doctorVerified else {
            submitStatus = "⚠️ 尚未完成醫師修邊確認，不得送訓練標註。\n"
                         + "請按「醫師確認・修邊」並完成後再送（按取消不算確認）。"
            return
        }
        guard let imageId = r.imageId, !imageId.isEmpty else {
            submitStatus = "⚠️ 此結果未綁定後端影像（缺 image_id），送出的會是孤兒 GT，已擋下"
            return
        }
        if clinicalMode {
            guard let c = woundCase else { submitStatus = "⚠️ 臨床樣本需先選個案傷口"; return }
            let fresh = await repo.activeConsent(patientId: c.patientId)?.trainEffective == true
            guard fresh else {
                submitStatus = "⚠️ 此病患的訓練同意已撤回或失效，不得送出訓練標註"; return
            }
        }
        guard await ensureLogin(), let backend else {
            submitStatus = "⚠️ " + (error ?? "後端未連線"); return
        }
        // 臨床用個案的**穩定** wdCode（回診沿用同一組）；範例／模擬圖沒有個案才另發。
        let code = woundCase?.wdCode ?? WoundCase.newWdCode()
        submitStatus = "送出中…"
        do {
            let outcome = try await backend.submitAnnotation(
                code: code,
                gtPolygon: PolygonJson.largest(polys),
                exudate: exudate,
                allPolygons: polys,
                // 面積以遮罩像素為真值，不讓後端由多邊形反算（RDP 有損＋多輪廓要合計，
                // 兩邊各算一次必然對不上——兩個都合理、都沒警告的數字最難查）。
                areaCm2: r.areaCm2,
                imageId: imageId, imageW: r.imageW, imageH: r.imageH,
                mmPerPx: r.mmPerPx, route: r.route, segModel: r.segModel,
                // 修邊完成時 applyEdited 已把醫師確認過的比例寫回 result。
                tissueFrac: r.tissueFrac,
                // 柵格為 nil（理論上不會，doctorVerified 已保證修過）就整組不送——
                // 硬用輪廓補一張遮罩只會製造假 GT。
                tissueMaskPngBase64: raster.flatMap {
                    TissueMaskCodec.encodeBase64(tissue: $0.tissue, mask: $0.mask,
                                                 mw: $0.mw, mh: $0.mh)
                },
                tissueRaster: raster,
                correctionIou: correctionIou,
                careNote: "app confirm",
                source: source,
                depthSource: pendingDepth != nil ? "lidar_local" : "none",
                consentTrain: true,          // 上面已重讀驗證；範例／模擬圖無受試者
                doctorVerified: doctorVerified)
            switch outcome {
            case .rejected(let msg):
                submitStatus = "⚠️ 被守門擋下：\(msg)"
            case .duplicateSkipped(let note):
                submitStatus = "ℹ️ \(note)"
            case .enqueued(let note):
                submitStatus = "✅ 已送出，進再訓練佇列（\(code)）"
                             + (note.map { "\n\($0)" } ?? "")
            }
        } catch {
            submitStatus = "⚠️ 送出失敗：\(error.localizedDescription)"
        }
    }
}

struct MeasureFlowView: View {
    let clinicalMode: Bool
    @EnvironmentObject var app: AppState
    @StateObject private var vm = MeasureViewModel()
    @State private var pick: PhotosPickerItem?
    @State private var phantom = false
    @State private var showCamera = false
    @State private var showImporter = false
    @State private var confirmDiscard = false
    /// ②訓練同意的畫面提示快取。真值在送出當下重讀（見 `submitTraining`）。
    @State private var trainOkHint = false
    @State private var statusDlg: String?
    @State private var seenStatus: String?

    /// 臨床模式下沒有個案就不該存——`caseId` 為 null 的列進不了任何一條時間軸，
    /// 只會落到「快速量測紀錄」裡，而醫師以為自己存進了病患的病歷。
    ///
    /// `@MainActor` 不可省：View 的 `body` 有隔離，但**自訂的計算屬性沒有**，
    /// 而 `AppState` 是 `@MainActor` 類別。少了這一行是編譯錯誤，不是警告。
    @MainActor
    private var saveBlockedReason: String? {
        if clinicalMode && app.chosenCase == nil { return "尚未選擇個案" }
        return nil
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    if clinicalMode, let c = app.chosenCase {
                        Text("個案：\(c.bodySite)・\(c.woundType)　\(c.wdCode)")
                            .font(.footnote).foregroundStyle(.secondary)
                    }

                    // 三個影像入口（對齊 Android SamplePicker）：臨床現場以「拍照」為主——
                    // 事後從相簿補件有壓縮／裁切破壞尺度的風險；「檔案」可讀 Files app 裡
                    // 相簿看不到的圖（AirDrop 進來的範例圖）。
                    HStack(spacing: 8) {
                        Button("拍照") { showCamera = true }
                            .buttonStyle(.borderedProminent)
                            .frame(maxWidth: .infinity)
                        PhotosPicker("相簿", selection: $pick, matching: .images)
                            .buttonStyle(.bordered)
                            .frame(maxWidth: .infinity)
                        Button("檔案") { showImporter = true }
                            .buttonStyle(.bordered)
                            .frame(maxWidth: .infinity)
                    }

                    if !clinicalMode {
                        Toggle("印刷模擬圖（走色彩分割，不使用 AI 模型）", isOn: $phantom)
                            .font(.footnote)
                    }

                    if vm.loading { ProgressView(vm.loadingHint) }
                    if let e = vm.error {
                        Text(e).foregroundStyle(.red).font(.footnote)
                    }

                    if let r = vm.result, let img = vm.image {
                        // ★ 安全關鍵：校正框必須畫出來讓醫師看一眼。
                        AnalysisPreview(image: img, result: r)

                        ResultCard(result: r)

                        if let note = vm.statusNote, !note.isEmpty {
                            Text(note)
                                .font(.footnote)
                                .padding(10)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .background(Color.orange.opacity(0.15))
                                .cornerRadius(8)
                        }

                        ExudatePicker(value: $vm.exudate)

                        // 滲液未填前，修邊與存檔都鎖定（對齊 Android `needExudate`）：
                        // PUSH 總分缺滲液算不出來，存進去的 notes 會永遠是「滲液 未填」。
                        let needExudate = vm.exudate == nil
                        if needExudate {
                            Text("⚠ 請先輸入滲液量，才能進行「修邊」或「存入時間軸」")
                                .font(.footnote).foregroundStyle(.red)
                        }

                        // 醫師修邊確認狀態。**取消不算確認**——存檔仍允許（合法的 AI 初步
                        // 量測紀錄），但送訓練標註必須擋下，且要讓醫師看得出現在是什麼狀態。
                        if vm.doctorVerified {
                            Text("✓ 已完成醫師修邊確認 — 可送訓練標註")
                                .font(.footnote).foregroundStyle(.blue)
                        } else {
                            Text("尚未完成醫師修邊確認：此結果為 AI 原始輸出。"
                                 + "可存入時間軸作為初步量測，但**不得送訓練標註**——"
                                 + "訓練集的 GT 必須來自人的判斷。請按「醫師確認・修邊」並完成（按取消不算）。")
                                .font(.footnote).foregroundStyle(.red)
                        }

                        Button("醫師確認・修邊") { vm.editing = true }
                            .buttonStyle(.borderedProminent)
                            .disabled(needExudate)

                        // ★ 存檔與「送訓練標註」門檻不同：存病歷不需要 image_id 與修邊
                        //   （那仍是一次真實量測）；送標註才需要三者齊備。
                        if let reason = saveBlockedReason {
                            Text("存入時間軸：\(reason)")
                                .font(.footnote).foregroundStyle(.secondary)
                        }
                        Button(vm.lastSavedId == nil ? "存入時間軸" : "更新這一筆紀錄") {
                            Task {
                                await vm.saveToTimeline(
                                    repo: app.repo,
                                    imageStore: app.imageStore,
                                    // ⚠ 快速量測一律傳 nil。範例圖／模擬圖不得綁上真實病患。
                                    woundCase: clinicalMode ? app.chosenCase : nil)
                            }
                        }
                        .buttonStyle(.bordered)
                        .disabled(vm.saving || needExudate || saveBlockedReason != nil)

                        if let s = vm.saveNote {
                            Text(s).font(.footnote).foregroundStyle(.secondary)
                        }
                        if vm.lastSavedId != nil {
                            Button("查看時間軸") { app.screen = .timeline }
                        }

                        flywheelSection
                    }
                }
                .padding()
            }
            .navigationTitle(clinicalMode ? "臨床量測" : "快速量測")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("返回") { app.screen = app.backTo }
                }
            }
            .onChange(of: pick) { _, newValue in
                guard let newValue else { return }
                Task {
                    if let data = try? await newValue.loadTransferable(type: Data.self),
                       let img = UIImage(data: data) {
                        vm.source = clinicalMode ? "clinical" : (phantom ? "phantom" : "sample")
                        await vm.analyze(img, phantom: phantom && !clinicalMode)
                    }
                }
            }
            .task {
                // ②訓練同意的畫面提示（真值在送出當下另行重讀，這裡只是讓警告早點出現）。
                if clinicalMode, let c = app.chosenCase {
                    trainOkHint = await app.repo.activeConsent(patientId: c.patientId)?
                        .trainEffective == true
                }
            }
            .fullScreenCover(isPresented: $vm.editing) {
                editSheet
            }
            .fullScreenCover(isPresented: $showCamera) {
                CameraCaptureView(
                    onCapture: { img, depth in
                        showCamera = false
                        Task {
                            vm.source = clinicalMode ? "clinical" : (phantom ? "phantom" : "sample")
                            await vm.analyze(img, phantom: phantom && !clinicalMode, depth: depth)
                        }
                    },
                    onCancel: { showCamera = false })
            }
            .fileImporter(isPresented: $showImporter, allowedContentTypes: [.image]) { result in
                guard case .success(let url) = result else { return }
                // 沙箱外的檔案要先取用安全範圍權杖，讀完就還。
                let ok = url.startAccessingSecurityScopedResource()
                defer { if ok { url.stopAccessingSecurityScopedResource() } }
                guard let data = try? Data(contentsOf: url), let img = UIImage(data: data) else {
                    vm.error = "讀不到這個影像檔"
                    return
                }
                Task {
                    vm.source = clinicalMode ? "clinical" : (phantom ? "phantom" : "sample")
                    await vm.analyze(img, phantom: phantom && !clinicalMode)
                }
            }
            .onChange(of: vm.submitStatus) { _, s in
                // 重要狀態（✅／ℹ️／⚠️）彈窗強制確認：一行綠字實測會被錯過，
                // 而「送出訓練標註」是需要人知道它發生了的動作。「送出中…」之類過場不彈。
                guard let s, s != seenStatus,
                      s.hasPrefix("✅") || s.hasPrefix("ℹ️") || s.hasPrefix("⚠️") else { return }
                statusDlg = s
                seenStatus = s
            }
            .alert(statusDlg?.hasPrefix("⚠️") == true ? "注意" : "完成",
                   isPresented: Binding(get: { statusDlg != nil },
                                        set: { if !$0 { statusDlg = nil } })) {
                Button("確認") { statusDlg = nil }
            } message: {
                Text(statusDlg ?? "")
            }
        }
    }

    /// 修邊畫面（fullScreenCover 內容）。取消先彈確認——醫師手畫的遮罩不能被一個誤觸清掉。
    @ViewBuilder
    private var editSheet: some View {
        if let img = vm.image, let r = vm.result {
            WoundEditView(
                image: img,
                initialPolygons: vm.effectivePolygons,
                originalArea: r.areaCm2,
                tissueFrac: r.tissueFrac,
                exudate: vm.exudate,
                mmPerPx: r.mmPerPx,
                // 同影像續編：原樣載回上次遮罩（組織分區保留、面積不漂移）。
                resume: vm.raster,
                wbGains: r.wbGains,
                onCancel: { confirmDiscard = true },
                onDone: { poly, all, iou, area, tis, raster in
                    vm.applyEdited(polygon: poly, all: all, iou: iou, newArea: area,
                                   tissue: tis, raster: raster)
                    vm.editing = false
                }
            )
            .alert("放棄修邊？", isPresented: $confirmDiscard) {
                Button("放棄", role: .destructive) { vm.editing = false }
                Button("繼續修邊", role: .cancel) {}
            } message: {
                Text("尚未按「完成修邊」，目前的筆畫會全部捨棄。")
            }
        }
    }

    /// 飛輪送出區（對等 Android `DoctorFlywheelSubmit`）：滲液已填＋（修邊完成或已存檔）
    /// 才顯示。條件不足時給一行說明，不是讓按鈕神祕地消失。
    @ViewBuilder
    private var flywheelSection: some View {
        Divider()
        if vm.exudate != nil && (vm.doctorVerified || vm.lastSavedId != nil) {
            let sourceLabel: String = {
                switch vm.source {
                case "clinical": return "臨床"
                case "sample":   return "範例"
                case "phantom":  return "模擬圖(色彩分割)"
                default:          return "未選"
                }
            }()
            // 先在 String 層組好（見 ResultCard 的型別檢查器教訓）。
            let summary: String = {
                var s = "來源 \(sourceLabel) · "
                if clinicalMode, let c = app.chosenCase { s += "個案 \(c.wdCode) · " }
                s += "滲液 \(vm.exudate.map(String.init) ?? "—") · "
                s += "修邊\(vm.doctorVerified ? "✓" : "—") · "
                s += "存檔\(vm.lastSavedId != nil ? "✓" : "—")"
                return s
            }()
            Text("醫師確認・送出訓練標註（飛輪）").font(.subheadline).bold()
            Text(summary).font(.footnote).foregroundStyle(.secondary)
            if vm.source == "phantom" {
                Text("⚠ 模擬圖樣本僅供量測鏈驗證，不計入臨床樣本數、不作模型訓練")
                    .font(.footnote).foregroundStyle(.red)
            }
            if clinicalMode && app.chosenCase == nil {
                Text("⚠ 臨床樣本需先選個案傷口（代碼要穩定，回診才串得起來）")
                    .font(.footnote).foregroundStyle(.red)
            }
            if clinicalMode && app.chosenCase != nil && !trainOkHint {
                Text("⚠ 此病患未勾選②訓練同意（或已撤回），不得送出訓練標註")
                    .font(.footnote).foregroundStyle(.red)
            }
            if let s = vm.submitStatus {
                // 狀態放按鈕**上方**，按下即可見。
                Text(s).font(.footnote).foregroundStyle(.blue)
            }
            Button("醫師確認・送出標註 → 再訓練佇列") {
                Task {
                    await vm.submitTraining(repo: app.repo,
                                            woundCase: clinicalMode ? app.chosenCase : nil,
                                            clinicalMode: clinicalMode)
                }
            }
            .buttonStyle(.borderedProminent)
            .disabled(vm.submitBlockedReason != nil
                      || (clinicalMode && (app.chosenCase == nil || !trainOkHint)))
        } else {
            Text("（輸入滲液並完成「修邊確認」或「存入時間軸」後，將顯示送出訓練標註）")
                .font(.footnote).foregroundStyle(.secondary)
        }
    }
}

// MARK: - 校正框目視複核

/**
 把後端回傳的輪廓與**校正框**畫在照片上。

 ## 為什麼校正框圖層是必要的，不是裝飾

 ArUco 偵測**沒有「認錯了」這個錯誤狀態**——它要嘛回一個四邊形，要嘛回 nil。
 若它把反光、地磚接縫或別處的印刷圖案認成標記，`mm_per_px` 就是錯的，
 而**每一筆面積都會安靜地錯**：服務照回 200、畫面照顯示一個合理的數字，沒有任何跡象。

 程式判斷不了這件事。唯一實際可行的防線，是讓醫師看一眼那個框有沒有套在貼紙上。
 */
struct AnalysisPreview: View {
    let image: UIImage
    let result: ClassifyResult
    @State private var showWound = true
    @State private var showMarker = true

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            GeometryReader { geo in
                // 影像座標 → 顯示座標的等比縮放。用 min 是因為 .fit 會留白，
                // 兩軸各自縮放會讓框歪掉，而歪掉的框看起來就像偵測錯誤。
                let iw = CGFloat(max(result.imageW, 1))
                let ih = CGFloat(max(result.imageH, 1))
                let s = min(geo.size.width / iw, geo.size.height / ih)
                let ox = (geo.size.width - iw * s) / 2
                let oy = (geo.size.height - ih * s) / 2

                ZStack(alignment: .topLeading) {
                    Image(uiImage: image)
                        .resizable().scaledToFit()
                        .frame(width: geo.size.width, height: geo.size.height)

                    // 多處傷口要**全部**畫出來。只畫最大的那一個，醫師會以為第二個傷口
                    // 沒被偵測到（Android 2026-08-07 實測回報「畫面沒更新」）。
                    if showWound {
                        let polys = result.woundPolygons.isEmpty
                            ? (result.woundPolygon.count >= 3 ? [result.woundPolygon] : [])
                            : result.woundPolygons
                        Path { p in
                            for poly in polys where poly.count >= 3 {
                                let pts = poly.map {
                                    CGPoint(x: ox + CGFloat($0[0]) * s, y: oy + CGFloat($0[1]) * s)
                                }
                                p.move(to: pts[0])
                                p.addLines(pts)
                                p.closeSubpath()
                            }
                        }
                        .stroke(Color.cyan, lineWidth: 2)
                    }

                    if showMarker, let q = result.markerQuad, q.count == 4 {
                        Path { p in
                            let pts = q.map {
                                CGPoint(x: ox + CGFloat($0[0]) * s, y: oy + CGFloat($0[1]) * s)
                            }
                            p.addLines(pts); p.closeSubpath()
                        }
                        .stroke(Color.yellow, lineWidth: 3)
                    }
                }
            }
            .frame(height: 300)
            .background(Color.black.opacity(0.05))

            HStack {
                Toggle("傷口輪廓", isOn: $showWound).font(.caption)
                Toggle("校正框", isOn: $showMarker).font(.caption)
            }
            .toggleStyle(.button)

            if result.markerQuad != nil {
                Text("請確認**黃框**確實套在校正貼紙上。框錯了的話面積會整筆錯，而系統無法自行察覺。")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
    }
}

// MARK: - 結果卡

struct ResultCard: View {
    let result: ClassifyResult

    private func pct(_ v: Double?) -> String {
        guard let v else { return "—" }
        return "\(Int((v * 100).rounded()))%"
    }

    // 下面三行原本直接寫成 `Text("…" + "…" + "…")`。字串 `+` 的多載很多，
    // 混著字串插值一路串五段會讓型別檢查器組合爆炸——症狀是
    // 「unable to type-check this expression in reasonable time」，
    // 而且錯誤指的是整個 body，看不出是哪一行。先在 String 層組好，Text 只吃一個引數。
    private var pushLine: String {
        let v = result.pushPartial.map(String.init) ?? "—"
        return "PUSH：\(v)（面積＋組織；滲液須醫師輸入才算得出總分）"
    }

    private var tissueLine: String {
        var s = "組織　肉芽 \(pct(result.tissueFrac["granulation"]))"
        s += "・腐肉 \(pct(result.tissueFrac["slough"]))"
        s += "・壞死 \(pct(result.tissueFrac["necrosis"]))"
        s += "・上皮 \(pct(result.tissueFrac["epithelial"]))"
        s += "・其他 \(pct(result.tissueFrac["other"]))"
        return s
    }

    private var routeLine: String {
        let tail = result.escalated ? "　（難例已上雲集成）" : ""
        return "路由 \(result.route)　信心 \(pct(result.confidence))\(tail)"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(result.areaCm2.map { String(format: "面積：%.2f cm²", $0) } ?? "面積：未校正（無貼紙）")
                .font(.headline)
            Text(pushLine)
                .font(.subheadline)
            Text(tissueLine)
                .font(.footnote)
            Text(routeLine)
                .font(.caption).foregroundStyle(.secondary)
            Text("輔助用途、非診斷，需醫師確認。")
                .font(.caption2).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(Color.secondary.opacity(0.08))
        .cornerRadius(10)
    }
}

// MARK: - 滲液

/// PUSH 滲液子分。**單張影像判定不出來**，後端一律回 null，只能由醫師輸入。
struct ExudatePicker: View {
    @Binding var value: Int?
    private let labels = ["0 無", "1 少量", "2 中量", "3 大量"]

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("滲液量（PUSH 子分，須醫師判定）").font(.subheadline)
            Picker("滲液", selection: Binding(
                get: { value ?? -1 },
                set: { value = $0 < 0 ? nil : $0 }
            )) {
                Text("未填").tag(-1)
                ForEach(0..<labels.count, id: \.self) { Text(labels[$0]).tag($0) }
            }
            .pickerStyle(.segmented)
        }
    }
}
