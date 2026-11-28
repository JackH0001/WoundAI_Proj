import Foundation

enum LitePurchaseFailure: Error, Equatable { case invalid, accountMismatch, accountChanged, unavailable, journalConflict, offerChanged, contextExpired }

/// Only the reviewed one-month product is offered. Storefront belongs to the
/// quote, even where two storefronts use the same currency and display price.
struct LiteMonthlyOffer: Equatable {
    static let productID = "com.woundai.lite.plus.monthly"
    let name: String
    let displayPrice: String
    let price: Decimal
    let currency: String
    let storefront: String
}

struct LitePurchaseContext: Codable, Equatable, Sendable {
    let v: Int, operation: String, installation: String, account_sha256: String
    let app_account_token: String, registration_sha256: String, scope_sha256: String
    let product_id: String, environment: String
    let created_at: Int, expires_at: Int
    func isCurrent(at date: Date) -> Bool {
        let time = Int(date.timeIntervalSince1970)
        return created_at <= time && time < expires_at
    }

    func validate() throws {
        guard v == 1, LiteServiceBytes.hex(operation,32),
              LiteAttestClient.matches(installation,"[A-Za-z0-9_-]{1,128}"),
              [account_sha256,registration_sha256,scope_sha256].allSatisfy({LiteServiceBytes.hex($0)}),
              let token = UUID(uuidString:app_account_token), token.uuidString.lowercased() == app_account_token,
              token != UUID(uuidString:"00000000-0000-0000-0000-000000000000"),
              product_id == LiteMonthlyOffer.productID, ["Sandbox","Production","Xcode"].contains(environment),
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
    func context(operation: String, existingOnly: Bool) async throws -> LitePurchaseContext
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
    func context(operation: String, existingOnly: Bool = false) async throws -> LitePurchaseContext {
        let owner=try await installation()
        var body = ["anon_id": owner, "operation": operation]
        if existingOnly { body["mode"] = "existing" }
        let c=try await send("context",body:body,owner:owner,as:LitePurchaseContext.self)
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
    func monthlyOffer() async throws -> LiteMonthlyOffer
    func purchase(offer: LiteMonthlyOffer, token: UUID, checkAccount: @MainActor () throws -> Void) async throws -> LitePurchaseOutcome
    func knownPurchases() async throws -> [LitePurchaseEvent]
    func restore() async throws -> [LitePurchaseEvent]
    func unfinished() async throws -> [LitePurchaseEvent]
    func updates() -> AsyncStream<LitePurchaseUpdate>
    func finish(_ event: LitePurchaseEvent) async throws
}

/// Server acknowledgment is persisted before StoreKit finish. No local paid flag.
@MainActor final class LiteNativePurchaseStore: ObservableObject {
    @Published private(set) var message = ""
    enum Activity {
        case purchasing, restoring, recheckingEntitlements, checkingTransactions, observingTransactions

        var progress: String {
            switch self {
            case .purchasing: return "正在處理訂閱…"
            case .restoring: return "正在恢復既有訂閱…"
            case .recheckingEntitlements: return "已與 Apple 同步，正在再次查看訂閱權益…"
            case .checkingTransactions: return "正在查詢未完成的交易…"
            case .observingTransactions: return "正在查看既有訂閱，完成後即可操作。"
            }
        }
    }

    @Published private(set) var activity: Activity?
    var busy: Bool { activity != nil }
    @Published private(set) var offer: LiteMonthlyOffer?
    @Published private(set) var loadingOffer = false
    @Published private(set) var confirmedUpdates = 0
    let service: any LitePurchaseService, provider: any LitePurchaseProvider, journal: any LiteServiceJournal
    private let now: () -> Date
    private let accountGeneration: () -> Int
    private var processing=Set<String>()
    private var listener: Task<Void,Never>?
    private var listeningToken: String?
    private var listeningGeneration: Int?
    init(service: any LitePurchaseService,provider: any LitePurchaseProvider,journal: any LiteServiceJournal,
         accountGeneration: @escaping () -> Int, now:@escaping ()->Date=Date.init) {
        self.service=service;self.provider=provider;self.journal=journal;self.now=now
        self.accountGeneration = accountGeneration
    }
    private func checkAccount(_ generation: Int) throws {
        guard generation == accountGeneration() else { throw LitePurchaseFailure.accountChanged }
    }
    deinit { listener?.cancel() }
    private func id(_ s:String)->String { String(LiteServiceBytes.hash(Data(s.utf8)).prefix(32)) }
    private func save(_ key:String,_ old:Data?,_ record:LitePurchaseCheckpoint) async throws -> Data {
        try record.validate();let data=try JSONEncoder().encode(record)
        try await journal.replace(key,expected:old,with:data);return data
    }
    private func context(for account: LiteNativeSession.PurchaseAccount? = nil, generation: Int) async throws -> LitePurchaseContext {
        try checkAccount(generation)
        let operation=UUID().uuidString.replacingOccurrences(of:"-",with:"").lowercased()
        let c=try await service.context(operation:operation, existingOnly: false);try c.validate()
        guard c.installation == (try await service.installation()), c.operation == operation,
              c.isCurrent(at: now()) else { throw LitePurchaseFailure.invalid }
        try checkAccount(generation)
        if let account, account.token != c.app_account_token { throw LitePurchaseFailure.accountChanged }
        _=try await save(operation,nil,LitePurchaseCheckpoint(version:1,origin:service.origin.absoluteString,context:c))
        try checkAccount(generation)
        return c
    }
    private func process(_ event:LitePurchaseEvent,using supplied:LitePurchaseContext?=nil, generation: Int) async throws {
        try checkAccount(generation)
        let owner=try await service.installation()
        // A saved acknowledgment is scoped to the original service account.
        // Even a finished transaction must match today's explicit restore/buy context.
        if let supplied {
            guard supplied.installation == owner else { throw LitePurchaseFailure.accountMismatch }
            try event.validate(supplied)
        }
        let key=id(service.origin.absoluteString+"\0"+owner+"\0"+event.environment+"\0"+event.transaction)
        guard processing.insert(key).inserted else { return }
        defer { processing.remove(key) }
        var old=try await journal.load(key),record:LitePurchaseCheckpoint
        if let old {
            record=try JSONDecoder().decode(LitePurchaseCheckpoint.self,from:old);try record.validate()
            guard record.origin == service.origin.absoluteString, record.context.installation == owner,
                  record.event?.sameTransaction(as:event) == true else { throw LitePurchaseFailure.accountMismatch }
            if let supplied, record.context.account_sha256 != supplied.account_sha256 {
                throw LitePurchaseFailure.accountMismatch
            }
            if supplied == nil, record.acknowledgment != nil {
                // Recheck the original owner before finishing from a cached
                // receipt. This lookup cannot create a missing context.
                let current = try await service.context(operation: record.context.operation, existingOnly: true)
                guard current == record.context else { throw LitePurchaseFailure.accountMismatch }
            }
        } else {
            let c:LitePurchaseContext
            if let supplied { c=supplied } else { c=try await context(generation: generation) }
            try event.validate(c)
            record=LitePurchaseCheckpoint(version:1,origin:service.origin.absoluteString,context:c,event:event)
            old=try await save(key,nil,record)
        }
        guard try await service.installation() == owner else { throw LitePurchaseFailure.accountMismatch }
        if record.acknowledgment == nil {
            try checkAccount(generation)
            try Task.checkCancellation()
            let r=try await service.confirm(record.context,event:record.event!);try r.validate(record.context,event)
            record.acknowledgment=r;old=try await save(key,old,record)
        }
        guard try await service.installation() == owner else { throw LitePurchaseFailure.accountMismatch }
        if !record.finished {
            try checkAccount(generation)
            try Task.checkCancellation()
            try await provider.finish(event)
            record.finished=true;_=try await save(key,old,record)
        }
        try checkAccount(generation)
        confirmedUpdates += 1
        message="購買與服務帳號關聯已確認；可用次數依伺服器核對，不會重設今日已用額度。"
    }
    private func failed(_ e:Error) {
        if (e as? LitePurchaseFailure) == .accountChanged {
            message = "服務帳號已變更。請登入原帳號後核對訂閱狀態。"
            return
        }
        message = (e as? LitePurchaseFailure) == .accountMismatch ?
            "購買與目前服務帳號不符，未移轉訂閱或增加額度；請保留原紀錄並核對帳號。" :
            "購買關聯尚未確認，原紀錄與交易保留。請按「核對未完成購買」，不要重複購買。"
    }
    private func reconcileKnown(_ events: [LitePurchaseEvent], using context: LitePurchaseContext, generation: Int) async throws {
        // Validate the whole StoreKit batch before mutating its first transaction.
        for event in events { try event.validate(context) }
        for event in events { try await process(event, using: context, generation: generation) }
    }
    func observePurchases(accountToken: String?) async {
        guard !Task.isCancelled else { return }
        guard let accountToken else { stopObserving(); return }
        startObserving(accountToken: accountToken)
        await reconcile(reportEmpty: false, accountToken: accountToken)
    }
    func loadOffer() async {
        // A background transaction lookup does not use the catalog or start a purchase.
        guard !loadingOffer, activity == nil || activity == .observingTransactions else { return }
        loadingOffer = true
        defer { loadingOffer = false }
        offer = try? await provider.monthlyOffer()
    }

    func purchase(accepting displayed: LiteMonthlyOffer, for account: LiteNativeSession.PurchaseAccount? = nil) async {
        guard !busy, !loadingOffer, offer == displayed else { return }
        let generation = account?.generation ?? accountGeneration()
        activity = .purchasing
        defer { activity = nil }
        do {
            try checkAccount(generation)
            let current = try await provider.monthlyOffer()
            try checkAccount(generation)
            offer = current
            guard current == displayed else {
                message = "Apple 的訂閱方案已變更。請確認畫面上的新方案後再決定是否訂閱。"
                return
            }
        } catch LitePurchaseFailure.accountChanged {
            failed(LitePurchaseFailure.accountChanged)
            return
        } catch {
            offer = nil
            message = "目前無法核對 Apple 的商品與價格，尚未開始購買。"
            return
        }
        let known: [LitePurchaseEvent]
        do { known = try await provider.knownPurchases() }
        catch {
            message = "無法核對 Apple 的既有訂閱，尚未發起新的購買。請稍後再試。"
            return
        }
        let c: LitePurchaseContext
        do { c = try await context(for: account, generation: generation) }
        catch LitePurchaseFailure.accountChanged { failed(LitePurchaseFailure.accountChanged); return }
        catch {
            message = "服務帳號尚未核對，尚未向 Apple 發起購買。請確認連線並登入原服務帳號後再試。"
            return
        }
        do {
            if !known.isEmpty {
                activity = .checkingTransactions
                try await reconcileKnown(known, using: c, generation: generation)
                return
            }
            let outcome = try await provider.purchase(offer: displayed, token: UUID(uuidString:c.app_account_token)!) {
                try self.checkAccount(generation)
                guard c.isCurrent(at: self.now()) else { throw LitePurchaseFailure.contextExpired }
            }
            try checkAccount(generation)
            switch outcome {
            case .completed(let e):try await process(e,using:c,generation:generation)
            case .pending:message="購買等待核准，尚未增加額度；核准後會核對原交易。"
            case .cancelled:message="已取消購買；原紀錄、額度與研究同意不變。"
            }
        } catch LitePurchaseFailure.contextExpired {
            message = "購買前的帳號核對已逾時。請再按一次訂閱，重新核對帳號與月費。"
        } catch LitePurchaseFailure.offerChanged {
            offer = nil
            message = "商店或商品資料已變更。請重新讀取價格後再決定是否訂閱。"
        } catch { failed(error) }
    }
    func restore(for account: LiteNativeSession.PurchaseAccount? = nil) async {
        guard !busy else { return }
        activity = .restoring
        defer { activity = nil }
        let generation = account?.generation ?? accountGeneration()
        let c: LitePurchaseContext
        do { c = try await context(for: account, generation: generation) }
        catch LitePurchaseFailure.accountChanged { failed(LitePurchaseFailure.accountChanged); return }
        catch {
            message = "服務帳號尚未核對，尚未向 Apple 查詢既有訂閱。請確認連線並登入原服務帳號後再試。"
            return
        }
        func checkRestoreContext() async throws {
            try Task.checkCancellation()
            let installation = try await service.installation()
            try Task.checkCancellation()
            try checkAccount(generation)
            guard installation == c.installation else { throw LitePurchaseFailure.accountMismatch }
            guard c.isCurrent(at: now()) else { throw LitePurchaseFailure.contextExpired }
        }
        var events: [LitePurchaseEvent]
        do {
            try await checkRestoreContext()
            events = try await provider.restore()
            try await checkRestoreContext()
            if events.isEmpty {
                // StoreKit can briefly return an empty entitlement snapshot after sync.
                // Read it once more without repeating the user-initiated Apple sync.
                activity = .recheckingEntitlements
                try await Task.sleep(for: .milliseconds(500))
                try await checkRestoreContext()
                events = try await provider.knownPurchases()
                try await checkRestoreContext()
            }
        } catch is CancellationError {
            return
        } catch LitePurchaseFailure.contextExpired {
            message = "恢復購買的帳號核對已逾時。請再按一次「恢復購買」。"
            return
        } catch LitePurchaseFailure.accountMismatch {
            failed(LitePurchaseFailure.accountMismatch)
            return
        } catch {
            guard !Task.isCancelled else { return }
            guard generation == accountGeneration() else { failed(LitePurchaseFailure.accountChanged); return }
            message = "未能確認 Apple 的既有訂閱。請稍後再按「恢復購買」。"
            return
        }
        guard !events.isEmpty else {
            message = "目前沒有找到可恢復的訂閱。若已訂閱，請在「管理 Apple 訂閱」確認購買帳號後再試。"
            return
        }
        do {
            try await reconcileKnown(events, using: c, generation: generation)
        } catch { failed(error) }
    }
    func reconcile(reportEmpty: Bool = true, accountToken: String? = nil) async {
        guard !busy else { return }
        activity = reportEmpty ? .checkingTransactions : .observingTransactions
        defer { activity = nil }
        let generation = accountGeneration()
        do {
            let events = try await provider.unfinished()
            if events.isEmpty && reportEmpty { message = "Apple 目前沒有待完成的交易。要查找既有訂閱，請按「恢復購買」。" }
            for event in events {
                try Task.checkCancellation()
                if let accountToken {
                    guard listeningToken == accountToken else { return }
                    guard event.token == accountToken else { continue }
                }
                try await process(event, generation: generation)
            }
        }
        catch { if !Task.isCancelled { failed(error) } }
    }
    func startObserving(accountToken: String) {
        let generation = accountGeneration()
        guard listeningToken != accountToken || listeningGeneration != generation else { return }
        stopObserving()
        listeningToken = accountToken
        listeningGeneration = generation
        let updates = provider.updates()
        listener=Task { [weak self] in
            for await update in updates {
                guard !Task.isCancelled else{return}
                do {
                    switch update {
                    case .event(let e):
                        guard e.token == accountToken else { continue }
                        try await self?.process(e, generation: generation)
                    case .invalid:throw LitePurchaseFailure.invalid
                    }
                } catch { if !Task.isCancelled { self?.failed(error) } }
            }
        }
    }
    func stopObserving() {
        listener?.cancel()
        listener = nil
        listeningToken = nil
        listeningGeneration = nil
    }
}
