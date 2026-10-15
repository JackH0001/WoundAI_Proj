import SwiftUI
import UniformTypeIdentifiers
import CryptoKit

/// Private review copies are encrypted inside Application Support, never Documents/iCloud.
/// Readable copies leave private storage only via explicit export or an opted-in submission folder.
enum InstitutionPrivateExports {
    static let preferenceKey = "institution.administrativeExport.enabled"
    static let lastResultKey = "institution.administrativeExport.lastResult"
    static let lastAttemptKey = "institution.administrativeExport.lastAttempt"
    static let folderKey = "institution.administrativeExport.folderBookmark"
    static let folderLabelKey = "institution.administrativeExport.folderLabel"
    static func revisionName(rowID: Int64, files: [String: Data]) -> String {
        let digest = SHA256.hash(data: files["sha256.json"]!).map { String(format: "%02x", $0) }.joined()
        return "measurement-\(rowID)-\(digest)"
    }
    static func submissionContents(row: Measurement, image: Data, raster: Data, payload: Data, medicalRecordNumber: String? = nil) throws -> [String: Data] {
        // Compare on the same JSON representation as annotationPayload. Foundation
        // can move a binary Double by one ULP when decoding its decimal JSON form.
        // Do not round values, use an epsilon, or change the saved/exported result.
        var savedNumbers: [String: Any] = [:]
        if let fractions = row.tissueFrac { savedNumbers["tissue_frac"] = fractions }
        if let area = row.estimatedArea { savedNumbers["area_cm2"] = area }
        let wireNumbers = try JSONSerialization.jsonObject(with:
            JSONSerialization.data(withJSONObject: savedNumbers)) as? [String: Any] ?? [:]
        guard row.doctorVerified, let bitmap = UIImage(data: image), let cg = bitmap.cgImage,
              row.imageW == cg.width, row.imageH == cg.height,
              let edit = EditRasterCodec.decode(png: raster, meta: row.rasterMeta, canvasW: cg.width, canvasH: cg.height),
              let body = try JSONSerialization.jsonObject(with: payload) as? [String: Any],
              row.imageId != nil, body["image_id"] as? String == row.imageId,
              body["code"] as? String == row.wdCode,
              body["image_w"] as? Int == row.imageW, body["image_h"] as? Int == row.imageH,
              (body["gt_polygons"] as? [[[Int]]] ?? (body["gt_polygon"] as? [[Int]]).map { [$0] } ?? []) == row.polygons,
              (body["tissue_frac"] as? [String: Double]) == (wireNumbers["tissue_frac"] as? [String: Double]),
              body["doctor_verified"] as? Bool == true,
              (body["area_cm2"] as? NSNumber)?.doubleValue == (wireNumbers["area_cm2"] as? NSNumber)?.doubleValue,
              (body["exudate"] as? NSNumber)?.intValue == row.exudate,
              let wound = body["wound_mask_png"] as? String,
              let tissue = body["tissue_mask_png"] as? String,
              wound == WoundMaskCodec.encodeBase64(mask: edit.mask, mw: edit.mw, mh: edit.mh),
              tissue == TissueMaskCodec.encodeBase64(tissue: edit.tissue, mask: edit.mask, mw: edit.mw, mh: edit.mh)
        else { throw InstitutionExport.Failure.inconsistentExistingExport }
        var files = try InstitutionExport.contents(image: bitmap, raster: edit, polygons: row.polygons,
            marker: nil, measurementID: row.id, doctorVerified: true, exudate: row.exudate, submitted: row)
        // Local encrypted administrative metadata only; never added to the research payload.
        files["delivery.json"] = try JSONSerialization.data(withJSONObject: ["medical_record_number": medicalRecordNumber ?? ""])
        files["measurement-image.jpg"] = image
        files["submission.json"] = payload
        files["wound.png"] = Data(base64Encoded: wound)
        files["tissue.png"] = Data(base64Encoded: tissue)
        files.removeValue(forKey: "sha256.json")
        let hashes = files.mapValues { SHA256.hash(data: $0).map { String(format: "%02x", $0) }.joined() }
        files["sha256.json"] = try JSONSerialization.data(withJSONObject: hashes, options: [.sortedKeys])
        try validate(files)
        return files
    }
    static func deliveryImages(_ files: [String: Data]) throws -> [String: Data] {
        try validate(files)
        guard let overlay = files["overlay.png"], !overlay.isEmpty else { throw InstitutionExport.Failure.incompleteRaster }
        if let image = files["measurement-image.jpg"], !image.isEmpty {
            return ["image.jpg": image, "overlay.png": overlay]
        }
        guard let image = files["image.png"], !image.isEmpty else { throw InstitutionExport.Failure.imageEncoding }
        return ["image.png": image, "overlay.png": overlay]
    }
    static func deliveryName(files: [String: Data], at date: Date = Date()) throws -> String {
        try validate(files)
        var info: [String: String] = [:]
        if let data = files["delivery.json"] {
            guard let decoded = try JSONSerialization.jsonObject(with: data) as? [String: String] else { throw InstitutionExport.Failure.inconsistentExistingExport }
            info = decoded
        }
        let raw = info["medical_record_number"] ?? ""
        let safe = raw.trimmingCharacters(in: .whitespacesAndNewlines).unicodeScalars.map {
            CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_").contains($0) ? String($0) : "_"
        }.joined().prefix(64)
        let label = safe.contains(where: { $0 != "_" && $0 != "-" }) ? String(safe) : "無病歷號"
        let formatter = DateFormatter(); formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.calendar = Calendar(identifier: .gregorian); formatter.timeZone = TimeZone(secondsFromGMT: 8 * 3600)
        formatter.dateFormat = "yyyyMMdd_HHmmss_SSS"
        return label + "_" + formatter.string(from: date)
    }
    static func folderBookmark(_ folder: URL) throws -> Data {
        guard folder.startAccessingSecurityScopedResource() else { throw CocoaError(.fileReadNoPermission) }
        defer { folder.stopAccessingSecurityScopedResource() }
        return try folder.bookmarkData(options: .minimalBookmark, includingResourceValuesForKeys: nil, relativeTo: nil)
    }
    /// Only fixed descriptions reach the UI; provider errors can contain private paths.
    static func failureDescription(_ error: Error) -> String {
        if let failure = error as? InstitutionExport.Failure {
            switch failure {
            case .invalidGeometry: return "影像與遮罩座標不一致"
            case .incompleteRaster: return "遮罩資料不完整"
            case .imageEncoding: return "無法產生或加密影像副本"
            case .inconsistentExistingExport: return "送件與本機副本的一致性核對失敗"
            }
        }
        let failure = error as NSError
        if failure.domain == NSCocoaErrorDomain {
            switch failure.code {
            case NSFileReadNoPermissionError, NSFileWriteNoPermissionError:
                return "資料夾授權無效，請重新選擇可寫入的資料夾"
            case NSFileWriteOutOfSpaceError: return "裝置可用空間不足"
            case NSFileNoSuchFileError, NSFileReadNoSuchFileError: return "找不到原始副本或輸出資料夾"
            default: break
            }
        }
        return "檔案寫入或讀回核對失敗"
    }
    static func deliver(files: [String: Data], name: String, bookmark: Data) throws {
        var stale = false
        let folder = try URL(resolvingBookmarkData: bookmark, options: .withoutUI, relativeTo: nil, bookmarkDataIsStale: &stale)
        guard !stale, folder.startAccessingSecurityScopedResource() else { throw CocoaError(.fileReadNoPermission) }
        defer { folder.stopAccessingSecurityScopedResource() }
        var coordinationError: NSError?
        var writingError: Error?
        NSFileCoordinator().coordinate(writingItemAt: folder, options: .forMerging, error: &coordinationError) { location in
            do { try writeDelivery(files: files, name: name, folder: location) } catch { writingError = error }
        }
        if let error = coordinationError { throw error }
        if let error = writingError { throw error }
    }
    static func writeDelivery(files: [String: Data], name: String, folder: URL) throws {
        let images = try deliveryImages(files)
        guard !name.contains("/"), !name.contains("\\"), !name.hasPrefix(".") else { throw CocoaError(.fileWriteInvalidFileName) }
        let stage = folder.appendingPathComponent(".WoundAI-partial-" + UUID().uuidString, isDirectory: true)
        let destination = folder.appendingPathComponent(name + "_" + UUID().uuidString.prefix(8), isDirectory: true)
        try FileManager.default.createDirectory(at: stage, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: stage) }
        for (key, bytes) in images { try bytes.write(to: stage.appendingPathComponent(key), options: .atomic) }
        for (key, bytes) in images where try Data(contentsOf: stage.appendingPathComponent(key)) != bytes {
            _ = key; throw InstitutionExport.Failure.inconsistentExistingExport
        }
        try FileManager.default.moveItem(at: stage, to: destination)
    }
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
    init(files: [String: Data]) throws { self.files = try InstitutionPrivateExports.deliveryImages(files) }
    init(configuration: ReadConfiguration) throws { throw CocoaError(.fileReadUnsupportedScheme) }
    func fileWrapper(configuration: WriteConfiguration) throws -> FileWrapper {
        FileWrapper(directoryWithFileWrappers: files.mapValues { FileWrapper(regularFileWithContents: $0) })
    }
}

