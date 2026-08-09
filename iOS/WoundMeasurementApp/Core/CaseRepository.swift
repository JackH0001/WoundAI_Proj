import Foundation

/**
 個案資料存取。**這是 PII 加解密的唯一邊界**（對等 Android `data/repo/CaseRepository.kt`）。

 ## 為什麼加解密不做在更底層

 直覺的做法是在 store 層對「所有字串欄位」透明加解密。那會把 `notes`、`route`、
 `bodySite` 也一起加密——不只浪費，還讓 `WHERE route = 'student'` 這種查詢永遠查不到。
 Android 端踩過同一個坑（Room TypeConverter 依型別套用，掛在 `String` 上會波及全庫）。

 所以：**明確地在這一層加解密指定欄位**，其餘照舊。代價是不可以繞過這個類別直接寫
 `patients` 表——那樣寫進去的是明文 PHI，而且不會有任何錯誤。

 ## 哪些欄位加密

 | 欄位 | 處理 |
 |---|---|
 | `patients.name` | AES-GCM |
 | `patients.medicalRecordNumber` | AES-GCM |
 | `patients.mrnHash` | HMAC（不可逆，供查重） |
 | `consents.signaturePng` | AES-GCM（BLOB） |

 其餘欄位明文。`wdCode` 明文——它本來就是設計來離開本機的去識別代碼。
 */
