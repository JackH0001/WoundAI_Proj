import XCTest
import UIKit
@testable import WoundLite

@MainActor final class LiteHistoryCopyTests: XCTestCase {
    private let en = LiteHistoryCopy(locale: Locale(identifier: "en_US"))
    private let zh = LiteHistoryCopy(locale: Locale(identifier: "zh_Hant_TW"))

    func testPackagedLanguagesKeepPlaceholdersAndOriginalChinese() throws {
        func table(_ language: String) throws -> [String: String] {
            let url = try XCTUnwrap(Bundle.main.url(forResource: "LiteHistory", withExtension: "strings", subdirectory: nil, localization: language))
            return try XCTUnwrap(PropertyListSerialization.propertyList(from: Data(contentsOf: url), format: nil) as? [String: String])
        }
        let english = try table("en"), chinese = try table("zh-Hant")
        XCTAssertEqual(Set(english.keys), Set(chinese.keys))
        XCTAssertGreaterThan(english.count, 160)
        for (key, value) in english {
            XCTAssertEqual(chinese[key], key)
            XCTAssertFalse(value.isEmpty)
            XCTAssertFalse(value.unicodeScalars.contains { (0x4E00...0x9FFF).contains($0.value) }, key)
            XCTAssertEqual(key.components(separatedBy: "%@").count, value.components(separatedBy: "%@").count, key)
        }
    }

    func testNamesIdentifiersAndLocationCodesStayVerbatim() throws {
        let group = LiteWoundGroup(id: "AAAAAAAA-0000-4000-8000-000000ABC123", name: "紀錄 · 右 %d",
            location: LiteWoundLocation(side: "right", site: "knee_back"), account: String(repeating: "a", count: 64))
        let before = group
        XCTAssertEqual(en.group(group), "紀錄 · 右 %d, Right, Back of knee")
        XCTAssertEqual(zh.group(group), group.displayName)
        XCTAssertEqual(en.trackingLabel(group), "紀錄 · 右 %d, Right, Back of knee (ABC123)")
        XCTAssertEqual(zh.trackingLabel(group), group.trackingLabel)
        XCTAssertEqual(group, before)
        XCTAssertEqual(try LiteWoundRoster.wireID(group.id), "aaaaaaaa000040008000000000abc123")
        for (code, title) in LiteWoundLocation.sides + LiteWoundLocation.sites {
            XCTAssertNotEqual(en.text(title), title)
            XCTAssertEqual(zh.text(title), title)
            XCTAssertFalse(code.isEmpty)
        }
    }

    func testDowngradedRosterKeepsExtraHistoriesWithoutGrantingExtraSlots() throws {
        let account = String(repeating: "a", count: 64)
        let active = (1...5).map { String(format: "%032x", $0) }
        let roster = LiteWoundRoster(schema: "lite.wounds/1", account_sha256: account, revision: 7, active: active, limit: 1, decision: nil)
        try roster.validate(account: account)
        XCTAssertEqual(en.roster(roster), "Wounds available for further entries: 1. Plan limit: 1. Another 4 remain on the list. Restore Plus or end tracking for earlier wounds to add entries again.")
        XCTAssertEqual(zh.roster(roster), roster.summary)
        XCTAssertEqual(roster.active, active)
        XCTAssertEqual(roster.revision, 7)
        XCTAssertEqual(roster.selectableGroups(in: []).count, 1)
        XCTAssertEqual(roster.remoteGroups(excluding: []).count, 5)
    }

    func testRevisionMessagesRequireWholeKnownMessageAndKeepNumber() {
        XCTAssertEqual(en.message("雲端已確認第 5 版"), "Cloud confirmed revision 5")
        XCTAssertEqual(en.message("第 6 版尚未獲雲端確認"), "Revision 6 is not yet confirmed by the cloud")
        XCTAssertEqual(en.message("正在同步第 6 版…"), "Syncing revision 6…")
        for unknown in ["雲端已確認第 5 版\n其他說明", "前綴雲端已確認第 5 版", "雲端已確認第 %@ 版；未知", "自訂名稱：紀錄"] {
            XCTAssertEqual(en.message(unknown), unknown)
        }
        XCTAssertTrue(en.message(LiteWoundTrackingError.full(5).localizedDescription).contains("5 wounds"))
    }

