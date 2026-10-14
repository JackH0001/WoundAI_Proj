import SwiftUI
import Combine
#if WOUND_INSTITUTION
import GoogleSignIn
#endif

/**
 App 進入點與導覽狀態機（對等 Android `MainActivity.WoundMeasurementApp()`）。

 主選單刻意與 Android 同構——同一套教學文件、同一份 SOP 要能同時適用兩個平台，
 臨床人員換手機時不必重學。
 */
@main
struct WoundAIApp: App {
    var body: some Scene {
        WindowGroup {
            RootView()
                #if WOUND_INSTITUTION
                .onOpenURL { url in _ = GIDSignIn.sharedInstance.handle(url) }
                #endif
        }
    }
}

/// 導覽目的地。與 Android 的 `currentScreen` 字串一一對應。
enum Screen: Hashable {
    case main
    case cases          // 個案（病患・同意書・個案傷口・量測）
    case measure        // 量測（臨床模式，需 case）
    case quick          // 快速量測（範例／模擬圖・不綁個案）
    case timeline       // 個案時間軸
    case review         // 單筆複核：重新修邊／補送標註（從時間軸點紀錄進入）
    case settings       // 後端連線・帳號・佇列健康度
    case manual         // 使用說明書（離線）
}

@MainActor
final class AppState: ObservableObject {
    @Published var screen: Screen = .main
    /// 從哪來回哪去。Android 用 `backTo`，這裡同義。
    @Published var backTo: Screen = .main
    @Published var chosenCase: WoundCase?
    /// 從時間軸點進來要複核的那一筆。離開複核頁時清空。
    @Published var reviewRecord: Measurement?
    @Published var identity: LoginIdentity?
    @Published var pendingBanner: String?

    let runtime: MedicalRuntime
    var repo: CaseRepository { runtime.local.repo }
    var imageStore: LocalImageStore { runtime.local.images }
    init(runtime: MedicalRuntime) { self.runtime = runtime }
    func refreshBanner() { pendingBanner = ConsentSync.pendingBanner(context: runtime.consent) }
}

struct RootView: View {
    @State private var app: AppState?
    @State private var issue: String?
    @State private var switching = false
    var body: some View {
        Group {
            if let app { MedicalRootView(app: app, switchEnvironment: activate).id(app.runtime.id) }
            else { ProgressView("正在開啟本機紀錄…") }
        }
        .allowsHitTesting(!switching)
        .overlay {
            if switching { ProgressView("正在切換環境，保存中的工作會留在原環境…").padding().background(.regularMaterial).cornerRadius(12) }
        }
        .task {
            guard app == nil else { return }
            #if WOUND_INSTITUTION
            let saved = UserDefaults.standard.string(forKey: "medical.selectedEnvironment")
            let scope = MedicalDataScope(rawValue: saved ?? "") ?? .mmh
            activate(scope == .syntheticReview && !MedicalRuntime.reviewConfigured ? .mmh : scope)
            #else
            activate(.mmh)
            #endif
        }
        .alert("無法切換環境", isPresented: Binding(get: { issue != nil }, set: { if !$0 { issue = nil } })) {
            Button("確定") { issue = nil }
        } message: { Text(issue ?? "") }
    }
    private func activate(_ scope: MedicalDataScope) {
        guard !switching, app?.runtime.scope != scope else { return }
        switching = true
        Task { @MainActor in
            defer { switching = false }
            do {
                let replacement = try MedicalRuntime(scope: scope)
                let previous = app?.runtime
                previous?.deactivate()
                // Do not reopen an environment's files while its previous worker
                // can still persist an acknowledgement or retry state.
                await previous?.waitUntilSuspended()
                app = AppState(runtime: replacement)
                UserDefaults.standard.set(scope.rawValue, forKey: "medical.selectedEnvironment")
            } catch { issue = "環境設定或本機儲存未能開啟。原環境與紀錄已保留，請稍後重試。" }
        }
    }
}

