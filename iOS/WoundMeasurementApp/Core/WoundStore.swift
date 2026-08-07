import Foundation
import SQLite3

/// `sqlite3_bind_text` 需要告訴 SQLite 這塊記憶體會不會被回收。`SQLITE_TRANSIENT` 讓
/// SQLite 自行複製一份——不加的話，Swift 的暫時字串在 step 之前就可能被釋放。
private let SQLITE_TRANSIENT = unsafeBitCast(-1, to: sqlite3_destructor_type.self)

/**
 本機病歷儲存（SQLite）。schema 與 Android Room **v6 逐欄位對齊**。

 ## 為什麼是 SQLite 而不是 CoreData

 這個 schema 的真值定義在 Android 端，兩邊必須逐欄位一致才能拿同一份匯出腳本、
 同一份 migration 檢核表去驗。CoreData 的模型檔是 XML、欄位型別對應不透明，
 比對「iOS 的 `doctorVerified` 有沒有 NOT NULL DEFAULT 0」得去讀 xcdatamodeld 的內部結構。
 直接寫 SQL 讓兩邊的 DDL 可以並排 diff。

 舊的 `WoundMeasurementModel.xcdatamodeld`（單一 `WoundRecord` 實體、無病患關聯）
 是上一代架構的產物，與本 store 無關，也沒有資料需要遷移——iOS 從未發行過 release 版。

 ## ⚠ 絕不可加上「schema 不符就重建」的退路

 Android 端的對應禁令是 `fallbackToDestructiveMigration()`。這是**病歷庫**：
 schema 一改就清空全部病歷，而且沒有任何錯誤訊息。所有 migration 一律只加不刪。
 */
final class WoundStore {

    /// 與 Android Room 的 `version = 6` 對齊。
    static let schemaVersion: Int32 = 6

    private var db: OpaquePointer?
    private let queue = DispatchQueue(label: "com.woundmeasurement.app.store")

    static let shared: WoundStore = {
        let dir = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return WoundStore(path: dir.appendingPathComponent("wound_measurement_database.sqlite").path)
    }()

    init(path: String) {
        // FULLMUTEX：多個背景佇列會同時解密／查詢，序列化交給 SQLite 自己做。
        let flags = SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX
        if sqlite3_open_v2(path, &db, flags, nil) != SQLITE_OK {
            assertionFailure("無法開啟病歷資料庫：\(String(cString: sqlite3_errmsg(db)))")
        }
        exec("PRAGMA journal_mode=WAL;")
        exec("PRAGMA foreign_keys=ON;")
        // 檔案保護：裝置鎖定後仍可寫（背景上傳需要），但重開機後首次解鎖前不可讀。
        try? FileManager.default.setAttributes(
            [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication],
            ofItemAtPath: path)
        migrate()
    }

    deinit { if db != nil { sqlite3_close(db) } }

    // MARK: - 低階

    @discardableResult
    private func exec(_ sql: String) -> Bool {
        return sqlite3_exec(db, sql, nil, nil, nil) == SQLITE_OK
    }

    private var userVersion: Int32 {
        get {
            var st: OpaquePointer?
            guard sqlite3_prepare_v2(db, "PRAGMA user_version;", -1, &st, nil) == SQLITE_OK else { return 0 }
            defer { sqlite3_finalize(st) }
            return sqlite3_step(st) == SQLITE_ROW ? sqlite3_column_int(st, 0) : 0
        }
        set { exec("PRAGMA user_version=\(newValue);") }
    }

    // MARK: - Schema

    private func migrate() {
        queue.sync {
            let from = userVersion
            if from >= WoundStore.schemaVersion { return }
            exec("BEGIN;")
            if from == 0 { createV6() }          // 全新安裝直接建到最新版
            // 未來的 migration 依序接在這裡，**一律只加不刪**：
            //   if from <= 6 { exec("ALTER TABLE measurements ADD COLUMN …;") }
            userVersion = WoundStore.schemaVersion
            exec("COMMIT;")
        }
    }

