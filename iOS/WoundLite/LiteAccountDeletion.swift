import Foundation

enum LiteDeletionFailure: Error { case invalid, unavailable, identity, conflict, expired }

struct LiteDeletionChallenge: Codable, Equatable {
    let v:Int,installation:String,operation:String,reauth_operation:String,account_sha256:String
    let nonce:String,state:String,created_at:Int,expires_at:Int,audience:String,purpose:String
    var appleChallenge:LiteAccountChallenge {
        LiteAccountChallenge(v:v,installation:installation,operation:reauth_operation,nonce:nonce,state:state,
            created_at:created_at,expires_at:expires_at,audience:audience,authorization_code_required:false)
    }
    func validate(_ context:LiteDeletionContext,operation:String,attempt:String)throws {
        try context.validate();try appleChallenge.validate()
        guard purpose=="continue_deletion",installation==context.installation,account_sha256==context.account,
              self.operation==operation,LiteServiceBytes.hex(operation,32),reauth_operation==attempt
        else{throw LiteDeletionFailure.conflict}
    }
}
struct LiteDeletionRecovery:Codable,Equatable {
    let attempt:String
    var challenge:LiteDeletionChallenge?,identityToken:String?
}

struct LiteDeletionContext: Codable, Equatable {
    let origin: String, installation: String, account: String
    func validate() throws {
        guard let u=URL(string:origin),u.scheme=="https",u.host != nil,u.user==nil,u.password==nil,
              u.query==nil,u.fragment==nil,["","/"].contains(u.path),
              LiteAttestClient.matches(installation,"[A-Za-z0-9_-]{1,128}"),LiteServiceBytes.hex(account)
        else {throw LiteDeletionFailure.invalid}
    }
}

/// Explicitly incomplete catalog: terminal receipts for individual lanes never
/// imply that historical media, research data or the entire account was erased.
struct LiteDispositionProgress: Codable, Equatable {
    static let catalog="490147fc0741120d0f36252d2d6c03f15da67e1b89bd3bc217c6d1b6848fc891"
    static let pending=["historical_versions_backups","research_association_disposition",
                        "identity_subscription_retention","live_provider_device_acceptance"]
    let v:Int,catalog_sha256:String,revision:Int,service_total:Int,service_sealed:Int
    let service_state:String,grant_key_state:String,blocked_lanes:[String],pending_lanes:[String]
    func validate(apple:String,previous:Self?=nil)throws {
        let serviceOrder=["not_started":0,"pending":1,"current_sealed":2]
        let keyOrder=["not_started":0,"destroy_scheduled":1,"unrecoverable":2]
        guard v==1,catalog_sha256==Self.catalog,(0...1000000).contains(revision),
              (0...100000).contains(service_total),(0...service_total).contains(service_sealed),
              let sr=serviceOrder[service_state],let kr=keyOrder[grant_key_state],
              pending_lanes==Self.pending,blocked_lanes==Array(Set(blocked_lanes)).sorted(),
              blocked_lanes.allSatisfy({["apple_revocation","service_results","grant_key"].contains($0)}),
              service_state != "not_started" || service_sealed==0,
              service_state != "pending" || service_sealed<service_total,
              service_state != "current_sealed" || service_sealed==service_total,
              grant_key_state=="not_started" || apple=="confirmed",
              revision>0 || (sr==0 && kr==0 && blocked_lanes.isEmpty)
        else{throw LiteDeletionFailure.invalid}
        if let p=previous {
            guard let ps=serviceOrder[p.service_state],let pk=keyOrder[p.grant_key_state],
                  catalog_sha256==p.catalog_sha256,service_total==p.service_total,revision>=p.revision,
                  service_sealed>=p.service_sealed,sr>=ps,kr>=pk,
                  revision>p.revision || self==p else{throw LiteDeletionFailure.conflict}
        }
    }
    var serviceText:String {
        service_state=="current_sealed" ? "目前結果已封存（\(service_sealed)/\(service_total)）" :
        service_state=="not_started" ? "尚未開始" : "處理中（\(service_sealed)/\(service_total)）"
    }
    var keyText:String {
        grant_key_state=="unrecoverable" ? "已確認無法解密" :
        grant_key_state=="destroy_scheduled" ? "已排程，等待銷毀確認" : "尚未開始"
    }
}

