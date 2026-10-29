import XCTest
@testable import WoundLite

private let deletionOrigin=URL(string:"https://lite.example.invalid")!
private let deletionOwner="synthetic-deletion-device"
private let deletionAccount=String(repeating:"a",count:64)

private actor DeletionJournal:LiteServiceJournal {
    var values:[String:Data]=[:],fail=false
    func load(_ id:String)throws->Data? {values[id]}
    func replace(_ id:String,expected:Data?,with data:Data)throws {
        guard !fail,values[id]==expected else{throw LiteDeletionFailure.conflict};values[id]=data
    }
    func failWrites(){fail=true}
    func rows()->[String:Data]{values}
}
@MainActor private final class DeletionIdentity:LiteDeletionIdentity {
    var contexts=0,suspensions=0,refuse=false
    var onSuspend:(()async throws->Void)?
    func deletionContext()async throws->LiteDeletionContext {
        contexts+=1;if refuse{throw LiteDeletionFailure.identity}
        return LiteDeletionContext(origin:deletionOrigin.absoluteString,installation:deletionOwner,account:deletionAccount)
    }
    func suspendForDeletion(_ context:LiteDeletionContext,operation:String)async throws {
        suspensions+=1;try await onSuspend?();if refuse{throw LiteDeletionFailure.identity}
    }
}
@MainActor private final class DeletionService:LiteAccountDeletionService {
    let origin=deletionOrigin
    var owner=deletionOwner,requests:[String]=[],statuses:[String]=[],fail=false,wrongOwner=false,identityFailure=false
    var onRequest:(()async throws->Void)?,onStatus:(()async throws->Void)?
    var saved:[String:LiteDeletionReceipt]=[:]
    func installation()async throws->String{owner}
    func reply(_ op:String)->LiteDeletionReceipt {
        LiteDeletionReceipt(v:1,state:"account_deletion_pending",operation:op,installation:wrongOwner ? "wrong":owner,
            requested_at:1000,inventory_sha256:String(repeating:"b",count:64),grant_total:2,revocation_scan:1,
            grants_examined:0,grants_revoked:0,apple_revocation:"not_started",data_disposition:"not_started",
            account_deleted:false,subscription_cancelled:false,local_records_preserved:true)
    }
    func request(operation:String,installation:String)async throws->LiteDeletionReceipt {
        requests.append(operation);if identityFailure{throw LiteDeletionFailure.identity};saved[operation]=saved[operation] ?? reply(operation);try await onRequest?()
        if fail{throw LiteDeletionFailure.unavailable};return saved[operation]!
    }
    func status(operation:String,installation:String)async throws->LiteDeletionReceipt {
        statuses.append(operation);try await onStatus?();if fail{throw LiteDeletionFailure.unavailable}
        return saved[operation] ?? reply(operation)
    }
}

