import XCTest
@testable import WoundLite

private let purchaseToken="10000000-0000-4000-8000-000000000001"
private let purchaseProduct="com.woundai.lite.plus.monthly"
let nativeTestOffer = LiteMonthlyOffer(name: "WoundAILite Plus", displayPrice: "NT$60", price: 60,
    currency: "TWD", storefront: "143486")

@MainActor extension LiteNativePurchaseStore {
    func purchaseDisplayedTestOffer(file: StaticString = #filePath, line: UInt = #line) async {
        await loadOffer()
        guard let displayed = offer else {
            XCTFail("Test storefront must have a monthly offer", file: file, line: line)
            return
        }
        await purchase(accepting: displayed)
    }
}
private let purchaseNow=1_800_000_000
private func event(_ token:String=purchaseToken,signed:String="synthetic-jws",environment:String="Sandbox",transaction:String="123")->LitePurchaseEvent {
    LitePurchaseEvent(transaction:transaction,original:"100",token:token,product:purchaseProduct,environment:environment,signed:signed)
}
private actor PurchaseJournal:LiteServiceJournal {
    var rows=[String:Data](),writes=0,failAt=0
    var afterFirstWrite: (@Sendable () async -> Void)?
    func onFirstWrite(_ action: @escaping @Sendable () async -> Void) { afterFirstWrite = action }
    func load(_ id:String)->Data? {rows[id]}
    func replace(_ id:String,expected:Data?,with data:Data) async throws {
        writes+=1;if writes==failAt{throw LitePurchaseFailure.unavailable}
        guard rows[id]==expected else{throw LitePurchaseFailure.journalConflict};rows[id]=data
        if writes == 1 { await afterFirstWrite?() }
    }
    func fail(_ n:Int){failAt=n}
    func values()->[Data]{Array(rows.values)}
}
@MainActor private final class PurchaseService:LitePurchaseService {
    let origin=URL(string:"https://synthetic.invalid")!
    var owner="synthetic-device",token=purchaseToken,environment="Sandbox",calls=0,contexts=0,fail=false,wrongAck=false,expired=false
    var afterConfirm:(()->Void)?
    var generation = 0
    var issuedAt = purchaseNow
    var account = String(repeating:"a",count:64)
    func installation() async throws ->String{owner}
    var lookups = [String]()
    func context(operation:String, existingOnly: Bool = false) async throws ->LitePurchaseContext {
        if existingOnly { lookups.append(operation) }
        contexts+=1
        return LitePurchaseContext(v:1,operation:operation,installation:owner,account_sha256:account,
            app_account_token:token,registration_sha256:String(repeating:"b",count:64),scope_sha256:String(repeating:"c",count:64),
            product_id:purchaseProduct,environment:environment,created_at:issuedAt-(expired ? 900:0),expires_at:issuedAt+(expired ? 0:900))
    }
    func confirm(_ c:LitePurchaseContext,event:LitePurchaseEvent) async throws ->LitePurchaseAcknowledgment {
        calls+=1;if fail{throw LitePurchaseFailure.unavailable};afterConfirm?()
        return LitePurchaseAcknowledgment(v:1,state:"associated",account_sha256:c.account_sha256,
            app_account_token:wrongAck ? "other":c.app_account_token,transaction_id:event.transaction,
            original_transaction_id:event.original,product_id:event.product,environment:event.environment,
            association_sha256:String(repeating:"d",count:64))
    }
}
@MainActor private final class PurchaseProvider:LitePurchaseProvider {
    var outcome=LitePurchaseOutcome.completed(event()),pending=[LitePurchaseEvent](),restored=[LitePurchaseEvent]()
    var purchases=0,finishes=0,restoreCalls=0,requestedToken:UUID?,failFinish=false
    var beforePurchase:(()async throws->Void)?,beforeFinish:(()async throws->Void)?
    var continuation:AsyncStream<LitePurchaseUpdate>.Continuation?
    var catalog: LiteMonthlyOffer? = nativeTestOffer
    var lookups = 0, unfinishedReads = 0
    var beforeKnown: (() -> Void)?
    var duringKnownRead: (() async throws -> Void)?
    private(set) var knownReads = 0
    var afterPayment: (() -> Void)?
    var duringRestore: (() -> Void)?
    var duringUnfinished: (() async -> Void)?
    func knownPurchases() async throws -> [LitePurchaseEvent] {
        knownReads += 1
        beforeKnown?()
        try await duringKnownRead?()
        return pending + restored
    }
    func monthlyOffer() async throws -> LiteMonthlyOffer {
        lookups += 1
        guard let catalog else { throw LitePurchaseFailure.unavailable }
        return catalog
    }
    func purchase(offer: LiteMonthlyOffer, token: UUID, checkAccount: @MainActor () throws -> Void) async throws -> LitePurchaseOutcome {
        XCTAssertEqual(offer, catalog)
        try await beforePurchase?()
        try checkAccount()
        purchases+=1;requestedToken=token
        if case .completed(let e)=outcome{pending=[e]}
        afterPayment?()
        return outcome
    }
    func restore() async throws ->[LitePurchaseEvent]{restoreCalls+=1;duringRestore?();return restored}
    func unfinished() async throws ->[LitePurchaseEvent]{unfinishedReads += 1; await duringUnfinished?(); return pending}
    func updates()->AsyncStream<LitePurchaseUpdate>{AsyncStream{continuation=$0}}
    func finish(_ e:LitePurchaseEvent)async throws {
        try await beforeFinish?()
        if failFinish { throw LitePurchaseFailure.unavailable }
        finishes += 1
        pending.removeAll { $0.sameTransaction(as: e) }
    }
}
@MainActor final class LitePurchaseBridgeTests:XCTestCase {
    private func setup()->(PurchaseService,PurchaseProvider,PurchaseJournal,LiteNativePurchaseStore){
        let s=PurchaseService(),p=PurchaseProvider(),j=PurchaseJournal()
        return(s,p,j,LiteNativePurchaseStore(service:s,provider:p,journal:j,accountGeneration:{s.generation},now:{Date(timeIntervalSince1970:Double(purchaseNow))}))
    }

    func testProgressSeparatesPurchaseRestoreExplicitCheckAndBackgroundObservation() async {
        let (_, provider, _, store) = setup()
        var seen: [String?] = []
        provider.beforePurchase = { seen.append(store.activity?.progress) }
        provider.duringRestore = { seen.append(store.activity?.progress) }
        provider.duringUnfinished = {
            XCTAssertTrue(store.busy)
            seen.append(store.activity?.progress)
        }
        await store.purchaseDisplayedTestOffer()
        await store.restore()
        await store.reconcile()
        await store.reconcile(reportEmpty: false)
        XCTAssertEqual(seen, ["正在處理訂閱…", "正在恢復既有訂閱…", "正在查詢未完成的交易…", "正在查看既有訂閱，完成後即可操作。"])
        XCTAssertFalse(store.busy)
        XCTAssertNil(store.activity)
        XCTAssertEqual(provider.purchases, 1)
        XCTAssertEqual(provider.restoreCalls, 1)
    }

