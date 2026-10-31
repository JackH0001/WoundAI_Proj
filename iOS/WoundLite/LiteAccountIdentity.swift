import Foundation
import AuthenticationServices

enum LiteAccountFailure: Error, Equatable { case invalid, unavailable, cancelled, conflict, grantRecoveryRequired }

struct LiteAccountChallenge: Codable, Equatable {
    let v: Int, installation: String, operation: String, nonce: String, state: String
    let created_at: Int, expires_at: Int, audience: String
    var authorization_code_required: Bool? = nil
    func validate() throws {
        guard v == 1, LiteAttestClient.matches(installation,"[A-Za-z0-9_-]{1,128}"),
              LiteServiceBytes.hex(operation,32), LiteServiceBytes.hex(nonce), LiteServiceBytes.hex(state),
              created_at >= 0, expires_at-created_at == 300, audience == "com.woundai.lite" else { throw LiteAccountFailure.invalid }
    }
}

struct LiteAccountSession: Codable, Equatable {
    let v: Int, state: String, installation: String, operation: String, account_sha256: String, app_account_token: String
    let expires_at: Int
    var renewal_generation:Int?=nil,renewed_at:Int?=nil,family_expires_at:Int?=nil
    func validate(_ c:LiteAccountChallenge) throws {
        try c.validate()
        guard v == 1, state == "signed_in", installation == c.installation, operation == c.operation,
              LiteServiceBytes.hex(account_sha256), let token=UUID(uuidString:app_account_token),
              token.uuidString.lowercased()==app_account_token, token != UUID(uuidString:"00000000-0000-0000-0000-000000000000"),
              expires_at > c.created_at else { throw LiteAccountFailure.invalid }
        if let generation=renewal_generation,let renewed=renewed_at,let family=family_expires_at {
            guard (0...10000).contains(generation),renewed>=c.created_at,family==c.created_at+30*86400,
                  expires_at<=family,expires_at<=renewed+(generation==0 ? 1200:900),expires_at>renewed else{throw LiteAccountFailure.invalid}
        } else {
            guard renewal_generation==nil,renewed_at==nil,family_expires_at==nil,expires_at<=c.expires_at+900 else{throw LiteAccountFailure.invalid}
        }
    }
}

struct LiteAppleIdentity: Codable, Equatable {
    let identityToken: String, state: String, user: String
    var authorizationCode: String? = nil
    func validate(_ c:LiteAccountChallenge, requireAuthorizationCode:Bool = true) throws {
        guard state==c.state, (1...16000).contains(identityToken.utf8.count),
              (1...255).contains(user.utf8.count) else { throw LiteAccountFailure.invalid }
        if let code=authorizationCode {
            guard (1...4096).contains(code.utf8.count),code.unicodeScalars.allSatisfy({(32...126).contains($0.value)}) else {throw LiteAccountFailure.invalid}
        }
        if requireAuthorizationCode && c.authorization_code_required == true && authorizationCode == nil {throw LiteAccountFailure.invalid}
    }
}

@MainActor protocol LiteAccountIdentityService {
    var origin: URL { get }
    func installation() async throws -> String
    func challenge(operation:String) async throws -> LiteAccountChallenge
    func confirm(_ challenge:LiteAccountChallenge,credential:LiteAppleIdentity) async throws -> LiteAccountSession
    func status(_ challenge:LiteAccountChallenge) async throws -> LiteAccountSession
    func logout(_ challenge:LiteAccountChallenge) async throws
    func renew(_ challenge:LiteAccountChallenge,operation:String,generation:Int) async throws ->LiteAccountSession
}
extension LiteAccountIdentityService {
    func renew(_ challenge:LiteAccountChallenge,operation:String,generation:Int) async throws ->LiteAccountSession {throw LiteAccountFailure.unavailable}
}

@MainActor protocol LiteAppleIdentityProvider {
    func signIn(_ challenge:LiteAccountChallenge) async throws -> LiteAppleIdentity
    func authorized(user:String) async throws -> Bool
}

