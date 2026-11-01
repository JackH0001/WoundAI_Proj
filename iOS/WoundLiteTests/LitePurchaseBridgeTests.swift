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
private func event(_ token:String=purchaseToken,signed:String="synthetic-jws",environment:String="Sandbox")->LitePurchaseEvent {
    LitePurchaseEvent(transaction:"123",original:"100",token:token,product:purchaseProduct,environment:environment,signed:signed)
}
private actor PurchaseJournal:LiteServiceJournal {
    var rows=[String:Data](),writes=0,failAt=0
    func load(_ id:String)->Data? {rows[id]}
    func replace(_ id:String,expected:Data?,with data:Data) throws {
        writes+=1;if writes==failAt{throw LitePurchaseFailure.unavailable}
        guard rows[id]==expected else{throw LitePurchaseFailure.journalConflict};rows[id]=data
    }
    func fail(_ n:Int){failAt=n}
    func values()->[Data]{Array(rows.values)}
}
@MainActor private final class PurchaseService:LitePurchaseService {
    let origin=URL(string:"https://synthetic.invalid")!
    var owner="synthetic-device",token=purchaseToken,environment="Sandbox",calls=0,contexts=0,fail=false,wrongAck=false,expired=false
    var afterConfirm:(()->Void)?
    var account = String(repeating:"a",count:64)
    func installation() async throws ->String{owner}
    var lookups = [String]()
    func context(operation:String, existingOnly: Bool = false) async throws ->LitePurchaseContext {
        if existingOnly { lookups.append(operation) }
        contexts+=1
        return LitePurchaseContext(v:1,operation:operation,installation:owner,account_sha256:account,
            app_account_token:token,registration_sha256:String(repeating:"b",count:64),scope_sha256:String(repeating:"c",count:64),
            product_id:purchaseProduct,environment:environment,created_at:purchaseNow-(expired ? 900:0),expires_at:purchaseNow+(expired ? 0:900))
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
    var lookups = 0
    func knownPurchases() async throws -> [LitePurchaseEvent] { pending + restored }
    func monthlyOffer() async throws -> LiteMonthlyOffer {
        lookups += 1
        guard let catalog else { throw LitePurchaseFailure.unavailable }
        return catalog
    }
    func purchase(offer: LiteMonthlyOffer, token: UUID) async throws -> LitePurchaseOutcome {
        XCTAssertEqual(offer, catalog)
        try await beforePurchase?();purchases+=1;requestedToken=token
        if case .completed(let e)=outcome{pending=[e]};return outcome
    }
    func restore() async throws ->[LitePurchaseEvent]{restoreCalls+=1;return restored}
    func unfinished() async throws ->[LitePurchaseEvent]{pending}
    func updates()->AsyncStream<LitePurchaseUpdate>{AsyncStream{continuation=$0}}
    func finish(_ e:LitePurchaseEvent)async throws {
        try await beforeFinish?();if failFinish{throw LitePurchaseFailure.unavailable};finishes+=1;pending=[]
    }
}
@MainActor final class LitePurchaseBridgeTests:XCTestCase {
    private func setup()->(PurchaseService,PurchaseProvider,PurchaseJournal,LiteNativePurchaseStore){
        let s=PurchaseService(),p=PurchaseProvider(),j=PurchaseJournal()
        return(s,p,j,LiteNativePurchaseStore(service:s,provider:p,journal:j,now:{Date(timeIntervalSince1970:Double(purchaseNow))}))
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
        s.fail=false;let restarted=LiteNativePurchaseStore(service:s,provider:p,journal:j)
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
        m.startObserving();await Task.yield();p.continuation?.yield(.event(event()))
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
    func testEmptyRestoreDoesNotRegisterOrResetQuota()async {
        let(s,p,_,m)=setup();await m.restore();XCTAssertEqual(s.calls,0);XCTAssertEqual(p.purchases,0)
        XCTAssertTrue(m.message.contains("沒有找到"))
    }
    func testDuplicateConcurrentUpdatesFinishOnlyOnce()async throws {
        let(s,p,_,m)=setup();m.startObserving();await Task.yield()
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
        let session = try SKTestSession(contentsOf: file)
        session.disableDialogs = true
        defer { session.resetToDefaultState() }
        let products = try await Product.products(for: [purchaseProduct])
        let product = try XCTUnwrap(products.first)
        XCTAssertEqual(product.subscription?.subscriptionPeriod.unit, .year)
        let service = PurchaseService()
        let provider = LiteStoreKitPurchaseProvider(xcodeTest: true)
        let model = LiteNativePurchaseStore(service: service, provider: provider, journal: PurchaseJournal())
        let count = session.allTransactions().count
        await model.loadOffer()
        XCTAssertNil(model.offer)
        await model.purchase(accepting: nativeTestOffer)
        XCTAssertEqual(service.contexts, 0)
        XCTAssertEqual(session.allTransactions().count, count)
    }
    func testLocalStoreKitPropagatesServerTokenAndDefersFinishUntilAcknowledged()async throws {
        let url=try XCTUnwrap(Bundle(for:Self.self).url(forResource:"WoundLiteCommerce",withExtension:"storekit"))
        let session=try SKTestSession(contentsOf:url);session.disableDialogs=true;session.clearTransactions()
        defer{session.clearTransactions()}
        let s=PurchaseService();s.environment="Xcode";s.fail=true
        let p=LiteStoreKitPurchaseProvider(xcodeTest:true),j=PurchaseJournal()
        let m=LiteNativePurchaseStore(service:s,provider:p,journal:j,now:{Date(timeIntervalSince1970:Double(purchaseNow))})
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
