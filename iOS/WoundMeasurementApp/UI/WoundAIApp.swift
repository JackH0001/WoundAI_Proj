import SwiftUI

/**
 App 進入點與導覽狀態機（對等 Android `MainActivity.WoundMeasurementApp()`）。

 主選單刻意與 Android 同構——同一套教學文件、同一份 SOP 要能同時適用兩個平台，
 臨床人員換手機時不必重學。
 */
@main
struct WoundAIApp: App {
    var body: some Scene {
        WindowGroup { RootView() }
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

    let repo = CaseRepository()
    let imageStore = LocalImageStore()

    func refreshBanner() { pendingBanner = ConsentSync.pendingBanner() }
}

struct RootView: View {
    @StateObject private var app = AppState()

    var body: some View {
        Group {
            switch app.screen {
            case .main:     MainMenuView()
            case .cases:    CaseSelectView()
            case .measure:  MeasureFlowView(clinicalMode: true)
            case .quick:    MeasureFlowView(clinicalMode: false)
            case .timeline: TimelineView()
            case .review:
                // 沒有選定紀錄不該進得來；真的發生就退回時間軸，不要顯示空殼。
                if app.reviewRecord != nil { ReviewView() } else { TimelineView() }
            case .settings: BackendSettingsView()
            case .manual:   ManualView()
            }
        }
        .environmentObject(app)
        .task {
            // 啟動時做三件事，都吞例外——它們失敗不該擋住 App 開啟。
            //  1. 補做未完成的同意同步（撤回／重新取得）
            //  2. 清除逾期影像
            //  3. 預熱 Cloud Run（冷啟動實測 >10s，醫師第一次按下去時最有感）
            _ = await ConsentSync.retryPending()
            // ⚠ 這裡**不要**加 `await`。
            //   舊註解寫的是「`.task` 的閉包是 @Sendable，不繼承 @MainActor 隔離，所以要 await」
            //   ——那在 Xcode 26／Swift 6.2 的 SwiftUI 上已經不成立：`View` 本身帶
            //   `@MainActor @preconcurrency`，`.task` 的閉包與 `refreshBanner()` 同隔離，
            //   沒有 actor hop。多寫一個 await 會拿到
            //   "No 'async' operations occur within 'await' expression"。
            //   下面 `app.repo` 是 `actor`，那個 await 是真的、不可省。
            app.refreshBanner()

            // 90 天影像清除。
            //
            // 掛在**啟動路徑**上，不掛在任何使用者操作後面：PHI 保存期限是法遵要求，
            // 而「醫師這個月剛好沒有打開個案畫面」不是可以晚一個月刪的理由。
            //
            // 清完不提示、也不需要提示——量測數字與組織比例都留著，只有影像本身被移除，
            // 時間軸上會標示「影像已清除」。跳一個對話框要醫師確認，等於把一個
            // 不該由他決定的事情丟給他。
            _ = await app.repo.purgeExpiredImages(imageStore: app.imageStore)

            let backend = BackendClient(baseUrl: AppSettings.backendURL())
            _ = try? await backend.health()
        }
    }
}

struct MainMenuView: View {
    @EnvironmentObject var app: AppState

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 14) {
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