struct LiteSignedAccountTransport:LiteAuthenticatedTransport {
    let client:LiteAttestClient
    let allowService:@Sendable () async ->Bool
    func installation() async throws ->String {
        guard await allowService(), let owner=try await client.existingInstallation() else { throw LiteAccountFailure.unavailable }
        return owner
    }
    func send(_ request:URLRequest,installation:String) async throws ->(Data,HTTPURLResponse) {
        guard try await self.installation()==installation,
              ["challenge","confirm","status","logout","renew"].map({"/api/v1/lite/account/"+$0}).contains(request.url?.path ?? "") else { throw LiteAccountFailure.invalid }
        return try await client.send(request,installation:installation,requestID:UUID(),allowSending:allowService)
    }
}

@MainActor final class LiteAccountHTTPService:LiteAccountIdentityService {
    let origin:URL, transport:any LiteAuthenticatedTransport
    init(origin:URL,transport:any LiteAuthenticatedTransport) throws {
        guard origin.scheme=="https",origin.host != nil,origin.user==nil,origin.password==nil,
              origin.query==nil,origin.fragment==nil,["","/"].contains(origin.path) else { throw LiteAccountFailure.invalid }
        self.origin=origin;self.transport=transport
    }
    func installation() async throws ->String {try await transport.installation()}
    private func send(_ kind:String,owner:String,body:[String:Any])async throws ->Data {
        guard try await installation()==owner else {throw LiteAccountFailure.conflict}
        var r=URLRequest(url:origin.appendingPathComponent("api/v1/lite/account/"+kind))
        r.httpMethod="POST";r.setValue("application/json",forHTTPHeaderField:"Content-Type");r.httpBody=try JSONSerialization.data(withJSONObject:body,options:[.sortedKeys]);r.timeoutInterval=30
        let(data,response)=try await transport.send(r,installation:owner)
        guard try await installation()==owner,data.count<=20000,
              response.url==r.url else {throw LiteAccountFailure.unavailable}
        if ["confirm","renew"].contains(kind),response.statusCode==409 {
            struct Recovery:Decodable {let error:String,keep_local:Bool,explicit_sign_in_required:Bool,retry_saved_code:Bool}
            if let r=try? JSONDecoder().decode(Recovery.self,from:data),r.error=="lite_apple_grant_recovery_required",
               r.keep_local,r.explicit_sign_in_required,!r.retry_saved_code {throw LiteAccountFailure.grantRecoveryRequired}
        }
        if kind=="renew",response.statusCode==401 {
            struct Expired:Decodable {let error:String,keep_local:Bool}
            if let r=try? JSONDecoder().decode(Expired.self,from:data),r.error=="lite_sign_in_required",r.keep_local {
                throw LiteAccountFailure.grantRecoveryRequired
            }
        }
        guard response.statusCode==200 else {throw LiteAccountFailure.unavailable}
        return data
    }
    func challenge(operation:String)async throws ->LiteAccountChallenge {
        let owner=try await installation()
        let c=try JSONDecoder().decode(LiteAccountChallenge.self,from:await send("challenge",owner:owner,body:["anon_id":owner,"operation":operation]))
        try c.validate();guard c.installation==owner,c.operation==operation else {throw LiteAccountFailure.conflict};return c
    }
    func confirm(_ c:LiteAccountChallenge,credential:LiteAppleIdentity)async throws ->LiteAccountSession {
        try c.validate();try credential.validate(c)
        var body=["anon_id":c.installation,"operation":c.operation,"state":credential.state,"identity_token":credential.identityToken]
        if c.authorization_code_required == true {body["authorization_code"]=credential.authorizationCode}
        let r=try JSONDecoder().decode(LiteAccountSession.self,from:await send("confirm",owner:c.installation,
            body:body))
        try r.validate(c);return r
    }
    func logout(_ c:LiteAccountChallenge)async throws {
        struct Receipt:Decodable {let v:Int,state:String,installation:String,operation:String}
        let r=try JSONDecoder().decode(Receipt.self,from:await send("logout",owner:c.installation,body:["anon_id":c.installation,"operation":c.operation]))
        guard r.v==1,r.state=="signed_out",r.installation==c.installation,r.operation==c.operation else {throw LiteAccountFailure.conflict}
    }
    func renew(_ c:LiteAccountChallenge,operation:String,generation:Int)async throws ->LiteAccountSession {
        try c.validate();guard LiteServiceBytes.hex(operation,32),(0..<10000).contains(generation) else{throw LiteAccountFailure.invalid}
        let r=try JSONDecoder().decode(LiteAccountSession.self,from:await send("renew",owner:c.installation,
            body:["anon_id":c.installation,"operation":c.operation,"renewal_operation":operation,"expected_generation":generation]))
        try r.validate(c);guard r.renewal_generation==generation+1 else{throw LiteAccountFailure.conflict};return r
    }
    func status(_ c:LiteAccountChallenge)async throws ->LiteAccountSession {
        let r=try JSONDecoder().decode(LiteAccountSession.self,from:await send("status",owner:c.installation,body:["anon_id":c.installation]))
        try r.validate(c);return r
    }
}

