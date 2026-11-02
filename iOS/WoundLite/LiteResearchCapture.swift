import Foundation

/// Set while capturing, never inferred from a historical record on a later login.
struct LiteResearchCapturePin: Codable, Equatable {
    let scope: LiteCaptureResearchScope
    let account: LiteDeletionContext
    let owner: String
    let consentVersion: String
    var epoch: String { scope.epoch ?? "legacy" }
}

struct LitePendingResearchCapture: Codable, Equatable {
    let pin: LiteResearchCapturePin
    let captureID: String
    let imageHash: String
    let operation: String?
    let resultHash: String?
    let width: Int
    let height: Int
    var firstRevision: String?
    var encryptedTicket: Data?
    var encryptedReceipt: Data?
    var signature: String?
    var retryAt: Date? = Date.distantPast
    var notice: String?
}

private enum CaptureDeliveryError: Error {
    case retain, renewTicket, response(Int, String?)

    var notice: String {
        switch self {
        case .response(401, _): return "請核對原服務帳號與裝置登記，再從這筆紀錄重試。"
        case .response(404, _): return "這個服務尚未開放研究保存。照片與圈選已存於手機。"
        case .response(_, "face_detected"): return "照片可能包含人臉，本次研究送件已停止。"
        case .response(_, "research_source_receipt_expired"): return "這筆辨識的來源回執已到期，紀錄保留在手機。"
        case .response(410, _): return "原研究批次已停止收件，紀錄保留在手機。"
        case .response(409, _), .response(403, _): return "研究批次或拍攝資料與原送件不符，已保留原紀錄。"
        default: return "尚未確認研究保存。請核對原帳號、研究登記與本機紀錄後重試。"
        }
    }
}

/// Original bytes and per-service identities remain pinned through all retries.
/// The index contains an encrypted ticket so an interrupted response can be
/// recovered before the phone sends any large media again.
@MainActor final class LiteResearchCaptureDelivery {
    let source: any LiteAuthenticatedTransport
    let currentAccount: () async throws -> LiteDeletionContext
    let researchOwner: (String) async throws -> String
    let permits: (LiteCaptureResearchScope) -> Bool
    let researchTransport: (LiteResearchCapturePin, @escaping @Sendable () async -> Bool) -> any LiteAuthenticatedTransport
    private var inFlight = Set<String>()

    init(source: any LiteAuthenticatedTransport,
         currentAccount: @escaping () async throws -> LiteDeletionContext,
         researchOwner: @escaping (String) async throws -> String,
         permits: @escaping (LiteCaptureResearchScope) -> Bool,
         researchTransport: @escaping (LiteResearchCapturePin, @escaping @Sendable () async -> Bool) -> any LiteAuthenticatedTransport) {
        self.source = source
        self.currentAccount = currentAccount
        self.researchOwner = researchOwner
        self.permits = permits
        self.researchTransport = researchTransport
    }

    func pin(_ scope: LiteCaptureResearchScope) async -> LiteResearchCapturePin? {
        guard permits(scope), let account = try? await currentAccount(), account.origin != scope.origin,
              let owner = try? await researchOwner(scope.origin), LiteServiceBytes.hex(owner, 32),
              account == (try? await currentAccount()), permits(scope) else { return nil }
        return .init(scope: scope, account: account, owner: owner, consentVersion: LitePrefs.consentVersion)
    }

    /// Check consent and the exact still-live local job at dispatch and on return.
    private func allowed(_ store: LiteStore, _ id: String, _ job: LitePendingResearchCapture,
                         signingResearchRequest: Bool = false) async -> Bool {
        guard job.pin.account.origin != job.pin.scope.origin,
              job.pin.account == (try? await currentAccount()) else { return false }
        // During signing, LiteAttestClient already holds and verifies the
        // research installation. Never wait on its own serialization lock.
        if !signingResearchRequest, job.pin.owner != (try? await researchOwner(job.pin.scope.origin)) { return false }
        return permits(job.pin.scope) && job.pin.consentVersion == LitePrefs.consentVersion
            && store.captureCanResume(id) && store.records.contains {
                $0.id == id && $0.researchCapture == job && $0.cloudSync == nil
            }
    }

    private func request(_ path: String, origin: String, body: Data, type: String = "application/json",
                         owner: String, transport: any LiteAuthenticatedTransport) async throws -> Data {
        guard let base = URL(string: origin) else { throw CaptureDeliveryError.retain }
        let url = base.appendingPathComponent("api/v1/lite/" + path)
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue(type, forHTTPHeaderField: "Content-Type")
        req.httpBody = body
        let (data, response) = try await transport.send(req, installation: owner)
        guard response.url == url else { throw CaptureDeliveryError.retain }
        try ResearchCapacityWait.check(response, data: data)
        guard data.count <= 32768 else { throw CaptureDeliveryError.retain }
        if response.statusCode != 200 {
            let code = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["error"] as? String
            if response.statusCode == 409, ["research/capture/status", "research/capture"].contains(path),
               code == "research_source_expired" {
                throw CaptureDeliveryError.renewTicket
            }
            throw CaptureDeliveryError.response(response.statusCode, code)
        }
        return data
    }

