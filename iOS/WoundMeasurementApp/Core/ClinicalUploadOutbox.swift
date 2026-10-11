import Foundation
import SwiftUI
import UIKit
import CryptoKit

/// Immutable, encrypted research submission. Contains no passwords or login tokens.
struct ClinicalUploadJob: Codable, Identifiable {
    var id = UUID().uuidString
    var createdAt = Date()
    var server: String
    var configuredUser: String
    var actor: String
    var measurement: Measurement
    var annotationPayload: Data
    var depthRaw: Data?
    var depthMetadata: Data?
    var requiresDepth: Bool
    var annotationRevisionID: String?
    var annotationConfirmed = false
    var depthConfirmed = false
    var completed = false
    var lastIssue: String?
    var attempts = 0
    var nextAttemptAt: Date?

    var status: String {
        if completed { return requiresDepth ? "研究標註與原始深度已確認保存" : "研究標註已保存；本筆未附原始深度" }
        if let lastIssue { return lastIssue }
        return annotationConfirmed ? "標註已保存；原始深度待傳" : "研究資料已安全排入待傳佇列"
    }
}

/// One app-wide worker: navigating away does not cancel a submission. iOS may still
/// suspend/terminate the process; each server acknowledgement is saved atomically,
/// and a foreground retry reuses the exact payload under the original server/actor.
@MainActor
final class ClinicalUploadOutbox: ObservableObject {
    static let shared = ClinicalUploadOutbox()
    @Published private(set) var jobs: [ClinicalUploadJob] = []
    @Published private(set) var activeID: String?
    @Published private(set) var phase = ""
    @Published private(set) var storageIssue: String?
    private let directory: URL
    private var worker: Task<Void, Never>?
    private var backgroundID: UIBackgroundTaskIdentifier = .invalid

    init(directory: URL? = nil) {
        self.directory = directory ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("clinical_research_outbox", isDirectory: true)
        reload()
    }

    var pendingCount: Int { jobs.filter { !$0.completed }.count }
    func latest(recordID: Int64) -> ClinicalUploadJob? { jobs.last { $0.measurement.id == recordID } }

    private func file(_ id: String) -> URL { directory.appendingPathComponent(id + ".enc") }
    private func persist(_ job: ClinicalUploadJob) throws {
        let data = try JSONEncoder().encode(job)
        guard let encrypted = try PhiCrypto.encryptBytes(data) else { throw BackendError.badResponse("outbox encrypt") }
        try HealthDataFiles.write(encrypted, to: file(job.id))
        if let i = jobs.firstIndex(where: { $0.id == job.id }) { jobs[i] = job }
        else { jobs.append(job); jobs.sort { $0.createdAt < $1.createdAt } }
    }

