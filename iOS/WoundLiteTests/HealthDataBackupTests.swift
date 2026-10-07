import XCTest
@testable import WoundLite

final class HealthDataBackupTests: XCTestCase {
    private func folder() throws -> URL {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        return directory
    }
    private func excluded(_ url: URL) throws -> Bool {
        try URL(fileURLWithPath: url.path).resourceValues(forKeys: [.isExcludedFromBackupKey]).isExcludedFromBackup == true
    }
    private func resetExclusion(_ url: URL) throws {
        var item = url; var values = URLResourceValues(); values.isExcludedFromBackup = false
        try item.setResourceValues(values)
    }

    func testAtomicReplacementRetainsExclusion() throws {
        let directory = try folder(); defer { try? FileManager.default.removeItem(at: directory) }
        let file = directory.appendingPathComponent("record.json")
        try Data("old record".utf8).write(to: file)
        for value in ["revision one", "revision two"] {
            try resetExclusion(directory); try resetExclusion(file)
            try HealthDataFiles.write(Data(value.utf8), to: file)
            XCTAssertEqual(try Data(contentsOf: file), Data(value.utf8))
            XCTAssertTrue(try excluded(file)); XCTAssertTrue(try excluded(directory))
            XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath: directory.path), ["record.json"])
        }
    }

    func testDeviceFileProtectionClassSurvivesAtomicReplacement() throws {
        #if targetEnvironment(simulator)
        throw XCTSkip("This simulator returns no protectionKey; verify the class on an iOS device.")
        #else
        let directory = try folder(); defer { try? FileManager.default.removeItem(at: directory) }
        let file = directory.appendingPathComponent("record.json")
        for bytes in [Data([1]), Data([2])] {
            try HealthDataFiles.write(bytes, to: file)
            let attributes = try FileManager.default.attributesOfItem(atPath: file.path)
            XCTAssertEqual(attributes[.protectionKey] as? String,
                           FileProtectionType.completeUntilFirstUserAuthentication.rawValue)
        }
        #endif
    }

    func testExistingAttachmentDirectoryIsExcludedWithoutChangingEncryptedBytes() throws {
        let directory = try folder(); defer { try? FileManager.default.removeItem(at: directory) }
        let store = LocalImageStore(directory: directory)
        let raw = Data([1,2,3,4,5])
        let name = try XCTUnwrap(store.saveRaw(raw)); let file = directory.appendingPathComponent(name)
        let cipher = try Data(contentsOf: file); XCTAssertNotEqual(cipher, raw)
        try resetExclusion(directory); try resetExclusion(file)
        let restarted = LocalImageStore(directory: directory)
        XCTAssertTrue(try excluded(directory))
        XCTAssertEqual(restarted.rawBytes(name), raw)
        XCTAssertTrue(try excluded(file)); XCTAssertEqual(try Data(contentsOf: file), cipher)
    }

    func testNewImageAndDepthFilesRequestBackupExclusion() throws {
        let directory = try folder(); defer { try? FileManager.default.removeItem(at: directory) }
        let store = LocalImageStore(directory: directory)
        for name in [try XCTUnwrap(store.save(jpeg: Data([1,2]))), try XCTUnwrap(store.saveRaw(Data([3,4])))] {
            XCTAssertTrue(try excluded(directory.appendingPathComponent(name)))
        }
        XCTAssertTrue(try excluded(directory))
    }

    func testUnusableParentCannotCreateAttachmentOrReplaceSource() throws {
        let directory = try folder(); defer { try? FileManager.default.removeItem(at: directory) }
        let blocked = directory.appendingPathComponent("not-a-directory")
        let original = Data("keep".utf8); try original.write(to: blocked)
        let store = LocalImageStore(directory: blocked)
        XCTAssertNil(store.saveRaw(Data([5])))
        XCTAssertNil(store.save(jpeg: Data([6])))
        XCTAssertEqual(try Data(contentsOf: blocked), original)
    }

    func testSymlinkDestinationDoesNotModifyExternalFile() throws {
        let directory = try folder(); defer { try? FileManager.default.removeItem(at: directory) }
        let original = directory.appendingPathComponent("original")
        let alias = directory.appendingPathComponent("alias")
        let bytes = Data("keep".utf8); try bytes.write(to: original)
        try FileManager.default.createSymbolicLink(at: alias, withDestinationURL: original)
        XCTAssertThrowsError(try HealthDataFiles.write(Data("new".utf8), to: alias))
        XCTAssertEqual(try Data(contentsOf: original), bytes)
    }

    func testDirectoryDestinationFailsWithoutRemovingItsContents() throws {
        let directory = try folder(); defer { try? FileManager.default.removeItem(at: directory) }
        let destination = directory.appendingPathComponent("occupied")
        try FileManager.default.createDirectory(at: destination, withIntermediateDirectories: false)
        let keep = destination.appendingPathComponent("keep"); try Data([7]).write(to: keep)
        XCTAssertThrowsError(try HealthDataFiles.write(Data([8]), to: destination))
        XCTAssertEqual(try Data(contentsOf: keep), Data([7]))
        XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath: directory.path), ["occupied"])
    }

    @MainActor
    func testLegacyLiteIndexReloadAndReplacementPreserveRecordsAndExcludeBackups() throws {
        let directory = try folder(); defer { try? FileManager.default.removeItem(at: directory) }
        let file = directory.appendingPathComponent("lite_records.json")
        let record = LiteRecord(id: "legacy", dateISO: "2026-10-05T00:00:00Z", surfaceCm2: 4,
            projectedCm2: 3.8, quality: "ok", imageName: "image.enc", source: "manual")
        let old = try JSONEncoder().encode([record]); try old.write(to: file)
        let store = LiteStore(fileURL: file)
        XCTAssertEqual(store.records.map(\.id), ["legacy"]); XCTAssertFalse(store.needsReload)
        XCTAssertEqual(try Data(contentsOf: file), old)
        XCTAssertTrue(try excluded(file)); XCTAssertTrue(try excluded(directory))
        var revision = record; revision.surfaceCm2 = 3
        XCTAssertTrue(store.update(revision)); XCTAssertTrue(try excluded(file))
        XCTAssertEqual(LiteStore(fileURL: file).records.first?.surfaceCm2, 3)
    }

    @MainActor
    func testLiteIndexCannotFollowSymlinkOrOverwriteOriginal() throws {
        let directory = try folder(); defer { try? FileManager.default.removeItem(at: directory) }
        let original = directory.appendingPathComponent("real.json"), alias = directory.appendingPathComponent("alias.json")
        let bytes = Data("[]".utf8); try bytes.write(to: original)
        try FileManager.default.createSymbolicLink(at: alias, withDestinationURL: original)
        let store = LiteStore(fileURL: alias)
        XCTAssertTrue(store.needsReload); XCTAssertNotNil(store.storageError)
        XCTAssertEqual(try Data(contentsOf: original), bytes)
    }
}
