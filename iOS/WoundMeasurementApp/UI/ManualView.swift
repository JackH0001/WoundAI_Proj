import SwiftUI
import WebKit

/**
 使用說明書（對等 Android `ManualScreen`）。內容是與 Android **同一份** `manual.html`
 （bundle 內建、離線可看）——最需要手冊的時刻是操作卡住的當下，那時等不了網路；
 兩端共用同一份檔，按鈕名稱與流程說明才不會分岔。

 WKWebView 只開 JavaScript（角色分頁切換用），不給網路與檔案存取——
 手冊是自家靜態檔，沒有理由讓它有更多能力。
 */
struct ManualView: View {
    @EnvironmentObject var app: AppState

    var body: some View {
        NavigationStack {
            ManualWebView()
                .ignoresSafeArea(edges: .bottom)
                .navigationTitle("使用說明書")
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        Button("返回") { app.screen = .main }
                    }
                }
        }
    }
}

private struct ManualWebView: UIViewRepresentable {
    func makeUIView(context: Context) -> WKWebView {
        let cfg = WKWebViewConfiguration()
        cfg.defaultWebpagePreferences.allowsContentJavaScript = true
        let v = WKWebView(frame: .zero, configuration: cfg)
        v.isOpaque = false
        if let url = Bundle.main.url(forResource: "manual", withExtension: "html") {
            // loadFileURL 只授權該檔所在目錄，不開放整個沙箱。
            v.loadFileURL(url, allowingReadAccessTo: url.deletingLastPathComponent())
        } else {
            v.loadHTMLString("<h3>找不到手冊檔（manual.html 未進 bundle——請重跑 xcodegen）</h3>",
                             baseURL: nil)
        }
        return v
    }
    func updateUIView(_ uiView: WKWebView, context: Context) {}
}