/// v1 remains readable; v2 reports individually verified progress only.
struct LiteDeletionReceipt: Codable, Equatable {
    let v:Int, state:String, operation:String, installation:String, requested_at:Int
    let inventory_sha256:String, grant_total:Int, revocation_scan:Int, grants_examined:Int, grants_revoked:Int
    let apple_revocation:String, data_disposition:String
    let account_deleted:Bool, subscription_cancelled:Bool, local_records_preserved:Bool
    var disposition:LiteDispositionProgress?=nil
    func validate(owner:String,operation:String,previous:Self?=nil) throws {
        guard [1,2].contains(v),state=="account_deletion_pending",self.operation==operation,LiteServiceBytes.hex(operation,32),
              installation==owner,requested_at>=0,LiteServiceBytes.hex(inventory_sha256),
              (0...10000).contains(grant_total),(1...1000000).contains(revocation_scan),
              (0...grant_total).contains(grants_examined),(0...grants_examined).contains(grants_revoked),
              ["not_started","in_progress","pending","confirmed"].contains(apple_revocation),
              !account_deleted,!subscription_cancelled,local_records_preserved
        else {throw LiteDeletionFailure.invalid}
        if v==1 {
            guard data_disposition=="not_started",disposition==nil else{throw LiteDeletionFailure.invalid}
        } else {
            guard data_disposition=="pending",let progress=disposition else{throw LiteDeletionFailure.invalid}
            try progress.validate(apple:apple_revocation,previous:previous?.disposition)
        }
        if apple_revocation=="not_started" && (grants_examined != 0 || grants_revoked != 0) {throw LiteDeletionFailure.invalid}
        if apple_revocation=="confirmed" && (grants_examined != grant_total || grants_revoked != grant_total) {throw LiteDeletionFailure.invalid}
        if apple_revocation=="pending" && (grants_examined != grant_total || grants_revoked==grant_total) {throw LiteDeletionFailure.invalid}
        if let p=previous {
            guard requested_at==p.requested_at,inventory_sha256==p.inventory_sha256,grant_total==p.grant_total,
                  revocation_scan>=p.revocation_scan,
                  revocation_scan>p.revocation_scan || (grants_examined>=p.grants_examined && grants_revoked>=p.grants_revoked),
                  v>=p.v,
                  p.apple_revocation != "confirmed" || (apple_revocation==p.apple_revocation &&
                    grants_examined==p.grants_examined && grants_revoked==p.grants_revoked && revocation_scan==p.revocation_scan)
            else{throw LiteDeletionFailure.conflict}
        }
    }
}

@MainActor protocol LiteDeletionIdentity {
    func deletionContext() async throws ->LiteDeletionContext
    func suspendForDeletion(_ context:LiteDeletionContext,operation:String) async throws
    func authenticateDeletion(_ challenge:LiteDeletionChallenge,context:LiteDeletionContext) async throws ->String
}
extension LiteDeletionIdentity {
    func authenticateDeletion(_ challenge:LiteDeletionChallenge,context:LiteDeletionContext)async throws->String {throw LiteDeletionFailure.unavailable}
}
@MainActor protocol LiteAccountDeletionService {
    var origin:URL {get}
    func installation() async throws ->String
    func request(operation:String,installation:String) async throws ->LiteDeletionReceipt
    func status(operation:String,installation:String) async throws ->LiteDeletionReceipt
    func deletionChallenge(context:LiteDeletionContext,operation:String,attempt:String)async throws->LiteDeletionChallenge
    func confirmDeletion(_ challenge:LiteDeletionChallenge,context:LiteDeletionContext,identityToken:String)async throws->LiteDeletionReceipt
}
extension LiteAccountDeletionService {
    func deletionChallenge(context:LiteDeletionContext,operation:String,attempt:String)async throws->LiteDeletionChallenge {throw LiteDeletionFailure.unavailable}
    func confirmDeletion(_ challenge:LiteDeletionChallenge,context:LiteDeletionContext,identityToken:String)async throws->LiteDeletionReceipt {throw LiteDeletionFailure.unavailable}
}

