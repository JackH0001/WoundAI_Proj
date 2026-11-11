import Foundation

/// Presentation adapter for the shared camera and boundary editor. No capture or edit state.
struct LiteCaptureControlsCopy {
    let locale: Locale
    func text(_ source: String) -> String {
        let language = LiteCaptureCopy(locale: locale).english ? "en" : "zh-Hant"
        guard let path = Bundle.main.path(forResource: language, ofType: "lproj"),
              let bundle = Bundle(path: path) else { return source }
        return bundle.localizedString(forKey: source, value: source, table: "LiteCaptureControls")
    }
    func camera(_ source: String) -> String {
        switch source {
        case "📐 LiDAR 深度：擷取中（隨照片加密保存，供 3D 重建研究）":
            return text("LiDAR 可用；拍攝後會核對是否取得可用深度。")
        case "📐 此裝置無 LiDAR 深度（照片照常可量測）":
            return text("目前沒有可用 LiDAR 深度；本次無法提供免貼紙面積估算。")
        case "相機權限未授權。請到 iOS 設定 → WoundAI 開啟相機權限。":
            return text("相機權限未授權。請到 iOS 設定 → WoundAILite 開啟相機權限。")
        case "找不到可用相機（模擬器沒有相機，請改用「相簿」或「檔案」）。":
            return text("找不到可用相機。請在有相機的裝置重試；取消會保留先前的照片與圈選。")
        default:
            // Preserve the platform's complete error detail; never turn an unknown failure into success.
            if source.hasPrefix("拍攝失敗：") {
                let detail = String(source.dropFirst("拍攝失敗：".count))
                return String(format: text("拍攝失敗：%@"), locale: locale,
                              detail == "未知錯誤" ? text(detail) : detail)
            }
            return text(source)
        }
    }
}
