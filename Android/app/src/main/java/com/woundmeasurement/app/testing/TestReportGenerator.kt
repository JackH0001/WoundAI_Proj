package com.woundmeasurement.app.testing

import android.content.Context
import android.util.Log
import com.woundmeasurement.app.processing.ModuleTestRunner
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import java.io.File
import java.text.SimpleDateFormat
import java.util.*

class TestReportGenerator(
    private val context: Context,
    private val coroutineScope: CoroutineScope
) {
    companion object {
        private const val TAG = "TestReportGenerator"
    }
    
    suspend fun generateComprehensiveTestReport(): TestReport {
        Log.d(TAG, "開始生成綜合測試報告")
        
        val testRunner = ModuleTestRunner(context, coroutineScope)
        val testResult = testRunner.runFullTestSuite()
        
        val report = TestReport(
            timestamp = Date(),
            overallResult = testResult,
            systemInfo = getSystemInfo(),
            recommendations = generateRecommendations(testResult)
        )
        
        // 保存報告到文件
        saveReportToFile(report)
        
        return report
    }
    
    private fun getSystemInfo(): SystemInfo {
        return SystemInfo(
            androidVersion = android.os.Build.VERSION.RELEASE,
            deviceModel = android.os.Build.MODEL,
            manufacturer = android.os.Build.MANUFACTURER,
            appVersion = "1.0.0",
            testDate = Date()
        )
    }
    
    private fun generateRecommendations(testResult: ModuleTestRunner.TestResult): List<String> {
        val recommendations = mutableListOf<String>()
        
        when {
            testResult.successRate >= 90 -> {
                recommendations.add("系統運行狀況良好，可以進行生產環境部署")
                recommendations.add("建議進行性能優化和用戶體驗測試")
            }
            testResult.successRate >= 70 -> {
                recommendations.add("系統基本功能正常，但需要修復部分問題")
                recommendations.add("建議優先修復失敗的測試用例")
                recommendations.add("進行更深入的集成測試")
            }
            testResult.successRate >= 50 -> {
                recommendations.add("系統存在較多問題，需要重點修復")
                recommendations.add("建議重新審查架構設計")
                recommendations.add("增加單元測試覆蓋率")
            }
            else -> {
                recommendations.add("系統存在嚴重問題，需要全面重構")
                recommendations.add("建議重新設計核心模組")
                recommendations.add("進行代碼審查和重構")
            }
        }
        
        // 根據具體失敗的測試添加建議
        testResult.details.filter { !it.passed }.forEach { failedTest ->
            when {
                failedTest.testName.contains("數據庫") -> {
                    recommendations.add("檢查數據庫連接和權限設置")
                }
                failedTest.testName.contains("病患識別") -> {
                    recommendations.add("檢查病患識別模組的數據驗證邏輯")
                }
                failedTest.testName.contains("測量") -> {
                    recommendations.add("檢查測量結果的保存邏輯")
                }
                failedTest.testName.contains("歷史") -> {
                    recommendations.add("檢查歷史數據的載入邏輯")
                }
            }
        }
        
        return recommendations
    }
    
    private fun saveReportToFile(report: TestReport) {
        coroutineScope.launch(Dispatchers.IO) {
            try {
                val timestamp = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.getDefault()).format(Date())
                val fileName = "test_report_$timestamp.json"
                val file = File(context.filesDir, fileName)
                
                val jsonReport = generateJsonReport(report)
                file.writeText(jsonReport)
                
                Log.d(TAG, "測試報告已保存: ${file.absolutePath}")
            } catch (e: Exception) {
                Log.e(TAG, "保存測試報告失敗", e)
            }
        }
    }
    
    private fun generateJsonReport(report: TestReport): String {
        return buildString {
            appendLine("{")
            appendLine("  \"timestamp\": \"${report.timestamp}\",")
            appendLine("  \"overallResult\": {")
            appendLine("    \"moduleName\": \"${report.overallResult.moduleName}\",")
            appendLine("    \"totalTests\": ${report.overallResult.totalTests},")
            appendLine("    \"passedTests\": ${report.overallResult.passedTests},")
            appendLine("    \"failedTests\": ${report.overallResult.failedTests},")
            appendLine("    \"successRate\": ${report.overallResult.successRate}")
            appendLine("  },")
            appendLine("  \"systemInfo\": {")
            appendLine("    \"androidVersion\": \"${report.systemInfo.androidVersion}\",")
            appendLine("    \"deviceModel\": \"${report.systemInfo.deviceModel}\",")
            appendLine("    \"manufacturer\": \"${report.systemInfo.manufacturer}\",")
            appendLine("    \"appVersion\": \"${report.systemInfo.appVersion}\",")
            appendLine("    \"testDate\": \"${report.systemInfo.testDate}\"")
            appendLine("  },")
            appendLine("  \"recommendations\": [")
            report.recommendations.forEachIndexed { index, recommendation ->
                appendLine("    \"$recommendation\"${if (index < report.recommendations.size - 1) "," else ""}")
            }
            appendLine("  ]")
            appendLine("}")
        }
    }
    
    data class TestReport(
        val timestamp: Date,
        val overallResult: ModuleTestRunner.TestResult,
        val systemInfo: SystemInfo,
        val recommendations: List<String>
    )
    
    data class SystemInfo(
        val androidVersion: String,
        val deviceModel: String,
        val manufacturer: String,
        val appVersion: String,
        val testDate: Date
    )
} 