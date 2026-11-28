#if DEBUG && targetEnvironment(simulator)
import XCTest
import StoreKit
import StoreKitTest
@testable import WoundLite

/// A previous simulated purchase may have left Ask to Buy or another override
/// active if its test process timed out before cleanup.
@MainActor func freshCommerceTestSession(url: URL) throws -> SKTestSession {
    let session = try SKTestSession(contentsOf: url)
    session.resetToDefaultState()
    session.disableDialogs = true
    session.clearTransactions()
    return session
}

final class LiteCommerceStoreTests: XCTestCase {
    @MainActor private func waitFor(_ plan: LiteLabPlan, store: LiteCommerceStore) async throws {
        for _ in 0..<40 {
            await store.refresh()
            if store.plan == plan { return }
            try await Task.sleep(for: .milliseconds(100))
        }
        XCTAssertEqual(store.plan, plan, store.message)
    }
    @MainActor func testRealStoreKitPurchaseRestoreAndRefund() async throws {
        executionTimeAllowance = 60
        let url = try XCTUnwrap(Bundle(for: Self.self).url(forResource: "WoundLiteCommerce", withExtension: "storekit"))
        let session = try freshCommerceTestSession(url: url)
        print("STOREKIT session initialized: purchase")
        defer { session.clearTransactions() }
        let store = LiteCommerceStore()
        await store.load()
        print("STOREKIT catalog loaded")
        let product = try XCTUnwrap(store.product)
        XCTAssertEqual(product.price, Decimal(string: "1.99"))
        XCTAssertEqual(product.subscription?.subscriptionPeriod.unit, .month)
        XCTAssertEqual(product.subscription?.subscriptionPeriod.value, 1)
        // StoreKit may deliver the prior entitlement briefly after clearing the test history.
        XCTAssertTrue(session.allTransactions().isEmpty)
        if store.plan != .free && session.allTransactions().isEmpty {
            try await AppStore.sync()
        }
        try await waitFor(.free, store: store)
        await store.purchase()
        XCTAssertEqual(store.plan, .plus, store.message)
        let restored = LiteCommerceStore()
        await restored.refresh()
        XCTAssertEqual(restored.plan, .plus)
        await restored.restore()
        XCTAssertEqual(restored.plan, .plus)
        let transaction = try XCTUnwrap(session.allTransactions().first)
        try session.refundTransaction(identifier: transaction.identifier)
        try await waitFor(.free, store: restored)
    }

    @MainActor func testEmptyEntitlementSnapshotAfterSyncChecksStoreKitStatusAndRefund() async throws {
        let url = try XCTUnwrap(Bundle(for: Self.self).url(forResource: "WoundLiteCommerce", withExtension: "storekit"))
        let session = try freshCommerceTestSession(url: url)
        defer { session.clearTransactions() }
        _ = try await session.buyProduct(identifier: LiteCommerceStore.productID)
        let transaction = try XCTUnwrap(session.allTransactions().first)
        guard case .verified(let original)? = await Transaction.latest(for: LiteCommerceStore.productID) else {
            return XCTFail("Expected a verified local purchase")
        }
        await original.finish()

        // Only the empty current-entitlements snapshot is injected. The sync, latest
        // transaction, signed group status, and refund all come from local StoreKit.
        var reads = 0
        let restored = LiteCommerceStore(currentExpiration: { reads += 1; return nil })
        await restored.restore()
        XCTAssertEqual(reads, 1)
        XCTAssertEqual(restored.plan, .plus)
        XCTAssertEqual(session.allTransactions().count, 1)

        try session.refundTransaction(identifier: transaction.identifier)
        try await waitForRevokedTestTransaction()
        await restored.restore()
        XCTAssertEqual(restored.plan, .free)
    }

    @MainActor private func waitForRevokedTestTransaction() async throws {
        let deadline = ContinuousClock.now + .seconds(4)
        while ContinuousClock.now < deadline {
            if case .verified(let t)? = await Transaction.latest(for: LiteCommerceStore.productID),
               t.revocationDate != nil { return }
            try await Task.sleep(for: .milliseconds(50))
        }
        XCTFail("StoreKit did not deliver the signed refund")
    }

    @MainActor func testExpiredSubscriptionDoesNotKeepPaidQuota() async throws {
        executionTimeAllowance = 60
        let url = try XCTUnwrap(Bundle(for: Self.self).url(forResource: "WoundLiteCommerce", withExtension: "storekit"))
        let session = try freshCommerceTestSession(url: url)
        print("STOREKIT session initialized: expiration")
        defer { session.clearTransactions() }
        _ = try await session.buyProduct(identifier: LiteCommerceStore.productID)
        print("STOREKIT test purchase completed")
        let store = LiteCommerceStore()
        await store.refresh()
        XCTAssertEqual(store.plan, .plus)
        let transaction = try XCTUnwrap(session.allTransactions().first)
        try session.disableAutoRenewForTransaction(identifier: transaction.identifier)
        try session.expireSubscription(productIdentifier: LiteCommerceStore.productID)
        try await waitFor(.free, store: store)
    }

    @MainActor func testPendingPurchaseDoesNotGrantUntilApproved() async throws {
        executionTimeAllowance = 60
        let url = try XCTUnwrap(Bundle(for: Self.self).url(forResource: "WoundLiteCommerce", withExtension: "storekit"))
        let session = try freshCommerceTestSession(url: url)
        session.askToBuyEnabled = true
        defer { session.askToBuyEnabled = false; session.clearTransactions() }
        let store = LiteCommerceStore()
        await store.load()
        await store.purchase()
        XCTAssertEqual(store.plan, .free)
        XCTAssertTrue(store.message.contains("等待核准"), store.message)
        let transaction = try XCTUnwrap(session.allTransactions().first)
        try session.approveAskToBuyTransaction(identifier: transaction.identifier)
        try await waitFor(.plus, store: store)
    }
}
#endif
