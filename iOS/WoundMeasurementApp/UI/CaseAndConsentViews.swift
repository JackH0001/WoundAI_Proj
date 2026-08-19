import SwiftUI
import UIKit   // UIGraphicsImageRenderer / UIColor / UIBezierPath（簽名柵格化）

/**
 個案管理三段式：病患 → 知情同意 → 傷口個案（對等 Android `CaseSelectScreen`）。

 順序不可調換：**沒有 ①照護同意就不該讓人按下量測**。Android 端把這個閘門做在
 `measureEnabled` 上，這裡同樣——按鈕直接 disabled 並說明原因，而不是按下去才擋。
 */
struct CaseSelectView: View {
    @EnvironmentObject var app: AppState
    @State private var patients: [Patient] = []
    @State private var selected: Patient?
    @State private var cases: [WoundCase] = []
    @State private var consent: Consent?
    @State private var signing = false
    @State private var newName = ""
    @State private var newMrn = ""
    /// 新增傷口個案的部位／類型。**不給預設值**——「未指定・未指定」的兩個個案在清單上
    /// 無法分辨，臨床上會量錯傷口（同病房兩位病患都可能是薦骨壓瘡）。
    @State private var newSite = ""
    @State private var newType = ""
    /// 個案摘要（上次面積／變化％／天數）：醫護真正要的決策資訊，
    /// 只有部位＋代碼看不出傷口在不在癒合。
    @State private var summaries: [Int64: CaseSummary] = [:]
    @State private var message: String?

