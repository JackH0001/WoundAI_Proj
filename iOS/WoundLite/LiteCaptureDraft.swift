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

private struct LiteCaptureSavedReceipt: Codable, Equatable {
    let version: Int
    let kind: String
    let id: String
    let jpegSHA256: String
    let depthSHA256: String
    init(_ value: LiteCaptureDraft) {
        version = 1; kind = "saved-measurement"; id = value.id
        jpegSHA256 = LiteServiceBytes.hash(value.jpeg)
        depthSHA256 = LiteServiceBytes.hash(value.encodedDepth)
    }
    func valid(for expected: String) -> Bool {
        version == 1 && kind == "saved-measurement" && id == expected && UUID(uuidString: id)?.uuidString == id
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
        guard let data = PhiCrypto.decryptBytes(try Data(contentsOf: path)), data.count <= 32 * 1024 * 1024
        else { throw LiteServiceFailure.invalidState }
        return data
    }
    func load(_ id: String) throws -> LiteCaptureDraft {
        let data = try bytes(id)
        let value = try JSONDecoder().decode(LiteCaptureDraft.self, from: data)
        guard value.id == id else { throw LiteServiceFailure.conflict }
        _ = try value.depth()
        return value
    }
    func pending(excluding saved: Set<String>) throws -> [LiteCaptureDraftSummary] {
        try HealthDataFiles.prepareDirectory(directory)
        var result = [LiteCaptureDraftSummary]()
        for file in try FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil)
            where file.pathExtension == "enc" {
            let id = file.deletingPathExtension().lastPathComponent
            if saved.contains(id) { continue }
            let raw = try bytes(id)
            if let receipt = try? JSONDecoder().decode(LiteCaptureSavedReceipt.self, from: raw), receipt.valid(for: id) { continue }
            let draft = try load(id) // Unreadable is not an empty draft library.
            if draft.savedRecordID == nil { result.append(.init(id: id, createdAt: draft.createdAt)) }
        }
        return result.sorted { $0.createdAt > $1.createdAt }
    }
    func unfinalized(savedIDs: Set<String>) throws -> [LiteCaptureDraft] {
        var result = [LiteCaptureDraft]()
        for id in savedIDs {
            guard UUID(uuidString: id)?.uuidString == id,
                  FileManager.default.fileExists(atPath: try file(id).path) else { continue }
            let raw = try bytes(id)
            if let receipt = try? JSONDecoder().decode(LiteCaptureSavedReceipt.self, from: raw), receipt.valid(for: id) { continue }
            result.append(try load(id))
        }
        return result
    }
    func create(jpeg: Data, depth: DepthCapture, id: UUID = UUID(), now: Date = Date()) throws -> LiteCaptureDraft {
        let value = LiteCaptureDraft(id: id.uuidString, createdAt: now, jpeg: jpeg,
                                    encodedDepth: try LiteDepthCodec.encode(depth, matchingJPEG: jpeg))
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
              value.createdAt == previous.createdAt, previous.savedRecordID == nil,
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
        guard let encrypted = try PhiCrypto.encryptBytes(JSONEncoder().encode(receipt)) else { throw LiteServiceFailure.invalidState }
        try write(encrypted, file(previous.id))
        guard try JSONDecoder().decode(LiteCaptureSavedReceipt.self, from: bytes(previous.id)) == receipt else {
            throw LiteServiceFailure.invalidState
        }
    }
    private func persist(_ value: LiteCaptureDraft) throws {
        guard let bytes = try PhiCrypto.encryptBytes(JSONEncoder().encode(value)) else { throw LiteServiceFailure.invalidState }
        try write(bytes, file(value.id))
        guard try load(value.id) == value else { throw LiteServiceFailure.invalidState }
    }
}