    func testOfferCanLoadWhileBackgroundCheckKeepsPaymentAndRestoreLocked() async {
        let (service, provider, journal, store) = setup()
        provider.duringUnfinished = {
            XCTAssertEqual(store.activity, .observingTransactions)
            await store.loadOffer()
            XCTAssertEqual(store.offer, nativeTestOffer)
            await store.purchase(accepting: nativeTestOffer)
            await store.restore()
            XCTAssertEqual(store.activity, .observingTransactions)
        }

        await store.observePurchases(accountToken: purchaseToken)
        store.stopObserving()
        XCTAssertNil(store.activity)
        XCTAssertEqual(provider.lookups, 1)
        XCTAssertEqual(provider.purchases, 0)
        XCTAssertEqual(provider.restoreCalls, 0)
        XCTAssertEqual(service.contexts, 0)
        let rows = await journal.values()
        XCTAssertTrue(rows.isEmpty)
    }

    func testKnownTransactionFoundDuringPurchaseSwitchesToCheckingWithoutNewPayment() async {
        let (_, provider, _, store) = setup()
        provider.pending = [event()]
        provider.beforeFinish = { XCTAssertEqual(store.activity?.progress, "正在查詢未完成的交易…") }
        await store.purchaseDisplayedTestOffer()
        XCTAssertEqual(provider.purchases, 0)
        XCTAssertEqual(provider.finishes, 1)
        XCTAssertNil(store.activity)
    }

    func testSecondTapWithUnfinishedTransactionReconcilesWithoutBuyingAgain() async {
        let (service, provider, _, store) = setup()
        service.fail = true
        await store.purchaseDisplayedTestOffer()
        XCTAssertEqual(provider.purchases, 1)
        XCTAssertFalse(provider.pending.isEmpty)
        service.fail = false
        await store.purchaseDisplayedTestOffer()
        XCTAssertEqual(provider.purchases, 1)
        XCTAssertEqual(provider.finishes, 1)
        XCTAssertEqual(service.calls, 2)
    }

    func testCachedFinishedReceiptCannotConfirmRestoreForDifferentServiceToken() async {
        let (service, provider, _, store) = setup()
        await store.purchaseDisplayedTestOffer()
        XCTAssertEqual(provider.finishes, 1)
        provider.restored = [event()]
        service.token = "20000000-0000-4000-8000-000000000001"
        await store.restore()
        XCTAssertTrue(store.message.contains("不符"), store.message)
        XCTAssertEqual(service.calls, 1)
        XCTAssertEqual(provider.finishes, 1)
    }

    func testCatalogLoadNeverStartsPaymentOrContactsTheService() async {
        let (service, provider, journal, store) = setup()
        await store.purchase(accepting: nativeTestOffer)
        XCTAssertEqual(provider.lookups, 0)
        await store.loadOffer()
        XCTAssertEqual(store.offer, nativeTestOffer)
        XCTAssertEqual(provider.lookups, 1)
        XCTAssertEqual(provider.purchases, 0)
        XCTAssertEqual(service.contexts, 0)
        let rows = await journal.values()
        XCTAssertTrue(rows.isEmpty)
    }

    func testAccountSwitchDuringEntitlementLookupCannotStartPaymentForNewAccount() async {
        let (service, provider, _, store) = setup()
        let secondToken = "20000000-0000-4000-8000-000000000001"
        provider.beforeKnown = {
            service.generation += 1
            service.token = secondToken
            provider.outcome = .completed(event(secondToken))
        }
        await store.purchaseDisplayedTestOffer()
        XCTAssertEqual(provider.purchases, 0)
        XCTAssertNil(provider.requestedToken)
        XCTAssertEqual(service.calls, 0)
        XCTAssertEqual(provider.finishes, 0)
    }

    func testSignOutDuringFinalStorefrontCheckCannotOpenPayment() async {
        let (service, provider, _, store) = setup()
        provider.beforePurchase = { service.generation += 1 }
        await store.purchaseDisplayedTestOffer()
        XCTAssertEqual(provider.purchases, 0)
        XCTAssertEqual(service.calls, 0)
        XCTAssertEqual(provider.finishes, 0)
    }

    func testNewAccountMustAcceptItsOwnOfferAfterASwitch() async {
        let (service, provider, _, store) = setup()
        await store.loadOffer()
        let previous = LiteNativeSession.PurchaseAccount(generation: 0, token: purchaseToken)
        service.generation = 1
        service.token = "20000000-0000-4000-8000-000000000001"
        provider.outcome = .completed(event(service.token))
        await store.purchase(accepting: nativeTestOffer, for: previous)
        XCTAssertEqual(provider.purchases, 0)
        XCTAssertEqual(service.contexts, 0)
        let current = LiteNativeSession.PurchaseAccount(generation: 1, token: service.token)
        await store.purchase(accepting: nativeTestOffer, for: current)
        XCTAssertEqual(provider.purchases, 1)
        XCTAssertEqual(provider.requestedToken?.uuidString.lowercased(), service.token)
        XCTAssertEqual(service.calls, 1)
        XCTAssertEqual(provider.finishes, 1)
    }

    func testDisplayedAccountTokenMustMatchBeforePurchaseOrRestore() async {
        let (service, provider, journal, store) = setup()
        let displayed = LiteNativeSession.PurchaseAccount(generation: service.generation,
            token: "20000000-0000-4000-8000-000000000001")
        await store.loadOffer()
        await store.purchase(accepting: nativeTestOffer, for: displayed)
        await store.restore(for: displayed)
        XCTAssertEqual(provider.purchases, 0)
        XCTAssertEqual(provider.restoreCalls, 0)
        XCTAssertEqual(service.calls, 0)
        let saved = await journal.values()
        XCTAssertTrue(saved.isEmpty)
    }

    func testChangeAfterPaymentLeavesTransactionForOriginalAccountToFinishOnce() async {
        let (service, provider, _, store) = setup()
        provider.afterPayment = { service.generation += 1 }
        await store.purchaseDisplayedTestOffer()
        XCTAssertEqual(provider.pending, [event()])
        XCTAssertEqual(service.calls, 0)
        XCTAssertEqual(provider.finishes, 0)
        await store.observePurchases(accountToken: "20000000-0000-4000-8000-000000000001")
        XCTAssertEqual(service.calls, 0)
        await store.observePurchases(accountToken: purchaseToken)
        await store.observePurchases(accountToken: purchaseToken)
        XCTAssertEqual(provider.purchases, 1)
        XCTAssertEqual(service.calls, 1)
        XCTAssertEqual(provider.finishes, 1)
        XCTAssertTrue(provider.pending.isEmpty)
        store.stopObserving()
    }

