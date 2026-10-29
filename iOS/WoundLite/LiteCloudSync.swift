import Foundation
import CryptoKit

/// Persist the identity used for the ORIGINAL upload. Settings changes must not retarget it.
struct LiteCloudBinding: Codable, Equatable {
    var server: String
    var anonID: String
    var imageID: String
    var imageW: Int
    var imageH: Int
    var storageReceipt: LiteStorageReceipt? = nil
    var researchEpoch: String? = nil
}

struct LiteRevisionPayload: Codable, Equatable {
    var polygons: [[[Int]]]
    var image_w: Int
    var image_h: Int
    var surface_cm2: Double
    var projected_cm2: Double
    var volume_ml: Double?
    var max_depth_mm: Double?
    var wound_id: String?
    var wound_side: String?
    var wound_site: String?
    var source = "manual"
    var consent_version: String
}

struct LitePendingRevision: Codable, Equatable {
    var revision: Int
    /// Keep exact bytes across restarts/timeouts; JSON re-encoding need not be byte identical.
    var payloadJSON: String
    var digest: String { Self.digest(payloadJSON) }
    static func digest(_ text: String) -> String {
        SHA256.hash(data: Data(text.utf8)).map { String(format: "%02x", $0) }.joined()
    }
}

/// Local scheduling audit only; never changes the immutable research payload.
struct LiteCapacityWaitRepair: Codable, Equatable {
    var originalNotBefore: Date
    var originalAutomaticRetryAt: Date?
    var adjustedAt: Date
    var boundedNotBefore: Date
}

struct LiteCloudSyncState: Codable {
    var binding: LiteCloudBinding
    var acknowledgedRevision = 0
    var acknowledgedDigest: String? = nil
    var pending: LitePendingRevision? = nil
    var lastError: String? = nil
    var capacityNotBefore: Date? = nil
    var automaticRetryAt: Date? = nil
    var capacityWaitRepairs: [LiteCapacityWaitRepair]? = nil

    /// Old builds could persist distantFuture. Persist this repair before allowing
    /// retry, so opening the record again cannot move the deadline forward.
    mutating func boundPersistedCapacityWait(now: Date) -> Bool {
        guard let original = capacityNotBefore else { return false }
        let bounded = now.addingTimeInterval(ResearchCapacityWait.maximumDelay)
        guard original > bounded else { return false }
        var repairs = capacityWaitRepairs ?? []
        repairs.append(LiteCapacityWaitRepair(originalNotBefore: original,
            originalAutomaticRetryAt: automaticRetryAt, adjustedAt: now, boundedNotBefore: bounded))
        capacityWaitRepairs = repairs
        capacityNotBefore = bounded
        // Never create a new pending revision merely to repair a timer.
        if pending != nil { automaticRetryAt = min(automaticRetryAt ?? bounded, bounded) }
        return true
    }
}

/// Presentation follows the CURRENT payload receipt, not a sticky message from a previous edit.
/// This does not enqueue uploads or change consent, identity, or revision numbering.
struct LiteRevisionPresentation: Equatable {
    enum Phase: Equatable { case synced, syncing, waiting, failed, pending, localOnly, unavailable }
    var phase: Phase
    var title: String
    var detail: String
    var retryTitle: String? = nil

