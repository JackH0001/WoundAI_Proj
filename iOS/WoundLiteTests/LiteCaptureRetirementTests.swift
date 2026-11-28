import XCTest
@testable import WoundLite

@MainActor
final class LiteCaptureRetirementTests: XCTestCase {
    private func record(_ id: String, image: String = "unused") -> LiteRecord {
        LiteRecord(id: id, dateISO: "2026-11-01T10:00:00Z", surfaceCm2: 4, projectedCm2: 4,
                   quality: "ok", imageName: image, source: "manual")
    }
    private func folder() throws -> URL {
        let path = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try HealthDataFiles.prepareDirectory(path); return path
    }
    func testRetirementWriteFailureCannotRemoveRecordOrMedia() throws {
        let dir = try folder(); defer { try? FileManager.default.removeItem(at: dir) }
        let index = dir.appendingPathComponent("index.json")
        let store = LiteStore(fileURL: index, writeRetirement: { _, _ in throw LiteServiceFailure.conflict })
        let image = try XCTUnwrap(store.images.saveRaw(Data([17, 18])))
        defer { store.images.delete(image) }
        let row = record(UUID().uuidString, image: image)
        XCTAssertTrue(store.add(row))
        let before = try Data(contentsOf: index)
        XCTAssertFalse(store.delete(row))
        XCTAssertEqual(try Data(contentsOf: index), before)
        XCTAssertTrue(store.images.exists(image)); XCTAssertEqual(store.records.count, 1)
        XCTAssertTrue(store.retiredCaptureIDs.isEmpty)
    }
    func testInterruptedDeletionKeepsRecordButCannotRecreateItAfterRemoval() throws {
        let dir = try folder(); defer { try? FileManager.default.removeItem(at: dir) }
        let index = dir.appendingPathComponent("index.json"), first = LiteStore(fileURL: index)
        let id = UUID().uuidString, row = record(id)
        XCTAssertTrue(first.add(row))
        let failing = LiteStore(fileURL: index, writeIndex: { _, _ in throw LiteServiceFailure.conflict })
        XCTAssertFalse(failing.delete(row)); XCTAssertEqual(failing.records.count, 1)
        XCTAssertFalse(failing.captureCanResume(id))
        let healthy = LiteStore(fileURL: index)
        XCTAssertEqual(healthy.records.count, 1, "Opening the app must not silently finish a failed index deletion")
        XCTAssertTrue(healthy.delete(row))
        let restarted = LiteStore(fileURL: index)
        XCTAssertTrue(restarted.records.isEmpty); XCTAssertFalse(restarted.add(row))
        XCTAssertFalse(restarted.captureCanResume(id))
    }
    func testRetirementIsEncryptedAndVisibleToAnotherStoreWithoutIndexChange() throws {
        let dir = try folder(); defer { try? FileManager.default.removeItem(at: dir) }
        let index = dir.appendingPathComponent("index.json"), first = LiteStore(fileURL: index)
        let stale = LiteStore(fileURL: index), id = UUID().uuidString
        XCTAssertTrue(stale.captureCanResume(id))
        XCTAssertTrue(first.retireCapture(id, requireSavedRecord: false))
        XCTAssertFalse(stale.captureCanResume(id)); XCTAssertFalse(stale.add(record(id)))
        let url = LiteCaptureRetirement.file(for: index)
        let encrypted = try Data(contentsOf: url)
        XCTAssertNil(encrypted.range(of: Data(id.utf8)))
        XCTAssertNotNil(PhiCrypto.decryptBytes(encrypted))
        XCTAssertTrue(try url.resourceValues(forKeys: [.isExcludedFromBackupKey]).isExcludedFromBackup == true)
        let other = LiteStore(fileURL: dir.appendingPathComponent("other.json"))
        XCTAssertTrue(other.captureCanResume(id), "Separate libraries must not share a retirement namespace")
    }
    func testUnreadableOrDisappearingRetirementCannotPretendEmpty() throws {
        let dir = try folder(); defer { try? FileManager.default.removeItem(at: dir) }
        let index = dir.appendingPathComponent("index.json"), store = LiteStore(fileURL: index)
        let row = record(UUID().uuidString); XCTAssertTrue(store.add(row)); XCTAssertTrue(store.delete(row))
        let url = LiteCaptureRetirement.file(for: index), original = try Data(contentsOf: url)
        try Data("damaged".utf8).write(to: url)
        let damaged = LiteStore(fileURL: index)
        XCTAssertTrue(damaged.needsReload); XCTAssertFalse(damaged.add(record(UUID().uuidString)))
        XCTAssertEqual(try Data(contentsOf: url), Data("damaged".utf8))
        try HealthDataFiles.write(original, to: url)
        XCTAssertTrue(damaged.reload()); XCTAssertFalse(damaged.captureCanResume(row.id))
        try FileManager.default.removeItem(at: url)
        XCTAssertFalse(damaged.reload()); XCTAssertTrue(damaged.needsReload)
        XCTAssertFalse(damaged.add(row))
    }
    func testRetirementCannotRollBackKnownIDs() throws {
        let dir = try folder(); defer { try? FileManager.default.removeItem(at: dir) }
        let index = dir.appendingPathComponent("index.json"), store = LiteStore(fileURL: index)
        let first = UUID().uuidString, second = UUID().uuidString
        XCTAssertTrue(store.retireCapture(first, requireSavedRecord: false))
        let url = LiteCaptureRetirement.file(for: index), old = try Data(contentsOf: url)
        XCTAssertTrue(store.retireCapture(second, requireSavedRecord: false))
        try HealthDataFiles.write(old, to: url)
        XCTAssertFalse(store.captureCanResume(second)); XCTAssertTrue(store.needsReload)
    }
}
