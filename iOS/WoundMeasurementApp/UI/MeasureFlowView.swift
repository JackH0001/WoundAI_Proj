import SwiftUI
import PhotosUI
import UIKit

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

    /// - Parameter phantom: 印刷模擬圖 → `seg=color`，走決定性 HSV 色彩分割，完全不碰 AI 模型。
    func analyze(_ img: UIImage, phantom: Bool) async {
        guard await ensureLogin(), let backend else { return }
        // ⚠ 一律送原始影像。先自行套白平衡的話，後端會在已校正的圖上再做一次 gray-world，
        //   兩層疊起來的結果沒有人推得出來，而且它不會報錯。
        guard let jpeg = img.jpegData(compressionQuality: 0.92) else {
            error = "影像編碼失敗"; return
        }
        loading = true; error = nil; statusNote = nil
        defer { loading = false }
        do {
            let r = try await backend.classify(jpeg: jpeg, seg: phantom ? "color" : nil)
            image = img
            result = r
            // 換了影像就作廢上一輪的修邊與醫師背書。
            raster = nil
            doctorVerified = false
            statusNote = Self.advisories(r, clinical: source == "clinical").joined(separator: "\n")
        } catch {
            self.error = error.localizedDescription
        }
    }

    /// 必須讓醫師看到的提示。每一條都對應一種「服務回 200 但數字是錯的」的情形。
    nonisolated static func advisories(_ r: ClassifyResult, clinical: Bool) -> [String] {
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

    /// 只有修邊畫面按下「完成修邊」才會走到這裡。
    func markDoctorVerified(raster: EditRaster) {
        self.raster = raster
        self.doctorVerified = true
        // 修邊後的面積走 mm_per_px × 像素數，**不是**「AI 面積 × 修正比例」。
        if let a = raster.areaCm2 { result?.areaCm2 = a }
    }

    /// 存入個案時間軸。柵格一併加密持久化——沒有它，回頭修邊時組織分區會整批消失，
    /// 而面積每進出一次就漂移一點。
    func saveToTimeline(repo: CaseRepository, imageStore: LocalImageStore,
                        woundCase: WoundCase?) async -> Bool {
        guard let r = result, let img = image else { return false }

        var imagePath = ""
        if let jpeg = img.jpegData(compressionQuality: 0.9),
           let name = imageStore.save(jpeg: jpeg) { imagePath = name }

        var rasterPath: String?
        var rasterMeta: String?
        if let ras = raster, let (png, meta) = EditRasterCodec.encode(ras) {
            // 兩者必須成對。只存其中一個的話，載回時解不出座標空間，
            // 而程式會誤以為「這筆沒有柵格」並悄悄退回由多邊形重建。
            if let name = imageStore.saveRaw(png) { rasterPath = name; rasterMeta = meta }
        }

        let poly = raster.map { RasterOps.rasterToPolygon($0) } ?? r.woundPolygon
        let frac = raster?.tissueFrac() ?? r.tissueFrac

        let m = Measurement(
            patientId: woundCase?.patientId,
            hasWound: !poly.isEmpty,
            confidence: r.confidence,
            estimatedArea: raster?.areaCm2 ?? r.areaCm2,
            woundTypeLabel: "AI(\(r.route))",
            imagePath: imagePath,
            notes: noteLine(r, frac: frac),
            isPatientIdentified: woundCase != nil,
            caseId: woundCase?.id,
            wdCode: woundCase?.wdCode,
            imageId: r.imageId,
            mmPerPx: r.mmPerPx,
            route: r.route,
            source: source,
            gtPolygon: Self.polygonJSON(poly),
            imageW: r.imageW, imageH: r.imageH,
            exudate: exudate,
            correctionIou: raster?.correctionIou,
            annotationSubmitted: false,
            doctorVerified: doctorVerified,
            tissueGranulation: frac["granulation"], tissueSlough: frac["slough"],
            tissueNecrosis: frac["necrosis"], tissueEpithelial: frac["epithelial"],
            tissueOther: frac["other"],
            rasterPath: rasterPath, rasterMeta: rasterMeta
        )
        let id = await repo.insertMeasurement(m)
        if woundCase != nil, let pid = woundCase?.patientId { await repo.touchLastVisit(patientId: pid) }
        statusNote = id != nil ? "已存入時間軸。" : "存檔失敗。"
        return id != nil
    }

    private func noteLine(_ r: ClassifyResult, frac: [String: Double]) -> String {
        func p(_ k: String) -> Int { Int(((frac[k] ?? 0) * 100).rounded()) }
        var s = "PUSH \(r.pushPartial.map(String.init) ?? "-")"
        s += "; 肉芽\(p("granulation"))% 腐肉\(p("slough"))% 壞死\(p("necrosis"))%"
        if let e = exudate { s += "; 滲液\(e)" }
        s += "; route \(r.route)"
        if let iou = raster?.correctionIou { s += String(format: "; 修邊IoU %.2f", iou) }
        return s
    }

    nonisolated static func polygonJSON(_ poly: [[Int]]) -> String? {
        guard !poly.isEmpty, let d = try? JSONSerialization.data(withJSONObject: poly) else { return nil }
        return String(data: d, encoding: .utf8)
    }

    /**
     送出訓練標註。

     - Parameter consentTrainTruth: **送出當下重讀的**同意真值，不是畫面快照——
       剛撤回的話快照仍是 true，等於換個形式回到「App 對後端聲稱它沒驗證過的事」。
     */
    func submitAnnotation(code: String, consentTrainTruth: Bool) async {
        guard let r = result, let ras = raster, let backend else {
            statusNote = "尚未完成修邊或未登入。"; return
        }
        guard consentTrainTruth else {
            statusNote = "此個案未取得（或已撤回）訓練同意，不送出。"; return
        }
        loading = true
        defer { loading = false }

        let poly = RasterOps.rasterToPolygon(ras)
        let allPolys = RasterOps.rasterToPolygons(ras)
        let maskB64 = TissueMaskCodec.encodeBase64(tissue: ras.tissue, mask: ras.mask,
                                                   mw: ras.mw, mh: ras.mh)
        do {
            let outcome = try await backend.submitAnnotation(
                code: code, gtPolygon: poly, allPolygons: allPolys, exudate: exudate,
                areaCm2: ras.areaCm2,
                imageId: r.imageId, imageW: r.imageW, imageH: r.imageH,
                mmPerPx: r.mmPerPx, route: r.route, segModel: r.segModel,
                tissueFrac: ras.tissueFrac(),
                tissueMaskPngBase64: maskB64,
                tissueRaster: ras,
                correctionIou: ras.correctionIou,
                source: source,
                quality: r.quality,
                consentTrain: consentTrainTruth,
                doctorVerified: doctorVerified
            )
            switch outcome {
            case .enqueued(let note):
                statusNote = "✅ 已送出，進再訓練佇列。" + (note.map { "\n" + $0 } ?? "")
            case .duplicateSkipped(let note):
                // 後端以 200 回覆但實際沒入列。一定要讓醫師看到，
                // 否則他會以為剛才的修改已經存進去了。
                statusNote = "ℹ️ " + note
            case .rejected(let msg):
                statusNote = "⚠ 被守門擋下：" + msg
            }
        } catch {
            statusNote = "送出失敗：" + error.localizedDescription
        }
    }

    /// 送出條件。任何一項不成立都不該讓按鈕可按——而且要說得出是哪一項。
    var submitBlockedReason: String? {
        guard let r = result else { return "尚未完成量測" }
        if r.imageId == nil { return "缺 image_id，無法綁定影像（見上方提示）" }
        if exudate == nil { return "請先輸入滲液量（0–3）" }
        if !doctorVerified { return "需先完成「醫師確認・修邊」才能送出訓練標註" }
        return nil
    }
}

