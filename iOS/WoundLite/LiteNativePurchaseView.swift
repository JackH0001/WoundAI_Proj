import SwiftUI

struct LiteAppleSubscriptionLink: View {
    @Environment(\.locale) private var locale
    private func copy(_ source: String) -> String { LiteServiceCopy.text(source, locale: locale) }
    var body: some View {
        Link(copy("管理 Apple 訂閱"), destination: URL(string: "https://apps.apple.com/account/subscriptions")!)
            .accessibilityIdentifier("lite.billing.manage")
    }
}

/// Mounted on the home screen, including when the offer has never been opened.
struct LitePurchaseObservation: View {
    @ObservedObject private var session: LiteNativeSession
    @ObservedObject private var identity: LiteAccountIdentityStore
    @Environment(\.scenePhase) private var scene

    init(session: LiteNativeSession) {
        self.session = session
        self.identity = session.identity
    }

    private struct AccountCheck: Equatable {
        let registered: Bool, busy: Bool, active: Bool
        let generation: Int
    }

    var body: some View {
        Color.clear
            .allowsHitTesting(false)
            .accessibilityHidden(true)
            .task(id: scene) {
                if scene == .active { await session.checkExistingRegistration() }
            }
            .task(id: AccountCheck(registered: session.registration == .confirmed, busy: identity.busy,
                                   active: scene != .background, generation: identity.generation)) {
                await session.trackPurchaseAccess(foreground: scene != .background)
            }
            .task(id: session.purchaseObservationAccount) {
                await session.purchases.observePurchases(accountToken: session.purchaseObservationAccount?.token)
            }
            .onDisappear { session.purchases.stopObserving() }
    }
}

/// The candidate injects a trusted service bridge. Reading the disclosures needs no service access.
struct LiteNativePurchaseView: View {
    @ObservedObject var store: LiteNativePurchaseStore
    @ObservedObject private var session: LiteNativeSession
    @ObservedObject private var identity: LiteAccountIdentityStore
    init(session: LiteNativeSession) {
        self.session = session
        self.identity = session.identity
        self.store = session.purchases
    }

    private var canManagePurchases: Bool {
        session.purchaseObservationAccount != nil && !identity.busy
    }
    @Environment(\.locale) private var locale
    private func copy(_ source: String) -> String { LiteServiceCopy.text(source, locale: locale) }
    var body: some View {
        Form {
            Section(copy("個人雲端辨識")) {
                Text(copy("免費方案每日可成功辨識 2 次。Plus 每日共 10 次，包含當日已使用的次數。")).accessibilityIdentifier("lite.offer.quota")
                Text(copy("拍攝新的傷口紀錄需使用配備後置 LiDAR 的 iPhone 或 iPad。請先在量測頁確認這部裝置可拍攝，再決定是否訂閱。"))
                    .font(.footnote).accessibilityIdentifier("lite.offer.captureRequirement")
                Text(copy("免費可同時追蹤 1 處傷口，Plus 每帳戶最多 5 處。每日辨識次數由各處傷口合計。結束追蹤可換另一處，舊時間軸仍可查看及匯出 PDF。"))
                Text(copy("額度每日在 UTC 0 時重算，台灣時間為上午 8 時。處理中的辨識會暫時保留名額。查看已保存的結果不扣次數。"))
                    .font(.footnote)
            }
            Section(copy("每月自動續訂")) {
                if store.loadingOffer {
                    ProgressView(copy("正在讀取 Apple 的商品與價格…"))
                } else if let offer = store.offer {
                    Text(offer.name).font(.headline)
                    Text(LiteServiceCopy.format("%@／月", locale: locale, offer.displayPrice))
                        .font(.title2.weight(.semibold))
                        .accessibilityIdentifier("lite.offer.price")
                    Text(copy("每月自動續訂，可在 Apple 訂閱設定取消。購買會綁定目前的服務帳號。"))
                        .font(.footnote)
                    if let account = session.purchaseObservationAccount, !identity.busy {
                        Button(LiteServiceCopy.format("以 %@／月訂閱", locale: locale, offer.displayPrice)) { Task { await store.purchase(accepting: offer, for: account) } }
                            .buttonStyle(.borderedProminent)
                            .disabled(session.preparingPurchase)
                            .accessibilityIdentifier("lite.offer.buy")
                    }
                } else {
                    Text(copy("尚未取得可購買的每月方案與價格。"))
                    Button(copy("重新讀取 Apple 商品")) { Task { await store.loadOffer() } }
                }
            }.disabled(store.busy)
            if !canManagePurchases {
                Section(copy("繼續訂閱或恢復購買")) {
                    if session.registration == .pending {
                        Button(copy("續接裝置登記")) { Task { await session.registerDevice(resumePending: true) } }
                            .disabled(session.registering || identity.busy || store.busy)
                            .accessibilityIdentifier("lite.offer.resumeRegistration")
                    } else {
                        Button(copy("登入並繼續訂閱 Plus")) { Task { await session.prepareForPurchase() } }
                            .buttonStyle(.borderedProminent)
                            .disabled(session.preparingPurchase || session.registering || identity.busy || store.busy)
                            .accessibilityIdentifier("lite.offer.signIn")
                    }
                    if session.preparingPurchase { ProgressView(copy("正在準備，請勿重複操作…")) }
                    Text(session.registration == .pending ? copy(session.message) : session.presentationPurchasePreparation(locale: locale))
                        .accessibilityIdentifier("lite.offer.preparationStatus")
                    Text(copy("已有訂閱請登入原服務帳號。此步不會付款；研究上傳由您另行決定。"))
                        .font(.footnote).accessibilityIdentifier("lite.offer.preview")
                    if session.registration == .confirmed, let account = try? session.accountView() {
                        NavigationLink(copy("核對未完成登入或帳號狀態")) { account }
                    }
                }
            }
            LiteNativeSponsorSection(session: session)
            Section(copy("訂閱管理與條款")) {
                LiteAppleSubscriptionLink()
                LitePrivacyLink()
                LiteTermsLink()
            }
            if let account = session.purchaseObservationAccount, !identity.busy {
                Section(copy("既有訂閱")) {
                    Button(copy("恢復購買")) { Task { await store.restore(for: account) } }
                    Button(copy("核對未完成購買")) { Task { await store.reconcile() } }
                }.disabled(store.busy)
                Section(copy("核對狀態")) {
                    if store.busy { ProgressView(copy("正在核對，請勿重複購買…")) }
                    Text(copy(store.message)).accessibilityIdentifier("nativePurchaseStatus")
                    Text(copy("研究上傳由您另行決定。")).font(.footnote)
                }
            }
        }
        .navigationTitle(copy("Plus 方案與價格"))
        .task { await store.loadOffer() }
    }
}
