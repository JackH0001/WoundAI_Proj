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

struct LiteCloudSyncState: Codable {
    var binding: LiteCloudBinding
    var acknowledgedRevision = 0
    var acknowledgedDigest: String? = nil
    var pending: LitePendingRevision? = nil
    var lastError: String? = nil
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
            return "未保存可核對的雲端影像識別碼；此筆不會自動補傳或猜測配對。"
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
    /// At most one sender per record in this store. The durable pending slot also protects restart/retry.
    /// The production sender checks the stored App Attest owner; the optional identity is a test seam.
    /// v2 has no fallback to the old append-only endpoint; an old server leaves this pending.
    func syncRevision(recordID: String, consent: () -> Bool = { LitePrefs.researchConsent == true },
                      identity: (() -> String)? = nil,
                      send: (LiteCloudBinding, LitePendingRevision) async throws -> Void = { binding, pending in
                          try await BackendClient(baseUrl: binding.server).liteRevision(bindingAnonID: binding.anonID,
                              imageID: binding.imageID, revision: pending.revision,
                              payloadJSON: pending.payloadJSON, digest: pending.digest)
                      }) async {
        guard !syncingRecordIDs.contains(recordID) else { return }
        syncingRecordIDs.insert(recordID)
        defer { syncingRecordIDs.remove(recordID) }
        // A previous pending revision must be acknowledged before we enqueue a newer edit.
        while true {
            guard consent(), var record = records.first(where: { $0.id == recordID }),
                  var sync = record.cloudSync, (identity == nil || identity?() == sync.binding.anonID) else { return }
            do {
                if sync.pending == nil {
                    guard let json = try record.revisionJSON(),
                          LitePendingRevision.digest(json) != sync.acknowledgedDigest else { return }
                    sync.pending = LitePendingRevision(revision: sync.acknowledgedRevision + 1, payloadJSON: json)
                }
                sync.lastError = nil
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
                current.lastError = nil
                latest.cloudSync = current // Preserve edits/group changes made while the request was in flight.
                guard update(latest) else { return }
            } catch {
                guard var latest = records.first(where: { $0.id == recordID }),
                      var current = latest.cloudSync else { return }
                current.lastError = (error as? LiteAttestFailure) == .identityMismatch
                    ? "本機已保存；這筆影像的原上傳身分無法由目前裝置驗證，尚未同步。舊資料不會自動轉移或重傳。"
                    : "本機已保存；雲端尚未確認此修訂（可能尚未支援同步或連線失敗）。可稍後重試，不重傳照片。"
                latest.cloudSync = current
                _ = update(latest)
                return
            }
        }
    }
}
