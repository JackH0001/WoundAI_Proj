import Foundation

/// A server observation, never a local entitlement or an authorization to spend.
/// Missing/invalid responses from an older service must remain unknown, not 5/5.
struct LiteCloudQuota: Codable, Equatable {
    let limit: Int
    let used: Int
    let remaining: Int
    let resetsAt: Date

    private enum CodingKeys: String, CodingKey {
        case limit, used, remaining, scope, operation
        case resetsAt = "resets_at"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        limit = try c.decode(Int.self, forKey: .limit)
        used = try c.decode(Int.self, forKey: .used)
        remaining = try c.decode(Int.self, forKey: .remaining)
        let date = try c.decode(String.self, forKey: .resetsAt)
        guard limit >= 0, used >= 0, remaining == max(0, limit - used),
              try c.decode(String.self, forKey: .scope) == "installation",
              try c.decode(String.self, forKey: .operation) == "segment",
              let reset = ISO8601DateFormatter().date(from: date) else {
            throw DecodingError.dataCorrupted(.init(codingPath: decoder.codingPath,
                                                    debugDescription: "Invalid Lite quota"))
        }
        resetsAt = reset
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(limit, forKey: .limit)
        try c.encode(used, forKey: .used)
        try c.encode(remaining, forKey: .remaining)
        try c.encode("installation", forKey: .scope)
        try c.encode("segment", forKey: .operation)
        try c.encode(ISO8601DateFormatter().string(from: resetsAt), forKey: .resetsAt)
    }

    static func parse(_ object: Any?) -> LiteCloudQuota? {
        guard let object, JSONSerialization.isValidJSONObject(object),
              let data = try? JSONSerialization.data(withJSONObject: object) else { return nil }
        return try? JSONDecoder().decode(Self.self, from: data)
    }

    func isCurrent(at date: Date) -> Bool { date < resetsAt }
}
