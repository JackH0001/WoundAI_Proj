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
            .task(id: session.preparingAccount ? nil : session.purchaseObservationAccount) {
                let account = session.preparingAccount ? nil : session.purchaseObservationAccount
                await session.purchases.observePurchases(accountToken: account?.token)
            }
            .onDisappear { session.purchases.stopObserving() }
    }
}

enum LiteOfferPlanStatus: Equatable {
    case checking, available(LitePersonalBalance), unavailable, expired

    /// The purchase tap may arrive just after the displayed plan expires. Refreshing
    /// only changes what is displayed. A separate tap accepts the refreshed offer.
    @MainActor func handlePurchaseTap(displayed: LitePersonalBalance, at date: Date,
                                      refresh: () -> Void, purchase: () async -> Void) async {
        guard self == .available(displayed), displayed.limit == 2, displayed.isFresh(at: date) else {
            refresh()
            return
        }
        await purchase()
    }

    /// One automatic recheck per visit, account change, transaction update or explicit retry.
    @MainActor static func observe(
        fetch: () async throws -> LitePersonalBalance,
        waitUntil: (Date) async throws -> Void = { deadline in
            try await Task.sleep(for: .seconds(max(0, deadline.timeIntervalSinceNow)))
        },
        publish: (Self) -> Void
    ) async {
        for _ in 0..<2 {
            guard !Task.isCancelled else { return }
            publish(.checking)
            do {
                let balance = try await fetch()
                try Task.checkCancellation()
                publish(.available(balance))
                try await waitUntil(balance.expiresAt)
            } catch {
                if !Task.isCancelled { publish(.unavailable) }
                return
            }
        }
        if !Task.isCancelled { publish(.expired) }
    }
}

/// The candidate injects a trusted service bridge. Reading the disclosures needs no service access.
struct LiteNativePurchaseView: View {
    @Environment(\.liteRearLiDARAvailable) private var canCapture
    @ObservedObject var store: LiteNativePurchaseStore
    @ObservedObject private var session: LiteNativeSession
    @ObservedObject private var identity: LiteAccountIdentityStore
    init(session: LiteNativeSession) {
        self.session = session
        self.identity = session.identity
        self.store = session.purchases
    }

    private struct PlanQuery: Equatable {
        let account: LiteNativeSession.PurchaseAccount?
        let confirmedUpdates: Int
        let retry: Int
    }
    @Environment(\.scenePhase) private var phase
    @Environment(\.locale) private var locale
    @State private var plan = LiteOfferPlanStatus.checking
    @State private var finishedInitialPriceCheck = false
    @State private var ownerPreparationNotice: String?
    @State private var recheckForPurchase: LiteNativeSession.PurchaseAccount?
    @State private var checked: PlanQuery?
    @State private var retry = 0
    private var query: PlanQuery {
        PlanQuery(account: phase == .active && !session.preparingAccount ? session.purchaseObservationAccount : nil,
                  confirmedUpdates: store.confirmedUpdates, retry: retry)
    }
    private var busy: Bool { identity.busy || session.preparingAccount || session.registering || store.busy }
    private func copy(_ source: String) -> String { LiteServiceCopy.text(source, locale: locale) }

