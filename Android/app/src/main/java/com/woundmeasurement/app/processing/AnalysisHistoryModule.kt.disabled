package com.woundmeasurement.app.processing

import android.util.Log
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.util.*
import kotlin.math.abs

class AnalysisHistoryModule {
    companion object {
        private const val TAG = "AnalysisHistoryModule"
        private const val MAX_HISTORY_SIZE = 100
    }

    // 狀態管理
    private val _historicalData = MutableStateFlow<List<HistoricalMeasurement>>(emptyList())
    val historicalData: StateFlow<List<HistoricalMeasurement>> = _historicalData.asStateFlow()

    private val _selectedTimeRange = MutableStateFlow(TimeRange.WEEK)
    val selectedTimeRange: StateFlow<TimeRange> = _selectedTimeRange.asStateFlow()

    private val _trendAnalysis = MutableStateFlow<TrendAnalysis?>(null)
    val trendAnalysis: StateFlow<TrendAnalysis?> = _trendAnalysis.asStateFlow()

    // 處理佇列
    private val processingScope = CoroutineScope(Dispatchers.Default + SupervisorJob())

    enum class TimeRange(val days: Int, val displayName: String) {
        WEEK(7, "一週"),
        MONTH(30, "一個月"),
        THREE_MONTHS(90, "三個月"),
        SIX_MONTHS(180, "六個月")
    }

    /**
     * 載入歷史數據
     */
    suspend fun loadHistoricalData(
        patientId: String,
        timeRange: TimeRange = TimeRange.WEEK
    ): List<HistoricalMeasurement> {
        return withContext(Dispatchers.Default) {
            try {
                Log.d(TAG, "載入歷史數據: 患者ID=$patientId, 時間範圍=${timeRange.displayName}")
                
                _selectedTimeRange.value = timeRange
                
                // 檢查是否有有效的病患ID
                if (patientId.isBlank()) {
                    Log.w(TAG, "病患ID為空，無法載入歷史數據")
                    return@withContext emptyList()
                }
                
                // 從數據庫載入指定病患的歷史數據
                val historicalData = loadHistoricalDataFromDatabase(patientId, timeRange)
                
                // 更新狀態
                _historicalData.value = historicalData
                
                // 計算趨勢分析
                calculateTrendAnalysis(historicalData)
                
                Log.d(TAG, "歷史數據載入完成，共${historicalData.size}條記錄")
                historicalData
                
            } catch (e: Exception) {
                Log.e(TAG, "載入歷史數據失敗", e)
                emptyList()
            }
        }
    }

    /**
     * 從數據庫載入歷史數據
     */
    private suspend fun loadHistoricalDataFromDatabase(
        patientId: String,
        timeRange: TimeRange
    ): List<HistoricalMeasurement> {
        return withContext(Dispatchers.IO) {
            try {
                // 這裡應該從SQLite數據庫載入指定病患的歷史數據
                // 暫時返回模擬數據
                generateMockHistoricalData(timeRange, patientId)
            } catch (e: Exception) {
                Log.e(TAG, "從數據庫載入歷史數據失敗", e)
                emptyList()
            }
        }
    }

    /**
     * 添加新的測量記錄
     */
    suspend fun addMeasurement(measurement: HistoricalMeasurement) {
        withContext(Dispatchers.Default) {
            try {
                val currentData = _historicalData.value.toMutableList()
                currentData.add(0, measurement)
                
                // 限制歷史記錄數量
                if (currentData.size > MAX_HISTORY_SIZE) {
                    currentData.removeAt(currentData.size - 1)
                }
                
                _historicalData.value = currentData
                
                // 重新計算趨勢分析
                calculateTrendAnalysis(currentData)
                
                Log.d(TAG, "新增測量記錄: ${measurement.timestamp}")
                
            } catch (e: Exception) {
                Log.e(TAG, "添加測量記錄失敗", e)
            }
        }
    }

    /**
     * 計算趨勢分析
     */
    private fun calculateTrendAnalysis(data: List<HistoricalMeasurement>) {
        if (data.size < 2) {
            _trendAnalysis.value = null
            return
        }

        try {
            // 按時間排序
            val sortedData = data.sortedBy { it.timestamp }
            
            // 計算面積趨勢
            val areaTrend = calculateTrend(sortedData.map { it.area })
            val volumeTrend = calculateTrend(sortedData.map { it.volume })
            val depthTrend = calculateTrend(sortedData.map { it.averageDepth })
            
            // 計算癒合進度
            val healingProgress = calculateHealingProgress(sortedData)
            
            // 計算統計摘要
            val statistics = calculateStatistics(sortedData)
            
            // 生成建議
            val recommendations = generateRecommendations(areaTrend, volumeTrend, healingProgress)
            
            val trendAnalysis = TrendAnalysis(
                areaTrend = areaTrend,
                volumeTrend = volumeTrend,
                depthTrend = depthTrend,
                healingProgress = healingProgress,
                statistics = statistics,
                recommendations = recommendations,
                analysisTime = Date()
            )
            
            _trendAnalysis.value = trendAnalysis
            Log.d(TAG, "趨勢分析計算完成")
            
        } catch (e: Exception) {
            Log.e(TAG, "計算趨勢分析失敗", e)
            _trendAnalysis.value = null
        }
    }

