import Foundation
import CryptoKit
import ImageIO
import Darwin

// Opt-in dependencies only. No app startup, research preference, quota reset,
// account enrollment, automatic background task or public UI is changed here.
enum LiteServiceFailure: Error { case invalidState, conflict, busy, missingImage, invalidResponse }

enum LiteServiceBytes {
    static func hash(_ data: Data) -> String { SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined() }
    static func hex(_ text: String, _ count: Int = 64) -> Bool {
        LiteAttestClient.matches(text, "[0-9a-f]{\(count)}")
    }
    static func imageSize(_ jpeg: Data) throws -> (Int, Int) {
        guard (1...8*1024*1024).contains(jpeg.count),
              let source = CGImageSourceCreateWithData(jpeg as CFData, nil),
              CGImageSourceGetType(source) as String? == "public.jpeg",
              let p = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
              let w = p[kCGImagePropertyPixelWidth] as? Int, let h = p[kCGImagePropertyPixelHeight] as? Int,
              (1...8192).contains(w), (1...8192).contains(h), w*h <= 16_000_000,
              (p[kCGImagePropertyOrientation] as? Int ?? 1) == 1 else { throw LiteServiceFailure.invalidState }
        return (w,h)
    }
}

struct LiteServiceResult: Codable, Equatable, Sendable {
    let image_w: Int, image_h: Int
    let wound_polygons: [[[Double]]]
    let confidence: Double
    let route: String
    func validate() throws {
        guard (1...8192).contains(image_w), (1...8192).contains(image_h), image_w*image_h <= 16_000_000,
              confidence.isFinite, (0...1).contains(confidence), (1...64).contains(route.count),
              wound_polygons.count <= 32, wound_polygons.reduce(0, { $0+$1.count }) <= 512 else {
            throw LiteServiceFailure.invalidResponse
        }
        for polygon in wound_polygons {
            guard (3...512).contains(polygon.count) else { throw LiteServiceFailure.invalidResponse }
            for point in polygon {
                guard point.count == 2, point.allSatisfy(\.isFinite), point[0] >= 0, point[1] >= 0,
                      point[0] < Double(image_w), point[1] < Double(image_h) else { throw LiteServiceFailure.invalidResponse }
            }
        }
    }
}

struct LiteServiceReceipt: Codable, Equatable, Sendable {
    let day: Int, operation: String, request_digest: String, outcome: String
    let result_sha256: String?, grant_sha256: String
    let admitted_limit: Int
}

struct LiteServiceRecoveryAuthorization: Codable, Equatable, Sendable {
    let version: Int, parent_operation: String, parent_day: Int, parent_request_digest: String
    let image_sha256: String, fence_sha256: String, successor_operation: String, successor_day: Int
    let authorization_sha256: String
    func validate(parent: String, image: String, day: Int?) throws {
        guard version == 1, parent_operation == parent, image_sha256 == image,
              LiteServiceBytes.hex(parent,32), LiteServiceBytes.hex(successor_operation,32),
              successor_operation != parent, (0..<100_000_000).contains(parent_day),
              successor_day > parent_day, successor_day < 100_000_000,
              day == nil || day == parent_day,
              [parent_request_digest,image_sha256,fence_sha256,authorization_sha256].allSatisfy({LiteServiceBytes.hex($0)}) else {
            throw LiteServiceFailure.invalidResponse
        }
    }
}

struct LiteServiceRecoveryCheckpoint: Codable, Equatable, Sendable {
    let authorization: LiteServiceRecoveryAuthorization
    let priorRevision: Int, priorNote: String, priorDay: Int?
    let priorNext: String, priorRetryAt: Double, priorRequestDigest: String?
    let priorReply: LiteServiceReply?
}

struct LiteServiceReply: Codable, Equatable, Sendable {
    let state: String, operation: String, day: Int, retry_action: String, research_stored: Bool
    let receipt: LiteServiceReceipt?, result: LiteServiceResult?, result_json: String?
    let service_result_stored: Bool?, result_expires: Int?
    var recovery: LiteServiceRecoveryAuthorization? = nil

