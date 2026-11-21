import Foundation

/// Recoverable, encrypted RGB-D before any personal service request. This is not
/// a measurement, research consent, or a cloud receipt. Retakes keep prior drafts.
struct LiteCaptureDraft: Codable, Equatable, Identifiable, Sendable {
    var version = 1
    let id: String
    let createdAt: Date
    let jpeg: Data
    let encodedDepth: Data
    var revision = 1
    var polygons: [[[Int]]] = []
    var source = "manual"
    var origin = LiteContourOrigin.manual
    var manuallyEdited = false
    var savedRecordID: String?
    var researchPin: LiteResearchCapturePin?

    func depth() throws -> DepthCapture {
        guard version == 1, UUID(uuidString: id) != nil, createdAt.timeIntervalSince1970.isFinite,
              revision > 0, ["manual", "cloud", "local"].contains(source),
              savedRecordID == nil || savedRecordID == id else { throw LiteServiceFailure.invalidState }
        let d = try LiteDepthCodec.decode(encodedDepth, matchingJPEG: jpeg)
        guard d.geometryIssue(imageW: d.rgbWidth, imageH: d.rgbHeight, requireOrientation: true) == nil,
              polygons.isEmpty || DepthCapture.polygonsFit(polygons, width: d.rgbWidth, height: d.rgbHeight)
        else { throw LiteServiceFailure.invalidState }
        return d
    }
}

struct LiteCaptureDraftSummary: Identifiable, Sendable {
    let id: String
    let createdAt: Date
}

struct LiteCaptureInventory: Sendable {
    var pending: [LiteCaptureDraftSummary] = []
    var needsFinalization: [String] = []
    var unresolvedCount = 0
}

private struct LiteCaptureSavedReceipt: Codable, Equatable {
    let version: Int
    let kind: String
    let id: String
    let jpegSHA256: String
    let depthSHA256: String
    init(_ value: LiteCaptureDraft, kind: String = "saved-measurement") {
        version = 1; self.kind = kind; id = value.id
        jpegSHA256 = LiteServiceBytes.hash(value.jpeg)
        depthSHA256 = LiteServiceBytes.hash(value.encodedDepth)
    }
    func valid(for expected: String) -> Bool {
        version == 1 && ["saved-measurement", "retired-capture"].contains(kind)
            && id == expected && UUID(uuidString: id)?.uuidString == id
            && LiteServiceBytes.hex(jpegSHA256) && LiteServiceBytes.hex(depthSHA256)
    }
}

