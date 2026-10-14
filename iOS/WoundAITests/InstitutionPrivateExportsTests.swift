import XCTest
import CryptoKit
#if WOUND_INSTITUTION_TESTS
@testable import WoundAIInstitution
#else
@testable import WoundMeasurementApp
#endif

final class InstitutionPrivateExportsTests: XCTestCase {
    func testExportToggleDefaultsOffAndHonoursBuildCapability() {
        let d = UserDefaults.standard, key = InstitutionPrivateExports.preferenceKey
        let old = d.object(forKey:key); defer { if let old { d.set(old,forKey:key) } else { d.removeObject(forKey:key) } }
        d.removeObject(forKey:key)
        XCTAssertFalse(InstitutionPrivateExports.automaticEnabled)
        d.set(true,forKey:key)
        XCTAssertEqual(InstitutionPrivateExports.automaticEnabled, InstitutionExport.enabled)
        d.set(false,forKey:key)
        XCTAssertFalse(InstitutionPrivateExports.automaticEnabled)
    }
    func testPrivateCopyIsEncryptedExcludedAndRoundTripsOnlyOnExplicitRead() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at:root) }
        let staging = root.appendingPathComponent("measurement-fixture"), vault = root.appendingPathComponent("private")
        try FileManager.default.createDirectory(at:staging,withIntermediateDirectories:true)
        let data = Data("synthetic-image-data-not-for-upload".utf8)
        let hash = SHA256.hash(data:data).map { String(format:"%02x",$0) }.joined()
        try data.write(to:staging.appendingPathComponent("image.png"))
        try JSONSerialization.data(withJSONObject:["image.png":hash]).write(to:staging.appendingPathComponent("sha256.json"))
        let first = try InstitutionPrivateExports.save(folder:staging,directory:vault)
        XCTAssertEqual(try InstitutionPrivateExports.save(folder:staging,directory:vault),first)
        let encrypted = try Data(contentsOf:first)
        XCTAssertNil(encrypted.range(of:data))
        XCTAssertEqual(try InstitutionPrivateExports.read(first)["image.png"],data)
        XCTAssertEqual(try first.resourceValues(forKeys:[.isExcludedFromBackupKey]).isExcludedFromBackup,true)
        try Data("corrupt".utf8).write(to:first)
        XCTAssertThrowsError(try InstitutionPrivateExports.read(first))
        XCTAssertThrowsError(try InstitutionPrivateExports.save(folder:staging,directory:vault))
    }
    /// Run after the system folder UI test. An unprovisioned simulator is not a provider acceptance.
    func testPersistedSystemFolderDeliversAfterProcessRestart() throws {
        let prefs = UserDefaults.standard
        try XCTSkipIf(prefs.data(forKey: InstitutionPrivateExports.folderKey) == nil, "Requires a system-picker grant from testSelectLocalFolderForRestartValidation")
        let bookmark = try XCTUnwrap(prefs.data(forKey: InstitutionPrivateExports.folderKey))
        let bytes = Data("synthetic-local-folder-validation".utf8)
        let hashes = ["fixture.json": SHA256.hash(data:bytes).map{String(format:"%02x",$0)}.joined()]
        let files = ["fixture.json":bytes,"sha256.json":try JSONSerialization.data(withJSONObject:hashes)]
        let name = "synthetic-folder-check-" + UUID().uuidString
        try InstitutionPrivateExports.deliver(files:files,name:name,bookmark:bookmark)
        var stale=false
        let folder = try URL(resolvingBookmarkData:bookmark,options:.withoutUI,relativeTo:nil,bookmarkDataIsStale:&stale)
        XCTAssertFalse(stale); XCTAssertTrue(folder.startAccessingSecurityScopedResource())
        defer {folder.stopAccessingSecurityScopedResource()}
        let own = try FileManager.default.contentsOfDirectory(at:folder,includingPropertiesForKeys:nil).filter{$0.lastPathComponent.hasPrefix(name)}
        XCTAssertEqual(own.count,1)
        for item in own {
            for (file,data) in files {XCTAssertEqual(try Data(contentsOf:item.appendingPathComponent(file)),data)}
            try FileManager.default.removeItem(at:item)
        }
        prefs.removeObject(forKey:InstitutionPrivateExports.folderKey)
        prefs.removeObject(forKey:InstitutionPrivateExports.folderLabelKey)
        prefs.set(false,forKey:InstitutionPrivateExports.preferenceKey)
    }

    func testTamperedPlaintextSourceCannotBecomePrivateExport() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at:root) }
        try FileManager.default.createDirectory(at:root,withIntermediateDirectories:true)
        try Data("bad".utf8).write(to:root.appendingPathComponent("image.png"))
        try JSONSerialization.data(withJSONObject:["image.png":"wrong"]).write(to:root.appendingPathComponent("sha256.json"))
        XCTAssertThrowsError(try InstitutionPrivateExports.save(folder:root,directory:root.appendingPathComponent("vault")))
    }
}
