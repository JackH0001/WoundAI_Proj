import SwiftUI

/// The research service meters a registered installation. This receipt has
/// no authority to describe a personal account or a subscription.
struct LiteResearchQuotaReceipt: Equatable {
    let quota: LiteCloudQuota
    let scope: LiteCaptureResearchScope
    let owner: String

    func current(at date: Date, scope current: LiteCaptureResearchScope?, registeredOwner: String?) -> LiteCloudQuota? {
        guard scope == current, owner == registeredOwner, LiteAttestClient.matches(owner, "[0-9a-f]{32}"),
              quota.isCurrent(at: date) else { return nil }
        return quota
    }
}

@MainActor final class LiteUsageState: ObservableObject {
    static let shared = LiteUsageState()
    @Published private(set) var receipt: LiteResearchQuotaReceipt?

    func record(_ value: LiteCloudQuota?, server: String, anonId: String, epoch: String?) {
        guard let origin = try? LiteWithdrawalJournal.origin(server) else { receipt = nil; return }
        receipt = value.map { LiteResearchQuotaReceipt(quota: $0, scope: .init(origin: origin, epoch: epoch), owner: anonId) }
    }
}

/// Loading the existing registration reads local state only. This view cannot
/// enroll a replacement identity or contact either recognition service.
struct LiteResearchQuotaView: View {
    @ObservedObject private var usage = LiteUsageState.shared
    @Environment(\.scenePhase) private var phase
    @State private var owner: String?
    @State private var registrationScope: LiteCaptureResearchScope?

    var body: some View {
        TimelineView(.periodic(from: .now, by: 60)) { context in
            let scope = try? LiteCaptureResearchScope.capture(server: AppSettings.backendURL(), consent: LitePrefs.researchConsent == true)
            VStack(alignment: .leading, spacing: 4) {
                if phase == .active, registrationScope == scope, let quota = usage.receipt?.current(at: context.date, scope: scope, registeredOwner: owner) {
                    Text("研究辨識剩餘 \(quota.remaining)／\(quota.limit) 次")
                    Text("依最近一次研究服務回覆；\(quota.resetsAt.formatted(date: .abbreviated, time: .shortened)) 重置。")
                        .foregroundStyle(.secondary)
                } else {
                    Text("研究辨識額度尚待服務確認")
                    Text("研究服務會在辨識後回報本機研究登記的剩餘次數。")
                        .foregroundStyle(.secondary)
                }
            }
            .font(.footnote)
            .accessibilityElement(children: .combine)
        }
        .task(id: phase == .active ? usage.receipt : nil) {
            owner = nil
            registrationScope = nil
            guard phase == .active, let receipt = usage.receipt,
                  receipt.scope.permits(server: AppSettings.backendURL(), consent: LitePrefs.researchConsent == true),
                  let client = try? await LiteSignedCloudTransport(baseURL: receipt.scope.origin).resolve() else { return }
            let existing = try? await client.existingInstallation()
            guard !Task.isCancelled, usage.receipt == receipt else { return }
            registrationScope = receipt.scope
            owner = existing
        }
    }
}
