import Foundation
import SwiftUI
import UIKit
import AuthenticationServices

/// One explicit Sandbox composition. Ordinary startup does not construct this.
/// Account/revocation requests remain usable when ordinary service is fenced.
@MainActor private final class LiteSandboxAccess {
    weak var identity: LiteAccountIdentityStore?
    func permits(_ ordinary: Bool) async -> Bool {
        if !ordinary { return true }
        return await identity?.canUseService() ?? false
    }
}

private struct LiteSandboxScopeTransport: LiteAuthenticatedTransport {
    let origin: URL
    let base: any LiteAuthenticatedTransport
    let paths: Set<String>
    let permits: @Sendable () async -> Bool
    func installation() async throws -> String {
        // Reading the already registered installation never enrolls or renews.
        // Admission is checked around HTTP, not repeatedly for local identity reads.
        try await base.installation()
    }
    func send(_ request: URLRequest, installation owner: String) async throws -> (Data, HTTPURLResponse) {
        guard let url = request.url, request.httpMethod == "POST", paths.contains(url.path),
              url == origin.appendingPathComponent(String(url.path.dropFirst())),
              try await installation() == owner, await permits() else { throw LiteAccountFailure.conflict }
        let reply = try await base.send(request, installation: owner)
        guard reply.1.url == url, try await installation() == owner, await permits() else { throw LiteAccountFailure.conflict }
        return reply
    }
}

/// Purpose-separated keys, while identity and deletion deliberately share a
/// namespace so a saved deletion intent fences all ordinary clients after restart.
private struct LiteSandboxJournal: LiteServiceJournal {
    let base: any LiteServiceJournal
    let scope: String
    private func key(_ id: String) throws -> String {
        guard LiteServiceBytes.hex(id, 32) else { throw LiteServiceFailure.invalidState }
        return String(LiteServiceBytes.hash(Data((scope + "\0" + id).utf8)).prefix(32))
    }
    func load(_ id: String) async throws -> Data? { try await base.load(key(id)) }
    func replace(_ id: String, expected: Data?, with data: Data) async throws {
        try await base.replace(key(id), expected: expected, with: data)
    }
}

private struct LiteSandboxSignedTransport: LiteAuthenticatedTransport {
    let client: LiteAttestClient
    let access: LiteSandboxAccess
    func installation() async throws -> String {
        guard let owner = try await client.existingInstallation() else { throw LiteAccountFailure.unavailable }
        return owner
    }
    func send(_ request: URLRequest, installation: String) async throws -> (Data, HTTPURLResponse) {
        let path = request.url?.path ?? ""
        let ordinary = path.hasPrefix("/api/v1/lite/service/") || path.hasPrefix("/api/v1/lite/purchase/")
        // Recheck after assertion preparation, immediately before media dispatch.
        return try await client.send(request, installation: installation, requestID: UUID(),
                                     allowSending: { await access.permits(ordinary) })
    }
}

@MainActor final class LiteNativeSession: ObservableObject {
    let origin: URL
    let identity: LiteAccountIdentityStore
    let purchases: LiteNativePurchaseStore
    let service: LiteServiceClient
    let deletionService: LiteAccountDeletionHTTPService
    private let access: LiteSandboxAccess
    private let register: @Sendable () async throws -> String
    @Published private(set) var registering = false
    @Published private(set) var message = "內部 Sandbox 驗收；服務登入、訂閱與研究同意分開處理。"

    /// Explicit injected transport also permits signed synthetic HTTP replay.
    /// No credentials, enrollment or request is made during construction.
    convenience init(origin: URL, transport: any LiteAuthenticatedTransport,
                     identityProvider: any LiteAppleIdentityProvider, purchaseProvider: any LitePurchaseProvider,
                     journal: any LiteServiceJournal,
                     register: @escaping @Sendable () async throws -> String,
                     now: @escaping @Sendable () -> Date = { Date() }) throws {
        try self.init(origin: origin, transport: transport, identityProvider: identityProvider,
                      purchaseProvider: purchaseProvider, journal: journal, register: register,
                      access: LiteSandboxAccess(), now: now)
    }