    func testAccountChangeDuringRestoreLeavesOriginalTransactionAvailable() async {
        let (service, provider, _, store) = setup()
        provider.restored = [event()]
        provider.duringRestore = { service.generation += 1 }
        await store.restore()
        XCTAssertTrue(store.message.contains("帳號已變更"))
        XCTAssertEqual(service.calls, 0)
        XCTAssertEqual(provider.finishes, 0)
        provider.duringRestore = nil
        await store.restore()
        XCTAssertEqual(provider.purchases, 0)
        XCTAssertEqual(service.calls, 1)
        XCTAssertEqual(provider.finishes, 1)
    }
    func testUnregisteredPreviewReadsOnlyCatalogEvenWithAnUnfinishedPurchase() async {
        let (service, provider, journal, store) = setup()
        provider.pending = [event()]
        await store.observePurchases(accountToken: nil)
        await store.loadOffer()
        await Task.yield()
        XCTAssertEqual(store.offer, nativeTestOffer)
        XCTAssertNil(provider.continuation)
        XCTAssertEqual(provider.unfinishedReads, 0)
        XCTAssertEqual(provider.purchases, 0)
        XCTAssertEqual(provider.restoreCalls, 0)
        XCTAssertEqual(service.contexts, 0)
        XCTAssertEqual(service.calls, 0)
        let saved = await journal.values()
        XCTAssertTrue(saved.isEmpty)
        provider.catalog = nil
        await store.observePurchases(accountToken: nil)
        await store.loadOffer()
        XCTAssertNil(store.offer)
        XCTAssertEqual(provider.lookups, 2)
        XCTAssertEqual(provider.unfinishedReads, 0)
        XCTAssertEqual(provider.pending.count, 1)
    }

    func testAutomaticCatchUpIgnoresAnotherAccountsTransactionBeforeAValidOne() async {
        let (service, provider, journal, store) = setup()
        let foreign = event("20000000-0000-4000-8000-000000000001", transaction: "124")
        provider.pending = [foreign]
        await store.observePurchases(accountToken: purchaseToken)
        let rows = await journal.values()
        XCTAssertTrue(rows.isEmpty)
        XCTAssertEqual(service.contexts, 0)
        XCTAssertEqual(provider.finishes, 0)
        provider.pending = [foreign, event()]
        await store.observePurchases(accountToken: purchaseToken)
        XCTAssertEqual(service.contexts, 1)
        XCTAssertEqual(service.calls, 1)
        XCTAssertEqual(provider.finishes, 1)
        XCTAssertEqual(provider.pending, [foreign])
        XCTAssertEqual(provider.purchases + provider.restoreCalls, 0)
        store.stopObserving()
    }

    func testStopAfterServerConfirmationRecoversReceiptWithoutConfirmingTwice() async throws {
        let (service, provider, journal, store) = setup()
        let approved = event()
        service.afterConfirm = { store.stopObserving() }
        store.startObserving(accountToken: purchaseToken)
        provider.pending = [approved]
        provider.continuation?.yield(.event(approved))
        var acknowledged = false
        for _ in 0..<100 {
            let rows = try await journal.values().map { try JSONDecoder().decode(LitePurchaseCheckpoint.self, from: $0) }
            acknowledged = rows.contains { $0.acknowledgment != nil && !$0.finished }
            if acknowledged { break }
            try await Task.sleep(for: .milliseconds(10))
        }
        XCTAssertTrue(acknowledged)
        XCTAssertEqual(service.calls, 1)
        XCTAssertEqual(provider.finishes, 0)
        service.afterConfirm = nil
        await store.observePurchases(accountToken: purchaseToken)
        XCTAssertEqual(service.calls, 1)
        XCTAssertEqual(provider.finishes, 1)
        store.stopObserving()
    }

    func testPriceAndStorefrontChangesRequireFreshUserActionBeforePurchase() async {
        for changed in [
            LiteMonthlyOffer(name: "Plus", displayPrice: "NT$90", price: 90, currency: "TWD", storefront: "143486"),
            LiteMonthlyOffer(name: nativeTestOffer.name, displayPrice: nativeTestOffer.displayPrice,
                price: nativeTestOffer.price, currency: nativeTestOffer.currency, storefront: "other-storefront")
        ] {
            let (service, provider, journal, store) = setup()
            await store.loadOffer()
            provider.catalog = changed
            await store.purchase(accepting: nativeTestOffer)
            XCTAssertEqual(store.offer, changed)
            XCTAssertTrue(store.message.contains("變更"))
            await store.purchase(accepting: nativeTestOffer)
            XCTAssertEqual(service.contexts, 0)
            XCTAssertEqual(provider.purchases, 0)
            let rows = await journal.values()
            XCTAssertTrue(rows.isEmpty)
            await store.purchase(accepting: changed)
            XCTAssertEqual(provider.purchases, 1)
            XCTAssertEqual(service.calls, 1)
        }
    }

    func testMissingPriceBlocksNewPurchaseButExplicitRestoreStillWorks() async {
        let (service, provider, _, store) = setup()
        await store.loadOffer()
        provider.catalog = nil
        await store.purchase(accepting: nativeTestOffer)
        XCTAssertNil(store.offer)
        XCTAssertEqual(service.contexts, 0)
        provider.restored = [event()]
        await store.restore()
        XCTAssertEqual(provider.restoreCalls, 1)
        XCTAssertEqual(service.calls, 1)
        XCTAssertEqual(provider.purchases, 0)
    }

    func testExistingEntitlementWithSameTokenIsReconciledWithoutOpeningPayment() async {
        let (service, provider, _, store) = setup()
        provider.restored = [event()]
        await store.purchaseDisplayedTestOffer()
        XCTAssertEqual(provider.restoreCalls, 0)
        XCTAssertEqual(provider.purchases, 0)
        XCTAssertEqual(provider.finishes, 1)
        XCTAssertEqual(service.calls, 1)
        await store.purchaseDisplayedTestOffer()
        XCTAssertEqual(service.calls, 1)
        XCTAssertEqual(provider.finishes, 1)
        XCTAssertEqual(provider.purchases, 0)
    }

    func testCachedReceiptCannotBeReusedByAnotherAccountHashWithSameToken() async {
        let (service, provider, journal, store) = setup()
        await store.purchaseDisplayedTestOffer()
        provider.restored = [event()]
        service.account = String(repeating: "e", count: 64)
        await store.restore()
        XCTAssertTrue(store.message.contains("不符"))
        let completed = await journal.values().compactMap { try? JSONDecoder().decode(LitePurchaseCheckpoint.self, from: $0) }.filter { $0.finished }
        XCTAssertEqual(completed.count, 1)
        XCTAssertEqual(completed.first?.context.account_sha256, String(repeating: "a", count: 64))
        XCTAssertEqual(provider.finishes, 1)
    }

    func testSecondTapWhileStoreKitIsStartingCannotOpenAnotherPurchase() async {
        let (service, provider, _, store) = setup()
        await store.loadOffer()
        provider.beforePurchase = { await store.purchase(accepting: nativeTestOffer) }
        await store.purchase(accepting: nativeTestOffer)
        XCTAssertEqual(provider.purchases, 1)
        XCTAssertEqual(service.calls, 1)
        XCTAssertEqual(service.contexts, 1)
    }
    func testMixedAccountRestoreBatchDoesNotFinishItsFirstValidEvent() async {
        let (service, provider, _, store) = setup()
        provider.restored = [event(), event("20000000-0000-4000-8000-000000000001")]
        await store.restore()
        XCTAssertEqual(service.calls, 0)
        XCTAssertEqual(provider.finishes, 0)
        XCTAssertEqual(provider.purchases, 0)
        XCTAssertTrue(store.message.contains("不符"))
    }

