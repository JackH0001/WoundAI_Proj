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

    var plan: LiteLabPlan { (expires ?? .distantPast) > Date() ? .plus : .free }

    init() {
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

    func refresh() async {
        refreshGeneration += 1
        let generation = refreshGeneration
        var end: Date?
        for await result in Transaction.currentEntitlements {
            guard case .verified(let t) = result, Self.eligible(t) else { continue }
            end = max(end ?? .distantPast, t.expirationDate ?? .distantPast)
        }
        if generation == refreshGeneration { expires = end }
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
            await refresh()
            message = plan == .plus ? "已恢復本機測試權益。" : "沒有有效的本機測試訂閱。"
        } catch { message = "恢復購買未完成，請稍後再試。" }
    }
}
#endif
