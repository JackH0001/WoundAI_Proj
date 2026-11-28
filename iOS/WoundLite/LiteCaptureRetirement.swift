import Foundation

/// A small encrypted, monotonic set of IDs that must never become captures again.
/// Written before removing a saved measurement; contains no photo or contour.
@MainActor
final class LiteCaptureRetirement {
    static func file(for index: URL) -> URL {
        index.deletingLastPathComponent().appendingPathComponent(".lite-capture-retirement")
            .appendingPathComponent(index.lastPathComponent + ".enc")
    }
    private struct Envelope: Codable { let version: Int; let ids: Set<String> }
    private let file: URL
    private let write: (Data, URL) throws -> Void
    private var loaded: Data?
    private(set) var ids: Set<String> = []
    init(file: URL, write: @escaping (Data, URL) throws -> Void) {
        self.file = file; self.write = write
    }
    private func read() throws -> Data? {
        do { try HealthDataFiles.excludeExistingFile(file); return try Data(contentsOf: file) }
        catch let e as NSError where e.domain == NSCocoaErrorDomain && e.code == NSFileReadNoSuchFileError { return nil }
    }
    func reload() throws {
        let data = try read()
        guard data != nil || loaded == nil else { throw LiteServiceFailure.conflict }
        var next = Set<String>()
        if let data {
            guard let clear = PhiCrypto.decryptBytes(data) else { throw LiteServiceFailure.invalidState }
            let envelope = try JSONDecoder().decode(Envelope.self, from: clear)
            guard envelope.version == 1, envelope.ids.allSatisfy({ !$0.isEmpty && $0.utf8.count <= 1024 }),
                  envelope.ids.isSuperset(of: ids) else { throw LiteServiceFailure.invalidState }
            next = envelope.ids
        }
        ids = next; loaded = data
    }
    func contains(_ id: String) throws -> Bool { try reload(); return ids.contains(id) }
    func retire(_ id: String) throws {
        try reload()
        guard !id.isEmpty, id.utf8.count <= 1024 else { throw LiteServiceFailure.invalidState }
        if ids.contains(id) { return }
        let next = ids.union([id])
        guard try read() == loaded,
              let bytes = try PhiCrypto.encryptBytes(JSONEncoder().encode(Envelope(version: 1, ids: next)))
        else { throw LiteServiceFailure.conflict }
        try write(bytes, file)
        try reload()
        guard ids == next else { throw LiteServiceFailure.conflict }
    }
}
