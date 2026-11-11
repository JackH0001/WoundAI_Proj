import Foundation

/// Read-only operation guidance. IDs, user questions and stored records are never translated.
struct LiteGuideCopy {
    let locale: Locale
    var english: Bool { locale.language.languageCode?.identifier == "en" }
    var modelLocale: Locale { Locale(identifier: english ? "en" : "zh-Hant") }

    func text(_ source: String) -> String {
        guard let path = Bundle.main.path(forResource: english ? "en" : "zh-Hant", ofType: "lproj"),
              let bundle = Bundle(path: path) else { return source }
        return bundle.localizedString(forKey: source, value: source, table: "LiteGuide")
    }

    func format(_ source: String, _ arguments: String...) -> String {
        String(format: text(source), locale: locale, arguments: arguments)
    }
}