/// Explicit native provider: no scopes for name/email, no token decoding as identity.
@MainActor final class LiteAppleSignInProvider:NSObject,LiteAppleIdentityProvider,ASAuthorizationControllerDelegate,ASAuthorizationControllerPresentationContextProviding {
    private let anchor:ASPresentationAnchor
    private var continuation:CheckedContinuation<LiteAppleIdentity,Error>?
    private var challenge:LiteAccountChallenge?
    private var controller:ASAuthorizationController?
    init(anchor:ASPresentationAnchor){self.anchor=anchor}
    func signIn(_ c:LiteAccountChallenge)async throws ->LiteAppleIdentity {
        try c.validate();guard continuation==nil else {throw LiteAccountFailure.conflict}
        return try await withCheckedThrowingContinuation { continuation in
            self.continuation=continuation;self.challenge=c
            let request=ASAuthorizationAppleIDProvider().createRequest()
            request.nonce=c.nonce;request.state=c.state;request.requestedScopes=[]
            let controller=ASAuthorizationController(authorizationRequests:[request])
            self.controller=controller;controller.delegate=self;controller.presentationContextProvider=self;controller.performRequests()
        }
    }
    func presentationAnchor(for controller:ASAuthorizationController)->ASPresentationAnchor {anchor}
    func authorizationController(controller:ASAuthorizationController,didCompleteWithAuthorization authorization:ASAuthorization) {
        do {
            guard let c=challenge,let apple=authorization.credential as? ASAuthorizationAppleIDCredential,
                  let data=apple.identityToken,let token=String(data:data,encoding:.utf8),let state=apple.state else {throw LiteAccountFailure.invalid}
            var result=LiteAppleIdentity(identityToken:token,state:state,user:apple.user)
            if c.authorization_code_required == true,let code=apple.authorizationCode {result.authorizationCode=String(data:code,encoding:.utf8)}
            try result.validate(c)
            finish(.success(result))
        } catch {finish(.failure(error))}
    }
    func authorizationController(controller:ASAuthorizationController,didCompleteWithError error:Error) {
        finish(.failure((error as? ASAuthorizationError)?.code == .canceled ? LiteAccountFailure.cancelled:error))
    }
    private func finish(_ result:Result<LiteAppleIdentity,Error>) {
        let c=continuation;continuation=nil;challenge=nil;controller=nil;c?.resume(with:result)
    }
    func authorized(user:String)async throws ->Bool {
        try await withCheckedThrowingContinuation { continuation in
            ASAuthorizationAppleIDProvider().getCredentialState(forUserID:user) { state,error in
                if let error{continuation.resume(throwing:error)}else{continuation.resume(returning:state == .authorized)}
            }
        }
    }
}

