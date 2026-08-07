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
    case settings       // 後端連線・帳號・佇列健康度
}

@MainActor
final class AppState: ObservableObject {
    @Published var screen: Screen = .main
    /// 從哪來回哪去。Android 用 `backTo`，這裡同義。
    @Published var backTo: Screen = .main
    @Published var chosenCase: WoundCase?
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
            case .settings: BackendSettingsView()
            }
        }
        .environmentObject(app)
        .task {
            // 啟動時做兩件事，都吞例外——它們失敗不該擋住 App 開啟。
            //  1. 補做未完成的同意同步（撤回／重新取得）
            //  2. 預熱 Cloud Run（冷啟動實測 >10s，醫師第一次按下去時最有感）
            _ = await ConsentSync.retryPending()
            app.refreshBanner()
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

                MainButton("個案（病患・同意書・個案傷口・量測）") { app.screen = .cases }
                MainButton("快速量測（範例／模擬圖・不綁個案）") { app.screen = .quick }
                MainButton("設定（後端連線・帳號・佇列健康度）") { app.screen = .settings }

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