actor LiteCaptureDraftStore {
    let directory: URL
    private let write: @Sendable (Data, URL) throws -> Void
    init(directory: URL, write: @escaping @Sendable (Data, URL) throws -> Void = { try HealthDataFiles.write($0, to: $1) }) {
        self.directory = directory; self.write = write
    }
    private func file(_ id: String) throws -> URL {
        guard let uuid = UUID(uuidString: id), uuid.uuidString == id else { throw LiteServiceFailure.invalidState }
        return directory.appendingPathComponent(id + ".enc")
    }
    private func bytes(_ id: String) throws -> Data {
        let path = try file(id)
        try HealthDataFiles.excludeExistingFile(path)
        // The encrypted envelope includes a 12-byte GCM nonce and a 16-byte tag.
        let maxSealedSize = 32 * 1024 * 1024 + 28
        guard let size = try path.resourceValues(forKeys: [.fileSizeKey]).fileSize,
              size > 28, size <= maxSealedSize else { throw LiteServiceFailure.invalidState }
        let sealed = try Data(contentsOf: path, options: .mappedIfSafe)
        guard sealed.count == size, let data = PhiCrypto.decryptBytes(sealed)
        else { throw LiteServiceFailure.invalidState }
        return data
    }
    func load(_ id: String) throws -> LiteCaptureDraft {
        try decodeDraft(id, from: bytes(id))
    }
    private func decodeDraft(_ id: String, from raw: Data) throws -> LiteCaptureDraft {
        let value = try JSONDecoder().decode(LiteCaptureDraft.self, from: raw)
        guard value.id == id else { throw LiteServiceFailure.conflict }
        _ = try value.depth()
        return value
    }
    func unfinishedDraft(_ id: String) throws -> LiteCaptureDraft? {
        let raw = try bytes(id)
        if let receipt = try? JSONDecoder().decode(LiteCaptureSavedReceipt.self, from: raw), receipt.valid(for: id) { return nil }
        return try decodeDraft(id, from: raw)
    }

    /// A directory error blocks the list. A single unreadable envelope only
    /// blocks that capture. Keep summaries, never a batch of decoded RGB-D pairs.
    func inventory(saved: Set<String>, retired: Set<String>) throws -> LiteCaptureInventory {
        try HealthDataFiles.prepareDirectory(directory)
        var result = LiteCaptureInventory()
        for file in try FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil)
            where file.pathExtension == "enc" {
            let id = file.deletingPathExtension().lastPathComponent
            do {
                guard let draft = try unfinishedDraft(id) else { continue }
                if retired.contains(id) {
                    try saveReceipt(LiteCaptureSavedReceipt(draft, kind: "retired-capture"))
                } else if saved.contains(id) {
                    result.needsFinalization.append(id)
                } else if draft.savedRecordID == nil {
                    result.pending.append(.init(id: id, createdAt: draft.createdAt))
                } else {
                    result.unresolvedCount += 1
                }
            } catch { result.unresolvedCount += 1 }
        }
        result.pending.sort { $0.createdAt > $1.createdAt }
        return result
    }
    func create(jpeg: Data, depth: DepthCapture, id: UUID = UUID(), now: Date = Date(),
                researchPin: LiteResearchCapturePin? = nil) throws -> LiteCaptureDraft {
        let value = LiteCaptureDraft(id: id.uuidString, createdAt: now, jpeg: jpeg,
                                    encodedDepth: try LiteDepthCodec.encode(depth, matchingJPEG: jpeg), researchPin: researchPin)
        _ = try value.depth()
        if FileManager.default.fileExists(atPath: try file(value.id).path) {
            guard try load(value.id) == value else { throw LiteServiceFailure.conflict }
            return value // Retry after a completed write whose acknowledgement was lost.
        }
        try persist(value)
        return value
    }
    /// All callers use this single actor. Check the complete prior envelope so a
    /// stale editor cannot replace a later contour or re-open a committed draft.
    func update(_ value: LiteCaptureDraft, previous: LiteCaptureDraft) throws -> LiteCaptureDraft {
        guard value.id == previous.id, value.jpeg == previous.jpeg, value.encodedDepth == previous.encodedDepth,
              value.createdAt == previous.createdAt, value.researchPin == previous.researchPin, previous.savedRecordID == nil,
              value.revision == previous.revision, try load(value.id) == previous else { throw LiteServiceFailure.conflict }
        var next = value; next.revision += 1
        _ = try next.depth()
        try persist(next)
        return next
    }
    /// Called only after the measurement's exact JPEG and depth have been read
    /// back. Keep a small encrypted receipt instead of a hidden second photo copy.
    func finalize(_ previous: LiteCaptureDraft) throws {
        let receipt = LiteCaptureSavedReceipt(previous)
        let raw = try bytes(previous.id)
        if let existing = try? JSONDecoder().decode(LiteCaptureSavedReceipt.self, from: raw), existing == receipt { return }
        guard try load(previous.id) == previous else { throw LiteServiceFailure.conflict }
        try saveReceipt(receipt)
    }
    /// IDs come only from the measurement store's durable retirement journal.
    /// Keep service/consent receipts untouched; remove this local RGB-D copy only.
    func retire(_ ids: Set<String>) throws {
        for id in ids where UUID(uuidString: id)?.uuidString == id {
            guard FileManager.default.fileExists(atPath: try file(id).path) else { continue }
            guard let draft = try unfinishedDraft(id) else { continue }
            try saveReceipt(LiteCaptureSavedReceipt(draft, kind: "retired-capture"))
        }
    }
    private func saveReceipt(_ receipt: LiteCaptureSavedReceipt) throws {
        guard let encrypted = try PhiCrypto.encryptBytes(JSONEncoder().encode(receipt)) else { throw LiteServiceFailure.invalidState }
        try write(encrypted, file(receipt.id))
        guard try JSONDecoder().decode(LiteCaptureSavedReceipt.self, from: bytes(receipt.id)) == receipt else {
            throw LiteServiceFailure.invalidState
        }
    }
    private func persist(_ value: LiteCaptureDraft) throws {
        guard let bytes = try PhiCrypto.encryptBytes(JSONEncoder().encode(value)) else { throw LiteServiceFailure.invalidState }
        try write(bytes, file(value.id))
        guard try load(value.id) == value else { throw LiteServiceFailure.invalidState }
    }
}
