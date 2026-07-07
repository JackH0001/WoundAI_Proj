package com.woundmeasurement.app.processing

import android.content.Context
import com.woundmeasurement.app.data.database.WoundMeasurementDatabase
import com.woundmeasurement.app.data.repository.PatientRepository
import com.woundmeasurement.app.data.repository.MeasurementRepository
import com.woundmeasurement.app.data.entity.PatientEntity
import com.woundmeasurement.app.data.entity.MeasurementEntity
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import java.util.Date

class DatabaseIntegrationModule(
    private val context: Context,
    private val coroutineScope: CoroutineScope
) {
    private val database = WoundMeasurementDatabase.getDatabase(context)
    private val patientRepository = PatientRepository(database.patientDao())
    private val measurementRepository = MeasurementRepository(database.measurementDao())
    
    // 狀態流
    private val _currentPatient = MutableStateFlow<PatientEntity?>(null)
    val currentPatient: StateFlow<PatientEntity?> = _currentPatient
    
    private val _recentPatients = MutableStateFlow<List<PatientEntity>>(emptyList())
    val recentPatients: StateFlow<List<PatientEntity>> = _recentPatients
    
    private val _measurements = MutableStateFlow<List<MeasurementEntity>>(emptyList())
    val measurements: StateFlow<List<MeasurementEntity>> = _measurements
    
    private val _isLoading = MutableStateFlow(false)
    val isLoading: StateFlow<Boolean> = _isLoading
    
    private val _error = MutableStateFlow<String?>(null)
    val error: StateFlow<String?> = _error
    
    init {
        loadRecentPatients()
    }
    
    // 病患管理
    suspend fun savePatient(patientInfo: PatientIdentificationModule.PatientInfo): Result<PatientEntity> {
        return try {
            _isLoading.value = true
            _error.value = null
            
            val patientEntity = PatientEntity(
                id = patientInfo.id,
                name = patientInfo.name,
                birthDate = patientInfo.birthDate,
                gender = patientInfo.gender,
                medicalRecordNumber = patientInfo.medicalRecordNumber,
                department = patientInfo.department,
                registrationTime = Date(),
                lastVisitTime = Date()
            )
            
            patientRepository.insertPatient(patientEntity)
            _currentPatient.value = patientEntity
            loadRecentPatients()
            
            Result.success(patientEntity)
        } catch (e: Exception) {
            _error.value = "保存病患失敗: ${e.message}"
            Result.failure(e)
        } finally {
            _isLoading.value = false
        }
    }
    
    suspend fun getPatientById(patientId: String): PatientEntity? {
        return try {
            patientRepository.getPatientById(patientId)
        } catch (e: Exception) {
            _error.value = "查詢病患失敗: ${e.message}"
            null
        }
    }
    
    suspend fun searchPatients(query: String): List<PatientEntity> {
        return try {
            patientRepository.searchPatients(query)
        } catch (e: Exception) {
            _error.value = "搜尋病患失敗: ${e.message}"
            emptyList()
        }
    }
    
    private fun loadRecentPatients() {
        coroutineScope.launch(Dispatchers.IO) {
            try {
                patientRepository.getRecentPatients(10).collect { patients ->
                    _recentPatients.value = patients
                }
            } catch (e: Exception) {
                _error.value = "載入最近病患失敗: ${e.message}"
            }
        }
    }
    
    // 測量記錄管理
    suspend fun saveMeasurement(
        patientId: String?,
        hasWound: Boolean,
        confidence: Double,
        estimatedArea: Double?,
        estimatedVolume: Double?,
        woundType: String?,
        quality: String,
        processingTime: Long,
        imagePath: String,
        dataPath: String,
        notes: String? = null
    ): Result<Long> {
        return try {
            _isLoading.value = true
            _error.value = null
            
            val measurementEntity = MeasurementEntity(
                patientId = patientId,
                timestamp = Date(),
                hasWound = hasWound,
                confidence = confidence,
                estimatedArea = estimatedArea,
                estimatedVolume = estimatedVolume,
                woundType = woundType,
                quality = quality,
                processingTime = processingTime,
                imagePath = imagePath,
                dataPath = dataPath,
                notes = notes,
                isPatientIdentified = patientId != null
            )
            
            val measurementId = measurementRepository.insertMeasurement(measurementEntity)
            
            // 更新病患最後訪問時間
            patientId?.let { id ->
                patientRepository.updateLastVisitTime(id, Date())
            }
            
            loadMeasurements(patientId)
            
            Result.success(measurementId)
        } catch (e: Exception) {
            _error.value = "保存測量記錄失敗: ${e.message}"
            Result.failure(e)
        } finally {
            _isLoading.value = false
        }
    }
    
    suspend fun loadMeasurements(patientId: String?) {
        try {
            if (patientId != null) {
                measurementRepository.getMeasurementsByPatient(patientId).collect { measurements ->
                    _measurements.value = measurements
                }
            } else {
                measurementRepository.getGeneralMeasurements().collect { measurements ->
                    _measurements.value = measurements
                }
            }
        } catch (e: Exception) {
            _error.value = "載入測量記錄失敗: ${e.message}"
        }
    }
    
    suspend fun getMeasurementById(measurementId: Long): MeasurementEntity? {
        return try {
            measurementRepository.getMeasurementById(measurementId)
        } catch (e: Exception) {
            _error.value = "查詢測量記錄失敗: ${e.message}"
            null
        }
    }
    
    suspend fun getLatestMeasurementByPatient(patientId: String): MeasurementEntity? {
        return try {
            measurementRepository.getLatestMeasurementByPatient(patientId)
        } catch (e: Exception) {
            _error.value = "查詢最新測量記錄失敗: ${e.message}"
            null
        }
    }
    
    // 統計分析
    suspend fun getMeasurementStatistics(patientId: String): MeasurementStatistics {
        return try {
            val measurements = measurementRepository.getMeasurementsByPatient(patientId).first()
            val averageArea = measurementRepository.getAverageAreaByPatient(patientId)
            val averageVolume = measurementRepository.getAverageVolumeByPatient(patientId)
            val minArea = measurementRepository.getMinAreaByPatient(patientId)
            val maxArea = measurementRepository.getMaxAreaByPatient(patientId)
            val count = measurementRepository.getMeasurementCountByPatient(patientId)
            
            MeasurementStatistics(
                totalMeasurements = count,
                averageArea = averageArea,
                averageVolume = averageVolume,
                minArea = minArea,
                maxArea = maxArea,
                measurements = measurements
            )
        } catch (e: Exception) {
            _error.value = "獲取統計數據失敗: ${e.message}"
            MeasurementStatistics()
        }
    }
    
    // 清除錯誤
    fun clearError() {
        _error.value = null
    }
    
    // 設置當前病患
    fun setCurrentPatient(patient: PatientEntity?) {
        _currentPatient.value = patient
        patient?.let { loadMeasurements(it.id) }
    }
    
    data class MeasurementStatistics(
        val totalMeasurements: Int = 0,
        val averageArea: Double? = null,
        val averageVolume: Double? = null,
        val minArea: Double? = null,
        val maxArea: Double? = null,
        val measurements: List<MeasurementEntity> = emptyList()
    )
} 