struct LiteAccountCheckpoint:Codable,Equatable {
    let version:Int,origin:String,challenge:LiteAccountChallenge
    var phase:String
    var credential:LiteAppleIdentity?=nil,session:LiteAccountSession?=nil
    var renewalOperation:String?=nil,renewalGeneration:Int?=nil
    var deletionOperation:String?=nil
    func validate()throws {
        try challenge.validate()
        guard version==1,URL(string:origin)?.scheme=="https",["apple","confirm","active","logout","ended","reauthenticate","renew","deleting"].contains(phase) else {throw LiteAccountFailure.invalid}
        if phase=="deleting" {
            guard let op=deletionOperation,LiteServiceBytes.hex(op,32),session != nil,credential==nil else{throw LiteAccountFailure.invalid}
        } else if deletionOperation != nil {throw LiteAccountFailure.invalid}
        if let credential{try credential.validate(challenge,requireAuthorizationCode:phase=="confirm")}
        if phase=="reauthenticate" && (credential != nil || session != nil) {throw LiteAccountFailure.invalid}
        if let session{try session.validate(challenge)}
        if phase=="confirm" && credential==nil{throw LiteAccountFailure.invalid}
        if ["active","renew"].contains(phase) && (credential==nil || session==nil){throw LiteAccountFailure.invalid}
        if phase=="renew" {
            guard let op=renewalOperation,LiteServiceBytes.hex(op,32),let n=renewalGeneration,
                  (0..<10000).contains(n),session?.renewal_generation==n else{throw LiteAccountFailure.invalid}
        } else if renewalOperation != nil || renewalGeneration != nil {throw LiteAccountFailure.invalid}
    }
}