    /**
     * 計算趨勢
     */
    private fun calculateTrend(values: List<Double>): TrendData {
        if (values.size < 2) {
            return TrendData(
                trend = TrendType.STABLE,
                changeRate = 0.0,
                confidence = 0.0
            )
        }

        // 計算線性回歸
        val n = values.size
        val xValues = (0 until n).map { it.toDouble() }
        
        val sumX = xValues.sum()
        val sumY = values.sum()
        val sumXY = xValues.zip(values).sumOf { it.first * it.second }
        val sumX2 = xValues.sumOf { it * it }
        
        val slope = (n * sumXY - sumX * sumY) / (n * sumX2 - sumX * sumX)
        val changeRate = slope / values.first() * 100 // 百分比變化率
        
        // 計算趨勢類型
        val trend = when {
            changeRate > 5.0 -> TrendType.INCREASING
            changeRate < -5.0 -> TrendType.DECREASING
            else -> TrendType.STABLE
        }
        
        // 計算置信度 (基於數據點數量)
        val confidence = (n.toDouble() / 10.0).coerceAtMost(1.0)
        
        return TrendData(trend, changeRate, confidence)
    }

    /**
     * 計算癒合進度
     */
    private fun calculateHealingProgress(data: List<HistoricalMeasurement>): HealingProgress {
        if (data.size < 2) {
            return HealingProgress(
                progress = 0.0,
                estimatedCompletionDays = null,
                status = HealingStatus.UNKNOWN
            )
        }

        val firstMeasurement = data.first()
        val latestMeasurement = data.last()
        
        val initialArea = firstMeasurement.area
        val currentArea = latestMeasurement.area
        val areaReduction = (initialArea - currentArea) / initialArea * 100
        
        val progress = areaReduction.coerceIn(0.0, 100.0)
        
        // 估算完成時間
        val daysElapsed = (latestMeasurement.timestamp.time - firstMeasurement.timestamp.time) / (1000 * 60 * 60 * 24)
        val estimatedCompletionDays = if (progress > 0) {
            (daysElapsed * (100 - progress) / progress).toInt()
        } else null
        
        // 判斷癒合狀態
        val status = when {
            progress >= 90 -> HealingStatus.ALMOST_HEALED
            progress >= 50 -> HealingStatus.GOOD_PROGRESS
            progress >= 20 -> HealingStatus.SLOW_PROGRESS
            progress > 0 -> HealingStatus.MINIMAL_PROGRESS
            else -> HealingStatus.NO_PROGRESS
        }
        
        return HealingProgress(progress, estimatedCompletionDays, status)
    }

    /**
     * 計算統計摘要
     */
    private fun calculateStatistics(data: List<HistoricalMeasurement>): StatisticsSummary {
        val areas = data.map { it.area }
        val volumes = data.map { it.volume }
        val depths = data.map { it.averageDepth }
        
        return StatisticsSummary(
            totalMeasurements = data.size,
            averageArea = areas.average(),
            averageVolume = volumes.average(),
            averageDepth = depths.average(),
            maxArea = areas.maxOrNull() ?: 0.0,
            minArea = areas.minOrNull() ?: 0.0,
            areaVariance = calculateVariance(areas),
            volumeVariance = calculateVariance(volumes),
            depthVariance = calculateVariance(depths)
        )
    }

    /**
     * 計算方差
     */
    private fun calculateVariance(values: List<Double>): Double {
        if (values.isEmpty()) return 0.0
        
        val mean = values.average()
        return values.map { (it - mean) * (it - mean) }.average()
    }

