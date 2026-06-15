package com.woundmeasurement.app.processing

import android.graphics.Bitmap
import android.graphics.RectF
import android.util.Log
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import org.opencv.android.OpenCVLoader
import org.opencv.android.Utils
import org.opencv.core.*
import org.opencv.imgproc.Imgproc
import java.util.*

class SmartROIModule {
    companion object {
        private const val TAG = "SmartROIModule"
        private const val MIN_CONFIDENCE = 0.5
        private const val MIN_ROI_SIZE = 0.1 // 最小ROI尺寸比例
    }

    // 狀態管理
    private val _detectedROI = MutableStateFlow(RectF())
    val detectedROI: StateFlow<RectF> = _detectedROI.asStateFlow()

    private val _confidence = MutableStateFlow(0.0)
    val confidence: StateFlow<Double> = _confidence.asStateFlow()

    private val _woundFeatures = MutableStateFlow<WoundFeatures?>(null)
    val woundFeatures: StateFlow<WoundFeatures?> = _woundFeatures.asStateFlow()

    // 處理佇列
    private val processingScope = CoroutineScope(Dispatchers.Default + SupervisorJob())

    init {
        // 初始化OpenCV
        if (!OpenCVLoader.initDebug()) {
            Log.e(TAG, "OpenCV初始化失敗")
        }
    }

    // 主要ROI偵測方法
    suspend fun detectWoundROI(
        image: Bitmap,
        depthData: ByteArray? = null
    ): SmartROIResult {
        return withContext(Dispatchers.Default) {
            try {
                Log.d(TAG, "開始ROI檢測，圖像尺寸: ${image.width}x${image.height}")

                // 驗證圖像
                if (!validateImageForROIDetection(image)) {
                    val issues = diagnoseImageIssues(image)
                    Log.e(TAG, "圖像驗證失敗: ${issues.joinToString()}")
                    throw SmartROIError.INVALID_IMAGE
                }

                // 第一階段：基礎矩形區域偵測
                Log.d(TAG, "執行第一階段 - 基礎矩形區域偵測")
                val rectangleResults = detectRectangularRegions(image)
                Log.d(TAG, "第一階段完成，找到 ${rectangleResults.size} 個候選區域")

                // 第二階段：深度數據優化（如果有）
                Log.d(TAG, "執行第二階段 - 深度數據優化")
                val depthEnhancedROI = enhanceROIWithDepth(rectangleResults, depthData, image.width, image.height)
                Log.d(TAG, "第二階段完成，優化後候選區域: ${depthEnhancedROI.size}")

                // 第三階段：傷口特徵篩選
                Log.d(TAG, "執行第三階段 - 傷口特徵篩選")
                val woundSpecificROI = filterForWoundCharacteristics(image, depthEnhancedROI)
                Log.d(TAG, "第三階段完成，篩選後候選區域: ${woundSpecificROI.size}")

                // 第四階段：提取最佳ROI和特徵
                val bestROI = woundSpecificROI.firstOrNull()
                if (bestROI == null) {
                    Log.w(TAG, "沒有找到有效的ROI區域，使用默認ROI")
                    val defaultROI = ROICandidate(
                        boundingBox = RectF(0.1f, 0.1f, 0.8f, 0.8f),
                        confidence = 0.5,
                        shapeScore = 0.5,
                        depthScore = 0.5
                    )
                    val defaultFeatures = extractWoundFeatures(image, defaultROI)
                    
                    return@withContext SmartROIResult(
                        roi = defaultROI.boundingBox,
                        confidence = defaultROI.confidence,
                        features = defaultFeatures,
                        processingTime = 0.0
                    )
                }

                Log.d(TAG, "執行第四階段 - 特徵提取")
                val features = extractWoundFeatures(image, bestROI)
                Log.d(TAG, "第四階段完成，特徵提取成功")

                val result = SmartROIResult(
                    roi = bestROI.boundingBox,
                    confidence = bestROI.confidence,
                    features = features,
                    processingTime = 0.0
                )

                // 更新狀態
                withContext(Dispatchers.Main) {
                    _detectedROI.value = bestROI.boundingBox
                    _confidence.value = bestROI.confidence
                    _woundFeatures.value = features
                }

                Log.d(TAG, "ROI檢測成功完成，置信度: ${bestROI.confidence}")
                result

            } catch (e: Exception) {
                Log.e(TAG, "ROI檢測失敗", e)
                throw SmartROIError.PROCESSING_FAILED
            }
        }
    }

