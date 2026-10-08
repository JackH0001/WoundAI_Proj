import XCTest
@testable import WoundLite

final class LiteStoreLifecycleTests: XCTestCase {
    private func record(_ id: String = "first", image: String = "unused", depth: String? = nil) -> LiteRecord {
        LiteRecord(id: id, dateISO: "2026-10-03T10:00:00Z", surfaceCm2: 4, projectedCm2: 3.9,
                   volumeMl: 1.2, maxDepthMm: 2.3, quality: "ok", imageName: image, source: "manual",
                   polysJson: "[[[0,0],[8,0],[8,8]]]", depthName: depth, woundID: "wound-a", woundName: "傷口 1")
    }

    private func directory() throws -> URL {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }

    @MainActor
    func testCorruptIndexCannotBeOverwrittenByNewRecord() throws {
        let dir = try directory()
        defer { try? FileManager.default.removeItem(at: dir) }
        let file = dir.appendingPathComponent("records.json")
        let original = Data("[{\"id\":\"valuable-old-record\"".utf8)
        try original.write(to: file)
        let store = LiteStore(fileURL: file)
        XCTAssertNotNil(store.storageError)
        XCTAssertFalse(store.add(record()))
        XCTAssertEqual(try Data(contentsOf: file), original)
    }

    @MainActor
    func testDuplicateIDsAreRejectedOnLoadAndAdd() throws {
        let dir = try directory()
        defer { try? FileManager.default.removeItem(at: dir) }
        let file = dir.appendingPathComponent("records.json")
        let store = LiteStore(fileURL: file)
        XCTAssertTrue(store.add(record()))
        let original = try Data(contentsOf: file)
        XCTAssertFalse(store.add(record()))
        XCTAssertEqual(try Data(contentsOf: file), original)
        let duplicate = try JSONEncoder().encode([record(), record()])
        try duplicate.write(to: file)
        let reloaded = LiteStore(fileURL: file)
        XCTAssertNotNil(reloaded.storageError)
        XCTAssertFalse(reloaded.add(record("third")))
        XCTAssertEqual(try Data(contentsOf: file), duplicate)
    }

    @MainActor
    func testStaleStoreDoesNotOverwriteAnotherSavedRecord() throws {
        let dir = try directory()
        defer { try? FileManager.default.removeItem(at: dir) }
        let file = dir.appendingPathComponent("records.json")
        let first = LiteStore(fileURL: file)
        XCTAssertTrue(first.add(record()))
        let stale = LiteStore(fileURL: file)
        XCTAssertTrue(first.add(record("second")))
        let latest = try Data(contentsOf: file)
        XCTAssertFalse(stale.add(record("third")))
        XCTAssertEqual(try Data(contentsOf: file), latest)
        XCTAssertNotNil(stale.storageError)
    }

    @MainActor
    func testDeletionUsesCurrentAttachmentsNotStaleViewSnapshot() throws {
        let dir = try directory()
        defer { try? FileManager.default.removeItem(at: dir) }
        let file = dir.appendingPathComponent("records.json")
        let store = LiteStore(fileURL: file)
        let oldImage = try XCTUnwrap(store.images.saveRaw(Data([1])))
        let newImage = try XCTUnwrap(store.images.saveRaw(Data([2])))
        let newDepth = try XCTUnwrap(store.images.saveRaw(Data([3])))
        defer { [oldImage, newImage, newDepth].forEach { store.images.delete($0) } }
        let old = record(image: oldImage)
        XCTAssertTrue(store.add(old))
        XCTAssertTrue(store.update(record(image: newImage, depth: newDepth)))
        store.delete(old)
        XCTAssertTrue(store.images.exists(oldImage), "A stale view must not choose which file to erase")
        XCTAssertFalse(store.images.exists(newImage))
        XCTAssertFalse(store.images.exists(newDepth))
        XCTAssertTrue(LiteStore(fileURL: file).records.isEmpty)
    }

    @MainActor
    func testAbsentRecordCannotDeleteUnrelatedMedia() throws {
        let dir = try directory()
        defer { try? FileManager.default.removeItem(at: dir) }
        let store = LiteStore(fileURL: dir.appendingPathComponent("records.json"))
        let unrelated = try XCTUnwrap(store.images.saveRaw(Data([4])))
        defer { store.images.delete(unrelated) }
        store.delete(record("missing", image: unrelated))
        XCTAssertTrue(store.images.exists(unrelated))
    }

