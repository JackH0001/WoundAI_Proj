import Foundation

struct MedicalWoundCatalog: Decodable {
    struct Option: Decodable, Identifiable { let code: String; let label: String; var id: String { code } }
    struct Site: Decodable, Identifiable {
        let code: String; let label: String; let region: String
        let lateralities: [String]; let surfaces: [String]; let digits: [String]
        var id: String { code }
    }
    struct Etiology: Decodable, Identifiable {
        let code: String; let label: String; let instruments: [String]
        var id: String { code }
    }
    struct Instrument: Decodable, Identifiable {
        let code: String; let label: String; let automatic: String; let manual: String; let status: String
        var id: String { code }
    }
    let version: String
    let regions: [Option]; let lateralities: [Option]; let surfaces: [Option]
    let sites: [Site]; let etiologies: [Etiology]; let factors: [Option]; let instruments: [Instrument]
    static let shared = try! JSONDecoder().decode(Self.self, from: Data(MedicalWoundCatalogData.json.utf8))
    func label(_ code: String, in options: [Option]) -> String { options.first { $0.code == code }?.label ?? code }
    func guidance(etiology: String) -> [Instrument] {
        let codes = etiologies.first { $0.code == etiology }?.instruments ?? []
        return codes.compactMap { key in instruments.first { $0.code == key } }
    }
}

/// Codes only. Supplementary text stays local; old records keep nil, never inferred.
struct MedicalWoundProfile: Codable, Equatable {
    var schema_version = MedicalWoundCatalog.shared.version
    var site = ""; var side = ""; var surface = ""; var digit = "not_applicable"
    var etiology = ""; var factors: [String] = []
    var valid: Bool {
        let c = MedicalWoundCatalog.shared
        guard schema_version == c.version, let s = c.sites.first(where: { $0.code == site }),
              s.lateralities.contains(side), s.surfaces.contains(surface), s.digits.contains(digit),
              c.etiologies.contains(where: { $0.code == etiology }), Set(factors).count == factors.count,
              factors.allSatisfy({ f in c.factors.contains { $0.code == f } }) else { return false }
        return etiology != "diabetic_foot" || ["heel","forefoot","midfoot","hindfoot","toe","interdigital_foot","toe_tip"].contains(site)
    }
    var bodyLabel: String {
        let c = MedicalWoundCatalog.shared
        return [c.label(side, in: c.lateralities), c.sites.first { $0.code == site }?.label ?? "",
                surface == "not_applicable" ? "" : c.label(surface, in: c.surfaces),
                digit == "not_applicable" ? "" : (digit == "unknown" ? "指趾未判定" : "第\(digit)指／趾")].filter { !$0.isEmpty }.joined(separator: "・")
    }
    var typeLabel: String { MedicalWoundCatalog.shared.etiologies.first { $0.code == etiology }?.label ?? "" }
    func encoded() throws -> String {
        guard valid else { throw NSError(domain: "MedicalWoundProfile", code: 1, userInfo: [NSLocalizedDescriptionKey: "請完成部位、左右側、表面與傷口成因選單，並核對組合。"] ) }
        var copy = self; copy.factors.sort()
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
        return String(decoding: try encoder.encode(copy), as: UTF8.self)
    }
    static func decode(_ json: String?) -> Self? {
        guard let json, let obj = try? JSONSerialization.jsonObject(with: Data(json.utf8)) as? [String: Any],
              Set(obj.keys) == Set(["schema_version", "site", "side", "surface", "digit", "etiology", "factors"]),
              let p = try? JSONDecoder().decode(Self.self, from: Data(json.utf8)), p.valid else { return nil }
        return p
    }
}