    // 圖像驗證
    private fun validateImageForROIDetection(image: Bitmap): Boolean {
        return image.width > 100 && image.height > 100 && !image.isRecycled
    }

    // 診斷圖像問題
    private fun diagnoseImageIssues(image: Bitmap): List<String> {
        val issues = mutableListOf<String>()
        
        if (image.width <= 100 || image.height <= 100) {
            issues.add("圖像解析度過低: ${image.width}x${image.height}")
        }
        
        if (image.isRecycled) {
            issues.add("圖像已被回收")
        }
        
        return issues
    }

    // 第一階段：矩形區域偵測
    private fun detectRectangularRegions(image: Bitmap): List<ROICandidate> {
        val mat = Mat()
        Utils.bitmapToMat(image, mat)
        
        // 轉換為灰度圖
        val grayMat = Mat()
        Imgproc.cvtColor(mat, grayMat, Imgproc.COLOR_BGR2GRAY)
        
        // 高斯模糊
        val blurredMat = Mat()
        Imgproc.GaussianBlur(grayMat, blurredMat, Size(5.0, 5.0), 0.0)
        
        // 邊緣偵測
        val edgesMat = Mat()
        Imgproc.Canny(blurredMat, edgesMat, 50.0, 150.0)
        
        // 尋找輪廓
        val contours = ArrayList<MatOfPoint>()
        val hierarchy = Mat()
        Imgproc.findContours(edgesMat, contours, hierarchy, Imgproc.RETR_EXTERNAL, Imgproc.CHAIN_APPROX_SIMPLE)
        
        val candidates = mutableListOf<ROICandidate>()
        
        for (contour in contours) {
            val area = Imgproc.contourArea(contour)
            if (area < 1000) continue // 過小的輪廓忽略
            
            // 近似多邊形
            val epsilon = 0.02 * Imgproc.arcLength(contour, true)
            val approx = MatOfPoint2f()
            Imgproc.approxPolyDP(MatOfPoint2f(*contour.toArray()), approx, epsilon, true)
            
            // 檢查是否為矩形
            if (approx.total() == 4L) {
                val boundingRect = Imgproc.boundingRect(contour)
                val roiRect = RectF(
                    boundingRect.x.toFloat() / image.width,
                    boundingRect.y.toFloat() / image.height,
                    boundingRect.width.toFloat() / image.width,
                    boundingRect.height.toFloat() / image.height
                )
                
                // 檢查ROI尺寸
                if (roiRect.width() > MIN_ROI_SIZE && roiRect.height() > MIN_ROI_SIZE) {
                    val confidence = calculateShapeConfidence(contour, area)
                    candidates.add(
                        ROICandidate(
                            boundingBox = roiRect,
                            confidence = confidence,
                            shapeScore = confidence,
                            depthScore = 0.5
                        )
                    )
                }
            }
        }
        
        // 清理資源
        mat.release()
        grayMat.release()
        blurredMat.release()
        edgesMat.release()
        hierarchy.release()
        
        return candidates.sortedByDescending { it.confidence }
    }

    // 第二階段：深度數據優化
    private fun enhanceROIWithDepth(
        candidates: List<ROICandidate>,
        depthData: ByteArray?,
        imageWidth: Int,
        imageHeight: Int
    ): List<ROICandidate> {
        if (depthData == null) {
            Log.d(TAG, "無深度數據，跳過深度優化")
            return candidates
        }
        
        return candidates.map { candidate ->
            val depthScore = calculateDepthScore(candidate.boundingBox, depthData, imageWidth, imageHeight)
            candidate.copy(depthScore = depthScore)
        }.sortedByDescending { it.confidence * it.depthScore }
    }