    func validate(for record: LiteServiceRecord) throws {
        guard operation == record.operation, (0..<100_000_000).contains(day), !research_stored,
              record.day == nil || record.day == day else { throw LiteServiceFailure.invalidResponse }
        if state == "recovery_available" || state == "new_operation_required" {
            guard record.requestDigest == nil, record.reply?.receipt == nil,
                  receipt == nil, result == nil, result_json == nil,
                  service_result_stored != true, retry_action == "recover" else { throw LiteServiceFailure.invalidResponse }
            if state == "new_operation_required" {
                guard let recovery else { throw LiteServiceFailure.invalidResponse }
                try recovery.validate(parent:record.operation,image:record.imageSHA256,day:day)
            } else if recovery != nil { throw LiteServiceFailure.invalidResponse }
            return
        }
        guard recovery == nil else { throw LiteServiceFailure.invalidResponse }
        if state == "awaiting_reservation" {
            guard record.requestDigest == nil, receipt == nil, result == nil, result_json == nil,
                  retry_action == "prepare" else { throw LiteServiceFailure.invalidResponse }
            return
        }
        guard let r = receipt, r.day == day, r.operation == operation,
              LiteServiceBytes.hex(r.request_digest), LiteServiceBytes.hex(r.grant_sha256),
              record.requestDigest == nil || record.requestDigest == r.request_digest,
              [2,10].contains(r.admitted_limit), let expires = result_expires,
              expires > day*86400, expires <= (day+2)*86400 else { throw LiteServiceFailure.invalidResponse }
        if let prior = record.reply?.receipt {
            guard prior.grant_sha256 == r.grant_sha256, prior.admitted_limit == r.admitted_limit,
                  record.reply?.result_expires == result_expires else { throw LiteServiceFailure.invalidResponse }
        }
        if state == "completed" {
            guard retry_action == "none", service_result_stored == true,
                  let result, let raw = result_json, raw.utf8.count <= 8192,
                  let hash = r.result_sha256, LiteServiceBytes.hex(hash),
                  LiteServiceBytes.hash(Data(raw.utf8)) == hash,
                  try JSONDecoder().decode(LiteServiceResult.self, from: Data(raw.utf8)) == result,
                  result.image_w == record.width, result.image_h == record.height,
                  r.outcome == (result.wound_polygons.isEmpty ? "empty" : "succeeded") else {
                throw LiteServiceFailure.invalidResponse
            }
            try result.validate()
        } else {
            guard result == nil, result_json == nil, service_result_stored == false else { throw LiteServiceFailure.invalidResponse }
            let states = ["ready": ("reserved", "run"), "pending": ("running", "status"), "cancelled": ("cancelled", "none")]
            guard let expected = states[state], r.outcome == expected.0, retry_action == expected.1 else {
                throw LiteServiceFailure.invalidResponse
            }
        }
    }
}

struct LiteServiceRecord: Codable, Equatable, Sendable {
    var version = 1
    let captureID: String
    var operation: String
    let origin: String, installation: String, imageSHA256: String
    let width: Int, height: Int
    var revision = 1
    var next = "prepare"
    var note = "local"
    var retryAt: Double = 0
    var day: Int?
    var requestDigest: String?
    var reply: LiteServiceReply?
    var recoveryHistory: [LiteServiceRecoveryCheckpoint]? = nil

    func validate() throws {
        guard [1,2].contains(version), LiteServiceBytes.hex(captureID,32), LiteServiceBytes.hex(operation,32),
              LiteServiceBytes.hex(imageSHA256), revision > 0,
              LiteAttestClient.matches(installation,"[A-Za-z0-9_-]{1,128}"),
              (1...8192).contains(width), (1...8192).contains(height), width*height <= 16_000_000,
              ["prepare","run","status","recover","none"].contains(next),
              ["local","checking","ready","pending","confirmed","quota","capacity","unavailable","review","expired","rejected","missingImage","recovering"].contains(note),
              retryAt.isFinite, retryAt >= 0, day == nil || day! >= 0,
              requestDigest == nil || LiteServiceBytes.hex(requestDigest!) else { throw LiteServiceFailure.invalidState }
        let history=recoveryHistory ?? []
        if version == 1 {
            guard history.isEmpty, operation == captureID else { throw LiteServiceFailure.invalidState }
        } else {
            guard (1...8).contains(history.count) else { throw LiteServiceFailure.invalidState }
            var previous=captureID, seen=Set([captureID]), priorRevision=0, priorDay: Int? = nil
            for item in history {
                let auth=item.authorization
                try auth.validate(parent:previous,image:imageSHA256,day:item.priorDay)
                guard item.priorRevision > priorRevision, item.priorRevision < revision,
                      item.priorReply?.receipt == nil, item.priorRequestDigest == nil,
                      item.priorRetryAt.isFinite, item.priorRetryAt >= 0,
                      ["prepare","run","status","recover","none"].contains(item.priorNext),
                      item.priorReply == nil || (item.priorReply?.operation == previous && item.priorReply?.day == auth.parent_day),
                      priorDay == nil || auth.parent_day == priorDay,
                      seen.insert(auth.successor_operation).inserted else { throw LiteServiceFailure.invalidState }
                priorRevision=item.priorRevision;previous=auth.successor_operation;priorDay=auth.successor_day
            }
            guard operation == previous, day == priorDay else { throw LiteServiceFailure.invalidState }
        }
        if let reply { try reply.validate(for:self) }
        if requestDigest != nil { guard day != nil else { throw LiteServiceFailure.invalidState } }
        if let reply { guard day == reply.day, reply.receipt == nil || requestDigest == reply.receipt?.request_digest else { throw LiteServiceFailure.invalidState } }
        if next == "run" { guard reply?.state == "ready", requestDigest != nil else { throw LiteServiceFailure.invalidState } }
        if note == "confirmed" { guard next == "none", reply?.state == "completed" else { throw LiteServiceFailure.invalidState } }
    }

