import SwiftUI
import WebKit

struct LiteTermsLink: View {
    @Environment(\.locale) private var locale
    private func copy(_ source: String) -> String { LiteServiceCopy.text(source, locale: locale) }
    var body: some View {
        Link(copy("Apple 標準使用條款"), destination: URL(string: "https://www.apple.com/legal/internet-services/itunes/dev/stdeula/")!)
            .accessibilityIdentifier("lite.legal.terms")
    }
}

struct LitePrivacyLink: View {
    @Environment(\.locale) private var locale
    private func copy(_ source: String) -> String { LiteServiceCopy.text(source, locale: locale) }
    var body: some View {
        NavigationLink(copy("隱私權政策"), destination: LitePrivacyView())
            .accessibilityIdentifier("lite.legal.privacy")
    }
}

private struct LitePrivacyView: View {
    @Environment(\.locale) private var locale
    private func copy(_ source: String) -> String { LiteServiceCopy.text(source, locale: locale) }
    var body: some View {
        Group {
            if let url = Bundle.main.url(forResource: "woundlite", withExtension: "html"),
               let html = try? String(contentsOf: url, encoding: .utf8) {
                LitePrivacyDocument(html: html)
            } else {
                Text(copy("此版本缺少隱私權政策，請聯絡資料處理支援。"))
            }
        }
        .navigationTitle(copy("隱私權政策"))
        .navigationBarTitleDisplayMode(.inline)
    }
}

private struct LitePrivacyDocument: UIViewRepresentable {
    let html: String
    @Environment(\.openURL) private var openURL
    func makeCoordinator() -> Coordinator { Coordinator(openURL: openURL) }
    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.defaultWebpagePreferences.allowsContentJavaScript = false
        config.websiteDataStore = .nonPersistent()
        let view = WKWebView(frame: .zero, configuration: config)
        view.navigationDelegate = context.coordinator
        // The website footer goes to the policy index. The app has its own Back button.
        view.loadHTMLString(html.replacingOccurrences(of: "<footer>", with: "<footer hidden>"), baseURL: nil)
        return view
    }
    func updateUIView(_ view: WKWebView, context: Context) {}
    final class Coordinator: NSObject, WKNavigationDelegate {
        let openURL: OpenURLAction
        init(openURL: OpenURLAction) { self.openURL = openURL }
        func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                     decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
            let url = action.request.url
            if action.navigationType == .linkActivated && url?.absoluteString == "mailto:jack.hou@gmail.com",
               let url { openURL(url) }
            decisionHandler(action.navigationType == .other && url?.absoluteString == "about:blank" ? .allow : .cancel)
        }
    }
}
