import Foundation
import SwiftUI
import UIKit
import AuthenticationServices

/// One explicit Sandbox composition. Ordinary startup does not construct this.
/// Account/revocation requests remain usable when ordinary service is fenced.
@MainActor final class LiteSandboxAccess {
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

struct LiteSandboxSignedTransport: LiteAuthenticatedTransport {
    let client: LiteAttestClient
    let access: LiteSandboxAccess
    func installation() async throws -> String {
        guard let owner = try await client.existingInstallation() else { throw LiteAccountFailure.unavailable }
        return owner
    }
    func send(_ request: URLRequest, installation: String) async throws -> (Data, HTTPURLResponse) {
        let path = request.url?.path ?? ""
        let ordinary = path.hasPrefix("/api/v1/lite/service/") || path.hasPrefix("/api/v1/lite/purchase/")
        // Session renewal runs before signing. Inside the signing lane, only
        // validate the captured session and deletion journal using the supplied
        // owner. Asking this same client for its installation here would deadlock.
        let permit = ordinary ? await access.identity?.dispatchSession(for: installation) : nil
        guard !ordinary || permit != nil else { throw LiteAccountFailure.unavailable }
        return try await client.send(request, installation: installation, requestID: UUID(),
            allowSending: {
                if !ordinary { return true }
                return await access.identity?.dispatchSession(for: installation) == permit
            })
    }
}

@MainActor final class LiteNativeSession: ObservableObject {
    enum Registration { case checking, required, confirmed, unreadable }
    let origin: URL
    let identity: LiteAccountIdentityStore
    let purchases: LiteNativePurchaseStore
    let service: LiteServiceClient
    let captures: LiteCaptureDraftStore
    var researchDelivery: LiteResearchCaptureDelivery?
    let deletionService: LiteAccountDeletionHTTPService
    private let balanceTransport: any LiteAuthenticatedTransport
    let woundTransport: any LiteAuthenticatedTransport
    private var researchLink: LiteResearchDeletionLink?
    private var deletingLocalRecords = Set<String>()
    private let access: LiteSandboxAccess
    private let register: @Sendable () async throws -> String
    private let existing: @Sendable () async throws -> String?
    @Published private(set) var registration: Registration = .checking
    @Published private(set) var registering = false
    @Published private(set) var message = "內部 Sandbox 驗收；服務登入、訂閱與研究同意分開處理。"

    /// Explicit injected transport also permits signed synthetic HTTP replay.
    /// No credentials, enrollment or request is made during construction.
    convenience init(origin: URL, transport: any LiteAuthenticatedTransport,
                     identityProvider: any LiteAppleIdentityProvider, purchaseProvider: any LitePurchaseProvider,
                     journal: any LiteServiceJournal,
                     captures: LiteCaptureDraftStore? = nil,
                     register: @escaping @Sendable () async throws -> String,
                     existing: (@Sendable () async throws -> String?)? = nil,
                     now: @escaping @Sendable () -> Date = { Date() }) throws {
        try self.init(origin: origin, transport: transport, identityProvider: identityProvider,
                      purchaseProvider: purchaseProvider, journal: journal, register: register,
                      existing: existing ?? { try await transport.installation() }, captures: captures, access: LiteSandboxAccess(), now: now)
    }

    private init(origin: URL, transport: any LiteAuthenticatedTransport,
                 identityProvider: any LiteAppleIdentityProvider, purchaseProvider: any LitePurchaseProvider,
                 journal: any LiteServiceJournal, register: @escaping @Sendable () async throws -> String,
                 existing: @escaping @Sendable () async throws -> String?,
                 captures: LiteCaptureDraftStore? = nil, access: LiteSandboxAccess, now: @escaping @Sendable () -> Date) throws {
        guard origin.scheme == "https", origin.host != nil, origin.port == nil, origin.path.isEmpty,
              origin.user == nil, origin.password == nil, origin.query == nil, origin.fragment == nil,
              !origin.absoluteString.contains("%") else { throw LiteAccountFailure.invalid }
        self.captures = captures ?? LiteCaptureDraftStore(directory:
            FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
                .appendingPathComponent("lite-capture-drafts")
                .appendingPathComponent(LiteServiceBytes.hash(Data(origin.absoluteString.utf8))))
        self.origin = origin; self.access = access; self.register = register; self.existing = existing
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
        balanceTransport = scoped("service", ["balance"], ordinary: true)
        woundTransport = scoped("service", ["wounds"], ordinary: true)
        deletionService = try LiteAccountDeletionHTTPService(origin: origin,
            transport: scoped("account/deletion", ["request", "status", "reauth/challenge", "reauth/confirm"], ordinary: false))
    }

