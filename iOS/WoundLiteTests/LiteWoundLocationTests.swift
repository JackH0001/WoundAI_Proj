import XCTest
@testable import WoundLite

final class LiteWoundLocationTests: XCTestCase {
    private let rightHeel = LiteWoundLocation(side: "right", site: "heel")
    private let leftHeel = LiteWoundLocation(side: "left", site: "heel")
    private let empty = LiteWoundLocation(side: "", site: "")
    private func record(_ id: String = UUID().uuidString, wound: String? = nil) -> LiteRecord {
        LiteRecord(id: id, dateISO: "2026-10-03T10:00:00Z", surfaceCm2: 4, projectedCm2: 4,
                   quality: "ok", imageName: "test-unused", source: "manual", woundID: wound, woundName: "傷口 1")
    }
    private func tempFile() throws -> URL {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir.appendingPathComponent("records.json")
    }
    func testRequiredFieldsAndInvalidCombinations() {
        XCTAssertFalse(empty.isComplete)
        XCTAssertFalse(LiteWoundLocation(side: "left", site: "").isComplete)
        XCTAssertFalse(LiteWoundLocation(side: "", site: "heel").isComplete)
        XCTAssertFalse(LiteWoundLocation(side: "midline", site: "heel").isComplete)
        XCTAssertFalse(LiteWoundLocation(side: "left", site: "invented").isComplete)
        XCTAssertTrue(rightHeel.isComplete)
        XCTAssertTrue(LiteWoundLocation(side: "midline", site: "sacrococcygeal").isComplete)
    }
    @MainActor
    func testNewWoundCannotSaveUntilLocationComplete() throws {
        let file = try tempFile(); defer { try? FileManager.default.removeItem(at: file.deletingLastPathComponent()) }
        let store = LiteStore(fileURL: file)
        XCTAssertNil(store.saveMeasurement(record(), selectedWoundID: "", enteredLocation: empty))
        XCTAssertTrue(store.records.isEmpty)
        XCTAssertFalse(FileManager.default.fileExists(atPath: file.path))
        let saved = try XCTUnwrap(store.saveMeasurement(record(), selectedWoundID: "", enteredLocation: rightHeel))
        XCTAssertEqual(saved.woundLocation, rightHeel)
        XCTAssertNotNil(saved.woundID)
        XCTAssertEqual(LiteStore(fileURL: file).records.first?.woundLocation, rightHeel)
    }
    @MainActor
    func testExistingWoundUsesItsOwnLocationWithoutRetyping() throws {
        let file = try tempFile(); defer { try? FileManager.default.removeItem(at: file.deletingLastPathComponent()) }
        let store = LiteStore(fileURL: file)
        let first = try XCTUnwrap(store.saveMeasurement(record(), selectedWoundID: "", enteredLocation: rightHeel))
        let second = try XCTUnwrap(store.saveMeasurement(record(), selectedWoundID: try XCTUnwrap(first.woundID), enteredLocation: leftHeel))
        XCTAssertEqual(second.woundID, first.woundID)
        XCTAssertEqual(second.woundLocation, rightHeel)
        XCTAssertNotNil(store.saveMeasurement(record(), selectedWoundID: first.woundID!, enteredLocation: empty))
    }
    @MainActor
    func testSameLocationStillCreatesSeparateWounds() throws {
        let file = try tempFile(); defer { try? FileManager.default.removeItem(at: file.deletingLastPathComponent()) }
        let store = LiteStore(fileURL: file)
        let a = try XCTUnwrap(store.saveMeasurement(record(), selectedWoundID: "", enteredLocation: rightHeel))
        let b = try XCTUnwrap(store.saveMeasurement(record(), selectedWoundID: "", enteredLocation: rightHeel))
        XCTAssertNotEqual(a.woundID, b.woundID)
        XCTAssertEqual(store.wounds.count, 2)
    }
    @MainActor
    func testLegacyGroupCompletionIsPersistedForEveryMember() throws {
        let file = try tempFile(); defer { try? FileManager.default.removeItem(at: file.deletingLastPathComponent()) }
        let old = [record("old1", wound: "legacy"), record("old2", wound: "legacy")]
        try JSONEncoder().encode(old).write(to: file)
        let store = LiteStore(fileURL: file)
        XCTAssertNil(store.wounds.first?.location)
        XCTAssertNil(store.saveMeasurement(record(), selectedWoundID: "legacy", enteredLocation: empty))
        XCTAssertNotNil(store.saveMeasurement(record(), selectedWoundID: "legacy", enteredLocation: leftHeel))
        let reloaded = LiteStore(fileURL: file)
        XCTAssertEqual(reloaded.records.count, 3)
        XCTAssertTrue(reloaded.records.allSatisfy { $0.woundLocation == leftHeel })
    }
    @MainActor
    func testUnknownGroupDoesNotSilentlyBecomeNewWound() throws {
        let file = try tempFile(); defer { try? FileManager.default.removeItem(at: file.deletingLastPathComponent()) }
        let store = LiteStore(fileURL: file)
        XCTAssertNil(store.saveMeasurement(record(), selectedWoundID: "deleted", enteredLocation: rightHeel))
        XCTAssertTrue(store.records.isEmpty)
    }
    @MainActor
    func testFailedSaveDoesNotPartiallyBackfillLegacyLocation() throws {
        let file = try tempFile(); defer { try? FileManager.default.removeItem(at: file.deletingLastPathComponent()) }
        let old = record("old", wound: "legacy")
        try JSONEncoder().encode([old]).write(to: file)
        let store = LiteStore(fileURL: file)
        let original = try Data(contentsOf: file)
        try FileManager.default.removeItem(at: file)
        try FileManager.default.createDirectory(at: file, withIntermediateDirectories: false)
        XCTAssertNil(store.saveMeasurement(record(), selectedWoundID: "legacy", enteredLocation: rightHeel))
        XCTAssertEqual(store.records.count, 1)
        XCTAssertNil(store.records[0].woundLocation)
        try FileManager.default.removeItem(at: file)
        try original.write(to: file)
        XCTAssertNil(LiteStore(fileURL: file).records[0].woundLocation)
    }
    @MainActor
    func testDetailReassignmentRequiresLocationAndKeepsMeasurements() throws {
        let file = try tempFile(); defer { try? FileManager.default.removeItem(at: file.deletingLastPathComponent()) }
        let store = LiteStore(fileURL: file)
        let old = record("old")
        XCTAssertTrue(store.add(old))
        var group = store.newWound()
        XCTAssertFalse(store.assign(old, to: group))
        group.location = rightHeel
        XCTAssertTrue(store.assign(old, to: group))
        let saved = try XCTUnwrap(LiteStore(fileURL: file).records.first)
        XCTAssertEqual(saved.surfaceCm2, old.surfaceCm2)
        XCTAssertEqual(saved.woundLocation, rightHeel)
        XCTAssertEqual(saved.woundID, group.id)
    }
    func testDefaultUsesLatestMeasurementInsteadOfGroupCreationOrder() {
        var old = record("old", wound: "first")
        old.dateISO = "2026-10-01T10:00:00Z"
        var other = record("other", wound: "second")
        other.dateISO = "2026-10-02T10:00:00Z"
        var followUp = record("follow-up", wound: "first")
        followUp.dateISO = "2026-10-04T10:00:00Z"
        XCTAssertEqual(LiteGrouping.mostRecentWoundID([other, old, followUp]), "first")
        XCTAssertEqual(LiteGrouping.mostRecentWoundID([old, followUp, other]), "first")
    }

