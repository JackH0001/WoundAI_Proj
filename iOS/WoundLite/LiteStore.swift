import Foundation
import UIKit

/**
 民眾版本地紀錄——輕量 JSON＋加密影像。

 **刻意不用醫療版的 `CaseRepository`／SQLite schema**：那份 schema 是 Android Room v6
 的鏡像，受 `tools/parity_check.py` 管制；民眾版沒有病患／個案／同意書概念，
 硬塞進去只會把兩件事互相綁死。這裡一個 JSON 檔就夠（單人、百筆級）。
 影像沿用 `LocalImageStore`（AES 加密落地，與醫療版同一套實作、不同沙盒）。
 */
struct LiteRecord: Codable, Identifiable {
    var id: String
    var dateISO: String
    /// 主數字：三角化 3D 表面積（cm²）。民眾版的「傷口面積」。
    var surfaceCm2: Double
    var projectedCm2: Double
    var tiltDeg: Double?
    var volumeMl: Double?
    var maxDepthMm: Double?
    /// 拍攝品質摘要（"ok" 或警告短語），列表徽章用。
    var quality: String
    /// LocalImageStore 內的加密檔名。
    var imageName: String
    /// 輪廓來源："manual"（離線手動）／"cloud"（雲端辨識）／"local"（地端模型）。
    var source: String
    /// 輪廓（work 影像座標，[[[Int]]] 的 JSON）。詳情頁重繪與「重新圈選」預載用。
    /// Optional：舊紀錄沒有 → 只能檢視。
    var polysJson: String?
    /// 深度側檔（加密）檔名。重新圈選後要重算面積，靠它把 DepthCapture 讀回來。
    /// Optional：舊紀錄沒有 → 無法重新量測。
    var depthName: String?
    /// 舊版缺少此欄位時保持未分組，不猜測是否為同一傷口。
    var woundID: String? = nil
    var woundName: String? = nil
    var woundLocation: LiteWoundLocation? = nil
    var cloudSync: LiteCloudSyncState? = nil
    var manuallyConfirmed: Bool? = nil
}

@MainActor
final class LiteStore: ObservableObject {
    @Published private(set) var records: [LiteRecord] = []
    @Published private(set) var storageError: String?
    @Published private(set) var needsReload = false
    let images = LocalImageStore()
    @Published var syncingRecordIDs: Set<String> = []
    private let storageURL: URL?
    // A failed read is not an empty library. Keep the last verified bytes so a stale
    // store cannot silently overwrite a later save by another store in this process.
    private var loadedIndex: Data?
    private enum IndexFailure: Error { case invalidIDs, changed }

    var wounds: [LiteWoundGroup] { LiteGrouping.groups(records) }

    func newWound() -> LiteWoundGroup {
        let last = wounds.compactMap { Int($0.name.replacingOccurrences(of: "傷口 ", with: "")) }.max() ?? 0
        return LiteWoundGroup(id: UUID().uuidString, name: "傷口 \(last + 1)")
    }

    @discardableResult
    func assign(_ record: LiteRecord, to wound: LiteWoundGroup) -> Bool {
        guard let index = records.firstIndex(where: { $0.id == record.id }),
              let location = wound.location, location.isComplete else {
            storageError = "請先補齊傷口側別與詳細部位，尚未變更分組。"
            return false
        }
        var next = records
        // A completed target group is authoritative; don't overwrite it from a stale sheet.
        let resolved = wounds.first(where: { $0.id == wound.id })?.location ?? location
        for i in next.indices where next[i].woundID == wound.id { next[i].woundLocation = resolved }
        next[index].woundID = wound.id
        next[index].woundName = wound.name
        next[index].woundLocation = resolved
        return persist(next)
    }

    private var fileURL: URL {
        if let storageURL { return storageURL }
        let base = FileManager.default.urls(for: .applicationSupportDirectory,
                                            in: .userDomainMask)[0]
        return base.appendingPathComponent("lite_records.json")
    }

    init(fileURL: URL? = nil) { storageURL = fileURL; reload() }

    @discardableResult
    func add(_ r: LiteRecord) -> Bool {
        persist([r] + records)
    }

    /// The user-facing save gate. Resolve the existing group here, not from a stale Picker snapshot.
    /// Legacy records remain readable; completing their location and adding the new record is one write.
    func saveMeasurement(_ record: LiteRecord, selectedWoundID: String,
                         enteredLocation: LiteWoundLocation) -> LiteRecord? {
        let wound: LiteWoundGroup
        if selectedWoundID.isEmpty {
            wound = newWound()
        } else if let existing = wounds.first(where: { $0.id == selectedWoundID }) {
            wound = existing
        } else {
            storageError = "所選傷口已不存在，請重新選擇；尚未儲存。"
            return nil
        }
        let location = wound.location ?? enteredLocation
        guard location.isComplete else {
            storageError = "請先選擇側別與詳細部位，再存入紀錄。"
            return nil
        }
        var next = records
        for i in next.indices where next[i].woundID == wound.id { next[i].woundLocation = location }
        var saved = record
        saved.woundID = wound.id
        saved.woundName = wound.name
        saved.woundLocation = location
        guard persist([saved] + next) else { return nil }
        return saved
    }

