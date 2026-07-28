package com.woundmeasurement.app.data.dao

import androidx.room.*
import com.woundmeasurement.app.data.entity.PatientEntity
import kotlinx.coroutines.flow.Flow
import java.util.Date

@Dao
interface PatientDao {
    
    @Query("SELECT * FROM patients ORDER BY lastVisitTime DESC, registrationTime DESC")
    fun getAllPatients(): Flow<List<PatientEntity>>
    
    @Query("SELECT * FROM patients WHERE id = :patientId")
    suspend fun getPatientById(patientId: String): PatientEntity?
    
    /** 一次取回（非 Flow），供 Repository 解密後回傳。 */
    @Query("SELECT * FROM patients ORDER BY lastVisitTime DESC, registrationTime DESC")
    suspend fun getAllPatientsList(): List<PatientEntity>

    /**
     * 以病歷號**雜湊**查病患（Sprint N1 起的正確查法）。
     * `medicalRecordNumber` 欄位自 v2 起是密文，直接比對明文永遠查不到。
     */
    @Query("SELECT * FROM patients WHERE mrnHash = :mrnHash LIMIT 1")
    suspend fun getPatientByMrnHash(mrnHash: String): PatientEntity?

    @Deprecated(
        "v2 起 medicalRecordNumber 為密文，比對明文永遠查不到。改用 getPatientByMrnHash()",
        ReplaceWith("getPatientByMrnHash(PhiCrypto.hashMrn(mrn)!!)")
    )
    @Query("SELECT * FROM patients WHERE medicalRecordNumber = :mrn")
    suspend fun getPatientByMRN(mrn: String): PatientEntity?

    @Query("SELECT * FROM patients ORDER BY lastVisitTime DESC LIMIT :limit")
    fun getRecentPatients(limit: Int = 10): Flow<List<PatientEntity>>

    @Deprecated("v2 起 name/medicalRecordNumber 為密文，LIKE 比對無效。請在 Repository 解密後於記憶體篩選")
    @Query("SELECT * FROM patients WHERE name LIKE '%' || :searchQuery || '%' OR medicalRecordNumber LIKE '%' || :searchQuery || '%'")
    suspend fun searchPatients(searchQuery: String): List<PatientEntity>
    
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertPatient(patient: PatientEntity)
    
    @Update
    suspend fun updatePatient(patient: PatientEntity)
    
    @Delete
    suspend fun deletePatient(patient: PatientEntity)
    
    @Query("UPDATE patients SET lastVisitTime = :visitTime WHERE id = :patientId")
    suspend fun updateLastVisitTime(patientId: String, visitTime: Date)
    
    @Query("SELECT COUNT(*) FROM patients")
    suspend fun getPatientCount(): Int
} 