    private func ticketText(_ bytes: Data, job: LitePendingResearchCapture) throws -> (String, String) {
        guard bytes.count <= 16384, let text = String(data: bytes, encoding: .utf8),
              let ticket = try JSONSerialization.jsonObject(with: bytes) as? [String: Any],
              let claim = ticket["claim"] as? [String: Any], let signature = ticket["signature"] as? String,
              Data(base64Encoded: signature)?.count == 64,
              job.signature == nil || job.signature == signature,
              claim["schema"] as? String == "lite.research-source/1",
              claim["account_sha256"] as? String == job.pin.account.account,
              claim["native_installation"] as? String == job.pin.account.installation,
              claim["research_installation"] as? String == job.pin.owner,
              claim["consent_epoch"] as? String == job.pin.epoch,
              claim["consent_version"] as? String == job.pin.consentVersion,
              claim["capture_id"] as? String == job.captureID,
              claim["image_sha256"] as? String == job.imageHash,
              claim["source"] as? String == (job.operation == nil ? "manual" : "service"),
              (claim["operation"] as? String) == job.operation, (claim["result_sha256"] as? String) == job.resultHash
        else { throw CaptureDeliveryError.retain }
        // Keep the signed JSON text exactly as received, including floating point spelling.
        return (text, signature)
    }

    private func replace(_ job: LitePendingResearchCapture, with next: LitePendingResearchCapture,
                         id: String, store: LiteStore) throws {
        guard store.captureCanResume(id), var latest = store.records.first(where: { $0.id == id }),
              latest.researchCapture == job, latest.cloudSync == nil else { throw CaptureDeliveryError.retain }
        latest.researchCapture = next
        guard store.update(latest) else { throw CaptureDeliveryError.retain }
    }