struct MeasureFlowView: View {
    let clinicalMode: Bool
    @EnvironmentObject var app: AppState
    @StateObject private var vm = MeasureViewModel()
    @State private var pick: PhotosPickerItem?
    @State private var phantom = false
    @State private var editing = false

    /// 送出前**重讀**同意真值。畫面快照在「剛剛才撤回」的情況下仍是 true，
    /// 而那正是同意閘門要擋的那一種情形。
    private func submit() async {
        guard let c = await app.chosenCase else { return }
        let truth = await app.repo.activeConsent(patientId: c.patientId)?.trainEffective ?? false
        await vm.submitAnnotation(code: c.wdCode, consentTrainTruth: truth)
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    if clinicalMode, let c = app.chosenCase {
                        Text("個案：\(c.bodySite)・\(c.woundType)　\(c.wdCode)")
                            .font(.footnote).foregroundStyle(.secondary)
                    }

                    PhotosPicker("選擇照片", selection: $pick, matching: .images)
                        .buttonStyle(.borderedProminent)

                    if !clinicalMode {
                        Toggle("印刷模擬圖（走色彩分割，不使用 AI 模型）", isOn: $phantom)
                            .font(.footnote)
                    }

                    if vm.loading { ProgressView("分析中…（雲端冷啟動可能需 10 秒以上）") }
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

                        Button(vm.doctorVerified ? "重新修邊" : "醫師確認・修邊") { editing = true }
                            .buttonStyle(.bordered)

                        if clinicalMode {
                            Button("存入個案時間軸") {
                                Task { _ = await vm.saveToTimeline(repo: app.repo,
                                                                   imageStore: app.imageStore,
                                                                   woundCase: app.chosenCase) }
                            }
                            .buttonStyle(.bordered)
                            .disabled(app.chosenCase == nil)
                        }

                        if let reason = vm.submitBlockedReason {
                            Text("送出訓練標註：\(reason)")
                                .font(.footnote).foregroundStyle(.secondary)
                        }
                        Button("送出訓練標註") {
                            Task { await submit() }
                        }
                        .buttonStyle(.borderedProminent)
                        .disabled(vm.submitBlockedReason != nil || !clinicalMode)

                        if !clinicalMode {
                            Text("快速量測不綁個案，因此沒有可送出的同意紀錄與 WD 代碼。")
                                .font(.caption2).foregroundStyle(.secondary)
                        }
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
            .fullScreenCover(isPresented: $editing) {
                if let r = vm.result, let img = vm.image {
                    WoundEditView(
                        image: img,
                        initialPolygon: r.woundPolygon,
                        initialPolygons: r.woundPolygons,
                        imageW: r.imageW, imageH: r.imageH,
                        mmPerPx: r.mmPerPx,
                        wbGains: r.wbGains,
                        resume: vm.raster,
                        onCancel: {
                            // ⚠ 取消**不可**設 doctorVerified。醫師按取消代表他沒有背書，
                            //   而一筆從未被人確認的 AI 輸出以「醫師已驗證」進訓練集，
                            //   會讓後續所有模型評估失去意義。
                            editing = false
                        },
                        onDone: { ras in
                            vm.markDoctorVerified(raster: ras)
                            editing = false
                        }
                    )
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

                    if showWound, result.woundPolygon.count >= 3 {
                        Path { p in
                            let pts = result.woundPolygon.map {
                                CGPoint(x: ox + CGFloat($0[0]) * s, y: oy + CGFloat($0[1]) * s)
                            }
                            p.addLines(pts); p.closeSubpath()
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

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(result.areaCm2.map { String(format: "面積：%.2f cm²", $0) } ?? "面積：未校正（無貼紙）")
                .font(.headline)
            Text("PUSH：" + (result.pushPartial.map(String.init) ?? "—")
                 + "（面積＋組織；滲液須醫師輸入才算得出總分）")
                .font(.subheadline)
            Text("組織　肉芽 \(pct(result.tissueFrac["granulation"]))"
                 + "・腐肉 \(pct(result.tissueFrac["slough"]))"
                 + "・壞死 \(pct(result.tissueFrac["necrosis"]))"
                 + "・上皮 \(pct(result.tissueFrac["epithelial"]))"
                 + "・其他 \(pct(result.tissueFrac["other"]))")
                .font(.footnote)
            Text("路由 \(result.route)　信心 \(pct(result.confidence))"
                 + (result.escalated ? "　（難例已上雲集成）" : ""))
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
