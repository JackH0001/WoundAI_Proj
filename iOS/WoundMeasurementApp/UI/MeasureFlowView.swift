import SwiftUI
import PhotosUI

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

    func markDoctorVerified(raster: EditRaster) {
        self.raster = raster
        self.doctorVerified = true
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

                        if let reason = vm.submitBlockedReason {
                            Text("送出訓練標註：\(reason)")
                                .font(.footnote).foregroundStyle(.secondary)
                        }
                        Button("送出訓練標註") { }
                            .buttonStyle(.borderedProminent)
                            .disabled(vm.submitBlockedReason != nil)
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
