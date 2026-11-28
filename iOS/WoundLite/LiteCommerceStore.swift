#if DEBUG && targetEnvironment(simulator)
import Foundation
import StoreKit

/// Local Xcode StoreKit only. Does not grant any server quota or change research consent.
@MainActor final class LiteCommerceStore: ObservableObject {
    static let productID = "com.woundai.lite.plus.monthly"
    @Published private(set) var product: Product?
    @Published private(set) var expires: Date?
    @Published private(set) var message = "僅限 Xcode 本機交易；沒有正式扣款或雲端額度變更。"
    @Published private(set) var busy = false
    private var updates: Task<Void, Never>?
    private var refreshGeneration = 0
    private let currentExpiration: @MainActor () async -> Date?

    var plan: LiteLabPlan { (expires ?? .distantPast) > Date() ? .plus : .free }

    init(currentExpiration: (@MainActor () async -> Date?)? = nil) {
        self.currentExpiration = currentExpiration ?? Self.verifiedEntitlementExpiration
        updates = Task { [weak self] in
            for await result in Transaction.updates {
                guard !Task.isCancelled else { return }
                await self?.accept(result)
            }
        }
    }
    deinit { updates?.cancel() }

    func load() async {
        do {
            product = try await Product.products(for: [Self.productID]).first {
                $0.id == Self.productID && $0.type == .autoRenewable
            }
            if product == nil { message = "請用 WoundLiteCommerceLab scheme 啟動本機 StoreKit 設定。" }
            await refresh()
        } catch { message = "本機商品載入失敗，未建立訂閱。" }
    }

    private static func verifiedEntitlementExpiration() async -> Date? {
        var end: Date?
        for await result in Transaction.currentEntitlements {
            guard case .verified(let t) = result, eligible(t) else { continue }
            end = max(end ?? .distantPast, t.expirationDate ?? .distantPast)
        }
        return end
    }

    // An explicit sync can briefly yield no current entitlements. Confirm the latest
    // verified transaction against the subscription group's independently signed status.
    private static func subscribedExpiration() async throws -> Date? {
        guard case .verified(let latest)? = await Transaction.latest(for: productID),
              eligible(latest), let group = latest.subscriptionGroupID else { return nil }
        for status in try await Product.SubscriptionInfo.status(for: group) where status.state == .subscribed {
            guard case .verified(let transaction) = status.transaction,
                  case .verified(let renewal) = status.renewalInfo,
                  eligible(transaction), transaction.id == latest.id,
                  transaction.originalID == renewal.originalTransactionID,
                  renewal.currentProductID == productID, renewal.environment == .xcode else { continue }
            return transaction.expirationDate
        }
        return nil
    }

    private func refresh(whenEmpty confirm: () async throws -> Date?) async rethrows {
        refreshGeneration += 1
        let generation = refreshGeneration
        var end = await currentExpiration()
        guard generation == refreshGeneration else { return }
        if end == nil {
            do { end = try await confirm() }
            catch {
                if generation == refreshGeneration { expires = nil }
                throw error
            }
        }
        if generation == refreshGeneration { expires = end }
    }

    func refresh() async {
        await refresh(whenEmpty: { nil })
    }

    private static func eligible(_ t: Transaction) -> Bool {
        t.productID == productID && t.productType == .autoRenewable && t.environment == .xcode
            && t.revocationDate == nil && !t.isUpgraded && (t.expirationDate ?? .distantPast) > Date()
    }

    private func accept(_ result: VerificationResult<Transaction>) async {
        guard case .verified(let t) = result, t.environment == .xcode,
              t.productID == Self.productID, t.productType == .autoRenewable else {
            message = "交易未通過本機驗證，未授予測試權益。"
            return
        }
        if Self.eligible(t) {
            // The verified purchase result can arrive before currentEntitlements catches up.
            // Invalidate older in-flight reads instead of briefly showing a successful buyer as Free.
            refreshGeneration += 1
            expires = max(expires ?? .distantPast, t.expirationDate ?? .distantPast)
        } else {
            await refresh()
        }
        // Only the local test entitlement has been delivered. No production transaction is finished.
        await t.finish()
    }

    func purchase() async {
        guard !busy, let product else { return }
        busy = true; defer { busy = false }
        do {
            switch try await product.purchase() {
            case .success(let result):
                await accept(result)
                if plan == .plus { message = "本機測試訂閱已確認；每日共 10 次與免廣告僅套用下方合成預覽。" }
            case .pending: message = "測試購買等待核准，目前不增加額度。"
            case .userCancelled: message = "已取消測試購買。"
            @unknown default: message = "交易狀態尚未確認，未增加額度。"
            }
        } catch { message = "測試購買失敗，原紀錄與研究同意不變。" }
    }

    func restore() async {
        guard !busy else { return }
        busy = true; defer { busy = false }
        do {
            // Explicit button action only; never call sync on launch.
            try await AppStore.sync()
            try await refresh(whenEmpty: Self.subscribedExpiration)
            message = plan == .plus ? "已恢復本機測試權益。" : "沒有有效的本機測試訂閱。"
        } catch { message = "恢復購買未完成，請稍後再試。" }
    }
}
#endif
