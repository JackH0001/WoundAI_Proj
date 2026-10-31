import Foundation

enum LitePurchaseFailure: Error, Equatable { case invalid, accountMismatch, unavailable, journalConflict }

struct LitePurchaseContext: Codable, Equatable, Sendable {
    let v: Int, operation: String, installation: String, account_sha256: String
    let app_account_token: String, registration_sha256: String, scope_sha256: String
    let product_id: String, environment: String
    let created_at: Int, expires_at: Int
    func validate() throws {
        guard v == 1, LiteServiceBytes.hex(operation,32),
              LiteAttestClient.matches(installation,"[A-Za-z0-9_-]{1,128}"),
              [account_sha256,registration_sha256,scope_sha256].allSatisfy({LiteServiceBytes.hex($0)}),
              let token = UUID(uuidString:app_account_token), token.uuidString.lowercased() == app_account_token,
              token != UUID(uuidString:"00000000-0000-0000-0000-000000000000"),
              product_id == "com.woundai.lite.plus.monthly", ["Sandbox","Production","Xcode"].contains(environment),
              created_at >= 0, expires_at > created_at, expires_at-created_at == 900 else { throw LitePurchaseFailure.invalid }
    }
}

struct LitePurchaseEvent: Codable, Equatable, Sendable {
    let transaction: String, original: String, token: String, product: String, environment: String, signed: String
    func sameTransaction(as other:Self) -> Bool {
        transaction == other.transaction && original == other.original && token == other.token && product == other.product && environment == other.environment
    }
    func validate(_ context: LitePurchaseContext) throws {
        try context.validate()
        guard LiteAttestClient.matches(transaction,"[0-9]{1,32}"), LiteAttestClient.matches(original,"[0-9]{1,32}"),
              token == context.app_account_token, product == context.product_id, environment == context.environment,
              (1...12000).contains(signed.utf8.count) else { throw LitePurchaseFailure.accountMismatch }
    }
}

struct LitePurchaseAcknowledgment: Codable, Equatable, Sendable {
    let v: Int, state: String, account_sha256: String, app_account_token: String
    let transaction_id: String, original_transaction_id: String, product_id: String, environment: String, association_sha256: String
    func validate(_ context: LitePurchaseContext, _ event: LitePurchaseEvent) throws {
        try event.validate(context)
        guard v == 1, state == "associated", account_sha256 == context.account_sha256,
              app_account_token == context.app_account_token, transaction_id == event.transaction,
              original_transaction_id == event.original, product_id == event.product, environment == event.environment,
              LiteServiceBytes.hex(association_sha256) else { throw LitePurchaseFailure.accountMismatch }
    }
}

@MainActor protocol LitePurchaseService {
    var origin: URL { get }
    func installation() async throws -> String
    func context(operation: String) async throws -> LitePurchaseContext
    func confirm(_ context: LitePurchaseContext, event: LitePurchaseEvent) async throws -> LitePurchaseAcknowledgment
}

struct LiteSignedPurchaseTransport: LiteAuthenticatedTransport {
    let client: LiteAttestClient
    let allowService: @Sendable () async -> Bool
    func installation() async throws -> String {
        guard await allowService(), let owner = try await client.existingInstallation() else { throw LitePurchaseFailure.unavailable }
        return owner
    }
    func send(_ request: URLRequest, installation: String) async throws -> (Data,HTTPURLResponse) {
        guard try await self.installation() == installation,
              ["/api/v1/lite/purchase/context","/api/v1/lite/purchase/confirm"].contains(request.url?.path ?? "") else {
            throw LitePurchaseFailure.accountMismatch
        }
        return try await client.send(request,installation:installation,requestID:UUID(),allowSending:allowService)
    }
}

