import Foundation
import StoreKit

/// Opt-in Sandbox adapter; not connected to the public UI or startup.
@MainActor final class LiteStoreKitPurchaseProvider: LitePurchaseProvider {
    private var transactions:[String:Transaction]=[:]
    private let environment:String
    init() { environment="Sandbox" }
    #if DEBUG && targetEnvironment(simulator)
    init(xcodeTest:Bool) { environment=xcodeTest ? "Xcode":"Sandbox" }
    #endif
    private func event(_ result:VerificationResult<Transaction>) throws -> LitePurchaseEvent {
        guard case .verified(let t)=result, t.productID == "com.woundai.lite.plus.monthly",
              t.productType == .autoRenewable, t.ownershipType == .purchased, !t.isUpgraded,
              let token=t.appAccountToken else { throw LitePurchaseFailure.invalid }
        let env=t.environment == .sandbox ? "Sandbox" : t.environment == .xcode ? "Xcode":"Production"
        guard env == environment else { throw LitePurchaseFailure.invalid }
        let e=LitePurchaseEvent(transaction:String(t.id),original:String(t.originalID),token:token.uuidString.lowercased(),
            product:t.productID,environment:env,signed:result.jwsRepresentation)
        transactions[e.transaction]=t;return e
    }
    func purchase(product:String,token:UUID) async throws -> LitePurchaseOutcome {
        guard product == "com.woundai.lite.plus.monthly",
              let item=try await Product.products(for:[product]).first(where:{$0.id==product && $0.type == .autoRenewable}) else {
            throw LitePurchaseFailure.unavailable
        }
        switch try await item.purchase(options:[.appAccountToken(token)]) {
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