    private init(origin: URL, transport: any LiteAuthenticatedTransport,
                 identityProvider: any LiteAppleIdentityProvider, purchaseProvider: any LitePurchaseProvider,
                 journal: any LiteServiceJournal, register: @escaping @Sendable () async throws -> String,
                 access: LiteSandboxAccess, now: @escaping @Sendable () -> Date) throws {
        guard origin.scheme == "https", origin.host != nil, origin.port == nil, origin.path.isEmpty,
              origin.user == nil, origin.password == nil, origin.query == nil, origin.fragment == nil,
              !origin.absoluteString.contains("%") else { throw LiteAccountFailure.invalid }
        self.origin = origin; self.access = access; self.register = register
        func scoped(_ prefix: String, _ actions: [String], ordinary: Bool) -> LiteSandboxScopeTransport {
            LiteSandboxScopeTransport(origin: origin, base: transport,
                paths: Set(actions.map { "/api/v1/lite/" + prefix + "/" + $0 }),
                permits: { await access.permits(ordinary) })
        }
        func storage(_ purpose: String) -> LiteSandboxJournal {
            LiteSandboxJournal(base: journal, scope: origin.absoluteString + "\0" + purpose)
        }
        identity = LiteAccountIdentityStore(service: try LiteAccountHTTPService(origin: origin,
            transport: scoped("account", ["challenge", "confirm", "status", "logout", "renew"], ordinary: false)),
            provider: identityProvider, journal: storage("identity-deletion"), now: now)
        access.identity = identity
        purchases = LiteNativePurchaseStore(service: try LitePurchaseHTTPService(origin: origin, environment: "Sandbox",
            transport: scoped("purchase", ["context", "confirm"], ordinary: true)),
            provider: purchaseProvider, journal: storage("purchases"), now: now)
        service = try LiteServiceClient(origin: origin,
            transport: scoped("service", ["prepare", "run", "status", "recover", "resubmit"], ordinary: true),
            journal: storage("recognition"), now: now)
        deletionService = try LiteAccountDeletionHTTPService(origin: origin,
            transport: scoped("account/deletion", ["request", "status", "reauth/challenge", "reauth/confirm"], ordinary: false))
    }

    /// Uses the caller's already configured App Attest origin and audience.
    /// Never silently enrolls on startup, retry, purchase, deletion or inspection.
    static func sandbox(client: LiteAttestClient, anchor: ASPresentationAnchor, directory: URL) throws -> LiteNativeSession {
        let access = LiteSandboxAccess()
        return try LiteNativeSession(origin: client.origin,
            transport: LiteSandboxSignedTransport(client: client, access: access),
            identityProvider: LiteAppleSignInProvider(anchor: anchor), purchaseProvider: LiteStoreKitPurchaseProvider(),
            journal: LiteServiceFileJournal(directory: directory), register: { try await client.registeredInstallation() },
            access: access, now: { Date() })
    }

    func registerDevice() async {
        guard !registering else { return }; registering = true; defer { registering = false }
        do {
            let owner = try await register()
            guard try await identity.service.installation() == owner else { throw LiteAccountFailure.conflict }
            message = "裝置登記已核對；可接續 Apple 服務帳號登入。尚未送出照片或變更研究同意。"
        } catch { message = "裝置登記尚未確認；請保留原紀錄與身分，不要刪除 App 或重建帳號。" }
    }

    func accountView() throws -> LiteAccountManagementView {
        try LiteAccountManagementView(identity: identity, service: deletionService)
    }
    func deletion() -> LiteAccountDeletionStore {
        LiteAccountDeletionStore(service: deletionService, identity: identity, journal: identity.journal, now: identity.now)
    }

    static func captureID(recordID: String) throws -> String {
        guard let id = UUID(uuidString: recordID) else { throw LiteServiceFailure.invalidState }
        return id.uuidString.lowercased().replacingOccurrences(of: "-", with: "")
    }

