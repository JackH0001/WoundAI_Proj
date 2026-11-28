import Foundation
import Combine
import UserNotifications

/// One device-local, explicitly chosen reminder. No record/account identifier,
/// research consent, cloud transport, badge or medical schedule is involved.
struct LiteReminderSetting: Codable, Equatable {
    let version: Int
    var enabled: Bool
    let hour: Int, minute: Int, language: String
    static let initial = Self(version: 1, enabled: false, hour: 9, minute: 0, language: "zh-Hant")
    func validate() throws {
        guard version == 1, (0...23).contains(hour), (0...59).contains(minute),
              ["zh-Hant", "en"].contains(language) else { throw LiteReminderFailure.invalid }
    }
}
enum LiteReminderFailure: Error { case invalid }

struct LiteReminderCopy {
    let locale: Locale
    var language: String { locale.language.languageCode?.identifier == "en" ? "en" : "zh-Hant" }
    func text(_ value: String) -> String {
        guard let path = Bundle.main.path(forResource: language, ofType: "lproj"),
              let bundle = Bundle(path: path) else { return value }
        return bundle.localizedString(forKey: value, value: value, table: "LiteReminder")
    }
}

@MainActor protocol LiteReminderJournal {
    func load() throws -> LiteReminderSetting?
    func save(_ setting: LiteReminderSetting) throws
}
struct LiteReminderFileJournal: LiteReminderJournal {
    let file: URL
    static let standard = Self(file: FileManager.default.urls(for: .applicationSupportDirectory,
        in: .userDomainMask)[0].appendingPathComponent("lite-local-reminder/settings.json"))
    func load() throws -> LiteReminderSetting? {
        let data: Data
        do { data = try Data(contentsOf: file) }
        catch let error as NSError where error.domain == NSCocoaErrorDomain && error.code == NSFileReadNoSuchFileError { return nil }
        guard data.count <= 2048 else { throw LiteReminderFailure.invalid }
        let value = try JSONDecoder().decode(LiteReminderSetting.self, from: data)
        try value.validate(); return value
    }
    func save(_ setting: LiteReminderSetting) throws {
        try setting.validate()
        try HealthDataFiles.write(JSONEncoder().encode(setting), to: file)
        guard try load() == setting else { throw LiteReminderFailure.invalid }
    }
}

@MainActor protocol LiteReminderCenter {
    func authorization() async -> UNAuthorizationStatus
    func requestPermission() async throws
    func add(_ request: UNNotificationRequest) async throws
    func pending() async -> [UNNotificationRequest]
    func remove()
}
struct LiteSystemReminderCenter: LiteReminderCenter {
    let center: UNUserNotificationCenter
    func authorization() async -> UNAuthorizationStatus { await center.notificationSettings().authorizationStatus }
    func requestPermission() async throws { _ = try await center.requestAuthorization(options: [.alert, .sound]) }
    func add(_ request: UNNotificationRequest) async throws { try await center.add(request) }
    func pending() async -> [UNNotificationRequest] { await center.pendingNotificationRequests() }
    func remove() {
        center.removePendingNotificationRequests(withIdentifiers: [LiteReminderModel.identifier])
        center.removeDeliveredNotifications(withIdentifiers: [LiteReminderModel.identifier])
    }
}

@MainActor final class LiteReminderModel: ObservableObject {
    static let identifier = "com.woundai.lite.local-record-reminder.v1"
    static let shared = LiteReminderModel(center: LiteSystemReminderCenter(center: .current()), journal: LiteReminderFileJournal.standard)
    enum State { case off, scheduled, denied, unconfirmed, storageUnavailable }
    @Published private(set) var setting = LiteReminderSetting.initial
    @Published private(set) var state: State = .off
    @Published private(set) var busy = false
    private let center: any LiteReminderCenter
    private let journal: any LiteReminderJournal
    init(center: any LiteReminderCenter, journal: any LiteReminderJournal) { self.center = center; self.journal = journal }

