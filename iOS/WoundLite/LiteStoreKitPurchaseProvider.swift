import Foundation
import StoreKit

/// Opt-in Sandbox adapter; not connected to the public UI or startup.
@MainActor final class LiteStoreKitPurchaseProvider: LitePurchaseProvider {
    private var transactions:[String:Transaction]=[:]
    private var quotedProduct: (offer: LiteMonthlyOffer, item: Product)?
    private let environment:String
    init() { environment="Sandbox" }
    #if DEBUG && targetEnvironment(simulator)
    init(xcodeTest:Bool) { environment=xcodeTest ? "Xcode":"Sandbox" }
    #endif
    private func event(_ result:VerificationResult<Transaction>) throws -> LitePurchaseEvent {
        guard case .verified(let t)=result, t.productID == LiteMonthlyOffer.productID,
              t.productType == .autoRenewable, t.ownershipType == .purchased, !t.isUpgraded,
              let token=t.appAccountToken else { throw LitePurchaseFailure.invalid }
        let env=t.environment == .sandbox ? "Sandbox" : t.environment == .xcode ? "Xcode":"Production"
        guard env == environment else { throw LitePurchaseFailure.invalid }
        let e=LitePurchaseEvent(transaction:String(t.id),original:String(t.originalID),token:token.uuidString.lowercased(),
            product:t.productID,environment:env,signed:result.jwsRepresentation)
        transactions[e.transaction]=t;return e
    }
    func monthlyOffer() async throws -> LiteMonthlyOffer {
        quotedProduct = nil
        guard let storefront = await Storefront.current,
              let item = try await Product.products(for: [LiteMonthlyOffer.productID]).first,
              item.id == LiteMonthlyOffer.productID, item.type == .autoRenewable,
              let subscription = item.subscription,
              subscription.subscriptionPeriod.unit == .month, subscription.subscriptionPeriod.value == 1,
              subscription.introductoryOffer == nil, item.price > 0,
              !item.displayName.isEmpty, !item.displayPrice.isEmpty,
              await Storefront.current?.id == storefront.id else {
            throw LitePurchaseFailure.unavailable
        }
        let offer = LiteMonthlyOffer(name: item.displayName, displayPrice: item.displayPrice,
            price: item.price, currency: item.priceFormatStyle.currencyCode, storefront: storefront.id)
        quotedProduct = (offer, item)
        return offer
    }

    func purchase(offer: LiteMonthlyOffer, token: UUID) async throws -> LitePurchaseOutcome {
        guard await Storefront.current?.id == offer.storefront,
              let quote = quotedProduct, quote.offer == offer else {
            throw LitePurchaseFailure.offerChanged
        }
        switch try await quote.item.purchase(options:[.appAccountToken(token)]) {
        case .success(let r):return .completed(try event(r))
        case .pending:return .pending
        case .userCancelled:return .cancelled
        @unknown default:throw LitePurchaseFailure.unavailable
        }
    }
    func unfinished() async throws -> [LitePurchaseEvent] {
        var out:[LitePurchaseEvent]=[]
        for await r in Transaction.unfinished { out.append(try event(r)) }
        return out
    }
    func restore() async throws -> [LitePurchaseEvent] {
        try await AppStore.sync() // Only invoked by explicit restore button.
        return try await knownPurchases()
    }
    func knownPurchases() async throws -> [LitePurchaseEvent] {
        var out:[String:LitePurchaseEvent]=[:]
        for await r in Transaction.currentEntitlements { let e=try event(r);out[e.transaction]=e }
        for e in try await unfinished() { out[e.transaction]=e }
        return out.values.sorted{$0.transaction<$1.transaction}
    }
    func updates() -> AsyncStream<LitePurchaseUpdate> {
        AsyncStream { continuation in
            let task=Task { [weak self] in
                for await r in Transaction.updates {
                    guard !Task.isCancelled,let self else{break}
                    do { continuation.yield(.event(try self.event(r))) }
                    catch { continuation.yield(.invalid) }
                }
                continuation.finish()
            }
            continuation.onTermination={_ in task.cancel()}
        }
    }
    func finish(_ event:LitePurchaseEvent) async throws {
        guard let t=transactions[event.transaction],String(t.originalID)==event.original,
              t.appAccountToken?.uuidString.lowercased()==event.token else { throw LitePurchaseFailure.invalid }
        await t.finish()
    }
}