@MainActor final class LiteAccountDeletionTests:XCTestCase {
    private func fixture()->(DeletionService,DeletionIdentity,DeletionJournal,LiteAccountDeletionStore){
        let s=DeletionService(),i=DeletionIdentity(),j=DeletionJournal()
        return(s,i,j,LiteAccountDeletionStore(service:s,identity:i,journal:j,now:{Date(timeIntervalSince1970:1000)}))
    }
    func testFirstDeliveryIdentityExpiredIsExplicitAndNeverReopensService()async {
        let(s,i,_,m)=fixture();s.identityFailure=true;await m.requestConfirmed()
        XCTAssertTrue(m.identityRequired);XCTAssertNotNil(m.record);XCTAssertNil(m.record?.receipt)
        XCTAssertTrue(m.message.contains("身分需要重新核對"));XCTAssertEqual(i.contexts,1)
        let op=m.record!.operation;await m.retry();XCTAssertEqual(s.requests,[op,op]);XCTAssertEqual(i.contexts,1)
        XCTAssertFalse(m.message.contains("已受理"))
    }
    func testReloadAndRetryWithoutIntentNeverInitiateDeletion()async {
        let(s,i,_,m)=fixture();await m.reload();await m.retry()
        XCTAssertTrue(s.requests.isEmpty);XCTAssertTrue(s.statuses.isEmpty);XCTAssertEqual(i.contexts,0)
    }
    func testIntentSavedAndServiceSuspendedBeforeNetwork()async throws {
        let(s,i,j,m)=fixture()
        i.onSuspend={let rows=await j.rows();XCTAssertEqual(rows.count,1)}
        s.onRequest={XCTAssertEqual(i.suspensions,1);let rows=await j.rows();let r=try JSONDecoder().decode(LiteAccountDeletionCheckpoint.self,from:rows.values.first!);XCTAssertNil(r.receipt)}
        await m.requestConfirmed();XCTAssertEqual(s.requests.count,1);XCTAssertFalse(m.record!.receipt!.account_deleted)
        XCTAssertTrue(m.message.contains("尚未完成刪除"))
    }
    func testFailedLocalPersistenceCannotSendOrSuspend()async {
        let(s,i,j,m)=fixture();await j.failWrites();await m.requestConfirmed()
        XCTAssertTrue(s.requests.isEmpty);XCTAssertEqual(i.suspensions,0)
    }
    func testLostResponseRestartResendsSameOperationWithoutAnotherLogin()async {
        let(s,i,j,m)=fixture();s.fail=true;await m.requestConfirmed();let op=m.record!.operation
        s.fail=false;let next=LiteAccountDeletionStore(service:s,identity:i,journal:j,now:{Date(timeIntervalSince1970:2000)})
        await next.retry();XCTAssertEqual(s.requests,[op,op]);XCTAssertEqual(i.contexts,1);XCTAssertEqual(s.saved.count,1)
        XCTAssertEqual(next.record?.receipt?.operation,op)
    }
    func testAcknowledgedRetryOnlyQueriesProgress()async {
        let(s,_,_,m)=fixture();await m.requestConfirmed();await m.retry();await m.requestConfirmed()
        XCTAssertEqual(s.requests.count,1);XCTAssertEqual(s.statuses.count,2)
    }
    func testConcurrentClickDoesNotDispatchTwice()async {
        let(s,_,_,m)=fixture();s.onRequest={await m.requestConfirmed();await m.retry()}
        await m.requestConfirmed();XCTAssertEqual(s.requests.count,1);XCTAssertTrue(s.statuses.isEmpty)
    }
    func testSuspensionFailureKeepsIntentAndDoesNotSend()async {
        let(s,i,_,m)=fixture();i.onSuspend={throw LiteDeletionFailure.unavailable}
        await m.requestConfirmed();XCTAssertNotNil(m.record);XCTAssertTrue(s.requests.isEmpty)
        i.onSuspend=nil;await m.retry();XCTAssertEqual(s.requests.count,1);XCTAssertEqual(i.contexts,1)
    }
    func testIdentityFailureDoesNotCreateIntent()async {
        let(s,i,j,m)=fixture();i.refuse=true;await m.requestConfirmed()
        let rows=await j.rows();XCTAssertTrue(rows.isEmpty);XCTAssertTrue(s.requests.isEmpty)
    }
    func testWrongOwnerReceiptIsNeverAccepted()async {
        let(s,_,_,m)=fixture();s.wrongOwner=true;await m.requestConfirmed()
        XCTAssertNil(m.record?.receipt);XCTAssertTrue(m.message.contains("尚未確認"))
    }
    func testDeviceChangesDuringReplyCannotSaveReceipt()async {
        let(s,_,_,m)=fixture();s.onRequest={s.owner="different-device"};await m.requestConfirmed()
        XCTAssertNil(m.record?.receipt)
    }
    func testExistingLocalRecordsAndResearchChoicesAreNeverTouched()async throws {
        let(s,_,j,m)=fixture();let key=String(repeating:"c",count:32),raw=Data("original-record-research-mask".utf8)
        try await j.replace(key,expected:nil,with:raw);await m.requestConfirmed();await m.retry()
        let actual=try await j.load(key);XCTAssertEqual(actual,raw);XCTAssertEqual(s.requests.count,1)
    }
    func testCorruptIntentBlocksNewRequestInsteadOfResetting()async throws {
        let(s,i,j,m)=fixture();let k=LiteAccountDeletionCheckpoint.key(origin:deletionOrigin.absoluteString,owner:deletionOwner)
        try await j.replace(k,expected:nil,with:Data("corrupt".utf8));await m.reload();await m.requestConfirmed()
        XCTAssertTrue(m.needsReview);XCTAssertTrue(s.requests.isEmpty);XCTAssertEqual(i.contexts,0)
    }
    func testReceiptCannotClaimDeletionOrCancelledSubscription()throws {
        let s=DeletionService(),op=String(repeating:"e",count:32),r=s.reply(String(repeating:"e",count:32))
        for change in [["account_deleted":true],["subscription_cancelled":true],["local_records_preserved":false],
                       ["data_disposition":"completed"],["grants_revoked":3],["apple_revocation":"confirmed"],["v":2]] as [[String:Any]] {
            var d=try JSONSerialization.jsonObject(with:JSONEncoder().encode(r)) as! [String:Any];d.merge(change){_,new in new}
            let x=try JSONDecoder().decode(LiteDeletionReceipt.self,from:JSONSerialization.data(withJSONObject:d))
            XCTAssertThrowsError(try x.validate(owner:deletionOwner,operation:op))
        }
    }
    func testReceiptRejectsChangedInventoryAndBackwardsProgress()throws {
        let s=DeletionService(),op=String(repeating:"e",count:32),r=s.reply(String(repeating:"e",count:32))
        var d=try JSONSerialization.jsonObject(with:JSONEncoder().encode(r)) as! [String:Any]
        d["grants_examined"]=1;d["grants_revoked"]=1;d["apple_revocation"]="in_progress"
        let newer=try JSONDecoder().decode(LiteDeletionReceipt.self,from:JSONSerialization.data(withJSONObject:d))
        XCTAssertThrowsError(try r.validate(owner:deletionOwner,operation:op,previous:newer))
        d["inventory_sha256"]=String(repeating:"d",count:64)
        let changed=try JSONDecoder().decode(LiteDeletionReceipt.self,from:JSONSerialization.data(withJSONObject:d))
        XCTAssertThrowsError(try changed.validate(owner:deletionOwner,operation:op,previous:r))
    }
    func testNewScanAllowsCountsToRestartWithoutClaimingDeletion()throws {
        let s=DeletionService(),op=String(repeating:"e",count:32),r=s.reply(String(repeating:"e",count:32))
        var d=try JSONSerialization.jsonObject(with:JSONEncoder().encode(r)) as! [String:Any]
        d["grants_examined"]=2;d["grants_revoked"]=1;d["apple_revocation"]="pending"
        let first=try JSONDecoder().decode(LiteDeletionReceipt.self,from:JSONSerialization.data(withJSONObject:d))
        d["grants_examined"]=0;d["grants_revoked"]=0;d["revocation_scan"]=2;d["apple_revocation"]="in_progress"
        let next=try JSONDecoder().decode(LiteDeletionReceipt.self,from:JSONSerialization.data(withJSONObject:d))
        XCTAssertNoThrow(try next.validate(owner:deletionOwner,operation:op,previous:first))
    }
}