    var body: some View {
        NavigationStack {
            List {
                if let b = app.pendingBanner {
                    Section { Text(b).font(.footnote).foregroundStyle(.orange) }
                }

                if selected == nil {
                    Section("病患") {
                        ForEach(patients) { p in
                            Button {
                                Task { await choose(p) }
                            } label: {
                                VStack(alignment: .leading) {
                                    Text(p.name)
                                    // 顯示一律用遮罩，避免整串病歷號出現在肩窺可見的清單上。
                                    Text(PhiCrypto.maskMrn(p.medicalRecordNumber))
                                        .font(.caption).foregroundStyle(.secondary)
                                }
                            }
                        }
                    }
                    Section("新增病患") {
                        TextField("姓名", text: $newName)
                        TextField("病歷號", text: $newMrn)
                        Button("建立") { Task { await createPatient() } }
                            .disabled(newName.isEmpty || newMrn.isEmpty)
                    }
                } else if let p = selected {
                    Section("病患") {
                        Text(p.name)
                        Text(PhiCrypto.maskMrn(p.medicalRecordNumber))
                            .font(.caption).foregroundStyle(.secondary)
                        Button("換一位") { selected = nil; cases = []; consent = nil }
                    }

                    Section("知情同意") {
                        if let c = consent {
                            Label(c.consentCare ? "①照護同意　已簽署" : "①照護同意　未簽署",
                                  systemImage: c.consentCare ? "checkmark.circle" : "xmark.circle")
                            Label(c.trainEffective ? "②訓練同意　已簽署" : "②訓練同意　未同意／已撤回",
                                  systemImage: c.trainEffective ? "checkmark.circle" : "circle")
                            if c.trainEffective {
                                Button("撤回訓練同意", role: .destructive) {
                                    Task { await withdraw(p) }
                                }
                            }
                        } else {
                            Text("尚未簽署").foregroundStyle(.secondary)
                        }
                        Button("簽署／重新簽署同意書") { signing = true }
                    }

                    Section("傷口個案") {
                        ForEach(cases) { c in
                            // 同一列兩個獨立的點擊區。List 裡預設整列共用一個按鈕動作，
                            // 所以兩顆都要 `.borderless`——否則點哪裡都會觸發第一顆。
                            HStack {
                                Button {
                                    app.chosenCase = c
                                    app.backTo = .cases
                                    app.screen = .measure
                                } label: {
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text("\(c.bodySite)・\(c.woundType)")
                                        Text(c.wdCode).font(.caption).foregroundStyle(.secondary)
                                        // 決策資訊：負＝縮小＝在癒合。±10% 變色與 Android 同閾值。
                                        Text(Self.summaryLine(summaries[c.id]))
                                            .font(.caption2)
                                            .foregroundStyle({ () -> Color in
                                                guard let p = summaries[c.id]?.changePct else { return .secondary }
                                                if p < -10 { return .blue }
                                                if p > 10 { return .red }
                                                return .secondary
                                            }())
                                    }
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .contentShape(Rectangle())
                                }
                                .buttonStyle(.borderless)
                                .disabled(!(consent?.consentCare ?? false))
                                // 長按個案列：結案（有紀錄的正途）／刪除（僅限空個案）。
                                .contextMenu {
                                    Button("結案（紀錄保留，90 天後清影像）") {
                                        Task {
                                            await app.repo.closeCase(id: c.id)
                                            cases = await app.repo.openCases(patientId: p.id)
                                            message = "已結案 \(c.wdCode)。紀錄與趨勢保留，不再出現在開啟清單。"
                                        }
                                    }
                                    Button("刪除（僅限沒有任何量測的個案）", role: .destructive) {
                                        Task {
                                            if await app.repo.deleteCaseIfEmpty(id: c.id) {
                                                cases = await app.repo.openCases(patientId: p.id)
                                                message = "已刪除空個案 \(c.wdCode)。"
                                            } else {
                                                message = "⚠ 此個案已有量測紀錄，不可刪除——病歷要留痕。請改用「結案」。"
                                            }
                                        }
                                    }
                                }

                                // 時間軸**不需要**①照護同意。看既有紀錄與「能不能再拍一張」
                                // 是兩件事：同意撤回之後，已經記錄過的病歷仍然必須看得到，
                                // 那是病歷法規要求，不是功能開關。
                                Button {
                                    app.chosenCase = c
                                    app.backTo = .cases
                                    app.screen = .timeline
                                } label: {
                                    Label("時間軸", systemImage: "chart.xyaxis.line")
                                        .labelStyle(.iconOnly)
                                }
                                .buttonStyle(.borderless)
                            }
                        }
                        if !(consent?.consentCare ?? false) {
                            Text("未取得①照護同意，無法開始量測。")
                                .font(.footnote).foregroundStyle(.secondary)
                        }
                        // 部位／類型必填才能建（對齊 Android：enabled = care && 兩欄非空）。
                        TextField("部位（如 薦骨/右足跟）", text: $newSite)
                            .disabled(!(consent?.consentCare ?? false))
                        TextField("類型（如 壓瘡/糖尿病足）", text: $newType)
                            .disabled(!(consent?.consentCare ?? false))
                        Button("＋新增傷口個案") { Task { await createCase(p) } }
                            .disabled(!(consent?.consentCare ?? false)
                                      || newSite.trimmingCharacters(in: .whitespaces).isEmpty
                                      || newType.trimmingCharacters(in: .whitespaces).isEmpty)
                    }
                }

                if let m = message {
                    Section { Text(m).font(.footnote) }
                }
            }
            .navigationTitle("個案")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("返回") { app.screen = .main }
                }
            }
            .sheet(isPresented: $signing) {
                if let p = selected {
                    ConsentSignatureView(patientLabel: p.name) { care, train, png in
                        Task { await sign(p, care: care, train: train, png: png) }
                        signing = false
                    } onCancel: { signing = false }
                }
            }
            .task {
                patients = await app.repo.listPatients()
                // 進入這一頁時補做未完成的雲端同步——使用者剛好有網路而且在等畫面。
                _ = await ConsentSync.retryPending()
                // 不加 await：`refreshBanner()` 與 `.task` 閉包同隔離，沒有 actor hop。
                // （`app.repo` 是 actor，那些 await 才是真的。理由同 WoundAIApp.swift）
                app.refreshBanner()
            }
        }
    }

    private func choose(_ p: Patient) async {
        selected = p
        cases = await app.repo.openCases(patientId: p.id)
        consent = await app.repo.activeConsent(patientId: p.id)
        await reloadSummaries()
    }

    private func reloadSummaries() async {
        var out: [Int64: CaseSummary] = [:]
        for c in cases { out[c.id] = await app.repo.summary(caseId: c.id) }
        summaries = out
    }

    /// 摘要一行：上次面積・較首次 ±%・N 天前・共 N 次（與 Android 同格式）。
    static func summaryLine(_ s: CaseSummary?) -> String {
        guard let s, s.count > 0 else { return "尚無量測" }
        var t = "上次 " + (s.lastArea.map { String(format: "%.2f cm²", $0) } ?? "—")
        if let c = s.changePct { t += String(format: "・較首次 %+.0f%%", c) }
        if let d = s.daysSinceLast { t += "・\(d) 天前" }
        t += "・共 \(s.count) 次"
        return t
    }

    private func createPatient() async {
        do {
            // 先查重。同一位病患建兩筆會讓時間軸從中間斷開。
            if let dup = try await app.repo.findByMrn(newMrn) {
                message = "此病歷號已存在：\(dup.name)"
                await choose(dup)
                return
            }
            let p = try await app.repo.createPatient(name: newName, mrn: newMrn)
            newName = ""; newMrn = ""
            patients = await app.repo.listPatients()
            await choose(p)
        } catch {
            message = "建立失敗：\(error.localizedDescription)"
        }
    }

    private func createCase(_ p: Patient) async {
        do {
            let c = try await app.repo.createCase(
                patientId: p.id,
                bodySite: newSite.trimmingCharacters(in: .whitespaces),
                woundType: newType.trimmingCharacters(in: .whitespaces))
            newSite = ""; newType = ""
            cases = await app.repo.openCases(patientId: p.id)
            await reloadSummaries()
            message = "✅ 已建立傷口 \(c.wdCode)（此代碼固定不變，回診沿用同一組）"
        } catch {
            message = error.localizedDescription
        }
    }

    private func sign(_ p: Patient, care: Bool, train: Bool, png: Data?) async {
        do {
            _ = try await app.repo.signConsent(patientId: p.id, care: care, train: train,
                                               signaturePng: png)
            consent = await app.repo.activeConsent(patientId: p.id)
            if train {
                // ⚠ 重新簽署必須同時解除**雲端**的撤回封鎖。
                //   少了這一步就是死局：App 顯示「訓練同意 ✓」而雲端持續擋下每一次送出，
                //   錯誤訊息還會把內部端點路徑印給醫師看，而他沒有辦法自己呼叫它。
                let codes = await app.repo.wdCodes(patientId: p.id)
                let r = await ConsentSync.restoreOnBackend(codes: codes)
                if !r.allOK { message = "部分代碼尚未同步到雲端，已排入重試佇列。" }
                app.refreshBanner()
            }
        } catch {
            message = "簽署失敗：\(error.localizedDescription)"
        }
    }

    private func withdraw(_ p: Patient) async {
        // 本機先撤回——病患的撤回是立即生效的權利，不能因為沒訊號就拒絕。
        let codes = await app.repo.withdrawTraining(patientId: p.id, reason: "患者要求")
        consent = await app.repo.activeConsent(patientId: p.id)
        let r = await ConsentSync.pushToBackend(codes: codes)
        message = r.allOK
            ? "已撤回，雲端同步完成。"
            : "本機已撤回；雲端尚有 \(r.pending.count) 筆未完成，已排入重試佇列。"
        app.refreshBanner()
    }
}

