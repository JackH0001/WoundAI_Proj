package com.woundmeasurement.app.data.repo

import com.woundmeasurement.app.data.crypto.PhiCrypto
import com.woundmeasurement.app.data.dao.ConsentDao
import com.woundmeasurement.app.data.dao.MeasurementDao
import com.woundmeasurement.app.data.dao.PatientDao
import com.woundmeasurement.app.data.dao.WoundCaseDao
import com.woundmeasurement.app.data.database.WoundMeasurementDatabase
import com.woundmeasurement.app.data.entity.ConsentEntity
import com.woundmeasurement.app.data.entity.PatientEntity
import com.woundmeasurement.app.data.entity.WoundCaseEntity
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.util.Date
import java.util.UUID

/**
 * 個案化醫療紀錄的唯一入口。
 *
 * **請一律經由本類別存取病患資料，不要直接呼叫 PatientDao** —— PII 的加解密在這裡，
 * 繞過去就會把明文姓名/病歷號寫進 DB（而且不會有任何錯誤，事後很難發現）。
 *
 * 加解密刻意不用 Room TypeConverter：TypeConverter 依「型別」套用，掛在 String 上
 * 會把全庫每個字串欄位都加密（連 notes、route 都會），完全不是我們要的。
 */
class CaseRepository(
    private val patients: PatientDao,
    private val cases: WoundCaseDao,
    private val consents: ConsentDao,
    private val measurements: MeasurementDao
) {
    companion object {
        fun from(db: WoundMeasurementDatabase) = CaseRepository(
            db.patientDao(), db.woundCaseDao(), db.consentDao(), db.measurementDao()
        )
    }

    // ---------- 病患（PII 加解密邊界就在這幾個函式） ----------
    //
    // 全部包 withContext(IO)：Keystore 每次操作都是 binder 往返，清單解密會呼叫上百次，
    // 留在 Compose 的 Main dispatcher 上會直接 ANR。

    /** 建立病患：姓名/病歷號加密後入庫，另存病歷號指紋供查重。 */
    suspend fun createPatient(
        name: String, mrn: String, birthDate: String = "", gender: String = "",
        department: String = "", notes: String? = null
    ): PatientEntity = withContext(Dispatchers.IO) {
        val e = PatientEntity(
            id = UUID.randomUUID().toString(),
            name = PhiCrypto.encrypt(name) ?: "",
            birthDate = birthDate,
            gender = gender,
            medicalRecordNumber = PhiCrypto.encrypt(mrn) ?: "",
            department = department,
            registrationTime = Date(),
            notes = notes,
            mrnHash = PhiCrypto.hashMrn(mrn)
        )
        patients.insertPatient(e)
        decrypt(e)
    }

    /** 讀出時解密。解不開（App 重裝導致金鑰遺失）回 "（無法解密）" 而不是讓畫面崩潰。 */
    private fun decrypt(p: PatientEntity) = p.copy(
        name = PhiCrypto.decrypt(p.name) ?: "（無法解密）",
        medicalRecordNumber = PhiCrypto.decrypt(p.medicalRecordNumber) ?: ""
    )

    suspend fun listPatients(): List<PatientEntity> = withContext(Dispatchers.IO) {
        patients.getAllPatientsList().map { decrypt(it) }
    }

    suspend fun getPatient(id: String): PatientEntity? = withContext(Dispatchers.IO) {
        patients.getPatientById(id)?.let { decrypt(it) }
    }

    /** 用病歷號查重：比對指紋（DB 直接查），不必把全表明文帶進記憶體。 */
    suspend fun findByMrn(mrn: String): PatientEntity? = withContext(Dispatchers.IO) {
        val h = PhiCrypto.hashMrn(mrn) ?: return@withContext null
        patients.getPatientByMrnHash(h)?.let { decrypt(it) }
    }

    /** 姓名/病歷號搜尋：密文無法用 SQL LIKE，只能解密後在記憶體篩選。 */
    suspend fun searchPatients(q: String): List<PatientEntity> {
        if (q.isBlank()) return listPatients()
        return listPatients().filter {
            it.name.contains(q, true) || it.medicalRecordNumber.contains(q, true)
        }
    }

    // ---------- 傷口個案 ----------

    /** 建立傷口個案並配發**穩定**的 WD-code（碰撞時重試，由 DB UNIQUE 索引把關）。 */
    suspend fun createCase(
        patientId: String, bodySite: String, woundType: String, notes: String? = null
    ): WoundCaseEntity = withContext(Dispatchers.IO) {
        var code = WoundCaseEntity.newWdCode()
        var tries = 0
        while (cases.countByWdCode(code) > 0) {
            // 2^-32 等級的事件,但耗盡重試仍碰撞就明確報錯,不要讓它撞上 UNIQUE 約束變成崩潰
            if (tries++ >= 5) throw IllegalStateException("WD-code 配發失敗(連續碰撞),請重試")
            code = WoundCaseEntity.newWdCode()
        }
        val e = WoundCaseEntity(
            patientId = patientId, wdCode = code,
            bodySite = bodySite, woundType = woundType, notes = notes
        )
        e.copy(id = cases.insert(e))
    }

    suspend fun openCases(patientId: String) = withContext(Dispatchers.IO) { cases.getOpenCasesByPatient(patientId) }
    suspend fun getCase(id: Long) = withContext(Dispatchers.IO) { cases.getById(id) }
    suspend fun closeCase(id: Long) = withContext(Dispatchers.IO) { cases.close(id, Date()) }

    /**
     * 刪除**空**個案（建錯的那種）。有任何量測就拒絕——那是病歷，走「結案」。
     * 空個案可真刪：沒有臨床紀錄要保全；wdCode 即使已隨②同意同步上雲，
     * 留在雲端的只是沒資料掛著的代碼，無害。規則與 iOS 完全一致。
     */
    suspend fun deleteCaseIfEmpty(id: Long): Boolean = withContext(Dispatchers.IO) {
        if (measurements.getCountByCase(id) > 0) return@withContext false
        cases.deleteById(id); true
    }

    /**
     * 刪除單筆量測。**只允許沒送出過訓練標註的**——送出過的已進雲端 append-only 佇列，
     * 本機刪了兩邊對不上帳；那種用主控台「誤送排除」。連同加密影像與柵格檔一起清
     * （先刪列再刪檔，反向會留死路徑）。
     */
    suspend fun deleteMeasurementIfUnsubmitted(
        id: Long, imageStore: com.woundmeasurement.app.data.store.LocalImageStore
    ): Boolean = withContext(Dispatchers.IO) {
        val m = measurements.getById(id) ?: return@withContext false
        if (m.annotationSubmitted) return@withContext false
        measurements.deleteById(id)
        if (m.imagePath.isNotEmpty()) runCatching { imageStore.delete(m.imagePath) }
        m.rasterPath?.takeIf { it.isNotEmpty() }?.let { runCatching { imageStore.delete(it) } }
        true
    }

    // ---------- 知情同意 ----------

    /**
     * 簽署同意。**這是 `consent_train` 的唯一真值來源**——
     * 先前 BackendClient 硬編碼 true，等於每筆都謊稱已取得訓練同意。
     */
    suspend fun signConsent(
        patientId: String, care: Boolean, train: Boolean,
        signaturePng: ByteArray?, signerRole: String = "patient", witnessStaff: String? = null
    ): ConsentEntity = withContext(Dispatchers.IO) {
        val e = ConsentEntity(
            patientId = patientId, consentCare = care, consentTrain = train,
            // 手寫簽名是可識別資訊,與姓名同級 → 一併加密後入庫
            signaturePng = PhiCrypto.encryptBytes(signaturePng),
            signerRole = signerRole, witnessStaff = witnessStaff
        )
        e.copy(id = consents.insert(e))
    }

    /** 取出簽名影像（解密）。供列印/稽核檢視用；解不開回 null。 */
    fun signatureBitmapBytes(c: ConsentEntity): ByteArray? = PhiCrypto.decryptBytes(c.signaturePng)

    suspend fun activeConsent(patientId: String) = withContext(Dispatchers.IO) { consents.getActive(patientId) }

    /** 可否量測（①照護同意）。沒有就該擋住量測入口。 */
    suspend fun canMeasure(patientId: String) = withContext(Dispatchers.IO) { consents.countActiveCare(patientId) > 0 }

    /** 可否送訓練標註（②訓練同意且未撤回）。 */
    suspend fun canSubmitTraining(patientId: String) = withContext(Dispatchers.IO) {
        consents.getActive(patientId)?.trainEffective == true
    }

    /**
     * 撤回訓練同意。回傳**該病患名下所有 wdCode**，呼叫端須逐一打後端
     * `/api/v1/consent/withdraw` 才算完成——本機留痕只是一半，
     * 雲端那份不排除的話，資料照樣會進訓練集。
     */
    suspend fun withdrawTraining(patientId: String, reason: String?): List<String> = withContext(Dispatchers.IO) {
        val prev = consents.getActive(patientId)
        prev?.let { consents.withdraw(it.id, Date(), reason) }
        // ⚠ `withdraw` 是把**整列**標成撤回,而 getActive/countActiveCare 都濾 withdrawnAt IS NULL
        //   → 只撤訓練同意會連①照護同意一起失效,病患從此不能量測(流程直接鎖死)。
        //   雙層同意的語意是「②可撤回、①不受影響」,所以要補簽一筆保留照護、關閉訓練的紀錄。
        //   舊紀錄仍留著(撤回留痕),稽核看得出「曾經同意訓練、何時撤回」。
        if (prev?.consentCare == true) {
            consents.insert(
                ConsentEntity(
                    patientId = patientId,
                    consentCare = true, consentTrain = false,
                    signaturePng = prev.signaturePng,          // 沿用同一份簽名(同一次簽署的延續)
                    signerRole = prev.signerRole, witnessStaff = prev.witnessStaff,
                    templateVersion = prev.templateVersion
                )
            )
        }
        cases.getWdCodesByPatient(patientId)
    }

    // ---------- 量測 ----------

    fun measurementsOfCase(caseId: Long) = measurements.getMeasurementsByCase(caseId)
    suspend fun measurementCount(caseId: Long) = withContext(Dispatchers.IO) { measurements.getCountByCase(caseId) }

    /**
     * 個案摘要：上次面積、與**首次**相比的變化%、距上次量測天數、總筆數。
     *
     * 為什麼跟首次比而不是跟上一次比：臨床看的是「這個傷口有沒有在癒合」，
     * 那是相對於治療起點的變化；跟上一次比只反映單次波動（拍攝角度、描邊差異都會蓋過真實變化）。
     */
    data class CaseSummary(
        val count: Int,
        val lastArea: Double?,
        val firstArea: Double?,
        val daysSinceLast: Long?
    ) {
        /** 相對首次的面積變化%（負值＝縮小＝在癒合）。樣本 <2 或首次面積為 0 時回 null。 */
        val changePct: Double?
            get() {
                val f = firstArea ?: return null
                val l = lastArea ?: return null
                if (count < 2 || f <= 0.0) return null
                return (l - f) / f * 100.0
            }
    }

    suspend fun caseSummary(caseId: Long): CaseSummary = withContext(Dispatchers.IO) {
        val n = measurements.getCountByCase(caseId)
        val last = measurements.getLatestByCase(caseId)
        val first = measurements.getFirstByCase(caseId)
        // coerceAtLeast(0):裝置時鐘回撥會算出負值,顯示「-3 天前」比沒有還糟
        val days = last?.timestamp?.let {
            ((Date().time - it.time) / (1000L * 60 * 60 * 24)).coerceAtLeast(0L)
        }
        CaseSummary(n, last?.estimatedArea, first?.estimatedArea, days)
    }

    /**
     * 最近有量測活動的**開立中**個案（主畫面「最近就診」用；回診時一鍵接續）。
     * 已結案（癒合／轉出）者不列出——否則護理師會對一個已經收掉的傷口繼續量測。
     */
    suspend fun recentCases(limit: Int = 10): List<WoundCaseEntity> = withContext(Dispatchers.IO) {
        // 多取一些再過濾:結案過濾發生在 SQL LIMIT 之後,
        // 若最近 10 個剛好都已結案,清單會整頁空白而其實還有開立中的個案。
        measurements.getRecentCaseIds(limit * 3)
            .mapNotNull { cases.getById(it) }
            .filter { it.closedAt == null }
            .take(limit)
    }

    /**
     * 保存期限清理：**結案逾 [days] 天的個案，刪影像但保留量測數值與趨勢**。
     *
     * 為什麼這樣切（對應 `IRB_consent_templates.md:57`「逾期即下架/銷毀」）：
     *  - 影像是最敏感的 PHI，也最佔空間，逾期就該清；
     *  - 面積、PUSH、時間戳是**病歷**，不能因為逾期而毀掉（那是另一種違規）。
     *  - **開立中的個案一律保留**——還要跟蹤癒合。
     *
     * 回傳刪掉的影像數。呼叫端請在 App 啟動或開時間軸時跑一次。
     */
    suspend fun purgeExpiredImages(
        store: com.woundmeasurement.app.data.store.LocalImageStore,
        days: Int = 90
    ): Int = withContext(Dispatchers.IO) {
        val cutoff = Date(System.currentTimeMillis() - days.toLong() * 24 * 60 * 60 * 1000)
        var n = 0
        for (m in measurements.getExpiredWithImages(cutoff)) {
            // ⚠ 順序是「先清 DB 路徑,再刪檔」,不可反過來。
            // 反過來的話,程序在兩步之間被殺(或 Room 拋例外被外層 runCatching 吞掉)會留下
            // 「imagePath 非空但檔案不存在」的死路徑 → 時間軸每次都嘗試解碼失敗,縮圖永遠停在「…」,
            // 看起來像卡住而不是「影像已依保存期限清除」。
            // 這個順序最壞情況只是留下一個沒人引用的孤兒密文檔(下一輪 GC 可回收),
            // 而檔名是 UUID、內容是密文,留著不會多洩漏什麼。
            runCatching { measurements.clearImagePath(m.id) }
                .onSuccess { store.delete(m.imagePath); n++ }
        }
        n
    }

    /** 該個案還可補送標註的筆數（時間軸提示用）。 */
    suspend fun pendingAnnotationCount(caseId: Long): Int = withContext(Dispatchers.IO) {
        measurements.getPendingAnnotation(caseId).size
    }

    /**
     * 最近就診用的一列資料：個案 + 病患辨識線索 + 是否可量測（①照護同意）。
     *
     * 為什麼要帶病患辨識：只顯示「部位・類型 + WD-code」時，同病房兩位病患都有「薦骨・壓瘡」
     * 就分不出是誰，而 WD-code 是隨機碼、肉眼不可辨 → 量錯個案會永久寫進錯的時間軸。
     * 但也不能攤明文 PII，故只給**遮蔽後**的病歷號。
     */
    data class RecentRow(
        val case: WoundCaseEntity,
        val patientHint: String,
        val canMeasure: Boolean,
        val summary: CaseSummary
    )

    suspend fun recentRows(limit: Int = 10): List<RecentRow> = withContext(Dispatchers.IO) {
        recentCases(limit).mapNotNull { c ->
            val p = patients.getPatientById(c.patientId) ?: return@mapNotNull null
            // 病歷號解不開(App 重裝→金鑰失效)時 maskMrn 會回 "—",辨識線索就沒了;
            // 退回姓名遮蔽,再不行才用個案序號——總之不能讓兩位「薦骨・壓瘡」變得無法區分。
            val mrn = PhiCrypto.decrypt(p.medicalRecordNumber)
            val name = PhiCrypto.decrypt(p.name)
            val hint = when {
                !mrn.isNullOrBlank() -> PhiCrypto.maskMrn(mrn)
                !name.isNullOrBlank() -> name.take(1) + "○"
                else -> "個案 #${c.id}"
            }
            RecentRow(
                case = c,
                patientHint = hint,
                canMeasure = consents.countActiveCare(c.patientId) > 0,
                summary = caseSummary(c.id)
            )
        }
    }
}
