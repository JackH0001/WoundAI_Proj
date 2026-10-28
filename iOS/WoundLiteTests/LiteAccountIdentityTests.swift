import XCTest
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
    func installation()async throws->String{owner}
    func challenge(operation:String)async throws->LiteAccountChallenge {
        challenges+=1;await afterChallenge?()
        var c=LiteAccountChallenge(v:1,installation:owner,operation:operation,nonce:String(repeating:"a",count:64),state:String(repeating:"b",count:64),created_at:identityNow-(expired ? 300:0),expires_at:identityNow+(expired ? 0:300),audience:"com.woundai.lite")
        if requireCode{c.authorization_code_required=true};return c
    }
    func confirm(_ c:LiteAccountChallenge,credential:LiteAppleIdentity)async throws->LiteAccountSession {
        confirms+=1;received.append(credential);if fail{throw LiteAccountFailure.unavailable};await afterConfirm?()
        if recovery{throw LiteAccountFailure.grantRecoveryRequired}
        return LiteAccountSession(v:1,state:"signed_in",installation:wrongAck ? "other":c.installation,operation:c.operation,account_sha256:String(repeating:"c",count:64),app_account_token:"10000000-0000-4000-8000-000000000001",expires_at:identityNow+900)
    }
    func status(_ c:LiteAccountChallenge)async throws->LiteAccountSession {
        if ended{throw LiteAccountFailure.unavailable}
        return LiteAccountSession(v:1,state:"signed_in",installation:c.installation,operation:c.operation,account_sha256:String(repeating:"c",count:64),app_account_token:"10000000-0000-4000-8000-000000000001",expires_at:identityNow+900)
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
@MainActor final class LiteAccountIdentityTests:XCTestCase {
    private func setup()->(IdentityService,IdentityProvider,IdentityJournal,LiteAccountIdentityStore){
        let s=IdentityService(),p=IdentityProvider(),j=IdentityJournal()
        return(s,p,j,LiteAccountIdentityStore(service:s,provider:p,journal:j,now:{Date(timeIntervalSince1970:Double(identityNow))}))
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