// MARK: - 知情同意書

/**
 雙層同意 + 手寫簽名（對等 Android `ConsentSignatureScreen`）。

 ① 照護同意：**必填**。沒有它連拍照都不該讓按。
 ② 訓練同意：選填、可撤回。「不同意不影響照護權益」必須明白寫出來——那是同意書的核心承諾。
 */
struct ConsentSignatureView: View {
    let patientLabel: String
    let onSigned: (Bool, Bool, Data?) -> Void
    let onCancel: () -> Void

    @State private var care = false
    @State private var train = false
    @State private var strokes: [[CGPoint]] = []
    @State private var current: [CGPoint] = []
    @State private var canvasSize: CGSize = .zero

    private var hasSignature: Bool { return !strokes.isEmpty }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    Text("受試者：\(patientLabel)").font(.headline)

                    Toggle(isOn: $care) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("① 同意以影像進行傷口照護與紀錄（必填）")
                            Text("拍攝的影像用於本次與後續回診的傷口評估、面積追蹤與病歷紀錄。")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    }

                    Toggle(isOn: $train) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("② 同意去識別化影像用於 AI 模型訓練（選填，可隨時撤回）")
                            Text("影像在去除姓名、病歷號等識別資訊後，用於改進傷口分割與組織判讀模型；"
                                 + "拍攝時的**去識別深度幾何資料**（LiDAR 深度圖與相機參數，不含任何"
                                 + "可辨識個人的影像）一併用於 3D 量測精度研究。"
                                 + "**不同意不影響您的照護權益**；同意後仍可隨時撤回，"
                                 + "撤回後該影像與深度資料不再納入後續訓練。")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    }

                    Text("受試者簽名").font(.subheadline)
                    // ⚠ 拖曳手勢必須把事件吃掉，否則垂直筆畫會被外層 ScrollView 當成捲動，
                    //   使用者會發現「往下畫」畫不出來。
                    Canvas { ctx, _ in
                        for s in strokes + [current] where s.count > 1 {
                            var p = Path()
                            p.addLines(s)
                            ctx.stroke(p, with: .color(.black), lineWidth: 3)
                        }
                    }
                    .frame(height: 180)
                    .background(Color.white)
                    .border(Color.secondary)
                    .onGeometryChangeCompat { canvasSize = $0 }
                    .gesture(
                        DragGesture(minimumDistance: 0)
                            .onChanged { v in current.append(v.location) }
                            .onEnded { _ in
                                if current.count > 1 { strokes.append(current) }
                                current = []
                            }
                    )
                    Button("清除簽名") { strokes = []; current = [] }.font(.caption)

                    HStack {
                        Button("取消", role: .cancel) { onCancel() }
                        Spacer()
                        Button("確認簽署") {
                            onSigned(care, train, renderSignature())
                        }
                        .buttonStyle(.borderedProminent)
                        // ①與簽名都必要；②可以是 false。
                        .disabled(!care || !hasSignature)
                    }
                }
                .padding()
            }
            .navigationTitle("知情同意書")
        }
    }

    /// 白底黑筆 PNG。**簽名不隨標註上傳**，只加密存在本機。
    private func renderSignature() -> Data? {
        let size = canvasSize == .zero ? CGSize(width: 600, height: 180) : canvasSize
        let r = UIGraphicsImageRenderer(size: size)
        let img = r.image { c in
            UIColor.white.setFill()
            c.fill(CGRect(origin: .zero, size: size))
            UIColor.black.setStroke()
            let path = UIBezierPath()
            path.lineWidth = 3; path.lineJoinStyle = .round; path.lineCapStyle = .round
            for s in strokes where s.count > 1 {
                path.move(to: s[0])
                for p in s.dropFirst() { path.addLine(to: p) }
            }
            path.stroke()
        }
        return img.pngData()
    }
}

