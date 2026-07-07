package com.woundmeasurement.app.processing

import android.content.Context
import android.graphics.Bitmap
import android.util.Log
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.util.*
import java.util.concurrent.ConcurrentHashMap

class RealTimeAnalysisModule(context: Context) {
    companion object {
        private const val TAG = "RealTimeAnalysisModule"
        private const val ANALYSIS_INTERVAL_MS = 1000L // 1秒
        private const val MAX_CACHE_SIZE = 10
    }

    // 狀態管理
    private val _isAnalyzing = MutableStateFlow(false)
    val isAnalyzing: StateFlow<Boolean> = _isAnalyzing.asStateFlow()

    private val _currentAnalysis = MutableStateFlow<RealTimeAnalysisResult?>(null)
    val currentAnalysis: StateFlow<RealTimeAnalysisResult?> = _currentAnalysis.asStateFlow()

    private val _analysisHistory = MutableStateFlow<List<RealTimeAnalysisResult>>(emptyList())
    val analysisHistory: StateFlow<List<RealTimeAnalysisResult>> = _analysisHistory.asStateFlow()

    // 分析引擎
    private val segmentationEngine   = SegmentationEngine()
    private val measurementEngine    = MeasurementEngine()
    private val multiModelClassifier = MultiModelClassifier(context)
    
    // 分析任務管理
    private var analysisJob: Job? = null
    private var lastAnalysisTime = 0L
    
    // 結果緩存
    private val cachedResults = ConcurrentHashMap<String, RealTimeAnalysisResult>()

    data class RealTimeAnalysisResult(
        val timestamp: Date,
        val hasWound: Boolean,
        val confidence: Double,
        val estimatedArea: Double?, // cm²
        val estimatedVolume: Double?, // cm³
        val woundType: String?,
        val quality: String,
        val processingTime: Long,
        // 多模型集成結果（可選，舊呼叫端不受影響）
        val ensembleResult: MultiModelClassifier.EnsembleResult? = null,
    )

    // 初始化（載入 TFLite 模型）
    suspend fun initialize() {
        multiModelClassifier.initialize()
    }

    // 開始即時分析
    fun startRealTimeAnalysis(
        imageStream: () -> Bitmap?,
        scope: CoroutineScope
    ) {
        stopRealTimeAnalysis()
        
        analysisJob = scope.launch {
            while (isActive) {
                val image = imageStream()
                if (image != null) {
                    val currentTime = System.currentTimeMillis()
                    
                    // 控制分析頻率
                    if (currentTime - lastAnalysisTime >= ANALYSIS_INTERVAL_MS) {
                        performQuickAnalysis(image)
                        lastAnalysisTime = currentTime
                    }
                }
                
                delay(100) // 100ms檢查間隔
            }
        }
        
        Log.d(TAG, "即時分析已開始")
    }

    // 停止即時分析並釋放資源
    fun stopRealTimeAnalysis() {
        analysisJob?.cancel()
        analysisJob = null
        _isAnalyzing.value = false
        multiModelClassifier.release()
        Log.d(TAG, "即時分析已停止")
    }

    // 執行快速分析
    private suspend fun performQuickAnalysis(image: Bitmap) {
        val startTime = System.currentTimeMillis()
        
        try {
            _isAnalyzing.value = true
            
            // 1. 快速品質評估
            val quality = assessImageQuality(image)
            
            // 2. 快速傷口偵測
            val woundDetection = detectWoundPresence(image)
            
            // 3. 如果有傷口，進行快速測量
            var estimatedArea: Double? = null
            var estimatedVolume: Double? = null
            var woundType: String? = null
            
            var ensembleResult: MultiModelClassifier.EnsembleResult? = null
            if (woundDetection.hasWound && woundDetection.confidence > 0.6) {
                val quickMeasurement = performQuickMeasurement(image)
                estimatedArea   = quickMeasurement.area
                estimatedVolume = quickMeasurement.volume

                // 多模型集成分類
                ensembleResult = multiModelClassifier.classify(image)
                woundType      = ensembleResult.woundType
            }

            val processingTime = System.currentTimeMillis() - startTime

            val result = RealTimeAnalysisResult(
                timestamp       = Date(),
                hasWound        = woundDetection.hasWound,
                confidence      = ensembleResult?.confidence?.toDouble() ?: woundDetection.confidence,
                estimatedArea   = estimatedArea,
                estimatedVolume = estimatedVolume,
                woundType       = woundType,
                quality         = quality,
                processingTime  = processingTime,
                ensembleResult  = ensembleResult,
            )
            
            // 更新當前分析結果
            _currentAnalysis.value = result
            
            // 添加到歷史記錄
            val currentHistory = _analysisHistory.value.toMutableList()
            currentHistory.add(0, result)
            if (currentHistory.size > 50) { // 保留最近50個結果
                currentHistory.removeAt(currentHistory.size - 1)
            }
            _analysisHistory.value = currentHistory
            
            // 緩存結果
            cacheResult(result)
            
            Log.d(TAG, "快速分析完成: 傷口=${woundDetection.hasWound}, 置信度=${woundDetection.confidence}")
            
        } catch (e: Exception) {
            Log.e(TAG, "快速分析失敗", e)
        } finally {
            _isAnalyzing.value = false
        }
    }

