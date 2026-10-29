import Foundation

enum LiteDeletionFailure: Error { case invalid, unavailable, identity, conflict }

struct LiteDeletionContext: Codable, Equatable {
    let origin: String, installation: String, account: String
    func validate() throws {
        guard let u=URL(string:origin),u.scheme=="https",u.host != nil,u.user==nil,u.password==nil,
              u.query==nil,u.fragment==nil,["","/"].contains(u.path),
              LiteAttestClient.matches(installation,"[A-Za-z0-9_-]{1,128}"),LiteServiceBytes.hex(account)
        else {throw LiteDeletionFailure.invalid}
    }
}

/// This version understands pending disposition only. A future complete-deletion
/// receipt needs a separately validated data-disposition contract.
struct LiteDeletionReceipt: Codable, Equatable {
    let v:Int, state:String, operation:String, installation:String, requested_at:Int
    let inventory_sha256:String, grant_total:Int, revocation_scan:Int, grants_examined:Int, grants_revoked:Int
    let apple_revocation:String, data_disposition:String
    let account_deleted:Bool, subscription_cancelled:Bool, local_records_preserved:Bool
    func validate(owner:String,operation:String,previous:Self?=nil) throws {
        guard v==1,state=="account_deletion_pending",self.operation==operation,LiteServiceBytes.hex(operation,32),
              installation==owner,requested_at>=0,LiteServiceBytes.hex(inventory_sha256),
              (0...10000).contains(grant_total),(1...1000000).contains(revocation_scan),
              (0...grant_total).contains(grants_examined),(0...grants_examined).contains(grants_revoked),
              ["not_started","in_progress","pending","confirmed"].contains(apple_revocation),
              data_disposition=="not_started",!account_deleted,!subscription_cancelled,local_records_preserved
        else {throw LiteDeletionFailure.invalid}
        if apple_revocation=="not_started" && (grants_examined != 0 || grants_revoked != 0) {throw LiteDeletionFailure.invalid}
        if apple_revocation=="confirmed" && (grants_examined != grant_total || grants_revoked != grant_total) {throw LiteDeletionFailure.invalid}
        if apple_revocation=="pending" && (grants_examined != grant_total || grants_revoked==grant_total) {throw LiteDeletionFailure.invalid}
        if let p=previous {
            guard requested_at==p.requested_at,inventory_sha256==p.inventory_sha256,grant_total==p.grant_total,
                  revocation_scan>=p.revocation_scan,
                  revocation_scan>p.revocation_scan || (grants_examined>=p.grants_examined && grants_revoked>=p.grants_revoked),
                  p.apple_revocation != "confirmed" || self==p else{throw LiteDeletionFailure.conflict}
        }
    }
}

@MainActor protocol LiteDeletionIdentity {
    func deletionContext() async throws ->LiteDeletionContext
    func suspendForDeletion(_ context:LiteDeletionContext,operation:String) async throws
}
@MainActor protocol LiteAccountDeletionService {
    var origin:URL {get}
    func installation() async throws ->String
    func request(operation:String,installation:String) async throws ->LiteDeletionReceipt
    func status(operation:String,installation:String) async throws ->LiteDeletionReceipt
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
              ["request","status"].map({"/api/v1/lite/account/deletion/"+$0}).contains(request.url?.path ?? "")
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
        var req=URLRequest(url:origin.appendingPathComponent("api/v1/lite/account/deletion/"+kind));req.httpMethod="POST"
        req.setValue("application/json",forHTTPHeaderField:"Content-Type");req.httpBody=try JSONSerialization.data(withJSONObject:body,options:[.sortedKeys]);req.timeoutInterval=30
        let(data,response)=try await transport.send(req,installation:owner)
        guard try await installation()==owner,response.url==req.url,data.count<=20000 else{throw LiteDeletionFailure.invalid}
        if response.statusCode==401 {throw LiteDeletionFailure.identity}
        guard response.statusCode==202 else{throw LiteDeletionFailure.unavailable}
        let r=try JSONDecoder().decode(LiteDeletionReceipt.self,from:data);try r.validate(owner:owner,operation:operation);return r
    }
}

struct LiteAccountDeletionCheckpoint: Codable, Equatable {
    let version:Int,context:LiteDeletionContext,operation:String,createdAt:Int
    var receipt:LiteDeletionReceipt?
    static func key(origin:String,owner:String)->String {
        String(LiteServiceBytes.hash(Data((origin+"\0"+owner+"\0account-deletion").utf8)).prefix(32))
    }
    func validate()throws {
        try context.validate()
        guard version==1,LiteServiceBytes.hex(operation,32),createdAt>=0 else{throw LiteDeletionFailure.invalid}
        try receipt?.validate(owner:context.installation,operation:operation)
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
            if current.receipt==nil {reply=try await service.request(operation:current.operation,installation:owner)}
            else {reply=try await service.status(operation:current.operation,installation:owner)}
            try reply.validate(owner:owner,operation:current.operation,previous:current.receipt)
            guard reply.requested_at<=Int(now().timeIntervalSince1970)+60 else{throw LiteDeletionFailure.invalid}
            current.receipt=reply;_=try await save(owner,old,current);needsReview=false;identityRequired=false;describe()
        } catch LiteDeletionFailure.identity {
            identityRequired=true
            message="雲端尚未確認刪帳：身分需要重新核對。本機要求與紀錄保留，一般服務保持暫停；目前驗收版尚未接通刪帳專用身分復原，請勿反覆重試或刪除 App。"
        } catch {
            message="刪帳處理尚未確認完成。原要求與手機紀錄保留；請按「核對原刪帳進度」，不會另建要求。"
        }
    }
    private func describe() {
        guard let r=record else{return}
        guard let reply=r.receipt else{message="刪帳要求已存於手機，尚未確認雲端受理。一般服務暫停，手機紀錄保留。";return}
        let apple=reply.apple_revocation=="confirmed" ? "Apple 授權撤銷已確認。":"Apple 授權撤銷仍在處理或待核對。"
        message="雲端已受理，帳號服務已關閉。"+apple+"帳號資料清理尚未開始，尚未完成刪除；手機紀錄仍保留。"
    }
}
