import SwiftUI
import AVFoundation

/**
 WoundLite——民眾版免貼紙傷口量測（App Store 一般用戶）。

 ## 與醫療版（WoundMeasurementApp）的關係

 同一個 repo、同一份 Core（`DepthAreaEstimator`／`DepthCapture`／`MaskTrace`／
 `LocalImageStore`／`BackendClient`）、獨立 target 與 bundle id。醫療版是這裡的
 對照驗證台：貼紙 vs 深度的 phantom 誤差表（2026-08-18 實測）決定了本版的設計：

 - **表面積為主數字**：投影面積隨拍攝角度變（cosθ），民眾手持晃動數字就跟著晃；
   表面積傾角不變且在肢體曲面上才是皮膚實際面積。平滑＋品質閘是前提。
 - **單一中心傷口**：只取最靠近畫面中央的輪廓，與相機中央對焦框同一套構圖語言。
 - **品質不足就不給數字**：比值閘（表面/投影 >1.25）、攝距、傾角、覆蓋率——
   寧可請使用者重拍，也不給一個看起來煞有介事的錯數字。

 ## 輪廓來源（研究同意分流，2026-08-18 產品決策）

 - 同意研究上傳 → 雲端自動辨識（去識別影像＋深度幾何供精度研究與訓練）。
 - 不同意 → 完全離線：手動圈選，照片與深度不離開手機。
 - 本地端分割模型成熟後再考慮第三條路（見 docs/lite_backend_contract.md）。
 */
@main
struct WoundLiteApp: App {
    var body: some Scene {
        WindowGroup { LiteRootView() }
    }
}

/// 民眾版偏好設定。獨立於醫療版（不同 bundle 沙盒，天然隔離）。
enum LitePrefs {
    private static let d = UserDefaults.standard
    /// nil＝還沒問過（首啟要問）；true/false＝使用者的選擇，設定頁可改。
    static var researchConsent: Bool? {
        get {
            guard !LiteWithdrawalJournal.standard.blocksUploads else { return false }
            // An old acceptance must not silently authorize the revised disclosure.
            guard d.string(forKey: "lite_consent_version") == consentVersion else { return nil }
            return d.object(forKey: "lite_research_consent") as? Bool
        }
        set {
            d.set(newValue, forKey: "lite_research_consent")
            d.set(newValue == nil ? nil : consentVersion, forKey: "lite_consent_version")
        }
    }
    /// 同意文案版本。**改了同意頁的實質內容就要遞增**——每筆上傳都帶著它，
    /// 日後治理要能回答「這筆是在哪一版文案下同意的」。
    static let consentVersion = "2026-10-04.1"

    /// Legacy anonymous UUID, retained for old records/support only.
    /// New uploads use the server-assigned App Attest installation; never overwrite old bindings.
    static var anonId: String {
        if let v = d.string(forKey: "lite_anon_id"), !v.isEmpty { return v }
        let v = UUID().uuidString.lowercased()
        d.set(v, forKey: "lite_anon_id")
        return v
    }
}

enum LiteRootTab: Hashable { case measure, history, settings }

struct LiteRootView: View {
    @StateObject private var store = LiteStore()
    @State private var askConsent = false
    @State private var selectedTab: LiteRootTab = .measure
    /// Hardware gates new capture only; records and privacy controls stay accessible.
    let lidarOK: Bool

    init(lidarAvailable: Bool = AVCaptureDevice.default(.builtInLiDARDepthCamera,
                                                       for: .video, position: .back) != nil) {
        lidarOK = lidarAvailable
    }

    var body: some View {
        TabView(selection: $selectedTab) {
            Group {
                if lidarOK { LiteMeasureView(store: store) }
                else { lidarGate }
            }
            .tag(LiteRootTab.measure)
            .tabItem { Label("量測", systemImage: "camera.metering.center.weighted") }
            LiteHistoryView(store: store)
                .tag(LiteRootTab.history)
                .tabItem { Label("紀錄", systemImage: "chart.line.uptrend.xyaxis") }
            LiteSettingsView()
                .tag(LiteRootTab.settings)
                .tabItem { Label("設定", systemImage: "gearshape") }
        }
        .onAppear { if lidarOK, LitePrefs.researchConsent == nil { askConsent = true } }
        .fullScreenCover(isPresented: $askConsent) {
            LiteConsentView { agreed in
                LitePrefs.researchConsent = agreed
                askConsent = false
            }
        }
    }

    private var lidarGate: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 18) {
                    Image(systemName: "camera.metering.unknown")
                        .font(.system(size: 48)).foregroundStyle(.secondary)
                    Text("此裝置無法拍攝深度量測").font(.title3.bold())
                    Text("本裝置未偵測到可用的 LiDAR 深度相機，無法拍攝新的傷口面積量測。")
                        .foregroundStyle(.secondary)
                    Text("您仍可查看此裝置已有的紀錄、設定、隱私說明與資料撤回。")
                    Button { selectedTab = .history } label: {
                        Label("查看既有紀錄", systemImage: "chart.line.uptrend.xyaxis")
                            .frame(maxWidth: .infinity, minHeight: 44)
                    }
                    .buttonStyle(.borderedProminent)
                    Button { selectedTab = .settings } label: {
                        Label("設定與資料撤回", systemImage: "gearshape")
                            .frame(maxWidth: .infinity, minHeight: 44)
                    }
                    .buttonStyle(.bordered)
                    NavigationLink("量測精確度與限制") { LiteMeasurementInfoView() }
                }
                .multilineTextAlignment(.center)
                .padding(24)
            }
            .navigationTitle("量測")
        }
    }
}
