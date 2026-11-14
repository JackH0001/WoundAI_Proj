import XCTest
import UserNotifications
@testable import WoundLite

@MainActor private final class ReminderMemory: LiteReminderJournal {
    var value: LiteReminderSetting?, failLoad = false, failSave = false
    var writes = 0
    func load() throws -> LiteReminderSetting? {
        if failLoad { throw LiteReminderFailure.invalid }; return value
    }
    func save(_ setting: LiteReminderSetting) throws {
        if failSave { throw LiteReminderFailure.invalid }
        try setting.validate(); writes += 1; value = setting
    }
}
@MainActor private final class ReminderCenter: LiteReminderCenter {
    var status: UNAuthorizationStatus = .authorized
    var permissionResult: UNAuthorizationStatus = .authorized
    var requests = [UNNotificationRequest](), prompts = 0, adds = 0
    var failAdd = false, loseReply = false, ignoreRemoval = false, revokeOnAdd = false
    var hold: CheckedContinuation<Void, Never>?
    var waitForAdd = false
    func authorization() async -> UNAuthorizationStatus { status }
    func requestPermission() async throws { prompts += 1; status = permissionResult }
    func pending() async -> [UNNotificationRequest] { requests }
    func remove() { if !ignoreRemoval { requests.removeAll { $0.identifier == LiteReminderModel.identifier } } }
    func add(_ request: UNNotificationRequest) async throws {
        adds += 1
        if waitForAdd { await withCheckedContinuation { hold = $0 } }
        if failAdd { throw LiteReminderFailure.invalid }
        requests.removeAll { $0.identifier == request.identifier }; requests.append(request)
        if revokeOnAdd { status = .denied }
        if loseReply { throw LiteReminderFailure.invalid }
    }
}

