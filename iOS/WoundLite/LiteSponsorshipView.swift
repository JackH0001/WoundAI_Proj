import SwiftUI
import StoreKit

/// Mounted only in the plans screen. Research/record/capture/PDF views do not
/// receive this component. The approved bundle currently contains no campaign.
struct LiteNativeSponsorSection: View {
    @ObservedObject var session: LiteNativeSession
    @ObservedObject private var identity: LiteAccountIdentityStore
    @ObservedObject private var purchases: LiteNativePurchaseStore
    @Environment(\.scenePhase) private var phase
    @StateObject private var model: LiteSponsorModel
    private struct Check: Equatable {
        let generation: Int
        let ready, busy, purchasing, active: Bool
        let purchaseMessage: String
    }
    private var check: Check { .init(generation: identity.generation, ready: session.registration == .confirmed,
        busy: identity.busy, purchasing: purchases.busy, active: phase == .active, purchaseMessage: purchases.message) }
    @State private var verified: Check?
    init(session: LiteNativeSession) {
        self.session = session; identity = session.identity; purchases = session.purchases
        _model = StateObject(wrappedValue: LiteSponsorModel(campaign: .bundled(), journal: LiteSponsorFileDismissal.standard) {
            guard session.registration == .confirmed, !session.identity.busy, !session.purchases.busy else { throw LiteSponsorError.unavailable }
            let generation = session.identity.generation
            let balance = try await session.personalBalance()
            // A just-purchased/restored entitlement also suppresses advertising
            // while the service association may still be catching up.
            let known = try await session.purchases.provider.knownPurchases()
            guard generation == session.identity.generation, !session.identity.busy, !session.purchases.busy,
                  let storefront = await Storefront.current else { throw LiteSponsorError.unavailable }
            return LiteSponsorEligibility(free: balance.limit == 2 && known.isEmpty,
                market: storefront.countryCode, checkedAt: Date(timeIntervalSince1970: Double(balance.as_of)))
        })
    }
    var body: some View {
        Group {
            if model.campaign != nil, verified == check { LiteSponsorCard(model: model) }
        }
        .task(id: check) {
            verified = nil; model.invalidate()
            let started = check
            guard started.ready, started.active, !started.busy, !started.purchasing else { return }
            await model.refresh()
            if !Task.isCancelled, started == check { verified = started }
        }
        .onDisappear { verified = nil; model.invalidate() }
    }
}

struct LiteSponsorCard: View {
    @ObservedObject var model: LiteSponsorModel
    @Environment(\.locale) private var locale
    @Environment(\.openURL) private var openURL
    @Environment(\.scenePhase) private var phase
    @State private var confirm = false
    @State private var failedOpen = false
    private var copy: LiteSponsorCopy { LiteSponsorCopy(locale: locale) }
    var body: some View {
        TimelineView(.periodic(from: .now, by: 1)) { clock in
            if phase == .active, model.visible(at: clock.date), let campaign = model.campaign, let creative = campaign.copy[copy.language] {
                Section {
                  VStack(alignment: .leading, spacing: 12) {
                    HStack {
                        Text(copy.text("廣告・品牌贊助")).font(.caption.bold()).accessibilityIdentifier("lite.sponsor.label")
                        Spacer()
                        Button(copy.text("隱藏這則廣告")) { model.dismiss() }.font(.caption)
                            .accessibilityIdentifier("lite.sponsor.hide")
                    }
                    Text(campaign.advertiser).font(.subheadline)
                    Text(creative.title).font(.headline)
                    Text(creative.body)
                    Text(copy.text("此為付費版位，不代表本 App 推薦其產品或療效。"))
                        .font(.footnote).foregroundStyle(.secondary)
                    Button(copy.text("前往品牌網站")) { confirm = true }.accessibilityIdentifier("lite.sponsor.open")
                    DisclosureGroup(copy.text("為何看到此廣告？")) {
                        Text(copy.text("只依免費方案、Apple 商店地區及活動期間顯示；文字依 App 語言呈現。不使用傷口、照片、深度、研究同意或跨 App 活動。"))
                    }
                    ShareLink(item: model.reportText ?? "", subject: Text(copy.text("回報廣告問題"))) {
                        Label(copy.text("回報廣告問題"), systemImage: "exclamationmark.bubble")
                    }.accessibilityIdentifier("lite.sponsor.report")
                    Text(copy.text("可將廣告編號及問題寄至 jack.hou@gmail.com。請勿附傷口照片或健康資料。"))
                        .font(.footnote).foregroundStyle(.secondary)
                    if failedOpen { Text(copy.text("無法開啟網站，您可繼續使用 App。")) }
                  }.frame(maxWidth: .infinity, alignment: .leading)
                    .buttonStyle(.borderless)
                }
                .alert(copy.text("即將離開 App"), isPresented: $confirm) {
                    Button(copy.text("繼續前往品牌網站")) {
                        guard phase == .active, let url = model.destination(at: Date()) else { return }
                        openURL(url) { accepted in failedOpen = !accepted }
                    }
                    Button(copy.text("取消"), role: .cancel) { }
                } message: {
                    Text(copy.text("網站由品牌經營，適用該網站的隱私政策。App 不附加您的帳號或健康資料；瀏覽器連線仍可能向網站提供 IP 與 Cookie。") + "\n\n" + campaign.landing)
                }
            }
        }
    }
}