    /// User-selected, already saved image only. Retrying this record never invents
    /// another operation, alters its original annotation/depth, or opts into research.
    func recognizeSaved(recordID: String, jpeg: Data) async throws -> LiteServiceRecord {
        let id = try Self.captureID(recordID: recordID)
        _ = try await service.enqueue(captureID: id, jpeg: jpeg)
        return try await service.resume(id) { _ in jpeg }
    }
}

struct LiteNativeSessionView: View {
    @ObservedObject var session: LiteNativeSession
    var body: some View {
        Form {
            Section("內部服務驗收") {
                Text("先核對裝置，再登入同一服務帳號。此入口不會自動開啟研究上傳或清除手機紀錄。")
                Button("核對裝置登記（不傳照片）") { Task { await session.registerDevice() } }
                    .disabled(session.registering)
                Text(session.message).font(.footnote)
            }
            if let account = try? session.accountView() {
                NavigationLink("服務帳號與刪除") { account }
            }
            NavigationLink("Sandbox 訂閱與恢復購買") { LiteNativePurchaseView(store: session.purchases) }
            Text("登入後可從既有紀錄開啟服務辨識驗收。正式拍攝流程與完整刪帳驗收尚未啟用。")
                .font(.footnote).foregroundStyle(.secondary)
        }.navigationTitle("內部服務驗收")
    }
}

/// Existing saved records provide durable RGB-D before the first service request.
/// Opening this view only reads the local journal; transmission needs a button.
struct LiteSavedServiceView: View {
    let recordID: String
    @ObservedObject var records: LiteStore
    let session: LiteNativeSession
    @State private var result: LiteServiceRecord?
    @State private var busy = false
    @State private var reviewed = false
    @State private var failureMessage: String?
    private var record: LiteRecord? { records.records.first { $0.id == recordID } }
    private var jpeg: Data? { record.flatMap { records.images.rawBytes($0.imageName) } }
    private func reload() async {
        do {
            let id = try LiteNativeSession.captureID(recordID: recordID)
            result = try await session.service.storedIfPresent(id); reviewed = true; failureMessage = nil
        } catch { reviewed = false; failureMessage = "服務紀錄無法讀取；原檔保留，未建立新辨識。" }
    }
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                Text("僅供內部服務驗收").font(.headline)
                Text("按下送出才會傳送這筆已保存的照片供辨識，可能使用服務額度。原面積、圈選、深度與研究同意不變；結果另存服務紀錄，不表示已送入研究庫。")
                if let result {
                    LiteServiceStatusView(record: result, client: session.service,
                        loadJPEG: { _ in await MainActor.run { jpeg } }, onRecord: { self.result = $0 })
                    if let answer = result.reply?.result, let raw = jpeg, let image = UIImage(data: raw) {
                        LitePreview(image: image, polys: answer.wound_polygons.map { $0.map { $0.map { Int($0) } } },
                                    imageW: answer.image_w, imageH: answer.image_h)
                        Text("服務圈選參照；未覆寫原紀錄或重新計算原面積。")
                    }
                }
                if result == nil || (result?.next != "none" && result?.canResubmit != true) {
                    Button(busy ? "正在核對同一筆…" : result == nil ? "送出此筆照片供服務辨識" : "核對原服務工作（不另建工作）") {
                        guard !busy, let jpeg else { return }; busy = true
                        Task { @MainActor in
                            defer { busy = false }
                            do { result = try await session.recognizeSaved(recordID: recordID, jpeg: jpeg); failureMessage = nil }
                            catch { failureMessage = "尚未確認；請先核對服務帳號登入及原紀錄，勿重新拍攝或重建工作。" }
                        }
                    }.buttonStyle(.borderedProminent).disabled(busy || !reviewed || records.needsReload || jpeg == nil)
                }
                if busy { ProgressView() }
                if let failureMessage { Text(failureMessage).foregroundStyle(.orange) }
                Button("重讀本機服務紀錄（不連網）") { Task { await reload() } }.disabled(busy)
            }.padding()
        }.navigationTitle("服務辨識驗收").task { await reload() }
    }
}
