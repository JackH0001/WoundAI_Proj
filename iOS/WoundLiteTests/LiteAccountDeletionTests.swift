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
    var contexts=0,suspensions=0,refuse=false,authCalls=0,cancel=false
    var beforeAuth:(()async throws->Void)?
    var onSuspend:(()async throws->Void)?
    func authenticateDeletion(_ challenge:LiteDeletionChallenge,context:LiteDeletionContext)async throws->String {
        authCalls+=1;try await beforeAuth?();if cancel{throw LiteAccountFailure.cancelled};return "synthetic-id-token"
    }
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
    var clock=1000,challengeCalls=0,confirmCalls=0,confirmFail=false,confirmIdentityFail=false,challengeExpired=false
    var afterConfirm:(()async throws->Void)?,beforeChallenge:(()async throws->Void)?
    func deletionChallenge(context:LiteDeletionContext,operation:String,attempt:String)async throws->LiteDeletionChallenge {
        challengeCalls+=1;try await beforeChallenge?();if challengeExpired{throw LiteDeletionFailure.expired}
        return LiteDeletionChallenge(v:1,installation:owner,operation:operation,reauth_operation:attempt,account_sha256:context.account,
            nonce:String(repeating:"a",count:64),state:String(repeating:"b",count:64),created_at:clock,expires_at:clock+300,audience:"com.woundai.lite",purpose:"continue_deletion")
    }
    func confirmDeletion(_ c:LiteDeletionChallenge,context:LiteDeletionContext,identityToken:String)async throws->LiteDeletionReceipt {
        confirmCalls+=1;if confirmIdentityFail{throw LiteDeletionFailure.identity}
        if confirmFail{throw LiteDeletionFailure.unavailable}
        saved[c.operation]=reply(c.operation);identityFailure=false;try await afterConfirm?();return saved[c.operation]!
    }
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
        XCTAssertTrue(m.message.contains("使用原 Apple 帳號核對"));XCTAssertEqual(i.contexts,1)
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
    func testReauthenticationHTTPBindsOriginalIntentAndNeverSendsAuthorizationCode()async throws {
        let(t,s)=try fixture();let context=LiteDeletionContext(origin:deletionOrigin.absoluteString,installation:deletionOwner,account:deletionAccount)
        let q=try await DeletionService().deletionChallenge(context:context,operation:op,attempt:String(repeating:"f",count:32))
        t.status=200;t.data=try JSONEncoder().encode(q)
        _=try await s.deletionChallenge(context:context,operation:op,attempt:q.reauth_operation)
        t.status=202;t.data=try JSONEncoder().encode(DeletionService().reply(op))
        _=try await s.confirmDeletion(q,context:context,identityToken:"synthetic-proof")
        let bodies=try t.requests.map{try JSONSerialization.jsonObject(with:$0.httpBody!) as! [String:String]}
        XCTAssertEqual(bodies[0]["confirmation"],"continue_deletion")
        XCTAssertEqual(bodies[1]["operation"],op);XCTAssertEqual(bodies[1]["account_sha256"],deletionAccount)
        XCTAssertEqual(bodies[1]["identity_token"],"synthetic-proof");XCTAssertNil(bodies[1]["authorization_code"])
    }
    func testReauthenticationExpiryIsNotGenericNetworkError()async throws {
        let(t,s)=try fixture();t.status=410
        do{_=try await s.request(operation:op,installation:deletionOwner);XCTFail()}catch LiteDeletionFailure.expired{}catch{XCTFail()}
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

@MainActor final class LiteDeletionRecoveryTests:XCTestCase {
    private func fixture()->(DeletionService,DeletionIdentity,DeletionJournal,LiteAccountDeletionStore){
        let s=DeletionService(),i=DeletionIdentity(),j=DeletionJournal();s.identityFailure=true
        return(s,i,j,LiteAccountDeletionStore(service:s,identity:i,journal:j,now:{Date(timeIntervalSince1970:Double(s.clock))}))
    }
    func testExplicitRecoveryKeepsOriginalOperationAndDoesNotRelogin()async {
        let(s,i,_,m)=fixture();await m.requestConfirmed();let op=m.record!.operation
        await m.reauthenticate();XCTAssertEqual(m.record?.receipt?.operation,op)
        XCTAssertEqual(i.contexts,1);XCTAssertEqual(i.authCalls,1);XCTAssertEqual(s.confirmCalls,1)
        XCTAssertNil(m.record?.recovery);XCTAssertFalse(m.record!.receipt!.account_deleted)
    }
    func testReloadAndRetryNeverOpenApplePrompt()async {
        let(s,i,_,m)=fixture();await m.requestConfirmed();await m.reload();await m.retry()
        XCTAssertEqual(i.authCalls,0);XCTAssertEqual(s.challengeCalls,0);XCTAssertNil(m.record?.receipt)
    }
    func testChallengeAndTokenPersistBeforeAppleAndConfirm()async throws {
        let(s,i,j,m)=fixture();await m.requestConfirmed()
        let k=LiteAccountDeletionCheckpoint.key(origin:s.origin.absoluteString,owner:s.owner)
        s.beforeChallenge={let raw=try await j.load(k);let row=try JSONDecoder().decode(LiteAccountDeletionCheckpoint.self,from:XCTUnwrap(raw));XCTAssertNotNil(row.recovery);XCTAssertNil(row.recovery?.challenge)}
        i.beforeAuth={let raw=try await j.load(k);let row=try JSONDecoder().decode(LiteAccountDeletionCheckpoint.self,from:XCTUnwrap(raw));XCTAssertNotNil(row.recovery?.challenge);XCTAssertNil(row.recovery?.identityToken)}
        s.afterConfirm={let raw=try await j.load(k);let row=try JSONDecoder().decode(LiteAccountDeletionCheckpoint.self,from:XCTUnwrap(raw));XCTAssertEqual(row.recovery?.identityToken,"synthetic-id-token")}
        await m.reauthenticate();XCTAssertNotNil(m.record?.receipt)
    }
    func testLocalWriteFailureCannotOpenAppleOrConfirm()async {
        let(s,i,j,m)=fixture();await m.requestConfirmed();await j.failWrites();await m.reauthenticate()
        XCTAssertEqual(i.authCalls,0);XCTAssertEqual(s.challengeCalls,0);XCTAssertEqual(s.confirmCalls,0)
    }
    func testCancelledPromptKeepsOriginalRequestAndResumesSameChallenge()async {
        let(s,i,_,m)=fixture();await m.requestConfirmed();let op=m.record!.operation;i.cancel=true
        await m.reauthenticate();let attempt=m.record!.recovery!.attempt
        XCTAssertTrue(m.message.contains("已取消"));XCTAssertNil(m.record?.receipt);XCTAssertEqual(s.confirmCalls,0)
        i.cancel=false;await m.reauthenticate();XCTAssertEqual(m.record?.operation,op)
        XCTAssertEqual(s.challengeCalls,1);XCTAssertEqual(i.authCalls,2);XCTAssertNotNil(m.record?.receipt);XCTAssertEqual(attempt.count,32)
    }
    func testLostConfirmationReplyRestartDoesNotPromptOrReexchange()async {
        let(s,i,j,m)=fixture();await m.requestConfirmed();let op=m.record!.operation
        s.afterConfirm={throw LiteDeletionFailure.unavailable};await m.reauthenticate();XCTAssertNil(m.record?.receipt)
        s.afterConfirm=nil;s.clock=2000
        let next=LiteAccountDeletionStore(service:s,identity:i,journal:j,now:{Date(timeIntervalSince1970:Double(s.clock))})
        await next.retry();XCTAssertEqual(next.record?.receipt?.operation,op)
        XCTAssertEqual(i.authCalls,1);XCTAssertEqual(s.confirmCalls,1);XCTAssertEqual(s.saved.count,1)
    }
    func testUnsentProofRetriesExactTokenWithoutApplePrompt()async {
        let(s,i,_,m)=fixture();await m.requestConfirmed();s.confirmFail=true;await m.reauthenticate()
        XCTAssertEqual(m.record?.recovery?.identityToken,"synthetic-id-token")
        s.confirmFail=false;await m.retry();XCTAssertNotNil(m.record?.receipt)
        XCTAssertEqual(i.authCalls,1);XCTAssertEqual(s.challengeCalls,1);XCTAssertEqual(s.confirmCalls,2)
    }
    func testExpiredChallengeCreatesNewAttemptOnlyOnExplicitButton()async {
        let(s,i,_,m)=fixture();await m.requestConfirmed();i.cancel=true;await m.reauthenticate()
        let old=m.record!.recovery!.attempt,op=m.record!.operation;s.clock+=301;await m.reload();await m.retry()
        XCTAssertEqual(i.authCalls,1);XCTAssertEqual(m.record?.recovery?.attempt,old)
        i.cancel=false;await m.reauthenticate();XCTAssertEqual(s.challengeCalls,2);XCTAssertEqual(m.record?.operation,op);XCTAssertNotNil(m.record?.receipt)
    }
    func testExpiredChallengeWithoutResponseDoesNotTrapAttemptForever()async {
        let(s,i,_,m)=fixture();await m.requestConfirmed();s.challengeExpired=true;await m.reauthenticate()
        XCTAssertNil(m.record?.recovery);XCTAssertEqual(i.authCalls,0)
        s.challengeExpired=false;await m.reauthenticate();XCTAssertNotNil(m.record?.receipt)
    }
    func testRejectedIdentityAllowsNewPromptButNeverNewDeletion()async {
        let(s,i,_,m)=fixture();await m.requestConfirmed();let op=m.record!.operation;s.confirmIdentityFail=true;await m.reauthenticate()
        XCTAssertNil(m.record?.recovery?.identityToken);XCTAssertNil(m.record?.receipt)
        s.confirmIdentityFail=false;await m.reauthenticate();XCTAssertEqual(i.authCalls,2);XCTAssertEqual(m.record?.operation,op)
    }
    func testDoubleTapCannotDuplicateApplePromptOrConfirmation()async {
        let(s,i,_,m)=fixture();await m.requestConfirmed();i.beforeAuth={await m.reauthenticate();await m.retry()}
        await m.reauthenticate();XCTAssertEqual(i.authCalls,1);XCTAssertEqual(s.confirmCalls,1)
    }
    func testDeviceChangeWhilePromptOpenDoesNotTransmitToken()async {
        let(s,i,_,m)=fixture();await m.requestConfirmed();i.beforeAuth={s.owner="other-device"}
        await m.reauthenticate();XCTAssertEqual(s.confirmCalls,0);XCTAssertNil(m.record?.receipt)
    }
    func testFutureOrWrongAccountChallengeRejected()throws {
        let c=LiteDeletionContext(origin:deletionOrigin.absoluteString,installation:deletionOwner,account:deletionAccount)
        let q=LiteDeletionChallenge(v:1,installation:deletionOwner,operation:String(repeating:"d",count:32),reauth_operation:String(repeating:"e",count:32),account_sha256:String(repeating:"f",count:64),nonce:String(repeating:"a",count:64),state:String(repeating:"b",count:64),created_at:1000,expires_at:1300,audience:"com.woundai.lite",purpose:"continue_deletion")
        XCTAssertThrowsError(try q.validate(c,operation:q.operation,attempt:q.reauth_operation))
        XCTAssertEqual(q.appleChallenge.authorization_code_required,false)
    }
    func testOldCheckpointWithoutRecoveryStillDecodes()throws {
        let c=LiteDeletionContext(origin:deletionOrigin.absoluteString,installation:deletionOwner,account:deletionAccount)
        let r=LiteAccountDeletionCheckpoint(version:1,context:c,operation:String(repeating:"e",count:32),createdAt:1000)
        var json=try JSONSerialization.jsonObject(with:JSONEncoder().encode(r)) as! [String:Any];json.removeValue(forKey:"recovery")
        let saved=try JSONDecoder().decode(LiteAccountDeletionCheckpoint.self,from:JSONSerialization.data(withJSONObject:json))
        XCTAssertEqual(saved,r);XCTAssertNoThrow(try saved.validate())
    }
}