@MainActor final class LiteLocalReminderTests: XCTestCase {
    private func fixture() -> (LiteReminderModel, ReminderCenter, ReminderMemory) {
        let c = ReminderCenter(), j = ReminderMemory(); return (LiteReminderModel(center: c, journal: j), c, j)
    }
    func testDefaultAndReadbackNeverPromptOrSchedule() async {
        let (m,c,j) = fixture(); c.status = .notDetermined
        await m.refresh(); await m.refresh()
        XCTAssertEqual(m.state,.off); XCTAssertFalse(m.setting.enabled)
        XCTAssertEqual(c.prompts,0); XCTAssertEqual(c.adds,0); XCTAssertEqual(j.writes,0)
    }
    func testPermissionIsRequestedOnlyOnExplicitSaveAndDenialDoesNotPersist() async {
        let (m,c,j) = fixture(); c.status = .notDetermined; c.permissionResult = .denied
        await m.save(hour:14,minute:30,language:"en"); await m.save(hour:14,minute:30,language:"en")
        XCTAssertEqual(m.state,.denied); XCTAssertEqual(c.prompts,1); XCTAssertEqual(c.adds,0); XCTAssertNil(j.value)
    }
    func testAllowSaveChangeTimeAndReloadUseOneIdentifier() async throws {
        let (m,c,j) = fixture(); c.status = .notDetermined
        await m.save(hour:14,minute:30,language:"en")
        XCTAssertEqual(m.state,.scheduled); XCTAssertEqual(c.prompts,1)
        await m.save(hour:20,minute:15,language:"zh-Hant")
        XCTAssertEqual(c.requests.count,1); XCTAssertEqual(c.adds,2)
        let reopened = LiteReminderModel(center:c,journal:j); await reopened.refresh()
        XCTAssertEqual(reopened.state,.scheduled); XCTAssertEqual(reopened.setting.hour,20)
        XCTAssertEqual(c.adds,2); XCTAssertEqual(c.prompts,1)
    }
    func testLostAcknowledgementReadsBackInsteadOfDuplicating() async {
        let (m,c,_) = fixture(); c.loseReply = true
        await m.save(hour:12,minute:5,language:"en")
        XCTAssertEqual(m.state,.scheduled); XCTAssertEqual(c.adds,1); XCTAssertEqual(c.requests.count,1)
        await m.refresh(); XCTAssertEqual(c.adds,1)
    }
    func testFailedAddStaysUnconfirmedAcrossRestartAndNeverAutoReschedules() async {
        let (m,c,j) = fixture(); c.failAdd = true
        await m.save(hour:12,minute:5,language:"en")
        XCTAssertEqual(m.state,.unconfirmed); XCTAssertEqual(j.value?.enabled,true)
        let reopened = LiteReminderModel(center:c,journal:j); await reopened.refresh()
        XCTAssertEqual(reopened.state,.unconfirmed); XCTAssertEqual(c.adds,1)
        c.failAdd = false; await reopened.save(hour:12,minute:5,language:"en")
        XCTAssertEqual(reopened.state,.scheduled); XCTAssertEqual(c.requests.count,1)
    }
    func testWriteFailureDoesNotScheduleOrOverwriteOldReminder() async throws {
        let (m,c,j) = fixture(); await m.save(hour:12,minute:5,language:"en")
        j.failSave = true; await m.save(hour:13,minute:6,language:"en")
        XCTAssertEqual(m.state,.storageUnavailable); XCTAssertEqual(c.adds,1)
        XCTAssertTrue(LiteReminderModel.matches(try XCTUnwrap(c.requests.first),try XCTUnwrap(j.value)))
    }
    func testDisablePreservesOtherNotificationsAndStaysOffAfterRestart() async throws {
        let (m,c,j) = fixture(); await m.save(hour:12,minute:5,language:"en")
        c.requests.append(UNNotificationRequest(identifier:"unrelated",content:UNMutableNotificationContent(),trigger:nil))
        await m.disable(); XCTAssertEqual(m.state,.off); XCTAssertFalse(m.setting.enabled)
        XCTAssertEqual(c.requests.map(\.identifier),["unrelated"])
        let reopened = LiteReminderModel(center:c,journal:j); await reopened.refresh()
        XCTAssertEqual(reopened.state,.off); XCTAssertEqual(c.adds,1)
    }
    func testDisableStillCancelsWhenPersistenceFailsAndDoesNotClaimSuccess() async {
        let (m,c,j) = fixture(); await m.save(hour:12,minute:5,language:"en")
        j.failSave = true; await m.disable()
        XCTAssertEqual(m.state,.storageUnavailable); XCTAssertTrue(c.requests.isEmpty)
        j.failSave = false; await m.refresh()
        XCTAssertEqual(m.state,.unconfirmed); XCTAssertEqual(c.adds,1)
    }
    func testPermissionRevocationCancelsAndRequiresExplicitSaveAfterRestoration() async {
        let (m,c,_) = fixture(); await m.save(hour:12,minute:5,language:"en")
        c.status = .denied; await m.refresh()
        XCTAssertEqual(m.state,.denied); XCTAssertTrue(c.requests.isEmpty)
        c.status = .authorized; await m.refresh()
        XCTAssertEqual(m.state,.unconfirmed); XCTAssertEqual(c.adds,1)
    }
    func testRevocationDuringAddDoesNotClaimScheduled() async {
        let (m,c,_) = fixture(); c.revokeOnAdd = true
        await m.save(hour:12,minute:5,language:"en")
        XCTAssertEqual(m.state,.denied); XCTAssertTrue(c.requests.isEmpty)
    }
    func testCorruptJournalDoesNotOverwriteItOrRequestPermission() async {
        let (m,c,j) = fixture(); j.failLoad = true; c.status = .notDetermined
        await m.refresh(); XCTAssertEqual(m.state,.storageUnavailable)
        XCTAssertEqual(j.writes,0); XCTAssertEqual(c.prompts,0); XCTAssertEqual(c.adds,0)
    }
    func testUnconfirmedRemovalIsNotReportedOff() async {
        let (m,c,_) = fixture(); await m.save(hour:12,minute:5,language:"en")
        c.ignoreRemoval = true; await m.disable(); XCTAssertEqual(m.state,.unconfirmed)
    }
    func testConcurrentSaveCannotCreateSecondScheduleOrChangeJournalMidFlight() async {
        let (m,c,j) = fixture(); c.waitForAdd = true
        let first = Task { await m.save(hour:12,minute:5,language:"en") }
        while c.hold == nil { await Task.yield() }
        await m.save(hour:23,minute:59,language:"en")
        await m.refresh(); XCTAssertEqual(j.writes,1); XCTAssertEqual(j.value?.hour,12)
        c.hold?.resume(); await first.value
        XCTAssertEqual(c.adds,1); XCTAssertEqual(m.state,.scheduled)
    }
    func testInvalidSettingsDoNotAskPermissionOrSchedule() async {
        let (m,c,j) = fixture(); c.status = .notDetermined
        for (h,n,l) in [(-1,0,"en"),(24,0,"en"),(1,60,"en"),(1,0,"invalid")] {
            await m.save(hour:h,minute:n,language:l)
        }
        XCTAssertEqual(c.adds,0); XCTAssertEqual(c.prompts,0); XCTAssertEqual(j.writes,0)
    }
    func testNotificationContainsNoRecordDataAndUsesFloatingLocalDailyTime() throws {
        for language in ["en","zh-Hant"] {
            let s = LiteReminderSetting(version:1,enabled:true,hour:18,minute:17,language:language)
            let r = try LiteReminderModel.request(s)
            let trigger = try XCTUnwrap(r.trigger as? UNCalendarNotificationTrigger)
            XCTAssertTrue(trigger.repeats); XCTAssertEqual(trigger.dateComponents.hour,18)
            XCTAssertEqual(trigger.dateComponents.minute,17); XCTAssertNil(trigger.dateComponents.timeZone)
            XCTAssertNil(trigger.dateComponents.day); XCTAssertNil(trigger.dateComponents.weekday)
            XCTAssertEqual(r.content.title,"WoundAILite"); XCTAssertNil(r.content.badge)
            XCTAssertTrue(r.content.attachments.isEmpty); XCTAssertTrue(r.content.userInfo.isEmpty)
            XCTAssertFalse(r.content.body.contains("傷口")); XCTAssertFalse(r.content.body.contains("wound"))
            XCTAssertEqual(r.content.body.contains("record reminder"),language == "en")
        }
    }
    func testFileJournalKeepsSeparateDataAndRejectsMalformedBytes() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let other = dir.appendingPathComponent("record.data"); try HealthDataFiles.write(Data([1,2,3]),to:other)
        let file = dir.appendingPathComponent("reminder/settings.json"), j = LiteReminderFileJournal(file:file)
        XCTAssertNil(try j.load()); let s = LiteReminderSetting(version:1,enabled:true,hour:0,minute:0,language:"en")
        try j.save(s); XCTAssertEqual(try j.load(),s)
        XCTAssertEqual(try Data(contentsOf:other),Data([1,2,3]))
        XCTAssertTrue(try file.resourceValues(forKeys:[.isExcludedFromBackupKey]).isExcludedFromBackup == true)
        try Data("{}".utf8).write(to:file); XCTAssertThrowsError(try j.load())
    }
    func testPackagedReminderTranslationsAndGuidePathAgree() throws {
        func table(_ language: String) throws -> [String:String] {
            let url = try XCTUnwrap(Bundle.main.url(forResource: "LiteReminder", withExtension: "strings", subdirectory: nil, localization: language))
            return try XCTUnwrap(PropertyListSerialization.propertyList(from: Data(contentsOf: url), format: nil) as? [String:String])
        }
        let en = try table("en"), zh = try table("zh-Hant")
        XCTAssertEqual(en.count, 19); XCTAssertEqual(Set(en.keys), Set(zh.keys))
        for (key,value) in en {
            XCTAssertEqual(zh[key],key); XCTAssertFalse(value.isEmpty)
            XCTAssertFalse(value.unicodeScalars.contains { (0x4E00...0x9FFF).contains($0.value) })
        }
        for language in ["en", "zh-Hant"] {
            let locale = Locale(identifier: language)
            let topic = try XCTUnwrap(LiteHelpTopic.topics(personal: false, locale: locale).first { $0.id == "reminder" })
            XCTAssertTrue(topic.path.contains(LiteReminderCopy(locale: locale).text("本機紀錄提醒")))
        }
    }
    func testDeniedReasonSurvivesForegroundReadbackUntilExplicitDisable() async {
        let (m,c,j) = fixture(); c.status = .notDetermined; c.permissionResult = .denied
        await m.save(hour:10,minute:30,language:"en")
        XCTAssertEqual(m.state,.denied)
        await m.refresh(); XCTAssertEqual(m.state,.denied)
        let reopened = LiteReminderModel(center:c,journal:j)
        await reopened.refresh(); XCTAssertEqual(reopened.state,.denied)
        XCTAssertEqual(c.prompts,1); XCTAssertEqual(j.writes,0)
        await reopened.disable(); await reopened.refresh()
        XCTAssertEqual(reopened.state,.off); XCTAssertFalse(try! j.load()!.enabled)
    }
}
