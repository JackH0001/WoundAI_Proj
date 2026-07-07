package com.woundmeasurement.app.processing

import android.content.Context
import android.graphics.Bitmap
import android.util.Log
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.util.*

class PatientIdentificationModule(private val context: Context? = null) {
    companion object {
        private const val TAG = "PatientIdentification"
        private const val MAX_RECENT_PATIENTS = 10
    }

    // 狀態管理
    private val _currentPatient = MutableStateFlow<PatientInfo?>(null)
    val currentPatient: StateFlow<PatientInfo?> = _currentPatient.asStateFlow()

    private val _isScanning = MutableStateFlow(false)
    val isScanning: StateFlow<Boolean> = _isScanning.asStateFlow()

    private val _recentPatients = MutableStateFlow<List<PatientInfo>>(emptyList())
    val recentPatients: StateFlow<List<PatientInfo>> = _recentPatients.asStateFlow()

    private val _scanResult = MutableStateFlow<ScanResult?>(null)
    val scanResult: StateFlow<ScanResult?> = _scanResult.asStateFlow()

    // 處理佇列
    private val processingScope = CoroutineScope(Dispatchers.Default + SupervisorJob())

    /**
     * 開始條碼掃描
     */
    fun startBarcodeScanning() {
        _isScanning.value = true
        Log.d(TAG, "開始條碼掃描")
    }

    /**
     * 停止條碼掃描
     */
    fun stopBarcodeScanning() {
        _isScanning.value = false
        Log.d(TAG, "停止條碼掃描")
    }

    /**
     * 處理掃描結果
     */
    suspend fun processScanResult(barcodeData: String): PatientIdentificationResult {
        return withContext(Dispatchers.Default) {
            try {
                Log.d(TAG, "處理掃描結果: $barcodeData")
                
                // 解析條碼數據
                val patientInfo = parseBarcodeData(barcodeData)
                
                if (patientInfo != null) {
                    // 驗證病患信息
                    val validationResult = validatePatientInfoDetailed(patientInfo)
                    
                    if (validationResult.isValid) {
                        // 設置當前病患
                        _currentPatient.value = patientInfo
                        
                        // 添加到最近病患列表
                        addToRecentPatients(patientInfo)
                        
                        // 更新掃描結果
                        _scanResult.value = ScanResult.SUCCESS(patientInfo)
                        
                        Log.d(TAG, "病患識別成功: ${patientInfo.name}")
                        
                        PatientIdentificationResult(
                            success = true,
                            patientInfo = patientInfo,
                            message = "病患識別成功"
                        )
                    } else {
                        _scanResult.value = ScanResult.INVALID_DATA(validationResult.errorMessage)
                        
                        PatientIdentificationResult(
                            success = false,
                            patientInfo = null,
                            message = validationResult.errorMessage
                        )
                    }
                } else {
                    _scanResult.value = ScanResult.INVALID_FORMAT("無效的條碼格式")
                    
                    PatientIdentificationResult(
                        success = false,
                        patientInfo = null,
                        message = "無效的條碼格式"
                    )
                }
                
            } catch (e: Exception) {
                Log.e(TAG, "處理掃描結果失敗", e)
                
                _scanResult.value = ScanResult.ERROR(e.message ?: "未知錯誤")
                
                PatientIdentificationResult(
                    success = false,
                    patientInfo = null,
                    message = e.message ?: "處理掃描結果時發生錯誤"
                )
            } finally {
                _isScanning.value = false
            }
        }
    }