private struct MedicalRootView: View {
    @ObservedObject var app: AppState
    @ObservedObject private var uploads: ClinicalUploadOutbox
    let switchEnvironment: (MedicalDataScope) -> Void
    init(app: AppState, switchEnvironment: @escaping (MedicalDataScope) -> Void) {
        self.app = app; self.switchEnvironment = switchEnvironment
        uploads = app.runtime.local.outbox
    }
    @Environment(\.scenePhase) private var scenePhase
    private let retryTimer = Timer.publish(every: 60, on: .main, in: .common).autoconnect()

    var body: some View {
        Group {
            switch app.screen {
            case .main:     MainMenuView(switchEnvironment: switchEnvironment)
            case .cases:    CaseSelectView()
            case .measure:  MeasureFlowView(clinicalMode: true, runtime: app.runtime)
            case .quick:    MeasureFlowView(clinicalMode: false, runtime: app.runtime)
            case .timeline: TimelineView(runtime: app.runtime)
            case .review:
                // 沒有選定紀錄不該進得來；真的發生就退回時間軸，不要顯示空殼。
                if app.reviewRecord != nil { ReviewView(runtime: app.runtime) } else { TimelineView(runtime: app.runtime) }
            case .settings: BackendSettingsView(runtime: app.runtime)
            case .manual:   ManualView()
            }
        }
        .safeAreaInset(edge: .bottom) {
            if let issue = uploads.storageIssue {
                Text(issue).font(.footnote).foregroundStyle(.red).padding().background(.regularMaterial)
            } else if uploads.pendingCount > 0 {
                HStack {
                    if uploads.activeID != nil { ProgressView() }
                    VStack(alignment: .leading) {
                        Text(uploads.activeID == nil ? "研究資料待傳：\(uploads.pendingCount) 筆" : uploads.phase)
                            .font(.footnote).bold()
                        Text("臨床紀錄已保存在手機，可繼續操作；中斷後回到 App 會續傳。")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    Button("重試") { app.runtime.resumeUploads(force: true) }
                        .disabled(uploads.activeID != nil)
                }.padding(10).background(.regularMaterial)
            }
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active { app.runtime.resumeUploads() }
        }
        .onReceive(retryTimer) { _ in
            if scenePhase == .active { app.runtime.resumeUploads() }
        }
        .environmentObject(app)
        .task {
            #if WOUND_INSTITUTION
            if app.runtime.needsGoogle { await InstitutionGoogleSession.shared.restoreIfNeeded() }
            #endif
            // 啟動時做三件事，都吞例外——它們失敗不該擋住 App 開啟。
            //  1. 補做未完成的同意同步（撤回／重新取得）
            //  2. 清除逾期影像
            //  3. 預熱 Cloud Run（冷啟動實測 >10s，醫師第一次按下去時最有感）
            _ = await ConsentSync.retryPending(context: app.runtime.consent)
            // ⚠ 這裡**不要**加 `await`。
            //   舊註解寫的是「`.task` 的閉包是 @Sendable，不繼承 @MainActor 隔離，所以要 await」
            //   ——那在 Xcode 26／Swift 6.2 的 SwiftUI 上已經不成立：`View` 本身帶
            //   `@MainActor @preconcurrency`，`.task` 的閉包與 `refreshBanner()` 同隔離，
            //   沒有 actor hop。多寫一個 await 會拿到
            //   "No 'async' operations occur within 'await' expression"。
            //   下面 `app.repo` 是 `actor`，那個 await 是真的、不可省。
            app.refreshBanner()
            app.runtime.resumeUploads()

            // 90 天影像清除。
            //
            // 掛在**啟動路徑**上，不掛在任何使用者操作後面：PHI 保存期限是法遵要求，
            // 而「醫師這個月剛好沒有打開個案畫面」不是可以晚一個月刪的理由。
            //
            // 清完不提示、也不需要提示——量測數字與組織比例都留著，只有影像本身被移除，
            // 時間軸上會標示「影像已清除」。跳一個對話框要醫師確認，等於把一個
            // 不該由他決定的事情丟給他。
            _ = await app.repo.purgeExpiredImages(imageStore: app.imageStore)

            #if !WOUND_INSTITUTION
            let backend = app.runtime.makeBackend()
            _ = try? await backend.health()
            #endif
            // MMH restore already probed IAP health. Avoid a redundant serialized warm-up request.
        }
    }
}

struct MainMenuView: View {
    @EnvironmentObject var app: AppState
    let switchEnvironment: (MedicalDataScope) -> Void

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 14) {
                #if WOUND_INSTITUTION
                Text(app.runtime.title).font(.headline).accessibilityIdentifier("MedicalEnvironment")
                if app.runtime.scope == .syntheticReview {
                    Text("僅供合成影像操作示範。請勿輸入真實病患資料；示範紀錄與 MMH 分開保存。")
                        .font(.footnote).foregroundStyle(.orange)
                    Button("返回 MMH 機構服務") { switchEnvironment(.mmh) }.buttonStyle(.bordered)
                } else if MedicalRuntime.reviewConfigured {
                    Button("進入合成示範（不需 Google）") { switchEnvironment(.syntheticReview) }
                        .buttonStyle(.bordered).accessibilityIdentifier("OpenSyntheticReview")
                }
                #endif
                if let b = app.pendingBanner {
                    // 常駐橫幅。一次性的 toast 會被滑掉，而沒完成的同意同步必須一直看得見。
                    Text(b)
                        .font(.footnote)
                        .padding(10)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Color.orange.opacity(0.18))
                        .cornerRadius(8)
                }