    func resume(_ id: String, store: LiteStore, explicit: Bool = false) async {
        guard let record = store.records.first(where: { $0.id == id }), var job = record.researchCapture,
              record.cloudSync == nil, store.captureCanResume(id), permits(job.pin.scope),
              job.retryAt.map({ $0 <= store.revisionNow }) ?? explicit, inFlight.insert(id).inserted else { return }
        defer { inFlight.remove(id) }
        do {
            guard await allowed(store, id, job), let name = record.depthName,
                  let jpeg = store.images.rawBytes(record.imageName), LiteServiceBytes.hash(jpeg) == job.imageHash,
                  let depth = store.loadDepth(name, matchingImage: record.imageName),
                  depth.captureProvenance?.id.uuidString.lowercased() == job.captureID,
                  depth.rgbWidth == job.width, depth.rgbHeight == job.height,
                  let first = job.firstRevision else { throw CaptureDeliveryError.retain }
            let packet = try LiteRawDepthPacket.make(depth: depth, jpeg: jpeg)
            let text: String
            if let encrypted = job.encryptedTicket {
                guard let bytes = PhiCrypto.decryptBytes(encrypted) else { throw CaptureDeliveryError.retain }
                text = try ticketText(bytes, job: job).0
            } else {
                let fields: [String: Any] = ["anon_id": job.pin.account.installation, "research": job.pin.owner,
                    "consent_epoch": job.pin.epoch, "consent_version": job.pin.consentVersion,
                    "capture_id": job.captureID, "image_sha256": job.imageHash, "operation": job.operation as Any? ?? NSNull()]
                let bytes = try await request("service/research-source", origin: job.pin.account.origin,
                    body: JSONSerialization.data(withJSONObject: fields), owner: job.pin.account.installation, transport: source)
                let checked = try ticketText(bytes, job: job)
                guard await allowed(store, id, job), let encrypted = try PhiCrypto.encryptBytes(bytes) else { throw CaptureDeliveryError.retain }
                var next = job
                next.encryptedTicket = encrypted
                next.signature = checked.1
                try replace(job, with: next, id: id, store: store)
                job = next
                text = checked.0
            }
            let pinned = job
            let transport = researchTransport(job.pin) { await self.allowed(store, id, pinned, signingResearchRequest: true) }
            let lookup: [String: Any] = ["anon_id": job.pin.owner, "consent_epoch": job.pin.epoch,
                "research_consent": true, "consent_version": job.pin.consentVersion, "source_ticket": text,
                "image_bytes": jpeg.count, "bundle_sha256": packet.bundleDigest]
            guard await allowed(store, id, job) else { throw CaptureDeliveryError.retain }
            var data = try await request("research/capture/status", origin: job.pin.scope.origin,
                body: JSONSerialization.data(withJSONObject: lookup), owner: job.pin.owner, transport: transport)
            let lookupReply = try JSONSerialization.jsonObject(with: data) as? [String: Any]
            if lookupReply?["status"] as? String == "upload_required" {
                guard lookupReply?["schema"] as? String == "lite.research-capture.lookup/1",
                      lookupReply?["capture_id"] as? String == job.captureID,
                      lookupReply?["consent_epoch"] as? String == job.pin.epoch,
                      lookupReply?["image_sha256"] as? String == job.imageHash,
                      lookupReply?["bundle_sha256"] as? String == packet.bundleDigest,
                      await allowed(store, id, job) else { throw CaptureDeliveryError.retain }
                let boundary = "WoundLite-" + UUID().uuidString
                let fields = ["anon_id": job.pin.owner, "consent_epoch": job.pin.epoch, "research_consent": "true",
                              "consent_version": job.pin.consentVersion, "source_ticket": text]
                data = try await request("research/capture", origin: job.pin.scope.origin,
                    body: packet.multipart(boundary: boundary, fields: fields), type: "multipart/form-data; boundary=" + boundary,
                    owner: job.pin.owner, transport: transport)
            }
            let binding = try receipt(data, for: job, packet: packet)
            guard await allowed(store, id, job), var latest = store.records.first(where: { $0.id == id }),
                  latest.researchCapture == job, latest.cloudSync == nil else { throw CaptureDeliveryError.retain }
            // Preserve any edits made during upload. Revision one is the payload
            // frozen at save; the revision sender subsequently picks up newer edits.
            var sync = LiteCloudSyncState(binding: binding)
            sync.pending = LitePendingRevision(revision: 1, payloadJSON: first)
            sync.automaticRetryAt = store.revisionNow
            latest.cloudSync = sync
            guard let evidence = try PhiCrypto.encryptBytes(data) else { throw CaptureDeliveryError.retain }
            job.encryptedReceipt = evidence
            job.notice = nil
            job.retryAt = nil
            latest.researchCapture = job
            guard store.update(latest) else { return }
        } catch {
            var next = job
            if let wait = error as? ResearchCapacityWait {
                next.retryAt = wait.notBefore
                next.notice = "研究服務忙碌，已保留原紀錄，稍後會自動核對。"
            } else if case CaptureDeliveryError.renewTicket = error {
                next.encryptedTicket = nil
                next.retryAt = store.revisionNow.addingTimeInterval(60)
                next.notice = "正在等待更新這筆拍攝的來源證明。"
            } else if error is URLError {
                next.retryAt = store.revisionNow.addingTimeInterval(60)
                next.notice = "連線中斷，原紀錄已保存，下次會先核對雲端收據。"
            } else {
                next.retryAt = nil
                next.notice = ((error as? CaptureDeliveryError) ?? .retain).notice
            }
            try? replace(job, with: next, id: id, store: store)
        }
    }

    private func receipt(_ data: Data, for job: LitePendingResearchCapture, packet: LiteRawDepthPacket) throws -> LiteCloudBinding {
        let identity = ["lite.research-capture/1", job.pin.owner, job.pin.epoch, job.captureID]
        let canonical = try JSONSerialization.data(withJSONObject: identity, options: .withoutEscapingSlashes)
        let imageID = String(LiteServiceBytes.hash(canonical).prefix(16))
        guard let body = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              body["schema"] as? String == "lite.research-capture.receipt/1", body["status"] as? String == "stored",
              body["consent_epoch"] as? String == job.pin.epoch, body["capture_id"] as? String == job.captureID,
              body["image_id"] as? String == imageID, body["image_sha256"] as? String == job.imageHash,
              let intent = body["capture_intent_sha256"] as? String, LiteServiceBytes.hex(intent),
              body["annotation_status"] as? String == "not_confirmed", body["registration"] as? String == "not_verified",
              body["training_admission"] as? String == "not_evaluated",
              let raw = body["raw_depth_receipt"] as? [String: Any] else { throw CaptureDeliveryError.retain }
        let bytes = try JSONSerialization.data(withJSONObject: raw)
        guard packet.verifiesReceipt(bytes, installationID: job.pin.owner, captureID: imageID) else { throw CaptureDeliveryError.retain }
        let storage = LiteStorageReceipt(schemaVersion: 1, image: .stored, metadata: .stored, depth: .stored,
            validityMask: .notProvided, rgbdValidation: "not_performed", rawDepthReceipt: try JSONDecoder().decode(LiteRawDepthReceipt.self, from: bytes))
        return LiteCloudBinding(server: job.pin.scope.origin, anonID: job.pin.owner, imageID: imageID,
            imageW: job.width, imageH: job.height, storageReceipt: storage, researchEpoch: job.pin.scope.epoch)
    }
}
