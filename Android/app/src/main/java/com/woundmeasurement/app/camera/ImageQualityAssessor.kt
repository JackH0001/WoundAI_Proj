package com.woundmeasurement.app.camera

import android.graphics.Bitmap
import android.graphics.Color
import android.util.Log
import kotlin.math.*

/**
 * 圖像品質評估器
 * 提供多維度的圖像品質分析
 */
class ImageQualityAssessor {
    
    companion object {
        private const val TAG = "ImageQualityAssessor"
        
        // 品質閾值設定
        private const val MIN_SHARPNESS_SCORE = 50.0
        private const val MIN_BRIGHTNESS_SCORE = 60.0
        private const val MIN_CONTRAST_SCORE = 40.0
        private const val MIN_OVERALL_SCORE = 70.0
    }

    /**
     * 評估圖像整體品質
     */
    fun assessImageQuality(bitmap: Bitmap): ImageQualityScore {
        Log.d(TAG, "開始評估圖像品質，尺寸: ${bitmap.width}x${bitmap.height}")
        
        val startTime = System.currentTimeMillis()
        
        // 並行計算各項品質指標
        val sharpnessScore = calculateSharpness(bitmap)
        val brightnessScore = calculateBrightness(bitmap)
        val contrastScore = calculateContrast(bitmap)
        val noiseLevel = calculateNoiseLevel(bitmap)
        val colorBalanceScore = calculateColorBalance(bitmap)
        val exposureScore = calculateExposure(bitmap)
        
        // 計算綜合品質分數
        val overallScore = calculateOverallScore(
            sharpnessScore, brightnessScore, contrastScore, 
            noiseLevel, colorBalanceScore, exposureScore
        )
        
        val processingTime = System.currentTimeMillis() - startTime
        
        val qualityScore = ImageQualityScore(
            overallScore = overallScore,
            sharpnessScore = sharpnessScore,
            brightnessScore = brightnessScore,
            contrastScore = contrastScore,
            noiseLevel = noiseLevel,
            colorBalanceScore = colorBalanceScore,
            exposureScore = exposureScore,
            isAcceptable = overallScore >= MIN_OVERALL_SCORE,
            processingTimeMs = processingTime
        )
        
        Log.d(TAG, "品質評估完成: $qualityScore")
        return qualityScore
    }

    /**
     * 計算圖像清晰度 - 使用 Laplacian 變異數
     */
    private fun calculateSharpness(bitmap: Bitmap): Double {
        val width = bitmap.width
        val height = bitmap.height
        val pixels = IntArray(width * height)
        bitmap.getPixels(pixels, 0, width, 0, 0, width, height)
        
        // 轉換為灰度並計算 Laplacian 變異數
        var sum = 0.0
        var sumSquared = 0.0
        var count = 0
        
        for (y in 1 until height - 1) {
            for (x in 1 until width - 1) {
                val center = getGrayValue(pixels[y * width + x])
                val left = getGrayValue(pixels[y * width + x - 1])
                val right = getGrayValue(pixels[y * width + x + 1])
                val top = getGrayValue(pixels[(y - 1) * width + x])
                val bottom = getGrayValue(pixels[(y + 1) * width + x])
                
                // Laplacian 運算子
                val laplacian = abs(4 * center - left - right - top - bottom)
                
                sum += laplacian
                sumSquared += laplacian * laplacian
                count++
            }
        }
        
        val mean = sum / count
        val variance = (sumSquared / count) - (mean * mean)
        
        // 正規化為 0-100 分數
        val sharpnessScore = min(100.0, variance / 100.0)
        
        Log.d(TAG, "清晰度分數: $sharpnessScore (變異數: $variance)")
        return sharpnessScore
    }

    /**
     * 計算圖像亮度
     */
    private fun calculateBrightness(bitmap: Bitmap): Double {
        val width = bitmap.width
        val height = bitmap.height
        val pixels = IntArray(width * height)
        bitmap.getPixels(pixels, 0, width, 0, 0, width, height)
        
        var totalBrightness = 0.0
        
        for (pixel in pixels) {
            val gray = getGrayValue(pixel)
            totalBrightness += gray
        }
        
        val averageBrightness = totalBrightness / pixels.size
        
        // 理想亮度範圍 100-180 (0-255)
        val brightnessScore = when {
            averageBrightness < 80 -> max(0.0, averageBrightness / 80.0 * 60.0) // 過暗
            averageBrightness > 200 -> max(0.0, 100.0 - ((averageBrightness - 200.0) / 55.0 * 40.0)) // 過亮
            else -> 100.0 // 理想範圍
        }
        
        Log.d(TAG, "亮度分數: $brightnessScore (平均亮度: $averageBrightness)")
        return brightnessScore
    }

