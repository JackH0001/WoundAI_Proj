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
    func testTamperedPlaintextSourceCannotBecomePrivateExport() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at:root) }
        try FileManager.default.createDirectory(at:root,withIntermediateDirectories:true)
        try Data("bad".utf8).write(to:root.appendingPathComponent("image.png"))
        try JSONSerialization.data(withJSONObject:["image.png":"wrong"]).write(to:root.appendingPathComponent("sha256.json"))
        XCTAssertThrowsError(try InstitutionPrivateExports.save(folder:root,directory:root.appendingPathComponent("vault")))
    }
}
