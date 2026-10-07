import SwiftUI

@MainActor
final class LiteUsageState: ObservableObject {
    static let shared = LiteUsageState()
    @Published private var quota: LiteCloudQuota?
    private var server = ""
    private var anonId = ""

    func record(_ value: LiteCloudQuota?, server: String, anonId: String) {
        self.server = server
        self.anonId = anonId
        quota = value
    }

    func current(at date: Date) -> LiteCloudQuota? {
        // This in-memory snapshot belongs to a verified upload. Registered keys never
        // rotate automatically; reinstall/restart drops the snapshot, not the old binding.
        guard server == AppSettings.backendURL(), LiteAttestClient.matches(anonId, "[0-9a-f]{32}"),
              let quota, quota.isCurrent(at: date) else { return nil }
        return quota
    }
}

/// No local countdown or purchase button: only a valid server reply can supply
/// a balance. Old backends, app restart and UTC rollover all remain unknown.
struct LiteQuotaView: View {
    @ObservedObject private var usage = LiteUsageState.shared

    var body: some View {
        TimelineView(.periodic(from: .now, by: 60)) { context in
            VStack(alignment: .leading, spacing: 4) {
                if let quota = usage.current(at: context.date) {
                    Text("雲端辨識剩餘 \(quota.remaining)／\(quota.limit) 次")
                    Text("依最近一次服務回覆；\(quota.resetsAt.formatted(date: .abbreviated, time: .shortened)) 重置。")
                        .foregroundStyle(.secondary)
                } else {
                    Text("雲端辨識額度尚待服務確認")
                    Text("支援的服務會在辨識後回報剩餘次數；本機紀錄不受雲端額度限制。")
                        .foregroundStyle(.secondary)
                }
            }
            .font(.footnote)
            .accessibilityElement(children: .combine)
        }
    }
}
