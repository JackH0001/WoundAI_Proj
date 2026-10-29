import XCTest
import SwiftUI
@testable import WoundLite

private let identityNow=1_800_000_000
private actor IdentityJournal:LiteServiceJournal {
    var rows=[String:Data](),writes=0,failAt=0
    func load(_ id:String)->Data?{rows[id]}
    func replace(_ id:String,expected:Data?,with data:Data)throws {
        writes+=1;if writes==failAt{throw LiteAccountFailure.unavailable}
        guard rows[id]==expected else{throw LiteAccountFailure.conflict};rows[id]=data
    }
    func fail(_ n:Int){failAt=n}
    func records()throws->[LiteAccountCheckpoint]{try rows.values.map{try JSONDecoder().decode(LiteAccountCheckpoint.self,from:$0)}}
    func corrupt(){for k in rows.keys{rows[k]=Data("{}".utf8)}}
}
@MainActor private final class IdentityService:LiteAccountIdentityService {
    let origin=URL(string:"https://synthetic.invalid")!
    var owner="synthetic-device",challenges=0,confirms=0,logouts=0,fail=false,logoutFail=false,wrongAck=false,expired=false,ended=false
    var afterConfirm:(()async->Void)?,afterChallenge:(()async->Void)?
    var requireCode=false,recovery=false,received=[LiteAppleIdentity]()
    var enableRenewal=false,clock=identityNow,renewCalls=0,rotations=0,renewFail=false,renewRecovery=false,renewWrongAccount=false
    var renewed:LiteAccountSession?,renewReceipts=[String:LiteAccountSession](),renewOperations=[String]()
    var beforeRenew:(()async throws->Void)?,afterRenew:(()async->Void)?
    func installation()async throws->String{owner}
    func challenge(operation:String)async throws->LiteAccountChallenge {
        challenges+=1;await afterChallenge?()
        var c=LiteAccountChallenge(v:1,installation:owner,operation:operation,nonce:String(repeating:"a",count:64),state:String(repeating:"b",count:64),created_at:identityNow-(expired ? 300:0),expires_at:identityNow+(expired ? 0:300),audience:"com.woundai.lite")
        if requireCode{c.authorization_code_required=true};return c
    }
    func confirm(_ c:LiteAccountChallenge,credential:LiteAppleIdentity)async throws->LiteAccountSession {
        confirms+=1;received.append(credential);if fail{throw LiteAccountFailure.unavailable};await afterConfirm?()
        if recovery{throw LiteAccountFailure.grantRecoveryRequired}
        var r=LiteAccountSession(v:1,state:"signed_in",installation:wrongAck ? "other":c.installation,operation:c.operation,account_sha256:String(repeating:"c",count:64),app_account_token:"10000000-0000-4000-8000-000000000001",expires_at:identityNow+900)
        if enableRenewal{r.renewal_generation=0;r.renewed_at=identityNow;r.family_expires_at=identityNow+30*86400;renewed=r};return r
    }
    func status(_ c:LiteAccountChallenge)async throws->LiteAccountSession {
        if ended{throw LiteAccountFailure.unavailable}
        if enableRenewal,let renewed{return renewed}
        return LiteAccountSession(v:1,state:"signed_in",installation:c.installation,operation:c.operation,account_sha256:String(repeating:"c",count:64),app_account_token:"10000000-0000-4000-8000-000000000001",expires_at:identityNow+900)
    }
    func renew(_ c:LiteAccountChallenge,operation:String,generation:Int)async throws->LiteAccountSession {
        renewCalls+=1;renewOperations.append(operation);try await beforeRenew?()
        if renewRecovery{throw LiteAccountFailure.grantRecoveryRequired}
        guard !ended,enableRenewal else{throw LiteAccountFailure.unavailable}
        let result:LiteAccountSession
        if let prior=renewReceipts[operation]{result=prior}
        else {
            guard renewed?.renewal_generation==generation else{throw LiteAccountFailure.conflict}
            var r=LiteAccountSession(v:1,state:"signed_in",installation:c.installation,operation:c.operation,
                account_sha256:String(repeating:renewWrongAccount ? "d":"c",count:64),app_account_token:"10000000-0000-4000-8000-000000000001",expires_at:clock+900)
            r.renewal_generation=generation+1;r.renewed_at=clock;r.family_expires_at=identityNow+30*86400
            rotations+=1;renewed=r;renewReceipts[operation]=r;result=r
        }
        await afterRenew?();if renewFail{throw LiteAccountFailure.unavailable};return result
    }
    func logout(_ c:LiteAccountChallenge)async throws{logouts+=1;if logoutFail{throw LiteAccountFailure.unavailable};ended=true}
}
@MainActor private final class IdentityProvider:LiteAppleIdentityProvider {
    var calls=0,isAuthorized=true,cancel=false,wrongState=false
    var beforeSignIn:(()async throws->Void)?,onAuthorized:(()async->Void)?
    func signIn(_ c:LiteAccountChallenge)async throws->LiteAppleIdentity {
        try await beforeSignIn?();calls+=1;if cancel{throw LiteAccountFailure.cancelled}
        var result=LiteAppleIdentity(identityToken:"synthetic-not-real-jwt",state:wrongState ? "wrong":c.state,user:"synthetic-apple-user")
        if c.authorization_code_required == true {result.authorizationCode="synthetic-one-use-code"};return result
    }
    func authorized(user:String)async throws->Bool{await onAuthorized?();return isAuthorized}
}
@MainActor private final class IdentityDeletionService:LiteAccountDeletionService {
    let origin=URL(string:"https://synthetic.invalid")!
    var requests=0
    func installation()async throws->String{"synthetic-device"}
    func request(operation:String,installation:String)async throws->LiteDeletionReceipt{requests+=1;throw LiteDeletionFailure.unavailable}
    func status(operation:String,installation:String)async throws->LiteDeletionReceipt{throw LiteDeletionFailure.unavailable}
}
@MainActor final class LiteAccountIdentityTests:XCTestCase {
    private func setup()->(IdentityService,IdentityProvider,IdentityJournal,LiteAccountIdentityStore){
        let s=IdentityService(),p=IdentityProvider(),j=IdentityJournal()
        return(s,p,j,LiteAccountIdentityStore(service:s,provider:p,journal:j,now:{Date(timeIntervalSince1970:Double(identityNow))}))
    }
    private func deletionIntent(_ s:IdentityService,_ j:IdentityJournal)async throws->LiteAccountDeletionCheckpoint {
        let c=LiteDeletionContext(origin:s.origin.absoluteString,installation:s.owner,account:String(repeating:"c",count:64))
        let r=LiteAccountDeletionCheckpoint(version:1,context:c,operation:String(repeating:"d",count:32),createdAt:identityNow)
        try await j.replace(LiteAccountDeletionCheckpoint.key(origin:c.origin,owner:c.installation),expected:nil,with:JSONEncoder().encode(r))
        return r
    }
    private func accountRow(_ s:IdentityService,_ j:IdentityJournal)async throws->LiteAccountCheckpoint {
        let k=String(LiteServiceBytes.hash(Data((s.origin.absoluteString+"\0"+s.owner+"\0account").utf8)).prefix(32))
        let raw=await j.load(k);let data=try XCTUnwrap(raw)
        return try JSONDecoder().decode(LiteAccountCheckpoint.self,from:data)
    }
    func testPersistedDeletionIntentBlocksServiceBeforeSuspensionAndAfterRestart()async throws {
        let(s,p,j,m)=setup();await m.signIn();_=try await deletionIntent(s,j)
        let before=await m.canUseService();XCTAssertFalse(before)
        let restart=LiteAccountIdentityStore(service:s,provider:p,journal:j,now:{Date(timeIntervalSince1970:Double(identityNow))})
        let after=await restart.canUseService();XCTAssertFalse(after)
        await restart.signIn();await restart.retry();await restart.signOut()
        XCTAssertEqual(s.challenges,1);XCTAssertEqual(s.confirms,1);XCTAssertEqual(s.logouts,0)
    }
    func testDeletionSuspensionDropsCredentialPreservesAccountAndOperation()async throws {
        let(s,_,j,m)=setup();await m.signIn();let intent=try await deletionIntent(s,j)
        try await m.suspendForDeletion(intent.context,operation:intent.operation)
        try await m.suspendForDeletion(intent.context,operation:intent.operation)
        let row=try await accountRow(s,j)
        XCTAssertEqual(row.phase,"deleting");XCTAssertNil(row.credential)
        XCTAssertEqual(row.session?.account_sha256,intent.context.account);XCTAssertEqual(row.deletionOperation,intent.operation)
        let allowed=await m.canUseService();XCTAssertFalse(allowed)
    }
    func testSuspensionRequiresExactDurableIntentAndCannotSwitchAccount()async throws {
        let(s,_,j,m)=setup();await m.signIn();let intent=try await deletionIntent(s,j)
        let wrong=LiteDeletionContext(origin:s.origin.absoluteString,installation:s.owner,account:String(repeating:"e",count:64))
        do{try await m.suspendForDeletion(wrong,operation:intent.operation);XCTFail()}catch{}
        do{try await m.suspendForDeletion(intent.context,operation:String(repeating:"f",count:32));XCTFail()}catch{}
        let row=try await accountRow(s,j);XCTAssertEqual(row.phase,"active")
        let allowed=await m.canUseService();XCTAssertFalse(allowed)
    }
    func testCorruptDeletionIntentFailsClosedWithoutResettingIt()async throws {
        let(s,_,j,m)=setup();await m.signIn()
        let k=LiteAccountDeletionCheckpoint.key(origin:s.origin.absoluteString,owner:s.owner),bad=Data("bad-intent".utf8)
        try await j.replace(k,expected:nil,with:bad)
        await m.signIn();await m.retry();let allowed=await m.canUseService()
        let saved=await j.load(k);XCTAssertEqual(saved,bad);XCTAssertFalse(allowed);XCTAssertEqual(s.challenges,1)
    }
    func testDeletionIntentDuringRenewalCannotReactivateService()async throws {
        let s=IdentityService(),p=IdentityProvider(),j=IdentityJournal();s.enableRenewal=true
        let m=LiteAccountIdentityStore(service:s,provider:p,journal:j,now:{Date(timeIntervalSince1970:Double(s.clock))})
        await m.signIn();s.clock=identityNow+850
        s.afterRenew={_=try? await self.deletionIntent(s,j)}
        let allowed=await m.canUseService();XCTAssertFalse(allowed)
        let row=try await accountRow(s,j);XCTAssertEqual(row.phase,"renew")
        let intentKey=LiteAccountDeletionCheckpoint.key(origin:s.origin.absoluteString,owner:s.owner)
        let raw=await j.load(intentKey);let intent=try JSONDecoder().decode(LiteAccountDeletionCheckpoint.self,from:XCTUnwrap(raw))
        try await m.suspendForDeletion(intent.context,operation:intent.operation)
        let suspended=try await accountRow(s,j);XCTAssertEqual(suspended.phase,"deleting")
        XCTAssertNil(suspended.renewalOperation);XCTAssertNil(suspended.credential)
    }
    func testCandidateManagementViewRendersPendingWithoutSendingOrClaimingComplete()async throws {
        let(s,_,j,m)=setup();await m.signIn();let intent=try await deletionIntent(s,j)
        try await m.suspendForDeletion(intent.context,operation:intent.operation)
        let deletion=IdentityDeletionService()
        let view=try LiteAccountManagementView(identity:m,service:deletion)
        let host=UIHostingController(rootView:NavigationStack{view}.environment(\.locale,Locale(identifier:"zh_TW")))
        let window=UIWindow(frame:CGRect(x:0,y:0,width:430,height:932))
        window.rootViewController=host;window.makeKeyAndVisible()
        defer{window.isHidden=true;window.rootViewController=nil}
        host.view.layoutIfNeeded();try await Task.sleep(nanoseconds:300_000_000)
        let image=UIGraphicsImageRenderer(bounds:host.view.bounds).image{_ in host.view.drawHierarchy(in:host.view.bounds,afterScreenUpdates:true)}
        let attachment=XCTAttachment(image:image);attachment.name="account-deletion-pending-candidate";attachment.lifetime = .keepAlways;add(attachment)
        XCTAssertEqual(deletion.requests,0)
        let row=try await accountRow(s,j);XCTAssertEqual(row.phase,"deleting")
        XCTAssertNil(LiteRootView(lidarAvailable:false).accountManagement)
    }
    func testChallengeSavedBeforeApplePromptAndAckBeforeServiceUse()async throws {
        let(s,p,j,m)=setup();p.beforeSignIn={let rows=try await j.records();XCTAssertEqual(rows.first?.phase,"apple")}
        await m.signIn();let rows=try await j.records();XCTAssertEqual(rows.first?.phase,"active")
        let allowed=await m.canUseService();XCTAssertTrue(allowed);XCTAssertEqual(s.confirms,1);XCTAssertTrue(m.message.contains("今日已用次數不變"))
    }
    func testChallengeWriteFailureNeverPromptsApple()async {
        let(s,p,j,m)=setup();await j.fail(1);await m.signIn();XCTAssertEqual(p.calls,0);XCTAssertEqual(s.confirms,0)
    }
    func testCredentialWriteFailureNeverConfirms()async {
        let(s,_,j,m)=setup();await j.fail(2);await m.signIn();XCTAssertEqual(s.confirms,0)
    }
    func testLostReplyRestartRetriesSameOperationWithoutApplePrompt()async throws {
        let(s,p,j,m)=setup();s.fail=true;await m.signIn();let before=try await j.records().first!.challenge.operation
        s.fail=false;let restarted=LiteAccountIdentityStore(service:s,provider:p,journal:j,now:{Date(timeIntervalSince1970:Double(identityNow))})
        await restarted.retry();let after=try await j.records().first!.challenge.operation
        XCTAssertEqual(before,after);XCTAssertEqual(p.calls,1);XCTAssertEqual(s.challenges,1);XCTAssertEqual(s.confirms,2)
    }
    func testAckWriteFailureKeepsConfirmationPending()async throws {
        let(s,p,j,m)=setup();await j.fail(3);await m.signIn();let first=try await j.records().first!
        XCTAssertEqual(first.phase,"confirm");let allowed=await m.canUseService();XCTAssertFalse(allowed)
        await m.retry();XCTAssertEqual(s.confirms,2);XCTAssertEqual(p.calls,1)
    }
    func testPendingConfirmationBlocksNewSignIn()async {
        let(s,p,_,m)=setup();s.fail=true;await m.signIn();await m.signIn();XCTAssertEqual(p.calls,1);XCTAssertEqual(s.challenges,1)
    }
    func testCancelledPromptPreservesNoActiveSession()async throws {
        let(s,p,j,m)=setup();p.cancel=true;await m.signIn();XCTAssertEqual(s.confirms,0)
        XCTAssertTrue(m.message.contains("已取消"));let rows=try await j.records();XCTAssertEqual(rows.first?.phase,"apple")
    }
    func testWrongStateNeverReachesServer()async {
        let(s,p,_,m)=setup();p.wrongState=true;await m.signIn();XCTAssertEqual(s.confirms,0)
    }
    func testWrongOwnerAcknowledgmentNeverActivates()async {
        let(s,_,_,m)=setup();s.wrongAck=true;await m.signIn();let allowed=await m.canUseService();XCTAssertFalse(allowed)
    }
    func testExpiredChallengeNeverPromptsApple()async {
        let(s,p,_,m)=setup();s.expired=true;await m.signIn();XCTAssertEqual(p.calls,0)
    }
    func testInstallationChangeDuringConfirmationCannotActivate()async {
        let(s,_,_,m)=setup();s.afterConfirm={s.owner="changed"};await m.signIn();let allowed=await m.canUseService();XCTAssertFalse(allowed)
    }
    func testLogoutClosesLocallyThenRetriesAfterRestart()async throws {
        let(s,p,j,m)=setup();await m.signIn();s.logoutFail=true;await m.signOut()
        let rows=try await j.records();XCTAssertEqual(rows.first?.phase,"logout");let allowed=await m.canUseService();XCTAssertFalse(allowed)
        let restarted=LiteAccountIdentityStore(service:s,provider:p,journal:j,now:{Date(timeIntervalSince1970:Double(identityNow))})
        let afterRestart=await restarted.canUseService();XCTAssertFalse(afterRestart)
        s.logoutFail=false;await restarted.retry();let ended=try await j.records().first!
        XCTAssertEqual(ended.phase,"ended");XCTAssertNil(ended.credential);XCTAssertNil(ended.session);XCTAssertEqual(s.confirms,1)
    }
    func testLogoutRacingConfirmationNeverReactivates()async throws {
        let(s,_,j,m)=setup();s.afterConfirm={await m.signOut()};await m.signIn()
        let allowed=await m.canUseService();XCTAssertFalse(allowed);let rows=try await j.records();XCTAssertEqual(rows.first?.phase,"ended")
    }
    func testLogoutWhileChallengeIsInFlightCannotStartAppleOrReactivate()async throws {
        let(s,p,j,m)=setup();s.afterChallenge={await m.signOut()};await m.signIn()
        XCTAssertEqual(p.calls,0);XCTAssertEqual(s.confirms,0);XCTAssertEqual(s.logouts,1)
        let rows=try await j.records();XCTAssertTrue(rows.isEmpty)
        let allowed=await m.canUseService();XCTAssertFalse(allowed)
    }
    func testLogoutWhileApplePromptIsOpenDiscardsLateCredential()async throws {
        let(s,p,j,m)=setup();p.beforeSignIn={await m.signOut()};await m.signIn()
        XCTAssertEqual(s.confirms,0);let rows=try await j.records();XCTAssertEqual(rows.first?.phase,"ended")
        XCTAssertNil(rows.first?.credential);let allowed=await m.canUseService();XCTAssertFalse(allowed)
    }
    func testAppleRevocationClosesSession()async {
        let(s,p,_,m)=setup();await m.signIn();p.isAuthorized=false;let allowed=await m.canUseService()
        XCTAssertFalse(allowed);XCTAssertEqual(s.logouts,1)
    }
    func testLogoutDuringCredentialStateCheckCannotReturnTrue()async {
        let(_,p,_,m)=setup();await m.signIn();p.onAuthorized={await m.signOut()};let allowed=await m.canUseService();XCTAssertFalse(allowed)
    }
    func testCorruptJournalNeverResetsIdentity()async {
        let(s,p,j,m)=setup();await m.signIn();await j.corrupt();await m.signIn()
        XCTAssertEqual(s.challenges,1);XCTAssertEqual(p.calls,1);let allowed=await m.canUseService();XCTAssertFalse(allowed)
    }
    func testExpiredSessionRequiresExplicitAuthentication()async {
        let(s,p,j,m)=setup();await m.signIn()
        let expired=LiteAccountIdentityStore(service:s,provider:p,journal:j,now:{Date(timeIntervalSince1970:Double(identityNow+901))})
        let allowed=await expired.canUseService();XCTAssertFalse(allowed);XCTAssertEqual(p.calls,1)
    }
    func testFailedLogoutJournalStillRevokesServerAndRestartCannotTrustStaleSession()async {
        let(s,p,j,m)=setup();await m.signIn();await j.fail(4);await m.signOut();XCTAssertEqual(s.logouts,1)
        let restarted=LiteAccountIdentityStore(service:s,provider:p,journal:j,now:{Date(timeIntervalSince1970:Double(identityNow))})
        let allowed=await restarted.canUseService();XCTAssertFalse(allowed)
    }
    func testAuthorizationCodeSavedBeforeExchangeAndRemovedOnlyAfterAck()async throws {
        let(s,_,j,m)=setup();s.requireCode=true
        s.afterConfirm={let r=try? await j.records().first;XCTAssertEqual(r?.phase,"confirm");XCTAssertEqual(r?.credential?.authorizationCode,"synthetic-one-use-code")}
        await m.signIn();let r=try await j.records().first!;XCTAssertEqual(r.phase,"active");XCTAssertNil(r.credential?.authorizationCode)
        let allowed=await m.canUseService();XCTAssertTrue(allowed)
    }
    func testAuthorizationCodePersistsAcrossLostReplyUntilSameOperationAck()async throws {
        let(s,p,j,m)=setup();s.requireCode=true;s.fail=true;await m.signIn()
        let before=try await j.records().first!;XCTAssertNotNil(before.credential?.authorizationCode)
        s.fail=false;let restarted=LiteAccountIdentityStore(service:s,provider:p,journal:j,now:{Date(timeIntervalSince1970:Double(identityNow))})
        await restarted.retry();let after=try await j.records().first!
        XCTAssertEqual(before.challenge.operation,after.challenge.operation);XCTAssertEqual(s.received[0],s.received[1]);XCTAssertEqual(p.calls,1)
        XCTAssertNil(after.credential?.authorizationCode)
    }
    func testUncertainExchangeDoesNotRetryCodeAndOnlyExplicitSignInStartsNewOperation()async throws {
        let(s,p,j,m)=setup();s.requireCode=true;s.recovery=true;await m.signIn()
        let before=try await j.records().first!;XCTAssertEqual(before.phase,"reauthenticate");XCTAssertNil(before.credential)
        XCTAssertTrue(m.message.contains("不會重送舊授權碼"));await m.retry();XCTAssertEqual(s.confirms,1);XCTAssertEqual(p.calls,1)
        s.recovery=false;await m.signIn();let after=try await j.records().first!
        XCTAssertNotEqual(before.challenge.operation,after.challenge.operation);XCTAssertEqual(after.phase,"active");XCTAssertEqual(p.calls,2)
    }
    func testRecoveryWriteFailureKeepsOriginalIntentUntilDurableRecoveryState()async throws {
        let(s,_,j,m)=setup();s.requireCode=true;s.recovery=true;await j.fail(3);await m.signIn()
        let before=try await j.records().first!;XCTAssertEqual(before.phase,"confirm")
        await m.retry();let after=try await j.records().first!;XCTAssertEqual(after.phase,"reauthenticate")
        await m.retry();XCTAssertEqual(s.confirms,2)
    }
    func testLogoutWinsLateExchangeRecoveryReply()async throws {
        let(s,_,j,m)=setup();s.requireCode=true;s.recovery=true;s.afterConfirm={await m.signOut()}
        await m.signIn();let r=try await j.records().first!;XCTAssertEqual(r.phase,"ended");XCTAssertNil(r.credential)
        let allowed=await m.canUseService();XCTAssertFalse(allowed)
    }
}