    /**
     * 計算圖像對比度
     */
    private fun calculateContrast(bitmap: Bitmap): Double {
        val width = bitmap.width
        val height = bitmap.height
        val pixels = IntArray(width * height)
        bitmap.getPixels(pixels, 0, width, 0, 0, width, height)
        
        var sum = 0.0
        var sumSquared = 0.0
        
        for (pixel in pixels) {
            val gray = getGrayValue(pixel).toDouble()
            sum += gray
            sumSquared += gray * gray
        }
        
        val mean = sum / pixels.size
        val variance = (sumSquared / pixels.size) - (mean * mean)
        val standardDeviation = sqrt(variance)
        
        // 理想對比度範圍 30-80
        val contrastScore = when {
            standardDeviation < 20 -> max(0.0, standardDeviation / 20.0 * 50.0) // 對比度不足
            standardDeviation > 100 -> max(0.0, 100.0 - ((standardDeviation - 100.0) / 55.0 * 40.0)) // 對比度過高
            else -> 100.0 // 理想範圍
        }
        
        Log.d(TAG, "對比度分數: $contrastScore (標準差: $standardDeviation)")
        return contrastScore
    }

    /**
     * 計算雜訊水平
     */
    private fun calculateNoiseLevel(bitmap: Bitmap): Double {
        val width = bitmap.width
        val height = bitmap.height
        val pixels = IntArray(width * height)
        bitmap.getPixels(pixels, 0, width, 0, 0, width, height)
        
        // 使用局部標準差方法估計雜訊
        var totalNoise = 0.0
        var count = 0
        
        val windowSize = 5
        val halfWindow = windowSize / 2
        
        for (y in halfWindow until height - halfWindow) {
            for (x in halfWindow until width - halfWindow) {
                var windowSum = 0.0
                var windowSumSquared = 0.0
                var windowCount = 0
                
                // 計算 5x5 視窗內的標準差
                for (dy in -halfWindow..halfWindow) {
                    for (dx in -halfWindow..halfWindow) {
                        val gray = getGrayValue(pixels[(y + dy) * width + (x + dx)]).toDouble()
                        windowSum += gray
                        windowSumSquared += gray * gray
                        windowCount++
                    }
                }
                
                val windowMean = windowSum / windowCount
                val windowVariance = (windowSumSquared / windowCount) - (windowMean * windowMean)
                val windowStdDev = sqrt(windowVariance)
                
                totalNoise += windowStdDev
                count++
            }
        }
        
        val averageNoise = totalNoise / count
        
        // 雜訊水平越低越好，返回 0-1 之間的值
        val normalizedNoise = min(1.0, averageNoise / 50.0)
        
        Log.d(TAG, "雜訊水平: $normalizedNoise (平均雜訊: $averageNoise)")
        return normalizedNoise
    }

    /**
     * 計算色彩平衡
     */
    private fun calculateColorBalance(bitmap: Bitmap): Double {
        val width = bitmap.width
        val height = bitmap.height
        val pixels = IntArray(width * height)
        bitmap.getPixels(pixels, 0, width, 0, 0, width, height)
        
        var redSum = 0L
        var greenSum = 0L
        var blueSum = 0L
        
        for (pixel in pixels) {
            redSum += Color.red(pixel)
            greenSum += Color.green(pixel)
            blueSum += Color.blue(pixel)
        }
        
        val redMean = redSum.toDouble() / pixels.size
        val greenMean = greenSum.toDouble() / pixels.size
        val blueMean = blueSum.toDouble() / pixels.size
        
        // 計算通道間的偏差
        val overallMean = (redMean + greenMean + blueMean) / 3.0
        val redDeviation = abs(redMean - overallMean) / overallMean
        val greenDeviation = abs(greenMean - overallMean) / overallMean
        val blueDeviation = abs(blueMean - overallMean) / overallMean
        
        val maxDeviation = maxOf(redDeviation, greenDeviation, blueDeviation)
        
        // 偏差越小色彩平衡越好
        val colorBalanceScore = max(0.0, 100.0 - (maxDeviation * 200.0))
        
        Log.d(TAG, "色彩平衡分數: $colorBalanceScore (最大偏差: $maxDeviation)")
        return colorBalanceScore
    }

