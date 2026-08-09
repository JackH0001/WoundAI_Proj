import SwiftUI
import UIKit

/**
 單筆複核：**重新修邊**與**補送訓練標註**，都不必重測（對等 Android
 `MeasurementReviewScreen`）。解決的情境：病患事後才簽②訓練同意時，
 醫師不必整個重新拍照量測——後端靠 `image_id` 就找得到當初的影像。
 */
struct ReviewView: View {
    @EnvironmentObject var app: AppState

    @State private var cur: Measurement?
    @State private var bmp: UIImage?
    @State private var resumeRaster: EditRaster?
    @State private var spaceMismatch: String?
    @State private var trainOk = false
    @State private var loggedIn = false
    @State private var loading = true
    @State private var editing = false
    @State private var confirmSubmit = false
    @State private var msg: String?
    @State private var statusDlg: String?
    @State private var seenStatus: String?
    @State private var backend: BackendClient?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 10) {
                    if let m = cur { content(m) }
                }
                .padding()
            }
            .navigationTitle("紀錄檢視／補送標註")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("返回時間軸") {
                        app.reviewRecord = nil
                        app.screen = .timeline
                    }
                }
            }
            .task { await load() }
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
        if let s = msg { Text(s).font(.footnote).foregroundStyle(.blue) }
        if loading { Text("載入影像中…").font(.footnote).foregroundStyle(.secondary) }

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

        Button("重新修邊（載回原影像與輪廓）") { editing = true }
            .buttonStyle(.borderedProminent)
            .disabled(bmp == nil || spaceMismatch != nil)

        Divider()
        Text("補送訓練標註").font(.subheadline).bold()
        Text(submitHint(m))
            .font(.footnote)
            .foregroundStyle(canSubmit(m) ? Color.secondary : Color.red)
        Button("補送訓練標註 → 再訓練佇列") { confirmSubmit = true }
            .buttonStyle(.borderedProminent)
            .disabled(!canSubmit(m))
            // 送出前逐欄揭露：資料離開手機是最不可逆的動作，不採「按了就送」。
            .confirmationDialog("確認送出訓練標註？", isPresented: $confirmSubmit, titleVisibility: .visible) {
                Button("確認送出", role: .destructive) { Task { await submit(m) } }
                Button("取消", role: .cancel) {}
            } message: {
                Text(disclosureText(m))
            }
    }

    private func canSubmit(_ m: Measurement) -> Bool {
        return !m.annotationSubmitted && m.gtPolygon != nil && m.imageId != nil
            && m.wdCode != nil && m.doctorVerified && trainOk && loggedIn
    }

    private func submitHint(_ m: Measurement) -> String {
        if m.annotationSubmitted { return "此筆已送出過訓練標註。重新修邊後可再送（雲端視為醫師修訂版）。" }
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
        guard let m = app.reviewRecord else { return }
        cur = m
        loading = true
        let img = m.imagePath.isEmpty ? nil : app.imageStore.loadFull(m.imagePath)
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
        let c = BackendClient(baseUrl: AppSettings.backendURL())
        let u = AppSettings.backendUser(), p = AppSettings.backendPassword()
        // ⚠ 不可寫成 `!u.isEmpty && (try? await …)`：`&&` 的右運算元是 @autoclosure，
        //   不支援 async，await 塞進去是編譯錯誤（而且訊息不會指向 &&）。
        if u.isEmpty || p.isEmpty {
            loggedIn = false
        } else {
            loggedIn = (try? await c.login(username: u, password: p)) == true
        }
        backend = c
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
        // 走到這裡代表醫師按了「完成修邊」（取消不會呼叫 onDone）。
        u.doctorVerified = true
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
            + "\n輪廓已變更，此筆需**重新送出**才會讓雲端拿到新的 GT。"
    }

    @MainActor
    private func submit(_ m: Measurement) async {
        // ⚠ 同意真值在按下的當下重讀，不可用進畫面時的快照——醫師可能剛撤回。
        if let cid = m.caseId, let c = await app.repo.getCase(id: cid) {
            let fresh = await app.repo.activeConsent(patientId: c.patientId)?.trainEffective == true
            trainOk = fresh
            guard fresh else { msg = "⚠️ 此病患目前無有效的②訓練同意（可能剛撤回），已停止送出"; return }
        }
        guard let backend, let imageId = m.imageId, let code = m.wdCode else { return }
        msg = "送出中…"
        do {
            let polys = m.polygons
            let outcome = try await backend.submitAnnotation(
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
                depthSource: DepthStore.lookup(imagePath: m.imagePath) != nil ? "lidar_local" : "none",
                consentTrain: trainOk,
                doctorVerified: m.doctorVerified)
            switch outcome {
            case .rejected(let t):
                msg = "⚠️ 被守門擋下：\(t)"
            case .duplicateSkipped(let note):
                msg = "ℹ️ \(note)"
            case .enqueued:
                // 本機標記失敗不可假裝成功：畫面顯示已送出而 DB 沒記，重開又變可補送。
                await app.repo.markAnnotationSubmitted(id: m.id)
                var u = m; u.annotationSubmitted = true
                cur = u; app.reviewRecord = u
                msg = "✅ 已補送訓練標註（\(code)）\n本筆已進入雲端再訓練佇列。"
            }
        } catch {
            msg = "⚠️ 送出失敗：\(error.localizedDescription)"
        }
    }
}