/// Explicit factory only. No startup routes, anonymous enrollment or credential discovery.
@MainActor final class LitePurchaseHTTPService: LitePurchaseService {
    let origin: URL
    let environment: String
    let transport: any LiteAuthenticatedTransport
    init(origin: URL, environment: String, transport: any LiteAuthenticatedTransport) throws {
        guard origin.scheme == "https", origin.host != nil, origin.user == nil, origin.password == nil,
              origin.query == nil, origin.fragment == nil, ["","/"].contains(origin.path),
              ["Sandbox","Production"].contains(environment) else { throw LitePurchaseFailure.invalid }
        self.origin=origin; self.environment=environment; self.transport=transport
    }
    func installation() async throws -> String { try await transport.installation() }
    private func send<T: Decodable>(_ path: String, body: [String:String], owner: String, as type:T.Type) async throws -> T {
        guard try await installation() == owner else { throw LitePurchaseFailure.accountMismatch }
        var request = URLRequest(url:origin.appendingPathComponent("api/v1/lite/purchase/"+path))
        request.httpMethod="POST";request.setValue("application/json",forHTTPHeaderField:"Content-Type")
        request.httpBody=try JSONEncoder().encode(body);request.timeoutInterval=30
        let (data,response)=try await transport.send(request,installation:owner)
        guard try await installation() == owner, data.count <= 20000,
              response.url?.scheme == origin.scheme, response.url?.host == origin.host,
              response.url?.port == origin.port, response.url?.path == request.url?.path,
              response.url?.query == nil, response.url?.fragment == nil, response.url?.user == nil, response.url?.password == nil,
              response.statusCode == 200 else { throw LitePurchaseFailure.unavailable }
        return try JSONDecoder().decode(type,from:data)
    }
    func context(operation: String) async throws -> LitePurchaseContext {
        let owner=try await installation()
        let c=try await send("context",body:["anon_id":owner,"operation":operation],owner:owner,as:LitePurchaseContext.self)
        try c.validate()
        guard c.installation == owner, c.operation == operation, c.environment == environment else { throw LitePurchaseFailure.accountMismatch }
        return c
    }
    func confirm(_ context: LitePurchaseContext,event: LitePurchaseEvent) async throws -> LitePurchaseAcknowledgment {
        try event.validate(context)
        guard context.environment == environment, try await installation() == context.installation else { throw LitePurchaseFailure.accountMismatch }
        let r=try await send("confirm",body:["anon_id":context.installation,"operation":context.operation,
            "signed_transaction":event.signed],owner:context.installation,as:LitePurchaseAcknowledgment.self)
        try r.validate(context,event);return r
    }
}

struct LitePurchaseCheckpoint: Codable, Equatable {
    let version: Int, origin: String, context: LitePurchaseContext
    var event: LitePurchaseEvent?, acknowledgment: LitePurchaseAcknowledgment?
    var finished = false
    func validate() throws {
        try context.validate()
        guard version == 1, URL(string:origin)?.scheme == "https" else { throw LitePurchaseFailure.invalid }
        if let event { try event.validate(context) }
        if let acknowledgment {
            guard let event else { throw LitePurchaseFailure.invalid }
            try acknowledgment.validate(context,event)
        }
        if finished && acknowledgment == nil { throw LitePurchaseFailure.invalid }
    }
}

enum LitePurchaseOutcome { case completed(LitePurchaseEvent), pending, cancelled }
enum LitePurchaseUpdate: Sendable { case event(LitePurchaseEvent), invalid }

@MainActor protocol LitePurchaseProvider {
    func purchase(product: String, token: UUID) async throws -> LitePurchaseOutcome
    func restore() async throws -> [LitePurchaseEvent]
    func unfinished() async throws -> [LitePurchaseEvent]
    func updates() -> AsyncStream<LitePurchaseUpdate>
    func finish(_ event: LitePurchaseEvent) async throws
}