    @MainActor
    func testFailedIndexWritePreservesRecordAndAttachments() throws {
        let dir = try directory()
        defer { try? FileManager.default.removeItem(at: dir) }
        let file = dir.appendingPathComponent("records.json")
        let store = LiteStore(fileURL: file)
        let image = try XCTUnwrap(store.images.saveRaw(Data([5])))
        let depth = try XCTUnwrap(store.images.saveRaw(Data([6])))
        defer { [image, depth].forEach { store.images.delete($0) } }
        let saved = record(image: image, depth: depth)
        XCTAssertTrue(store.add(saved))
        // Deterministic unavailable path, no permission assumptions or real user files.
        let backup = dir.appendingPathComponent("backup.json")
        try FileManager.default.moveItem(at: file, to: backup)
        try FileManager.default.createDirectory(at: file, withIntermediateDirectories: false)
        store.delete(saved)
        XCTAssertEqual(store.records.map(\.id), [saved.id])
        XCTAssertTrue(store.images.exists(image))
        XCTAssertTrue(store.images.exists(depth))
        XCTAssertNotNil(store.storageError)
    }

    @MainActor
    func testSaveRestartUpdateRestartDeleteKeepsOtherWound() throws {
        let dir = try directory()
        defer { try? FileManager.default.removeItem(at: dir) }
        let file = dir.appendingPathComponent("records.json")
        let initial = LiteStore(fileURL: file)
        let image = try XCTUnwrap(initial.images.saveRaw(Data([7])))
        let depth = try XCTUnwrap(initial.images.saveRaw(Data([8])))
        defer { [image, depth].forEach { initial.images.delete($0) } }
        XCTAssertTrue(initial.add(record(image: image, depth: depth)))
        var other = record("other")
        other.woundID = "wound-b"
        XCTAssertTrue(initial.add(other))
        let restarted = LiteStore(fileURL: file)
        var updated = try XCTUnwrap(restarted.records.first { $0.id == "first" })
        updated.surfaceCm2 = 3.5
        updated.volumeMl = 0.8
        updated.polysJson = "[[[1,1],[7,1],[7,7]]]"
        XCTAssertTrue(restarted.update(updated))
        let next = LiteStore(fileURL: file)
        let saved = try XCTUnwrap(next.records.first { $0.id == "first" })
        XCTAssertEqual(saved.surfaceCm2, 3.5)
        XCTAssertEqual(saved.volumeMl, 0.8)
        XCTAssertEqual(saved.woundID, "wound-a")
        XCTAssertEqual(saved.polysJson, updated.polysJson)
        next.delete(saved)
        XCTAssertEqual(LiteStore(fileURL: file).records.map(\.id), ["other"])
        XCTAssertFalse(next.images.exists(image))
        XCTAssertFalse(next.images.exists(depth))
    }

    @MainActor
    func testReadFailureCanRecoverOnlyAfterValidReload() throws {
        let dir = try directory()
        defer { try? FileManager.default.removeItem(at: dir) }
        let file = dir.appendingPathComponent("records.json")
        let store = LiteStore(fileURL: file)
        XCTAssertTrue(store.add(record()))
        let original = try Data(contentsOf: file)
        try Data("invalid".utf8).write(to: file)
        XCTAssertFalse(store.reload())
        XCTAssertTrue(store.needsReload)
        XCTAssertEqual(store.records.map(\.id), ["first"], "Keep the last known records visible")
        XCTAssertFalse(store.delete(record()))
        XCTAssertFalse(store.update(record()))
        XCTAssertEqual(try Data(contentsOf: file), Data("invalid".utf8))
        try original.write(to: file)
        XCTAssertTrue(store.reload())
        XCTAssertFalse(store.needsReload)
        XCTAssertNil(store.storageError)
        XCTAssertTrue(store.add(record("second")))
        XCTAssertEqual(LiteStore(fileURL: file).records.count, 2)
    }

    @MainActor
    func testSharedAttachmentIsDeletedOnlyAfterLastRecord() throws {
        let dir = try directory()
        defer { try? FileManager.default.removeItem(at: dir) }
        let store = LiteStore(fileURL: dir.appendingPathComponent("records.json"))
        let image = try XCTUnwrap(store.images.saveRaw(Data([9])))
        defer { store.images.delete(image) }
        let first = record("first", image: image)
        let second = record("second", image: image)
        XCTAssertTrue(store.add(first))
        XCTAssertTrue(store.add(second))
        XCTAssertTrue(store.delete(first))
        XCTAssertTrue(store.images.exists(image))
        XCTAssertTrue(store.delete(second))
        XCTAssertFalse(store.images.exists(image))
    }

    @MainActor
    func testPreviouslyLoadedIndexDisappearingIsNotAnEmptyLibrary() throws {
        let dir = try directory()
        defer { try? FileManager.default.removeItem(at: dir) }
        let file = dir.appendingPathComponent("records.json")
        let store = LiteStore(fileURL: file)
        XCTAssertTrue(store.add(record()))
        try FileManager.default.removeItem(at: file)
        XCTAssertFalse(store.reload())
        XCTAssertTrue(store.needsReload)
        XCTAssertFalse(store.add(record("second")))
        XCTAssertFalse(FileManager.default.fileExists(atPath: file.path))
        XCTAssertEqual(store.records.map(\.id), ["first"])
    }
}