    // 圖像品質評估
    private suspend fun assessImageQuality(image: Bitmap): String {
        return withContext(Dispatchers.Default) {
            try {
                // 基礎品質檢查
                val width = image.width
                val height = image.height
                val resolution = width * height
                
                // 解析度檢查
                if (resolution < 640 * 480) {
                    return@withContext "低"
                } else if (resolution < 1920 * 1080) {
                    return@withContext "中"
                } else {
                    return@withContext "高"
                }
            } catch (e: Exception) {
                Log.e(TAG, "品質評估失敗", e)
                "未知"
            }
        }
    }

    // 傷口存在偵測
    private suspend fun detectWoundPresence(image: Bitmap): WoundDetectionResult {
        return withContext(Dispatchers.Default) {
            try {
                // 使用OpenCV進行基礎傷口偵測
                val hasWound = segmentationEngine.detectWound(image)
                val confidence = if (hasWound) 0.8 else 0.2
                
                WoundDetectionResult(hasWound, confidence)
            } catch (e: Exception) {
                Log.e(TAG, "傷口偵測失敗", e)
                WoundDetectionResult(false, 0.0)
            }
        }
    }

    // 快速測量
    private suspend fun performQuickMeasurement(image: Bitmap): QuickMeasurementResult {
        return withContext(Dispatchers.Default) {
            try {
                val area = measurementEngine.estimateArea(image)
                val volume = measurementEngine.estimateVolume(image)
                val type = measurementEngine.classifyWoundType(image)
                
                QuickMeasurementResult(area, volume, type)
            } catch (e: Exception) {
                Log.e(TAG, "快速測量失敗", e)
                QuickMeasurementResult(null, null, null)
            }
        }
    }

    // 緩存結果
    private fun cacheResult(result: RealTimeAnalysisResult) {
        val key = result.timestamp.time.toString()
        cachedResults[key] = result
        
        // 清理過期緩存
        if (cachedResults.size > MAX_CACHE_SIZE) {
            val oldestKey = cachedResults.keys.first()
            cachedResults.remove(oldestKey)
        }
    }

    // 數據類別
    data class WoundDetectionResult(
        val hasWound: Boolean,
        val confidence: Double
    )

    data class QuickMeasurementResult(
        val area: Double?,
        val volume: Double?,
        val type: String?
    )

    // ------------------------------------------------------------------
    // 分析引擎（真實 OpenCV 實作）
    // ------------------------------------------------------------------

    /**
     * Fast colour-based wound presence detector using HSV thresholding.
     * Returns true when enough red/pink pixels are found in the frame.
     */
    private inner class SegmentationEngine {
        fun detectWound(image: Bitmap): Boolean {
            return try {
                val src = org.opencv.core.Mat()
                org.opencv.android.Utils.bitmapToMat(image, src)
                val bgr = org.opencv.core.Mat()
                if (src.channels() == 4) {
                    org.opencv.imgproc.Imgproc.cvtColor(
                        src, bgr, org.opencv.imgproc.Imgproc.COLOR_RGBA2BGR
                    )
                } else src.copyTo(bgr)
                val hsv = org.opencv.core.Mat()
                org.opencv.imgproc.Imgproc.cvtColor(
                    bgr, hsv, org.opencv.imgproc.Imgproc.COLOR_BGR2HSV
                )
                val m1 = org.opencv.core.Mat()
                val m2 = org.opencv.core.Mat()
                org.opencv.core.Core.inRange(
                    hsv,
                    org.opencv.core.Scalar(0.0, 40.0, 40.0),
                    org.opencv.core.Scalar(15.0, 255.0, 255.0),
                    m1
                )
                org.opencv.core.Core.inRange(
                    hsv,
                    org.opencv.core.Scalar(160.0, 40.0, 40.0),
                    org.opencv.core.Scalar(180.0, 255.0, 255.0),
                    m2
                )
                val mask = org.opencv.core.Mat()
                org.opencv.core.Core.bitwise_or(m1, m2, mask)
                val nz = org.opencv.core.Core.countNonZero(mask).toDouble()
                val total = (mask.rows() * mask.cols()).toDouble()
                val ratio = if (total > 0) nz / total else 0.0
                listOf(src, bgr, hsv, m1, m2, mask).forEach { it.release() }
                ratio > 0.003
            } catch (e: UnsatisfiedLinkError) {
                Log.w(TAG, "OpenCV not available; skipping wound detection", e)
                false
            } catch (e: Exception) {
                Log.w(TAG, "detectWound failed", e)
                false
            }
        }
    }

