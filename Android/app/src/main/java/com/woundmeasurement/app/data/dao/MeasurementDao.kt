package com.woundmeasurement.app.data.dao

import androidx.room.*
import com.woundmeasurement.app.data.entity.MeasurementEntity
import kotlinx.coroutines.flow.Flow
import java.util.Date

@Dao
interface MeasurementDao {
    
    @Query("SELECT * FROM measurements ORDER BY timestamp DESC")
    fun getAllMeasurements(): Flow<List<MeasurementEntity>>
    
    @Query("SELECT * FROM measurements WHERE patientId = :patientId ORDER BY timestamp DESC")
    fun getMeasurementsByPatient(patientId: String): Flow<List<MeasurementEntity>>
    
    @Query("SELECT * FROM measurements WHERE isPatientIdentified = 0 ORDER BY timestamp DESC")
    fun getGeneralMeasurements(): Flow<List<MeasurementEntity>>
    
    @Query("SELECT * FROM measurements WHERE patientId = :patientId AND timestamp >= :startDate ORDER BY timestamp DESC")
    fun getMeasurementsByPatientAndDateRange(patientId: String, startDate: Date): Flow<List<MeasurementEntity>>
    
    @Query("SELECT * FROM measurements WHERE id = :measurementId")
    suspend fun getMeasurementById(measurementId: Long): MeasurementEntity?
    
    @Query("SELECT * FROM measurements WHERE patientId = :patientId ORDER BY timestamp DESC LIMIT 1")
    suspend fun getLatestMeasurementByPatient(patientId: String): MeasurementEntity?
    
    @Insert
    suspend fun insertMeasurement(measurement: MeasurementEntity): Long
    
    @Update
    suspend fun updateMeasurement(measurement: MeasurementEntity)
    
    @Delete
    suspend fun deleteMeasurement(measurement: MeasurementEntity)
    
    @Query("DELETE FROM measurements WHERE patientId = :patientId")
    suspend fun deleteAllMeasurementsByPatient(patientId: String)
    
    @Query("SELECT COUNT(*) FROM measurements WHERE patientId = :patientId")
    suspend fun getMeasurementCountByPatient(patientId: String): Int
    
    @Query("SELECT COUNT(*) FROM measurements WHERE isPatientIdentified = 0")
    suspend fun getGeneralMeasurementCount(): Int
    
    @Query("SELECT AVG(estimatedArea) FROM measurements WHERE patientId = :patientId AND estimatedArea IS NOT NULL")
    suspend fun getAverageAreaByPatient(patientId: String): Double?
    
    @Query("SELECT AVG(estimatedVolume) FROM measurements WHERE patientId = :patientId AND estimatedVolume IS NOT NULL")
    suspend fun getAverageVolumeByPatient(patientId: String): Double?
    
    @Query("SELECT MIN(estimatedArea) FROM measurements WHERE patientId = :patientId AND estimatedArea IS NOT NULL")
    suspend fun getMinAreaByPatient(patientId: String): Double?
    
    @Query("SELECT MAX(estimatedArea) FROM measurements WHERE patientId = :patientId AND estimatedArea IS NOT NULL")
    suspend fun getMaxAreaByPatient(patientId: String): Double?
} 