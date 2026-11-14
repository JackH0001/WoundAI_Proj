import Foundation
import Combine

/// Bundled, editorially reviewed first-party sponsorship only. No network
/// creative loader, SDK, identifier, health input or impression/click telemetry.
struct LiteSponsorCampaign: Codable, Equatable {
    struct Copy: Codable, Equatable { let title, body: String }
    let version: Int
    let id, advertiser: String
    let startsAt, endsAt: Int
    let markets: [String]
    let landing: String
    let copy: [String: Copy]

    static func decode(_ data: Data) throws -> Self {
        guard data.count <= 8192,
              let object = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              Set(object.keys) == Set(["version", "id", "advertiser", "startsAt", "endsAt", "markets", "landing", "copy"])
        else { throw LiteSponsorError.invalid }
        guard let copies = object["copy"] as? [String: [String: Any]],
              copies.values.allSatisfy({ Set($0.keys) == Set(["title", "body"]) }) else { throw LiteSponsorError.invalid }
        let value = try JSONDecoder().decode(Self.self, from: data)
        try value.validate(); return value
    }
    func validate() throws {
        func plain(_ value: String, max: Int) -> Bool {
            !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && value.count <= max &&
            !value.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }) &&
            !value.contains("<") && !value.contains(">")
        }
        guard version == 1, LiteAttestClient.matches(id, "[a-z0-9-]{1,64}"), plain(advertiser, max: 80),
              startsAt >= 0, endsAt > startsAt, endsAt - startsAt <= 366 * 86400,
              !markets.isEmpty, markets.count <= 20, Set(markets).count == markets.count,
              markets.allSatisfy({ LiteAttestClient.matches($0, "[A-Z]{3}") }),
              Set(copy.keys) == Set(["en", "zh-Hant"]),
              copy.values.allSatisfy({ plain($0.title, max: 80) && plain($0.body, max: 280) }),
              let url = URL(string: landing), url.scheme == "https", let host = url.host,
              LiteAttestClient.matches(host, "[a-z0-9]+(?:[.-][a-z0-9]+)*\\.[a-z]{2,}"),
              url.user == nil, url.password == nil, url.port == nil, url.query == nil, url.fragment == nil,
              !landing.contains("%"), !landing.contains("\\"), url.absoluteString == landing
        else { throw LiteSponsorError.invalid }
    }
    static func bundled() -> Self? {
        // No campaign file is shipped until sponsor/content/privacy review and
        // a separately authorized release. Absence means no ad and no lookup.
        guard let url = Bundle.main.url(forResource: "LiteSponsorCampaign", withExtension: "json"),
              let data = try? Data(contentsOf: url) else { return nil }
        return try? decode(data)
    }
}
enum LiteSponsorError: Error { case invalid, unavailable }

struct LiteSponsorCopy {
    let locale: Locale
    var language: String { locale.language.languageCode?.identifier == "en" ? "en" : "zh-Hant" }
    func text(_ value: String) -> String {
        guard let path = Bundle.main.path(forResource: language, ofType: "lproj"), let bundle = Bundle(path: path) else { return value }
        return bundle.localizedString(forKey: value, value: value, table: "LiteSponsor")
    }
}

/// Only a short-lived plan decision reaches the ad presentation model. The
/// caller checks the service account generation; no identifiers enter this type.
struct LiteSponsorEligibility {
    let free: Bool
    let market: String
    let checkedAt: Date
    func permits(_ campaign: LiteSponsorCampaign, at now: Date) -> Bool {
        free && now >= checkedAt && now.timeIntervalSince(checkedAt) < 60 &&
        now.timeIntervalSince1970 >= Double(campaign.startsAt) && now.timeIntervalSince1970 < Double(campaign.endsAt) &&
        campaign.markets.contains(market)
    }
}

@MainActor protocol LiteSponsorDismissal {
    func load() throws -> String?
    func save(_ id: String) throws
}
struct LiteSponsorFileDismissal: LiteSponsorDismissal {
    let file: URL
    static let standard = Self(file: FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        .appendingPathComponent("lite-sponsor-dismissal/current.json"))
    func load() throws -> String? {
        let data: Data
        do { data = try Data(contentsOf: file) }
        catch let e as NSError where e.domain == NSCocoaErrorDomain && e.code == NSFileReadNoSuchFileError { return nil }
        guard data.count <= 256 else { throw LiteSponsorError.invalid }
        let value = try JSONDecoder().decode(String.self, from: data)
        guard LiteAttestClient.matches(value, "[a-z0-9-]{1,64}") else { throw LiteSponsorError.invalid }
        return value
    }
    func save(_ id: String) throws {
        guard LiteAttestClient.matches(id, "[a-z0-9-]{1,64}") else { throw LiteSponsorError.invalid }
        try HealthDataFiles.write(JSONEncoder().encode(id), to: file)
        guard try load() == id else { throw LiteSponsorError.invalid }
    }
}

@MainActor final class LiteSponsorModel: ObservableObject {
    @Published private(set) var eligibility: LiteSponsorEligibility?
    @Published private(set) var dismissed = false
    @Published private(set) var busy = false
    let campaign: LiteSponsorCampaign?
    private let resolve: () async throws -> LiteSponsorEligibility
    private let journal: any LiteSponsorDismissal
    private var generation = 0
    init(campaign: LiteSponsorCampaign?, journal: any LiteSponsorDismissal,
         resolve: @escaping () async throws -> LiteSponsorEligibility) {
        if let campaign, (try? campaign.validate()) != nil { self.campaign = campaign }
        else { self.campaign = nil }
        self.journal = journal; self.resolve = resolve
    }
    func visible(at now: Date) -> Bool {
        guard !dismissed, !busy, let campaign else { return false }
        return eligibility?.permits(campaign, at: now) == true
    }
    /// Invalidations immediately hide the card and make late callbacks harmless.
    func invalidate() { generation += 1; eligibility = nil }
    func refresh() async {
        guard !busy, let campaign else { return }
        invalidate(); let attempt = generation; busy = true; defer { busy = false }
        do {
            dismissed = try journal.load() == campaign.id
            guard !dismissed else { return }
            let reply = try await resolve()
            guard attempt == generation else { return }
            eligibility = reply
        } catch { eligibility = nil }
    }
    func dismiss() {
        invalidate(); dismissed = true
        if let campaign { try? journal.save(campaign.id) }
        // A failed journal write still hides this instance; no other data changes.
    }
    func destination(at date: Date) -> URL? {
        guard visible(at: date), let campaign else { return nil }
        return URL(string: campaign.landing)
    }
    var reportText: String? {
        guard let campaign else { return nil }
        return "WoundAILite advertisement report\nCampaign: \(campaign.id)\nAdvertiser: \(campaign.advertiser)\nContact: jack.hou@gmail.com"
    }
}
