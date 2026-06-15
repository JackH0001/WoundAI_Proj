package com.woundmeasurement.app.processing

import android.content.Context
import android.graphics.Bitmap
import android.util.Log
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.io.File
import java.io.FileOutputStream
import java.util.*

class DataStorageModule(private val context: Context) {
    companion object {
        private const val TAG = "DataStorageModule"
        private const val PATIENT_DATA_DIR = "patient_data"
        private const val GENERAL_IMAGE_DIR = "general_images"
        private const val IMAGE_QUALITY = 90
    }

    // 狀態管理
    private val _storageStatus = MutableStateFlow(StorageStatus.IDLE)
    val storageStatus: StateFlow<StorageStatus> = _storageStatus.asStateFlow()

    private val _currentPatient = MutableStateFlow<PatientIdentificationModule.PatientInfo?>(null)
    val currentPatient: StateFlow<PatientIdentificationModule.PatientInfo?> = _currentPatient.asStateFlow()

    // 處理佇列
    private val processingScope = CoroutineScope(Dispatchers.IO + SupervisorJob())

    /**
     * 設置當前病患
     */
    fun setCurrentPatient(patientInfo: PatientIdentificationModule.PatientInfo?) {
        _currentPatient.value = patientInfo
        Log.d(TAG, "設置當前病患: ${patientInfo?.name ?: "無"}")
    }

    /**
     * 保存測量結果
     */
    suspend fun saveMeasurementResult(
        image: Bitmap,
        measurementResult: RealTimeAnalysisModule.RealTimeAnalysisResult,
        notes: String = ""
    ): StorageResult {
        return withContext(Dispatchers.IO) {
            try {
                _storageStatus.value = StorageStatus.SAVING
                
                val currentPatient = _currentPatient.value
                
                if (currentPatient != null) {
                    // 有識別的病患，保存到病患專用目錄
                    saveToPatientDirectory(image, measurementResult, currentPatient, notes)
                } else {
                    // 無識別的病患，保存到一般圖像庫
                    saveToGeneralImageLibrary(image, measurementResult, notes)
                }
                
                _storageStatus.value = StorageStatus.SAVED
                
                Log.d(TAG, "測量結果保存成功")
                StorageResult.SUCCESS("測量結果保存成功")
                
            } catch (e: Exception) {
                Log.e(TAG, "保存測量結果失敗", e)
                _storageStatus.value = StorageStatus.ERROR
                StorageResult.ERROR(e.message ?: "保存失敗")
            }
        }
    }

    /**
     * 保存到病患專用目錄
     */
    private suspend fun saveToPatientDirectory(
        image: Bitmap,
        measurementResult: RealTimeAnalysisModule.RealTimeAnalysisResult,
        patientInfo: PatientIdentificationModule.PatientInfo,
        notes: String
    ) {
        withContext(Dispatchers.IO) {
            try {
                // 創建病患專用目錄
                val patientDir = createPatientDirectory(patientInfo.id)
                
                // 生成文件名
                val timestamp = System.currentTimeMillis()
                val imageFileName = "wound_${timestamp}.jpg"
                val dataFileName = "measurement_${timestamp}.json"
                
                // 保存圖像
                val imageFile = File(patientDir, imageFileName)
                saveImageToFile(image, imageFile)
                
                // 保存測量數據
                val dataFile = File(patientDir, dataFileName)
                saveMeasurementDataToFile(measurementResult, patientInfo, notes, dataFile)
                
                // 更新數據庫記錄
                updatePatientMeasurementRecord(patientInfo.id, imageFileName, dataFileName, measurementResult)
                
                Log.d(TAG, "病患數據保存成功: ${patientInfo.name}")
                
            } catch (e: Exception) {
                Log.e(TAG, "保存到病患目錄失敗", e)
                throw e
            }
        }
    }

