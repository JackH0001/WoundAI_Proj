import Foundation

/// An immutable bundle for one selected environment; no global active-scope mutation.
/// Creation alone does not switch origin, credentials, SDK identity, or the running AppState.
@MainActor
final class MedicalLocalStores {
    let scope: MedicalDataScope
    let repo: CaseRepository
    let images: LocalImageStore
    let outbox: ClinicalUploadOutbox
    let privateExports: URL
    let accounts: MedicalAccountPreferences
    let sessionEpoch = MedicalSessionEpoch()
    init(scope: MedicalDataScope, support: URL = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0],
         preferences: UserDefaults? = nil) throws {
        self.scope = scope
        accounts = MedicalAccountPreferences(scope: scope, defaults: preferences, invalidate: { [sessionEpoch] in sessionEpoch.invalidate() })
        try HealthDataFiles.prepareDirectory(scope.root(under: support))
        repo = CaseRepository(store: WoundStore(path: scope.database(under: support).path))
        images = LocalImageStore(directory: scope.images(under: support), depthIndexURL: scope.depthIndex(under: support))
        outbox = ClinicalUploadOutbox(directory: scope.outbox(under: support))
        privateExports = scope.exports(under: support)
    }
}
