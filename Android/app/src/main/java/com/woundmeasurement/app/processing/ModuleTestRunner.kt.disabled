package com.woundmeasurement.app.processing

import android.content.Context
import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.util.Date

class ModuleTestRunner(
    private val context: Context,
    private val coroutineScope: CoroutineScope
) {
    companion object {
        private const val TAG = "ModuleTestRunner"
    }
    
    suspend fun runFullTestSuite(): TestResult {
        Log.d(TAG, "開始執行完整測試套件")
        
        val results = mutableListOf<TestResult>()
        
        // 測試各個模組
        results.add(testPatientIdentificationModule())
        results.add(testDataStorageModule())
        results.add(testAnalysisHistoryModule())
        results.add(testDatabaseIntegrationModule())
        results.add(testModuleIntegration())
        
        // 計算總體結果
        val totalTests = results.sumOf { it.totalTests }
        val passedTests = results.sumOf { it.passedTests }
        val failedTests = results.sumOf { it.failedTests }
        
        val overallResult = TestResult(
            moduleName = "完整系統",
            totalTests = totalTests,
            passedTests = passedTests,
            failedTests = failedTests,
            successRate = if (totalTests > 0) (passedTests.toDouble() / totalTests * 100) else 0.0,
            details = results.flatMap { it.details }
        )
        
        Log.d(TAG, "測試完成: ${overallResult.successRate}% 通過率")
        return overallResult
    }
    
    private suspend fun testPatientIdentificationModule(): TestResult {
        Log.d(TAG, "測試病患識別模組")
        
        val testResults = mutableListOf<TestDetail>()
        var passedTests = 0
        var failedTests = 0
        
        try {
            val module = PatientIdentificationModule()
            
            // 測試條碼解析
            val barcodeData = "PATIENT:12345:張三:1990-01-01:男:內科"
            val result = module.parseBarcodeData(barcodeData)
            
            if (result != null && result.id == "12345" && result.name == "張三") {
                testResults.add(TestDetail("條碼解析", true, "成功解析病患條碼"))
                passedTests++
            } else {
                testResults.add(TestDetail("條碼解析", false, "條碼解析失敗"))
                failedTests++
            }
            
            // 測試病患信息驗證
            val validPatient = PatientIdentificationModule.PatientInfo(
                id = "12345",
                name = "張三",
                birthDate = "1990-01-01",
                gender = "男",
                department = "內科"
            )
            
            val validationResult = module.validatePatientInfo(validPatient)
            if (validationResult) {
                testResults.add(TestDetail("病患信息驗證", true, "病患信息驗證通過"))
                passedTests++
            } else {
                testResults.add(TestDetail("病患信息驗證", false, "病患信息驗證失敗"))
                failedTests++
            }
            
            // 測試手動輸入
            module.manualPatientInput(validPatient)
            val hasPatient = module.hasIdentifiedPatient()
            
            if (hasPatient) {
                testResults.add(TestDetail("手動輸入", true, "手動輸入病患信息成功"))
                passedTests++
            } else {
                testResults.add(TestDetail("手動輸入", false, "手動輸入病患信息失敗"))
                failedTests++
            }
            
        } catch (e: Exception) {
            testResults.add(TestDetail("病患識別模組", false, "模組測試異常: ${e.message}"))
            failedTests++
        }
        
        return TestResult(
            moduleName = "病患識別模組",
            totalTests = passedTests + failedTests,
            passedTests = passedTests,
            failedTests = failedTests,
            successRate = if ((passedTests + failedTests) > 0) (passedTests.toDouble() / (passedTests + failedTests) * 100) else 0.0,
            details = testResults
        )
    }
    
    private suspend fun testDataStorageModule(): TestResult {
        Log.d(TAG, "測試數據存儲模組")
        
        val testResults = mutableListOf<TestDetail>()
        var passedTests = 0
        var failedTests = 0
        
        try {
            val module = DataStorageModule(context)
            
            // 測試設置當前病患
            val patient = PatientIdentificationModule.PatientInfo(
                id = "12345",
                name = "張三",
                birthDate = "1990-01-01",
                gender = "男",
                department = "內科"
            )
            
            module.setCurrentPatient(patient)
            val currentPatient = module.getCurrentPatient()
            
            if (currentPatient?.id == patient.id) {
                testResults.add(TestDetail("設置當前病患", true, "成功設置當前病患"))
                passedTests++
            } else {
                testResults.add(TestDetail("設置當前病患", false, "設置當前病患失敗"))
                failedTests++
            }
            
            // 測試保存測量結果（有病患）
            val measurementResult = RealTimeAnalysisModule.QuickAnalysisResult(
                hasWound = true,
                confidence = 0.85,
                estimatedArea = 15.5,
                estimatedVolume = 8.2,
                woundType = "慢性傷口",
                quality = "高",
                processingTime = 1500
            )
            
            val saveResult = module.saveMeasurementResult(measurementResult, "test_image.jpg", "test_data.json")
            
            if (saveResult) {
                testResults.add(TestDetail("保存病患測量結果", true, "成功保存病患測量結果"))
                passedTests++
            } else {
                testResults.add(TestDetail("保存病患測量結果", false, "保存病患測量結果失敗"))
                failedTests++
            }
            
            // 測試保存測量結果（無病患）
            module.setCurrentPatient(null)
            val generalSaveResult = module.saveMeasurementResult(measurementResult, "test_image.jpg", "test_data.json")
            
            if (generalSaveResult) {
                testResults.add(TestDetail("保存一般測量結果", true, "成功保存一般測量結果"))
                passedTests++
            } else {
                testResults.add(TestDetail("保存一般測量結果", false, "保存一般測量結果失敗"))
                failedTests++
            }
            
        } catch (e: Exception) {
            testResults.add(TestDetail("數據存儲模組", false, "模組測試異常: ${e.message}"))
            failedTests++
        }
        
        return TestResult(
            moduleName = "數據存儲模組",
            totalTests = passedTests + failedTests,
            passedTests = passedTests,
            failedTests = failedTests,
            successRate = if ((passedTests + failedTests) > 0) (passedTests.toDouble() / (passedTests + failedTests) * 100) else 0.0,
            details = testResults
        )
    }
    
    private suspend fun testAnalysisHistoryModule(): TestResult {
        Log.d(TAG, "測試歷史分析模組")
        
        val testResults = mutableListOf<TestDetail>()
        var passedTests = 0
        var failedTests = 0
        
        try {
            val module = AnalysisHistoryModule()
            
            // 測試載入歷史數據
            val historicalData = module.loadHistoricalData("12345", AnalysisHistoryModule.TimeRange.WEEK)
            
            if (historicalData.isNotEmpty()) {
                testResults.add(TestDetail("載入歷史數據", true, "成功載入歷史數據，共${historicalData.size}條記錄"))
                passedTests++
            } else {
                testResults.add(TestDetail("載入歷史數據", false, "載入歷史數據失敗"))
                failedTests++
            }
            
            // 測試趨勢分析
            val trendAnalysis = module.calculateTrends(historicalData)
            
            if (trendAnalysis != null) {
                testResults.add(TestDetail("趨勢分析", true, "成功計算趨勢分析"))
                passedTests++
            } else {
                testResults.add(TestDetail("趨勢分析", false, "趨勢分析失敗"))
                failedTests++
            }
            
            // 測試癒合進度計算
            val healingProgress = module.calculateHealingProgress(historicalData)
            
            if (healingProgress != null) {
                testResults.add(TestDetail("癒合進度", true, "成功計算癒合進度: ${healingProgress.progressPercentage}%"))
                passedTests++
            } else {
                testResults.add(TestDetail("癒合進度", false, "癒合進度計算失敗"))
                failedTests++
            }
            
        } catch (e: Exception) {
            testResults.add(TestDetail("歷史分析模組", false, "模組測試異常: ${e.message}"))
            failedTests++
        }
        
        return TestResult(
            moduleName = "歷史分析模組",
            totalTests = passedTests + failedTests,
            passedTests = passedTests,
            failedTests = failedTests,
            successRate = if ((passedTests + failedTests) > 0) (passedTests.toDouble() / (passedTests + failedTests) * 100) else 0.0,
            details = testResults
        )
    }
    
    private suspend fun testDatabaseIntegrationModule(): TestResult {
        Log.d(TAG, "測試數據庫整合模組")
        
        val testResults = mutableListOf<TestDetail>()
        var passedTests = 0
        var failedTests = 0
        
        try {
            val module = DatabaseIntegrationModule(context, coroutineScope)
            
            // 測試保存病患
            val patientInfo = PatientIdentificationModule.PatientInfo(
                id = "TEST123",
                name = "測試病患",
                birthDate = "1990-01-01",
                gender = "男",
                department = "測試科"
            )
            
            val savePatientResult = module.savePatient(patientInfo)
            
            if (savePatientResult.isSuccess) {
                testResults.add(TestDetail("保存病患", true, "成功保存病患到數據庫"))
                passedTests++
            } else {
                testResults.add(TestDetail("保存病患", false, "保存病患到數據庫失敗"))
                failedTests++
            }
            
            // 測試查詢病患
            val queriedPatient = module.getPatientById("TEST123")
            
            if (queriedPatient != null && queriedPatient.name == "測試病患") {
                testResults.add(TestDetail("查詢病患", true, "成功查詢病患信息"))
                passedTests++
            } else {
                testResults.add(TestDetail("查詢病患", false, "查詢病患信息失敗"))
                failedTests++
            }
            
            // 測試保存測量記錄
            val saveMeasurementResult = module.saveMeasurement(
                patientId = "TEST123",
                hasWound = true,
                confidence = 0.85,
                estimatedArea = 15.5,
                estimatedVolume = 8.2,
                woundType = "測試傷口",
                quality = "高",
                processingTime = 1500,
                imagePath = "test_image.jpg",
                dataPath = "test_data.json"
            )
            
            if (saveMeasurementResult.isSuccess) {
                testResults.add(TestDetail("保存測量記錄", true, "成功保存測量記錄到數據庫"))
                passedTests++
            } else {
                testResults.add(TestDetail("保存測量記錄", false, "保存測量記錄到數據庫失敗"))
                failedTests++
            }
            
            // 測試統計分析
            val statistics = module.getMeasurementStatistics("TEST123")
            
            if (statistics.totalMeasurements > 0) {
                testResults.add(TestDetail("統計分析", true, "成功獲取統計數據"))
                passedTests++
            } else {
                testResults.add(TestDetail("統計分析", false, "獲取統計數據失敗"))
                failedTests++
            }
            
        } catch (e: Exception) {
            testResults.add(TestDetail("數據庫整合模組", false, "模組測試異常: ${e.message}"))
            failedTests++
        }
        
        return TestResult(
            moduleName = "數據庫整合模組",
            totalTests = passedTests + failedTests,
            passedTests = passedTests,
            failedTests = failedTests,
            successRate = if ((passedTests + failedTests) > 0) (passedTests.toDouble() / (passedTests + failedTests) * 100) else 0.0,
            details = testResults
        )
    }
    
    private suspend fun testModuleIntegration(): TestResult {
        Log.d(TAG, "測試模組整合")
        
        val testResults = mutableListOf<TestDetail>()
        var passedTests = 0
        var failedTests = 0
        
        try {
            // 創建所有模組
            val patientModule = PatientIdentificationModule()
            val dataStorageModule = DataStorageModule(context)
            val analysisHistoryModule = AnalysisHistoryModule()
            val databaseModule = DatabaseIntegrationModule(context, coroutineScope)
            
            // 測試完整工作流程
            val patientInfo = PatientIdentificationModule.PatientInfo(
                id = "INTEGRATION123",
                name = "整合測試病患",
                birthDate = "1990-01-01",
                gender = "女",
                department = "整合科"
            )
            
            // 1. 病患識別
            patientModule.manualPatientInput(patientInfo)
            if (patientModule.hasIdentifiedPatient()) {
                testResults.add(TestDetail("工作流程-病患識別", true, "病患識別成功"))
                passedTests++
            } else {
                testResults.add(TestDetail("工作流程-病患識別", false, "病患識別失敗"))
                failedTests++
            }
            
            // 2. 設置當前病患
            dataStorageModule.setCurrentPatient(patientInfo)
            databaseModule.setCurrentPatient(null) // 先清除，然後保存到數據庫
            
            // 3. 保存到數據庫
            val saveResult = databaseModule.savePatient(patientInfo)
            if (saveResult.isSuccess) {
                testResults.add(TestDetail("工作流程-數據庫保存", true, "病患信息保存到數據庫成功"))
                passedTests++
            } else {
                testResults.add(TestDetail("工作流程-數據庫保存", false, "病患信息保存到數據庫失敗"))
                failedTests++
            }
            
            // 4. 模擬測量結果
            val measurementResult = RealTimeAnalysisModule.QuickAnalysisResult(
                hasWound = true,
                confidence = 0.90,
                estimatedArea = 20.0,
                estimatedVolume = 12.0,
                woundType = "整合測試傷口",
                quality = "高",
                processingTime = 2000
            )
            
            // 5. 保存測量結果
            val saveMeasurementResult = dataStorageModule.saveMeasurementResult(
                measurementResult, "integration_test_image.jpg", "integration_test_data.json"
            )
            
            if (saveMeasurementResult) {
                testResults.add(TestDetail("工作流程-測量保存", true, "測量結果保存成功"))
                passedTests++
            } else {
                testResults.add(TestDetail("工作流程-測量保存", false, "測量結果保存失敗"))
                failedTests++
            }
            
            // 6. 保存到數據庫
            val dbSaveResult = databaseModule.saveMeasurement(
                patientId = patientInfo.id,
                hasWound = measurementResult.hasWound,
                confidence = measurementResult.confidence,
                estimatedArea = measurementResult.estimatedArea,
                estimatedVolume = measurementResult.estimatedVolume,
                woundType = measurementResult.woundType,
                quality = measurementResult.quality,
                processingTime = measurementResult.processingTime,
                imagePath = "integration_test_image.jpg",
                dataPath = "integration_test_data.json"
            )
            
            if (dbSaveResult.isSuccess) {
                testResults.add(TestDetail("工作流程-數據庫測量保存", true, "測量結果保存到數據庫成功"))
                passedTests++
            } else {
                testResults.add(TestDetail("工作流程-數據庫測量保存", false, "測量結果保存到數據庫失敗"))
                failedTests++
            }
            
            // 7. 歷史分析
            val historicalData = analysisHistoryModule.loadHistoricalData(patientInfo.id, AnalysisHistoryModule.TimeRange.WEEK)
            if (historicalData.isNotEmpty()) {
                testResults.add(TestDetail("工作流程-歷史分析", true, "歷史分析成功，共${historicalData.size}條記錄"))
                passedTests++
            } else {
                testResults.add(TestDetail("工作流程-歷史分析", false, "歷史分析失敗"))
                failedTests++
            }
            
        } catch (e: Exception) {
            testResults.add(TestDetail("模組整合", false, "整合測試異常: ${e.message}"))
            failedTests++
        }
        
        return TestResult(
            moduleName = "模組整合",
            totalTests = passedTests + failedTests,
            passedTests = passedTests,
            failedTests = failedTests,
            successRate = if ((passedTests + failedTests) > 0) (passedTests.toDouble() / (passedTests + failedTests) * 100) else 0.0,
            details = testResults
        )
    }
    
    data class TestResult(
        val moduleName: String,
        val totalTests: Int,
        val passedTests: Int,
        val failedTests: Int,
        val successRate: Double,
        val details: List<TestDetail>
    )
    
    data class TestDetail(
        val testName: String,
        val passed: Boolean,
        val message: String
    )
} 