    var message: String {
        switch note {
        case "confirmed": return "辨識結果與回執已核對；尚未存入量測紀錄或研究庫。"
        case "quota": return "今日辨識額度已用完或已有工作待確認；保留原照片，待額度可用後核對。"
        case "capacity": return "服務忙碌，稍後核對原工作；不需重新拍攝。"
        case "pending", "checking": return "正在核對原辨識工作；尚未確認完成，不會另建一筆重複辨識。"
        case "recovering": return "原工作已核對並保留；正在恢復跨日辨識，不需重新拍攝。"
        case "unavailable": return "連線或裝置驗證尚未完成；下次先查原工作，不會直接重傳照片。"
        case "expired": return "雲端結果查詢期限已過；本機資料保留，需核對後再處理。"
        case "rejected": return "本次影像未通過檢查；未執行辨識，原資料保留。"
        case "missingImage": return "找不到相符的原照片，已停止傳送；請保留紀錄待核對。"
        case "review": return "服務回覆或資料不一致，已停止傳送；請保留原紀錄待核對。"
        default: return "辨識工作已保存在手機，尚未確認雲端完成。"
        }
    }
}

protocol LiteServiceJournal: Sendable {
    func load(_ id: String) async throws -> Data?
    func replace(_ id: String, expected: Data?, with data: Data) async throws
}

/// Atomic protected journal; explicit directory, no singleton or production wiring.
/// OS lock also prevents two actor instances from overwriting one another.
actor LiteServiceFileJournal: LiteServiceJournal {
    let directory: URL
    init(directory: URL) { self.directory = directory }
    private func locked<T>(_ id: String, body: (URL) throws -> T) throws -> T {
        guard LiteServiceBytes.hex(id,32) else { throw LiteServiceFailure.invalidState }
        try HealthDataFiles.prepareDirectory(directory)
        let lock = directory.appendingPathComponent("journal.lock")
        let fd = Darwin.open(lock.path, O_CREAT|O_RDWR|O_NOFOLLOW|O_CLOEXEC, S_IRUSR|S_IWUSR)
        guard fd >= 0 else { throw LiteServiceFailure.invalidState }
        defer { Darwin.close(fd) }
        try HealthDataFiles.excludeExistingFile(lock)
        guard flock(fd, LOCK_EX|LOCK_NB) == 0 else { throw LiteServiceFailure.busy }
        defer { flock(fd,LOCK_UN) }
        return try body(directory.appendingPathComponent(id+".json"))
    }
    private func read(_ file: URL) throws -> Data? {
        do { try HealthDataFiles.excludeExistingFile(file) }
        catch let e as NSError where e.domain == NSCocoaErrorDomain && e.code == NSFileReadNoSuchFileError { return nil }
        let size = try file.resourceValues(forKeys:[.fileSizeKey]).fileSize ?? Int.max
        guard size <= 64_000 else { throw LiteServiceFailure.invalidState }
        let data = try Data(contentsOf:file)
        guard data.count <= 64_000 else { throw LiteServiceFailure.invalidState }; return data
    }
    func load(_ id: String) throws -> Data? { try locked(id) { try read($0) } }
    func replace(_ id: String, expected: Data?, with data: Data) throws {
        guard data.count <= 64_000 else { throw LiteServiceFailure.invalidState }
        try locked(id) { file in
            guard try read(file) == expected else { throw LiteServiceFailure.conflict }
            try HealthDataFiles.write(data,to:file)
            guard try read(file) == data else { throw LiteServiceFailure.invalidState }
        }
    }
}

