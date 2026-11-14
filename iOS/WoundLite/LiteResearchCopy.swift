import Foundation

/// Presentation only: research decisions, receipt versions and quota ownership
/// remain independent of the language used to read them.
struct LiteResearchCopy {
    let locale: Locale

    func text(_ source: String) -> String {
        let language = locale.language.languageCode?.identifier == "en" ? "en" : "zh-Hant"
        guard let path = Bundle.main.path(forResource: language, ofType: "lproj"),
              let bundle = Bundle(path: path) else { return source }
        let translated = bundle.localizedString(forKey: source, value: source, table: "LiteResearch")
        return translated == source ? LiteServiceCopy.text(source, locale: locale) : translated
    }

    func format(_ source: String, _ arguments: String...) -> String {
        String(format: text(source), locale: locale, arguments: arguments)
    }

    func date(_ value: Date, includesDate: Bool = true) -> String {
        value.formatted(Date.FormatStyle(date: includesDate ? .abbreviated : .omitted,
                                        time: .shortened).locale(locale))
    }
}
