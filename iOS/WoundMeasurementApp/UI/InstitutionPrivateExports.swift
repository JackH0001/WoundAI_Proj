import SwiftUI
import UniformTypeIdentifiers
import CryptoKit

/// Private review copies are encrypted inside Application Support, never Documents/iCloud.
/// Decryption into a FileDocument occurs only after an explicit export action.
enum InstitutionPrivateExports {
    static let preferenceKey = "institution.administrativeExport.enabled"
    static var automaticEnabled: Bool {
        InstitutionExport.enabled && UserDefaults.standard.bool(forKey: preferenceKey)
    }
    static var directory: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("MMHPS20261007/private_exports", isDirectory: true)
    }
    static func save(folder: URL, directory: URL = directory) throws -> URL {
        let files = try Dictionary(uniqueKeysWithValues: FileManager.default.contentsOfDirectory(at: folder,
            includingPropertiesForKeys: nil).map { ($0.lastPathComponent, try Data(contentsOf: $0)) })
        return try save(files: files, name: folder.lastPathComponent, directory: directory)
    }
    static func save(files: [String: Data], name: String, directory: URL = directory) throws -> URL {
        try validate(files)
        guard !name.contains("/"), !name.contains("\\"), !name.hasPrefix(".") else {
            throw InstitutionExport.Failure.inconsistentExistingExport
        }
        let destination = directory.appendingPathComponent(name + ".enc")
        if FileManager.default.fileExists(atPath: destination.path) {
            guard try read(destination) == files else { throw InstitutionExport.Failure.inconsistentExistingExport }
            return destination
        }
        guard let encrypted = try PhiCrypto.encryptBytes(JSONEncoder().encode(files)) else {
            throw InstitutionExport.Failure.imageEncoding
        }
        try HealthDataFiles.write(encrypted, to: destination)
        return destination
    }
    static func read(_ url: URL) throws -> [String: Data] {
        guard let raw = PhiCrypto.decryptBytes(try Data(contentsOf: url)) else {
            throw InstitutionExport.Failure.inconsistentExistingExport
        }
        let files = try JSONDecoder().decode([String: Data].self, from: raw)
        try validate(files)
        return files
    }
    private static func validate(_ files: [String: Data]) throws {
        guard let manifest = files["sha256.json"],
              let hashes = try JSONSerialization.jsonObject(with: manifest) as? [String: String],
              Set(files.keys) == Set(hashes.keys).union(["sha256.json"]),
              files.keys.allSatisfy({ !$0.contains("/") && !$0.contains("\\") && !$0.hasPrefix(".") }) else {
            throw InstitutionExport.Failure.inconsistentExistingExport
        }
        for (name, expected) in hashes {
            guard let data = files[name], SHA256.hash(data:data).map({String(format:"%02x",$0)}).joined() == expected else {
                throw InstitutionExport.Failure.inconsistentExistingExport
            }
        }
    }
}

struct InstitutionExportDocument: FileDocument {
    static var readableContentTypes: [UTType] { [.folder] }
    var files: [String: Data]
    init(files: [String: Data]) { self.files = files }
    init(configuration: ReadConfiguration) throws { throw CocoaError(.fileReadUnsupportedScheme) }
    func fileWrapper(configuration: WriteConfiguration) throws -> FileWrapper {
        FileWrapper(directoryWithFileWrappers: files.mapValues { FileWrapper(regularFileWithContents: $0) })
    }
}

struct InstitutionExportSettingsSection: View {
    @AppStorage(InstitutionPrivateExports.preferenceKey) private var enabled = false
    var body: some View {
        if InstitutionExport.enabled {
            Section("行政影像匯出（本機）") {
                Toggle("建立行政影像與結果副本", isOn: $enabled)
                Text("預設關閉。開啟後於存檔時產生量測影像、遮罩疊圖與結果，加密保存在 App 私密空間並排除系統備份，不會自動同步。關閉不影響時間軸或已同意的研究上傳，也不會刪除既有副本。")
                    .font(.caption).foregroundStyle(.secondary)
                NavigationLink("選擇副本並匯出…") { InstitutionExportsView() }
                Text("需交付時由你選擇匯出位置；匯出後為可讀取的檔案，目的地可能自行同步。舊版已放在「檔案」的副本不會自動搬移或刪除。")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
    }
}

struct InstitutionExportsView: View {
    @State private var files: [URL] = []
    @State private var document: InstitutionExportDocument?
    @State private var exporting = false
    @State private var name = "MMH-review"
    @State private var issue: String?
    var body: some View {
        List {
            if files.isEmpty { Text("尚無行政匯出副本。開啟選項後，下次存入或更新時間軸時建立。") }
            if let issue { Text(issue).foregroundStyle(.red) }
            ForEach(files, id: \.self) { file in
                Button(file.deletingPathExtension().lastPathComponent) {
                    do {
                        document = InstitutionExportDocument(files: try InstitutionPrivateExports.read(file))
                        name = file.deletingPathExtension().lastPathComponent
                        exporting = true
                    } catch { issue = "副本完整性檢查失敗，未匯出。" }
                }.font(.caption)
            }
        }
        .navigationTitle("行政副本")
        .task {
            do {
                try HealthDataFiles.prepareDirectory(InstitutionPrivateExports.directory)
                files = try FileManager.default.contentsOfDirectory(at: InstitutionPrivateExports.directory,
                    includingPropertiesForKeys:nil).filter { $0.pathExtension == "enc" }.sorted { $0.lastPathComponent < $1.lastPathComponent }
            } catch { issue = "無法讀取私密副本目錄。" }
        }
        .fileExporter(isPresented: $exporting, document: document, contentType: .folder, defaultFilename: name) { result in
            document = nil
            if case .failure = result { issue = "未完成匯出，私密副本仍保留。" }
        }
    }
}