    /**
     * 計算曝光品質
     */
    private fun calculateExposure(bitmap: Bitmap): Double {
        val width = bitmap.width
        val height = bitmap.height
        val pixels = IntArray(width * height)
        bitmap.getPixels(pixels, 0, width, 0, 0, width, height)
        
        var underexposed = 0
        var overexposed = 0
        
        for (pixel in pixels) {
            val gray = getGrayValue(pixel)
            when {
                gray < 30 -> underexposed++ // 過暗
                gray > 225 -> overexposed++ // 過亮
            }
        }
        
        val underexposedRatio = underexposed.toDouble() / pixels.size
        val overexposedRatio = overexposed.toDouble() / pixels.size
        
        // 理想情況下過曝和欠曝比例都應該很低
        val exposureScore = max(0.0, 100.0 - (underexposedRatio * 150.0 + overexposedRatio * 150.0))
        
        Log.d(TAG, "曝光分數: $exposureScore (欠曝: ${underexposedRatio * 100}%, 過曝: ${overexposedRatio * 100}%)")
        return exposureScore
    }

    /**
     * 計算綜合品質分數
     */
    private fun calculateOverallScore(
        sharpness: Double,
        brightness: Double,
        contrast: Double,
        noiseLevel: Double,
        colorBalance: Double,
        exposure: Double
    ): Double {
        // 加權計算
        val weights = mapOf(
            "sharpness" to 0.30,
            "brightness" to 0.20,
            "contrast" to 0.20,
            "noise" to 0.15,
            "colorBalance" to 0.10,
            "exposure" to 0.05
        )
        
        val overallScore = sharpness * weights["sharpness"]!! +
                brightness * weights["brightness"]!! +
                contrast * weights["contrast"]!! +
                (100.0 - noiseLevel * 100.0) * weights["noise"]!! +
                colorBalance * weights["colorBalance"]!! +
                exposure * weights["exposure"]!!
        
        return min(100.0, max(0.0, overallScore))
    }

    /**
     * 獲取像素的灰度值
     */
    private fun getGrayValue(pixel: Int): Int {
        val red = Color.red(pixel)
        val green = Color.green(pixel)
        val blue = Color.blue(pixel)
        return (0.299 * red + 0.587 * green + 0.114 * blue).toInt()
    }

    /**
     * 檢查圖像是否符合醫療影像要求
     */
    fun isMedicalQuality(qualityScore: ImageQualityScore): Boolean {
        return qualityScore.sharpnessScore >= MIN_SHARPNESS_SCORE &&
                qualityScore.brightnessScore >= MIN_BRIGHTNESS_SCORE &&
                qualityScore.contrastScore >= MIN_CONTRAST_SCORE &&
                qualityScore.overallScore >= MIN_OVERALL_SCORE
    }

    /**
     * 提供品質改善建議
     */
    fun getQualityRecommendations(qualityScore: ImageQualityScore): List<String> {
        val recommendations = mutableListOf<String>()
        
        if (qualityScore.sharpnessScore < MIN_SHARPNESS_SCORE) {
            recommendations.add("圖像模糊，請確保相機對焦正確並保持穩定")
        }
        
        if (qualityScore.brightnessScore < MIN_BRIGHTNESS_SCORE) {
            recommendations.add("亮度不足，請增加光源或調整拍攝環境")
        }
        
        if (qualityScore.contrastScore < MIN_CONTRAST_SCORE) {
            recommendations.add("對比度不足，請調整光線條件")
        }
        
        if (qualityScore.noiseLevel > 0.3) {
            recommendations.add("圖像雜訊較高，請在光線充足的環境下拍攝")
        }
        
        if (qualityScore.colorBalanceScore < 70.0) {
            recommendations.add("色彩平衡有問題，請檢查白平衡設定")
        }
        
        if (qualityScore.exposureScore < 70.0) {
            recommendations.add("曝光不當，請調整拍攝角度避免過曝或欠曝")
        }
        
        return recommendations
    }
}

/**
 * 圖像品質分數資料類
 */
data class ImageQualityScore(
    val overallScore: Double,           // 綜合品質分數 (0-100)
    val sharpnessScore: Double,         // 清晰度分數 (0-100)
    val brightnessScore: Double,        // 亮度分數 (0-100)
    val contrastScore: Double,          // 對比度分數 (0-100)
    val noiseLevel: Double,             // 雜訊水平 (0-1, 越低越好)
    val colorBalanceScore: Double,      // 色彩平衡分數 (0-100)
    val exposureScore: Double,          // 曝光分數 (0-100)
    val isAcceptable: Boolean,          // 是否符合醫療品質要求
    val processingTimeMs: Long          // 處理時間(毫秒)
) {
    override fun toString(): String {
        return "品質分數[總分:%.1f, 清晰:%.1f, 亮度:%.1f, 對比:%.1f, 雜訊:%.3f, 色彩:%.1f, 曝光:%.1f, 合格:%s]".format(
            overallScore, sharpnessScore, brightnessScore, contrastScore, 
            noiseLevel, colorBalanceScore, exposureScore, isAcceptable
        )
    }
}