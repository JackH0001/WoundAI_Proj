package com.woundmeasurement.app.data.repository

import com.woundmeasurement.app.data.dao.PatientDao
import com.woundmeasurement.app.data.entity.PatientEntity
import kotlinx.coroutines.flow.Flow
import java.util.Date

@Deprecated(
    "繞過 PhiCrypto,會把明文姓名/病歷號寫進 DB。請改用 data.repo.CaseRepository",
    ReplaceWith("com.woundmeasurement.app.data.repo.CaseRepository"),
    level = DeprecationLevel.ERROR
)
class PatientRepository(private val patientDao: PatientDao) {
    
    fun getAllPatients(): Flow<List<PatientEntity>> = patientDao.getAllPatients()
    
    fun getRecentPatients(limit: Int = 10): Flow<List<PatientEntity>> = patientDao.getRecentPatients(limit)
    
    suspend fun getPatientById(patientId: String): PatientEntity? = patientDao.getPatientById(patientId)
    
    suspend fun getPatientByMRN(mrn: String): PatientEntity? = patientDao.getPatientByMRN(mrn)
    
    suspend fun searchPatients(searchQuery: String): List<PatientEntity> = patientDao.searchPatients(searchQuery)
    
    suspend fun insertPatient(patient: PatientEntity) = patientDao.insertPatient(patient)
    
    suspend fun updatePatient(patient: PatientEntity) = patientDao.updatePatient(patient)
    
    suspend fun deletePatient(patient: PatientEntity) = patientDao.deletePatient(patient)
    
    suspend fun updateLastVisitTime(patientId: String, visitTime: Date) = patientDao.updateLastVisitTime(patientId, visitTime)
    
    suspend fun getPatientCount(): Int = patientDao.getPatientCount()
} 