/// Privacy transport is deliberately independent of research opt-in and normal
/// signed-in service admission. It never creates a replacement installation.
struct LiteSignedDeletionTransport: LiteAuthenticatedTransport {
    let client:LiteAttestClient
    let allowPrivacyRequest:@Sendable () async ->Bool
    func installation() async throws ->String {
        guard await allowPrivacyRequest(),let owner=try await client.existingInstallation() else{throw LiteDeletionFailure.identity}
        return owner
    }
    func send(_ request:URLRequest,installation:String) async throws ->(Data,HTTPURLResponse) {
        guard try await self.installation()==installation,request.httpMethod=="POST",
              ["request","status","reauth/challenge","reauth/confirm"].map({"/api/v1/lite/account/deletion/"+$0}).contains(request.url?.path ?? "")
        else{throw LiteDeletionFailure.invalid}
        return try await client.send(request,installation:installation,requestID:UUID(),allowSending:allowPrivacyRequest)
    }
}

@MainActor final class LiteAccountDeletionHTTPService: LiteAccountDeletionService {
    let origin:URL,transport:any LiteAuthenticatedTransport
    init(origin:URL,transport:any LiteAuthenticatedTransport)throws {
        try LiteDeletionContext(origin:origin.absoluteString,installation:"validation",account:String(repeating:"a",count:64)).validate()
        self.origin=origin;self.transport=transport
    }
    func installation()async throws->String {try await transport.installation()}
    func request(operation:String,installation:String)async throws->LiteDeletionReceipt {try await send("request",operation,installation)}
    func status(operation:String,installation:String)async throws->LiteDeletionReceipt {try await send("status",operation,installation)}
    private func send(_ kind:String,_ operation:String,_ owner:String)async throws->LiteDeletionReceipt {
        guard LiteServiceBytes.hex(operation,32),try await installation()==owner else{throw LiteDeletionFailure.conflict}
        var body=["anon_id":owner,"operation":operation]
        if kind=="request" {body["confirmation"]="delete_account"}
        let data=try await post(kind,owner:owner,body:body,expected:202)
        let r=try JSONDecoder().decode(LiteDeletionReceipt.self,from:data);try r.validate(owner:owner,operation:operation);return r
    }
    func deletionChallenge(context:LiteDeletionContext,operation:String,attempt:String)async throws->LiteDeletionChallenge {
        try context.validate();guard context.origin==origin.absoluteString,LiteServiceBytes.hex(operation,32),LiteServiceBytes.hex(attempt,32) else{throw LiteDeletionFailure.invalid}
        let data=try await post("reauth/challenge",owner:context.installation,body:["anon_id":context.installation,"operation":operation,
            "reauth_operation":attempt,"account_sha256":context.account,"confirmation":"continue_deletion"],expected:200)
        let c=try JSONDecoder().decode(LiteDeletionChallenge.self,from:data);try c.validate(context,operation:operation,attempt:attempt);return c
    }
    func confirmDeletion(_ challenge:LiteDeletionChallenge,context:LiteDeletionContext,identityToken:String)async throws->LiteDeletionReceipt {
        try challenge.validate(context,operation:challenge.operation,attempt:challenge.reauth_operation)
        guard context.origin==origin.absoluteString,(1...16000).contains(identityToken.utf8.count) else{throw LiteDeletionFailure.invalid}
        let data=try await post("reauth/confirm",owner:context.installation,body:["anon_id":context.installation,"operation":challenge.operation,
            "reauth_operation":challenge.reauth_operation,"account_sha256":context.account,"state":challenge.state,"identity_token":identityToken],expected:202)
        let r=try JSONDecoder().decode(LiteDeletionReceipt.self,from:data);try r.validate(owner:context.installation,operation:challenge.operation);return r
    }
    private func post(_ kind:String,owner:String,body:[String:String],expected:Int)async throws->Data {
        guard try await installation()==owner else{throw LiteDeletionFailure.conflict}
        var req=URLRequest(url:origin.appendingPathComponent("api/v1/lite/account/deletion/"+kind));req.httpMethod="POST"
        req.setValue("application/json",forHTTPHeaderField:"Content-Type");req.httpBody=try JSONSerialization.data(withJSONObject:body,options:[.sortedKeys]);req.timeoutInterval=30
        let(data,response)=try await transport.send(req,installation:owner)
        guard try await installation()==owner,response.url==req.url,data.count<=20000 else{throw LiteDeletionFailure.invalid}
        if response.statusCode==401 {throw LiteDeletionFailure.identity}
        if response.statusCode==410 {throw LiteDeletionFailure.expired}
        guard response.statusCode==expected else{throw LiteDeletionFailure.unavailable}
        return data
    }
}