    func testReconcileAfterFinishFailureRechecksOriginalAccountWithExistingOperationOnly() async throws {
        let (service, provider, journal, store) = setup()
        provider.failFinish = true
        await store.purchaseDisplayedTestOffer()
        let rows = await journal.values()
        let acknowledged = try XCTUnwrap(rows.compactMap {
            try? JSONDecoder().decode(LitePurchaseCheckpoint.self, from: $0)
        }.first { $0.acknowledgment != nil })
        provider.failFinish = false
        service.token = "20000000-0000-4000-8000-000000000001"
        await store.reconcile()
        XCTAssertEqual(provider.finishes, 0)
        XCTAssertTrue(store.message.contains("不符"))
        service.token = purchaseToken
        await store.reconcile()
        XCTAssertEqual(service.lookups, [acknowledged.context.operation, acknowledged.context.operation])
        XCTAssertEqual(provider.finishes, 1)
        XCTAssertEqual(provider.purchases, 1)
        XCTAssertEqual(service.calls, 1)
    }
    func testPurchaseUsesServerTokenSavesContextAndAcknowledgmentBeforeFinish()async throws {
        let(s,p,j,m)=setup()
        p.beforePurchase={let count=await j.values().count;XCTAssertEqual(count,1)}
        p.beforeFinish={
            let records=try await j.values().map{try JSONDecoder().decode(LitePurchaseCheckpoint.self,from:$0)}
            XCTAssertEqual(records.filter{$0.acknowledgment != nil}.count,1)
        }
        await m.purchaseDisplayedTestOffer();XCTAssertEqual(p.requestedToken?.uuidString.lowercased(),purchaseToken)
        XCTAssertEqual(s.calls,1);XCTAssertEqual(p.finishes,1);XCTAssertTrue(m.message.contains("不會重設"))
    }
    func testContextJournalFailurePreventsStoreKitPurchase()async {
        let(s,p,j,m)=setup();await j.fail(1);await m.purchaseDisplayedTestOffer();XCTAssertEqual(p.purchases,0);XCTAssertEqual(s.calls,0)
    }
    func testEventJournalFailureDoesNotConfirmOrFinish()async {
        let(s,p,j,m)=setup();await j.fail(2);await m.purchaseDisplayedTestOffer();XCTAssertEqual(s.calls,0);XCTAssertEqual(p.finishes,0)
        await m.reconcile();XCTAssertEqual(s.calls,1);XCTAssertEqual(p.finishes,1)
    }
    func testLostServerReplyRetainsSameContextOnRestart()async {
        let(s,p,j,m)=setup();s.fail=true;await m.purchaseDisplayedTestOffer();XCTAssertEqual(p.finishes,0)
        s.fail=false;let restarted=LiteNativePurchaseStore(service:s,provider:p,journal:j,accountGeneration:{s.generation})
        await restarted.reconcile();XCTAssertEqual(s.contexts,1);XCTAssertEqual(p.purchases,1);XCTAssertEqual(p.finishes,1)
    }
    func testAckJournalFailureRetriesServerButNeverPurchasesAgain()async {
        let(s,p,j,m)=setup();await j.fail(3);await m.purchaseDisplayedTestOffer();XCTAssertEqual(p.finishes,0)
        await m.reconcile();XCTAssertEqual(s.calls,2);XCTAssertEqual(p.purchases,1);XCTAssertEqual(p.finishes,1)
    }
    func testFinishFailureRetriesFromDurableAckWithoutAnotherConfirm()async {
        let(s,p,_,m)=setup();p.failFinish=true;await m.purchaseDisplayedTestOffer();p.failFinish=false;await m.reconcile()
        XCTAssertEqual(s.calls,1);XCTAssertEqual(p.finishes,1)
    }
    func testRestoreSameTransactionWithFreshJWSKeepsAcknowledgment()async {
        let(s,p,_,m)=setup();await m.purchaseDisplayedTestOffer();p.restored=[event(signed:"new-signature-same-transaction")]
        await m.restore();XCTAssertEqual(s.calls,1);XCTAssertEqual(p.finishes,1);XCTAssertEqual(p.restoreCalls,1)
    }
    func testForeignTokenDoesNotConfirmOrFinish()async {
        let(s,p,_,m)=setup();p.outcome = .completed(event("20000000-0000-4000-8000-000000000001"))
        await m.purchaseDisplayedTestOffer();XCTAssertEqual(s.calls,0);XCTAssertEqual(p.finishes,0);XCTAssertTrue(m.message.contains("不符"))
    }
    func testWrongServerAcknowledgmentNeverFinishes()async {
        let(s,p,_,m)=setup();s.wrongAck=true;await m.purchaseDisplayedTestOffer();XCTAssertEqual(p.finishes,0)
    }
    func testChangedInstallationAfterReplyCannotFinish()async {
        let(s,p,_,m)=setup();s.afterConfirm={s.owner="changed"};await m.purchaseDisplayedTestOffer();XCTAssertEqual(p.finishes,0)
    }
    func testPendingDoesNotConfirmUntilUpdate()async throws {
        let(s,p,_,m)=setup();p.outcome = .pending;await m.purchaseDisplayedTestOffer();XCTAssertEqual(s.calls,0);XCTAssertEqual(p.finishes,0)
        m.startObserving(accountToken: purchaseToken);await Task.yield();p.continuation?.yield(.event(event()))
        for _ in 0..<20 where p.finishes==0{try await Task.sleep(for:.milliseconds(10))}
        XCTAssertEqual(s.calls,1);XCTAssertEqual(p.finishes,1)
    }
    func testCancelKeepsServiceAndRecordsUnchanged()async {
        let(s,p,_,m)=setup();p.outcome = .cancelled;await m.purchaseDisplayedTestOffer();XCTAssertEqual(s.calls,0);XCTAssertEqual(p.finishes,0)
        XCTAssertTrue(m.message.contains("已取消"))
    }
    func testExpiredContextNeverStartsPurchase()async {
        let(s,p,_,m)=setup();s.expired=true;await m.purchaseDisplayedTestOffer();XCTAssertEqual(p.purchases,0)
    }
    func testStorefrontDelayAtExpiryRequiresAnotherTapAndNewContext() async {
        let service = PurchaseService(), provider = PurchaseProvider(), journal = PurchaseJournal()
        var time = purchaseNow
        let store = LiteNativePurchaseStore(service: service, provider: provider, journal: journal,
            accountGeneration: { service.generation }, now: { Date(timeIntervalSince1970: Double(time)) })
        provider.beforePurchase = { time += 900 }
        await store.purchaseDisplayedTestOffer()
        XCTAssertEqual(provider.purchases, 0)
        XCTAssertEqual(service.calls, 0)
        XCTAssertTrue(store.message.contains("逾時"), store.message)
        let english = LiteServiceCopy.text(store.message, locale: Locale(identifier: "en"))
        XCTAssertTrue(english.contains("expired"), english)
        provider.beforePurchase = nil
        service.issuedAt = time
        await store.purchaseDisplayedTestOffer()
        XCTAssertEqual(service.contexts, 2)
        XCTAssertEqual(provider.purchases, 1)
        XCTAssertEqual(service.calls, 1)
        XCTAssertEqual(provider.finishes, 1)
    }

