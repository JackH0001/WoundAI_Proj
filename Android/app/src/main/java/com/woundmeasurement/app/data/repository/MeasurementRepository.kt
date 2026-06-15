package com.woundmeasurement.app.data.repository

import com.woundmeasurement.app.data.dao.MeasurementDao
import com.woundmeasurement.app.data.entity.MeasurementEntity
import kotlinx.coroutines.flow.Flow
import java.util.Date

class MeasurementRepository(private val measurementDao: MeasurementDao) {
    
    fun getAllMeasurements(): Flow<List<MeasurementEntity>> = measurementDao.getAllMeasurements()
    
    fun getMeasurementsByPatient(patientId: String): Flow<List<MeasurementEntity>> = measurementDao.getMeasurementsByPatient(patientId)
    
    fun getGeneralMeasurements(): Flow<List<MeasurementEntity>> = measurementDao.getGeneralMeasurements()
    
    fun getMeasurementsByPatientAndDateRange(patientId: String, startDate: Date): Flow<List<MeasurementEntity>> = 
        measurementDao.getMeasurementsByPatientAndDateRange(patientId, startDate)
    
    suspend fun getMeasurementById(measurementId: Long): MeasurementEntity? = measurementDao.getMeasurementById(measurementId)
    
    suspend fun getLatestMeasurementByPatient(patientId: String): MeasurementEntity? = measurementDao.getLatestMeasurementByPatient(patientId)
    
    suspend fun insertMeasurement(measurement: MeasurementEntity): Long = measurementDao.insertMeasurement(measurement)
    
    suspend fun updateMeasurement(measurement: MeasurementEntity) = measurementDao.updateMeasurement(measurement)
    
    suspend fun deleteMeasurement(measurement: MeasurementEntity) = measurementDao.deleteMeasurement(measurement)
    
    suspend fun deleteAllMeasurementsByPatient(patientId: String) = measurementDao.deleteAllMeasurementsByPatient(patientId)
    
    suspend fun getMeasurementCountByPatient(patientId: String): Int = measurementDao.getMeasurementCountByPatient(patientId)
    
    suspend fun getGeneralMeasurementCount(): Int = measurementDao.getGeneralMeasurementCount()
    
    suspend fun getAverageAreaByPatient(patientId: String): Double? = measurementDao.getAverageAreaByPatient(patientId)
    
    suspend fun getAverageVolumeByPatient(patientId: String): Double? = measurementDao.getAverageVolumeByPatient(patientId)
    
    suspend fun getMinAreaByPatient(patientId: String): Double? = measurementDao.getMinAreaByPatient(patientId)
    
    suspend fun getMaxAreaByPatient(patientId: String): Double? = measurementDao.getMaxAreaByPatient(patientId)
} 