/// Candidate only: injection and a protected journal required; not a production tab.
@MainActor final class LiteAccountIdentityStore:ObservableObject,LiteDeletionIdentity {
    @Published private(set) var message="服務帳號登入與研究同意分開處理。"
    @Published private(set) var busy=false
    let service:any LiteAccountIdentityService,provider:any LiteAppleIdentityProvider,journal:any LiteServiceJournal
    private var blocked=false, generation=0
    let now:()->Date
    init(service:any LiteAccountIdentityService,provider:any LiteAppleIdentityProvider,journal:any LiteServiceJournal,now:@escaping ()->Date=Date.init){
        self.service=service;self.provider=provider;self.journal=journal;self.now=now
    }
    private func key(_ owner:String)->String{String(LiteServiceBytes.hash(Data((service.origin.absoluteString+"\0"+owner+"\0account").utf8)).prefix(32))}
    private func hasDeletionIntent(_ owner:String)async throws->Bool {
        try await journal.load(LiteAccountDeletionCheckpoint.key(origin:service.origin.absoluteString,owner:owner)) != nil
    }
    func deletionContext()async throws->LiteDeletionContext {
        guard await canUseService() else{throw LiteAccountFailure.unavailable}
        let owner=try await service.installation();let(_,r)=try await read(owner)
        guard let r,r.phase=="active",let account=r.session?.account_sha256 else{throw LiteAccountFailure.invalid}
        return LiteDeletionContext(origin:service.origin.absoluteString,installation:owner,account:account)
    }
    func suspendForDeletion(_ context:LiteDeletionContext,operation:String)async throws {
        try context.validate();guard context.origin==service.origin.absoluteString,LiteServiceBytes.hex(operation,32),
            try await service.installation()==context.installation else{throw LiteAccountFailure.conflict}
        let(owner,op)=(context.installation,operation)
        guard let intent=try await journal.load(LiteAccountDeletionCheckpoint.key(origin:context.origin,owner:owner)) else{throw LiteAccountFailure.invalid}
        let deletion=try JSONDecoder().decode(LiteAccountDeletionCheckpoint.self,from:intent);try deletion.validate()
        guard deletion.context==context,deletion.operation==op else{throw LiteAccountFailure.conflict}
        generation+=1;blocked=true
        let(old,r)=try await read(owner)
        guard let r,r.session?.account_sha256==context.account else{throw LiteAccountFailure.conflict}
        if r.phase=="deleting" {guard r.deletionOperation==op else{throw LiteAccountFailure.conflict};return}
        guard ["active","renew"].contains(r.phase) else{throw LiteAccountFailure.conflict}
        var next=r;next.phase="deleting";next.deletionOperation=op;next.credential=nil
        next.renewalOperation=nil;next.renewalGeneration=nil
        _=try await save(owner,old,next)
        message="刪帳要求處理中；一般服務已暫停，請至刪帳頁核對原進度。手機紀錄保留。"
    }
    func authenticateDeletion(_ challenge:LiteDeletionChallenge,context:LiteDeletionContext)async throws->String {
        guard !busy else{throw LiteAccountFailure.conflict};busy=true;defer{busy=false}
        try challenge.validate(context,operation:challenge.operation,attempt:challenge.reauth_operation)
        try await suspendForDeletion(context,operation:challenge.operation)
        let started=generation
        let key=LiteAccountDeletionCheckpoint.key(origin:context.origin,owner:context.installation)
        guard let saved=try await journal.load(key) else{throw LiteAccountFailure.conflict}
        let intent=try JSONDecoder().decode(LiteAccountDeletionCheckpoint.self,from:saved);try intent.validate()
        guard intent.context==context,intent.operation==challenge.operation,intent.receipt==nil,
              intent.recovery?.challenge==challenge,challenge.expires_at>Int(now().timeIntervalSince1970) else{throw LiteAccountFailure.conflict}
        let credential=try await provider.signIn(challenge.appleChallenge)
        try credential.validate(challenge.appleChallenge,requireAuthorizationCode:false)
        guard generation==started,try await service.installation()==context.installation,
              try await journal.load(key)==saved else{throw LiteAccountFailure.conflict}
        // Return only the ID token. Never exchange/persist a new authorization
        // code, create a refresh grant, or change the ordinary session state.
        return credential.identityToken
    }
    private func read(_ owner:String)async throws ->(Data?,LiteAccountCheckpoint?) {
        let data=try await journal.load(key(owner));guard let data else{return(nil,nil)}
        let r=try JSONDecoder().decode(LiteAccountCheckpoint.self,from:data);try r.validate()
        guard r.origin==service.origin.absoluteString,r.challenge.installation==owner else{throw LiteAccountFailure.conflict}
        return(data,r)
    }
    private func save(_ owner:String,_ old:Data?,_ record:LiteAccountCheckpoint)async throws ->Data {
        try record.validate();guard try await service.installation()==owner else{throw LiteAccountFailure.conflict}
        let d=try JSONEncoder().encode(record);try await journal.replace(key(owner),expected:old,with:d);return d
    }
    private func confirm(_ owner:String,_ old:Data,_ record:LiteAccountCheckpoint)async throws {
        guard let credential=record.credential,record.phase=="confirm" else{throw LiteAccountFailure.invalid}
        let started=generation
        let session:LiteAccountSession
        do {session=try await service.confirm(record.challenge,credential:credential)}
        catch LiteAccountFailure.grantRecoveryRequired {
            guard generation==started else{throw LiteAccountFailure.conflict}
            var next=record;next.phase="reauthenticate";next.credential=nil;next.session=nil
            _=try await save(owner,old,next)
            guard generation==started else{throw LiteAccountFailure.conflict}
            message="Apple 登入回覆無法確認，請按重新登入。不會重送舊授權碼；原紀錄與額度保留。"
            return
        }
        try session.validate(record.challenge)
        guard generation==started,try await !hasDeletionIntent(owner) else{throw LiteAccountFailure.conflict}
        guard session.expires_at>Int(now().timeIntervalSince1970) else{throw LiteAccountFailure.invalid}
        var next=record;next.phase="active";next.session=session
        next.credential?.authorizationCode=nil // Single-use code is no longer needed after acknowledgment.
        _=try await save(owner,old,next) // logout racing with the response makes CAS fail
        guard generation==started else{throw LiteAccountFailure.conflict};blocked=false
        message="服務帳號已確認；原紀錄、研究同意及今日已用次數不變。"
    }
    func signIn()async {
        guard !busy else{return};busy=true;defer{busy=false}
        generation+=1;let started=generation;blocked=true
        do {
            let owner=try await service.installation();let(old,previous)=try await read(owner)
            guard try await !hasDeletionIntent(owner),previous?.phase != "deleting" else{message="刪帳要求處理中；請核對原刪帳進度，不能重新開啟登入。";return}
            guard generation==started else{throw LiteAccountFailure.conflict}
            guard (previous?.phase != "confirm" || (previous?.challenge.expires_at ?? 0)<=Int(now().timeIntervalSince1970)),previous?.phase != "logout",previous?.phase != "renew" else{message="上次登入或登出尚未確認，請先按重試；不用重新購買。";return}
            let c=try await service.challenge(operation:UUID().uuidString.replacingOccurrences(of:"-",with:"").lowercased());try c.validate()
            guard generation==started else{try? await service.logout(c);throw LiteAccountFailure.conflict}
            guard c.installation==owner,c.created_at<=Int(now().timeIntervalSince1970),c.expires_at>Int(now().timeIntervalSince1970) else{throw LiteAccountFailure.invalid}
            var r=LiteAccountCheckpoint(version:1,origin:service.origin.absoluteString,challenge:c,phase:"apple")
            var saved=try await save(owner,old,r) // persist before displaying Apple authorization
            guard generation==started else{throw LiteAccountFailure.conflict}
            var credential=try await provider.signIn(c);try credential.validate(c)
            if c.authorization_code_required != true {credential.authorizationCode=nil}
            guard generation==started else{throw LiteAccountFailure.conflict}
            r.credential=credential;r.phase="confirm";saved=try await save(owner,saved,r)
            try await confirm(owner,saved,r)
        } catch {message=(error as? LiteAccountFailure) == .cancelled ? "已取消登入；原紀錄與研究同意不變。":"登入尚未確認，請保留原紀錄並重試；未移轉帳號或增加額度。"}
    }
    func retry()async {
        guard !busy else{return};busy=true;defer{busy=false}
        do {
            let owner=try await service.installation();let(old,r)=try await read(owner)
            guard try await !hasDeletionIntent(owner),r?.phase != "deleting" else{message="請至刪帳頁核對原進度；不會重送登入或續期。";return}
            guard let old,let r else{return}
            if r.phase=="confirm"{try await confirm(owner,old,r)}
            else if r.phase=="logout"{try await finishLogout(owner,old,r)}
            else if r.phase=="renew"{try await renew(owner,old,r)}
            else if r.phase=="reauthenticate"{message="請按重新登入以取得新的 Apple 授權；不會重送舊授權碼。"}
            else{message="目前沒有待確認的登入操作。"}
        } catch {message="帳號核對尚未完成，請稍後重試；本機紀錄仍保留。"}
    }
    private func finishLogout(_ owner:String,_ old:Data,_ r:LiteAccountCheckpoint)async throws {
        try await service.logout(r.challenge);var ended=r;ended.phase="ended";ended.credential=nil;ended.session=nil
        _=try await save(owner,old,ended);message="已登出服務帳號；未刪除紀錄，也未撤回研究資料。"
    }
    func signOut()async {
        generation+=1;blocked=true
        do {
            let owner=try await service.installation();let(old,r)=try await read(owner)
            guard try await !hasDeletionIntent(owner),r?.phase != "deleting" else{message="刪帳要求處理中；請核對原刪帳進度。";return}
            guard let r else{return};var next=r;next.phase="logout";next.renewalOperation=nil;next.renewalGeneration=nil
            let saved:Data
            do {saved=try await save(owner,old,next)} // local service closes before network
            catch {
                // Even when local storage fails, attempt server revocation. On
                // restart status must still be confirmed before service use.
                try? await service.logout(r.challenge)
                throw error
            }
            try await finishLogout(owner,saved,next)
        } catch {message="本次服務連線已暫停；登出尚未完成，請按重試。原紀錄保留。"}
    }
    private func renew(_ owner:String,_ old:Data,_ record:LiteAccountCheckpoint)async throws {
        guard record.phase=="renew",let operation=record.renewalOperation,let n=record.renewalGeneration,
              let previous=record.session else{throw LiteAccountFailure.invalid}
        let started=generation
        let session:LiteAccountSession
        do{session=try await service.renew(record.challenge,operation:operation,generation:n)}
        catch LiteAccountFailure.grantRecoveryRequired {
            guard generation==started else{throw LiteAccountFailure.conflict}
            var next=record;next.phase="reauthenticate";next.credential=nil;next.session=nil
            next.renewalOperation=nil;next.renewalGeneration=nil
            _=try await save(owner,old,next)
            guard generation==started else{throw LiteAccountFailure.conflict}
            message="Apple 授權需要重新確認，請按重新登入。原紀錄與已用次數保留。"
            throw LiteAccountFailure.grantRecoveryRequired
        }
        try session.validate(record.challenge)
        guard generation==started,try await !hasDeletionIntent(owner),session.renewal_generation==n+1,
              (session.renewed_at ?? Int.max)<=Int(now().timeIntervalSince1970),
              session.account_sha256==previous.account_sha256,session.app_account_token==previous.app_account_token,
              session.family_expires_at==previous.family_expires_at else{throw LiteAccountFailure.conflict}
        var next=record;next.phase="active";next.session=session;next.renewalOperation=nil;next.renewalGeneration=nil
        _=try await save(owner,old,next)
        guard generation==started else{throw LiteAccountFailure.conflict}
        message="服務連線已更新；原紀錄、研究同意與今日已用次數不變。"
    }
    func canUseService()async ->Bool {
        guard !blocked,!busy else{return false}
        busy=true;defer{busy=false};let started=generation
        do {
            let owner=try await service.installation();var(old,r)=try await read(owner)
            guard try await !hasDeletionIntent(owner) else{return false}
            guard let record=r,["active","renew"].contains(record.phase),let credential=record.credential else{return false}
            guard try await provider.authorized(user:credential.user) else{await signOut();return false}
            guard !blocked,generation==started else{return false}
            if record.phase=="renew",let saved=old {try await renew(owner,saved,record);(old,r)=try await read(owner)}
            guard var current=r,current.phase=="active",let session=current.session else{return false}
            let time=Int(now().timeIntervalSince1970)
            if let n=session.renewal_generation,time>=session.expires_at-60,time<(session.family_expires_at ?? 0),let saved=old {
                current.phase="renew";current.renewalGeneration=n
                current.renewalOperation=UUID().uuidString.replacingOccurrences(of:"-",with:"").lowercased()
                let pending=try await save(owner,saved,current) // persist before dispatch; retries reuse this operation
                guard !blocked,generation==started else{return false}
                try await renew(owner,pending,current);(old,r)=try await read(owner)
            }
            guard let final=r,final.phase=="active",let confirmed=final.session,
                  confirmed.expires_at>Int(now().timeIntervalSince1970) else{return false}
            guard try await service.status(final.challenge)==confirmed else{return false}
            let saved=try await journal.load(key(owner));let currentOwner=try await service.installation()
            let deletionPending=try await hasDeletionIntent(owner)
            return !blocked && generation==started && currentOwner==owner && saved==old && !deletionPending
        } catch {return false}
    }
}