    func testPaymentStartedBeforeExpiryCanFinishAfterExpiry() async {
        let service = PurchaseService(), provider = PurchaseProvider(), journal = PurchaseJournal()
        var time = purchaseNow
        let store = LiteNativePurchaseStore(service: service, provider: provider, journal: journal,
            accountGeneration: { service.generation }, now: { Date(timeIntervalSince1970: Double(time)) })
        provider.beforePurchase = { time += 899 }
        provider.afterPayment = { time += 3600 }
        await store.purchaseDisplayedTestOffer()
        XCTAssertEqual(provider.purchases, 1)
        XCTAssertEqual(service.calls, 1)
        XCTAssertEqual(provider.finishes, 1)
    }
    func testPurchaseContextFailureDoesNotClaimAPendingAppleTransaction() async {
        let (service, provider, _, store) = setup()
        service.expired = true
        await store.purchaseDisplayedTestOffer()
        XCTAssertEqual(provider.purchases, 0)
        XCTAssertEqual(service.calls, 0)
        XCTAssertTrue(store.message.contains("尚未向 Apple 發起購買"), store.message)
    }

    func testRestoreContextFailureReportsThatAppleWasNotQueried() async {
        let (service, provider, _, store) = setup()
        service.expired = true
        await store.restore()
        XCTAssertEqual(provider.restoreCalls, 0)
        XCTAssertEqual(service.calls, 0)
        XCTAssertTrue(store.message.contains("尚未向 Apple 查詢既有訂閱"), store.message)
    }

    func testExplicitCheckWithNoUnfinishedTransactionsReportsAResult() async {
        let (service, provider, _, store) = setup()
        await store.reconcile()
        XCTAssertEqual(provider.unfinishedReads, 1)
        XCTAssertEqual(service.contexts, 0)
        XCTAssertTrue(store.message.contains("沒有待完成的交易"), store.message)
    }
    func testEmptyRestoreDoesNotRegisterOrResetQuota() async {
        let (service, provider, _, store) = setup()
        await store.restore()
        XCTAssertEqual(provider.restoreCalls, 1)
        XCTAssertEqual(provider.knownReads, 1)
        XCTAssertEqual(provider.purchases, 0)
        XCTAssertEqual(provider.finishes, 0)
        XCTAssertEqual(service.calls, 0)
        XCTAssertEqual(store.confirmedUpdates, 0)
        XCTAssertTrue(store.message.contains("管理 Apple 訂閱"), store.message)
    }

    func testRestoreObservesDelayedEntitlementWithoutStartingAnotherAppleAction() async {
        let (service, provider, _, store) = setup()
        await store.loadOffer()
        XCTAssertEqual(store.offer, nativeTestOffer)
        provider.duringKnownRead = {
            XCTAssertEqual(store.activity, .recheckingEntitlements)
            await store.restore()
            await store.purchase(accepting: nativeTestOffer)
            provider.pending = [event()]
        }
        await store.restore()
        XCTAssertNil(store.activity)
        XCTAssertEqual(provider.knownReads, 1)
        XCTAssertEqual(provider.restoreCalls, 1)
        XCTAssertEqual(provider.purchases, 0)
        XCTAssertEqual(provider.finishes, 1)
        XCTAssertEqual(service.calls, 1)
        XCTAssertEqual(store.confirmedUpdates, 1)
    }

    func testFirstNonemptyRestoreSkipsSecondReadEvenWhenAccountDoesNotMatch() async {
        let (service, provider, _, store) = setup()
        provider.restored = [event("20000000-0000-4000-8000-000000000001")]
        await store.restore()
        XCTAssertEqual(provider.knownReads, 0)
        XCTAssertEqual(provider.restoreCalls, 1)
        XCTAssertEqual(provider.purchases + provider.finishes + service.calls, 0)
        XCTAssertTrue(store.message.contains("不符"), store.message)
    }

    func testUnavailableEntitlementReadOffersRestoreAgainWithoutMarkingFree() async {
        let (service, provider, _, store) = setup()
        provider.duringKnownRead = { throw LitePurchaseFailure.unavailable }
        await store.restore()
        XCTAssertEqual(provider.knownReads, 1)
        XCTAssertEqual(provider.restoreCalls, 1)
        XCTAssertEqual(provider.purchases + provider.finishes + service.calls, 0)
        XCTAssertTrue(store.message.contains("未能確認 Apple"), store.message)
        XCTAssertFalse(store.busy)
    }

    func testRestoreDropsDelayedEntitlementWhenAccountOrInstallationChanges() async {
        for switchAccount in [true, false] {
            let (service, provider, _, store) = setup()
            provider.duringKnownRead = {
                provider.pending = [event()]
                if switchAccount { service.generation += 1 }
                else { service.owner = "other-installation" }
            }
            await store.restore()
            XCTAssertEqual(provider.knownReads, 1)
            XCTAssertEqual(provider.finishes + provider.purchases + service.calls, 0)
            XCTAssertTrue(store.message.contains(switchAccount ? "帳號已變更" : "不符"), store.message)
        }
    }

    func testRestoreDropsEntitlementIfAccountCheckExpiresDuringRead() async {
        let (service, provider, journal, _) = setup()
        var clock = purchaseNow
        let store = LiteNativePurchaseStore(service: service, provider: provider, journal: journal,
            accountGeneration: { service.generation }, now: { Date(timeIntervalSince1970: Double(clock)) })
        provider.duringKnownRead = { clock += 900; provider.pending = [event()] }
        await store.restore()
        XCTAssertEqual(provider.knownReads, 1)
        XCTAssertEqual(provider.finishes + provider.purchases + service.calls, 0)
        XCTAssertTrue(store.message.contains("逾時"), store.message)
    }

    func testRestoreWhenLocalAccessExpiresReportsHowToRetryWithoutBuyingAgain() async {
        let (service, provider, _, store) = setup()
        let original = LiteNativeSession.PurchaseAccount(generation: service.generation, token: purchaseToken)
        var visible: LiteNativeSession.PurchaseAccount? = original
        provider.restored = [event()]
        provider.duringRestore = { visible = nil }
        await store.restore(for: original, currentAccount: { visible })
        XCTAssertEqual(store.restoreSignInHint, "請重新登入原服務帳號，再按「恢復購買」。")
        XCTAssertEqual(provider.restoreCalls, 1)
        XCTAssertEqual(provider.purchases, 0)
        XCTAssertEqual(service.calls, 1)
        await store.reconcile(reportEmpty: false)
        XCTAssertNotNil(store.restoreSignInHint)

        visible = original
        provider.duringRestore = nil
        await store.restore(for: original, currentAccount: { visible })
        XCTAssertNil(store.restoreSignInHint)
        XCTAssertTrue(store.message(for: original)?.contains("關聯已確認") == true)
        XCTAssertEqual(provider.restoreCalls, 2)
        XCTAssertEqual(provider.finishes, 1)
        XCTAssertEqual(service.calls, 1)
    }

    func testCompletedRestoreStatusIsVisibleOnlyToOriginalAccountAndGeneration() async {
        let (service, provider, _, store) = setup()
        let original = LiteNativeSession.PurchaseAccount(generation: service.generation, token: purchaseToken)
        provider.restored = [event()]
        await store.restore(for: original, currentAccount: { original })
        XCTAssertTrue(store.message(for: original)?.contains("關聯已確認") == true)
        XCTAssertNil(store.message(for: .init(generation: original.generation, token: "other-account")))
        XCTAssertNil(store.message(for: .init(generation: original.generation + 1, token: original.token)))
        XCTAssertNil(store.restoreSignInHint)
    }