struct InstitutionExportSettingsSection: View {
    let runtime: MedicalRuntime
    @AppStorage private var enabled: Bool
    @AppStorage private var lastResult: String
    @AppStorage private var lastAttempt: Double
    @State private var selectingFolder = false
    @State private var folderLabel = ""
    @State private var folderIssue: String?
    @State private var retrying = false
    init(runtime: MedicalRuntime) {
        self.runtime = runtime
        _enabled = AppStorage(wrappedValue: false, InstitutionPrivateExports.preferenceKey, store: runtime.exportPreferences)
        _lastResult = AppStorage(wrappedValue: "", InstitutionPrivateExports.lastResultKey, store: runtime.exportPreferences)
        _lastAttempt = AppStorage(wrappedValue: 0, InstitutionPrivateExports.lastAttemptKey, store: runtime.exportPreferences)
    }
    var body: some View {
        if InstitutionExport.enabled {
            Section("行政影像匯出（本機）") {
                Toggle("確認上傳時輸出影像與結果", isOn: $enabled)
                if enabled {
                    Text(folderLabel.isEmpty ? "輸出位置：App 私密空間" : "輸出位置：\(folderLabel)")
                    Button("選擇輸出資料夾…") { selectingFolder = true }
                    if !folderLabel.isEmpty {
                        Button("改用 App 私密空間") {
                            runtime.exportPreferences.removeObject(forKey: InstitutionPrivateExports.folderKey)
                            runtime.exportPreferences.removeObject(forKey: InstitutionPrivateExports.folderLabelKey)
                            folderLabel = ""
                        }
                    }
                }
                if !lastResult.isEmpty {
                    Text("最近一次輸出：" + Date(timeIntervalSince1970: lastAttempt).formatted(date: .abbreviated, time: .shortened))
                        .font(.caption)
                    Text(lastResult).font(.footnote).accessibilityIdentifier("AdministrativeExportStatus")
                }
                if enabled {
                    Button(retrying ? "正在重新輸出…" : "重新輸出最近一次送件（不上傳）") {
                        retrying = true
                        Task {
                            _ = await runtime.retryLatestSubmissionExport()
                            retrying = false
                        }
                    }.disabled(retrying || !runtime.hasSubmissionForExport)
                    Text("只將最近一次確認送件的影像與結果重新輸出至目前位置；不修改標註、不重送雲端資料。")
                        .font(.caption).foregroundStyle(.secondary)
                }
                if let folderIssue { Text(folderIssue).foregroundStyle(.red) }
                Text("預設關閉。開啟後於確認上傳時，只輸出 image 傷口影像與 overlay 疊圖（含測量數據）兩張；資料夾依病歷號（無則「無病歷號」）及台北輸出日期時間命名。使用同一份已確認修訂；只存時間軸不輸出，加密保存在 App 私密空間並排除系統備份，未選自訂資料夾時不會自動同步。關閉不影響時間軸或已同意的研究上傳，也不會刪除既有副本。")
                    .font(.caption).foregroundStyle(.secondary)
                NavigationLink("選擇副本並匯出…") { InstitutionExportsView(directory: runtime.local.privateExports) }
                Text("需交付時由你選擇匯出位置；匯出後為可讀取的檔案，目的地可能自行同步。舊版已放在「檔案」的副本不會自動搬移或刪除。")
                    .font(.caption).foregroundStyle(.secondary)
            }
            .task { folderLabel = runtime.exportPreferences.string(forKey: InstitutionPrivateExports.folderLabelKey) ?? "" }
            .fileImporter(isPresented: $selectingFolder, allowedContentTypes: [.folder]) { result in
                do {
                    let url = try result.get()
                    let bookmark = try InstitutionPrivateExports.folderBookmark(url)
                    runtime.exportPreferences.set(bookmark, forKey: InstitutionPrivateExports.folderKey)
                    runtime.exportPreferences.set(url.lastPathComponent, forKey: InstitutionPrivateExports.folderLabelKey)
                    folderLabel = url.lastPathComponent; folderIssue = nil
                } catch { folderIssue = "資料夾未設定，請重新選擇可寫入的位置。" }
            }
        }
    }
}