    func testAreaChangeRetainsPrecisionAndDoesNotCompareDifferentSources() {
        let changed = LiteContourChangeSummary(before: 87.86, after: 86.39)
        XCTAssertTrue(en.change(changed).contains("87.86 → 86.39"))
        XCTAssertEqual(zh.change(changed), changed.text)
        let rounded = LiteContourChangeSummary(before: 5.111, after: 5.112)
        XCTAssertTrue(en.change(rounded).contains("remains 5.11"))
        let incomparable = LiteContourChangeSummary(before: 87.86, after: 86.39, comparable: false)
        XCTAssertFalse(en.change(incomparable).contains("87.86"))
        XCTAssertFalse(en.change(incomparable).contains("→"))
        XCTAssertTrue(en.change(incomparable).contains("source differs"))
    }

    private func row() -> LiteRecord {
        LiteRecord(id: "synthetic-history", dateISO: "2026-11-01T08:22:44.100Z", surfaceCm2: 87.86,
            projectedCm2: 86.39, quality: "ok", imageName: "original-image.enc", source: "manual",
            polysJson: "[[[10,10],[90,10],[90,90],[10,90]]]", depthName: "original-depth.enc",
            woundID: "AAAAAAAA-0000-4000-8000-000000000001", woundName: "原始紀錄", woundLocation: .init(side: "right", site: "knee_back"),
            cloudSync: .init(binding: .init(server: "https://research.invalid", anonID: String(repeating: "a", count: 32), imageID: "original-image", imageW: 100, imageH: 100)),
            manuallyConfirmed: true)
    }

