import SwiftUI

/// A server snapshot for one signed-in account, not the research installation quota.
struct LitePersonalBalance: Decodable, Equatable {
    static let path = "/api/v1/lite/service/balance"
    let v, as_of, resets_at, used, pending, limit, remaining: Int
    let account_sha256: String
    let attempt_limit_reached: Bool

    func validate(account: String, at date: Date) throws {
        let now = date.timeIntervalSince1970
        guard v == 1, LiteServiceBytes.hex(account), account_sha256 == account,
              (0...253402214399).contains(as_of), Double(as_of) <= now + 60,
              resets_at == (as_of / 86400 + 1) * 86400, now < Double(resets_at), now < Double(as_of) + 300,
              limit == 2 || limit == 10, used >= 0, (0...30).contains(pending),
              remaining == max(0, max(0, limit - used) - pending) else { throw LiteAccountFailure.invalid }
    }

    func isFresh(at date: Date) -> Bool {
        let now = date.timeIntervalSince1970
        return now >= Double(as_of) - 60 && now < min(Double(resets_at), Double(as_of) + 300)
    }

    static func fetch(context: LiteDeletionContext, transport: any LiteAuthenticatedTransport,
                      now: @escaping () -> Date) async throws -> Self {
        try context.validate()
        guard let origin = URL(string: context.origin), try await transport.installation() == context.installation
        else { throw LiteAccountFailure.conflict }
        let url = origin.appendingPathComponent(String(path.dropFirst()))
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(["anon_id":context.installation, "account_sha256":context.account])
        let (data, response) = try await transport.send(request, installation: context.installation)
        guard response.url == url, response.statusCode == 200, data.count <= 2048,
              try await transport.installation() == context.installation else { throw LiteAccountFailure.unavailable }
        let value = try JSONDecoder().decode(Self.self, from: data)
        try value.validate(account: context.account, at: now())
        return value
    }
}

struct LitePersonalBalanceView: View {
    let session: LiteNativeSession
    @ObservedObject private var identity: LiteAccountIdentityStore
    @Environment(\.scenePhase) private var phase
    @State private var value: LitePersonalBalance?
    @State private var verifiedGeneration: Int?
    @State private var busy = false
    @State private var message = "登入個人帳號後，可核對今天還能辨識幾次。"

    init(session: LiteNativeSession) {
        self.session = session
        self.identity = session.identity
    }

    var body: some View {
        Form {
            Section("今日個人辨識額度") {
                TimelineView(.periodic(from: .now, by: 15)) { clock in
                    if phase == .active, let value, verifiedGeneration == identity.generation, value.isFresh(at: clock.date) {
                        LabeledContent("可用次數", value: value.attempt_limit_reached ? "今日嘗試已達上限" : "\(value.remaining) 次")
                        LabeledContent("每日方案總額", value: "\(value.limit) 次")
                        LabeledContent("已完成", value: "\(value.used) 次")
                        if value.pending > 0 { LabeledContent("處理中或已預留", value: "\(value.pending) 次") }
                        Text("核對於 \(Date(timeIntervalSince1970: Double(value.as_of)).formatted(date: .omitted, time: .shortened))；\(Date(timeIntervalSince1970: Double(value.resets_at)).formatted(date: .abbreviated, time: .shortened)) 重置。")
                            .font(.footnote).foregroundStyle(.secondary)
                    } else if value != nil {
                        Text("請重新核對目前帳號的今日額度。")
                    } else {
                        Text(message)
                    }
                }
                Button(busy ? "正在核對…" : "核對今日額度") { Task { await refresh() } }
                    .disabled(busy || phase != .active).accessibilityIdentifier("personalQuotaCheck")
                Text("個人服務與研究上傳分開計算。核對額度不傳照片，也不會預留或扣除辨識次數。")
                    .font(.footnote).foregroundStyle(.secondary)
            }
        }
        .navigationTitle("個人辨識額度")
        .onChange(of: identity.generation) { _, _ in value = nil; verifiedGeneration = nil }
        .onChange(of: phase) { _, state in if state != .active { value = nil; verifiedGeneration = nil } }
    }

    @MainActor private func refresh() async {
        guard !busy else { return }
        value = nil
        busy = true
        defer { busy = false }
        let start = identity.generation
        do {
            let balance = try await session.personalBalance()
            guard phase == .active, identity.generation == start else { throw LiteAccountFailure.conflict }
            value = balance
            verifiedGeneration = start
        } catch {
            message = "今日額度尚未確認。請先核對個人服務登入，或稍後再試。"
        }
    }

}