    var body: some View {
        Form {
            Section(copy("每月自動續訂")) {
                Text(copy("Plus 每天共 10 次辨識，可同時追蹤 5 處傷口。"))
                if let offer = store.offer {
                    Text(verbatim: offer.name)
                        .font(.headline).accessibilityIdentifier("lite.offer.productTitle")
                    Text(LiteServiceCopy.format("%@／月", locale: locale, offer.displayPrice))
                        .font(.title2.weight(.semibold)).accessibilityIdentifier("lite.offer.price")
                    Text(copy("每月自動續訂，可在 Apple 訂閱設定取消。購買會綁定目前的服務帳號。")).font(.footnote)
                } else if store.loadingOffer || !finishedInitialPriceCheck {
                    ProgressView(copy("正在讀取 Apple 的商品與價格…"))
                } else {
                    Text(copy("目前沒有可顯示的 Apple 月費。請重新讀取價格。"))
                        .font(.footnote).accessibilityIdentifier("lite.offer.priceUnavailable")
                    Button(copy("重新讀取 Apple 商品")) { Task { await store.loadOffer() } }
                        .disabled(busy).accessibilityIdentifier("lite.offer.reloadPrice")
                }
                Text(copy(canCapture
                    ? "拍攝新的傷口紀錄需使用配備後置 LiDAR 的 iPhone 或 iPad。請先在量測頁確認這部裝置可拍攝，再決定是否訂閱。"
                    : "此裝置沒有可用的後置 LiDAR 相機。訂閱 Plus 後仍無法在這部裝置拍攝新紀錄。您仍可恢復或管理既有訂閱。"))
                    .font(.footnote).accessibilityIdentifier("lite.offer.captureRequirement")

                if let account = session.purchaseObservationAccount {
                    if recheckForPurchase == account {
                        Text(copy("請查看更新後的方案與月費，再決定是否訂閱。"))
                            .font(.footnote).accessibilityIdentifier("lite.offer.recheckForPurchase")
                    }
                    TimelineView(.periodic(from: .now, by: 15)) { clock in
                        if query.account == account {
                            currentPlan(for: account, at: clock.date)
                        }
                    }
                    if let progress = store.activity?.progress {
                        ProgressView(copy(progress)).accessibilityIdentifier("lite.offer.transactionProgress")
                    } else if !session.preparingAccount, let message = store.message(for: account) {
                        Text(copy(message)).font(.footnote).accessibilityIdentifier("nativePurchaseStatus")
                    }
                } else {
                    if LiteNativeBuild.showsOwnerReview && session.registration == .required {
                        Text(copy("本人內測首次登記：請將這部裝置的登記識別碼私下傳到自己的 Mac。收到登記時段通知後，再按 Apple 登入。"))
                            .font(.footnote).accessibilityIdentifier("lite.offer.ownerSetup")
                        if let keyID = session.preparedEnrollmentKeyID {
                            ShareLink(item: keyID) { Text(copy("分享裝置登記識別碼")) }
                                .accessibilityIdentifier("lite.offer.shareOwnerID")
                        } else {
                            Button(copy("準備本人裝置登記識別碼")) {
                                Task {
                                    await session.prepareOwnerEnrollment()
                                    ownerPreparationNotice = session.preparedEnrollmentKeyID == nil ? session.message : nil
                                }
                            }.disabled(busy).accessibilityIdentifier("lite.offer.prepareOwnerID")
                            if let ownerPreparationNotice {
                                Text(copy(ownerPreparationNotice)).font(.footnote)
                            }
                        }
                    }
                    if session.registration == .pending {
                        Text(copy("續接裝置登記並以 Apple 登入")).font(.subheadline)
                        LiteAppleSignInButton(identifier: "lite.offer.resumeRegistration") {
                            Task { await session.prepareForPurchase(.resumeRegistrationAndSignIn) }
                        }.disabled(busy)
                    } else {
                        LiteAppleSignInButton(identifier: "lite.offer.signIn") {
                            Task { await session.prepareForPurchase() }
                        }.disabled(busy)
                    }
                    if let activity = store.activity, activity == .restoring || activity == .recheckingEntitlements {
                        ProgressView(copy(activity.progress)).accessibilityIdentifier("lite.offer.transactionProgress")
                    } else if let hint = store.restoreSignInHint {
                        Text(copy(hint)).accessibilityIdentifier("lite.offer.restoreSignInAgain")
                    } else {
                        Text(session.registration == .pending && !session.preparingAccount ? copy(session.message) : session.presentationPurchasePreparation(locale: locale))
                            .accessibilityIdentifier("lite.offer.preparationStatus")
                    }
                    if session.registration == .confirmed, let account = try? session.accountView() {
                        NavigationLink(copy("核對未完成登入或帳號狀態")) { account }
                    }
                }
            }
            Section(copy("既有訂閱")) {
                let resumePending = session.registration == .pending
                let account = session.purchaseObservationAccount
                Button(copy(resumePending ? "續接裝置登記並恢復購買" : "恢復購買")) {
                    Task {
                        if let account { await session.restorePurchases(for: account) }
                        else { await session.prepareForPurchase(resumePending ? .resumeRegistrationAndRestore : .restore) }
                    }
                }.disabled(busy).accessibilityIdentifier("lite.offer.restore")
                if session.purchaseObservationAccount != nil {
                    Button(copy("核對未完成購買")) { Task { await store.reconcile() } }.disabled(busy)
                }
                LiteAppleSubscriptionLink()
            }
            DisclosureGroup(copy("方案內容")) {
                Text(copy("免費方案每日可成功辨識 2 次。Plus 每日共 10 次，包含當日已使用的次數。")).accessibilityIdentifier("lite.offer.quota")
                Text(copy("免費可同時追蹤 1 處傷口，Plus 每帳戶最多 5 處。每日辨識次數由各處傷口合計。結束追蹤可換另一處，舊時間軸仍可查看及匯出 PDF。"))
                Text(copy("額度每日在 UTC 0 時重算，台灣時間為上午 8 時。處理中的辨識會暫時保留名額。查看已保存的結果不扣次數。"))
                Text(copy("研究上傳由您另行決定。"))
            }.accessibilityIdentifier("lite.offer.details")
            Section(copy("訂閱管理與條款")) { LitePrivacyLink(); LiteTermsLink() }
            LiteNativeSponsorSection(session: session)
        }
        .navigationTitle(copy("Plus 方案與價格"))
        .task {
            await store.loadOffer()
            if !Task.isCancelled { finishedInitialPriceCheck = true }
        }
        .onChange(of: query.account) { _, _ in recheckForPurchase = nil }
        .task(id: query) {
            let requested = query
            guard let account = requested.account else { checked = nil; plan = .checking; return }
            await LiteOfferPlanStatus.observe(fetch: {
                guard query == requested, session.purchaseObservationAccount == account else { throw CancellationError() }
                return try await session.personalBalance()
            }, publish: { status in
                guard query == requested, session.purchaseObservationAccount == account else { return }
                checked = requested
                plan = status
            })
        }
    }

