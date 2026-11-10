import Foundation

/// Presentation only. Never localize storage keys, consent versions, receipts,
/// transaction identifiers, or user-entered content. Other Lite screens are
/// localized separately; this table does not claim whole-app language coverage.
enum LiteServiceCopy {
    static func text(_ source: String, locale: Locale = .current) -> String {
        let language = locale.language.languageCode?.identifier == "en" ? "en" : "zh-Hant"
        guard let path = Bundle.main.path(forResource: language, ofType: "lproj"),
              let bundle = Bundle(path: path) else { return source }
        return bundle.localizedString(forKey: source, value: source, table: "LiteService")
    }

    static func format(_ source: String, locale: Locale = .current, _ arguments: String...) -> String {
        String(format: text(source, locale: locale), locale: locale, arguments: arguments)
    }

    struct Piece {
        let key: String
        var arguments: [String] = []
        init(_ key: String, _ arguments: [String] = []) { self.key = key; self.arguments = arguments }
        func render(locale: Locale) -> String {
            let value = text(key, locale: locale)
            return arguments.isEmpty ? value : String(format: value, locale: locale, arguments: arguments)
        }
        var source: String { render(locale: Locale(identifier: "zh-Hant")) }
    }

    /// The raw message remains Chinese for existing state tests and consumers.
    /// Parts are ephemeral: the on-disk journal and network payload are unchanged.
    struct Message {
        let pieces: [Piece]
        var source: String { pieces.map(\.source).joined() }
        func render(locale: Locale) -> String {
            pieces.map { $0.render(locale: locale) }.filter { !$0.isEmpty }
                .joined(separator: locale.language.languageCode?.identifier == "en" ? " " : "")
        }
        func render(matching current: String, locale: Locale) -> String {
            // A later error must replace, rather than be hidden by, a saved summary.
            current == source ? render(locale: locale) : text(current, locale: locale)
        }
    }
}