struct InstitutionExportsView: View {
    let directory: URL
    @State private var files: [URL] = []
    @State private var document: InstitutionExportDocument?
    @State private var exporting = false
    @State private var name = "MMH-review"
    @State private var issue: String?
    var body: some View {
        List {
            if files.isEmpty { Text("尚無行政匯出副本。開啟選項後，下次確認上傳時建立。") }
            if let issue { Text(issue).foregroundStyle(.red) }
            ForEach(files, id: \.self) { file in
                Button(file.deletingPathExtension().lastPathComponent) {
                    do {
                        let contents = try InstitutionPrivateExports.read(file)
                        document = try InstitutionExportDocument(files: contents)
                        name = try InstitutionPrivateExports.deliveryName(files: contents)
                        exporting = true
                    } catch { issue = "副本完整性檢查失敗，未匯出。" }
                }.font(.caption)
            }
        }
        .navigationTitle("行政副本")
        .task {
            do {
                try HealthDataFiles.prepareDirectory(directory)
                files = try FileManager.default.contentsOfDirectory(at: directory,
                    includingPropertiesForKeys:nil).filter { $0.pathExtension == "enc" }.sorted { $0.lastPathComponent < $1.lastPathComponent }
            } catch { issue = "無法讀取私密副本目錄。" }
        }
        .fileExporter(isPresented: $exporting, document: document, contentType: .folder, defaultFilename: name) { result in
            document = nil
            if case .failure = result { issue = "未完成匯出，私密副本仍保留。" }
        }
    }
}