    @ViewBuilder private func currentPlan(for account: LiteNativeSession.PurchaseAccount, at date: Date) -> some View {
        switch checked == query ? plan : .checking {
        case .available(let balance) where balance.isFresh(at: date):
            Text(copy(balance.limit == 10 ? "目前方案：Plus" : "目前方案：免費"))
                .accessibilityIdentifier("lite.offer.currentPlan")
            Text(balance.availabilityMessage(locale: locale))
            if balance.limit == 2, let offer = store.offer {
                Button(LiteServiceCopy.format("以 %@／月訂閱", locale: locale, offer.displayPrice)) {
                    Task { @MainActor in
                        // A tap may arrive after the last screen update.
                        guard query.account == account else { return }
                        let current = checked == query ? plan : .checking
                        await current.handlePurchaseTap(displayed: balance, at: Date(), refresh: {
                            recheckForPurchase = account
                            if checked == query { retry += 1 }
                        }, purchase: {
                            guard query.account == account else { return }
                            recheckForPurchase = nil
                            await store.purchase(accepting: offer, for: account)
                        })
                    }
                }
                .buttonStyle(.borderedProminent).disabled(busy || store.loadingOffer)
                .accessibilityIdentifier("lite.offer.buy")
            }
        case .checking, .available:
            ProgressView(copy("正在讀取目前方案…"))
        case .unavailable, .expired:
            Text(copy(plan == .expired ? "更新方案即可查看最新額度。" : "暫時無法確認目前方案。"))
            Button(copy("重新查詢方案")) { retry += 1 }.disabled(busy)
        }
    }
}