    @discardableResult
    func delete(_ r: LiteRecord) -> Bool {
        guard let current = records.first(where: { $0.id == r.id }) else {
            storageError = "這筆紀錄已不存在，未刪除任何影像或深度資料。"
            return false
        }
        let remaining = records.filter { $0.id != r.id }
        guard persist(remaining) else { return false }
        let stillReferenced = Set(remaining.flatMap { [$0.imageName] + [$0.depthName].compactMap { $0 } })
        let attachments = Set([current.imageName] + [current.depthName].compactMap { $0 })
        for name in attachments where !stillReferenced.contains(name) { images.delete(name) }
        if attachments.contains(where: { !stillReferenced.contains($0) && images.exists($0) }) {
            storageError = "紀錄已移除，但部分本機影像或深度檔案未能清除；刪除尚未完整完成。"
            return false
        }
        return true
    }

    /// 重新量測後覆寫同 id 的那一筆（詳情頁「重新圈選」用）。
    @discardableResult
    func update(_ r: LiteRecord) -> Bool {
        guard let i = records.firstIndex(where: { $0.id == r.id }) else {
            storageError = "這筆紀錄已不存在，未儲存修改。"
            return false
        }
        var next = records
        next[i] = r
        return persist(next)
    }

    // MARK: 深度側檔

    func saveDepth(_ d: DepthCapture, matchingJPEG jpeg: Data) -> String? {
        do {
            let data = try LiteDepthCodec.encode(d, matchingJPEG: jpeg)
            guard let name = images.saveRaw(data) else { throw LiteDepthCodec.Failure.invalidFormat }
            return name
        } catch {
            storageError = "照片與深度未完整保存，這次尚未存入紀錄。請確認儲存空間或重新拍攝。"
            return nil
        }
    }

    func loadDepth(_ name: String, matchingImage imageName: String) -> DepthCapture? {
        guard let data = images.rawBytes(name), let jpeg = images.rawBytes(imageName),
              let depth = try? LiteDepthCodec.decode(data, matchingJPEG: jpeg) else { return nil }
        return depth
    }

    private func readIndex() throws -> Data? {
        try HealthDataFiles.prepareDirectory(fileURL.deletingLastPathComponent())
        do {
            try HealthDataFiles.excludeExistingFile(fileURL)
            return try Data(contentsOf: fileURL)
        }
        catch let error as NSError where error.domain == NSCocoaErrorDomain && error.code == NSFileReadNoSuchFileError {
            return nil // Only a missing file can mean a first launch, not permission or decoding failures.
        }
    }

    private func validateIDs(_ rows: [LiteRecord]) throws {
        guard rows.allSatisfy({ !$0.id.isEmpty }), Set(rows.map(\.id)).count == rows.count else {
            throw IndexFailure.invalidIDs
        }
    }

    /// Retry after unlock or file recovery without replacing unreadable data with [].
    @discardableResult
    func reload() -> Bool {
        do {
            let data = try readIndex()
            guard data != nil || loadedIndex == nil else { throw IndexFailure.changed }
            let rows = try data.map { try JSONDecoder().decode([LiteRecord].self, from: $0) } ?? []
            try validateIDs(rows)
            records = rows
            loadedIndex = data
            needsReload = false
            storageError = nil
            return true
        } catch {
            needsReload = true
            storageError = "無法完整讀取紀錄，已暫停儲存與刪除以保護原資料。請先解鎖裝置並重試讀取；若仍失敗，請勿移除 App。"
            return false
        }
    }

    private func persist(_ next: [LiteRecord]) -> Bool {
        guard !needsReload else { return false }
        // 原子寫入：中途被殺不會留半個 JSON（下次 load 解不開＝整份紀錄消失）。
        do {
            try validateIDs(next)
            guard try readIndex() == loadedIndex else { throw IndexFailure.changed }
            let d = try JSONEncoder().encode(next)
            try HealthDataFiles.write(d, to: fileURL)
            loadedIndex = d
            records = next
            storageError = nil
            return true
        } catch IndexFailure.changed {
            needsReload = true
            storageError = "紀錄檔已變更，未覆寫資料。請先重新讀取，再重試此操作。"
            return false
        } catch IndexFailure.invalidIDs {
            storageError = "紀錄識別碼重複或無效，未儲存修改。"
            return false
        } catch {
            storageError = "無法儲存紀錄，原有紀錄未變更。請確認裝置儲存空間後重試。"
            return false
        }
    }
}