                MainButton("個案（病患・同意書・個案傷口・量測）") {
                    app.backTo = .main
                    app.screen = .cases
                }
                MainButton("快速量測（範例／模擬圖・不綁個案）") {
                    // ⚠ 一定要清掉 `chosenCase`。它是全域狀態，上一次臨床量測選的個案會留著，
                    //   而快速量測畫面**不顯示個案標題**——所以那個殘留是看不見的。
                    //   只要哪天存檔路徑漏了 `clinicalMode ?` 那個三元判斷，
                    //   範例圖就會被寫進真實病患的病歷，而且沒有任何跡象。
                    app.chosenCase = nil
                    app.backTo = .main
                    app.screen = .quick
                }
                MainButton("快速量測紀錄（未綁個案的量測）") {
                    app.chosenCase = nil
                    app.backTo = .main
                    app.screen = .timeline
                }
                MainButton("設定（後端連線・帳號・佇列健康度）") {
                    app.backTo = .main
                    app.screen = .settings
                }

                // 手冊放最後、樣式較輕（bordered 非 prominent）：查閱用，不與操作入口爭視覺重量；
                // 但一定要在首頁——需要手冊的時刻是卡住的當下，不會有人去設定裡翻。
                Button {
                    app.screen = .manual
                } label: {
                    Text("使用說明書（依角色分頁・可離線查閱）")
                        .font(.system(size: 16))
                        .frame(maxWidth: .infinity, minHeight: 48)
                }
                .buttonStyle(.bordered)

                Text("臨床量測請由『個案』進入；『快速量測』的結果不會綁定病患，僅供範例與模擬圖驗證。")
                    .font(.footnote)
                    .foregroundStyle(.secondary)

                Spacer()

                Text("輔助用途、非診斷，需醫師確認。")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            .padding()
            .navigationTitle("WoundAI")
        }
    }
}

struct MainButton: View {
    let title: String
    let action: () -> Void
    init(_ title: String, action: @escaping () -> Void) { self.title = title; self.action = action }

    var body: some View {
        Button(action: action) {
            Text(title)
                .font(.system(size: 18))
                .frame(maxWidth: .infinity, minHeight: 56)
        }
        .buttonStyle(.borderedProminent)
    }
}