    static func make(record: LiteRecord, syncing: Bool, consent: Bool,
                     withdrawalBlocked: Bool, retiredResearch: Bool = false, now: Date = Date()) -> Self {
        guard let sync = record.cloudSync else {
            return Self(phase: .unavailable, title: "僅存於此手機",
                        detail: "此筆未取得雲端影像保存確認，無法直接同步修改。原紀錄保留。")
        }
        if retiredResearch {
            return Self(phase: .localOnly, title: "此筆研究資料已撤回",
                        detail: "本機紀錄與修改保留；重新加入後也不會重傳這筆舊資料。")
        }
        if withdrawalBlocked {
            return Self(phase: .localOnly, title: "研究上傳已暫停",
                        detail: "撤回處理期間不送出新的修訂；本機紀錄保留。")
        }
        let json = try? record.revisionJSON()
        let currentAcknowledged = json.map { LitePendingRevision.digest($0) == sync.acknowledgedDigest } == true
        if sync.pending == nil, currentAcknowledged, sync.acknowledgedRevision > 0 {
            return Self(phase: .synced, title: "雲端已確認第 \(sync.acknowledgedRevision) 版",
                        detail: "目前輪廓與面積已同步，無須再次上傳。")
        }
        if !consent {
            return Self(phase: .localOnly, title: "修改已保留，尚未同步",
                        detail: "研究上傳已關閉，修改只存於此手機。")
        }
        guard json != nil || sync.pending != nil else {
            return Self(phase: .unavailable, title: "圈選來源待確認",
                        detail: "尚未建立可同步的輪廓與面積資料，原紀錄保留。")
        }
        let revision = sync.pending?.revision ?? (sync.acknowledgedRevision + 1)
        if let until = sync.capacityNotBefore, until > now {
            return Self(phase: .waiting, title: "第 \(revision) 版等待服務處理",
                        detail: "\(until.formatted(date: .omitted, time: .shortened)) 後自動續傳；可先離開此頁。")
        }
        if syncing {
            return Self(phase: .syncing, title: "正在同步第 \(revision) 版…",
                        detail: "等待雲端保存確認，不需再次點按。")
        }
        if let error = sync.lastError {
            return Self(phase: .failed, title: "第 \(revision) 版尚未獲雲端確認",
                        detail: error, retryTitle: "重試同步已保存的修改")
        }
        return Self(phase: .pending, title: "修改已保存，等待同步",
                    detail: "同步沿用這張照片與深度，不會重新辨識或扣除辨識次數。",
                    retryTitle: "同步已保存的修改")
    }
}

extension LiteRecord {
    func revisionJSON() throws -> String? {
        guard (manuallyConfirmed == true || (manuallyConfirmed == false && source == "cloud")), let cloudSync,
              let raw = polysJson?.data(using: .utf8),
              let polygons = try? JSONDecoder().decode([[[Int]]].self, from: raw),
              !polygons.isEmpty else { return nil }
        let p = LiteRevisionPayload(polygons: polygons,
                                    image_w: cloudSync.binding.imageW, image_h: cloudSync.binding.imageH,
                                    surface_cm2: surfaceCm2, projected_cm2: projectedCm2,
                                    volume_ml: volumeMl, max_depth_mm: maxDepthMm,
                                    wound_id: woundID, wound_side: woundLocation?.side,
                                    wound_site: woundLocation?.site,
                                    source: manuallyConfirmed == true ? "manual" : "ai",
                                    consent_version: LitePrefs.consentVersion)
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        return String(decoding: try encoder.encode(p), as: UTF8.self)
    }

    var cloudSyncDescription: String {
        guard let sync = cloudSync else {
            return "此筆已存於手機，但尚未取得可核對的雲端保存確認，未列為已上傳。請保留原紀錄，先不要重拍或反覆修邊送出。"
        }
        if let until = sync.capacityNotBefore, until > Date() {
            return "後端容量等待中；原修訂已保留，\(until.formatted(date: .omitted, time: .standard)) 後自動續傳。"
        }
        if let error = sync.lastError { return error }
        if sync.pending != nil { return "修訂等待雲端確認；可安全重試同一修訂，不重傳照片。" }
        if let json = try? revisionJSON(), LitePendingRevision.digest(json) == sync.acknowledgedDigest {
            return "輪廓與量測資料已確認同步（修訂 \(sync.acknowledgedRevision)）。完整 RGB-D 尚未驗證。"
        }
        if manuallyConfirmed == false && source == "cloud" {
            return "已綁定原雲端影像；AI 圈選的量測與位置尚未同步，不列為人工標註。"
        }
        return manuallyConfirmed == true
            ? "已綁定原雲端影像；本機修改尚未同步。"
            : "此筆圈選來源尚未確認，未自動送出標註或量測。"
    }
}

