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


private actor RestoreEntryFixture: InstitutionAuthenticatedTransport {
    var status = 200
    var statuses: [Int] = []
    var requests: [URLRequest] = []
    var invalidated = false
    func setStatuses(_ values: [Int]) { statuses = values }
    func setStatus(_ value: Int) { status = value }
    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        guard !invalidated else { throw InstitutionEntryError.signInRequired }
        requests.append(request)
        let code = statuses.isEmpty ? status : statuses.removeFirst()
        let bytes = Data(#"{"build":{"service":"woundai-backend-mmhps20261007","revision":"fixture"}}"#.utf8)
        return (bytes, HTTPURLResponse(url: request.url!, statusCode: code, httpVersion: nil, headerFields: nil)!)
    }
    func invalidate() { invalidated = true }
}

@MainActor
private final class RestoreDriverFixture: InstitutionGoogleDriver {
    var hasPreviousSignIn = true
    var subject = "original-subject"
    var restoreCalls = 0
    var interactiveCalls = 0
    var signOutCalls = 0
    var offline = false
    var wrongAudience = false
    var beforeReturn: (() async -> Void)?
    func signOut() { signOutCalls += 1; hasPreviousSignIn = false }
    func authenticate(clientID: String, audience: String, presenting: UIViewController?) async throws -> InstitutionGoogleAccount {
        if presenting == nil { restoreCalls += 1 } else { interactiveCalls += 1 }
        if let beforeReturn { await beforeReturn() }
        if offline { throw URLError(.notConnectedToInternet) }
        let payload: [String: Any] = ["sub": subject, "aud": wrongAudience ? "wrong" : audience,
            "iss":"https://accounts.google.com", "exp":Date().timeIntervalSince1970 + 3600]
        let body = try JSONSerialization.data(withJSONObject: payload).base64EncodedString()
            .replacingOccurrences(of: "+",with:"-").replacingOccurrences(of:"/",with:"_").replacingOccurrences(of:"=",with:"")
        let raw = "fixture." + body + ".signature"
        let provider = InstitutionRefreshingTokenProvider(subject: subject, audience: audience) { raw }
        return InstitutionGoogleAccount(subject: subject, email: "fixture@example.invalid", provider: provider)
    }
}

@MainActor
private final class RestoreHarness {
    let driver = RestoreDriverFixture()
    var entry = RestoreEntryFixture()
    let registry = InstitutionTransportRegistry()
    let suite = "restore-fixture-" + UUID().uuidString
    let preferences: UserDefaults
    var seconds: TimeInterval = 10
    var factories = 0
    var delays: [UInt64] = []
    var onWait: (() async throws -> Void)?
    init() {
        preferences = UserDefaults(suiteName: suite)!
        preferences.set("original-subject", forKey: "mmh.google.rememberedSubject")
    }
    func session() -> InstitutionGoogleSession {
        InstitutionGoogleSession(driver: driver, preferences: preferences, registry: registry,
            clock: { [unowned self] in seconds },
            waitBeforeProbeRetry: { [unowned self] delay in
                delays.append(delay)
                try await onWait?()
            }, makeTransport: { [unowned self] _,_,_,_ in
                factories += 1; return entry
            })
    }
    func clear() { preferences.removePersistentDomain(forName: suite) }
}

