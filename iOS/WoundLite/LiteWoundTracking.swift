import Foundation

struct LiteWoundPermit {
  let id: String
  let account: String
}

enum LiteWoundTrackingError: LocalizedError {
  case full(Int)
  case changed, ended, otherAccount, unavailable
  var errorDescription: String? {
    switch self {
    case .full(let count): return "目前方案同時可追蹤 \(count) 處。請先在紀錄頁結束一處追蹤，再新增傷口。"
    case .ended: return "這處傷口已結束追蹤。若要繼續，請到紀錄頁選「重新追蹤」。"
    case .changed: return "其他裝置已變更追蹤名單，請重新核對後再操作。"
    case .otherAccount: return "請使用原服務帳號管理這處傷口。"
    case .unavailable: return "目前無法核對追蹤名額，請稍後再試。原拍攝與時間軸已保留。"
    }
  }
}

struct LiteWoundRoster: Decodable {
  let schema: String
  let account_sha256: String
  let revision: Int
  let active: [String]
  let limit: Int
  let decision: String?

  static func wireID(_ id: String) throws -> String {
    guard !id.isEmpty else { throw LiteWoundTrackingError.unavailable }
    if let uuid = UUID(uuidString: id) {
      return uuid.uuidString.lowercased().replacingOccurrences(of: "-", with: "")
    }
    // Older local groups keep their exact IDs; never send arbitrary legacy strings to the server.
    return String(LiteServiceBytes.hash(Data(("legacy-wound/1|" + id).utf8)).prefix(32))
  }

  static func localID(_ hex: String) -> String? {
    guard LiteServiceBytes.hex(hex, 32) else { return nil }
    let bytes = Array(hex)
    let parts = [0..<8, 8..<12, 12..<16, 16..<20, 20..<32]
    return UUID(uuidString: parts.map { String(bytes[$0]) }.joined(separator: "-"))?.uuidString
  }

  /// Stable across a retry or restart and distinct from the RGB-D capture identifier.
  static func newID(for capture: String) throws -> String {
    let hash = LiteServiceBytes.hash(Data(("wound-slot/1|" + capture).utf8))
    guard let id = localID(String(hash.prefix(32))) else {
      throw LiteWoundTrackingError.unavailable
    }
    return id
  }

  func validate(account: String) throws {
    guard schema == "lite.wounds/1", LiteServiceBytes.hex(account), account_sha256 == account,
      (0..<(1 << 53)).contains(revision), [1, 5].contains(limit), active.count <= 5,
      Set(active).count == active.count, active.allSatisfy({ Self.localID($0) != nil }),
      revision != 0 || active.isEmpty
    else { throw LiteWoundTrackingError.unavailable }
  }

  var summary: String {
    let excess = max(0, active.count - limit)
    let current = "可續記 \(min(active.count, limit)) 處，方案上限 \(limit) 處。"
    return excess == 0 ? current : current + "另有 \(excess) 處保留在名單中。恢復 Plus 或結束前方傷口後可再續記。"
  }

  func remoteGroups(excluding local: [LiteWoundGroup]) -> [LiteWoundGroup] {
    let known = Set(local.compactMap { try? Self.wireID($0.id) })
    return active.compactMap { hex in
      guard let id = Self.localID(hex), !known.contains(hex) else { return nil }
      return LiteWoundGroup(
        id: id, name: "帳戶傷口", account: account_sha256)
    }
  }

  func selectableGroups(in local: [LiteWoundGroup]) -> [LiteWoundGroup] {
    let permitted = Set(active.prefix(limit))
    return (local + remoteGroups(excluding: local)).filter { group in
      if group.account == nil { return !group.ended }
      guard group.account == account_sha256, let wire = try? Self.wireID(group.id) else { return false }
      return permitted.contains(wire)
    }
  }
}

extension LiteNativeSession {
  private func woundRequest(
    _ context: LiteDeletionContext, action: String, wound: String? = nil,
    revision: Int? = nil
  ) async throws -> LiteWoundRoster {
    try context.validate()
    let start = identity.generation
    let url = origin.appendingPathComponent("api/v1/lite/service/wounds")
    var body: [String: Any] = [
      "anon_id": context.installation, "account_sha256": context.account, "action": action,
    ]
    if let wound, let revision {
      body["wound_id"] = wound
      body["revision"] = revision
    }
    var request = URLRequest(url: url)
    request.httpMethod = "POST"
    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
    request.httpBody = try JSONSerialization.data(withJSONObject: body)
    let (data, response) = try await woundTransport.send(
      request, installation: context.installation)
    guard response.url == url, data.count <= 2048, [200, 409].contains(response.statusCode),
      let roster = try? JSONDecoder().decode(LiteWoundRoster.self, from: data),
      try await identity.savedResearchContext() == context, start == identity.generation
    else {
      throw LiteWoundTrackingError.unavailable
    }
    try roster.validate(account: context.account)
    if response.statusCode == 409 {
      if roster.decision == "limit" { throw LiteWoundTrackingError.full(roster.limit) }
      throw LiteWoundTrackingError.changed
    }
    guard action == "status" ? roster.decision == nil : roster.decision == "confirmed" else {
      throw LiteWoundTrackingError.unavailable
    }
    return roster
  }

  func woundList() async throws -> LiteWoundRoster? {
    guard let context = try await trackingContext() else { return nil }
    return try await woundRequest(context, action: "status")
  }

  /// Previously bound groups cannot be silently reopened by a stale handset.
  func woundPermit(id: String, owner: String?, resume: Bool = false) async throws
    -> LiteWoundPermit?
  {
    guard let context = try await trackingContext() else {
      if owner != nil { throw LiteWoundTrackingError.otherAccount }
      return nil
    }
    guard owner == nil || owner == context.account else {
      throw LiteWoundTrackingError.otherAccount
    }
    let wire = try LiteWoundRoster.wireID(id)
    let status = try await woundRequest(context, action: "status")
    if status.active.contains(wire) {
      guard status.active.prefix(status.limit).contains(wire) else {
        throw LiteWoundTrackingError.full(status.limit)
      }
      return LiteWoundPermit(id: id, account: context.account)
    }
    if owner != nil, !resume { throw LiteWoundTrackingError.ended }
    let result = try await woundRequest(
      context, action: "open", wound: wire, revision: status.revision)
    guard result.active.prefix(result.limit).contains(wire) else {
      throw LiteWoundTrackingError.changed
    }
    return LiteWoundPermit(id: id, account: context.account)
  }

  func finishTracking(_ group: LiteWoundGroup) async throws -> String? {
    guard let context = try await trackingContext() else {
      if group.account != nil { throw LiteWoundTrackingError.otherAccount }
      return nil
    }
    guard group.account == nil || group.account == context.account else {
      throw LiteWoundTrackingError.otherAccount
    }
    let wire = try LiteWoundRoster.wireID(group.id)
    let status = try await woundRequest(context, action: "status")
    let result = try await woundRequest(
      context, action: "finish", wound: wire, revision: status.revision)
    guard !result.active.contains(wire) else { throw LiteWoundTrackingError.changed }
    return context.account
  }
}