@MainActor final class LiteAccountRenewalTests:XCTestCase {
    private func setup()->(IdentityService,IdentityProvider,IdentityJournal,LiteAccountIdentityStore){
        let s=IdentityService(),p=IdentityProvider(),j=IdentityJournal();s.enableRenewal=true
        return(s,p,j,LiteAccountIdentityStore(service:s,provider:p,journal:j,now:{Date(timeIntervalSince1970:Double(s.clock))}))
    }
    func testExpiredSessionRenewsWithoutApplePromptOrConfirmation()async throws {
        let(s,p,j,m)=setup();await m.signIn();s.clock+=901
        let allowed=await m.canUseService();XCTAssertTrue(allowed);XCTAssertEqual(p.calls,1);XCTAssertEqual(s.confirms,1);XCTAssertEqual(s.rotations,1)
        let r=try await j.records().first!;XCTAssertEqual(r.session?.renewal_generation,1);XCTAssertEqual(r.phase,"active")
        XCTAssertTrue(m.message.contains("今日已用次數不變"))
    }
    func testRenewalIntentSavedBeforeDispatch()async throws {
        let(s,_,j,m)=setup();await m.signIn();s.clock+=901
        s.beforeRenew={let r=try await j.records().first!;XCTAssertEqual(r.phase,"renew");XCTAssertEqual(r.renewalGeneration,0);XCTAssertNotNil(r.renewalOperation)}
        let allowed=await m.canUseService();XCTAssertTrue(allowed)
    }
    func testLostReplyRestartKeepsSameOperationAndRotatesOnce()async throws {
        let(s,p,j,m)=setup();await m.signIn();s.clock+=901;s.renewFail=true
        let first=await m.canUseService();XCTAssertFalse(first)
        let pending=try await j.records().first!;XCTAssertEqual(pending.phase,"renew")
        s.renewFail=false;let restarted=LiteAccountIdentityStore(service:s,provider:p,journal:j,now:{Date(timeIntervalSince1970:Double(s.clock))})
        let second=await restarted.canUseService();XCTAssertTrue(second);XCTAssertEqual(s.rotations,1)
        XCTAssertEqual(s.renewOperations,[pending.renewalOperation!,pending.renewalOperation!]);XCTAssertEqual(p.calls,1)
    }
    func testJournalFailureBeforeDispatchNeverRenews()async {
        let(s,_,j,m)=setup();await m.signIn();s.clock+=901;await j.fail(4)
        let allowed=await m.canUseService();XCTAssertFalse(allowed);XCTAssertEqual(s.renewCalls,0)
    }
    func testAckSaveFailureRetriesSameReceipt()async throws {
        let(s,_,j,m)=setup();await m.signIn();s.clock+=901;await j.fail(5)
        let first=await m.canUseService();XCTAssertFalse(first)
        let second=await m.canUseService();XCTAssertTrue(second);XCTAssertEqual(s.rotations,1)
        XCTAssertEqual(s.renewOperations[0],s.renewOperations[1])
    }
    func testLogoutDuringRenewalCannotReactivate()async throws {
        let(s,_,j,m)=setup();await m.signIn();s.clock+=901;s.afterRenew={await m.signOut()}
        let allowed=await m.canUseService();XCTAssertFalse(allowed)
        let r=try await j.records().first!;XCTAssertEqual(r.phase,"ended");XCTAssertNil(r.renewalOperation)
    }
    func testDifferentAccountAcknowledgmentNeverActivates()async throws {
        let(s,_,j,m)=setup();await m.signIn();s.clock+=901;s.renewWrongAccount=true
        let allowed=await m.canUseService();XCTAssertFalse(allowed)
        let r=try await j.records().first!;XCTAssertEqual(r.phase,"renew");XCTAssertEqual(r.session?.renewal_generation,0)
    }
    func testAppleRecoveryNeedsExplicitSignInWithoutRepeatedRenewal()async throws {
        let(s,p,j,m)=setup();await m.signIn();s.clock+=901;s.renewRecovery=true
        let first=await m.canUseService();XCTAssertFalse(first);let second=await m.canUseService();XCTAssertFalse(second)
        let r=try await j.records().first!;XCTAssertEqual(r.phase,"reauthenticate");XCTAssertNil(r.credential)
        XCTAssertEqual(s.renewCalls,1);XCTAssertEqual(p.calls,1)
    }
    func testFamilyExpiryDoesNotSilentlyStartNewLogin()async {
        let(s,p,_,m)=setup();await m.signIn();s.clock+=30*86400
        let allowed=await m.canUseService();XCTAssertFalse(allowed);XCTAssertEqual(s.renewCalls,0);XCTAssertEqual(p.calls,1)
    }
    func testFreshSessionDoesNotRenewOnEveryStatus()async {
        let(s,_,_,m)=setup();await m.signIn()
        for _ in 0..<3{let allowed=await m.canUseService();XCTAssertTrue(allowed)}
        XCTAssertEqual(s.renewCalls,0)
    }
    func testRenewalCannotCarryPartialOrOverlongMetadata()throws {
        let c=LiteAccountChallenge(v:1,installation:"device",operation:String(repeating:"1",count:32),nonce:String(repeating:"a",count:64),state:String(repeating:"b",count:64),created_at:identityNow,expires_at:identityNow+300,audience:"com.woundai.lite")
        var r=LiteAccountSession(v:1,state:"signed_in",installation:c.installation,operation:c.operation,account_sha256:String(repeating:"c",count:64),app_account_token:"10000000-0000-4000-8000-000000000001",expires_at:identityNow+2000)
        r.renewal_generation=1;XCTAssertThrowsError(try r.validate(c))
        r.renewed_at=identityNow;r.family_expires_at=identityNow+30*86400;XCTAssertThrowsError(try r.validate(c))
    }
}