    func testAccountChangeWhilePresentingRestoreNeverExposesPreviousSuccess() async {
        let (service, provider, _, store) = setup()
        let original = LiteNativeSession.PurchaseAccount(generation: service.generation, token: purchaseToken)
        provider.restored = [event()]
        await store.restore(for: original, currentAccount: {
            service.generation += 1
            return original
        })
        let next = LiteNativeSession.PurchaseAccount(generation: service.generation, token: "next-account")
        XCTAssertEqual(store.message(for: next), "請重新登入原服務帳號，再按「恢復購買」。")
        await store.reconcile()
        XCTAssertNil(store.restoreSignInHint)
        XCTAssertTrue(store.message(for: next)?.contains("沒有待完成的交易") == true)
    }

    func testCancellationAfterSyncDoesNotStartAnotherStoreKitRead() async {
        let (service, provider, _, store) = setup()
        provider.duringRestore = { withUnsafeCurrentTask { $0?.cancel() } }
        let task = Task { await store.restore() }
        await task.value
        XCTAssertEqual(provider.restoreCalls, 1)
        XCTAssertEqual(provider.knownReads, 0)
        XCTAssertEqual(provider.finishes + provider.purchases + service.calls, 0)
        XCTAssertFalse(store.busy)
    }
    func testDuplicateConcurrentUpdatesFinishOnlyOnce()async throws {
        let(s,p,_,m)=setup();m.startObserving(accountToken: purchaseToken);await Task.yield()
        p.continuation?.yield(.event(event()));p.continuation?.yield(.event(event()))
        for _ in 0..<20 where p.finishes==0{try await Task.sleep(for:.milliseconds(10))}
        XCTAssertEqual(s.calls,1);XCTAssertEqual(p.finishes,1)
    }
    func testMalformedTransactionCannotUseKnownAck()async {
        let(s,p,_,m)=setup();await m.purchaseDisplayedTestOffer();p.pending=[event("20000000-0000-4000-8000-000000000001")]
        await m.reconcile();XCTAssertEqual(s.calls,1);XCTAssertEqual(p.finishes,1);XCTAssertTrue(m.message.contains("不符"))
    }
    func testProductionEventCannotCrossSandboxContext()async {
        let(s,p,_,m)=setup();p.outcome = .completed(event(environment:"Production"));await m.purchaseDisplayedTestOffer()
        XCTAssertEqual(s.calls,0);XCTAssertEqual(p.finishes,0)
    }
}

#if DEBUG && targetEnvironment(simulator)
import StoreKit
import StoreKitTest
@MainActor final class LiteNativeStoreKitTests:XCTestCase {
    func testStoreKitCreatesNoTransactionWhenAccountCheckRejectsPayment() async throws {
        let config = try XCTUnwrap(Bundle(for: Self.self).url(forResource: "WoundLiteCommerce", withExtension: "storekit"))
        let localStore = try freshCommerceTestSession(url: config)
        defer { localStore.clearTransactions() }
        let provider = LiteStoreKitPurchaseProvider(xcodeTest: true)
        let offer = try await provider.monthlyOffer()
        let token = try XCTUnwrap(UUID(uuidString: purchaseToken))
        var checked = 0
        do {
            _ = try await provider.purchase(offer: offer, token: token) {
                checked += 1
                throw LitePurchaseFailure.accountChanged
            }
            XCTFail("A stale account started an Apple purchase")
        } catch LitePurchaseFailure.accountChanged {}
        XCTAssertEqual(checked, 1)
        XCTAssertTrue(localStore.allTransactions().isEmpty)
    }
    func testLocalStoreKitDoesNotStartPaymentIfSavedContextHasExpired() async throws {
        let config = try XCTUnwrap(Bundle(for: Self.self).url(forResource: "WoundLiteCommerce", withExtension: "storekit"))
        let session = try freshCommerceTestSession(url: config)
        defer { session.clearTransactions() }
        let service = PurchaseService(), journal = PurchaseJournal()
        service.environment = "Xcode"
        let provider = LiteStoreKitPurchaseProvider(xcodeTest: true)
        let store = LiteNativePurchaseStore(service: service, provider: provider, journal: journal,
            accountGeneration: { service.generation }, now: { Date(timeIntervalSince1970: Double(service.issuedAt)) })
        await journal.onFirstWrite { await MainActor.run { service.issuedAt += 900 } }
        store.startObserving(accountToken: purchaseToken)
        defer { store.stopObserving() }
        await store.purchaseDisplayedTestOffer()
        XCTAssertTrue(session.allTransactions().isEmpty)
        XCTAssertEqual(service.calls, 0)
        XCTAssertTrue(store.message.contains("逾時"), store.message)
    }

    func testOfferPreviewDisplaysTheStoreKitPriceWithoutATransaction() async throws {
        let config = try XCTUnwrap(Bundle(for: Self.self).url(forResource: "WoundLiteCommerce", withExtension: "storekit"))
        let session = try freshCommerceTestSession(url: config)
        defer { session.clearTransactions() }
        let products = try await Product.products(for: [purchaseProduct])
        let product = try XCTUnwrap(products.first)
        let service = PurchaseService(), journal = PurchaseJournal()
        let store = LiteNativePurchaseStore(service: service, provider: LiteStoreKitPurchaseProvider(xcodeTest: true), journal: journal, accountGeneration: { service.generation })
        await store.observePurchases(accountToken: nil)
        await store.loadOffer()
        XCTAssertEqual(store.offer?.displayPrice, product.displayPrice)
        XCTAssertEqual(store.offer?.name, product.displayName)
        XCTAssertTrue(session.allTransactions().isEmpty)
        XCTAssertEqual(service.contexts, 0)
        XCTAssertEqual(service.calls, 0)
        let saved = await journal.values()
        XCTAssertTrue(saved.isEmpty)
    }
    func testAnAnnualProductWithTheExpectedIdentifierCannotBeOfferedAsMonthly() async throws {
        // Change only the local test catalogue. Confirm StoreKit actually saw
        // the annual term before checking the purchase screen's rejection.
        let source = try XCTUnwrap(Bundle(for: Self.self).url(forResource: "WoundLiteCommerce", withExtension: "storekit"))
        let catalog = try String(contentsOf: source, encoding: .utf8)
        let annual = catalog.replacingOccurrences(of: "\"P1M\"", with: "\"P1Y\"")
        XCTAssertNotEqual(catalog, annual)
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let file = directory.appendingPathComponent("Annual.storekit")
        try annual.write(to: file, atomically: true, encoding: .utf8)
        let session = try freshCommerceTestSession(url: file)
        defer { session.resetToDefaultState() }
        let products = try await Product.products(for: [purchaseProduct])
        let product = try XCTUnwrap(products.first)
        XCTAssertEqual(product.subscription?.subscriptionPeriod.unit, .year)
        let service = PurchaseService()
        let provider = LiteStoreKitPurchaseProvider(xcodeTest: true)
        let model = LiteNativePurchaseStore(service: service, provider: provider, journal: PurchaseJournal(), accountGeneration: { service.generation })
        let count = session.allTransactions().count
        await model.loadOffer()
        XCTAssertNil(model.offer)
        await model.purchase(accepting: nativeTestOffer)
        XCTAssertEqual(service.contexts, 0)
        XCTAssertEqual(session.allTransactions().count, count)
    }
    func testLocalStoreKitPropagatesServerTokenAndDefersFinishUntilAcknowledged()async throws {
        let url=try XCTUnwrap(Bundle(for:Self.self).url(forResource:"WoundLiteCommerce",withExtension:"storekit"))
        let session=try freshCommerceTestSession(url:url)
        defer{session.clearTransactions()}
        let s=PurchaseService();s.environment="Xcode";s.fail=true
        let p=LiteStoreKitPurchaseProvider(xcodeTest:true),j=PurchaseJournal()
        let m=LiteNativePurchaseStore(service:s,provider:p,journal:j,accountGeneration:{s.generation},now:{Date(timeIntervalSince1970:Double(purchaseNow))})
        await m.observePurchases(accountToken: purchaseToken)
        defer { m.stopObserving() }
        await m.loadOffer()
        let offer = try XCTUnwrap(m.offer)
        let products = try await Product.products(for: [purchaseProduct])
        let product = try XCTUnwrap(products.first)
        XCTAssertEqual(offer.displayPrice, product.displayPrice)
        XCTAssertEqual(offer.name, product.displayName)
        XCTAssertEqual(offer.price, Decimal(string: "1.99"))
        XCTAssertEqual(offer.currency, product.priceFormatStyle.currencyCode)
        let storefront = await Storefront.current
        XCTAssertEqual(offer.storefront, storefront?.id)
        XCTAssertEqual(product.subscription?.subscriptionPeriod.unit, .month)
        XCTAssertEqual(product.subscription?.subscriptionPeriod.value, 1)
        XCTAssertTrue(session.allTransactions().isEmpty)
        XCTAssertEqual(s.contexts, 0)
        await m.purchase(accepting: offer)
        let unfinished=try await p.unfinished();XCTAssertEqual(unfinished.count,1)
        XCTAssertEqual(unfinished.first?.token,purchaseToken);XCTAssertEqual(s.calls,1)
        s.fail=false;await m.purchase(accepting: offer);XCTAssertEqual(s.calls,2)
        let remaining=try await p.unfinished();XCTAssertTrue(remaining.isEmpty)
        await m.restore();XCTAssertEqual(s.calls,2)
        XCTAssertEqual(session.allTransactions().count, 1)
    }