    /// Uses the caller's already configured App Attest origin and audience.
    /// Never silently enrolls on startup, retry, purchase, deletion or inspection.
    static func sandbox(client: LiteAttestClient, anchor: ASPresentationAnchor, directory: URL) throws -> LiteNativeSession {
        try sandbox(client: client, window: { [weak anchor] in anchor }, directory: directory)
    }
    static func sandbox(client: LiteAttestClient, window: @escaping () -> ASPresentationAnchor?, directory: URL) throws -> LiteNativeSession {
        let access = LiteSandboxAccess()
        let session = try LiteNativeSession(origin: client.origin,
            transport: LiteSandboxSignedTransport(client: client, access: access),
            identityProvider: LiteAppleSignInProvider(window: window), purchaseProvider: LiteStoreKitPurchaseProvider(),
            journal: LiteServiceFileJournal(directory: directory), register: { try await client.registeredInstallation() },
            existing: { try await client.existingInstallation() },
            captures: LiteCaptureDraftStore(directory: directory.appendingPathComponent("capture-drafts")), access: access, now: { Date() })
        // Create the transport lazily. Viewing account settings never registers
        // an installation, re-enables research or sends an association request.
        let configured = AppSettings.backendURL()
        if configured == LiteBackendConfiguration.dedicatedOrigin, let original = URL(string: configured) {
            session.researchLink = try LiteResearchDeletionLink(nativeOrigin: client.origin, researchOrigin: original,
                native: LiteSandboxSignedTransport(client: client, access: access),
                research: LiteExistingResearchTransport(origin: original), journal: session.identity.journal)
            if Bundle.main.object(forInfoDictionaryKey: "LiteResearchCaptureEnabled") as? String == "YES" {
                let native = LiteSandboxScopeTransport(origin: client.origin,
                    base: LiteSandboxSignedTransport(client: client, access: access),
                    paths: ["/api/v1/lite/service/research-source"], permits: { await access.permits(true) })
                session.researchDelivery = LiteResearchCaptureDelivery(source: native,
                    currentAccount: { [identity = session.identity] in try await identity.savedResearchContext() },
                    researchOwner: { server in
                        guard server == original.absoluteString else { throw LiteAttestFailure.serviceChanged }
                        return try await LiteExistingResearchTransport(origin: original).installation()
                    },
                    permits: { $0.permits(server: AppSettings.backendURL(), consent: LitePrefs.researchConsent == true) },
                    researchTransport: { pin, guardScope in
                        LiteSignedCloudTransport(baseURL: pin.scope.origin, scopeGuard: guardScope)
                    })
            }
        }
        return session
    }

    /// A fresh installation is expected. Never turn a missing local identity into
    /// a deletion warning, or a locked/corrupt identity into a new registration.
    func checkExistingRegistration() async {
        guard !registering else { return }
        registering = true; defer { registering = false }
        do {
            guard let owner = try await existing() else {
                registration = .required
                message = "請先核對這部裝置的登記，之後即可使用 Apple 登入。"
                return
            }
            guard try await identity.service.installation() == owner else { throw LiteAccountFailure.conflict }
            registration = .confirmed
            message = "已讀取這部裝置的登記。"
        } catch {
            registration = .unreadable
            message = "暫時無法讀取原裝置登記。請解鎖後重新核對。"
        }
    }

    func registerDevice() async {
        guard registration != .unreadable else { return }
        guard !registering else { return }; registering = true; defer { registering = false }
        do {
            let owner = try await register()
            guard try await identity.service.installation() == owner else { throw LiteAccountFailure.conflict }
            registration = .confirmed
            message = "裝置登記已核對；可接續 Apple 服務帳號登入。尚未送出照片或變更研究同意。"
        } catch {
            registration = .unreadable
            message = "裝置登記尚未確認。請先重讀原裝置登記，再決定是否續接。"
        }
    }

    func trackingContext() async throws -> LiteDeletionContext? {
        let start = identity.generation
        guard let owner = try await existing() else {
            guard start == identity.generation else { throw LiteAccountFailure.conflict }
            return nil
        }
        guard try await identity.hasTrackingAccount(owner: owner) else {
            guard start == identity.generation else { throw LiteAccountFailure.conflict }
            return nil
        }
        let context = try await identity.activeServiceContext()
        guard context.installation == owner, start == identity.generation else { throw LiteAccountFailure.conflict }
        return context
    }

    func personalBalance() async throws -> LitePersonalBalance {
        let generation = identity.generation
        let context = try await identity.activeServiceContext()
        let value = try await LitePersonalBalance.fetch(context: context, transport: balanceTransport, now: identity.now)
        guard try await identity.activeServiceContext() == context, identity.generation == generation else {
            throw LiteAccountFailure.conflict
        }
        return value
    }

    func accountView() throws -> LiteAccountManagementView {
        try LiteAccountManagementView(identity: identity, service: deletionService, researchLink: researchLink)
    }