/// Server acknowledgment is persisted before StoreKit finish. No local paid flag.
@MainActor final class LiteNativePurchaseStore: ObservableObject {
    @Published private(set) var message="請先確認服務帳號；訂閱與研究同意分開處理。"
    @Published private(set) var busy=false
    let service: any LitePurchaseService, provider: any LitePurchaseProvider, journal: any LiteServiceJournal
    private let now: () -> Date
    private var processing=Set<String>()
    private var listener: Task<Void,Never>?
    init(service: any LitePurchaseService,provider: any LitePurchaseProvider,journal: any LiteServiceJournal,now:@escaping ()->Date=Date.init) {
        self.service=service;self.provider=provider;self.journal=journal;self.now=now
    }
    deinit { listener?.cancel() }
    private func id(_ s:String)->String { String(LiteServiceBytes.hash(Data(s.utf8)).prefix(32)) }
    private func save(_ key:String,_ old:Data?,_ record:LitePurchaseCheckpoint) async throws -> Data {
        try record.validate();let data=try JSONEncoder().encode(record)
        try await journal.replace(key,expected:old,with:data);return data
    }
    private func context() async throws -> LitePurchaseContext {
        let operation=UUID().uuidString.replacingOccurrences(of:"-",with:"").lowercased()
        let c=try await service.context(operation:operation);try c.validate()
        guard c.installation == (try await service.installation()), c.operation == operation,
              c.created_at <= Int(now().timeIntervalSince1970), c.expires_at > Int(now().timeIntervalSince1970) else { throw LitePurchaseFailure.invalid }
        _=try await save(operation,nil,LitePurchaseCheckpoint(version:1,origin:service.origin.absoluteString,context:c))
        return c
    }
    private func process(_ event:LitePurchaseEvent,using supplied:LitePurchaseContext?=nil) async throws {
        let owner=try await service.installation()
        let key=id(service.origin.absoluteString+"\0"+owner+"\0"+event.environment+"\0"+event.transaction)
        guard processing.insert(key).inserted else { return }
        defer { processing.remove(key) }
        var old=try await journal.load(key),record:LitePurchaseCheckpoint
        if let old {
            record=try JSONDecoder().decode(LitePurchaseCheckpoint.self,from:old);try record.validate()
            guard record.origin == service.origin.absoluteString, record.context.installation == owner,
                  record.event?.sameTransaction(as:event) == true else { throw LitePurchaseFailure.accountMismatch }
        } else {
            let c:LitePurchaseContext
            if let supplied { c=supplied } else { c=try await context() }
            try event.validate(c)
            record=LitePurchaseCheckpoint(version:1,origin:service.origin.absoluteString,context:c,event:event)
            old=try await save(key,nil,record)
        }
        guard try await service.installation() == owner else { throw LitePurchaseFailure.accountMismatch }
        if record.acknowledgment == nil {
            let r=try await service.confirm(record.context,event:record.event!);try r.validate(record.context,event)
            record.acknowledgment=r;old=try await save(key,old,record)
        }
        guard try await service.installation() == owner else { throw LitePurchaseFailure.accountMismatch }
        if !record.finished {
            try await provider.finish(event)
            record.finished=true;_=try await save(key,old,record)
        }
        message="購買與服務帳號關聯已確認；可用次數依伺服器核對，不會重設今日已用額度。"
    }
    private func failed(_ e:Error) {
        message = (e as? LitePurchaseFailure) == .accountMismatch ?
            "購買與目前服務帳號不符，未移轉訂閱或增加額度；請保留原紀錄並核對帳號。" :
            "購買關聯尚未確認，原紀錄與交易保留。請按「核對未完成購買」，不要重複購買。"
    }
    func purchase() async {
        guard !busy else{return};busy=true;defer{busy=false}
        do {
            let c=try await context()
            switch try await provider.purchase(product:c.product_id,token:UUID(uuidString:c.app_account_token)!) {
            case .completed(let e):try await process(e,using:c)
            case .pending:message="購買等待核准，尚未增加額度；核准後會核對原交易。"
            case .cancelled:message="已取消購買；原紀錄、額度與研究同意不變。"
            }
        } catch { failed(error) }
    }
    func restore() async {
        guard !busy else{return};busy=true;defer{busy=false}
        do {
            let c=try await context();let events=try await provider.restore()
            if events.isEmpty { message="沒有找到可恢復的訂閱；未建立新帳號或重設額度。" }
            for e in events { try await process(e,using:c) }
        } catch { failed(error) }
    }
    func reconcile() async {
        guard !busy else{return};busy=true;defer{busy=false}
        do { for e in try await provider.unfinished() { try await process(e) } }
        catch { failed(error) }
    }
    func startObserving() {
        guard listener == nil else{return}
        listener=Task { [weak self] in
            guard let provider=self?.provider else{return}
            for await update in provider.updates() {
                guard !Task.isCancelled else{return}
                do {
                    switch update {
                    case .event(let e):try await self?.process(e)
                    case .invalid:throw LitePurchaseFailure.invalid
                    }
                } catch { self?.failed(error) }
            }
        }
    }
}