    /**
     * 保存到一般圖像庫
     */
    private suspend fun saveToGeneralImageLibrary(
        image: Bitmap,
        measurementResult: RealTimeAnalysisModule.RealTimeAnalysisResult,
        notes: String
    ) {
        withContext(Dispatchers.IO) {
            try {
                // 創建一般圖像庫目錄
                val generalDir = createGeneralImageDirectory()
                
                // 生成文件名
                val timestamp = System.currentTimeMillis()
                val imageFileName = "general_wound_${timestamp}.jpg"
                val dataFileName = "general_measurement_${timestamp}.json"
                
                // 保存圖像
                val imageFile = File(generalDir, imageFileName)
                saveImageToFile(image, imageFile)
                
                // 保存測量數據
                val dataFile = File(generalDir, dataFileName)
                saveMeasurementDataToFile(measurementResult, null, notes, dataFile)
                
                // 更新一般圖像庫記錄
                updateGeneralImageRecord(imageFileName, dataFileName, measurementResult)
                
                Log.d(TAG, "一般圖像庫數據保存成功")
                
            } catch (e: Exception) {
                Log.e(TAG, "保存到一般圖像庫失敗", e)
                throw e
            }
        }
    }

    /**
     * 創建病患專用目錄
     */
    private fun createPatientDirectory(patientId: String): File {
        val patientDir = File(context.filesDir, "$PATIENT_DATA_DIR/$patientId")
        if (!patientDir.exists()) {
            patientDir.mkdirs()
        }
        return patientDir
    }

    /**
     * 創建一般圖像庫目錄
     */
    private fun createGeneralImageDirectory(): File {
        val generalDir = File(context.filesDir, GENERAL_IMAGE_DIR)
        if (!generalDir.exists()) {
            generalDir.mkdirs()
        }
        return generalDir
    }

    /**
     * 保存圖像到文件
     */
    private fun saveImageToFile(image: Bitmap, file: File) {
        FileOutputStream(file).use { out ->
            image.compress(Bitmap.CompressFormat.JPEG, IMAGE_QUALITY, out)
        }
    }

    /**
     * 保存測量數據到文件
     */
    private fun saveMeasurementDataToFile(
        measurementResult: RealTimeAnalysisModule.RealTimeAnalysisResult,
        patientInfo: PatientIdentificationModule.PatientInfo?,
        notes: String,
        file: File
    ) {
        val measurementData = MeasurementData(
            timestamp = measurementResult.timestamp,
            patientInfo = patientInfo,
            hasWound = measurementResult.hasWound,
            confidence = measurementResult.confidence,
            estimatedArea = measurementResult.estimatedArea,
            estimatedVolume = measurementResult.estimatedVolume,
            woundType = measurementResult.woundType,
            quality = measurementResult.quality,
            processingTime = measurementResult.processingTime,
            notes = notes
        )
        
        // 將數據轉換為JSON並保存
        val jsonData = measurementData.toJson()
        file.writeText(jsonData)
    }

    /**
     * 更新病患測量記錄
     */
    private suspend fun updatePatientMeasurementRecord(
        patientId: String,
        imageFileName: String,
        dataFileName: String,
        measurementResult: RealTimeAnalysisModule.RealTimeAnalysisResult
    ) {
        withContext(Dispatchers.IO) {
            try {
                // 這裡應該更新SQLite數據庫
                // 暫時只記錄日誌
                Log.d(TAG, "更新病患測量記錄: $patientId")
                
            } catch (e: Exception) {
                Log.e(TAG, "更新病患測量記錄失敗", e)
            }
        }
    }

    /**
     * 更新一般圖像庫記錄
     */
    private suspend fun updateGeneralImageRecord(
        imageFileName: String,
        dataFileName: String,
        measurementResult: RealTimeAnalysisModule.RealTimeAnalysisResult
    ) {
        withContext(Dispatchers.IO) {
            try {
                // 這裡應該更新SQLite數據庫
                // 暫時只記錄日誌
                Log.d(TAG, "更新一般圖像庫記錄")
                
            } catch (e: Exception) {
                Log.e(TAG, "更新一般圖像庫記錄失敗", e)
            }
        }
    }