struct LiteAccountDeletionCheckpoint: Codable, Equatable {
    let version:Int,context:LiteDeletionContext,operation:String,createdAt:Int
    var receipt:LiteDeletionReceipt?
    var recovery:LiteDeletionRecovery?
    static func key(origin:String,owner:String)->String {
        String(LiteServiceBytes.hash(Data((origin+"\0"+owner+"\0account-deletion").utf8)).prefix(32))
    }
    func validate()throws {
        try context.validate()
        guard version==1,LiteServiceBytes.hex(operation,32),createdAt>=0 else{throw LiteDeletionFailure.invalid}
        try receipt?.validate(owner:context.installation,operation:operation)
        if let r=recovery {
            guard receipt==nil,LiteServiceBytes.hex(r.attempt,32) else{throw LiteDeletionFailure.invalid}
            try r.challenge?.validate(context,operation:operation,attempt:r.attempt)
            if let token=r.identityToken {
                guard r.challenge != nil,(1...16000).contains(token.utf8.count) else{throw LiteDeletionFailure.invalid}
            }
        }
    }
}

@MainActor final class LiteAccountDeletionStore: ObservableObject {
    @Published private(set) var record:LiteAccountDeletionCheckpoint?
    @Published private(set) var busy=false
    @Published private(set) var message="刪除帳號、取消 Apple 訂閱及研究撤回會分別處理。"
    @Published private(set) var needsReview=false
    @Published private(set) var identityRequired=false
    let service:any LiteAccountDeletionService,identity:any LiteDeletionIdentity,journal:any LiteServiceJournal
    let now:()->Date
    init(service:any LiteAccountDeletionService,identity:any LiteDeletionIdentity,journal:any LiteServiceJournal,now:@escaping()->Date=Date.init){
        self.service=service;self.identity=identity;self.journal=journal;self.now=now
    }
    private func read(_ owner:String)async throws->(Data?,LiteAccountDeletionCheckpoint?) {
        let raw=try await journal.load(LiteAccountDeletionCheckpoint.key(origin:service.origin.absoluteString,owner:owner))
        guard let raw else{return(nil,nil)}
        let r=try JSONDecoder().decode(LiteAccountDeletionCheckpoint.self,from:raw);try r.validate()
        guard r.context.origin==service.origin.absoluteString,r.context.installation==owner else{throw LiteDeletionFailure.conflict}
        return(raw,r)
    }
    private func save(_ owner:String,_ old:Data?,_ r:LiteAccountDeletionCheckpoint)async throws->Data {
        try r.validate();guard try await service.installation()==owner else{throw LiteDeletionFailure.conflict}
        let data=try JSONEncoder().encode(r)
        try await journal.replace(LiteAccountDeletionCheckpoint.key(origin:service.origin.absoluteString,owner:owner),expected:old,with:data)
        record=r;return data
    }
    func reload()async {
        guard !busy else{return};busy=true;defer{busy=false}
        do {let owner=try await service.installation();record=try await read(owner).1;needsReview=false;describe()}
        catch {needsReview=true;message="無法核對原刪帳要求；已停止新增要求。請保留紀錄，稍後再核對。"}
    }
    /// Called only by the destructive confirmation button; no onAppear intake.
    func requestConfirmed()async {await advance(create:true)}
    func retry()async {await advance(create:false)}
    private func advance(create:Bool)async {
        guard !busy else{return};busy=true;defer{busy=false}
        do {
            let owner=try await service.installation();var(raw,r)=try await read(owner)
            if r==nil {
                guard create else{return}
                let c=try await identity.deletionContext();try c.validate()
                guard c.installation==owner,c.origin==service.origin.absoluteString else{throw LiteDeletionFailure.conflict}
                let next=LiteAccountDeletionCheckpoint(version:1,context:c,operation:UUID().uuidString.replacingOccurrences(of:"-",with:"").lowercased(),createdAt:Int(now().timeIntervalSince1970))
                raw=try await save(owner,nil,next);r=next // Persist before closing local admission or transmitting.
            }
            guard var current=r,let old=raw else{throw LiteDeletionFailure.invalid}
            record=current
            try await identity.suspendForDeletion(current.context,operation:current.operation)
            let reply:LiteDeletionReceipt
            if current.receipt==nil {reply=try await pendingReply(current)}
            else {reply=try await service.status(operation:current.operation,installation:owner)}
            try reply.validate(owner:owner,operation:current.operation,previous:current.receipt)
            guard reply.requested_at<=Int(now().timeIntervalSince1970)+60 else{throw LiteDeletionFailure.invalid}
            current.receipt=reply;current.recovery=nil;_=try await save(owner,old,current);needsReview=false;identityRequired=false;describe()
        } catch LiteDeletionFailure.identity {
            identityRequired=true
            message="雲端尚未確認刪帳。請按「核對身分並繼續原刪帳要求」，使用原 Apple 帳號核對。原要求與紀錄保留，一般服務保持暫停。"
        } catch LiteDeletionFailure.expired {
            identityRequired=true;message="這次身分核對已逾時。請按「核對身分並繼續原刪帳要求」重新核對；仍會沿用同一筆刪帳要求。"
        } catch {
            message="刪帳處理尚未確認完成。原要求與手機紀錄保留；請按「核對原刪帳進度」，不會另建要求。"
        }
    }
    private func pendingReply(_ r:LiteAccountDeletionCheckpoint)async throws->LiteDeletionReceipt {
        do {return try await service.request(operation:r.operation,installation:r.context.installation)}
        catch LiteDeletionFailure.identity {
            if let c=r.recovery?.challenge,let token=r.recovery?.identityToken {
                return try await service.confirmDeletion(c,context:r.context,identityToken:token)
            }
            throw LiteDeletionFailure.identity
        }
    }
    /// Only this explicit button may open Apple UI. Retry/reload never prompts,
    /// changes the original deletion ID, or restores the general service login.
    func reauthenticate()async {
        guard !busy else{return};busy=true;defer{busy=false}
        do {
            let owner=try await service.installation();let(raw,r)=try await read(owner)
            guard var current=r,var old=raw,current.receipt==nil else{return}
            record=current;try await identity.suspendForDeletion(current.context,operation:current.operation)
            // A server may already have accepted the original request/proof.
            do {
                let reply=try await pendingReply(current)
                try reply.validate(owner:owner,operation:current.operation)
                guard reply.requested_at<=Int(now().timeIntervalSince1970)+60 else{throw LiteDeletionFailure.invalid}
                current.receipt=reply;current.recovery=nil;_=try await save(owner,old,current)
                identityRequired=false;describe();return
            } catch LiteDeletionFailure.identity {} catch LiteDeletionFailure.expired {}
            if current.recovery==nil || (current.recovery?.challenge?.expires_at ?? Int.max)<=Int(now().timeIntervalSince1970) {
                current.recovery=LiteDeletionRecovery(attempt:UUID().uuidString.replacingOccurrences(of:"-",with:"").lowercased())
                old=try await save(owner,old,current) // persist attempt before network / Apple UI
            }
            guard var recovery=current.recovery else{throw LiteDeletionFailure.invalid}
            if recovery.challenge==nil {
                let c=try await service.deletionChallenge(context:current.context,operation:current.operation,attempt:recovery.attempt)
                try c.validate(current.context,operation:current.operation,attempt:recovery.attempt)
                recovery.challenge=c;current.recovery=recovery;old=try await save(owner,old,current)
            }
            guard let c=recovery.challenge,c.created_at<=Int(now().timeIntervalSince1970)+60,c.expires_at>Int(now().timeIntervalSince1970) else{throw LiteDeletionFailure.expired}
            if recovery.identityToken==nil {
                recovery.identityToken=try await identity.authenticateDeletion(c,context:current.context)
                current.recovery=recovery;old=try await save(owner,old,current) // protected journal; no code or refresh token
            }
            guard let token=recovery.identityToken else{throw LiteDeletionFailure.invalid}
            let reply=try await service.confirmDeletion(c,context:current.context,identityToken:token)
            try reply.validate(owner:owner,operation:current.operation)
            guard reply.requested_at<=Int(now().timeIntervalSince1970)+60 else{throw LiteDeletionFailure.invalid}
            current.receipt=reply;current.recovery=nil;_=try await save(owner,old,current)
            needsReview=false;identityRequired=false;describe()
        } catch LiteAccountFailure.cancelled {
            identityRequired=true;message="已取消這次身分核對；原刪帳要求與手機紀錄保留。一般服務仍暫停，可稍後再核對。"
        } catch LiteDeletionFailure.expired {
            await clearRecovery(expired:true)
            identityRequired=true;message="身分核對已逾時，請再次按核對身分。原刪帳要求不變，不會恢復一般服務。"
        } catch LiteDeletionFailure.identity {
            await clearRecovery(expired:false)
            identityRequired=true;message="尚未確認為原 Apple 帳號，請重新核對身分。原刪帳要求不變，沒有恢復服務或建立新帳號。"
        } catch {
            identityRequired=true;message="尚未確認原帳號身分或雲端受理。請保留原要求，先核對原刪帳進度；必要時使用原 Apple 帳號重新核對。手機紀錄保留。"
        }
    }
    private func clearRecovery(expired:Bool)async {
        // Clear only the exact checkpoint this UI owns. Another task's newer
        // proof/receipt must survive a stale error response.
        guard let expected=record,expected.receipt==nil else{return}
        do {
            let owner=try await service.installation();let(raw,r)=try await read(owner)
            guard r==expected,let raw else{return}
            var next=expected
            if expired{next.recovery=nil}else{next.recovery?.identityToken=nil}
            _=try await save(owner,raw,next)
        } catch {needsReview=true}
    }
    private func describe() {
        guard let r=record else{return}
        guard let reply=r.receipt else{message="刪帳要求已存於手機，尚未確認雲端受理。一般服務暫停，手機紀錄保留。";return}
        let apple=reply.apple_revocation=="confirmed" ? "Apple 授權撤銷已確認。":"Apple 授權撤銷仍在處理或待核對。"
        if let progress=reply.disposition {
            let waiting=progress.blocked_lanes.isEmpty ? "":"有步驟尚待確認，可核對原進度，不會另建要求。"
            message=["雲端已受理，帳號服務已關閉。",apple,"目前服務結果：",progress.serviceText,
                "；Apple 憑證金鑰：",progress.keyText,"。",waiting,
                "歷史版本、備份及研究等資料仍待處理，尚未完成刪除；手機紀錄保留。"].joined()
        } else {
            message="雲端已受理，帳號服務已關閉。"+apple+"帳號資料清理尚未開始，尚未完成刪除；手機紀錄仍保留。"
        }
    }
}