/// iOS 17 相容的尺寸觀察（`onGeometryChange` 是 iOS 18+）。
extension View {
    func onGeometryChangeCompat(_ action: @escaping (CGSize) -> Void) -> some View {
        background(
            GeometryReader { g in
                Color.clear.onAppear { action(g.size) }
                    .onChange(of: g.size) { _, s in action(s) }
            }
        )
    }
}

// MARK: - 時間軸

/**
 時間軸。**有選個案就看該個案的病歷，沒有就看未綁個案的快速量測紀錄。**

 後者對應 Android 的 `quickHistory` 畫面。合成同一個 View 是因為兩邊的列一模一樣，
 差別只在查詢條件——而 `unassignedMeasurements()` 若沒有任何入口，快速量測存下去的
 東西就永遠沒有人看得到，那和不存沒有兩樣。
 */
struct TimelineView: View {
    @EnvironmentObject var app: AppState
    @State private var rows: [Measurement] = []
    @State private var caseTitle: String?
    @State private var loaded = false

    /// 時間由舊到新（趨勢圖與「較上次」都以此為準）。
    private var asc: [Measurement] { return rows.sorted { $0.timestamp < $1.timestamp } }

    var body: some View {
        let ascRows = asc
        let areas = ascRows.compactMap { $0.estimatedArea }
        let fmtShort: (Date) -> String = { d in
            let f = DateFormatter(); f.dateFormat = "MM/dd"; return f.string(from: d)
        }
        NavigationStack {
            List {
                if let t = caseTitle {
                    Section { Text(t).font(.footnote).foregroundStyle(.secondary) }
                }
                if loaded && rows.isEmpty {
                    // 空清單長得跟「還沒載入」一樣。這一段是為了讓「真的沒有資料」
                    // 與「載入失敗」看起來不同。
                    Section {
                        Text(caseTitle == nil
                             ? "尚無快速量測紀錄。分析完照片後按「存入時間軸」即可留下紀錄。"
                             : "這個個案還沒有任何量測紀錄。")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                }
                if !rows.isEmpty {
                    Section {
                        // 筆數**無條件顯示**：藏在趨勢摘要裡的話 n=1 時完全看不到累計了幾次。
                        Text("累計 \(rows.count) 次量測"
                             + (rows.count < 2 ? "（趨勢圖需 2 次以上）" : ""))
                            .font(.subheadline)
                        // 快速量測（未歸戶）不畫趨勢：不同影像混在一起，那條線沒有意義。
                        if caseTitle != nil, areas.count >= 2, let first = areas.first, first > 0 {
                            let delta = (areas.last! - first) / first * 100
                            Text(String(format: "面積趨勢：%.2f → %.2f cm²  (較首次 %@%.0f%%)",
                                        first, areas.last!, delta <= 0 ? "↓" : "↑", abs(delta)))
                                .font(.footnote)
                                .foregroundStyle(delta <= -10 ? .blue : (delta >= 10 ? .red : .primary))
                        }
                        if caseTitle != nil, areas.count >= 2 {
                            AreaTrendChart(
                                areas: areas,
                                labels: ascRows.filter { $0.estimatedArea != nil }
                                    .map { fmtShort($0.timestamp) })
                                .frame(height: 150)
                        }
                        if caseTitle != nil {
                            TissueTrendChart(rows: ascRows, labels: ascRows.map { fmtShort($0.timestamp) })
                        }
                    }
                }
                // 新→舊顯示；「較上次 ±%」跟**時間上的前一次**比（用 asc 索引查前值），
                // 臨床看的是這次跟上次有沒有變化，不該讓醫師自己心算。
                ForEach(ascRows.reversed()) { m in
                    let idx = ascRows.firstIndex { $0.id == m.id } ?? 0
                    let prevArea = idx > 0 ? ascRows[idx - 1].estimatedArea : nil
                    // 點卡片 → 複核（回頭修邊／補送標註，不必重測）。
                    // 條件與 Android 同：要有影像座標宣告才可修邊；其餘情況進去也會被
                    // ReviewView 逐項說明擋在哪。
                    Button {
                        app.reviewRecord = m
                        app.screen = .review
                    } label: {
                    HStack(alignment: .top, spacing: 10) {
                        VStack(alignment: .leading, spacing: 2) {
                            HStack(spacing: 6) {
                                Text(m.estimatedArea.map { String(format: "%.2f cm²", $0) } ?? "未校正")
                                    .font(.headline)
                                if let a = m.estimatedArea, let p = prevArea, p > 0 {
                                    let d = (a - p) / p * 100
                                    Text(String(format: "較上次 %+.0f%%", d))
                                        .font(.caption)
                                        .foregroundStyle(d <= -10 ? .blue : (d >= 10 ? .red : .secondary))
                                }
                                if m.doctorVerified {
                                    Label("醫師已確認", systemImage: "checkmark.seal.fill")
                                        .labelStyle(.iconOnly).font(.caption).foregroundStyle(.green)
                                }
                            }
                            Text(m.timestamp.formatted(date: .abbreviated, time: .shortened))
                                .font(.caption).foregroundStyle(.secondary)
                            if let n = m.notes {
                                Text(n).font(.caption2).foregroundStyle(.secondary).lineLimit(3)
                            }
                            // 標註狀態徽章：complete 的紀錄才有資格補送（見 pendingAnnotationCount）。
                            if m.annotationSubmitted {
                                Text("已送訓練").font(.caption2).foregroundStyle(.blue)
                            } else if m.gtPolygon != nil && m.imageId != nil {
                                Text("可補送標註").font(.caption2).foregroundStyle(.orange)
                            }
                        }
                        Spacer(minLength: 0)
                        TimelineThumb(m: m, store: app.imageStore)
                    }
                    }
                    .buttonStyle(.plain)
                    // 長按單筆：只有**沒送出過標註**的可刪（送過的已進雲端 append-only 佇列，
                    // 本機刪了兩邊對不上帳——那種用主控台「誤送排除」）。
                    .contextMenu {
                        if m.annotationSubmitted {
                            Text("此筆已送出訓練標註，不可刪除；誤送請用主控台「誤送排除」。")
                        } else {
                            Button("刪除這筆量測（含加密影像與深度）", role: .destructive) {
                                Task {
                                    if await app.repo.deleteMeasurementIfUnsubmitted(
                                        id: m.id, imageStore: app.imageStore) {
                                        await load()
                                    }
                                }
                            }
                        }
                    }
                }
            }
            .navigationTitle(caseTitle == nil ? "快速量測紀錄" : "時間軸")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("返回") { app.screen = app.backTo }
                }
            }
            .task { await load() }
        }
    }

    /// 明寫 `@MainActor`：`await repo…` 回來時保證回到主執行緒，`rows = …` 才不會
    /// 在背景執行緒上寫 SwiftUI 狀態（那不會崩、也不報錯，只是偶發地畫面不更新）。
    /// 目前的 SwiftUI 會自行推斷同一件事，寫出來是為了讓它不依賴推斷規則。
    @MainActor
    private func load() async {
        if let c = app.chosenCase {
            caseTitle = "\(c.bodySite)・\(c.woundType)　\(c.wdCode)"
            rows = await app.repo.measurements(caseId: c.id)
        } else {
            caseTitle = nil
            rows = await app.repo.unassignedMeasurements()
        }
        loaded = true
    }
}