    private func purchaseStore(_ service: PurchaseService, journal: PurchaseJournal) -> LiteNativePurchaseStore {
        service.environment = "Xcode"
        return LiteNativePurchaseStore(service: service, provider: LiteStoreKitPurchaseProvider(xcodeTest: true),
            journal: journal, accountGeneration: { service.generation },
            now: { Date(timeIntervalSince1970: Double(purchaseNow)) })
    }

    func testFinishedPurchaseRestoresToSameAccountWithFreshProviderAndEmptyJournal() async throws {
        let config = try XCTUnwrap(Bundle(for: Self.self).url(forResource: "WoundLiteCommerce", withExtension: "storekit"))
        let session = try freshCommerceTestSession(url: config)
        defer { session.clearTransactions() }

        let service = PurchaseService()
        let original = purchaseStore(service, journal: PurchaseJournal())
        await original.purchaseDisplayedTestOffer()
        XCTAssertEqual(service.calls, 1, original.message)
        let purchaseIDs = session.allTransactions().map(\.identifier)
        XCTAssertEqual(purchaseIDs.count, 1)

        let freshJournal = PurchaseJournal()
        let reopened = purchaseStore(service, journal: freshJournal)
        let unfinishedBefore = try await reopened.provider.unfinished()
        XCTAssertTrue(unfinishedBefore.isEmpty)
        let before = try await reopened.provider.knownPurchases()
        XCTAssertEqual(before.count, 1)
        let transaction = try XCTUnwrap(before.first)
        XCTAssertEqual(transaction.token, service.token)

        await reopened.restore()

        let unfinishedAfter = try await reopened.provider.unfinished()
        XCTAssertTrue(unfinishedAfter.isEmpty)
        let after = try await reopened.provider.knownPurchases()
        XCTAssertEqual(after.count, 1)
        XCTAssertTrue(try XCTUnwrap(after.first).sameTransaction(as: transaction))
        XCTAssertEqual(session.allTransactions().map(\.identifier), purchaseIDs)
        XCTAssertEqual(service.calls, 2, reopened.message)
        let finished = try await freshJournal.values().map {
            try JSONDecoder().decode(LitePurchaseCheckpoint.self, from: $0)
        }.filter { $0.finished }
        XCTAssertEqual(finished.count, 1)
        let acknowledgment = try XCTUnwrap(finished.first?.acknowledgment)
        XCTAssertEqual(acknowledgment.account_sha256, service.account)
        XCTAssertEqual(acknowledgment.app_account_token, transaction.token)
        XCTAssertEqual(acknowledgment.transaction_id, transaction.transaction)
        XCTAssertEqual(acknowledgment.original_transaction_id, transaction.original)
    }

    func testRenewalAfterAccountSwitchRestoresOnlyToOriginalAccountOnSecondInstallation() async throws {
        let config = try XCTUnwrap(Bundle(for: Self.self).url(forResource: "WoundLiteCommerce", withExtension: "storekit"))
        let session = try freshCommerceTestSession(url: config)
        defer { session.clearTransactions() }
        let originalService = PurchaseService()
        let originalStore = purchaseStore(originalService, journal: PurchaseJournal())
        await originalStore.purchaseDisplayedTestOffer()
        XCTAssertEqual(originalService.calls, 1)
        let initialEntitlements = try await originalStore.provider.knownPurchases()
        XCTAssertEqual(initialEntitlements.count, 1)
        let initial = try XCTUnwrap(initialEntitlements.first)

        try session.forceRenewalOfSubscription(productIdentifier: purchaseProduct)
        // Wait for StoreKit to publish the renewal while the original account is away.
        var renewal: LitePurchaseEvent?
        for _ in 0..<50 {
            let observed = try await originalStore.provider.unfinished()
            renewal = observed.first { $0.transaction != initial.transaction && $0.original == initial.original }
            if renewal != nil { break }
            try await Task.sleep(for: .milliseconds(100))
        }
        let renewed = try XCTUnwrap(renewal)
        XCTAssertEqual(renewed.token, purchaseToken)
        XCTAssertEqual(session.allTransactions().count, 2)

        let secondService = PurchaseService()
        secondService.owner = "synthetic-second-installation"
        secondService.account = String(repeating: "e", count: 64)
        secondService.token = "20000000-0000-4000-8000-000000000002"
        let secondJournal = PurchaseJournal()
        let secondStore = purchaseStore(secondService, journal: secondJournal)
        await secondStore.restore()
        XCTAssertTrue(secondStore.message.contains("不符"), secondStore.message)
        XCTAssertEqual(secondService.calls, 0)
        let retained = try await secondStore.provider.unfinished()
        XCTAssertTrue(retained.contains { $0.sameTransaction(as: renewed) })

        secondService.generation += 1
        secondService.account = originalService.account
        secondService.token = originalService.token
        await secondStore.restore()
        XCTAssertTrue(secondStore.message.contains("關聯已確認"), secondStore.message)
        XCTAssertEqual(secondService.calls, 1)
        let completed = try await secondJournal.values()
            .map { try JSONDecoder().decode(LitePurchaseCheckpoint.self, from: $0) }
            .filter { $0.finished }
        XCTAssertEqual(completed.count, 1)
        let checkpoint = try XCTUnwrap(completed.first)
        XCTAssertEqual(checkpoint.context.installation, secondService.owner)
        XCTAssertEqual(checkpoint.acknowledgment?.account_sha256, originalService.account)
        XCTAssertEqual(checkpoint.acknowledgment?.transaction_id, renewed.transaction)
        XCTAssertEqual(checkpoint.acknowledgment?.original_transaction_id, initial.original)
        let remaining = try await secondStore.provider.unfinished()
        XCTAssertTrue(remaining.isEmpty)

        await secondStore.restore()
        XCTAssertEqual(secondService.calls, 1)
        XCTAssertEqual(originalService.calls, 1)
        XCTAssertEqual(session.allTransactions().count, 2)
    }

