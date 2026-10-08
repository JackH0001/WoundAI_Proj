import XCTest
@testable import WoundMeasurementApp

final class BackendConfigurationTests: XCTestCase {
    private let defaults = UserDefaults(suiteName: "woundai_settings")!
    private let keys = ["backend_base_url", "backend_user"]
    private var saved: [String: Any] = [:]

    override func setUp() {
        super.setUp()
        for key in keys {
            saved[key] = defaults.object(forKey: key)
            defaults.removeObject(forKey: key)
        }
    }

    override func tearDown() {
        for key in keys {
            if let value = saved[key] { defaults.set(value, forKey: key) }
            else { defaults.removeObject(forKey: key) }
        }
        saved.removeAll()
        super.tearDown()
    }

    func testNewInstallUsesDemoInRelease() {
        #if DEBUG && targetEnvironment(simulator)
        XCTAssertEqual(AppSettings.backendURL(), "http://localhost:5000")
        #else
        XCTAssertEqual(AppSettings.backendURL(), "https://woundai-backend-demo-z4kgfkob4a-de.a.run.app")
        #endif
    }

    func testSavedProductionDestinationIsNotMigrated() {
        defaults.set("existing-account", forKey: "backend_user")
        AppSettings.setBackendURL("https://woundai-backend-421209514056.asia-east1.run.app")
        XCTAssertEqual(AppSettings.backendURL(), "https://woundai-backend-421209514056.asia-east1.run.app")
    }

    func testCredentialsWithoutDestinationNeverFollowNewDemoDefault() {
        defaults.set("existing-account", forKey: "backend_user")
        #if DEBUG && targetEnvironment(simulator)
        XCTAssertEqual(AppSettings.backendURL(), "http://localhost:5000")
        #else
        XCTAssertEqual(AppSettings.backendURL(), "https://woundai-backend-421209514056.asia-east1.run.app")
        #endif
    }

    func testExplicitDemoSelectionIsPreservedWithCredentials() {
        defaults.set("demo01", forKey: "backend_user")
        AppSettings.setBackendURL("  https://woundai-backend-demo-z4kgfkob4a-de.a.run.app/  ")
        XCTAssertEqual(AppSettings.backendURL(), "https://woundai-backend-demo-z4kgfkob4a-de.a.run.app")
    }
}