    /**
     * 載入病患歷史數據
     */
    suspend fun loadPatientHistory(patientId: String): List<MeasurementData> {
        return withContext(Dispatchers.IO) {
            try {
                val patientDir = File(context.filesDir, "$PATIENT_DATA_DIR/$patientId")
                if (!patientDir.exists()) {
                    return@withContext emptyList()
                }
                
                val measurementFiles = patientDir.listFiles { file ->
                    file.name.startsWith("measurement_") && file.name.endsWith(".json")
                } ?: emptyArray()
                
                measurementFiles.mapNotNull { file ->
                    try {
                        val jsonData = file.readText()
                        MeasurementData.fromJson(jsonData)
                    } catch (e: Exception) {
                        Log.e(TAG, "解析測量數據文件失敗: ${file.name}", e)
                        null
                    }
                }.sortedByDescending { it.timestamp }
                
            } catch (e: Exception) {
                Log.e(TAG, "載入病患歷史數據失敗", e)
                emptyList()
            }
        }
    }

    /**
     * 載入一般圖像庫數據
     */
    suspend fun loadGeneralImageLibrary(): List<MeasurementData> {
        return withContext(Dispatchers.IO) {
            try {
                val generalDir = File(context.filesDir, GENERAL_IMAGE_DIR)
                if (!generalDir.exists()) {
                    return@withContext emptyList()
                }
                
                val measurementFiles = generalDir.listFiles { file ->
                    file.name.startsWith("general_measurement_") && file.name.endsWith(".json")
                } ?: emptyArray()
                
                measurementFiles.mapNotNull { file ->
                    try {
                        val jsonData = file.readText()
                        MeasurementData.fromJson(jsonData)
                    } catch (e: Exception) {
                        Log.e(TAG, "解析一般圖像數據文件失敗: ${file.name}", e)
                        null
                    }
                }.sortedByDescending { it.timestamp }
                
            } catch (e: Exception) {
                Log.e(TAG, "載入一般圖像庫數據失敗", e)
                emptyList()
            }
        }
    }

    /**
     * 清理資源
     */
    fun cleanup() {
        processingScope.cancel()
    }

    // 數據類別
    data class MeasurementData(
        val timestamp: Date,
        val patientInfo: PatientIdentificationModule.PatientInfo?,
        val hasWound: Boolean,
        val confidence: Double,
        val estimatedArea: Double?,
        val estimatedVolume: Double?,
        val woundType: String?,
        val quality: String,
        val processingTime: Long,
        val notes: String
    ) {
        fun toJson(): String {
            // 簡化的JSON轉換，實際實作應使用Gson或Moshi
            return """
                {
                    "timestamp": "${timestamp.time}",
                    "hasWound": $hasWound,
                    "confidence": $confidence,
                    "estimatedArea": ${estimatedArea ?: "null"},
                    "estimatedVolume": ${estimatedVolume ?: "null"},
                    "woundType": "${woundType ?: ""}",
                    "quality": "$quality",
                    "processingTime": $processingTime,
                    "notes": "$notes"
                }
            """.trimIndent()
        }

        companion object {
            fun fromJson(json: String): MeasurementData? {
                try {
                    // 簡化的JSON解析，實際實作應使用Gson或Moshi
                    // 這裡只是示例，實際需要完整的JSON解析
                    return null
                } catch (e: Exception) {
                    Log.e(TAG, "解析JSON失敗", e)
                    return null
                }
            }
        }
    }

    sealed class StorageResult {
        data class SUCCESS(val message: String) : StorageResult()
        data class ERROR(val message: String) : StorageResult()
    }

    enum class StorageStatus {
        IDLE,
        SAVING,
        SAVED,
        ERROR
    }
} 