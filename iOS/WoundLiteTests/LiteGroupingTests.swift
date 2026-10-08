import XCTest
@testable import WoundLite

final class LiteGroupingTests: XCTestCase {
    private func record(_ id: String, day: Int, area: Double, wound: String? = nil) -> LiteRecord {
        LiteRecord(id: id, dateISO: String(format: "2026-10-%02dT10:00:00Z", day),
                   surfaceCm2: area, projectedCm2: area, volumeMl: 1.2, maxDepthMm: 2.3,
                   quality: "ok", imageName: "synthetic-unused", source: "manual",
                   woundID: wound, woundName: wound.map { "傷口 \($0)" })
    }

    func testLegacyRecordsStayUngroupedAndRetainExistingMeasurements() throws {
        let old = record("legacy", day: 1, area: 4)
        var json = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(old)) as? [String: Any])
        json.removeValue(forKey: "woundID")
        json.removeValue(forKey: "woundName")
        let loaded = try JSONDecoder().decode(LiteRecord.self, from: JSONSerialization.data(withJSONObject: json))
        XCTAssertNil(loaded.woundID)
        XCTAssertEqual(loaded.volumeMl, 1.2)
        XCTAssertEqual(loaded.maxDepthMm, 2.3)
        XCTAssertTrue(LiteGrouping.groups([loaded]).isEmpty)
        XCTAssertNil(LiteGrouping.changePercent(loaded, in: [old, loaded]))
    }

    func testInterleavedWoundsNeverContributeToEachOthersTrend() {
        let a1 = record("a1", day: 1, area: 10, wound: "a")
        let b = record("b", day: 2, area: 100, wound: "b")
        let a2 = record("a2", day: 3, area: 8, wound: "a")
        let input = [b, a1, a2]
        XCTAssertEqual(LiteGrouping.records(for: "a", in: input).map(\.id), ["a2", "a1"])
        XCTAssertEqual(LiteGrouping.changePercent(a2, in: input), -20)
        XCTAssertNil(LiteGrouping.changePercent(b, in: input))
        XCTAssertEqual(Set(LiteGrouping.groups(input).map(\.id)), ["a", "b"])
        XCTAssertTrue(LiteGrouping.records(for: "", in: input).isEmpty)
    }

    func testZeroBaselineAndSameTimestampDoNotInventAChange() {
        let first = record("first", day: 1, area: 0, wound: "a")
        let last = record("last", day: 2, area: 8, wound: "a")
        XCTAssertNil(LiteGrouping.changePercent(last, in: [first, last]))
        let sameTime = record("same", day: 2, area: 10, wound: "a")
        XCTAssertNil(LiteGrouping.changePercent(last, in: [sameTime, last]))
    }

    @MainActor
    func testExplicitGroupingSurvivesReloadWithoutChangingMeasurements() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let file = dir.appendingPathComponent("records.json")
        let store = LiteStore(fileURL: file)
        let original = record("old", day: 1, area: 4)
        XCTAssertTrue(store.add(original))
        var wound = store.newWound()
        wound.location = LiteWoundLocation(side: "right", site: "heel")
        store.assign(original, to: wound)
        let reloaded = LiteStore(fileURL: file)
        let result = try XCTUnwrap(reloaded.records.first)
        XCTAssertEqual(result.woundID, wound.id)
        XCTAssertEqual(result.woundName, wound.name)
        XCTAssertEqual(result.surfaceCm2, original.surfaceCm2)
        XCTAssertEqual(result.volumeMl, original.volumeMl)
        XCTAssertEqual(reloaded.wounds, [wound])
    }

    @MainActor
    func testFailedWriteDoesNotClaimThatARecordWasSaved() throws {
        // A missing directory is now created with the backup policy. Use a regular
        // file as the parent to make failure deterministic without permission assumptions.
        let blocked = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let original = Data("keep existing file".utf8)
        try original.write(to: blocked)
        defer { try? FileManager.default.removeItem(at: blocked) }
        let store = LiteStore(fileURL: blocked.appendingPathComponent("records.json"))
        XCTAssertFalse(store.add(record("unsaved", day: 1, area: 4, wound: "a")))
        XCTAssertTrue(store.records.isEmpty)
        XCTAssertNotNil(store.storageError)
        XCTAssertEqual(try Data(contentsOf: blocked), original)
    }

    func testTrendUsesOnlySelectedWoundAndOrdersActualTimes() {
        let early = record("a1", day: 1, area: 10, wound: "a")
        let late = record("a2", day: 3, area: 8, wound: "a")
        let other = record("b", day: 2, area: 99, wound: "b")
        let legacy = record("old", day: 2, area: 77)
        let points = LiteWoundTrend.points(woundID: "a", records: [late, other, early, legacy])
        XCTAssertEqual(points.map(\.id), ["a1", "a2"])
        XCTAssertEqual(points.map(\.area), [10, 8])
        XCTAssertEqual(points[1].date.timeIntervalSince(points[0].date), 172800)
        XCTAssertTrue(LiteWoundTrend.points(woundID: "", records: [early, legacy]).isEmpty)
    }

    func testTrendRejectsInvalidDateAndNonFiniteOrNegativeArea() {
        var badDate = record("date", day: 1, area: 10, wound: "a")
        badDate.dateISO = "not-a-date"
        let records = [badDate,
                       record("nan", day: 2, area: .nan, wound: "a"),
                       record("inf", day: 3, area: .infinity, wound: "a"),
                       record("negative", day: 4, area: -1, wound: "a"),
                       record("zero", day: 5, area: 0, wound: "a")]
        XCTAssertEqual(LiteWoundTrend.points(woundID: "a", records: records).map(\.id), ["zero"])
    }

    func testSinglePointAndEqualTimestampsRemainVisibleAndDeterministic() {
        let a = record("a", day: 1, area: 12, wound: "w")
        let b = record("b", day: 1, area: 13, wound: "w")
        XCTAssertEqual(LiteWoundTrend.points(woundID: "w", records: [a]).count, 1)
        XCTAssertEqual(LiteWoundTrend.points(woundID: "w", records: [b, a]).map(\.id), ["a", "b"])
        XCTAssertTrue(LiteWoundTrend.points(woundID: "missing", records: [a]).isEmpty)
    }

}
