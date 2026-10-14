import XCTest
#if WOUND_INSTITUTION_TESTS
@testable import WoundAIInstitution
#else
@testable import WoundMeasurementApp
#endif

final class MedicalAccountPreferencesTests: XCTestCase {
    private var names: [String] = []
    private func defaults(_ scope: MedicalDataScope) -> UserDefaults {
        let name = "synthetic-preferences-" + UUID().uuidString + "." + scope.settingsSuite
        names.append(name)
        return UserDefaults(suiteName: name)!
    }
    override func tearDown() {
        for name in names { UserDefaults.standard.removePersistentDomain(forName: name) }
        names = []; super.tearDown()
    }
    func testCredentialsSurviveReopenAndClearOnlyTheirScope() throws {
        let da = defaults(.mmh), db = defaults(.syntheticReview)
        let a = MedicalAccountPreferences(scope: .mmh, defaults: da)
        let b = MedicalAccountPreferences(scope: .syntheticReview, defaults: db)
        let pa = UUID().uuidString, pb = UUID().uuidString
        XCTAssertTrue(a.setCredentials(user: "fixture-a", password: pa))
        XCTAssertTrue(b.setCredentials(user: "fixture-b", password: pb))
        XCTAssertNotEqual(da.string(forKey: "backend_pass_enc"), pa)
        XCTAssertNotEqual(db.string(forKey: "backend_pass_enc"), pb)
        let reopened = MedicalAccountPreferences(scope: .syntheticReview, defaults: db)
        XCTAssertEqual(reopened.user(), "fixture-b"); XCTAssertEqual(reopened.password(), pb)
        a.clearCredentials()
        XCTAssertTrue(a.user().isEmpty); XCTAssertTrue(a.password().isEmpty)
        XCTAssertEqual(reopened.password(), pb)
    }
    func testExistingEncryptedKeysRemainReadableWithoutMigration() throws {
        let d = defaults(.mmh), p = UUID().uuidString
        d.set("existing-fixture", forKey: "backend_user")
        d.set(try PhiCrypto.encrypt(p), forKey: "backend_pass_enc")
        d.set(["same-code"], forKey: "pending_withdrawals")
        let store = MedicalAccountPreferences(scope: .mmh, defaults: d)
        XCTAssertEqual(store.user(), "existing-fixture"); XCTAssertEqual(store.password(), p)
        XCTAssertEqual(store.pending(.withdrawal), ["same-code"])
    }
    func testCorruptedCiphertextCannotFallBackToAnotherStore() {
        let d = defaults(.syntheticReview)
        let store = MedicalAccountPreferences(scope: .syntheticReview, defaults: d)
        for invalid in ["broken-ciphertext", "enc1:broken-ciphertext"] {
            d.set(invalid, forKey: "backend_pass_enc")
            XCTAssertEqual(store.password(), "")
            XCTAssertEqual(store.user(), "")
        }
    }
    func testEncryptionFailureDoesNotChangeAccountOrInvalidateSession() {
        let d = defaults(.mmh)
        d.set("old-user", forKey: "backend_user"); d.set("old-ciphertext", forKey: "backend_pass_enc")
        var invalidations = 0
        let store = MedicalAccountPreferences(scope: .mmh, defaults: d,
            invalidate: { invalidations += 1 }, encrypt: { _ in nil })
        XCTAssertFalse(store.setCredentials(user: "new-user", password: UUID().uuidString))
        XCTAssertEqual(store.user(), "old-user")
        XCTAssertEqual(d.string(forKey: "backend_pass_enc"), "old-ciphertext")
        XCTAssertEqual(invalidations, 0)
    }
    func testSameConsentCodeRemainsInOtherScopeAndOtherQueue() {
        let a = MedicalAccountPreferences(scope: .mmh, defaults: defaults(.mmh))
        let b = MedicalAccountPreferences(scope: .syntheticReview, defaults: defaults(.syntheticReview))
        for s in [a,b] { s.add(["same-code"], to: .withdrawal); s.add(["same-code"], to: .restore) }
        a.remove("same-code", from: .withdrawal)
        XCTAssertTrue(a.pending(.withdrawal).isEmpty)
        XCTAssertEqual(a.pending(.restore), ["same-code"])
        XCTAssertEqual(b.pending(.withdrawal), ["same-code"])
        XCTAssertEqual(b.pending(.restore), ["same-code"])
    }
    func testConcurrentWritersDoNotLoseConsentCodes() {
        let d = defaults(.syntheticReview)
        let stores = (0..<4).map { _ in MedicalAccountPreferences(scope: .syntheticReview, defaults: d) }
        DispatchQueue.concurrentPerform(iterations: 100) { i in stores[i % 4].add(["fixture-\(i)"], to: .withdrawal) }
        XCTAssertEqual(stores[0].pending(.withdrawal).count, 100)
        XCTAssertTrue(stores[0].pending(.restore).isEmpty)
    }
}
