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
                            Button {
                                app.chosenCase = c
                                app.backTo = .cases
                                app.screen = .measure
                            } label: {
                                VStack(alignment: .leading) {
                                    Text("\(c.bodySite)・\(c.woundType)")
                                    Text(c.wdCode).font(.caption).foregroundStyle(.secondary)
                                }
                            }
                            .disabled(!(consent?.consentCare ?? false))
                        }
                        if !(consent?.consentCare ?? false) {
                            Text("未取得①照護同意，無法開始量測。")
                                .font(.footnote).foregroundStyle(.secondary)
                        }
                        Button("新增傷口個案") { Task { await createCase(p) } }
                            .disabled(!(consent?.consentCare ?? false))
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
                await app.refreshBanner()
            }
        }
    }

    private func choose(_ p: Patient) async {
        selected = p
        cases = await app.repo.openCases(patientId: p.id)
        consent = await app.repo.activeConsent(patientId: p.id)
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
            _ = try await app.repo.createCase(patientId: p.id, bodySite: "未指定", woundType: "未指定")
            cases = await app.repo.openCases(patientId: p.id)
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
                            Text("影像在去除姓名、病歷號等識別資訊後，用於改進傷口分割與組織判讀模型。"
                                 + "**不同意不影響您的照護權益**；同意後仍可隨時撤回，"
                                 + "撤回後該影像不再納入後續訓練。")
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

struct TimelineView: View {
    @EnvironmentObject var app: AppState
    @State private var rows: [Measurement] = []

    var body: some View {
        NavigationStack {
            List(rows) { m in
                VStack(alignment: .leading, spacing: 2) {
                    Text(m.estimatedArea.map { String(format: "%.2f cm²", $0) } ?? "未校正")
                        .font(.headline)
                    Text(m.timestamp.formatted(date: .abbreviated, time: .shortened))
                        .font(.caption).foregroundStyle(.secondary)
                    if let n = m.notes { Text(n).font(.caption2).foregroundStyle(.secondary) }
                }
            }
            .navigationTitle("時間軸")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("返回") { app.screen = app.backTo }
                }
            }
            .task {
                if let c = await app.chosenCase { rows = await app.repo.measurements(caseId: c.id) }
            }
        }
    }
}

// MARK: - 設定

struct BackendSettingsView: View {
    @EnvironmentObject var app: AppState
    @State private var url = AppSettings.backendURL()
    @State private var user = AppSettings.backendUser()
    @State private var pass = ""
    @State private var status: String?

    var body: some View {
        NavigationStack {
            Form {
                Section("後端連線") {
                    TextField("位址", text: $url).autocapitalization(.none)
                    TextField("帳號", text: $user).autocapitalization(.none)
                    SecureField("密碼", text: $pass)
                    Button("儲存並測試連線") { Task { await save() } }
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
