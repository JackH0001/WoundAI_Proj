import Foundation

/// Read-only capture presentation. Never modify the capture/journal for a language change.
struct LiteCaptureCopy {
    let locale: Locale
    var history: LiteHistoryCopy { LiteHistoryCopy(locale: locale) }
    var english: Bool { history.english }
    func text(_ source: String) -> String {
        if let path = Bundle.main.path(forResource: english ? "en" : "zh-Hant", ofType: "lproj"),
           let bundle = Bundle(path: path) {
            let value = bundle.localizedString(forKey: source, value: source, table: "LiteCapture")
            if value != source { return value }
        }
        return history.text(source)
    }
    func format(_ source: String, _ arguments: String...) -> String {
        String(format: text(source), locale: locale, arguments: arguments)
    }
    func confirmation(_ target: LiteWoundSaveTarget) -> String {
        // Only an empty target ID identifies the generated "new wound" label.
        // An existing user name with exactly the same spelling stays verbatim.
        let name = target.woundID.isEmpty ? text("新增傷口") : target.name
        return format("傷口：%@\n位置：%@\n\n請確認本次拍攝的是這一處傷口，再存入紀錄。", name, history.location(target.location))
    }
    func message(_ source: String) -> String {
        guard english else { return source }
        let known = text(source)
        if known != source { return known }
        for template in [
            "已自動取畫面中央的傷口（偵測到 %@ 處，其餘忽略）。可用「修正圈選」微調。",
            "有 %@ 筆拍攝尚待核對。請解鎖後點「重新核對本機拍攝」。"
        ] {
            let pattern = "\\A" + NSRegularExpression.escapedPattern(for: template)
                .replacingOccurrences(of: "%@", with: "([0-9]{1,16})") + "\\z"
            guard let regex = try? NSRegularExpression(pattern: pattern),
                  let match = regex.firstMatch(in: source, range: NSRange(source.startIndex..., in: source)),
                  let range = Range(match.range(at: 1), in: source) else { continue }
            return format(template, String(source[range]))
        }
        let historical = history.message(source)
        if historical != source { return historical }
        // Legacy receipts are concatenated with these two fixed instructions.
        // Translate them only when the entire receipt remainder is recognized.
        let prefix = "雲端未辨識到傷口，請手動圈選。", suffix = "可於設定停止未來上傳。"
        if source.hasPrefix(prefix) {
            let remainder = String(source.dropFirst(prefix.count)), rendered = message(remainder)
            if rendered != remainder { return text(prefix) + " " + rendered }
        }
        if source.hasSuffix(suffix) {
            let remainder = String(source.dropLast(suffix.count)), rendered = message(remainder)
            if rendered != remainder { return rendered + " " + text(suffix) }
        }
        if source.contains("\n") { return source.components(separatedBy: "\n").map { message($0) }.joined(separator: "\n") }
        return source // Preserve unknown error details rather than claiming success.
    }
    func projection(_ e: DepthAreaResult) -> String {
        format("相機方向投影估算 %@ cm²・攝距 %@ cm・深度覆蓋 %@%%", history.decimal(e.projectedAreaCm2),
               String(format: "%.0f", locale: Locale(identifier: "en_US_POSIX"), e.medianDistanceM * 100),
               String(Int(e.coverage * 100)))
    }
}