    /**
     * 手動輸入病患信息
     */
    suspend fun manualPatientInput(patientInfo: PatientInfo): PatientIdentificationResult {
        return withContext(Dispatchers.Default) {
            try {
                Log.d(TAG, "手動輸入病患信息: ${patientInfo.name}")
                
                // 驗證病患信息
                val validationResult = validatePatientInfo(patientInfo)
                
                if (validationResult.isValid) {
                    // 設置當前病患
                    _currentPatient.value = patientInfo
                    
                    // 添加到最近病患列表
                    addToRecentPatients(patientInfo)
                    
                    Log.d(TAG, "手動輸入病患信息成功")
                    
                    PatientIdentificationResult(
                        success = true,
                        patientInfo = patientInfo,
                        message = "病患信息輸入成功"
                    )
                } else {
                    PatientIdentificationResult(
                        success = false,
                        patientInfo = null,
                        message = validationResult.errorMessage
                    )
                }
                
            } catch (e: Exception) {
                Log.e(TAG, "手動輸入病患信息失敗", e)
                
                PatientIdentificationResult(
                    success = false,
                    patientInfo = null,
                    message = e.message ?: "輸入病患信息時發生錯誤"
                )
            }
        }
    }

    /**
     * 從最近病患列表選擇
     */
    suspend fun selectFromRecentPatients(patientId: String): PatientIdentificationResult {
        return withContext(Dispatchers.Default) {
            try {
                val patient = _recentPatients.value.find { it.id == patientId }
                
                if (patient != null) {
                    _currentPatient.value = patient
                    
                    Log.d(TAG, "從最近病患列表選擇: ${patient.name}")
                    
                    PatientIdentificationResult(
                        success = true,
                        patientInfo = patient,
                        message = "已選擇病患: ${patient.name}"
                    )
                } else {
                    PatientIdentificationResult(
                        success = false,
                        patientInfo = null,
                        message = "找不到指定的病患"
                    )
                }
                
            } catch (e: Exception) {
                Log.e(TAG, "選擇最近病患失敗", e)
                
                PatientIdentificationResult(
                    success = false,
                    patientInfo = null,
                    message = e.message ?: "選擇病患時發生錯誤"
                )
            }
        }
    }

    /**
     * 清除當前病患
     */
    fun clearCurrentPatient() {
        _currentPatient.value = null
        Log.d(TAG, "已清除當前病患")
    }

    /**
     * 檢查是否有識別的病患
     */
    fun hasIdentifiedPatient(): Boolean {
        return _currentPatient.value != null
    }

    /**
     * 解析條碼數據
     */
    fun parseBarcodeData(barcodeData: String): PatientInfo? {
        try {
            // 支援多種條碼格式
            return when {
                // 格式1: PATIENT_ID|NAME|BIRTH_DATE|GENDER
                barcodeData.contains("|") -> {
                    val parts = barcodeData.split("|")
                    if (parts.size >= 4) {
                        PatientInfo(
                            id = parts[0],
                            name = parts[1],
                            birthDate = parts[2],
                            gender = parts[3],
                            medicalRecordNumber = parts.getOrNull(4) ?: "",
                            department = parts.getOrNull(5) ?: "",
                            registrationTime = Date()
                        )
                    } else null
                }
                
                // 格式2: 純數字病患ID
                barcodeData.matches(Regex("^\\d+$")) -> {
                    // 從本地數據庫或API查詢病患信息
                    lookupPatientById(barcodeData)
                }
                
                // 格式3: 醫療記錄號碼
                barcodeData.matches(Regex("^MR\\d+$")) -> {
                    // 從本地數據庫或API查詢病患信息
                    lookupPatientByMRN(barcodeData)
                }
                
                else -> null
            }
        } catch (e: Exception) {
            Log.e(TAG, "解析條碼數據失敗", e)
            return null
        }
    }

    /**
     * 根據病患ID查詢病患信息
     */
    private fun lookupPatientById(patientId: String): PatientInfo? {
        // 這裡應該從本地數據庫或API查詢
        // 暫時返回模擬數據
        return PatientInfo(
            id = patientId,
            name = "病患$patientId",
            birthDate = "1980-01-01",
            gender = "男",
            medicalRecordNumber = "MR$patientId",
            department = "內科",
            registrationTime = Date()
        )
    }