    /// Retire unfinished RGB-D before the history index is removed. The store's
    /// durable marker also prevents resurrection if another view/restart races.
    func deleteLocalRecord(_ record: LiteRecord, in store: LiteStore) async -> Bool {
        guard deletingLocalRecords.insert(record.id).inserted else { return false }
        defer { deletingLocalRecords.remove(record.id) }
        guard store.retireCapture(record.id, requireSavedRecord: true) else { return false }
        do { try await captures.retire([record.id]) }
        catch { store.captureCleanupFailed(); return false }
        return store.delete(record)
    }
    func deletion(pauseResearch: @escaping () throws -> Void = LitePrefs.pauseResearchForAccountDeletion) -> LiteAccountDeletionStore {
        LiteAccountDeletionStore(service: deletionService, identity: identity, journal: identity.journal, pauseResearch:pauseResearch, now: identity.now)
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
                Text(session.message).font(.footnote)
                if session.registration == .required {
                    Button("核對裝置登記（不傳照片）") { Task { await session.registerDevice() } }
                        .disabled(session.registering)
                } else if session.registration == .unreadable {
                    Button("重讀原裝置登記") { Task { await session.checkExistingRegistration() } }
                        .disabled(session.registering)
                }
            }
            if session.registration == .confirmed, let account = try? session.accountView() {
                NavigationLink("服務帳號與刪除") { account }
                NavigationLink("Sandbox 訂閱與恢復購買") { LiteNativePurchaseView(store: session.purchases, canManagePurchases: true) }
            } else {
                NavigationLink("Plus 方案與價格") { LiteNativePurchaseView(store: session.purchases, canManagePurchases: false) }
                Text("可先查看方案。完成裝置登記後，這裡會顯示帳號登入、刪帳及訂閱與恢復購買。")
            }
            Text("候選版可在拍攝後選擇個人雲端辨識；研究同意獨立。完整實機與刪帳驗收仍待完成。")
                .font(.footnote).foregroundStyle(.secondary)
        }.navigationTitle("內部服務驗收")
        .task { await session.checkExistingRegistration() }
    }
}

/// Existing saved records provide durable RGB-D before the first service request.
/// Opening this view only reads the local journal; transmission needs a button.
struct LiteSavedServiceView: View {
    let recordID: String
    @ObservedObject var records: LiteStore
    @ObservedObject var session: LiteNativeSession
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
                if session.registration != .confirmed {
                    Text("請先到設定的「服務登入、訂閱與刪帳」核對裝置登記。已保存的結果仍可查看。")
                        .font(.footnote)
                }
                if let result {
                    LiteServiceStatusView(record: result, client: session.service,
                        loadJPEG: { _ in await MainActor.run { jpeg } }, onRecord: { self.result = $0 })
                        .disabled(session.registration != .confirmed)
                    if let answer = result.reply?.result {
                        LiteSavedServicePreview(journal: result, answer: answer, images: records.images,
                                                name: record?.imageName)
                            .id([result.captureID, String(result.revision), record?.imageName ?? ""])
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
                    }.buttonStyle(.borderedProminent)
                        .disabled(session.registration != .confirmed || busy || !reviewed || records.needsReload ||
                                  record.map { records.images.exists($0.imageName) } != true)
                }
                if busy { ProgressView() }
                if let failureMessage { Text(failureMessage).foregroundStyle(.orange) }
                Button("重讀本機服務紀錄（不連網）") { Task { await reload() } }.disabled(busy)
            }.padding()
        }.navigationTitle("服務辨識驗收").task {
            await reload()
            await session.checkExistingRegistration()
        }
    }
}

extension ImageLoadQueue {
    func verifiedServiceImage(journal: LiteServiceRecord, store: LocalImageStore, name: String) -> UIImage? {
        guard !Task.isCancelled, journal.note == "confirmed", (try? journal.validate()) != nil else { return nil }
        return autoreleasepool {
            guard let bytes = store.rawBytes(name), let size = try? LiteServiceBytes.imageSize(bytes),
                  size.0 == journal.width, size.1 == journal.height,
                  LiteServiceBytes.hash(bytes) == journal.imageSHA256 else { return nil }
            return UIImage(data: bytes)
        }
    }
}

private struct LiteSavedServicePreview: View {
    let journal: LiteServiceRecord
    let answer: LiteServiceResult
    let images: LocalImageStore
    let name: String?
    @State private var image: UIImage?
    @State private var checked = false

    var body: some View {
        Group {
            if let image {
                LitePreview(image: image, polys: answer.wound_polygons.map { $0.map { $0.map(Int.init) } },
                            imageW: answer.image_w, imageH: answer.image_h)
                Text("服務圈選參照；未覆寫原紀錄或重新計算原面積。")
            } else if checked {
                Text("無法核對原照片，這次不顯示服務圈選。原紀錄與辨識回執保留。")
            } else {
                ProgressView("正在核對原照片…")
            }
        }.task {
            if let name {
                let verified = await ImageLoadQueue.shared.verifiedServiceImage(journal: journal, store: images, name: name)
                guard !Task.isCancelled else { return }
                image = verified
            }
            checked = true
        }
    }
}
