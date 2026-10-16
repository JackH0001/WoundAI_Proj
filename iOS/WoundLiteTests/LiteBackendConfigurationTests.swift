import XCTest
@testable import WoundLite

final class LiteBackendConfigurationTests: XCTestCase {
    let origin = LiteBackendConfiguration.dedicatedOrigin

    func testReleaseRequiresDedicatedOrigin() {
        XCTAssertEqual(LiteBackendConfiguration.releaseOrigin(origin), origin)
        XCTAssertEqual(LiteBackendConfiguration.releaseOrigin(origin + "/"), origin)
        for value in [nil, "", "$(WOUNDLITE_BACKEND_URL)",
                      "https://woundai-backend-421209514056.asia-east1.run.app",
                      "https://woundai-mmh-review-demo-421209514056.asia-east1.run.app",
                      "https://example.org"] {
            XCTAssertEqual(LiteBackendConfiguration.releaseOrigin(value), LiteBackendConfiguration.unavailableOrigin)
        }
    }
    func testCredentialsPathsPortsAndQueriesRejected() {
        let host = URL(string: origin)!.host!
        for value in ["http://" + host, "https://user@" + host, "https://user:password@" + host,
                      origin + "/api", origin + "?token=value", origin + "#fragment",
                      origin + ":443", origin + "/%2F", "https://" + host + ".example.org"] {
            XCTAssertEqual(LiteBackendConfiguration.releaseOrigin(value), LiteBackendConfiguration.unavailableOrigin, value)
        }
    }
    func testConsentRequiresBothVersionAndOrigin() {
        XCTAssertEqual(choice(true, version: "v2", saved: origin), true)
        XCTAssertEqual(choice(false, version: "v2", saved: origin), false)
        XCTAssertNil(choice(true, version: "v1", saved: origin))
        XCTAssertNil(choice(true, version: "v2", saved: nil))
        XCTAssertNil(choice(true, version: "v2", saved: "https://legacy.example.org"))
        XCTAssertNil(choice(nil, version: "v2", saved: origin))
    }
    func testUnconfiguredServiceCannotReuseConsent() {
        XCTAssertNil(LiteBackendConfiguration.consentChoice(true, savedVersion: "v2", requiredVersion: "v2",
                      savedOrigin: LiteBackendConfiguration.unavailableOrigin,
                      currentOrigin: LiteBackendConfiguration.unavailableOrigin))
    }
    func testOldRevisionCannotBeRetargeted() {
        XCTAssertFalse(LiteBackendConfiguration.sameService("https://legacy.example.org", origin))
        XCTAssertFalse(LiteBackendConfiguration.sameService(origin, "https://legacy.example.org"))
        XCTAssertTrue(LiteBackendConfiguration.sameService(origin + "/", origin))
        XCTAssertFalse(LiteBackendConfiguration.sameService(origin + "/api", origin))
    }
    private func choice(_ value: Bool?, version: String?, saved: String?) -> Bool? {
        LiteBackendConfiguration.consentChoice(value, savedVersion: version,
                    requiredVersion: "v2", savedOrigin: saved, currentOrigin: origin)
    }
}
