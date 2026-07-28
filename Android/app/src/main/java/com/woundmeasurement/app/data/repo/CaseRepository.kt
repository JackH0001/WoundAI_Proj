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
        consents.getActive(patientId)?.let { consents.withdraw(it.id, Date(), reason) }
        cases.getWdCodesByPatient(patientId)
    }

    // ---------- 量測 ----------

    fun measurementsOfCase(caseId: Long) = measurements.getMeasurementsByCase(caseId)
    suspend fun measurementCount(caseId: Long) = withContext(Dispatchers.IO) { measurements.getCountByCase(caseId) }
}
