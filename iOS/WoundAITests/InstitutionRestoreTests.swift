#if WOUND_INSTITUTION_TESTS
import XCTest
import GoogleSignIn
@testable import WoundAIInstitution

@MainActor
final class InstitutionRestoreTests: XCTestCase {
    override func tearDown() {
        AppSettings.clearCredentials()
        UserDefaults.standard.removeObject(forKey: "mmh.google.rememberedSubject")
        GIDSignIn.sharedInstance.signOut()
        super.tearDown()
    }
    func testNoGoogleCredentialDoesNotEraseEncryptedAppAccountOrPretendReady() async {
        GIDSignIn.sharedInstance.signOut()
        XCTAssertTrue(AppSettings.setCredentials(user: "fixture-doctor", password: UUID().uuidString))
        let session = InstitutionGoogleSession()
        await session.restoreIfNeeded()
        XCTAssertFalse(session.ready); XCTAssertFalse(session.busy)
        XCTAssertEqual(AppSettings.backendUser(), "fixture-doctor")
        XCTAssertFalse(AppSettings.backendPassword().isEmpty)
    }
    func testExplicitSignOutClearsBothGatesAndDisablesAutomaticRestore() async {
        XCTAssertTrue(AppSettings.setCredentials(user: "fixture-doctor", password: UUID().uuidString))
        UserDefaults.standard.set("fixture-subject", forKey: "mmh.google.rememberedSubject")
        let session = InstitutionGoogleSession()
        await session.signOut()
        await session.restoreIfNeeded()
        XCTAssertFalse(session.ready); XCTAssertNil(session.email)
        XCTAssertEqual(AppSettings.backendUser(), "")
        XCTAssertEqual(AppSettings.backendPassword(), "")
        XCTAssertNil(UserDefaults.standard.string(forKey: "mmh.google.rememberedSubject"))
        XCTAssertNil(InstitutionTransportRegistry.shared.snapshot())
    }
}

#endif
