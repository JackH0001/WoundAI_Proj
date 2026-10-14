import Foundation

/// A screen tree and every outstanding task retain one immutable environment.
/// A switch creates a new runtime; no singleton is retargeted to another bucket/account.
@MainActor
final class MedicalRuntime {
    let id = UUID()
    let local: MedicalLocalStores
    private(set) var active = true
    private var origin: String
    private let preferences: UserDefaults
    private let clientFactory: ((String, MedicalSessionEpoch, Bool) -> BackendClient)?
    var scope: MedicalDataScope { local.scope }
    var needsGoogle: Bool {
        #if WOUND_INSTITUTION
        return scope == .mmh
        #else
        return false
        #endif
    }
    var title: String { scope == .mmh ? "MMH 機構服務" : "合成示範環境" }
    static var reviewConfigured: Bool {
        Bundle.main.object(forInfoDictionaryKey: "WoundAIReviewBackendURL") as? String == SyntheticReviewTransport.origin
    }

    init(scope: MedicalDataScope, support: URL? = nil, preferences: UserDefaults? = nil,
         clientFactory: ((String, MedicalSessionEpoch, Bool) -> BackendClient)? = nil) throws {
        if scope == .syntheticReview && !Self.reviewConfigured { throw InstitutionEntryError.unconfigured }
        guard let defaults = preferences ?? UserDefaults(suiteName: scope.settingsSuite) else {
            throw BackendError.badResponse("無法讀取環境設定")
        }
        self.preferences = defaults; self.clientFactory = clientFactory
        origin = scope == .mmh ? AppSettings.backendURL() : SyntheticReviewTransport.origin
        local = try MedicalLocalStores(scope: scope,
            support: support ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0],
            preferences: defaults)
    }

    func deactivate() {
        active = false
        local.sessionEpoch.close()
        local.outbox.suspend()
    }
    func waitUntilSuspended() async { await local.outbox.waitUntilSuspended() }
    func backendURL() -> String { origin }
    func backendUser() -> String { local.accounts.user() }
    func backendPassword() -> String { local.accounts.password() }
    func setCredentials(user: String, password: String) -> Bool {
        guard active else { return false }
        return local.accounts.setCredentials(user: user, password: password)
    }
    func clearCredentials() { local.accounts.clearCredentials() }
    func setBackendURL(_ value: String) {
        #if !WOUND_INSTITUTION
        guard scope == .mmh, active else { return }
        if origin != BackendClient.normalize(value) { local.sessionEpoch.invalidate() }
        AppSettings.setBackendURL(value); origin = AppSettings.backendURL()
        #endif
    }
    func markBackendOk() { preferences.set(Date().timeIntervalSince1970, forKey: "last_backend_ok") }

    func makeBackend() -> BackendClient {
        if let clientFactory { return clientFactory(origin, local.sessionEpoch, needsGoogle) }
        return BackendClient(baseUrl: origin,
            institutionTransport: scope == .syntheticReview ? SyntheticReviewTransport() : nil,
            sessionContext: local.sessionEpoch, bindInstitutionSession: needsGoogle,
            expectedMedicalOrg: scope == .mmh ? "mmhps20261007" : "mmh-review-demo")
    }
    private lazy var pool = MedicalSessionPool(input: { [weak self] in
        guard let self, active else { return .init(origin: "", user: "", password: "", epoch: 0) }
        return .init(origin: origin, user: backendUser(), password: backendPassword(), epoch: local.sessionEpoch.snapshot())
    }, factory: { [weak self] _ in
        self?.makeBackend() ?? BackendClient(baseUrl: "https://environment-unavailable.invalid")
    })
    func acquire() async throws -> BackendClient {
        guard active else { throw CancellationError() }
        #if WOUND_INSTITUTION
        return try await pool.acquire()
        #else
        let client = makeBackend()
        guard try await client.login(username: backendUser(), password: backendPassword()), active else { throw BackendError.notLoggedIn }
        return client
        #endif
    }
    private func currentOperation() -> () -> Bool {
        let epoch = local.sessionEpoch.snapshot()
        let googleEpoch = needsGoogle ? MedicalSessionEpoch.shared.snapshot() : nil
        return { [weak self] in
            guard let self, active, local.sessionEpoch.isCurrent(epoch) else { return false }
            return googleEpoch.map { $0 == MedicalSessionEpoch.shared.snapshot() } ?? true
        }
    }
    var connection: ClinicalUploadOutbox.Connection {
        .init(origin: { [weak self] in self?.backendURL() ?? "" },
              user: { [weak self] in self?.backendUser() ?? "" },
              acquire: { [weak self] in guard let self else { throw CancellationError() }; return try await acquire() },
              active: currentOperation())
    }
    var consent: ConsentSync.Context {
        .init(accounts: local.accounts,
              acquire: { [weak self] in guard let self else { throw CancellationError() }; return try await acquire() },
              active: currentOperation())
    }
    var exportPreferences: UserDefaults { scope == .mmh ? .standard : preferences }
    var automaticExportEnabled: Bool {
        InstitutionExport.enabled && exportPreferences.bool(forKey: InstitutionPrivateExports.preferenceKey)
    }
    /// Called only after explicit submission has durably enqueued this exact payload.
    func exportSubmission(_ row: Measurement, payload: Data) async -> String {
        guard automaticExportEnabled else { return "" }
        guard active, row.doctorVerified, let image = local.images.rawBytes(row.imagePath),
              let path = row.rasterPath, let raster = local.images.rawBytes(path) else {
            return "\n研究已入列，但輸出資料不完整；請保留紀錄。"
        }
        if let cid = row.caseId {
            guard let c = await local.repo.getCase(id: cid),
                  let consent = await local.repo.activeConsent(patientId: c.patientId),
                  consent.consentCare, consent.trainEffective else { return "\n同意狀態已改變，未輸出行政副本。" }
        }
        guard active else { return "\n原環境已關閉，未輸出行政副本。" }
        let directory = local.privateExports
        let bookmark = exportPreferences.data(forKey: InstitutionPrivateExports.folderKey)
        return await Task.detached(priority: .utility) {
            do {
                let files = try InstitutionPrivateExports.submissionContents(row: row, image: image, raster: raster, payload: payload)
                let name = InstitutionPrivateExports.revisionName(rowID: row.id, files: files)
                _ = try InstitutionPrivateExports.save(files: files, name: name, directory: directory)
                if let bookmark {
                    do {
                        try InstitutionPrivateExports.deliver(files: files, name: name, bookmark: bookmark)
                        return "\n行政影像、遮罩與結果已輸出至自訂資料夾；研究上傳状态請另行確認。"
                    } catch {
                        return "\n研究已入列；自訂資料夾輸出失敗。私密副本保留，可從設定重新選擇資料夾或匯出。"
                    }
                }
                return "\n行政影像、遮罩與結果已保存於 App 私密空間；研究上傳狀態請另行確認。"
            } catch {
                return "\n研究已入列，但行政副本未完成；請保留紀錄並重試輸出。"
            }
        }.value
    }

    func resumeUploads(force: Bool = false) {
        guard active else { return }
        local.outbox.resume(repo: local.repo, imageStore: local.images, force: force, connection: connection)
    }
}
