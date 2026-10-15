import Foundation

/// A release is bound to the dedicated service, never a saved medical endpoint.
enum LiteBackendConfiguration {
    static let dedicatedOrigin = "https://woundai-lite-candidate-421209514056.asia-east1.run.app"
    static let unavailableOrigin = "https://woundlite-unconfigured.invalid"

    static func releaseOrigin(_ bundledValue: String?) -> String {
        guard let value = bundledValue, canonicalOrigin(value) == dedicatedOrigin else {
            return unavailableOrigin
        }
        return dedicatedOrigin
    }

    static func canonicalOrigin(_ raw: String) -> String? {
        let text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        #if DEBUG && targetEnvironment(simulator)
        if text == "http://localhost:5000" || text == "http://localhost:5000/" {
            return "http://localhost:5000"
        }
        #endif
        guard let url = URL(string: text), let host = url.host,
              !host.hasSuffix(".invalid"), url.user == nil, url.password == nil,
              url.query == nil, url.fragment == nil, url.port == nil,
              !text.contains("%"), url.path.isEmpty || url.path == "/",
              url.scheme == "https" else { return nil }
        return "https://" + host
    }

    static func sameService(_ original: String, _ current: String) -> Bool {
        guard let first = canonicalOrigin(original), let second = canonicalOrigin(current) else { return false }
        return first == second
    }

    static func consentChoice(_ choice: Bool?, savedVersion: String?, requiredVersion: String,
                              savedOrigin: String?, currentOrigin: String) -> Bool? {
        guard savedVersion == requiredVersion, let savedOrigin,
              sameService(savedOrigin, currentOrigin) else { return nil }
        return choice
    }
}