@MainActor private final class DeletionHTTPTransport:LiteAuthenticatedTransport {
    var owner=deletionOwner,status=202,wrongURL=false,requests=[URLRequest]()
    var data:Data=Data()
    func installation()async throws->String{owner}
    func send(_ request:URLRequest,installation:String)async throws->(Data,HTTPURLResponse){
        requests.append(request)
        return(data,HTTPURLResponse(url:wrongURL ? URL(string:"https://other.invalid")!:request.url!,statusCode:status,httpVersion:nil,headerFields:nil)!)
    }
}
@MainActor final class LiteAccountDeletionHTTPTests:XCTestCase {
    private let op=String(repeating:"e",count:32)
    private func fixture()throws->(DeletionHTTPTransport,LiteAccountDeletionHTTPService){
        let t=DeletionHTTPTransport();t.data=try JSONEncoder().encode(DeletionService().reply(op))
        return(t,try LiteAccountDeletionHTTPService(origin:deletionOrigin,transport:t))
    }
    func testRequestHasExplicitConfirmationAndStatusDoesNot()async throws {
        let(t,s)=try fixture();_=try await s.request(operation:op,installation:deletionOwner)
        _=try await s.status(operation:op,installation:deletionOwner)
        XCTAssertEqual(t.requests.map{$0.url!.path},["/api/v1/lite/account/deletion/request","/api/v1/lite/account/deletion/status"])
        let bodies=try t.requests.map{try JSONSerialization.jsonObject(with:$0.httpBody!) as! [String:String]}
        XCTAssertEqual(bodies[0],["anon_id":deletionOwner,"operation":op,"confirmation":"delete_account"])
        XCTAssertEqual(bodies[1],["anon_id":deletionOwner,"operation":op])
    }
    func testOwnerMismatchDoesNotSend()async throws {
        let(t,s)=try fixture();do{_=try await s.request(operation:op,installation:"wrong");XCTFail()}catch{}
        XCTAssertTrue(t.requests.isEmpty)
    }
    func testIdentityRejectionIsDistinguishedFromRetryableFailure()async throws {
        let(t,s)=try fixture();t.status=401
        do{_=try await s.request(operation:op,installation:deletionOwner);XCTFail()}catch LiteDeletionFailure.identity{}catch{XCTFail()}
        t.status=503
        do{_=try await s.request(operation:op,installation:deletionOwner);XCTFail()}catch LiteDeletionFailure.unavailable{}catch{XCTFail()}
    }
    func testWrongResponseOriginAndOversizedBodyAreRejected()async throws {
        let(t,s)=try fixture();t.wrongURL=true
        do{_=try await s.request(operation:op,installation:deletionOwner);XCTFail()}catch{}
        t.wrongURL=false;t.data=Data(repeating:32,count:20001)
        do{_=try await s.status(operation:op,installation:deletionOwner);XCTFail()}catch{}
    }
    func testPrivacyTransportRequiresExistingInstallationAndExactRoute()async throws {
        let d=AttestDeviceDouble(),v=AttestMemory(),h=AttestHTTPDouble()
        let c=try LiteAttestClient(origin:deletionOrigin,audience:"lite-test",device:d,persistence:v,transport:h,now:{Date(timeIntervalSince1970:1000)})
        let t=LiteSignedDeletionTransport(client:c,allowPrivacyRequest:{true})
        do{_=try await t.installation();XCTFail()}catch{}
        var counts=await d.counts();XCTAssertEqual(counts.0,0)
        let owner=try await c.registeredInstallation()
        var r=URLRequest(url:deletionOrigin.appendingPathComponent("api/v1/lite/account/status"));r.httpMethod="POST"
        do{_=try await t.send(r,installation:owner);XCTFail()}catch{}
        counts=await d.counts();XCTAssertEqual(counts.2,0)
        let denied=LiteSignedDeletionTransport(client:c,allowPrivacyRequest:{false})
        do{_=try await denied.installation();XCTFail()}catch{}
    }
}
