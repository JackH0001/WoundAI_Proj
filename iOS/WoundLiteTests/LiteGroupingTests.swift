import XCTest
@testable import WoundLite

final class LiteGroupingTests: XCTestCase {
    private func record(_ id: String, day: Int, area: Double, wound: String? = nil) -> LiteRecord {
        LiteRecord(id: id, dateISO: String(format: "2026-10-%02dT10:00:00Z", day),
                   surfaceCm2: area, projectedCm2: area, volumeMl: 1.2, maxDepthMm: 2.3,
                   quality: "ok", imageName: "synthetic-unused", source: "manual",
                   woundID: wound, woundName: wound.map { "傷口 \($0)" }, contourOrigin: .manual)
    }

    func testModelChangesAndMissingVersionsBreakTrendLinesAndPercentChanges() throws {
        var rows = (1...6).map { record("r\($0)", day: $0, area: Double($0), wound: "w") }
        let corrected = LiteContourOrigin.cloud(route: "student", contract: LiteContourOrigin.studentV1)
        rows[0].contourOrigin = nil
        rows[1].contourOrigin = corrected
        rows[2].contourOrigin = corrected
        rows[3].contourOrigin = .cloud(route: "student", contract: "unreviewed/v2")
        rows[4].contourOrigin = corrected
        rows[5].contourOrigin = .cloud(route: "cloud_escalated(AU)", contract: LiteContourOrigin.studentV1)
        let ids = LiteWoundTrend.points(woundID: "w", records: rows.reversed()).map(\.lineGroup)
        XCTAssertEqual(ids, [nil, "r2", "r2", nil, "r5", "r6"])
        XCTAssertNil(LiteGrouping.changePercent(rows[1], in: rows))
        XCTAssertEqual(LiteGrouping.changePercent(rows[2], in: rows), 50)
        XCTAssertNil(LiteGrouping.changePercent(rows[4], in: rows))
        XCTAssertNil(LiteGrouping.changePercent(rows[5], in: rows))
        rows[2].source = "manual"
        XCTAssertEqual(try JSONDecoder().decode([LiteRecord].self, from: JSONEncoder().encode(rows))[2].contourOrigin, corrected)
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

    func testPhotoPairAndListUseSameInstantOrderIncludingOffsetsAndFractionalSeconds() throws {
        var old = record("old", day: 1, area: 1, wound: "a")
        var first = record("prior", day: 1, area: 3, wound: "a")
        var last = record("recent", day: 2, area: 7, wound: "a")
        var undated = record("undated", day: 3, area: 5, wound: "a")
        old.dateISO = "2026-11-01T20:44:00+08:00"
        first.dateISO = "2026-11-01T16:00:00.100Z"
        last.dateISO = "2026-11-01T12:00:00.200-04:00"
        last.imageName = "" // A missing recent photo must not substitute the older date.
        undated.dateISO = "unknown"
        let other = record("other", day: 20, area: 9, wound: "b")
        let rows = [last, undated, other, old, first]
        let group = LiteWoundGroup(id: "a", name: "測試")
        guard case .pair(let previous, let current, let omitted) = LitePhotoComparison(group: group, records: rows)
        else { return XCTFail("Expected the two most recent observations from a single wound") }
        XCTAssertEqual([previous.id, current.id, current.imageName], ["prior", "recent", ""])
        XCTAssertEqual(omitted, 1)
        XCTAssertEqual(LiteGrouping.records(for: group.id, in: rows).map(\.id), ["recent", "prior", "old", "undated"])
        XCTAssertEqual(LiteGrouping.mostRecentWoundID(rows), group.id)
        XCTAssertEqual(LiteGrouping.changePercent(last, in: rows) ?? -1, 400.0/3.0, accuracy: 0.001)
        XCTAssertEqual(try LiteTimelineSelection(group: group, records: rows).rows.map(\.id),
                       ["old", "prior", "recent", "undated"])
    }

    func testPhotoPairWithholdsAmbiguousTimesUnassignedAndDifferentOwners() {
        let group = LiteWoundGroup(id: "a", name: "測試")
        let first = record("first", day: 1, area: 1, wound: "a")
        let last = record("last", day: 2, area: 2, wound: "a")
        var foreign = first
        foreign.woundAccount = "other"
        for records in [[first], [first, record("not assigned", day: 2, area: 1)],
                        [first, last, record("tie", day: 1, area: 4, wound: "a")],
                        [first, last, record("tie", day: 2, area: 5, wound: "a")],
                        [last, foreign], [first, first, last]] {
            guard case .unavailable = LitePhotoComparison(group: group, records: records) else {
                return XCTFail("Must not infer a pair across people, missing assignments or ambiguous timestamps")
            }
        }
    }

    func testConflictingOwnersOfOneImportedGroupCannotBeComparedOrExported() {
        var first = record("a", day: 1, area: 2, wound: "w")
        var second = record("b", day: 2, area: 4, wound: "w")
        first.woundAccount = "account-a"; second.woundAccount = "account-b"
        let group = LiteWoundGroup(id: "w", name: "測試", account: "account-a")
        let rows = [first, second]
        XCTAssertThrowsError(try LiteTimelineSelection(group: group, records: rows))
        XCTAssertTrue(LiteWoundTrend.points(woundID: group.id, records: rows).isEmpty)
        XCTAssertNil(LiteGrouping.changePercent(second, in: rows))
        XCTAssertEqual(Set(LiteGrouping.records(for: group.id, in: rows).map(\.id)), ["a", "b"])
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
