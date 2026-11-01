#if DEBUG && targetEnvironment(simulator)
import SwiftUI
import StoreKit

struct LiteCommerceLab: View {
    @StateObject private var store = LiteCommerceStore()
    @Environment(\.scenePhase) private var scenePhase
    @State private var cohort = LiteLabCohort()
    @AppStorage("lite.commerce.synthetic-lab.v1") private var saved = Data()
    @State private var loaded = false
    @State private var readable = true
    @State private var policy = LiteLabSampling.disabled
    @State private var agreed = false
    @State private var subject = "synthetic-001"
    @State private var outcome = "尚未建立收案決定；只有合成測試資料。"
    @State private var adDetails = false
    @State private var previewPlan = LiteLabPlan.free
    @State private var overridePlan = false
    private var plan: LiteLabPlan { overridePlan ? previewPlan : store.plan }
    private var day: String { ISO8601DateFormatter().string(from: Date()).prefix(10).description }

    var body: some View {
        Form {
            Section("本機商業流程驗證") {
                Text("測試交易與合成資料，不會扣款、傳送照片或改動既有研究紀錄。正式服務額度仍依伺服器回覆。")
                Text(store.message).font(.footnote)
                Text(store.plan.label)
                if let product = store.product {
                    Button("測試訂閱 \(product.displayPrice)／月") { Task { await store.purchase() } }
                        .disabled(store.busy)
                }
                Button("恢復測試購買") { Task { await store.restore() } }.disabled(store.busy)
                Button("重新核對訂閱") { Task { await store.refresh() } }
            }
            Section("合成方案預覽") {
                Toggle("使用方案模擬值（不代表已購買）", isOn: $overridePlan)
                if overridePlan {
                    Picker("方案", selection: $previewPlan) {
                        ForEach(LiteLabPlan.allCases, id: \.self) { Text($0.label).tag($0) }
                    }
                }
                Text("今日測試剩餘 \(cohort.remaining(subject: subject, day: day, plan: plan))／\(plan.dailyLimit) 次。升級共 10 次，不是額外 10 次。")
            }
            if LiteLabAdvertisement.visible(plan: plan, placement: .plans) {
                Section("廣告（測試版位）") {
                    Text(LiteLabAdvertisement().title).bold()
                    Text("第一方合作版位；未載入第三方廣告 SDK，不依傷口、部位或研究收案投放。沒有廣告收入。")
                        .font(.footnote)
                    Button("查看測試廣告") { adDetails = true }
                }
            }
            Section("後續改版：研究收案分流") {
                Picker("研究策略", selection: $policy) {
                    ForEach(LiteLabSampling.allCases, id: \.self) { Text($0.label).tag($0) }
                }
                TextField("合成使用身分", text: $subject).textInputAutocapitalization(.never)
                Toggle("合成研究同意", isOn: $agreed)
                Button("建立／讀回固定收案決定") {
                    do {
                        let assignment = try cohort.enroll(subject: subject, policy: policy, agreed: agreed)
                        outcome = assignment.map { "固定決定：\($0.selected ? "收案" : "未收案")；政策 \($0.policy.rawValue)。重開或重新同意不重抽。" }
                            ?? "尚不收案；仍可使用下方合成辨識。"
                    } catch { outcome = "僅接受 synthetic- 開頭的合成身分。" }
                }
                Text("已建立者保留原策略與決定；切換策略只影響新身分。50% 是預期比例，不保證小樣本恰好一半。")
                    .font(.footnote)
                Button("模擬辨識並保存同一傷口時間軸") {
                    do {
                        _ = try cohort.enroll(subject: subject, policy: policy, agreed: agreed)
                        let retained = try cohort.capture(subject: subject, day: day, plan: plan,
                            id: UUID().uuidString, wound: "synthetic-wound-1", mask: "synthetic-mask-v1",
                            researchEnabled: policy != .disabled)
                        outcome = retained ? "辨識完成；本機與合成研究時間軸各新增一筆。" : "辨識完成；結果留本機，不進合成長期研究庫。"
                    } catch { outcome = "未執行：測試額度已用完或輸入無效。" }
                }
                Button("模擬撤回並清理合成研究副本", role: .destructive) {
                    cohort.withdraw(subject: subject); agreed = false
                    outcome = "合成研究副本已清除；本機時間軸、原收案決定及稽核保留。正式資料未受影響。"
                }
                Text(outcome)
                LabeledContent("合成本機時間軸", value: "\(cohort.localTimeline(subject: subject).count) 筆")
                LabeledContent("合成研究庫", value: "\(cohort.researchTimeline(subject: subject).count) 筆")
                Text("合成決定與稽核獨立保存在此模擬器；正式方案需由後端保存決定、驗證訂閱及執行撤回，不可由手機自行授權。")
                    .font(.footnote)
                DisclosureGroup("合成稽核（\(cohort.audit.count) 筆）") {
                    ForEach(Array(cohort.audit.suffix(20).enumerated()), id: \.offset) { _, event in
                        Text(event).font(.caption).textSelection(.enabled)
                    }
                }
            }
            .disabled(!readable)
            if !readable { Text("合成測試紀錄無法讀取，已停止寫入；原始測試資料未覆蓋。") }
        }
        .navigationTitle("訂閱、廣告與收案內測")
        .task {
            if !loaded {
                if !saved.isEmpty {
                    do { cohort = try JSONDecoder().decode(LiteLabCohort.self, from: saved) }
                    catch { readable = false }
                }
                loaded = true
            }
            await store.load()
        }
        .onChange(of: cohort) { _, value in
            guard loaded, readable else { return }
            do { saved = try JSONEncoder().encode(value) }
            catch { readable = false }
        }
        .onChange(of: scenePhase) { _, value in if value == .active { Task { await store.refresh() } } }
        .sheet(isPresented: $adDetails) {
            NavigationStack {
                Text("廣告示意內容。這裡沒有敷料療效推薦、購物連結或外部追蹤。")
                    .padding().navigationTitle("測試贊助版位")
                    .toolbar { Button("完成") { adDetails = false } }
            }
        }
    }
}
#endif