    func testDefaultSkipsUngroupedAndInvalidDates() {
        var invalid = record("invalid", wound: "invalid")
        invalid.dateISO = "not-a-date"
        XCTAssertNil(LiteGrouping.mostRecentWoundID([]))
        XCTAssertNil(LiteGrouping.mostRecentWoundID([record(), invalid]))
        XCTAssertEqual(LiteGrouping.mostRecentWoundID([record("valid", wound: "valid"), invalid, record()]), "valid")
    }

    func testNewWoundRequiresExplicitModeAndCompleteLocation() {
        XCTAssertNil(LiteWoundSaveTarget.resolve(addingNew: false, selectedID: "", entered: rightHeel, groups: []))
        XCTAssertNil(LiteWoundSaveTarget.resolve(addingNew: true, selectedID: "", entered: empty, groups: []))
        let target = LiteWoundSaveTarget.resolve(addingNew: true, selectedID: "previous", entered: leftHeel,
            groups: [LiteWoundGroup(id: "previous", name: "傷口 1", location: rightHeel)])
        XCTAssertEqual(target?.woundID, "")
        XCTAssertEqual(target?.location, leftHeel)
    }

    @MainActor
    func testConfirmationPreparationDoesNotWriteAndExistingPositionIsAuthoritative() throws {
        let file = try tempFile(); defer { try? FileManager.default.removeItem(at: file.deletingLastPathComponent()) }
        let store = LiteStore(fileURL: file)
        let first = try XCTUnwrap(store.saveMeasurement(record(), selectedWoundID: "", enteredLocation: rightHeel))
        let before = try Data(contentsOf: file)
        let target = try XCTUnwrap(LiteWoundSaveTarget.resolve(addingNew: false,
            selectedID: first.woundID!, entered: leftHeel, groups: store.wounds))
        XCTAssertEqual(target.location, rightHeel)
        XCTAssertTrue(target.confirmationMessage.contains("右・足跟"))
        XCTAssertEqual(try Data(contentsOf: file), before)
        XCTAssertEqual(store.records.count, 1)
        let saved = try XCTUnwrap(store.saveMeasurement(record(), selectedWoundID: target.woundID, enteredLocation: target.location))
        XCTAssertEqual(saved.woundID, first.woundID)
        XCTAssertEqual(saved.woundLocation, target.location)
    }

