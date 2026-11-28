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

    func availabilityMessage(locale: Locale) -> String {
        if attempt_limit_reached { return LiteServiceCopy.text("今日辨識嘗試已達上限", locale: locale) }
        if remaining > 0 {
            return LiteServiceCopy.format("今天還可辨識 %@ 次", locale: locale, String(remaining))
        }
        if used < limit && pending > 0 {
            return LiteServiceCopy.text("剩餘名額正由處理中的辨識使用，完成後可再查看。", locale: locale)
        }
        return LiteServiceCopy.text("今天的辨識額度已用完。", locale: locale)
    }

    var waitingForReservedUses: Bool {
        remaining == 0 && used < limit && pending > 0 && !attempt_limit_reached
    }

    var canGainUsesWithPlus: Bool {
        limit == 2 && used >= limit && used < 10 && pending == 0 && remaining == 0 && !attempt_limit_reached
    }

    var expiresAt: Date {
        Date(timeIntervalSince1970: min(Double(resets_at), Double(as_of) + 300))
    }

    func isFresh(at date: Date) -> Bool {
        let now = date.timeIntervalSince1970
        return now >= Double(as_of) - 60 && now < expiresAt.timeIntervalSince1970
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

/// An account-level balance cannot identify a photo. Only a locally accepted
/// operation for the same session, installation and UTC day can appear here.
struct LitePendingCaptureScope: Equatable {
    let balance: LitePersonalBalance
    let account: LiteNativeSession.PurchaseAccount
    let context: LiteDeletionContext

    @MainActor func isCurrent(in session: LiteNativeSession) -> Bool {
        session.purchaseObservationAccount == account && context.origin == session.origin.absoluteString
            && context.account == balance.account_sha256 && balance.waitingForReservedUses
            && balance.isFresh(at: session.identity.now())
    }

    func contains(_ record: LiteServiceRecord) -> Bool {
        guard record.accountSHA256 == context.account, record.origin == context.origin,
              record.installation == context.installation, record.day == balance.as_of / 86400,
              ["run", "status"].contains(record.next), let reply = record.reply,
              let receipt = reply.receipt, receipt.operation == record.operation,
              receipt.day == record.day else { return false }
        return (reply.state == "ready" && receipt.outcome == "reserved")
            || (reply.state == "pending" && receipt.outcome == "running")
    }
}

/// The measurement tab reads the personal allowance only for its current signed-in session.
struct LiteMeasureAllowanceView: View {
    @Environment(\.locale) private var locale
    @Environment(\.scenePhase) private var phase
    @ObservedObject private var session: LiteNativeSession
    @ObservedObject private var identity: LiteAccountIdentityStore
    @ObservedObject private var purchases: LiteNativePurchaseStore
    let operation: String?
    let operationRevision: Int?

    @ObservedObject var measure: LiteMeasureVM
    @ObservedObject var store: LiteStore

    init(session: LiteNativeSession, measure: LiteMeasureVM, store: LiteStore) {
        self.measure = measure
        self.store = store
        let record = measure.serviceResult
        self.session = session
        self.identity = session.identity
        self.purchases = session.purchases
        operation = record?.operation
        operationRevision = record?.revision
    }

    private struct Query: Equatable {
        let account: LiteNativeSession.PurchaseAccount?
        let operation: String?, revision: Int?
        let purchases: Int, refresh: Int
    }
    @State private var refresh = 0
    @State private var checked: Query?
    @State private var balance: LitePersonalBalance?
    @State private var captureChanged = false
    private var query: Query {
        Query(account: phase == .active ? session.purchaseObservationAccount : nil,
              operation: operation, revision: operationRevision, purchases: purchases.confirmedUpdates, refresh: refresh)
    }
    private var copy: LiteResearchCopy { LiteResearchCopy(locale: locale) }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if phase != .active {
                EmptyView()
            } else if query.account == nil {
                NavigationLink(LiteCaptureCopy(locale: locale).text("個人服務登入")) { LiteNativeSessionView(session: session) }
            } else {
                Text(copy.text("今日個人辨識額度")).font(.subheadline.weight(.semibold))
                if captureChanged {
                    Text(LiteServiceCopy.text("目前無法開啟這筆待確認的拍攝，請查看最新額度與本機拍攝清單。", locale: locale))
                        .font(.footnote)
                }
                if checked != query {
                    ProgressView(copy.text("正在核對…"))
                } else if let balance, balance.isFresh(at: Date()) {
                    Text(balance.availabilityMessage(locale: locale))
                        .accessibilityIdentifier("lite.measure.allowance")
                    if balance.waitingForReservedUses, let account = query.account {
                        pendingCaptures(balance: balance, account: account)
                    }
                    if balance.canGainUsesWithPlus {
                        NavigationLink(LiteServiceCopy.text("Plus 方案與價格", locale: locale)) {
                            LiteNativePurchaseView(session: session)
                        }.accessibilityIdentifier("lite.measure.plus")
                    }
                    if balance.pending > 0 || balance.attempt_limit_reached || balance.remaining == 0 {
                        Text(copy.format("額度重置時間：%@", copy.date(Date(timeIntervalSince1970: Double(balance.resets_at)))))
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                    if balance.pending > 0 { refreshButton }
                } else {
                    Text(copy.text("今日額度尚未確認。")).font(.footnote)
                    refreshButton
                }
            }
        }
        .onChange(of: query.account) { _, _ in captureChanged = false }
        .task(id: query) {
            let request = query
            balance = nil
            checked = nil
            guard request.account != nil, request.account?.generation == identity.generation else { return }
            let result = try? await session.personalBalance()
            guard !Task.isCancelled, query == request else { return }
            checked = request
            balance = result
            guard let result else { return }
            // Expire on the UTC boundary or at the snapshot's freshness limit, without polling.
            do { try await Task.sleep(for: .seconds(max(0, result.expiresAt.timeIntervalSinceNow))) }
            catch { return }
            if !Task.isCancelled, query == request { balance = nil }
        }
    }

    private struct CaptureQuery: Equatable {
        let balance: LitePersonalBalance
        let account: LiteNativeSession.PurchaseAccount
        let drafts: [LiteCaptureDraftSummary]
    }
    private struct Match: Identifiable {
        let draft: LiteCaptureDraftSummary
        let operation: String
        var id: String { draft.id }
    }
    @State private var matchedQuery: CaptureQuery?
    @State private var matchedScope: LitePendingCaptureScope?
    @State private var matches: [Match] = []

    private func pendingCaptures(balance: LitePersonalBalance, account: LiteNativeSession.PurchaseAccount) -> some View {
        let request = CaptureQuery(balance: balance, account: account,
                                   drafts: measure.pendingCaptures.filter { $0.id != measure.draft?.id })
        let title = LiteServiceCopy.text("查看本機待確認的拍攝", locale: locale)
        return VStack(alignment: .leading) {
            if matchedQuery == request, let scope = matchedScope, scope.isCurrent(in: session) {
                if matches.count == 1, let item = matches.first {
                    Button(title) { open(item, scope: scope) }
                        .accessibilityIdentifier("lite.measure.resumePendingCapture")
                } else if matches.count > 1 {
                    Menu(title) {
                        ForEach(matches) { item in
                            Button(item.draft.createdAt.formatted(date: .numeric, time: .standard)) { open(item, scope: scope) }
                        }
                    }.accessibilityIdentifier("lite.measure.resumePendingCapture")
                }
            }
        }
        .disabled(measure.busy || store.needsReload || measure.hasUnstoredCapture)
        .task(id: request) {
            matchedQuery = nil
            guard !request.drafts.isEmpty, let context = try? await identity.savedResearchContext() else { return }
            let scope = LitePendingCaptureScope(balance: balance, account: account, context: context)
            guard scope.isCurrent(in: session) else { return }
            var found: [Match] = []
            for draft in request.drafts {
                guard !Task.isCancelled else { return }
                guard let id = try? LiteNativeSession.captureID(recordID: draft.id),
                      let record = try? await session.service.storedIfPresent(id), scope.contains(record) else { continue }
                found.append(Match(draft: draft, operation: record.operation))
            }
            guard !Task.isCancelled, scope.isCurrent(in: session) else { return }
            matches = found
            matchedScope = scope
            matchedQuery = request
        }
    }

    private func open(_ item: Match, scope: LitePendingCaptureScope) {
        Task {
            let opened = await measure.restoreDraft(item.id, in: store, pending: scope, operation: item.operation)
            captureChanged = !opened && query.account == scope.account
            if captureChanged { refresh += 1 }
        }
    }

    private var refreshButton: some View {
        Button(copy.text("核對今日額度")) { captureChanged = false; refresh += 1 }
            .accessibilityIdentifier("lite.measure.refreshAllowance")
    }
}

