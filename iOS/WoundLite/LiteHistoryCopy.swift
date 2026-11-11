import Foundation

/// Read-only presentation. Never translate IDs, persisted status, user names or receipts.
struct LiteHistoryCopy {
    let locale: Locale
    private var report: LiteReportCopy { LiteReportCopy(locale: locale) }
    var english: Bool { report.english }
    func text(_ source: String) -> String {
        if let path = Bundle.main.path(forResource: english ? "en" : "zh-Hant", ofType: "lproj"),
           let bundle = Bundle(path: path) {
            let value = bundle.localizedString(forKey: source, value: source, table: "LiteHistory")
            if value != source { return value }
        }
        let known = report.text(source)
        return known == source ? LiteResearchCopy(locale: locale).text(source) : known
    }
    func format(_ source: String, _ arguments: String...) -> String {
        String(format: text(source), locale: locale, arguments: arguments)
    }
    func decimal(_ value: Double) -> String { report.decimal(value) }
    func location(_ value: LiteWoundLocation?) -> String { report.location(value) }
    func group(_ value: LiteWoundGroup) -> String {
        value.name + (english ? ", " : "・") + (value.location.map { location($0) } ?? text("待補位置"))
    }
    func trackingLabel(_ value: LiteWoundGroup) -> String {
        guard value.account != nil, let wire = try? LiteWoundRoster.wireID(value.id) else { return group(value) }
        return group(value) + (english ? " (" : "（") + wire.suffix(6).uppercased() + (english ? ")" : "）")
    }
    func date(_ iso: String) -> String {
        guard let date = LiteRecord.captureDate(iso) else { return iso }
        let f = DateFormatter(); f.locale = locale
        f.dateFormat = english ? "d MMM yyyy, HH:mm" : "M/d HH:mm"
        return f.string(from: date)
    }
    func roster(_ value: LiteWoundRoster?) -> String {
        guard let value else { return text("免費可追蹤 1 處，Plus 每帳戶最多 5 處。") }
        let current = format("可續記 %@ 處，方案上限 %@ 處。", String(min(value.active.count, value.limit)), String(value.limit))
        let excess = max(0, value.active.count - value.limit)
        return excess == 0 ? current : current + (english ? " " : "")
            + format("另有 %@ 處保留在名單中。恢復 Plus 或結束前方傷口後可再續記。", String(excess))
    }
    func change(_ value: LiteContourChangeSummary) -> String {
        let old = decimal(value.before), new = decimal(value.after)
        if !value.comparable {
            return format("圈選已修正 · LiDAR 表面積估算 %@ cm²。原值來源不同或品質資訊不足，僅顯示這次估算。", new)
        }
        return old == new ? format("圈選已修正；LiDAR 表面積估算仍顯示 %@ cm²。", new)
            : format("圈選已修正 · LiDAR 表面積估算 %@ → %@ cm²", old, new)
    }
    func message(_ source: String) -> String {
        guard english else { return source }
        // Only complete, known generated messages; never replace arbitrary substrings.
        let templates = [
            "雲端已確認第 %@ 版", "第 %@ 版等待服務處理", "正在同步第 %@ 版…", "第 %@ 版尚未獲雲端確認",
            "目前方案同時可追蹤 %@ 處。請先在紀錄頁結束一處追蹤，再新增傷口。"
        ]
        for template in templates {
            let pattern = "\\A" + NSRegularExpression.escapedPattern(for: template)
                .replacingOccurrences(of: "%@", with: "([0-9]{1,16})") + "\\z"
            guard let regex = try? NSRegularExpression(pattern: pattern),
                  let match = regex.firstMatch(in: source, range: NSRange(source.startIndex..., in: source)),
                  let range = Range(match.range(at: 1), in: source) else { continue }
            return format(template, String(source[range]))
        }
        let prefix = "修正圈選後品質不足，未更新紀錄。"
        if source.hasPrefix(prefix) {
            return text(prefix) + " " + report.warning(String(source.dropFirst(prefix.count)))
        }
        let known = text(source)
        return known == source ? report.warning(source) : known
    }
    func revisionDetail(_ status: LiteRevisionPresentation) -> String {
        if english, status.phase == .waiting {
            let suffix = " 後自動續傳；可先離開此頁。"
            if status.detail.hasSuffix(suffix) {
                // The clock text came from the existing wait state. Do not recalculate
                // the deadline, set a timer or create a revision to change its language.
                return format("%@ 後自動續傳；可先離開此頁。", String(status.detail.dropLast(suffix.count)))
            }
        }
        return message(status.detail)
    }
}