/// Requires an already registered device and explicit service permission; does
/// not infer permission from research consent or create a new identity on error.
struct LiteSignedServiceTransport: LiteAuthenticatedTransport {
    let client: LiteAttestClient
    let allowService: @Sendable () async -> Bool
    func installation() async throws -> String {
        guard await allowService() else { throw LiteAttestFailure.consentRequired }
        guard let owner = try await client.existingInstallation() else { throw LiteAttestFailure.invalidState }
        guard await allowService() else { throw LiteAttestFailure.consentRequired }; return owner
    }
    func send(_ request: URLRequest, installation: String) async throws -> (Data, HTTPURLResponse) {
        guard ["prepare","run","status","recover"].contains(request.url?.lastPathComponent ?? ""),
              request.url?.path == "/api/v1/lite/service/"+(request.url?.lastPathComponent ?? ""),
              try await self.installation() == installation else { throw LiteAttestFailure.invalidRequest }
        return try await client.send(request,installation:installation,requestID:UUID(),allowSending:allowService)
    }
}

actor LiteServiceClient {
    let origin: URL
    private let transport: any LiteAuthenticatedTransport
    private let journal: any LiteServiceJournal
    private let now: @Sendable () -> Date
    private var busy = false
    init(origin: URL, transport: any LiteAuthenticatedTransport, journal: any LiteServiceJournal,
         now: @escaping @Sendable () -> Date = { Date() }) throws {
        guard origin.scheme == "https", origin.host != nil, origin.port == nil, origin.path.isEmpty,
              origin.user == nil, origin.password == nil, origin.query == nil, origin.fragment == nil,
              !origin.absoluteString.contains("%") else { throw LiteServiceFailure.invalidState }
        self.origin=origin; self.transport=transport; self.journal=journal; self.now=now
    }
    private func read(_ id: String) async throws -> (Data,LiteServiceRecord) {
        guard let data = try await journal.load(id), data.count <= 64_000 else { throw LiteServiceFailure.invalidState }
        let record = try JSONDecoder().decode(LiteServiceRecord.self,from:data); try record.validate()
        guard record.captureID == id, record.origin == origin.absoluteString else { throw LiteServiceFailure.conflict }
        return (data,record)
    }
    private func save(_ record: inout LiteServiceRecord, previous: inout Data) async throws {
        record.revision += 1; try record.validate()
        let data = try JSONEncoder().encode(record)
        try await journal.replace(record.captureID,expected:previous,with:data); previous=data
    }
    /// Caller has durably retained the normalized JPEG in its existing image store.
    /// Stable capture ID is also the operation ID; never generate a new one on retry.
    func enqueue(captureID: String, jpeg: Data) async throws -> LiteServiceRecord {
        guard !busy else { throw LiteServiceFailure.busy }; busy=true; defer { busy=false }
        let owner = try await transport.installation(), hash=LiteServiceBytes.hash(jpeg)
        let (w,h)=try LiteServiceBytes.imageSize(jpeg)
        if try await journal.load(captureID) != nil {
            let (_,r)=try await read(captureID)
            guard r.imageSHA256 == hash, r.installation == owner else { throw LiteServiceFailure.conflict }; return r
        }
        let r=LiteServiceRecord(captureID:captureID,operation:captureID,origin:origin.absoluteString,
            installation:owner,imageSHA256:hash,width:w,height:h)
        try r.validate(); try await journal.replace(captureID,expected:nil,with:JSONEncoder().encode(r)); return r
    }
    func stored(_ id: String) async throws -> LiteServiceRecord { try await read(id).1 }

    func resume(_ id: String, loadJPEG: @Sendable (String) async throws -> Data?) async throws -> LiteServiceRecord {
        guard !busy else { throw LiteServiceFailure.busy }; busy=true; defer { busy=false }
        var (previous,r)=try await read(id)
        guard try await transport.installation() == r.installation else { throw LiteAttestFailure.identityMismatch }
        if r.retryAt > now().timeIntervalSince1970+3600 {
            r.retryAt=now().timeIntervalSince1970+3600;try await save(&r,previous:&previous)
        }
        guard r.next != "none", r.retryAt <= now().timeIntervalSince1970 else { return r }
        // One explicit resume has a bounded prepare/run or status/prepare/run chain.
        for _ in 0..<3 {
            let action=r.next
            var request=URLRequest(url:origin.appendingPathComponent("api/v1/lite/service/"+action))
            request.httpMethod="POST"
            if action == "run" {
                guard let jpeg=try await loadJPEG(id), LiteServiceBytes.hash(jpeg) == r.imageSHA256 else {
                    r.note="missingImage"; r.next="none"; try await save(&r,previous:&previous); return r
                }
                let boundary="LiteService-"+UUID().uuidString
                var body=Data()
                for (key,value) in [("anon_id",r.installation),("operation",r.operation)] {
                    body.append(Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"\(key)\"\r\n\r\n\(value)\r\n".utf8))
                }
                body.append(Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"image\"; filename=\"capture.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n".utf8))
                body.append(jpeg);body.append(Data("\r\n--\(boundary)--\r\n".utf8))
                request.httpBody=body;request.setValue("multipart/form-data; boundary=\(boundary)",forHTTPHeaderField:"Content-Type")
            } else {
                var body=["anon_id":r.installation,"operation":r.operation]
                if action == "prepare" || action == "recover" { body["image_sha256"]=r.imageSHA256 }
                request.httpBody=try JSONSerialization.data(withJSONObject:body,options:.sortedKeys)
                request.setValue("application/json",forHTTPHeaderField:"Content-Type")
            }
            // Durable uncertainty marker BEFORE any media/security network work.
            r.next="status";r.note="checking";try await save(&r,previous:&previous)
            let response: (Data,HTTPURLResponse)
            do { response=try await transport.send(request,installation:r.installation) }
            catch {
                r.note="unavailable";r.retryAt=now().timeIntervalSince1970+5
                try await save(&r,previous:&previous); return r
            }
            let (data,http)=response
            guard http.url == request.url, data.count <= 32_768 else {
                r.note="review";r.next="none";try await save(&r,previous:&previous);return r
            }
            if http.statusCode == 200 || http.statusCode == 202 {
                do {
                    let reply=try JSONDecoder().decode(LiteServiceReply.self,from:data)
                    try reply.validate(for:r)
                    guard (http.statusCode == 202) == (reply.state == "pending") else { throw LiteServiceFailure.invalidResponse }
                    if reply.state == "new_operation_required" {
                        guard let auth=reply.recovery, (r.recoveryHistory?.count ?? 0) < 8 else { throw LiteServiceFailure.invalidResponse }
                        let checkpoint=LiteServiceRecoveryCheckpoint(authorization:auth,priorRevision:r.revision,
                            priorNote:r.note,priorDay:r.day,priorNext:r.next,priorRetryAt:r.retryAt,
                            priorRequestDigest:r.requestDigest,priorReply:r.reply)
                        r.recoveryHistory=(r.recoveryHistory ?? [])+[checkpoint]
                        r.version=2;r.operation=auth.successor_operation;r.day=auth.successor_day
                        r.requestDigest=nil;r.reply=nil;r.next="prepare";r.note="recovering";r.retryAt=0
                    } else {
                    r.day=reply.day;r.requestDigest=reply.receipt?.request_digest ?? r.requestDigest;r.reply=reply
                    r.next=reply.retry_action;r.retryAt=reply.state == "pending" ? now().timeIntervalSince1970+5 : 0
                    r.note=reply.state == "completed" ? "confirmed" : reply.state == "cancelled" ? "rejected" : reply.state == "pending" ? "pending" : reply.state == "recovery_available" ? "recovering" : "ready"
                    }
                } catch { r.next="none";r.note="review" }
                try await save(&r,previous:&previous)
                if r.next == "prepare" || r.next == "run" || r.next == "recover" { continue }; return r
            }
            let error=(try? JSONSerialization.jsonObject(with:data)) as? [String:Any]
            if http.statusCode == 404, error?["error"] as? String == "lite_operation_unknown", r.requestDigest == nil {
                r.next="prepare";r.note="local";try await save(&r,previous:&previous);continue
            }
            if http.statusCode == 429 || http.statusCode == 503 || http.statusCode == 401 || http.statusCode == 403 {
                let wait=Double(http.value(forHTTPHeaderField:"Retry-After") ?? "") ?? 5
                r.retryAt=now().timeIntervalSince1970+min(3600,max(1,wait.isFinite ? wait : 5))
                r.note=http.statusCode == 429 ? (error?["error"] as? String == "lite_usage_limit" ? "quota":"capacity") : "unavailable"
                // A rejected initial prepare can be retried with the same id/hash.
                if http.statusCode == 429, action == "prepare", r.requestDigest == nil { r.next="prepare" }
            } else {
                r.next="none";r.note=http.statusCode == 410 ? "expired" : http.statusCode == 422 ? "rejected" : "review"
            }
            try await save(&r,previous:&previous);return r
        }
        return r
    }
}