    /**
     * 根據醫療記錄號碼查詢病患信息
     */
    private fun lookupPatientByMRN(mrn: String): PatientInfo? {
        // 這裡應該從本地數據庫或API查詢
        // 暫時返回模擬數據
        return PatientInfo(
            id = mrn.substring(2), // 移除"MR"前綴
            name = "病患${mrn.substring(2)}",
            birthDate = "1980-01-01",
            gender = "女",
            medicalRecordNumber = mrn,
            department = "外科",
            registrationTime = Date()
        )
    }

    /**
     * 驗證病患信息
     */
    fun validatePatientInfo(patientInfo: PatientInfo): Boolean {
        val errors = mutableListOf<String>()
        
        if (patientInfo.id.isBlank()) {
            errors.add("病患ID不能為空")
        }
        
        if (patientInfo.name.isBlank()) {
            errors.add("病患姓名不能為空")
        }
        
        if (patientInfo.birthDate.isBlank()) {
            errors.add("出生日期不能為空")
        }
        
        if (patientInfo.gender.isBlank()) {
            errors.add("性別不能為空")
        }
        
        return errors.isEmpty()
    }
    
    /**
     * 驗證病患信息（詳細版本）
     */
    private fun validatePatientInfoDetailed(patientInfo: PatientInfo): ValidationResult {
        val errors = mutableListOf<String>()
        
        if (patientInfo.id.isBlank()) {
            errors.add("病患ID不能為空")
        }
        
        if (patientInfo.name.isBlank()) {
            errors.add("病患姓名不能為空")
        }
        
        if (patientInfo.birthDate.isBlank()) {
            errors.add("出生日期不能為空")
        }
        
        if (patientInfo.gender.isBlank()) {
            errors.add("性別不能為空")
        }
        
        return ValidationResult(
            isValid = errors.isEmpty(),
            errorMessage = errors.joinToString(", ")
        )
    }

    /**
     * 添加到最近病患列表
     */
    private fun addToRecentPatients(patientInfo: PatientInfo) {
        val currentList = _recentPatients.value.toMutableList()
        
        // 移除已存在的相同病患
        currentList.removeAll { it.id == patientInfo.id }
        
        // 添加到列表開頭
        currentList.add(0, patientInfo)
        
        // 限制列表大小
        if (currentList.size > MAX_RECENT_PATIENTS) {
            currentList.removeAt(currentList.size - 1)
        }
        
        _recentPatients.value = currentList
    }

    /**
     * 載入最近病患列表
     */
    suspend fun loadRecentPatients() {
        withContext(Dispatchers.IO) {
            try {
                // 從本地數據庫載入最近病患列表
                val recentPatients = loadRecentPatientsFromDatabase()
                _recentPatients.value = recentPatients
                
                Log.d(TAG, "載入最近病患列表完成，共${recentPatients.size}個病患")
                
            } catch (e: Exception) {
                Log.e(TAG, "載入最近病患列表失敗", e)
            }
        }
    }

    /**
     * 從數據庫載入最近病患列表
     */
    private fun loadRecentPatientsFromDatabase(): List<PatientInfo> {
        // 這裡應該從SQLite數據庫載入
        // 暫時返回空列表
        return emptyList()
    }

    /**
     * 清理資源
     */
    fun cleanup() {
        processingScope.cancel()
    }

    // 數據類別
    data class PatientInfo(
        val id: String,
        val name: String,
        val birthDate: String,
        val gender: String,
        val medicalRecordNumber: String = "",
        val department: String = "",
        val registrationTime: Date = Date()
    )

    data class PatientIdentificationResult(
        val success: Boolean,
        val patientInfo: PatientInfo?,
        val message: String
    )

    data class ValidationResult(
        val isValid: Boolean,
        val errorMessage: String
    )

    sealed class ScanResult {
        data class SUCCESS(val patientInfo: PatientInfo) : ScanResult()
        data class INVALID_FORMAT(val message: String) : ScanResult()
        data class INVALID_DATA(val message: String) : ScanResult()
        data class ERROR(val message: String) : ScanResult()
    }
} 