    func testConfirmationBecomesInvalidWhenGroupDeletedOrLocationChanges() throws {
        let original = LiteWoundGroup(id: "a", name: "傷口 1", location: rightHeel)
        let target = try XCTUnwrap(LiteWoundSaveTarget.resolve(addingNew: false, selectedID: "a", entered: empty, groups: [original]))
        XCTAssertNil(LiteWoundSaveTarget.resolve(addingNew: false, selectedID: "a", entered: empty, groups: []))
        let changed = LiteWoundGroup(id: "a", name: "傷口 1", location: leftHeel)
        XCTAssertNotEqual(target, LiteWoundSaveTarget.resolve(addingNew: false, selectedID: "a", entered: empty, groups: [changed]))
        let other = LiteWoundGroup(id: "b", name: "傷口 2", location: rightHeel)
        XCTAssertNotEqual(target, LiteWoundSaveTarget.resolve(addingNew: false, selectedID: "b", entered: empty, groups: [other]))
    }

    @MainActor
    func testOtherLocationPersistsAndUsesStableCodeInRevision() throws {
        let file = try tempFile()
        defer { try? FileManager.default.removeItem(at: file.deletingLastPathComponent()) }
        let store = LiteStore(fileURL: file)
        for side in ["left", "midline", "right"] {
            let location = LiteWoundLocation(side: side, site: "other")
            var input = record()
            input.manuallyConfirmed = true
            input.cloudSync = LiteCloudSyncState(binding: LiteCloudBinding(
                server: "https://example.invalid", anonID: "synthetic", imageID: "synthetic",
                imageW: 32, imageH: 32))
            input.polysJson = "[[[0,0],[10,0],[10,10]]]"
            let saved = try XCTUnwrap(store.saveMeasurement(input, selectedWoundID: "", enteredLocation: location))
            let loaded = try XCTUnwrap(LiteStore(fileURL: file).records.first { $0.id == saved.id })
            XCTAssertEqual(loaded.woundLocation, location)
            XCTAssertTrue(loaded.woundLocation!.label.contains("其他"))
            let json = try XCTUnwrap(loaded.revisionJSON())
            let payload = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(json.utf8)) as? [String: Any])
            XCTAssertEqual(payload["wound_site"] as? String, "other")
            XCTAssertEqual(payload["wound_side"] as? String, side)
        }
        XCTAssertFalse(LiteWoundLocation(side: "", site: "other").isComplete)
    }

}