private actor IdentityWire:LiteAuthenticatedTransport {
    var payload=Data(),status=200,alternate:URL?,sent=[URLRequest]()
    func installation()->String{"synthetic-device"}
    func plan(_ d:Data,code:Int=200,url:URL?=nil){payload=d;status=code;alternate=url}
    func requests()->[URLRequest]{sent}
    func send(_ request:URLRequest,installation:String)async throws->(Data,HTTPURLResponse){
        sent.append(request);return(payload,HTTPURLResponse(url:alternate ?? request.url!,statusCode:status,httpVersion:nil,headerFields:[:])!)
    }
}
@MainActor final class LiteAccountHTTPTests:XCTestCase {
    func testRenewalSendsBoundGenerationAndNoAppleCredential()async throws {
        let s=IdentityService();s.enableRenewal=true
        let c=try await s.challenge(operation:String(repeating:"1",count:32)),p=IdentityProvider()
        _=try await s.confirm(c,credential:p.signIn(c));s.clock+=901
        let op=String(repeating:"2",count:32),r=try await s.renew(c,operation:op,generation:0)
        let wire=IdentityWire(),http=try LiteAccountHTTPService(origin:s.origin,transport:wire)
        await wire.plan(try JSONEncoder().encode(r));let received=try await http.renew(c,operation:op,generation:0);XCTAssertEqual(received,r)
        let request=await wire.requests().first!;let body=try JSONSerialization.jsonObject(with:request.httpBody!) as! [String:Any]
        XCTAssertEqual(Set(body.keys),["anon_id","operation","renewal_operation","expected_generation"])
        XCTAssertEqual(body["expected_generation"] as? Int,0);XCTAssertEqual(request.url?.path,"/api/v1/lite/account/renew")
    }
    func testExpiredFamilyCanRecoverWithExplicitSignInButTransportFailureCannot()async throws {
        let s=IdentityService(),c=try await s.challenge(operation:String(repeating:"1",count:32)),wire=IdentityWire()
        let http=try LiteAccountHTTPService(origin:s.origin,transport:wire),payload=Data("{\"error\":\"lite_sign_in_required\",\"keep_local\":true}".utf8)
        for status in [401,503] {
            await wire.plan(payload,code:status)
            do{_=try await http.renew(c,operation:String(repeating:"2",count:32),generation:0);XCTFail()}
            catch{XCTAssertEqual(error as? LiteAccountFailure,status==401 ? .grantRecoveryRequired:.unavailable)}
        }
    }