    private func createV6() {
        exec("""
        CREATE TABLE IF NOT EXISTS patients (
          id TEXT PRIMARY KEY NOT NULL,
          name TEXT NOT NULL,
          birthDate TEXT NOT NULL DEFAULT '',
          gender TEXT NOT NULL DEFAULT '',
          medicalRecordNumber TEXT NOT NULL,
          department TEXT NOT NULL DEFAULT '',
          registrationTime INTEGER NOT NULL,
          lastVisitTime INTEGER,
          notes TEXT,
          mrnHash TEXT
        );
        """)
        exec("CREATE INDEX IF NOT EXISTS index_patients_mrnHash ON patients(mrnHash);")

        exec("""
        CREATE TABLE IF NOT EXISTS wound_cases (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          patientId TEXT NOT NULL,
          wdCode TEXT NOT NULL,
          bodySite TEXT NOT NULL,
          woundType TEXT NOT NULL,
          onsetDate INTEGER,
          createdAt INTEGER NOT NULL,
          closedAt INTEGER,
          notes TEXT,
          FOREIGN KEY(patientId) REFERENCES patients(id) ON DELETE CASCADE
        );
        """)
        exec("CREATE UNIQUE INDEX IF NOT EXISTS index_wound_cases_wdCode ON wound_cases(wdCode);")
        exec("CREATE INDEX IF NOT EXISTS index_wound_cases_patientId ON wound_cases(patientId);")

        exec("""
        CREATE TABLE IF NOT EXISTS consents (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          patientId TEXT NOT NULL,
          consentCare INTEGER NOT NULL,
          consentTrain INTEGER NOT NULL,
          signaturePng BLOB,
          signedAt INTEGER NOT NULL,
          signerRole TEXT NOT NULL DEFAULT 'patient',
          witnessStaff TEXT,
          templateVersion TEXT NOT NULL DEFAULT 'IRB_consent_v1',
          withdrawnAt INTEGER,
          withdrawReason TEXT,
          FOREIGN KEY(patientId) REFERENCES patients(id) ON DELETE CASCADE
        );
        """)
        exec("CREATE INDEX IF NOT EXISTS index_consents_patientId ON consents(patientId);")

        exec("""
        CREATE TABLE IF NOT EXISTS measurements (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          patientId TEXT,
          timestamp INTEGER NOT NULL,
          hasWound INTEGER NOT NULL DEFAULT 1,
          confidence REAL NOT NULL DEFAULT 0,
          estimatedArea REAL,
          woundTypeLabel TEXT,
          quality TEXT NOT NULL DEFAULT 'backend',
          imagePath TEXT NOT NULL DEFAULT '',
          notes TEXT,
          isPatientIdentified INTEGER NOT NULL DEFAULT 0,
          caseId INTEGER,
          wdCode TEXT,
          imageId TEXT,
          mmPerPx REAL,
          route TEXT,
          source TEXT,
          gtPolygon TEXT,
          imageW INTEGER,
          imageH INTEGER,
          exudate INTEGER,
          correctionIou REAL,
          annotationSubmitted INTEGER NOT NULL DEFAULT 0,
          doctorVerified INTEGER NOT NULL DEFAULT 0,
          tissueGranulation REAL,
          tissueSlough REAL,
          tissueNecrosis REAL,
          tissueEpithelial REAL,
          tissueOther REAL,
          rasterPath TEXT,
          rasterMeta TEXT,
          FOREIGN KEY(patientId) REFERENCES patients(id) ON DELETE CASCADE
        );
        """)
        exec("CREATE INDEX IF NOT EXISTS index_measurements_patientId ON measurements(patientId);")
        exec("CREATE INDEX IF NOT EXISTS index_measurements_caseId ON measurements(caseId);")
    }

    // MARK: - 查詢輔助

    private func prepare(_ sql: String, _ binds: [Any?]) -> OpaquePointer? {
        var st: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &st, nil) == SQLITE_OK else { return nil }
        for (i, v) in binds.enumerated() {
            let idx = Int32(i + 1)
            switch v {
            case nil:                   sqlite3_bind_null(st, idx)
            case let s as String:       sqlite3_bind_text(st, idx, s, -1, SQLITE_TRANSIENT)
            case let n as Int:          sqlite3_bind_int64(st, idx, Int64(n))
            case let n as Int32:        sqlite3_bind_int64(st, idx, Int64(n))
            case let n as Int64:        sqlite3_bind_int64(st, idx, n)
            case let b as Bool:         sqlite3_bind_int(st, idx, b ? 1 : 0)
            case let d as Double:       sqlite3_bind_double(st, idx, d)
            case let d as Date:         sqlite3_bind_int64(st, idx, Int64(d.timeIntervalSince1970 * 1000))
            case let d as Data:
                _ = d.withUnsafeBytes { sqlite3_bind_blob(st, idx, $0.baseAddress, Int32(d.count), SQLITE_TRANSIENT) }
            default:                    sqlite3_bind_null(st, idx)
            }
        }
        return st
    }

    func query<T>(_ sql: String, _ binds: [Any?] = [], _ map: (Row) -> T) -> [T] {
        return queue.sync {
            guard let st = prepare(sql, binds) else { return [] }
            defer { sqlite3_finalize(st) }
            var out: [T] = []
            while sqlite3_step(st) == SQLITE_ROW { out.append(map(Row(st))) }
            return out
        }
    }

    /// - Returns: 新列的 rowid；非 INSERT 或失敗回 nil。
    @discardableResult
    func write(_ sql: String, _ binds: [Any?] = []) -> Int64? {
        return queue.sync {
            guard let st = prepare(sql, binds) else { return nil }
            defer { sqlite3_finalize(st) }
            guard sqlite3_step(st) == SQLITE_DONE else { return nil }
            let id = sqlite3_last_insert_rowid(db)
            return id > 0 ? id : nil
        }
    }

    /// 讀取一列。欄位以名稱取用，避免 SELECT 欄位順序一改就整批錯位。
    struct Row {
        private let st: OpaquePointer
        private let index: [String: Int32]

        init(_ st: OpaquePointer) {
            self.st = st
            var m: [String: Int32] = [:]
            for i in 0..<sqlite3_column_count(st) {
                if let c = sqlite3_column_name(st, i) { m[String(cString: c)] = i }
            }
            self.index = m
        }

        private func col(_ n: String) -> Int32? {
            guard let i = index[n], sqlite3_column_type(st, i) != SQLITE_NULL else { return nil }
            return i
        }

        func string(_ n: String) -> String? {
            guard let i = col(n), let p = sqlite3_column_text(st, i) else { return nil }
            return String(cString: p)
        }
        func int(_ n: String) -> Int64?      { guard let i = col(n) else { return nil }; return sqlite3_column_int64(st, i) }
        func double(_ n: String) -> Double?  { guard let i = col(n) else { return nil }; return sqlite3_column_double(st, i) }
        func bool(_ n: String) -> Bool       { return (int(n) ?? 0) != 0 }
        func date(_ n: String) -> Date?      { guard let v = int(n) else { return nil }; return Date(timeIntervalSince1970: Double(v) / 1000) }
        func data(_ n: String) -> Data? {
            guard let i = col(n), let p = sqlite3_column_blob(st, i) else { return nil }
            return Data(bytes: p, count: Int(sqlite3_column_bytes(st, i)))
        }
    }
}