extension InstitutionRestoreTests {
    func testBusyEntryRetriesOnlyHealthUsingSameGoogleAuthentication() async {
        let h = RestoreHarness(); defer { h.clear() }
        await h.entry.setStatuses([429, 503, 200])
        let s = h.session()
        h.onWait = {
            XCTAssertFalse(s.applicationLoginReady)
            XCTAssertTrue(s.busy)
            XCTAssertNil(h.registry.snapshot())
            XCTAssertTrue(s.message?.contains("不需重填帳密") == true)
        }
        await s.signIn(presenting: UIViewController())
        XCTAssertTrue(s.applicationLoginReady)
        XCTAssertEqual(h.driver.interactiveCalls, 1)
        XCTAssertEqual(h.factories, 1)
        XCTAssertEqual(h.delays, [2_000_000_000, 4_000_000_000])
        let requests = await h.entry.requests
        XCTAssertEqual(requests.count, 3)
        for request in requests {
            XCTAssertEqual(request.httpMethod, "GET")
            XCTAssertEqual(request.url?.path, "/api/health")
            XCTAssertNil(request.httpBody)
            XCTAssertNil(request.value(forHTTPHeaderField: "Authorization"))
        }
    }
    func testBusyEntryExhaustionKeepsCredentialsAndExplainsServiceFailure() async {
        let h = RestoreHarness(); defer { h.clear() }
        await h.entry.setStatus(429)
        XCTAssertTrue(AppSettings.setCredentials(user: "fixture-doctor", password: UUID().uuidString))
        let s = h.session(); await s.restoreIfNeeded()
        XCTAssertFalse(s.applicationLoginReady); XCTAssertFalse(s.busy)
        XCTAssertNil(h.registry.snapshot())
        XCTAssertEqual(AppSettings.backendUser(), "fixture-doctor")
        XCTAssertTrue(s.message?.contains("不是 WoundAI 帳密錯誤") == true)
        let requests = await h.entry.requests; XCTAssertEqual(requests.count, 3)
        XCTAssertEqual(h.driver.restoreCalls, 1)
    }
    func testDeniedOrMalformedEntryNeverRetriesOrPublishesReady() async {
        // 200 with the wrong service is covered by InstitutionEntryHealth tests.
        for code in [401, 403, 302, 500] {
            let h = RestoreHarness(); defer { h.clear() }
            await h.entry.setStatus(code)
            let s = h.session(); await s.restoreIfNeeded()
            XCTAssertFalse(s.applicationLoginReady); XCTAssertNil(h.registry.snapshot())
            XCTAssertTrue(h.delays.isEmpty)
            let requests = await h.entry.requests; XCTAssertEqual(requests.count, 1)
        }
    }
    func testCancellationDuringRetryDoesNotSendAnotherProbeOrPublishReady() async {
        let h = RestoreHarness(); defer { h.clear() }
        await h.entry.setStatus(503)
        h.onWait = { throw CancellationError() }
        let s = h.session(); await s.restoreIfNeeded()
        XCTAssertFalse(s.applicationLoginReady); XCTAssertFalse(s.busy)
        XCTAssertNil(h.registry.snapshot())
        let requests = await h.entry.requests; XCTAssertEqual(requests.count, 1)
        let invalidated = await h.entry.invalidated; XCTAssertTrue(invalidated)
    }
    func testSameSubjectRestoresThroughHealthWithoutClearingAppCredentials() async throws {
        let h = RestoreHarness(); defer { h.clear() }
        XCTAssertTrue(AppSettings.setCredentials(user: "fixture-doctor", password: UUID().uuidString))
        let oldPassword = AppSettings.backendPassword()
        let s = h.session(); await s.restoreIfNeeded()
        XCTAssertTrue(s.ready); XCTAssertEqual(h.driver.restoreCalls,1)
        XCTAssertEqual(h.driver.interactiveCalls,0); XCTAssertEqual(h.driver.signOutCalls,0)
        XCTAssertEqual(AppSettings.backendPassword(),oldPassword)
        XCTAssertEqual(AppSettings.backendUser(),"fixture-doctor")
        XCTAssertNotNil(h.registry.snapshot())
        let requests = await h.entry.requests
        XCTAssertEqual(requests.count,1); XCTAssertEqual(requests.first?.httpMethod,"GET")
        XCTAssertEqual(requests.first?.url?.path,"/api/health")
        XCTAssertNil(requests.first?.value(forHTTPHeaderField:"Authorization"))
        XCTAssertNil(requests.first?.httpBody, "Recovery must not send the WoundAI password")
    }
    func testAutomaticDifferentSubjectIsRejectedBeforeEntryAndPreservesOldAccount() async {
        let h = RestoreHarness(); defer { h.clear() }
        h.driver.subject = "other-subject"
        XCTAssertTrue(AppSettings.setCredentials(user: "fixture-doctor", password: UUID().uuidString))
        let s = h.session(); await s.restoreIfNeeded()
        XCTAssertFalse(s.ready); XCTAssertNil(s.email); XCTAssertNil(h.registry.snapshot())
        XCTAssertEqual(h.factories,0)
        XCTAssertEqual(AppSettings.backendUser(),"fixture-doctor")
        XCTAssertEqual(h.preferences.string(forKey:"mmh.google.rememberedSubject"),"original-subject")
    }
    func testFailedExplicitAccountSwitchPreservesOldCredentialsUntilEntryAccepted() async {
        let h = RestoreHarness(); defer { h.clear() }
        h.driver.subject = "other-subject"
        await h.entry.setStatus(403)
        XCTAssertTrue(AppSettings.setCredentials(user: "fixture-doctor", password: UUID().uuidString))
        let s = h.session(); await s.signIn(presenting:UIViewController())
        XCTAssertFalse(s.ready); XCTAssertEqual(AppSettings.backendUser(),"fixture-doctor")
        XCTAssertEqual(h.preferences.string(forKey:"mmh.google.rememberedSubject"),"original-subject")
        let invalidated = await h.entry.invalidated; XCTAssertTrue(invalidated)
        h.entry = RestoreEntryFixture()
        await s.signIn(presenting:UIViewController())
        XCTAssertTrue(s.ready); XCTAssertEqual(AppSettings.backendUser(),"")
        XCTAssertEqual(h.preferences.string(forKey:"mmh.google.rememberedSubject"),"other-subject")
    }
    func testOfflineRestoreCanRetryAfterCooldownWithoutPromptingOrSigningOut() async {
        let h = RestoreHarness(); defer { h.clear() }
        h.driver.offline = true
        let s = h.session(); await s.restoreIfNeeded()
        XCTAssertFalse(s.ready); XCTAssertFalse(s.busy)
        h.driver.offline = false; h.seconds = 30
        await s.restoreIfNeeded(); XCTAssertEqual(h.driver.restoreCalls,1)
        h.seconds = 75; await s.restoreIfNeeded()
        XCTAssertTrue(s.ready); XCTAssertEqual(h.driver.restoreCalls,2)
        XCTAssertEqual(h.driver.interactiveCalls,0); XCTAssertEqual(h.driver.signOutCalls,0)
    }
    func testReadySessionIsNotReplacedOnRepeatedSettingsVisits() async {
        let h = RestoreHarness(); defer { h.clear() }
        let s = h.session(); await s.restoreIfNeeded()
        for _ in 0..<10 { h.seconds += 120; await s.restoreIfNeeded() }
        XCTAssertTrue(s.ready); XCTAssertEqual(h.driver.restoreCalls,1); XCTAssertEqual(h.factories,1)
    }
    func testLogoutAfterSuccessfulRestoreCannotSilentlyReopenEitherGate() async {
        let h = RestoreHarness(); defer { h.clear() }
        let s = h.session(); await s.restoreIfNeeded(); XCTAssertTrue(s.ready)
        await s.signOut(); h.seconds += 120; await s.restoreIfNeeded()
        XCTAssertFalse(s.ready); XCTAssertNil(h.registry.snapshot())
        XCTAssertEqual(h.driver.restoreCalls,1); XCTAssertEqual(h.driver.signOutCalls,1)
        XCTAssertNil(h.preferences.string(forKey:"mmh.google.rememberedSubject"))
    }
    func testWrongAudienceFromSDKCannotInstallTransport() async {
        let h = RestoreHarness(); defer { h.clear() }; h.driver.wrongAudience = true
        let s = h.session(); await s.restoreIfNeeded()
        XCTAssertFalse(s.ready); XCTAssertEqual(h.factories,0); XCTAssertNil(h.registry.snapshot())
    }
    func testConcurrentStartupAndSettingsUseOneSDKRestoreAndOneProbe() async {
        let h = RestoreHarness(); defer { h.clear() }
        var release: CheckedContinuation<Void, Never>?
        h.driver.beforeReturn = { await withCheckedContinuation { release = $0 } }
        let s = h.session()
        let first = Task { await s.restoreIfNeeded() }
        for _ in 0..<100 { if release != nil { break }; await Task.yield() }
        XCTAssertNotNil(release)
        await s.restoreIfNeeded()
        XCTAssertEqual(h.driver.restoreCalls,1); XCTAssertTrue(s.busy)
        release?.resume(); await first.value
        XCTAssertTrue(s.ready); XCTAssertEqual(h.factories,1)
    }
    func testSameSubjectDeniedByIAPIsNotDisplayedAsLoggedInAndKeepsEncryptedAccount() async {
        let h = RestoreHarness(); defer { h.clear() }
        await h.entry.setStatus(403)
        XCTAssertTrue(AppSettings.setCredentials(user:"fixture-doctor",password:UUID().uuidString))
        let s = h.session(); await s.restoreIfNeeded()
        XCTAssertFalse(s.ready); XCTAssertFalse(s.busy); XCTAssertNil(s.email)
        XCTAssertNil(h.registry.snapshot()); XCTAssertEqual(AppSettings.backendUser(),"fixture-doctor")
        XCTAssertTrue(s.message?.contains("403") == true)
        let invalidated = await h.entry.invalidated; XCTAssertTrue(invalidated)
    }

}
#endif