    /**
     * 生成建議
     */
    private fun generateRecommendations(
        areaTrend: TrendData,
        volumeTrend: TrendData,
        healingProgress: HealingProgress
    ): List<Recommendation> {
        val recommendations = mutableListOf<Recommendation>()
        
        // 基於面積趨勢的建議
        when (areaTrend.trend) {
            TrendType.INCREASING -> {
                recommendations.add(
                    Recommendation(
                        type = RecommendationType.WARNING,
                        title = "傷口面積增加",
                        description = "傷口面積呈現增加趨勢，建議立即就醫檢查",
                        priority = Priority.HIGH
                    )
                )
            }
            TrendType.DECREASING -> {
                recommendations.add(
                    Recommendation(
                        type = RecommendationType.POSITIVE,
                        title = "癒合進展良好",
                        description = "傷口面積持續減少，癒合進展良好",
                        priority = Priority.LOW
                    )
                )
            }
            TrendType.STABLE -> {
                recommendations.add(
                    Recommendation(
                        type = RecommendationType.INFO,
                        title = "癒合進展穩定",
                        description = "傷口面積保持穩定，繼續觀察",
                        priority = Priority.MEDIUM
                    )
                )
            }
        }
        
        // 基於癒合進度的建議
        when (healingProgress.status) {
            HealingStatus.NO_PROGRESS -> {
                recommendations.add(
                    Recommendation(
                        type = RecommendationType.WARNING,
                        title = "無癒合進展",
                        description = "傷口未顯示癒合進展，建議諮詢醫生",
                        priority = Priority.HIGH
                    )
                )
            }
            HealingStatus.SLOW_PROGRESS -> {
                recommendations.add(
                    Recommendation(
                        type = RecommendationType.INFO,
                        title = "癒合進展緩慢",
                        description = "癒合進展較慢，可能需要調整治療方案",
                        priority = Priority.MEDIUM
                    )
                )
            }
            else -> {
                // 其他狀態不需要額外建議
            }
        }
        
        return recommendations
    }

    /**
     * 生成模擬歷史數據
     */
    private fun generateMockHistoricalData(timeRange: TimeRange, patientId: String = "default"): List<HistoricalMeasurement> {
        val calendar = Calendar.getInstance()
        val now = Date()
        
        return (0 until timeRange.days).mapNotNull { dayOffset ->
            val date = calendar.apply {
                time = now
                add(Calendar.DAY_OF_YEAR, -dayOffset)
            }.time
            
            // 模擬癒合趨勢：面積逐漸減小
            val healingProgress = dayOffset.toDouble() / timeRange.days
            val baseArea = 10.0
            val area = baseArea * (1.0 - healingProgress * 0.3) + (Math.random() - 0.5) * 2
            val volume = (baseArea * 0.1) * (1.0 - healingProgress * 0.4) + (Math.random() - 0.5) * 0.2
            val depth = 2.0 * (1.0 - healingProgress * 0.5) + (Math.random() - 0.5) * 0.5
            
            HistoricalMeasurement(
                id = "measurement_${patientId}_$dayOffset",
                patientId = patientId,
                timestamp = date,
                area = area.coerceAtLeast(0.1),
                volume = volume.coerceAtLeast(0.01),
                averageDepth = depth.coerceAtLeast(0.1),
                woundType = "慢性傷口",
                quality = "高",
                notes = "模擬數據"
            )
        }.reversed() // 按時間正序排列
    }

    /**
     * 清理資源
     */
    fun cleanup() {
        processingScope.cancel()
    }

    // 數據類別
    data class HistoricalMeasurement(
        val id: String,
        val patientId: String,
        val timestamp: Date,
        val area: Double, // cm²
        val volume: Double, // cm³
        val averageDepth: Double, // cm
        val woundType: String,
        val quality: String,
        val notes: String
    )

    data class TrendAnalysis(
        val areaTrend: TrendData,
        val volumeTrend: TrendData,
        val depthTrend: TrendData,
        val healingProgress: HealingProgress,
        val statistics: StatisticsSummary,
        val recommendations: List<Recommendation>,
        val analysisTime: Date
    )

    data class TrendData(
        val trend: TrendType,
        val changeRate: Double, // 百分比
        val confidence: Double
    )

    enum class TrendType {
        INCREASING,
        DECREASING,
        STABLE
    }

    data class HealingProgress(
        val progress: Double, // 百分比
        val estimatedCompletionDays: Int?,
        val status: HealingStatus
    )

    enum class HealingStatus {
        NO_PROGRESS,
        MINIMAL_PROGRESS,
        SLOW_PROGRESS,
        GOOD_PROGRESS,
        ALMOST_HEALED,
        UNKNOWN
    }

    data class StatisticsSummary(
        val totalMeasurements: Int,
        val averageArea: Double,
        val averageVolume: Double,
        val averageDepth: Double,
        val maxArea: Double,
        val minArea: Double,
        val areaVariance: Double,
        val volumeVariance: Double,
        val depthVariance: Double
    )

    data class Recommendation(
        val type: RecommendationType,
        val title: String,
        val description: String,
        val priority: Priority
    )

    enum class RecommendationType {
        POSITIVE,
        WARNING,
        INFO
    }

    enum class Priority {
        LOW,
        MEDIUM,
        HIGH
    }
} 