actor CaseRepository {

    private let store: WoundStore

    init(store: WoundStore = .shared) { self.store = store }

    // MARK: - 病患

    private func decrypt(_ p: Patient) -> Patient {
        var out = p
        // 解不開時顯示「（無法解密）」而不是崩潰——一筆壞資料不該讓整個病患清單開不起來，
        // 但也不能假裝它是空的，那會讓人以為病患資料遺失了。
        out.name = PhiCrypto.decrypt(p.name) ?? "（無法解密）"
        out.medicalRecordNumber = PhiCrypto.decrypt(p.medicalRecordNumber) ?? ""
        return out
    }

    private static let patientSQL = """
        SELECT id, name, birthDate, gender, medicalRecordNumber, department,
               registrationTime, lastVisitTime, notes, mrnHash FROM patients
        """

    private func mapPatient(_ r: WoundStore.Row) -> Patient {
        return Patient(
            id: r.string("id") ?? "",
            name: r.string("name") ?? "",
            birthDate: r.string("birthDate") ?? "",
            gender: r.string("gender") ?? "",
            medicalRecordNumber: r.string("medicalRecordNumber") ?? "",
            department: r.string("department") ?? "",
            registrationTime: r.date("registrationTime") ?? Date(),
            lastVisitTime: r.date("lastVisitTime"),
            notes: r.string("notes"),
            mrnHash: r.string("mrnHash")
        )
    }

    /// **加密邊界（寫）。** 加密失敗直接拋錯，絕不落明文。
    func createPatient(name: String, mrn: String, birthDate: String = "",
                       gender: String = "", department: String = "",
                       notes: String? = nil) throws -> Patient {
        let p = Patient(name: name, birthDate: birthDate, gender: gender,
                        medicalRecordNumber: mrn, department: department,
                        notes: notes, mrnHash: try PhiCrypto.hashMrn(mrn))
        _ = store.write("""
            INSERT INTO patients (id, name, birthDate, gender, medicalRecordNumber,
                                  department, registrationTime, lastVisitTime, notes, mrnHash)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """, [p.id, try PhiCrypto.encrypt(name), birthDate, gender,
                  try PhiCrypto.encrypt(mrn), department, p.registrationTime,
                  nil, notes, p.mrnHash])
        return p   // 回傳已是明文
    }

    func listPatients() -> [Patient] {
        return store.query(Self.patientSQL + " ORDER BY lastVisitTime DESC, registrationTime DESC",
                           [], mapPatient).map(decrypt)
    }

    func getPatient(id: String) -> Patient? {
        return store.query(Self.patientSQL + " WHERE id = ? LIMIT 1", [id], mapPatient)
            .first.map(decrypt)
    }

    /// 以 HMAC 指紋查重。**不需要把全表帶進記憶體解密。**
    func findByMrn(_ mrn: String) throws -> Patient? {
        guard let h = try PhiCrypto.hashMrn(mrn) else { return nil }
        return store.query(Self.patientSQL + " WHERE mrnHash = ? LIMIT 1", [h], mapPatient)
            .first.map(decrypt)
    }

    /**
     搜尋病患。

     ⚠ 密文無法用 SQL `LIKE`，所以這裡是**在記憶體裡篩**。病患數上千之後要改成
     前綴索引或加密搜尋，現階段（單機構、試點規模）可接受。
     */
    func searchPatients(_ q: String) -> [Patient] {
        let t = q.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty else { return listPatients() }
        return listPatients().filter {
            $0.name.contains(t) || $0.medicalRecordNumber.contains(t)
        }
    }

    func touchLastVisit(patientId: String, at: Date = Date()) {
        _ = store.write("UPDATE patients SET lastVisitTime = ? WHERE id = ?", [at, patientId])
    }

    // MARK: - 傷口個案

    private func mapCase(_ r: WoundStore.Row) -> WoundCase {
        return WoundCase(
            id: r.int("id") ?? 0,
            patientId: r.string("patientId") ?? "",
            wdCode: r.string("wdCode") ?? "",
            bodySite: r.string("bodySite") ?? "",
            woundType: r.string("woundType") ?? "",
            onsetDate: r.date("onsetDate"),
            createdAt: r.date("createdAt") ?? Date(),
            closedAt: r.date("closedAt"),
            notes: r.string("notes")
        )
    }

    enum RepoError: Error, LocalizedError {
        case wdCodeCollision
        var errorDescription: String? {
            return "WD-code 配發失敗（連續碰撞）。請重試；若持續失敗請聯絡管理者。"
        }
    }

    /// 建立傷口個案並配發穩定的 WD 代碼。碰撞重試 5 次。
    func createCase(patientId: String, bodySite: String, woundType: String,
                    notes: String? = nil) throws -> WoundCase {
        for _ in 0..<5 {
            let code = WoundCase.newWdCode()
            let dup = store.query("SELECT COUNT(*) AS n FROM wound_cases WHERE wdCode = ?",
                                  [code]) { $0.int("n") ?? 0 }.first ?? 0
            if dup > 0 { continue }
            var c = WoundCase(patientId: patientId, wdCode: code,
                              bodySite: bodySite, woundType: woundType, notes: notes)
            guard let id = store.write("""
                INSERT INTO wound_cases (patientId, wdCode, bodySite, woundType,
                                         onsetDate, createdAt, closedAt, notes)
                VALUES (?,?,?,?,?,?,?,?)
                """, [patientId, code, bodySite, woundType, nil, c.createdAt, nil, notes])
            else { continue }
            c.id = id
            return c
        }
        throw RepoError.wdCodeCollision
    }

    func openCases(patientId: String) -> [WoundCase] {
        return store.query("""
            SELECT * FROM wound_cases WHERE patientId = ? AND closedAt IS NULL
            ORDER BY createdAt DESC
            """, [patientId], mapCase)
    }

    func allCases(patientId: String) -> [WoundCase] {
        return store.query("SELECT * FROM wound_cases WHERE patientId = ? ORDER BY createdAt DESC",
                           [patientId], mapCase)
    }

    func getCase(id: Int64) -> WoundCase? {
        return store.query("SELECT * FROM wound_cases WHERE id = ? LIMIT 1", [id], mapCase).first
    }

    func wdCodes(patientId: String) -> [String] {
        return store.query("SELECT wdCode FROM wound_cases WHERE patientId = ?",
                           [patientId]) { $0.string("wdCode") ?? "" }.filter { !$0.isEmpty }
    }

    func closeCase(id: Int64, at: Date = Date()) {
        _ = store.write("UPDATE wound_cases SET closedAt = ? WHERE id = ?", [at, id])
    }

    // MARK: - 同意書

    private func mapConsent(_ r: WoundStore.Row) -> Consent {
        return Consent(
            id: r.int("id") ?? 0,
            patientId: r.string("patientId") ?? "",
            consentCare: r.bool("consentCare"),
            consentTrain: r.bool("consentTrain"),
            signaturePng: r.data("signaturePng"),      // 仍為密文，見 signatureBytes()
            signedAt: r.date("signedAt") ?? Date(),
            signerRole: r.string("signerRole") ?? "patient",
            witnessStaff: r.string("witnessStaff"),
            templateVersion: r.string("templateVersion") ?? "IRB_consent_v1",
            withdrawnAt: r.date("withdrawnAt"),
            withdrawReason: r.string("withdrawReason")
        )
    }

    /// **加密邊界（寫）。**
    @discardableResult
    func signConsent(patientId: String, care: Bool, train: Bool, signaturePng: Data?,
                     signerRole: String = "patient", witnessStaff: String? = nil) throws -> Int64? {
        return store.write("""
            INSERT INTO consents (patientId, consentCare, consentTrain, signaturePng, signedAt,
                                  signerRole, witnessStaff, templateVersion, withdrawnAt, withdrawReason)
            VALUES (?,?,?,?,?,?,?,?,NULL,NULL)
            """, [patientId, care, train, try PhiCrypto.encryptBytes(signaturePng), Date(),
                  signerRole, witnessStaff, "IRB_consent_v1"])
    }

    /// **解密邊界。**
    func signatureBytes(_ c: Consent) -> Data? {
        return PhiCrypto.decryptBytes(c.signaturePng)
    }

    func activeConsent(patientId: String) -> Consent? {
        return store.query("""
            SELECT * FROM consents WHERE patientId = ? AND withdrawnAt IS NULL
            ORDER BY signedAt DESC LIMIT 1
            """, [patientId], mapConsent).first
    }

    /// 含已撤回，稽核用。**不刪任何一列。**
    func consentHistory(patientId: String) -> [Consent] {
        return store.query("SELECT * FROM consents WHERE patientId = ? ORDER BY signedAt DESC",
                           [patientId], mapConsent)
    }

    func canMeasure(patientId: String) -> Bool {
        return activeConsent(patientId: patientId)?.consentCare ?? false
    }

    func canSubmitTraining(patientId: String) -> Bool {
        return activeConsent(patientId: patientId)?.trainEffective ?? false
    }

    /**
     撤回訓練同意。回傳需要對後端撤回的 WD 代碼清單。

     ## 關鍵補償邏輯（否則會鎖死整條流程）

     撤回是把**整列**標成已撤回——包含 ①照護同意。若就此停手，病患等於連照護同意
     都沒有了，接下來連拍照都按不下去，而他明明只是不想讓資料拿去訓練。

     所以：撤回舊列之後，若舊列有 ①，**補簽一筆新的**（保留 ①、關閉 ②），沿用原簽名與
     範本版本。舊列留痕不刪，稽核軌跡完整。
     */
    func withdrawTraining(patientId: String, reason: String?) -> [String] {
        guard let prev = activeConsent(patientId: patientId) else { return [] }
        _ = store.write("UPDATE consents SET withdrawnAt = ?, withdrawReason = ? WHERE id = ?",
                        [Date(), reason, prev.id])
        if prev.consentCare {
            _ = store.write("""
                INSERT INTO consents (patientId, consentCare, consentTrain, signaturePng, signedAt,
                                      signerRole, witnessStaff, templateVersion, withdrawnAt, withdrawReason)
                VALUES (?,1,0,?,?,?,?,?,NULL,NULL)
                """, [patientId, prev.signaturePng, Date(), prev.signerRole,
                      prev.witnessStaff, prev.templateVersion])
        }
        return wdCodes(patientId: patientId)
    }

    // MARK: - 量測

    private func mapMeasurement(_ r: WoundStore.Row) -> Measurement {
        return Measurement(
            id: r.int("id") ?? 0,
            patientId: r.string("patientId"),
            timestamp: r.date("timestamp") ?? Date(),
            hasWound: r.bool("hasWound"),
            confidence: r.double("confidence") ?? 0,
            estimatedArea: r.double("estimatedArea"),
            woundTypeLabel: r.string("woundTypeLabel"),
            quality: r.string("quality") ?? "backend",
            imagePath: r.string("imagePath") ?? "",
            notes: r.string("notes"),
            isPatientIdentified: r.bool("isPatientIdentified"),
            caseId: r.int("caseId"),
            wdCode: r.string("wdCode"),
            imageId: r.string("imageId"),
            mmPerPx: r.double("mmPerPx"),
            route: r.string("route"),
            source: r.string("source"),
            gtPolygon: r.string("gtPolygon"),
            imageW: r.int("imageW").map(Int.init),
            imageH: r.int("imageH").map(Int.init),
            exudate: r.int("exudate").map(Int.init),
            correctionIou: r.double("correctionIou"),
            annotationSubmitted: r.bool("annotationSubmitted"),
            doctorVerified: r.bool("doctorVerified"),
            tissueGranulation: r.double("tissueGranulation"),
            tissueSlough: r.double("tissueSlough"),
            tissueNecrosis: r.double("tissueNecrosis"),
            tissueEpithelial: r.double("tissueEpithelial"),
            tissueOther: r.double("tissueOther"),
            rasterPath: r.string("rasterPath"),
            rasterMeta: r.string("rasterMeta")
        )
    }

    /**
     刪除**空**個案（建錯的「未指定」這類）。有任何量測就拒絕——那是病歷，走「結案」。

     為什麼空個案可以真刪：它沒有任何臨床紀錄要保全；wdCode 即使已隨②同意同步到雲端，
     留在雲端的只是一個沒有資料掛著的代碼，無害。有量測的個案永遠不硬刪（病歷留痕），
     結案後 90 天影像自動清除、數字與趨勢保留——與 Android 同一套規則。
     */
    func deleteCaseIfEmpty(id: Int64) -> Bool {
        let n = store.query("SELECT COUNT(*) AS n FROM measurements WHERE caseId = ?", [id]) {
            $0.int("n") ?? 0
        }.first ?? 0
        guard n == 0 else { return false }
        _ = store.write("DELETE FROM wound_cases WHERE id = ?", [id])
        return true
    }

    /**
     刪除單筆量測。**只允許沒送出過訓練標註的**——送出過的資料已進雲端 append-only 佇列，
     本機刪了雲端還在，兩邊就對不上帳；那種要用主控台的「誤送排除」（tombstone），不是刪除。
     連同加密影像與柵格檔一起清（先刪列再刪檔，反向會留死路徑）。
     */
    func deleteMeasurementIfUnsubmitted(id: Int64, imageStore: LocalImageStore) -> Bool {
        guard let m = measurement(id: id), !m.annotationSubmitted else { return false }
        _ = store.write("DELETE FROM measurements WHERE id = ?", [id])
        if !m.imagePath.isEmpty {
            imageStore.delete(m.imagePath)
            DepthStore.purge(imagePath: m.imagePath, store: imageStore)
        }
        if let rp = m.rasterPath { imageStore.delete(rp) }
        return true
    }

    @discardableResult
    func insertMeasurement(_ m: Measurement) -> Int64? {
        return store.write("""
            INSERT INTO measurements (patientId, timestamp, hasWound, confidence, estimatedArea,
              woundTypeLabel, quality, imagePath, notes, isPatientIdentified, caseId, wdCode,
              imageId, mmPerPx, route, source, gtPolygon, imageW, imageH, exudate, correctionIou,
              annotationSubmitted, doctorVerified, tissueGranulation, tissueSlough, tissueNecrosis,
              tissueEpithelial, tissueOther, rasterPath, rasterMeta)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, [m.patientId, m.timestamp, m.hasWound, m.confidence, m.estimatedArea,
                  m.woundTypeLabel, m.quality, m.imagePath, m.notes, m.isPatientIdentified,
                  m.caseId, m.wdCode, m.imageId, m.mmPerPx, m.route, m.source, m.gtPolygon,
                  m.imageW, m.imageH, m.exudate, m.correctionIou, m.annotationSubmitted,
                  m.doctorVerified, m.tissueGranulation, m.tissueSlough, m.tissueNecrosis,
                  m.tissueEpithelial, m.tissueOther, m.rasterPath, m.rasterMeta])
    }

    /**
     覆寫既有量測列（同一輪量測重複存檔時用）。

     ## 為什麼需要這一支，而不是每次都 INSERT

     醫師常常會存兩次：先存一次，再回頭補滲液、或修完邊再存。每一次都 INSERT 的話，
     時間軸上會多出一個**面積相同、時間戳不同**的點——而癒合曲線上那看起來像
     「這段期間完全沒有進展」。它不是錯誤資料，它是**看起來很合理的錯誤資料**。

     ## 不在這裡更新的欄位

     `timestamp` 與 `annotationSubmitted` 由呼叫端決定要不要沿用舊值（見
     `MeasureViewModel.saveToTimeline`）——這一支只負責照 `m` 的內容整列寫入。

     ⚠ **組織比例五欄每次都要寫。** Android 的 update 分支曾經漏掉它們，症狀是
     第一次存的比例正確、補完滲液再存一次就悄悄沿用舊值。影像 90 天後會被清除，
     組織比例是那之後唯一還原得回來的東西，寫錯了就沒有第二次機會。
     */
    func updateMeasurement(_ m: Measurement) {
        guard m.id > 0 else { return }
        _ = store.write("""
            UPDATE measurements SET
              patientId = ?, timestamp = ?, hasWound = ?, confidence = ?, estimatedArea = ?,
              woundTypeLabel = ?, quality = ?, imagePath = ?, notes = ?, isPatientIdentified = ?,
              caseId = ?, wdCode = ?, imageId = ?, mmPerPx = ?, route = ?, source = ?,
              gtPolygon = ?, imageW = ?, imageH = ?, exudate = ?, correctionIou = ?,
              annotationSubmitted = ?, doctorVerified = ?, tissueGranulation = ?, tissueSlough = ?,
              tissueNecrosis = ?, tissueEpithelial = ?, tissueOther = ?, rasterPath = ?, rasterMeta = ?
            WHERE id = ?
            """, [m.patientId, m.timestamp, m.hasWound, m.confidence, m.estimatedArea,
                  m.woundTypeLabel, m.quality, m.imagePath, m.notes, m.isPatientIdentified,
                  m.caseId, m.wdCode, m.imageId, m.mmPerPx, m.route, m.source, m.gtPolygon,
                  m.imageW, m.imageH, m.exudate, m.correctionIou, m.annotationSubmitted,
                  m.doctorVerified, m.tissueGranulation, m.tissueSlough, m.tissueNecrosis,
                  m.tissueEpithelial, m.tissueOther, m.rasterPath, m.rasterMeta, m.id])
    }

    /// 時間軸正解：**依個案分組**，時間由舊到新。
    func measurements(caseId: Int64) -> [Measurement] {
        return store.query("SELECT * FROM measurements WHERE caseId = ? ORDER BY timestamp ASC",
                           [caseId], mapMeasurement)
    }

    /// 快速量測（未綁個案）的紀錄。
    func unassignedMeasurements() -> [Measurement] {
        return store.query("SELECT * FROM measurements WHERE caseId IS NULL ORDER BY timestamp DESC",
                           [], mapMeasurement)
    }

    func measurement(id: Int64) -> Measurement? {
        return store.query("SELECT * FROM measurements WHERE id = ? LIMIT 1", [id], mapMeasurement).first
    }

    func markAnnotationSubmitted(id: Int64) {
        _ = store.write("UPDATE measurements SET annotationSubmitted = 1 WHERE id = ?", [id])
    }

    func pendingAnnotationCount(caseId: Int64) -> Int {
        return Int(store.query("""
            SELECT COUNT(*) AS n FROM measurements
            WHERE caseId = ? AND annotationSubmitted = 0
              AND gtPolygon IS NOT NULL AND imageId IS NOT NULL
            """, [caseId]) { $0.int("n") ?? 0 }.first ?? 0)
    }

    func summary(caseId: Int64) -> CaseSummary {
        let ms = measurements(caseId: caseId)
        let areas = ms.compactMap { $0.estimatedArea }
        let days: Int? = ms.last.map {
            max(0, Int(Date().timeIntervalSince($0.timestamp) / 86400))
        }
        return CaseSummary(count: ms.count, lastArea: areas.last,
                           firstArea: areas.first, daysSinceLast: days)
    }

    /**
     清除逾期個案的影像。

     **順序不可顛倒：先清 `imagePath` 再刪檔。** 反過來的話，刪檔成功而清欄位失敗時，
     資料庫裡會留下指向不存在檔案的死路徑，而畫面只會顯示「載入失敗」。
     */
    @discardableResult
    func purgeExpiredImages(imageStore: LocalImageStore, days: Int = 90) -> Int {
        let cutoff = Date().addingTimeInterval(-Double(days) * 86400)
        let rows = store.query("""
            SELECT m.id AS id, m.imagePath AS imagePath FROM measurements m
            JOIN wound_cases c ON m.caseId = c.id
            WHERE c.closedAt IS NOT NULL AND c.closedAt < ? AND m.imagePath != ''
            """, [cutoff]) { ($0.int("id") ?? 0, $0.string("imagePath") ?? "") }
        var n = 0
        for (id, path) in rows {
            _ = store.write("UPDATE measurements SET imagePath = '' WHERE id = ?", [id])
            imageStore.delete(path)
            // WoundAI3D 深度 sidecar 與影像同保存政策，一併清除。
            DepthStore.purge(imagePath: path, store: imageStore)
            n += 1
        }
        return n
    }
}
