import SwiftUI
import UIKit

/**
 單筆複核：**重新修邊**與**補送訓練標註**，都不必重測（對等 Android
 `MeasurementReviewScreen`）。解決的情境：病患事後才簽②訓練同意時，
 醫師不必整個重新拍照量測——後端靠 `image_id` 就找得到當初的影像。
 */
struct ReviewView: View {
    @EnvironmentObject var app: AppState

    let runtime: MedicalRuntime
    @ObservedObject private var uploads: ClinicalUploadOutbox
    init(runtime: MedicalRuntime) { self.runtime = runtime; uploads = runtime.local.outbox }
    @StateObject private var record = ClinicalReviewRecord()
    private var cur: Measurement? {
        get { record.measurement }
        nonmutating set { record.measurement = newValue }
    }
    @State private var bmp: UIImage?
    @State private var resumeRaster: EditRaster?
    @State private var spaceMismatch: String?
    @State private var trainOk = false
    @State private var loggedIn = false
    @State private var identity: LoginIdentity?
    @State private var loading = true
    @State private var editing = false
    @State private var confirmSubmit = false
    @State private var msg: String?
    @State private var statusDlg: String?
    @State private var seenStatus: String?
    @State private var backend: BackendClient?
    @State private var depthUploading = false
    @State private var submitting = false
    @State private var uploadPhase = ""
    private var uploadBusy: Bool { submitting || depthUploading }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 10) {
                    if let m = cur { content(m) }
                }
                .padding()
            }
            .disabled(uploadBusy)
            .safeAreaInset(edge: .bottom) {
                if uploadBusy { UploadProgressBanner(message: uploadPhase) }
            }
            .navigationTitle("紀錄檢視／重修補送標註")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("返回時間軸") {
                        app.reviewRecord = nil
                        app.screen = .timeline
                    }.disabled(uploadBusy)
                }
            }
            .task { await load() }
            .onReceive(uploads.$jobs) { _ in
                guard let snapshot = cur, !editing else { return }
                Task {
                    guard let current = await app.repo.measurement(id: snapshot.id),
                          cur?.id == current.id, cur?.imagePath == current.imagePath,
                          cur?.gtPolygon == current.gtPolygon, cur?.rasterPath == current.rasterPath else { return }
                    cur?.annotationSubmitted = current.annotationSubmitted
                }
            }
            .fullScreenCover(isPresented: $editing) { editCover }
            .onChange(of: msg) { _, s in
                guard let s, s != seenStatus,
                      s.hasPrefix("✅") || s.hasPrefix("ℹ️") || s.hasPrefix("⚠️") else { return }
                statusDlg = s; seenStatus = s
            }
            .alert(statusDlg?.hasPrefix("⚠️") == true ? "注意" : "完成",
                   isPresented: Binding(get: { statusDlg != nil },
                                        set: { if !$0 { statusDlg = nil } })) {
                Button("確認") { statusDlg = nil }
            } message: { Text(statusDlg ?? "") }
        }
    }

    @ViewBuilder
    private func content(_ m: Measurement) -> some View {
        Text("代碼 \(m.wdCode ?? "—")・面積 "
             + (m.estimatedArea.map { String(format: "%.2f cm²", $0) } ?? "未校正")
             + "・滲液 \(m.exudate.map(String.init) ?? "—")")
            .font(.subheadline)
        if let job = uploads.latest(recordID: m.id) {
            Text("臨床紀錄已保存 · " + job.status).font(.footnote).foregroundStyle(.secondary)
        }
        if let s = msg { Text(s).font(.footnote).foregroundStyle(.blue) }
        if loading { Text("載入影像中…").font(.footnote).foregroundStyle(.secondary) }

        if let image = bmp {
            // Only draw saved coordinates when their canvas is known to match the photo.
            let width = image.cgImage?.width ?? Int(image.size.width * image.scale)
            let height = image.cgImage?.height ?? Int(image.size.height * image.scale)
            let matches = m.imageW == width && m.imageH == height
            MedicalImagePreview(image: image, polygons: matches ? m.polygons : [],
                              imageW: width, imageH: height, raster: matches ? resumeRaster : nil)
            if !matches {
                Text("僅顯示原圖：此筆輪廓座標尺寸缺漏或不符，未疊加邊界。")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }

        if !loading && bmp == nil {
            Text("⚠ 此筆沒有本機影像（可能是舊紀錄，或已逾保存期限清理）。無法重新修邊；"
                 + (m.gtPolygon != nil && m.imageId != nil
                    ? "但輪廓與影像綁定都在，仍可補送標註。"
                    : "也缺輪廓或影像綁定，無法補送標註。"))
                .font(.footnote).foregroundStyle(.red)
        }
        if let sm = spaceMismatch {
            // 這是必要防線：座標空間不符時修邊會得到錯的面積且不會有任何警告。
            Text("⚠ 影像與輪廓座標空間不符（\(sm)），已停用重新修邊。此筆請重新量測；既有面積仍可作為病歷保留。")
                .font(.footnote).foregroundStyle(.red)
        }

        Button(identity?.canVerifyClinicalEdits == true ? "重新修邊（醫師確認）" : "調整輪廓（不作醫師確認）") { editing = true }
            .buttonStyle(.borderedProminent)
            .disabled(bmp == nil || spaceMismatch != nil)

        Divider()
        Text("補送訓練標註").font(.subheadline).bold()
        Text(submitHint(m))
            .font(.footnote)
            .foregroundStyle(m.annotationSubmitted ? Color.blue : (canSubmit(m) ? Color.secondary : Color.red))
        Button(m.annotationSubmitted ? "標註已保存，無需補送" : "補送訓練標註 → 再訓練佇列") { confirmSubmit = true }
            .buttonStyle(.borderedProminent)
            .disabled(!canSubmit(m))
            // 送出前逐欄揭露：資料離開手機是最不可逆的動作，不採「按了就送」。
            .confirmationDialog("確認送出訓練標註？", isPresented: $confirmSubmit, titleVisibility: .visible) {
                Button("確認送出", role: .destructive) { Task { await submit(m) } }
                Button("取消", role: .cancel) {}
            } message: {
                Text(disclosureText(m))
            }

        // ── LiDAR 深度補傳（sidecar → /api/v1/depth，2026-08-19 後端上線）────
        //
        // 深度在拍攝當下已加密存本機（depth_source=lidar_local）；這裡把 f32 原樣
        // 補傳供 3D 研究。後端拒收孤兒深度（必須先有標註綁定），所以未補送標註時
        // 只給引導不給按鈕——按了必失敗的按鈕比沒有按鈕更糟。
        if let entry = DepthStore.lookup(imagePath: m.imagePath, store: app.imageStore), m.imageId != nil {
            Divider()
            Text("LiDAR 深度補傳").font(.subheadline).bold()
            if entry["uploaded"] == "1" {
                Text("✓ 此筆深度已補傳雲端（float32 公尺原樣，供 3D 重建研究）。")
                    .font(.footnote).foregroundStyle(.blue)
            } else if identity?.can("annotation.submit") != true {
                Text("目前帳號無深度上傳權限；本機深度資料仍可檢視。")
                    .font(.footnote).foregroundStyle(.secondary)
            } else if !m.annotationSubmitted {
                Text("本筆尚未完成上傳。按「補送訓練標註」後會自動接續深度；相同標註已存在也可接續，不需重新修邊。")
                    .font(.footnote).foregroundStyle(.secondary)
            } else {
                Text("標註已保存，原始深度尚待補傳。資料大小與網路狀況會影響等待時間；只需重試深度。")
                    .font(.footnote).foregroundStyle(.secondary)
                Button(depthUploading ? "深度補傳中…" : "補傳深度 → 雲端") {
                    Task { await uploadDepth(m) }
                }
                .buttonStyle(.bordered)
                .disabled(depthUploading || !loggedIn)
            }
        }
    }

    @MainActor
    private func uploadDepth(_ m: Measurement) async {
        guard !uploadBusy else { return }
        depthUploading = true
        uploadPhase = "確認深度補傳權限…"
        defer { depthUploading = false }
        guard await refreshIdentity(), identity?.can("annotation.submit") == true else {
            msg = "⚠️ 目前帳號無深度上傳權限，未送出資料。"
            return
        }
        if let cid = m.caseId, let c = await app.repo.getCase(id: cid) {
            guard await app.repo.activeConsent(patientId: c.patientId)?.trainEffective == true else {
                msg = "⚠️ 訓練同意已撤回或失效，已停止深度補傳。"
                return
            }
        } else if (m.source ?? "clinical") == "clinical" {
            msg = "⚠️ 缺少可核對同意的個案，已停止深度補傳。"
            return
        }
        guard backend != nil, m.imageId != nil,
              let side = DepthStore.load(imagePath: m.imagePath, store: app.imageStore) else {
            msg = "⚠️ 深度側檔讀取失敗（可能已隨影像保存政策清除）"
            return
        }
        uploadPhase = "傳送原始深度並等待後端保存確認…"
        msg = nil
        do {
            guard let actor = identity?.identity else { throw BackendError.notLoggedIn }
            try uploads.enqueue(measurement: m, payload: Data(), server: runtime.backendURL(),
                configuredUser: runtime.backendUser(), actor: actor, depth: side,
                requiresDepth: true, annotationConfirmed: m.annotationSubmitted)
            runtime.resumeUploads()
            msg = "ℹ️ 原始深度已加入待傳佇列，可繼續其他工作。不需重新修邊。"
        } catch {
            msg = "⚠️ 未能建立深度待傳工作：\(error.localizedDescription)"
        }
    }

    private func canSubmit(_ m: Measurement) -> Bool {
        return !loading && !uploadBusy && !uploads.jobs.contains(where: { $0.measurement.id == m.id && !$0.completed }) && !m.annotationSubmitted && m.gtPolygon != nil && m.imageId != nil
            && m.wdCode != nil && m.doctorVerified && trainOk && loggedIn
            && identity?.canSubmitClinicalTraining == true
    }

    private func submitHint(_ m: Measurement) -> String {
        if identity?.canSubmitClinicalTraining != true {
            return "目前帳號無醫師確認／訓練標註權限；可調整輪廓與儲存量測。"
        }
        if uploads.jobs.contains(where: { $0.measurement.id == m.id && !$0.completed }) {
            return "此筆研究資料已排入待傳佇列，會自動接續；不需重新修邊或重複送出。"
        }
        if m.annotationSubmitted { return "此筆標註已獲雲端確認；僅在需要修正輪廓時重新修邊，深度上傳不需改動遮罩。" }
        if m.gtPolygon == nil { return "⚠ 此筆沒有 GT 輪廓，不能補送（請先重新修邊）。" }
        if m.imageId == nil { return "⚠ 此筆沒有後端影像綁定，不能補送。" }
        if !m.doctorVerified { return "⚠ 此筆未經醫師完成修邊確認，不得送訓練標註。請先「重新修邊」並完成（按取消不算）。" }
        if !trainOk { return "⚠ 此病患未取得②訓練同意（或已撤回），不得送出。" }
        if !loggedIn { return "⚠ 後端未連線或尚未設定帳密——請到「設定」確認。" }
        return "可補送：後端靠 image_id 就找得到當初的影像，不需要重新上傳或重測。"
    }

    /// 實際會離開手機的每一個欄位攤開確認；並明示 PII 不在其中——那是同意書的承諾，
    /// 醫師要能當場看見它成立。
    private func disclosureText(_ m: Measurement) -> String {
        let polys = m.polygons
        var s = "以下內容將離開本機、進入雲端再訓練佇列：\n"
        s += "・去識別代碼 \(m.wdCode ?? "—")\n"
        s += "・影像綁定 \(m.imageId ?? "—")（後端既有，不重新上傳）\n"
        s += polys.count > 1
            ? "・傷口輪廓 \(polys.count) 處・共 \(polys.reduce(0) { $0 + $1.count }) 點\n"
            : "・傷口輪廓 \(m.polygonPoints.count) 點\n"
        s += "・面積 \(m.estimatedArea.map { String(format: "%.2f cm²", $0) } ?? "未校正")・滲液 \(m.exudate.map(String.init) ?? "—")\n"
        let edPx = resumeRaster?.tissueEditedPx ?? 0
        if resumeRaster == nil {
            s += "・組織遮罩 ✗ 不送（本筆沒有修邊柵格）\n"
        } else if edPx <= 0 {
            s += "・組織遮罩 △ 送出但標記「未經醫師修正」，不進組織訓練集\n"
        } else {
            s += "・組織遮罩 ✓ 含醫師修正 \(edPx) 像素\n"
        }
        s += "✓ 姓名、病歷號等個資不在其中，且永不離開本機。"
        if (m.source ?? "clinical") == "clinical" {
            s += "\n⚠ 來源為「臨床」：會計入臨床收案進度。若這其實是範例圖，請取消。"
        }
        return s
    }

    @ViewBuilder
    private var editCover: some View {
        if let m = cur, let img = bmp {
            WoundEditView(
                image: img,
                initialPolygons: m.polygons,
                originalArea: m.estimatedArea,
                // v5 起組織比例有存。傳空 map 會讓底稿退回「比例最高那類」的猜測。
                tissueFrac: [
                    "granulation": m.tissueGranulation ?? 0, "slough": m.tissueSlough ?? 0,
                    "necrosis": m.tissueNecrosis ?? 0, "epithelial": m.tissueEpithelial ?? 0,
                    "other": m.tissueOther ?? 0
                ],
                exudate: m.exudate,
                mmPerPx: m.mmPerPx,
                // v6：柵格快照原樣載回（組織分區保留、面積不漂移）；載不到才退回多邊形重建。
                resume: resumeRaster,
                wbGains: resumeRaster?.wbGains,
                onCancel: { editing = false },
                onDone: { poly, all, iou, newArea, tis, raster in
                    editing = false
                    Task { await applyReEdit(m, poly: poly, all: all, iou: iou,
                                             newArea: newArea, tis: tis, raster: raster) }
                })
        }
    }

    @MainActor
    private func load() async {
        guard let selected = app.reviewRecord else { return }
        loading = true
        await record.refresh(id: selected.id, repo: app.repo)
        guard let m = cur else {
            msg = "⚠️ 紀錄已不存在，無法補送。"; loading = false; return
        }
        let img = await ImageLoadQueue.shared.fullImage(store: app.imageStore, name: m.imagePath)
        guard !Task.isCancelled else { return }
        bmp = img
        spaceMismatch = {
            guard let img, let w = m.imageW, let h = m.imageH else { return nil }
            let iw = img.cgImage?.width ?? 0, ih = img.cgImage?.height ?? 0
            return (iw != w || ih != h) ? "影像 \(iw)×\(ih) ≠ 輪廓空間 \(w)×\(h)" : nil
        }()
        resumeRaster = {
            guard let img, let rp = m.rasterPath else { return nil }
            return EditRasterCodec.decode(png: app.imageStore.rawBytes(rp), meta: m.rasterMeta,
                                          canvasW: img.cgImage?.width ?? 0,
                                          canvasH: img.cgImage?.height ?? 0)
        }()
        if let cid = m.caseId, let c = await app.repo.getCase(id: cid) {
            trainOk = await app.repo.activeConsent(patientId: c.patientId)?.trainEffective == true
        } else {
            // 範例／模擬圖無受試者，不受訓練同意限制。
            trainOk = (m.source ?? "clinical") != "clinical"
        }
        #if WOUND_INSTITUTION
        do {
            let c = try await runtime.acquire()
            try Task.checkCancellation()
            backend = c; identity = await c.currentIdentity(); loggedIn = identity != nil
        } catch {
            backend = nil; identity = nil; loggedIn = false
            msg = "登入狀態尚未驗證：\(error.localizedDescription)"
        }
        #else
        let c = runtime.makeBackend()
        let u = runtime.backendUser(), p = runtime.backendPassword()
        // ⚠ 不可寫成 `!u.isEmpty && (try? await …)`：`&&` 的右運算元是 @autoclosure，
        //   不支援 async，await 塞進去是編譯錯誤（而且訊息不會指向 &&）。
        if u.isEmpty || p.isEmpty {
            loggedIn = false
        } else {
            loggedIn = (try? await c.login(username: u, password: p)) == true
        }
        backend = c
        identity = loggedIn ? await c.currentIdentity() : nil
        #endif
        loading = false
    }

    /// 重新修邊完成 → 更新 DB。三個不可省的規則見註解。
    @MainActor
    private func applyReEdit(_ m: Measurement, poly: [[Int]], all: [[[Int]]], iou: Double?,
                             newArea: Double?, tis: [String: Double], raster: EditRaster) async {
        var u = m
        u.gtPolygon = PolygonJson.toJson(all.isEmpty ? (poly.count >= 3 ? [poly] : []) : all)
        let finalArea = newArea ?? m.estimatedArea
        u.estimatedArea = finalArea
        u.hasWound = (finalArea ?? 0) > 0 || m.hasWound
        // An edit is a physician verification only with an authenticated capability.
        // A nurse re-edit clears an earlier verification of different geometry.
        u.doctorVerified = identity?.canVerifyClinicalEdits == true
        // ⚠ correctionIou **刻意不覆寫**：它的定義是「與 AI 原始遮罩的 IoU」。從本畫面進來
        //   的起點是已修過的 GT，覆寫會讓指標系統性趨近 1.0——失真的自我評分。
        //   本次相對前版的 IoU 記在 notes。
        u.annotationSubmitted = false   // 輪廓改了就要重新送出雲端才有新 GT
        // 柵格成對更新；存檔失敗不可讓整筆更新失敗，但也不可假裝成功。
        var rasterSaveFailed = false
        if let (png, meta) = EditRasterCodec.encode(raster) {
            if let name = app.imageStore.saveRaw(png) {
                u.rasterPath = name
                u.rasterMeta = meta
            } else { rasterSaveFailed = true }
        } else { rasterSaveFailed = true }
        // 組織比例（v5）：影像會依 90 天政策清除，現在不存就永遠補不回來。
        u.tissueGranulation = tis["granulation"] ?? m.tissueGranulation
        u.tissueSlough = tis["slough"] ?? m.tissueSlough
        u.tissueNecrosis = tis["necrosis"] ?? m.tissueNecrosis
        u.tissueEpithelial = tis["epithelial"] ?? m.tissueEpithelial
        u.tissueOther = tis["other"] ?? m.tissueOther
        // 修訂行：時間軸卡片顯示的是 notes，最上行是首次量測值——要明講以本行為準。
        func pct(_ k: String) -> Int { return Int(((tis[k] ?? 0) * 100).rounded()) }
        let stamp = Date().formatted(date: .numeric, time: .shortened)
        var rev = "⟳ \(stamp) 醫師重新修邊：面積 "
        rev += (m.estimatedArea.map { String(format: "%.2f", $0) } ?? "—") + "→"
        rev += (finalArea.map { String(format: "%.2f", $0) } ?? "—") + " cm²"
        if let i = iou { rev += String(format: "；本次相對前版 IoU %.2f", i) }
        rev += "；組織 肉芽\(pct("granulation"))% 腐肉\(pct("slough"))% 壞死\(pct("necrosis"))%。以本行為準"
        u.notes = [m.notes, rev].compactMap { $0 }.joined(separator: "\n")

        await app.repo.updateMeasurement(u)
        // 先寫 DB 再刪舊柵格檔——反過來會留下指向不存在檔案的死路徑。
        if let old = m.rasterPath, old != u.rasterPath, u.rasterPath != nil {
            app.imageStore.delete(old)
        }
        cur = u
        app.reviewRecord = u
        // 記憶體快照同步換新，否則不離開這頁再修一次，載回的是上一輪的柵格。
        resumeRaster = raster
        msg = "✅ 已更新此筆紀錄的輪廓與面積。"
            + (rasterSaveFailed ? "\n⚠️ 但組織分區未能存檔，下次進來會退回 AI 的猜測。" : "")
            + (u.doctorVerified
               ? "\n輪廓已變更，此筆需重新送出才會更新雲端 GT。"
               : "\n此筆未標記為醫師確認，未送出訓練標註。")
    }

    @MainActor
    private func refreshIdentity() async -> Bool {
        identity = nil
        loggedIn = false
        #if WOUND_INSTITUTION
        do {
            let refreshed = try await runtime.acquire()
            backend = refreshed
            identity = await refreshed.currentIdentity()
            loggedIn = identity != nil
        } catch { backend = nil }
        #else
        guard let backend else { return false }
        loggedIn = (try? await backend.login(username: runtime.backendUser(), password: runtime.backendPassword())) == true
        if loggedIn { identity = await backend.currentIdentity() }
        #endif
        return loggedIn
    }

    @MainActor
    private func submit(_ m: Measurement) async {
        guard !uploadBusy, !uploads.jobs.contains(where: { $0.measurement.id == m.id && !$0.completed }) else { return }
        submitting = true
        uploadPhase = "確認登入與最新同意…"
        defer { submitting = false }
        guard let fresh = await record.prepareSubmission(id: m.id, repo: app.repo) else {
            msg = cur == nil ? "⚠️ 紀錄已不存在，未送出。" : "ℹ️ 此筆標註已獲雲端確認，無需補送。"
            return
        }
        // Never pair a stale screen raster with a newer persisted measurement.
        guard fresh.imagePath == m.imagePath, fresh.rasterPath == m.rasterPath,
              fresh.rasterMeta == m.rasterMeta, fresh.gtPolygon == m.gtPolygon,
              fresh.exudate == m.exudate else {
            await load(); msg = "ℹ️ 紀錄已更新，請先檢視最新內容再確認送出。"; return
        }
        let m = fresh
        guard await refreshIdentity(), identity?.canSubmitClinicalTraining == true else {
            msg = "⚠️ 目前帳號無醫師確認／訓練標註權限，未送出資料。"
            return
        }
        // ⚠ 同意真值在按下的當下重讀，不可用進畫面時的快照——醫師可能剛撤回。
        if let cid = m.caseId, let c = await app.repo.getCase(id: cid) {
            let fresh = await app.repo.activeConsent(patientId: c.patientId)?.trainEffective == true
            trainOk = fresh
            guard fresh else { msg = "⚠️ 此病患目前無有效的②訓練同意（可能剛撤回），已停止送出"; return }
        }
        guard backend != nil, let imageId = m.imageId, let code = m.wdCode else { return }
        msg = nil
        let polys = m.polygons
        do {
            let payload = try BackendClient.annotationPayload(
                code: code,
                gtPolygon: PolygonJson.largest(polys),
                exudate: m.exudate,
                allPolygons: polys,
                areaCm2: m.estimatedArea,   // 面積以本機紀錄為真值，不讓後端由多邊形反算
                imageId: imageId, imageW: m.imageW ?? 0, imageH: m.imageH ?? 0,
                mmPerPx: m.mmPerPx, route: m.route, segModel: nil,
                tissueFrac: m.tissueFrac,
                // 沒有柵格就整組不送——硬用輪廓補一張遮罩只會製造假 GT。
                tissueMaskPngBase64: resumeRaster.flatMap {
                    TissueMaskCodec.encodeBase64(tissue: $0.tissue, mask: $0.mask,
                                                 mw: $0.mw, mh: $0.mh)
                },
                tissueRaster: resumeRaster,
                correctionIou: m.correctionIou,
                careNote: "resubmit from timeline",
                source: m.source ?? "clinical",
                depthSource: DepthStore.lookup(imagePath: m.imagePath, store: app.imageStore) != nil ? "lidar_local" : "none",
                consentTrain: trainOk,
                doctorVerified: m.doctorVerified)
            guard let actor = identity?.identity else { throw BackendError.notLoggedIn }
            let depth = DepthStore.load(imagePath: m.imagePath, store: app.imageStore)
            let expectsDepth = DepthStore.lookup(imagePath: m.imagePath, store: app.imageStore) != nil
            try uploads.enqueue(measurement: m, payload: payload, server: runtime.backendURL(),
                configuredUser: runtime.backendUser(), actor: actor, depth: depth, requiresDepth: expectsDepth)
            runtime.resumeUploads()
            msg = "ℹ️ 研究資料已安全排入待傳佇列，可繼續工作；標註確認後自動接續原始深度。"
            msg = (msg ?? "") + (await runtime.exportSubmission(m, payload: payload))
        } catch {
            msg = "⚠️ 未能建立研究待傳工作：\(error.localizedDescription)"
        }
    }
}
