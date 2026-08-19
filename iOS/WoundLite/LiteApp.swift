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
        get { d.object(forKey: "lite_research_consent") as? Bool }
        set { d.set(newValue, forKey: "lite_research_consent") }
    }
    /// 裝置匿名代碼：首用隨機生成、不連結任何身分。它是 lite/segment 的
    /// **限流鍵與撤回鍵**（`DELETE /api/v1/lite/data/<anon_id>`），不是識別碼。
    /// ⚠ 上架前要換成 App Attest 裝置證明（後端契約已載明，限流擋不住有意濫用）。
    static var anonId: String {
        if let v = d.string(forKey: "lite_anon_id"), !v.isEmpty { return v }
        let v = UUID().uuidString.lowercased()
        d.set(v, forKey: "lite_anon_id")
        return v
    }
}

struct LiteRootView: View {
    @StateObject private var store = LiteStore()
    @State private var askConsent = false
    /// LiDAR 硬體閘門。App Store 沒有 LiDAR capability key 可以篩機型，只能 App 內把關。
    private let lidarOK = AVCaptureDevice.default(.builtInLiDARDepthCamera,
                                                  for: .video, position: .back) != nil

    var body: some View {
        Group {
            if lidarOK {
                TabView {
                    LiteMeasureView(store: store)
                        .tabItem { Label("量測", systemImage: "camera.metering.center.weighted") }
                    LiteHistoryView(store: store)
                        .tabItem { Label("紀錄", systemImage: "chart.line.uptrend.xyaxis") }
                    LiteSettingsView()
                        .tabItem { Label("設定", systemImage: "gearshape") }
                }
            } else {
                lidarGate
            }
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
        VStack(spacing: 14) {
            Image(systemName: "camera.metering.unknown").font(.system(size: 48))
                .foregroundStyle(.secondary)
            Text("此裝置不支援免貼紙量測").font(.headline)
            Text("WoundLite 以 LiDAR 深度取得真實尺度，需要配備 LiDAR 的 iPhone"
                 + "（iPhone 12 Pro 之後的 Pro 系列機型）。")
                .font(.subheadline).foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
        }
        .padding(32)
    }
}