struct LitePersonalBalanceView: View {
    @Environment(\.locale) private var locale
    private var copy: LiteResearchCopy { LiteResearchCopy(locale: locale) }
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
            Section(copy.text("今日個人辨識額度")) {
                TimelineView(.periodic(from: .now, by: 15)) { clock in
                    if phase == .active, let value, verifiedGeneration == identity.generation, value.isFresh(at: clock.date) {
                        LabeledContent(copy.text("可用次數"), value: value.attempt_limit_reached ? copy.text("今日嘗試已達上限") : copy.format("%@ 次", String(value.remaining)))
                        LabeledContent(copy.text("每日方案總額"), value: copy.format("%@ 次", String(value.limit)))
                        LabeledContent(copy.text("已完成"), value: copy.format("%@ 次", String(value.used)))
                        if value.pending > 0 { LabeledContent(copy.text("處理中或已預留"), value: copy.format("%@ 次", String(value.pending))) }
                        Text(copy.format("核對於 %@；%@ 重置。", copy.date(Date(timeIntervalSince1970: Double(value.as_of)), includesDate: false), copy.date(Date(timeIntervalSince1970: Double(value.resets_at)))))
                            .font(.footnote).foregroundStyle(.secondary)
                    } else if value != nil {
                        Text(copy.text("請重新核對目前帳號的今日額度。"))
                    } else {
                        Text(copy.text(message))
                    }
                }
                Button(copy.text(busy ? "正在核對…" : "核對今日額度")) { Task { await refresh() } }
                    .disabled(busy || phase != .active).accessibilityIdentifier("personalQuotaCheck")
                Text(copy.text("個人服務與研究上傳分開計算。核對額度不傳照片，也不會預留或扣除辨識次數。"))
                    .font(.footnote).foregroundStyle(.secondary)
            }
        }
        .navigationTitle(copy.text("個人辨識額度"))
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