    private func reload() {
        do {
            try HealthDataFiles.prepareDirectory(directory)
            var loaded: [ClinicalUploadJob] = []
            for path in try FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil)
                where path.pathExtension == "enc" {
                try HealthDataFiles.excludeExistingFile(path)
                let encrypted = try Data(contentsOf: path)
                guard let raw = PhiCrypto.decryptBytes(encrypted) else { throw BackendError.badResponse("outbox decrypt") }
                let job = try JSONDecoder().decode(ClinicalUploadJob.self, from: raw)
                guard path.lastPathComponent == job.id + ".enc", UUID(uuidString: job.id) != nil else {
                    throw BackendError.badResponse("outbox identity")
                }
                loaded.append(job)
            }
            jobs = loaded.sorted { $0.createdAt < $1.createdAt }
            storageIssue = nil
        } catch { storageIssue = "研究待傳資料無法完整讀取，已停止自動上傳，請聯絡管理員。" }
    }

    @discardableResult
    func enqueue(measurement: Measurement, payload: Data, server: String, configuredUser: String,
                 actor: String, depth: (raw: Data, meta: [String: Any])?, requiresDepth: Bool,
                 annotationConfirmed: Bool = false) throws -> String {
        guard storageIssue == nil, !server.isEmpty, !configuredUser.isEmpty, !actor.isEmpty,
              measurement.imageId != nil, !requiresDepth || depth != nil else {
            throw BackendError.badResponse("待傳資料或原始深度不完整；尚未建立待傳工作")
        }
        let metadata = try depth.map { try JSONSerialization.data(withJSONObject: $0.meta, options: [.sortedKeys]) }
        if let existing = jobs.last(where: {
            !$0.completed && $0.measurement.id == measurement.id && $0.server == server && $0.actor == actor
            && $0.annotationPayload == payload && $0.depthRaw == depth?.raw && $0.depthMetadata == metadata
        }) { return existing.id }
        let job = ClinicalUploadJob(server: server, configuredUser: configuredUser, actor: actor,
            measurement: measurement, annotationPayload: payload, depthRaw: depth?.raw,
            depthMetadata: metadata, requiresDepth: requiresDepth, annotationConfirmed: annotationConfirmed)
        try persist(job)
        return job.id
    }

    /// Dependency-free binding check is also exercised by restart/retargeting tests.
    static func bindingMatches(_ job: ClinicalUploadJob, server: String, user: String, actor: String) -> Bool {
        job.server == server && job.configuredUser == user && job.actor == actor
    }

    private func permitted(_ job: ClinicalUploadJob, repo: CaseRepository) async -> Bool {
        guard !Task.isCancelled, job.server == AppSettings.backendURL(),
              job.configuredUser == AppSettings.backendUser(),
              await repo.measurement(id: job.measurement.id) != nil else { return false }
        if let cid = job.measurement.caseId, let c = await repo.getCase(id: cid) {
            return await repo.activeConsent(patientId: c.patientId)?.trainEffective == true
        }
        return ["sample", "phantom"].contains(job.measurement.source ?? "")
    }

    /// Production state machine, injectable only at the transport/authorization boundary for tests.
    func process(id: String, allowed: @escaping () async -> Bool,
                 annotate: (Data) async throws -> AnnotationOutcome,
                 sendDepth: @escaping (ClinicalUploadJob) async throws -> Void,
                 acknowledgeRecord: (Measurement) async -> Void) async throws {
        guard var job = jobs.first(where: { $0.id == id }), !job.completed else { return }
        let message = await ClinicalUploadFlow.run(annotate: {
            guard !Task.isCancelled, await allowed() else { throw CancellationError() }
            if job.annotationConfirmed && job.annotationRevisionID != nil {
                return .duplicateSkipped(note: "saved receipt", revisionID: job.annotationRevisionID)
            }
            // Recover an explicit revision receipt for older pending jobs; do not
            // bind their depth to whichever annotation happened to arrive last.
            let outcome = try await annotate(job.annotationPayload)
            job.annotationRevisionID = outcome.revisionID
            return outcome
        }, acknowledge: {
            job.annotationConfirmed = true; job.lastIssue = nil
            try self.persist(job)
            await acknowledgeRecord(job.measurement)
        }, depth: job.requiresDepth ? {
            guard !Task.isCancelled, await allowed() else { throw CancellationError() }
            if !job.depthConfirmed {
                try await sendDepth(job)
                job.depthConfirmed = true
                try self.persist(job)
            }
        } : nil, progress: { self.phase = $0 })
        if job.annotationConfirmed && (!job.requiresDepth || job.depthConfirmed) {
            job.completed = true; job.lastIssue = nil
            job.annotationPayload = Data(); job.depthRaw = nil; job.depthMetadata = nil
        } else {
            job.lastIssue = message
            job.attempts += 1
            job.nextAttemptAt = Date().addingTimeInterval(min(3600, 60 * pow(2, Double(min(job.attempts - 1, 6)))))
        }
        try persist(job)
    }

    func resume(repo: CaseRepository, imageStore: LocalImageStore, force: Bool = false) {
        guard worker == nil, storageIssue == nil, pendingCount > 0 else { return }
        worker = Task {
            backgroundID = UIApplication.shared.beginBackgroundTask(withName: "ClinicalResearchUpload") {
                Task { @MainActor in self.worker?.cancel() }
            }
            defer {
                if backgroundID != .invalid { UIApplication.shared.endBackgroundTask(backgroundID); backgroundID = .invalid }
                activeID = nil; phase = ""; worker = nil
            }
            var blockedRecords = Set<Int64>()
            for original in jobs where !original.completed {
                if Task.isCancelled { break }
                // Never let a newer edit overtake an older unconfirmed revision.
                if blockedRecords.contains(original.measurement.id) { continue }
                var job = original
                if !force, let next = job.nextAttemptAt, next > Date() {
                    blockedRecords.insert(job.measurement.id); continue
                }
                do {
                    // Same 90-day maximum as source images; no hidden perpetual raw-data copy.
                    if Date().timeIntervalSince(job.measurement.timestamp) > 90 * 86400 {
                        try FileManager.default.removeItem(at: file(job.id))
                        jobs.removeAll { $0.id == job.id }
                        continue
                    }
                    guard await permitted(job, repo: repo) else {
                        job.lastIssue = "研究資料暫停：請核對原帳號、後端、紀錄及有效同意。"
                        try persist(job); blockedRecords.insert(job.measurement.id); continue
                    }
                    activeID = job.id; phase = "驗證研究資料上傳身分…"
                    let backend = BackendClient(baseUrl: job.server)
                    guard try await backend.login(username: job.configuredUser, password: AppSettings.backendPassword()),
                          let identity = await backend.currentIdentity(), identity.canSubmitClinicalTraining,
                          Self.bindingMatches(job, server: AppSettings.backendURL(), user: AppSettings.backendUser(), actor: identity.identity)
                    else { throw BackendError.badResponse("請用原送件醫師帳號登入") }
                    try await process(id: job.id, allowed: { await self.permitted(job, repo: repo) },
                        annotate: { try await backend.submitAnnotationPayload($0) },
                        sendDepth: { queued in
                            guard let raw = queued.depthRaw, let encoded = queued.depthMetadata,
                                  let metadata = try JSONSerialization.jsonObject(with: encoded) as? [String: Any],
                                  let imageID = queued.measurement.imageId else { throw BackendError.badResponse("depth snapshot") }
                            _ = try await backend.uploadDepth(imageId: imageID, raw: raw, sidecarMeta: metadata,
                                                              annotationRevisionID: queued.annotationRevisionID)
                            DepthStore.markUploaded(imagePath: queued.measurement.imagePath)
                        }, acknowledgeRecord: { snapshot in
                            if let current = await repo.measurement(id: snapshot.id),
                               current.imageId == snapshot.imageId, current.gtPolygon == snapshot.gtPolygon,
                               current.rasterPath == snapshot.rasterPath, current.exudate == snapshot.exudate {
                                await repo.markAnnotationSubmitted(id: current.id)
                            }
                        })
                    if jobs.first(where: { $0.id == job.id })?.completed != true {
                        blockedRecords.insert(job.measurement.id)
                    }
                } catch {
                    job = jobs.first(where: { $0.id == original.id }) ?? original
                    job.lastIssue = "研究資料待重試；臨床紀錄已保存。請核對網路及原登入帳號。"
                    job.attempts += 1
                    job.nextAttemptAt = Date().addingTimeInterval(min(3600, 60 * pow(2, Double(min(job.attempts - 1, 6)))))
                    do { try persist(job) } catch { storageIssue = "研究上傳狀態無法保存，已停止自動上傳。"; break }
                    blockedRecords.insert(job.measurement.id)
                }
            }
        }
    }
}