// MARK: - 設定

struct BackendSettingsView: View {
    @EnvironmentObject var app: AppState
    @State private var url = AppSettings.backendURL()
    @State private var user = AppSettings.backendUser()
    @State private var pass = ""
    @State private var status: String?
    @State private var me: LoginIdentity?
    @State private var client: BackendClient?
    @State private var opening = false
    @Environment(\.openURL) private var openURL

    var body: some View {
        NavigationStack {
            Form {
                Section("後端連線") {
                    TextField("位址", text: $url).autocapitalization(.none)
                    TextField("帳號", text: $user).autocapitalization(.none)
                    SecureField("密碼", text: $pass)
                    Button("儲存並測試連線") { Task { await save() } }
                }
                if let u = me {
                    Section("目前身分・主控台") {
                        Text(u.label()).font(.subheadline)
                        // 一次性登入碼放 URL fragment（# 之後不會送到伺服器、不留雲端日誌），
                        // 60 秒有效、用過即失效；拿不到碼仍開啟網址，只是要手動登入。
                        Button(opening ? "準備登入…" : "開啟雲端主控台（我的送件・佇列）") {
                            opening = true
                            Task {
                                let code = await client?.oneTimeCode()
                                if let target = client?.consoleURL(oneTimeCode: code) {
                                    openURL(target)
                                }
                                if code == nil { status = "ℹ 取不到一次性登入碼，已開啟主控台但需手動登入。" }
                                opening = false
                            }
                        }
                        .disabled(opening)
                    }
                }
                if let s = status {
                    Section("狀態") { Text(s).font(.footnote) }
                }
            }
            .navigationTitle("設定")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("返回") { app.screen = .main }
                }
            }
        }
    }

    private func save() async {
        AppSettings.setBackendURL(url)
        if !pass.isEmpty, !AppSettings.setCredentials(user: user, password: pass) {
            status = "密碼加密失敗，未儲存。"
            return
        }
        let c = BackendClient(baseUrl: AppSettings.backendURL())
        do {
            let h = try await c.health()
            var lines = ["伺服器：\(h.status)"]
            if h.degraded, let r = h.degradedReason {
                // degraded 代表面積或組織判讀其中之一不具參考價值——必須說出來。
                lines.append("⚠ 服務降級：\(r)")
            }
            let ok = (try? await c.login(username: AppSettings.backendUser(),
                                         password: AppSettings.backendPassword())) ?? false
            lines.append(ok ? "登入成功" : "登入失敗（帳號或密碼錯誤）")
            me = ok ? c.currentIdentity() : nil
            client = ok ? c : nil
            if ok {
                let (_, text, _) = await c.flywheelStats(source: nil)
                lines.append(text)
                let r = await ConsentSync.retryPending()
                if !r.done.isEmpty { lines.append("已補做 \(r.done.count) 筆同意同步。") }
                app.refreshBanner()
            }
            status = lines.joined(separator: "\n")
        } catch {
            status = "連線失敗：\(error.localizedDescription)"
        }
    }
}