    func testExactRoutesNeverSendClientSubjectOrTierOrAppleUser()async throws {
        let w=IdentityWire(),s=IdentityService(),op=String(repeating:"1",count:32)
        let c=try await s.challenge(operation:op),p=IdentityProvider(),http=try LiteAccountHTTPService(origin:s.origin,transport:w)
        await w.plan(try JSONEncoder().encode(c));_=try await http.challenge(operation:op)
        let credential=try await p.signIn(c),session=try await s.confirm(c,credential:credential)
        await w.plan(try JSONEncoder().encode(session));_=try await http.confirm(c,credential:credential)
        _=try await http.status(c)
        await w.plan(try JSONSerialization.data(withJSONObject:["v":1,"state":"signed_out","installation":c.installation,"operation":op]));try await http.logout(c)
        let requests=await w.requests();XCTAssertEqual(requests.map{$0.url!.path},["challenge","confirm","status","logout"].map{"/api/v1/lite/account/"+$0})
        let body=try JSONSerialization.jsonObject(with:requests[1].httpBody!) as! [String:String]
        XCTAssertEqual(Set(body.keys),["anon_id","operation","identity_token","state"])
    }
    func testRedirectOrFailureCannotAcceptSession()async throws {
        for code in [200,401] {
            let w=IdentityWire(),s=IdentityService(),op=String(repeating:"1",count:32),c=try await s.challenge(operation:String(repeating:"1",count:32))
            await w.plan(try JSONEncoder().encode(c),code:code,url:code==200 ? URL(string:"https://wrong.invalid"):nil)
            let h=try LiteAccountHTTPService(origin:s.origin,transport:w)
            do{_=try await h.challenge(operation:op);XCTFail()}catch{}
        }
    }
    func testRejectsNonHTTPSAndCredentialOrPathOrigins()throws {
        for origin in ["http://synthetic.invalid","https://x:y@synthetic.invalid","https://synthetic.invalid/path","https://synthetic.invalid?q=x"] {
            XCTAssertThrowsError(try LiteAccountHTTPService(origin:URL(string:origin)!,transport:IdentityWire()))
        }
    }
    func testCodeSentOnlyWhenTrustedChallengeRequiresIt()async throws {
        let w=IdentityWire(),s=IdentityService(),p=IdentityProvider();s.requireCode=true
        var c=try await s.challenge(operation:String(repeating:"1",count:32))
        let credential=try await p.signIn(c),http=try LiteAccountHTTPService(origin:s.origin,transport:w)
        await w.plan(try JSONEncoder().encode(try await s.confirm(c,credential:credential)))
        _=try await http.confirm(c,credential:credential)
        c.authorization_code_required=nil;_=try await http.confirm(c,credential:credential)
        let requests=await w.requests()
        let first=try JSONSerialization.jsonObject(with:requests[0].httpBody!) as! [String:String]
        let second=try JSONSerialization.jsonObject(with:requests[1].httpBody!) as! [String:String]
        XCTAssertEqual(first["authorization_code"],"synthetic-one-use-code");XCTAssertNil(second["authorization_code"])
    }
    func testRequiredCodeMissingOrUnboundedNeverSends()async throws {
        let w=IdentityWire(),s=IdentityService();s.requireCode=true
        let c=try await s.challenge(operation:String(repeating:"1",count:32)),http=try LiteAccountHTTPService(origin:s.origin,transport:w)
        for code in [nil,"",String(repeating:"x",count:4097),"x\ny"] as [String?] {
            let credential=LiteAppleIdentity(identityToken:"synthetic",state:c.state,user:"synthetic",authorizationCode:code)
            do{_=try await http.confirm(c,credential:credential);XCTFail()}catch{}
        }
        let requests=await w.requests();XCTAssertTrue(requests.isEmpty)
    }
    func testOnlyExactRecoveryResponseFromExpectedOriginAllowsExplicitNewSignIn()async throws {
        let w=IdentityWire(),s=IdentityService(),p=IdentityProvider();s.requireCode=true
        let c=try await s.challenge(operation:String(repeating:"1",count:32)),credential=try await p.signIn(c)
        let http=try LiteAccountHTTPService(origin:s.origin,transport:w)
        let body=try JSONSerialization.data(withJSONObject:["error":"lite_apple_grant_recovery_required","keep_local":true,"explicit_sign_in_required":true,"retry_saved_code":false])
        await w.plan(body,code:409)
        do{_=try await http.confirm(c,credential:credential);XCTFail()}catch{XCTAssertEqual(error as? LiteAccountFailure,.grantRecoveryRequired)}
        await w.plan(body,code:409,url:URL(string:"https://other.invalid"))
        do{_=try await http.confirm(c,credential:credential);XCTFail()}catch{XCTAssertEqual(error as? LiteAccountFailure,.unavailable)}
    }
}