extension LiteStore {
    /// Scheduling only. syncRevision still checks consent, original owner and epoch.
    var duePendingRevisionIDs: [String] {
        guard !needsReload else { return [] }
        let now = revisionNow
        return records.compactMap { record in
            guard record.cloudSync?.pending != nil,
                  let due = record.cloudSync?.automaticRetryAt, due <= now else { return nil }
            return record.id
        }
    }
    func resumePendingRevisions() async {
        guard !needsReload, LitePrefs.researchConsent == true else { return }
        for recordID in duePendingRevisionIDs {
            guard LitePrefs.researchConsent == true else { return }
            await syncRevision(recordID: recordID)
        }
    }
    /// At most one sender per record in this store. The durable pending slot also protects restart/retry.
    /// The production sender checks the stored App Attest owner; the optional identity is a test seam.
    /// v2 has no fallback to the old append-only endpoint; an old server leaves this pending.
    func syncRevision(recordID: String, consent: () -> Bool = { LitePrefs.researchConsent == true },
                      identity: (() -> String)? = nil,
                      bindingAllowed: (LiteCloudBinding) -> Bool = { LiteWithdrawalJournal.standard.permits($0) },
                      send: (LiteCloudBinding, LitePendingRevision) async throws -> Void = { binding, pending in
                          guard LiteBackendConfiguration.sameService(binding.server, AppSettings.backendURL()) else {
                              throw LiteAttestFailure.serviceChanged
                          }
                          guard LiteWithdrawalJournal.standard.permits(binding) else { throw LiteWithdrawalFailure.invalidState }
                          try await BackendClient(baseUrl: binding.server).liteRevision(bindingAnonID: binding.anonID,
                              imageID: binding.imageID, revision: pending.revision,
                              payloadJSON: pending.payloadJSON, digest: pending.digest, researchEpoch: binding.researchEpoch)
                      }) async {
        guard !needsReload, !syncingRecordIDs.contains(recordID) else { return }
        syncingRecordIDs.insert(recordID)
        defer { syncingRecordIDs.remove(recordID) }
        // A previous pending revision must be acknowledged before we enqueue a newer edit.
        while true {
            guard !needsReload, consent(), var record = records.first(where: { $0.id == recordID }),
                  var sync = record.cloudSync, (identity == nil || identity?() == sync.binding.anonID) else { return }
            // Old queued revisions retain their bytes and numbers for audit;
            // renewed consent must never enqueue/send them in a new epoch.
            guard bindingAllowed(sync.binding) else { return }
            guard !(sync.capacityNotBefore.map { $0 > revisionNow } ?? false) else { return }
            do {
                if sync.pending == nil {
                    guard let json = try record.revisionJSON(),
                          LitePendingRevision.digest(json) != sync.acknowledgedDigest else { return }
                    sync.pending = LitePendingRevision(revision: sync.acknowledgedRevision + 1, payloadJSON: json)
                }
                sync.lastError = nil; sync.automaticRetryAt = revisionNow.addingTimeInterval(60)
                record.cloudSync = sync
                guard update(record), let pending = sync.pending else { return }
                // No await between the last consent check and initiating the request.
                guard consent(), (identity == nil || identity?() == sync.binding.anonID) else { return }
                try await send(sync.binding, pending)
                guard var latest = records.first(where: { $0.id == recordID }),
                      var current = latest.cloudSync, current.binding == sync.binding,
                      current.pending == pending else { return }
                current.acknowledgedRevision = pending.revision
                current.acknowledgedDigest = pending.digest
                current.pending = nil
                current.lastError = nil; current.capacityNotBefore = nil; current.automaticRetryAt = nil
                latest.cloudSync = current // Preserve edits/group changes made while the request was in flight.
                guard update(latest) else { return }
            } catch {
                guard var latest = records.first(where: { $0.id == recordID }),
                      var current = latest.cloudSync else { return }
                current.automaticRetryAt = nil
                if let wait = error as? ResearchCapacityWait {
                    current.capacityNotBefore = wait.notBefore; current.automaticRetryAt = wait.notBefore
                    current.lastError = wait.localizedDescription
                } else if (error as? LiteAttestFailure) == .serviceChanged {
                    current.lastError = "本機已保存；此紀錄屬於舊服務，未轉送到目前服務。請保留原紀錄，另行核對舊資料。"
                } else if case BackendError.http(status: 401, message: _) = error {
                    current.lastError = "本機已保存；App或裝置驗證未通過（401），雲端尚未保存修改。請保留原紀錄並核對App版本與服務設定，不必重拍或反覆重試。"
                } else {
                    current.lastError = (error as? LiteAttestFailure) == .identityMismatch
                        ? "本機已保存；這筆影像的原上傳身分無法由目前裝置驗證，尚未同步。舊資料不會自動轉移或重傳。"
                        : "本機已保存；雲端尚未確認此修訂（可能尚未支援同步或連線失敗）。可稍後重試，不重傳照片。"
                }
                if error is URLError { current.automaticRetryAt = revisionNow.addingTimeInterval(60) }
                latest.cloudSync = current
                _ = update(latest)
                return
            }
        }
    }
}