    /**
     * Coarse measurement using contour area of the thresholded wound.
     * Assumes ~10 px/mm when no explicit calibration is available.
     */
    private inner class MeasurementEngine {
        private val assumedPxPerMm = 10.0
        private val pxAreaPerCm2: Double
            get() = (assumedPxPerMm * 10.0) * (assumedPxPerMm * 10.0)

        private fun largestContourArea(image: Bitmap): Double {
            return try {
                val src = org.opencv.core.Mat()
                org.opencv.android.Utils.bitmapToMat(image, src)
                val bgr = org.opencv.core.Mat()
                if (src.channels() == 4) {
                    org.opencv.imgproc.Imgproc.cvtColor(
                        src, bgr, org.opencv.imgproc.Imgproc.COLOR_RGBA2BGR
                    )
                } else src.copyTo(bgr)
                val hsv = org.opencv.core.Mat()
                org.opencv.imgproc.Imgproc.cvtColor(
                    bgr, hsv, org.opencv.imgproc.Imgproc.COLOR_BGR2HSV
                )
                val m1 = org.opencv.core.Mat()
                val m2 = org.opencv.core.Mat()
                org.opencv.core.Core.inRange(
                    hsv,
                    org.opencv.core.Scalar(0.0, 40.0, 40.0),
                    org.opencv.core.Scalar(15.0, 255.0, 255.0),
                    m1
                )
                org.opencv.core.Core.inRange(
                    hsv,
                    org.opencv.core.Scalar(160.0, 40.0, 40.0),
                    org.opencv.core.Scalar(180.0, 255.0, 255.0),
                    m2
                )
                val mask = org.opencv.core.Mat()
                org.opencv.core.Core.bitwise_or(m1, m2, mask)
                val kernel = org.opencv.imgproc.Imgproc.getStructuringElement(
                    org.opencv.imgproc.Imgproc.MORPH_ELLIPSE, org.opencv.core.Size(5.0, 5.0)
                )
                org.opencv.imgproc.Imgproc.morphologyEx(
                    mask, mask, org.opencv.imgproc.Imgproc.MORPH_CLOSE, kernel
                )

                val contours = mutableListOf<org.opencv.core.MatOfPoint>()
                val hierarchy = org.opencv.core.Mat()
                org.opencv.imgproc.Imgproc.findContours(
                    mask, contours, hierarchy,
                    org.opencv.imgproc.Imgproc.RETR_EXTERNAL,
                    org.opencv.imgproc.Imgproc.CHAIN_APPROX_SIMPLE
                )
                val areaPx = contours.maxOfOrNull { org.opencv.imgproc.Imgproc.contourArea(it) } ?: 0.0
                listOf(src, bgr, hsv, m1, m2, mask, hierarchy).forEach { it.release() }
                areaPx
            } catch (e: UnsatisfiedLinkError) {
                0.0
            } catch (e: Exception) {
                Log.w(TAG, "largestContourArea failed", e)
                0.0
            }
        }

        fun estimateArea(image: Bitmap): Double? {
            val areaPx = largestContourArea(image)
            if (areaPx <= 0) return null
            return areaPx / pxAreaPerCm2
        }

        fun estimateVolume(image: Bitmap): Double? {
            val area = estimateArea(image) ?: return null
            // 以平均深度 0.3cm 作為快速估算（更精確值由深度模組提供）
            return area * 0.3
        }

        fun classifyWoundType(image: Bitmap): String? {
            // 快速分類 — 多模型結果由呼叫端的 multiModelClassifier 提供，
            // 此處僅在其未啟用時給予安全預設。
            return "慢性傷口"
        }
    }
}