    func testStoreKitApprovalWhileClosedReopensWithOneAcknowledgment() async throws {
        let url = try XCTUnwrap(Bundle(for: Self.self).url(forResource: "WoundLiteCommerce", withExtension: "storekit"))
        let sandbox = try freshCommerceTestSession(url: url)
        sandbox.askToBuyEnabled = true
        defer { sandbox.askToBuyEnabled = false; sandbox.clearTransactions() }
        let service = PurchaseService(), journal = PurchaseJournal()
        service.environment = "Xcode"
        func start() -> LiteNativePurchaseStore {
            LiteNativePurchaseStore(service: service, provider: LiteStoreKitPurchaseProvider(xcodeTest: true),
                journal: journal, accountGeneration: { service.generation }, now: { Date(timeIntervalSince1970: Double(purchaseNow)) })
        }
        let original = start()
        await original.observePurchases(accountToken: purchaseToken)
        await original.purchaseDisplayedTestOffer()
        XCTAssertTrue(original.message.contains("等待核准"), original.message)
        XCTAssertEqual(service.calls, 0)
        original.stopObserving()
        let transaction = try XCTUnwrap(sandbox.allTransactions().first)
        try sandbox.approveAskToBuyTransaction(identifier: transaction.identifier)

        let reopened = start()
        defer { reopened.stopObserving() }
        await reopened.observePurchases(accountToken: purchaseToken)
        var unfinished = try await reopened.provider.unfinished()
        for _ in 0..<50 where service.calls == 0 || !unfinished.isEmpty {
            try await Task.sleep(for: .milliseconds(100))
            unfinished = try await reopened.provider.unfinished()
        }
        XCTAssertTrue(unfinished.isEmpty)
        XCTAssertEqual(service.calls, 1)
        XCTAssertEqual(sandbox.allTransactions().count, 1)
        let checkpoints = try await journal.values().map { try JSONDecoder().decode(LitePurchaseCheckpoint.self, from: $0) }
        XCTAssertEqual(checkpoints.filter { $0.finished && $0.acknowledgment?.app_account_token == purchaseToken }.count, 1)
    }
}
#endif

private actor PurchaseWire:LiteAuthenticatedTransport {
    var owner="synthetic-device",payload=Data(),status=200,alternate:URL?,sent=[URLRequest]()
    func installation()->String{owner}
    func plan(_ data:Data,code:Int=200,url:URL?=nil){payload=data;status=code;alternate=url}
    func requests()->[URLRequest]{sent}
    func send(_ request:URLRequest,installation:String) async throws->(Data,HTTPURLResponse){
        guard owner==installation else{throw LitePurchaseFailure.accountMismatch};sent.append(request)
        return(payload,HTTPURLResponse(url:alternate ?? request.url!,statusCode:status,httpVersion:nil,headerFields:[:])!)
    }
}
@MainActor final class LitePurchaseHTTPTests:XCTestCase {
    func testCachedReceiptLookupUsesOnlyTheExistingOperation() async throws {
        let wire = PurchaseWire(), service = PurchaseService(), op = String(repeating: "1", count: 32)
        let saved = try await service.context(operation: op)
        await wire.plan(try JSONEncoder().encode(saved))
        let http = try LitePurchaseHTTPService(origin: service.origin, environment: "Sandbox", transport: wire)
        let result = try await http.context(operation: op, existingOnly: true)
        XCTAssertEqual(result, saved)
        let requests = await wire.requests()
        XCTAssertEqual(requests.count, 1)
        let data = try XCTUnwrap(requests.first?.httpBody)
        XCTAssertEqual(try JSONDecoder().decode([String:String].self, from: data),
                       ["anon_id": saved.installation, "operation": op, "mode": "existing"])
    }
    func testContextAndConfirmUseExactSignedRoutesAndNoClientTier()async throws {
        let w=PurchaseWire(),s=PurchaseService(),op=String(repeating:"1",count:32)
        let c=try await s.context(operation:op)
        let http=try LitePurchaseHTTPService(origin:s.origin,environment:"Sandbox",transport:w)
        await w.plan(try JSONEncoder().encode(c));let result=try await http.context(operation:op);XCTAssertEqual(result,c)
        let ack=try await s.confirm(c,event:event());await w.plan(try JSONEncoder().encode(ack))
        let confirmed=try await http.confirm(c,event:event());XCTAssertEqual(confirmed,ack)
        let requests=await w.requests();XCTAssertEqual(requests.map{$0.url!.path},["/api/v1/lite/purchase/context","/api/v1/lite/purchase/confirm"])
        let body=try JSONSerialization.jsonObject(with:requests[1].httpBody!) as! [String:String]
        XCTAssertEqual(Set(body.keys),["anon_id","operation","signed_transaction"])
    }
    func testWrongEnvironmentResponseRejected()async throws {
        let w=PurchaseWire(),s=PurchaseService();s.environment="Production";let op=String(repeating:"1",count:32)
        let c=try await s.context(operation:op);await w.plan(try JSONEncoder().encode(c))
        let http=try LitePurchaseHTTPService(origin:s.origin,environment:"Sandbox",transport:w)
        do{_=try await http.context(operation:op);XCTFail()}catch{}
    }
    func testRedirectedOrFailedResponseNeverConfirms()async throws {
        for url in [URL(string:"https://evil.invalid/api/v1/lite/purchase/context")!,URL(string:"https://synthetic.invalid/api/v1/lite/purchase/context?q=1")!] {
            let w=PurchaseWire(),s=PurchaseService(),op=String(repeating:"1",count:32)
            let c=try await s.context(operation:op);await w.plan(try JSONEncoder().encode(c),url:url)
            let http=try LitePurchaseHTTPService(origin:s.origin,environment:"Sandbox",transport:w)
            do{_=try await http.context(operation:op);XCTFail()}catch{}
        }
    }
    func testHTTPFactoryRejectsLocalOrXcodeProductionMixing()throws {
        let w=PurchaseWire()
        for url in ["http://synthetic.invalid","https://u:p@synthetic.invalid","https://synthetic.invalid/x","https://synthetic.invalid?q=1"] {
            XCTAssertThrowsError(try LitePurchaseHTTPService(origin:URL(string:url)!,environment:"Sandbox",transport:w))
        }
        XCTAssertThrowsError(try LitePurchaseHTTPService(origin:URL(string:"https://synthetic.invalid")!,environment:"Xcode",transport:w))
    }
}