    func testAcknowledgedRecordTranslationCreatesNoRevisionOrUpload() async throws {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: folder) }
        let file = folder.appendingPathComponent("records.json")
        var record = row()
        let payload = try XCTUnwrap(record.revisionJSON())
        record.cloudSync?.acknowledgedRevision = 5
        record.cloudSync?.acknowledgedDigest = LitePendingRevision.digest(payload)
        let raw = try JSONEncoder().encode([record]); try raw.write(to: file)
        var writes = 0, sends = 0
        let store = LiteStore(fileURL: file, writeIndex: { _, _ in writes += 1 })
        for copy in [en, zh, en] {
            let current = store.records[0]
            let state = LiteRevisionPresentation.make(record: current, syncing: false, consent: true, withdrawalBlocked: false)
            XCTAssertEqual(state.phase, .synced); XCTAssertNil(state.retryTitle)
            XCTAssertTrue(copy.message(state.title).contains("5"))
            XCTAssertFalse(copy.revisionDetail(state).isEmpty)
            XCTAssertFalse(copy.group(store.wounds[0]).isEmpty)
            XCTAssertFalse(copy.date(current.dateISO).isEmpty)
            XCTAssertEqual(try current.revisionJSON(), payload)
            await store.syncRevision(recordID: current.id, consent: { true }) { _, _ in sends += 1 }
        }
        XCTAssertEqual(writes, 0); XCTAssertEqual(sends, 0)
        XCTAssertEqual(try Data(contentsOf: file), raw)
        XCTAssertEqual(store.records[0].cloudSync?.acknowledgedRevision, 5)
        XCTAssertNil(store.records[0].cloudSync?.pending)
    }

    func testWaitRenderingPreservesPendingPayloadAndDeadlineThenSendsOnce() async throws {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: folder) }
        let file = folder.appendingPathComponent("records.json")
        var now = Date(timeIntervalSince1970: 1_790_000_000), record = row()
        let pending = LitePendingRevision(revision: 6, payloadJSON: try XCTUnwrap(record.revisionJSON()))
        let deadline = now.addingTimeInterval(120)
        record.cloudSync?.acknowledgedRevision = 5; record.cloudSync?.pending = pending
        record.cloudSync?.capacityNotBefore = deadline; record.cloudSync?.automaticRetryAt = deadline
        let raw = try JSONEncoder().encode([record]); try raw.write(to: file)
        let store = LiteStore(fileURL: file, now: { now })
        var sends = 0
        for copy in [en, zh, en] {
            let state = LiteRevisionPresentation.make(record: store.records[0], syncing: false, consent: true, withdrawalBlocked: false, now: now)
            XCTAssertEqual(state.phase, .waiting); XCTAssertNil(state.retryTitle)
            XCTAssertTrue(copy.message(state.title).contains("6"))
            XCTAssertFalse(copy.revisionDetail(state).isEmpty)
            await store.syncRevision(recordID: record.id, consent: { true }) { _, _ in sends += 1 }
            XCTAssertEqual(try Data(contentsOf: file), raw)
        }
        XCTAssertEqual(sends, 0)
        XCTAssertEqual(store.records[0].cloudSync?.capacityNotBefore, deadline)
        now = deadline
        for _ in 0..<3 {
            await store.syncRevision(recordID: record.id, consent: { true }) { binding, revision in
                sends += 1; XCTAssertEqual(revision, pending); XCTAssertEqual(binding, record.cloudSync?.binding)
            }
        }
        XCTAssertEqual(sends, 1)
        XCTAssertEqual(store.records[0].cloudSync?.acknowledgedRevision, 6)
        XCTAssertEqual(store.records[0].imageName, record.imageName)
        XCTAssertEqual(store.records[0].depthName, record.depthName)
    }

    func testConsentOffRetiredAndUnknownErrorNeverBecomeSynced() {
        let record = row()
        for flags in [(false, false, false), (true, true, false), (true, false, true)] {
            let state = LiteRevisionPresentation.make(record: record, syncing: false, consent: flags.0, withdrawalBlocked: flags.1, retiredResearch: flags.2)
            XCTAssertEqual(state.phase, .localOnly); XCTAssertNil(state.retryTitle)
            XCTAssertNotEqual(en.message(state.title), state.title)
            XCTAssertNotEqual(en.revisionDetail(state), state.detail)
            XCTAssertFalse(en.message(state.title).contains("confirmed revision"))
        }
        var failed = record; failed.cloudSync?.lastError = "未知錯誤：keep these bytes"
        let state = LiteRevisionPresentation.make(record: failed, syncing: false, consent: true, withdrawalBlocked: false)
        XCTAssertEqual(state.phase, .failed)
        XCTAssertEqual(en.revisionDetail(state), "未知錯誤：keep these bytes")
        XCTAssertEqual(en.message(try! XCTUnwrap(state.retryTitle)), "Retry syncing saved changes")
    }

    func testUnavailableAreaDepthAndContourWarningsRemainExplicit() {
        XCTAssertEqual(en.text("未提供面積"), "Area unavailable")
        XCTAssertEqual(en.text(LiteAreaSeries.current.rawValue), "Current LiDAR surface area")
        XCTAssertEqual(en.text(LiteAreaSeries.legacy.rawValue), "Legacy LiDAR surface area")
        let limit = en.text("目前深度與體積尚未通過實物精度驗證，不作傷口量測或癒合判定。")
        XCTAssertTrue(limit.contains("have not passed physical accuracy validation"))
        XCTAssertTrue(en.text("本次沒有可提供的深度／體積參考值，不以零代替。").contains("not shown as zero"))
        XCTAssertTrue(en.message("修正圈選後品質不足，未更新紀錄。距離偏遠（約 45 公分），建議 25–40 公分。").contains("45"))
        XCTAssertEqual(en.date("not a captured date"), "not a captured date")
    }

    func testLitePreviewLabelsCanChangeWithoutChangingSharedMedicalDefaults() {
        let preview = WoundImagePreview(image: UIImage(), polygons: [], imageW: 1, imageH: 1)
        XCTAssertEqual(preview.gestureHint, "雙指縮放與移動，檢查圈選邊界。")
        XCTAssertEqual(preview.resetTitle, "重設檢視")
        XCTAssertEqual(en.text(preview.gestureHint), "Pinch to zoom and move to check the outline.")
        XCTAssertEqual(en.text(preview.resetTitle), "Reset view")
    }
}
