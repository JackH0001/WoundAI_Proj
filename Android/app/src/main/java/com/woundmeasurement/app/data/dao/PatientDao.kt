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
    
    @Query("SELECT * FROM patients WHERE medicalRecordNumber = :mrn")
    suspend fun getPatientByMRN(mrn: String): PatientEntity?
    
    @Query("SELECT * FROM patients ORDER BY lastVisitTime DESC LIMIT :limit")
    fun getRecentPatients(limit: Int = 10): Flow<List<PatientEntity>>
    
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