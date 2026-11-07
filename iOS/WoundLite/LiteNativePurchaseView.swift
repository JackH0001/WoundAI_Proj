import SwiftUI

struct LiteAppleSubscriptionLink: View {
    var body: some View {
        Link("管理 Apple 訂閱", destination: URL(string: "https://apps.apple.com/account/subscriptions")!)
            .accessibilityIdentifier("lite.billing.manage")
    }
}

/// The candidate injects a trusted service bridge. Reading the disclosures needs no service access.
struct LiteNativePurchaseView: View {
    @ObservedObject var store: LiteNativePurchaseStore
    @ObservedObject private var session: LiteNativeSession
    @ObservedObject private var identity: LiteAccountIdentityStore
    @Environment(\.scenePhase) private var scene
    @State private var authorizedGeneration: Int?

    init(session: LiteNativeSession) {
        self.session = session
        self.identity = session.identity
        self.store = session.purchases
    }

    private struct AccountCheck: Equatable {
        let registered: Bool, busy: Bool, active: Bool
        let generation: Int
    }

    private var canManagePurchases: Bool {
        session.registration == .confirmed && !identity.busy && authorizedGeneration == identity.generation
    }
    var body: some View {
        Form {
            Section("個人雲端辨識") {
                Text("免費方案每日可成功辨識 2 次。Plus 每日共 10 次，包含當日已使用的次數。").accessibilityIdentifier("lite.offer.quota")
                Text("拍攝新的傷口紀錄需使用配備後置 LiDAR 的 iPhone 或 iPad。請先在量測頁確認這部裝置可拍攝，再決定是否訂閱。")
                    .font(.footnote).accessibilityIdentifier("lite.offer.captureRequirement")
                Text("免費可同時追蹤 1 處傷口，Plus 每帳戶最多 5 處。每日辨識次數由各處傷口合計。結束追蹤可換另一處，舊時間軸仍可查看及匯出 PDF。")
                Text("額度每日在 UTC 0 時重算，台灣時間為上午 8 時。處理中的辨識會暫時保留名額。查看已保存的結果不扣次數。")
                    .font(.footnote)
            }
            Section("每月自動續訂") {
                if store.loadingOffer {
                    ProgressView("正在讀取 Apple 的商品與價格…")
                } else if let offer = store.offer {
                    Text(offer.name).font(.headline)
                    Text(verbatim: "\(offer.displayPrice)／月")
                        .font(.title2.weight(.semibold))
                        .accessibilityIdentifier("lite.offer.price")
                    Text("每月自動續訂，可在 Apple 訂閱設定取消。購買會綁定目前的服務帳號。")
                        .font(.footnote)
                    if canManagePurchases {
                        Button("以 \(offer.displayPrice)／月訂閱") { Task { await store.purchase(accepting: offer) } }
                            .buttonStyle(.borderedProminent)
                            .disabled(session.preparingPurchase)
                            .accessibilityIdentifier("lite.offer.buy")
                    }
                } else {
                    Text("尚未取得可購買的每月方案與價格。")
                    Button("重新讀取 Apple 商品") { Task { await store.loadOffer() } }
                }
            }.disabled(store.busy)
            if !canManagePurchases {
                Section("繼續訂閱或恢復購買") {
                    Button("登入並繼續訂閱 Plus") { Task { await session.prepareForPurchase() } }
                        .buttonStyle(.borderedProminent)
                        .disabled(session.preparingPurchase || session.registering || identity.busy || store.busy)
                        .accessibilityIdentifier("lite.offer.signIn")
                    if session.preparingPurchase { ProgressView("正在準備，請勿重複操作…") }
                    Text(session.purchasePreparationMessage)
                        .accessibilityIdentifier("lite.offer.preparationStatus")
                    Text("已有訂閱請登入原服務帳號。此步不會付款；研究上傳由您另行決定。")
                        .font(.footnote).accessibilityIdentifier("lite.offer.preview")
                    if session.registration == .confirmed, let account = try? session.accountView() {
                        NavigationLink("核對未完成登入或帳號狀態") { account }
                    }
                }
            }
            Section("訂閱管理與條款") {
                LiteAppleSubscriptionLink()
                LitePrivacyLink()
                LiteTermsLink()
            }
            if canManagePurchases {
                Section("既有訂閱") {
                    Button("恢復購買") { Task { await store.restore() } }
                    Button("核對未完成購買") { Task { await store.reconcile() } }
                }.disabled(store.busy)
                Section("核對狀態") {
                    if store.busy { ProgressView("正在核對，請勿重複購買…") }
                    Text(store.message).accessibilityIdentifier("nativePurchaseStatus")
                    Text("研究上傳由您另行決定。").font(.footnote)
                }
            }
        }
        .navigationTitle("Plus 方案與價格")
        .task(id: AccountCheck(registered: session.registration == .confirmed, busy: identity.busy,
                               active: scene == .active, generation: identity.generation)) {
            guard session.registration == .confirmed else { authorizedGeneration = nil; return }
            guard scene == .active, !identity.busy else { return }
            let generation = identity.generation
            let deadline = await identity.purchaseAccessDeadline()
            guard !Task.isCancelled, generation == identity.generation else { return }
            guard let deadline else { authorizedGeneration = nil; return }
            authorizedGeneration = generation
            do { try await Task.sleep(for: .seconds(max(0, deadline.timeIntervalSince(identity.now())))) }
            catch { return }
            if !Task.isCancelled { authorizedGeneration = nil }
        }
        .task { await store.loadOffer() }
        .task(id: canManagePurchases) {
            await store.observePurchases(canManagePurchases: canManagePurchases)
        }
    }
}
