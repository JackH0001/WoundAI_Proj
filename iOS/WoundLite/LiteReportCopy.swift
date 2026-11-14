import Foundation

/// Export presentation is frozen separately from the record, timezone and
/// numeric measurement. User-entered names and unknown historical notes remain verbatim.
struct LiteReportCopy {
    static let originalLocale = Locale(identifier: "zh_Hant_TW")
    let locale: Locale
    var english: Bool { locale.language.languageCode?.identifier == "en" }
    func text(_ source: String) -> String {
        guard let path = Bundle.main.path(forResource: english ? "en" : "zh-Hant", ofType: "lproj"),
              let bundle = Bundle(path: path) else { return source }
        return bundle.localizedString(forKey: source, value: source, table: "LiteReport")
    }
    func format(_ source: String, _ arguments: String...) -> String {
        String(format: text(source), locale: locale, arguments: arguments)
    }
    func decimal(_ value: Double) -> String { String(format: "%.2f", locale: Locale(identifier: "en_US_POSIX"), value) }
    func location(_ value: LiteWoundLocation?) -> String {
        guard let value else { return text("位置未填寫") }
        let side = LiteWoundLocation.sides.first { $0.0 == value.side }?.1 ?? "未選側別"
        let site = LiteWoundLocation.sites.first { $0.0 == value.site }?.1 ?? "未選部位"
        return text(side) + (english ? ", " : "・") + text(site)
    }
    func group(_ value: LiteWoundGroup) -> String {
        value.name + "・" + (value.location.map { location($0) } ?? text("待補位置"))
    }
    func warning(_ original: String) -> String {
        guard english else { return original }
        // Match only the three complete, known numeric messages. Do not rewrite
        // arbitrary notes or persist the translated value back into a record.
        let templates = [
            "拍攝角度偏斜（估計約 %@°），辨識邊界與面積誤差可能增加。結果僅供參考，建議正對傷口重拍。",
            "拍攝略斜（估計約 %@°）。建議正對傷口拍攝，並確認圈選邊界。",
            "距離偏遠（約 %@ 公分），建議 25–40 公分。"
        ]
        for template in templates {
            let pattern = "\\A" + NSRegularExpression.escapedPattern(for: template)
                .replacingOccurrences(of: "%@", with: "([0-9]{1,3})") + "\\z"
            guard let regex = try? NSRegularExpression(pattern: pattern),
                  let match = regex.firstMatch(in: original, range: NSRange(original.startIndex..., in: original)),
                  let range = Range(match.range(at: 1), in: original) else { continue }
            return format(template, String(original[range]))
        }
        return text(original)
    }
}
