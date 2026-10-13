import Foundation

/// Fixed namespaces, never a user-supplied path. MMH deliberately keeps all legacy locations.
enum MedicalDataScope: String, CaseIterable {
    case mmh = "mmhps20261007"
    case syntheticReview = "synthetic-review-v1"

    func root(under support: URL) -> URL {
        self == .mmh ? support : support.appendingPathComponent("medical_environments/" + rawValue, isDirectory: true)
    }
    func database(under support: URL) -> URL { root(under: support).appendingPathComponent("wound_measurement_database.sqlite") }
    func images(under support: URL) -> URL { root(under: support).appendingPathComponent("wound_images", isDirectory: true) }
    func depthIndex(under support: URL) -> URL { root(under: support).appendingPathComponent("depth_index.json") }
    func outbox(under support: URL) -> URL { root(under: support).appendingPathComponent("clinical_research_outbox", isDirectory: true) }
    func exports(under support: URL) -> URL {
        root(under: support).appendingPathComponent(self == .mmh ? "MMHPS20261007/private_exports" : "private_exports", isDirectory: true)
    }
    var settingsSuite: String { self == .mmh ? "woundai_settings" : "woundai_settings." + rawValue }
}