    static func request(_ setting: LiteReminderSetting) throws -> UNNotificationRequest {
        try setting.validate()
        guard setting.enabled else { throw LiteReminderFailure.invalid }
        let content = UNMutableNotificationContent()
        content.title = "WoundAILite"
        content.body = LiteReminderCopy(locale: Locale(identifier: setting.language)).text("您設定的紀錄時間到了。有需要時可開啟 App。")
        content.sound = .default
        var date = DateComponents(); date.hour = setting.hour; date.minute = setting.minute
        // Leave timezone/date unset: this repeats at the chosen local wall time.
        return UNNotificationRequest(identifier: identifier, content: content,
            trigger: UNCalendarNotificationTrigger(dateMatching: date, repeats: true))
    }
    static func matches(_ request: UNNotificationRequest, _ setting: LiteReminderSetting) -> Bool {
        guard let expected = try? self.request(setting), request.identifier == identifier,
              let trigger = request.trigger as? UNCalendarNotificationTrigger,
              let wanted = expected.trigger as? UNCalendarNotificationTrigger else { return false }
        return trigger.repeats && trigger.dateComponents == wanted.dateComponents &&
            request.content.title == expected.content.title && request.content.body == expected.content.body &&
            request.content.userInfo.isEmpty && request.content.attachments.isEmpty && request.content.badge == nil
    }
    private func allowed(_ status: UNAuthorizationStatus) -> Bool { [.authorized, .provisional, .ephemeral].contains(status) }
    private func owned() async -> [UNNotificationRequest] { await center.pending().filter { $0.identifier == Self.identifier } }

    /// Refresh never asks permission or recreates a missing schedule. A changed
    /// OS permission, interrupted save or missing journal needs an explicit save.
    func refresh() async {
        guard !busy else { return }; busy = true; defer { busy = false }
        let stored: LiteReminderSetting?
        do { stored = try journal.load(); setting = stored ?? .initial }
        catch { center.remove(); state = .storageUnavailable; return }
        guard setting.enabled else {
            center.remove()
            guard await owned().isEmpty else { state = .unconfirmed; return }
            // The system permission dialog changes scene phase. Preserve its
            // denial across that readback/relaunch when no choice was saved;
            // an explicit, persisted Turn off still displays off.
            if stored == nil, await center.authorization() == .denied { state = .denied }
            else { state = .off }
            return
        }
        let status = await center.authorization()
        guard allowed(status) else { center.remove(); state = status == .denied ? .denied : .unconfirmed; return }
        let saved = await owned()
        state = saved.count == 1 && Self.matches(saved[0], setting) ? .scheduled : .unconfirmed
    }

    /// Journal first, then schedule, then read the exact OS request back. A lost
    /// add acknowledgement is resolved by readback, not a new request identifier.
    func save(hour: Int, minute: Int, language: String) async {
        guard !busy else { return }; busy = true; defer { busy = false }
        let desired = LiteReminderSetting(version: 1, enabled: true, hour: hour, minute: minute, language: language)
        guard (try? desired.validate()) != nil else { state = .unconfirmed; return }
        var status = await center.authorization()
        if status == .notDetermined {
            do { try await center.requestPermission() } catch { state = .unconfirmed; return }
            status = await center.authorization()
        }
        guard allowed(status) else { center.remove(); state = .denied; return }
        do { try journal.save(desired); setting = desired }
        catch { state = .storageUnavailable; return }
        do { try await center.add(Self.request(desired)) } catch { /* Read back uncertain scheduling. */ }
        guard allowed(await center.authorization()) else { center.remove(); state = .denied; return }
        let saved = await owned()
        state = saved.count == 1 && Self.matches(saved[0], desired) ? .scheduled : .unconfirmed
    }
    func disable() async {
        guard !busy else { return }; busy = true; defer { busy = false }
        var desired = setting; desired.enabled = false
        // Cancellation still happens if local storage fails. Never clear other
        // notification identifiers or change photos, research or service state.
        var persisted = false
        do { try journal.save(desired); setting = desired; persisted = true } catch { }
        center.remove()
        state = persisted ? (await owned().isEmpty ? .off : .unconfirmed) : .storageUnavailable
    }
}