    // 第三階段：傷口特徵篩選
    private fun filterForWoundCharacteristics(
        image: Bitmap,
        candidates: List<ROICandidate>
    ): List<ROICandidate> {
        return candidates.filter { candidate ->
            val woundScore = calculateWoundScore(image, candidate.boundingBox)
            candidate.confidence * woundScore > MIN_CONFIDENCE
        }.map { candidate ->
            val woundScore = calculateWoundScore(image, candidate.boundingBox)
            candidate.copy(confidence = candidate.confidence * woundScore)
        }.sortedByDescending { it.confidence }
    }

    // 第四階段：特徵提取
    private fun extractWoundFeatures(image: Bitmap, roi: ROICandidate): WoundFeatures {
        val mat = Mat()
        Utils.bitmapToMat(image, mat)
        
        // 提取ROI區域
        val roiRect = Rect(
            (roi.boundingBox.left * image.width).toInt(),
            (roi.boundingBox.top * image.height).toInt(),
            (roi.boundingBox.width() * image.width).toInt(),
            (roi.boundingBox.height() * image.height).toInt()
        )
        
        val roiMat = Mat(mat, roiRect)
        
        // 計算特徵
        val colorFeatures = calculateColorFeatures(roiMat)
        val textureFeatures = calculateTextureFeatures(roiMat)
        val shapeFeatures = calculateShapeFeatures(roiMat)
        
        mat.release()
        roiMat.release()
        
        return WoundFeatures(
            colorFeatures = colorFeatures,
            textureFeatures = textureFeatures,
            shapeFeatures = shapeFeatures,
            extractionTime = Date()
        )
    }

    // 輔助方法
    private fun calculateShapeConfidence(contour: MatOfPoint, area: Double): Double {
        val perimeter = Imgproc.arcLength(contour, true)
        val circularity = 4 * Math.PI * area / (perimeter * perimeter)
        return circularity.coerceIn(0.0, 1.0)
    }

    private fun calculateDepthScore(roi: RectF, depthData: ByteArray, width: Int, height: Int): Double {
        // 簡化的深度評分
        // 實際實作中應分析深度數據的變化
        return 0.7
    }

    private fun calculateWoundScore(image: Bitmap, roi: RectF): Double {
        // 簡化的傷口評分
        // 實際實作中應使用ML模型
        return 0.8
    }

    private fun calculateColorFeatures(roiMat: Mat): ColorFeatures {
        val mean = Core.mean(roiMat)
        return ColorFeatures(
            meanRed = mean.`val`[2],
            meanGreen = mean.`val`[1],
            meanBlue = mean.`val`[0],
            colorVariance = 0.5
        )
    }

    private fun calculateTextureFeatures(roiMat: Mat): TextureFeatures {
        // 簡化的紋理特徵計算
        return TextureFeatures(
            textureComplexity = 0.6,
            edgeDensity = 0.4,
            smoothness = 0.3
        )
    }

    private fun calculateShapeFeatures(roiMat: Mat): ShapeFeatures {
        // 簡化的形狀特徵計算
        return ShapeFeatures(
            aspectRatio = roiMat.width().toFloat() / roiMat.height(),
            compactness = 0.7,
            irregularity = 0.3
        )
    }

    // 數據類別
    data class SmartROIResult(
        val roi: RectF,
        val confidence: Double,
        val features: WoundFeatures,
        val processingTime: Double
    )

    data class ROICandidate(
        val boundingBox: RectF,
        val confidence: Double,
        val shapeScore: Double,
        val depthScore: Double
    )

    data class WoundFeatures(
        val colorFeatures: ColorFeatures,
        val textureFeatures: TextureFeatures,
        val shapeFeatures: ShapeFeatures,
        val extractionTime: Date
    )

    data class ColorFeatures(
        val meanRed: Double,
        val meanGreen: Double,
        val meanBlue: Double,
        val colorVariance: Double
    )

    data class TextureFeatures(
        val textureComplexity: Double,
        val edgeDensity: Double,
        val smoothness: Double
    )

    data class ShapeFeatures(
        val aspectRatio: Float,
        val compactness: Double,
        val irregularity: Double
    )

    enum class SmartROIError : Exception() {
        INVALID_IMAGE,
        PROCESSING_FAILED
    }

    fun cleanup() {
        processingScope.cancel()
    }
} 