package com.woundmeasurement.app.calibration

import android.graphics.Bitmap
import android.util.Log
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.opencv.android.Utils
import org.opencv.core.*
import org.opencv.imgproc.Imgproc
import kotlin.math.*

class RulerCalibrationModule {

    companion object {
        private const val TAG = "RulerCalibration"
        private const val RULER_LENGTH_CM = 15.0 // 15cm白色直尺
        private const val MIN_LINE_LENGTH = 50
        private const val MAX_LINE_GAP = 10
        private const val CANNY_LOW_THRESHOLD = 50.0
        private const val CANNY_HIGH_THRESHOLD = 150.0
        private const val HOUGH_THRESHOLD = 80
        private const val PARALLEL_ANGLE_TOLERANCE = 10.0 // degrees
        private const val SCALE_MARK_MIN_LENGTH = 5
        private const val SCALE_MARK_MAX_LENGTH = 40
    }

    var isCalibrating = false
        private set

    var calibrationResult: CalibrationResult? = null
        private set

    suspend fun detectAndCalibrateRuler(bitmap: Bitmap): CalibrationResult = withContext(Dispatchers.Default) {
        isCalibrating = true

        try {
            Log.d(TAG, "開始標尺校正，尺規長度: ${RULER_LENGTH_CM}cm")

            // Convert Bitmap to OpenCV Mat
            val src = Mat()
            Utils.bitmapToMat(bitmap, src)

            // Convert to grayscale
            val gray = Mat()
            Imgproc.cvtColor(src, gray, Imgproc.COLOR_RGBA2GRAY)

            // Apply GaussianBlur for noise reduction
            val blurred = Mat()
            Imgproc.GaussianBlur(gray, blurred, Size(5.0, 5.0), 0.0)

            // Apply Canny edge detection
            val edges = Mat()
            Imgproc.Canny(blurred, edges, CANNY_LOW_THRESHOLD, CANNY_HIGH_THRESHOLD)

            // Use HoughLinesP to detect ruler lines
            val lines = Mat()
            Imgproc.HoughLinesP(
                edges, lines, 1.0, Math.PI / 180.0,
                HOUGH_THRESHOLD, MIN_LINE_LENGTH.toDouble(), MAX_LINE_GAP.toDouble()
            )

            // Convert detected lines to LineSegment objects
            val allSegments = mutableListOf<LineSegment>()
            for (i in 0 until lines.rows()) {
                val data = lines[i, 0]
                val x1 = data[0]; val y1 = data[1]
                val x2 = data[2]; val y2 = data[3]
                val length = sqrt((x2 - x1).pow(2) + (y2 - y1).pow(2))
                allSegments.add(LineSegment(x1, y1, x2, y2, length))
            }

            // Find ruler edges and scale marks
            val rulerEdges = detectRulerEdges(allSegments, Size(src.cols().toDouble(), src.rows().toDouble()))
            val scaleLines = detectScaleLines(edges, rulerEdges)
            val pixelScale = calculatePixelScale(scaleLines, rulerEdges)
            val confidence = calculateConfidence(rulerEdges, scaleLines)

            // Release Mats
            src.release()
            gray.release()
            blurred.release()
            edges.release()
            lines.release()

            val result = CalibrationResult(
                pixelPerMM = pixelScale,
                rulerLengthCM = RULER_LENGTH_CM,
                confidence = confidence,
                detectedEdges = rulerEdges.horizontalLines + rulerEdges.verticalLines,
                detectedScales = scaleLines,
                rulerType = RulerType.WHITE_STRAIGHT_15CM
            )

            calibrationResult = result
            Log.d(TAG, "校正完成 - 像素比例: $pixelScale px/mm, 信心度: $confidence")

            result
        } catch (e: UnsatisfiedLinkError) {
            Log.e(TAG, "OpenCV native library not loaded, falling back to defaults", e)
            val fallback = CalibrationResult(
                pixelPerMM = 10.0,
                rulerLengthCM = RULER_LENGTH_CM,
                confidence = 0.0,
                detectedEdges = emptyList(),
                detectedScales = emptyList(),
                rulerType = RulerType.WHITE_STRAIGHT_15CM
            )
            calibrationResult = fallback
            fallback
        } catch (e: Exception) {
            Log.e(TAG, "標尺校正失敗", e)
            throw CalibrationException("標尺校正失敗: ${e.message}", e)
        } finally {
            isCalibrating = false
        }
    }

    /**
     * Find the two longest parallel lines as the ruler edges.
     * Lines are grouped by angle (horizontal vs vertical), then the two longest
     * parallel lines with sufficient separation are selected.
     */
    private fun detectRulerEdges(segments: List<LineSegment>, imageSize: Size): RulerEdges {
        if (segments.isEmpty()) return RulerEdges(emptyList(), emptyList())

        // Classify lines by angle
        val horizontalLines = mutableListOf<LineSegment>()
        val verticalLines = mutableListOf<LineSegment>()

        for (seg in segments) {
            val angle = Math.toDegrees(atan2(seg.endY - seg.startY, seg.endX - seg.startX)).let {
                if (it < 0) it + 180.0 else it
            }
            when {
                angle < PARALLEL_ANGLE_TOLERANCE || angle > 180.0 - PARALLEL_ANGLE_TOLERANCE ->
                    horizontalLines.add(seg)
                abs(angle - 90.0) < PARALLEL_ANGLE_TOLERANCE ->
                    verticalLines.add(seg)
            }
        }

        // Pick the dominant orientation and find the two best parallel edges
        val dominant = if (horizontalLines.sumOf { it.length } >= verticalLines.sumOf { it.length })
            horizontalLines else verticalLines
        val isHorizontal = dominant === horizontalLines

        // Sort by length descending and pick the two longest that are sufficiently separated
        val sorted = dominant.sortedByDescending { it.length }
        val bestPair = mutableListOf<LineSegment>()
        if (sorted.isNotEmpty()) {
            bestPair.add(sorted[0])
            val minSeparation = imageSize.let { if (isHorizontal) it.height * 0.02 else it.width * 0.02 }
            for (i in 1 until sorted.size) {
                val sep = if (isHorizontal) {
                    abs((sorted[i].startY + sorted[i].endY) / 2.0 - (bestPair[0].startY + bestPair[0].endY) / 2.0)
                } else {
                    abs((sorted[i].startX + sorted[i].endX) / 2.0 - (bestPair[0].startX + bestPair[0].endX) / 2.0)
                }
                if (sep > minSeparation) {
                    bestPair.add(sorted[i])
                    break
                }
            }
        }

        return if (isHorizontal) {
            RulerEdges(horizontalLines = bestPair, verticalLines = emptyList())
        } else {
            RulerEdges(horizontalLines = emptyList(), verticalLines = bestPair)
        }
    }

    /**
     * Detect scale marks between the ruler edges by running a secondary
     * HoughLinesP pass looking for short perpendicular lines in the ruler region.
     */
    private fun detectScaleLines(edges: Mat, rulerEdges: RulerEdges): List<ScaleLine> {
        val edgeLines = rulerEdges.horizontalLines + rulerEdges.verticalLines
        if (edgeLines.size < 2) return emptyList()

        val isHorizontal = rulerEdges.horizontalLines.size >= rulerEdges.verticalLines.size

        // Define the ruler bounding region
        val allY = edgeLines.flatMap { listOf(it.startY, it.endY) }
        val allX = edgeLines.flatMap { listOf(it.startX, it.endX) }
        val minY = allY.minOrNull()?.toInt()?.coerceAtLeast(0) ?: return emptyList()
        val maxY = allY.maxOrNull()?.toInt()?.coerceAtMost(edges.rows() - 1) ?: return emptyList()
        val minX = allX.minOrNull()?.toInt()?.coerceAtLeast(0) ?: return emptyList()
        val maxX = allX.maxOrNull()?.toInt()?.coerceAtMost(edges.cols() - 1) ?: return emptyList()

        if (maxY <= minY || maxX <= minX) return emptyList()

        // Crop the ruler region
        val roi = edges.submat(minY, maxY, minX, maxX)
        val scaleHough = Mat()
        Imgproc.HoughLinesP(
            roi, scaleHough, 1.0, Math.PI / 180.0,
            30, SCALE_MARK_MIN_LENGTH.toDouble(), 3.0
        )

        val scaleMarks = mutableListOf<ScaleLine>()
        for (i in 0 until scaleHough.rows()) {
            val data = scaleHough[i, 0]
            val x1 = data[0]; val y1 = data[1]
            val x2 = data[2]; val y2 = data[3]
            val len = sqrt((x2 - x1).pow(2) + (y2 - y1).pow(2))

            if (len > SCALE_MARK_MAX_LENGTH) continue

            val angle = Math.toDegrees(atan2(y2 - y1, x2 - x1)).let {
                if (it < 0) it + 180.0 else it
            }

            // Scale marks are perpendicular to the ruler orientation
            val isPerpendicular = if (isHorizontal) {
                abs(angle - 90.0) < 20.0
            } else {
                angle < 20.0 || angle > 160.0
            }

            if (isPerpendicular) {
                val position = if (isHorizontal) (x1 + x2) / 2.0 + minX else (y1 + y2) / 2.0 + minY
                val scaleType = when {
                    len > SCALE_MARK_MAX_LENGTH * 0.7 -> ScaleType.CENTIMETER
                    len > SCALE_MARK_MAX_LENGTH * 0.4 -> ScaleType.MAJOR_MARK
                    else -> ScaleType.MILLIMETER
                }
                scaleMarks.add(ScaleLine(position, len, scaleType))
            }
        }

        roi.release()
        scaleHough.release()

        // Sort by position
        return scaleMarks.sortedBy { it.position }
    }

    /**
     * Calculate pixel-per-mm from the spacing of detected scale marks.
     * If enough centimeter marks are found, use their average spacing.
     * Otherwise fall back to estimating from the ruler edge length.
     */
    private fun calculatePixelScale(scaleLines: List<ScaleLine>, rulerEdges: RulerEdges): Double {
        // Try to calculate from centimeter marks first
        val cmMarks = scaleLines.filter { it.type == ScaleType.CENTIMETER }.sortedBy { it.position }
        if (cmMarks.size >= 2) {
            val spacings = mutableListOf<Double>()
            for (i in 1 until cmMarks.size) {
                spacings.add(cmMarks[i].position - cmMarks[i - 1].position)
            }
            val medianSpacing = spacings.sorted()[spacings.size / 2]
            // Each cm mark spacing = 10mm in pixels
            if (medianSpacing > 0) return medianSpacing / 10.0
        }

        // Fallback: use the ruler edge length
        val edgeLines = rulerEdges.horizontalLines + rulerEdges.verticalLines
        if (edgeLines.isNotEmpty()) {
            val longestEdge = edgeLines.maxByOrNull { it.length }!!
            // Ruler is RULER_LENGTH_CM cm = RULER_LENGTH_CM * 10 mm
            return longestEdge.length / (RULER_LENGTH_CM * 10.0)
        }

        // Ultimate fallback
        return 10.0
    }

    /**
     * Calculate confidence based on the number and quality of detected features.
     */
    private fun calculateConfidence(rulerEdges: RulerEdges, scaleLines: List<ScaleLine>): Double {
        val edgeCount = rulerEdges.horizontalLines.size + rulerEdges.verticalLines.size
        val scaleCount = scaleLines.size
        val cmCount = scaleLines.count { it.type == ScaleType.CENTIMETER }

        var confidence = 0.0

        // Two parallel edges detected -> good
        if (edgeCount >= 2) confidence += 0.3
        else if (edgeCount == 1) confidence += 0.1

        // Scale marks detected
        confidence += (scaleCount.coerceAtMost(30) / 30.0) * 0.4

        // Centimeter marks specifically
        confidence += (cmCount.coerceAtMost(15) / 15.0) * 0.3

        return confidence.coerceIn(0.0, 1.0)
    }
}

// 數據類別定義
data class CalibrationResult(
    val pixelPerMM: Double,
    val rulerLengthCM: Double,
    val confidence: Double,
    val detectedEdges: List<LineSegment>,
    val detectedScales: List<ScaleLine>,
    val rulerType: RulerType
)

data class LineSegment(
    val startX: Double,
    val startY: Double,
    val endX: Double,
    val endY: Double,
    val length: Double
)

data class ScaleLine(
    val position: Double,
    val length: Double,
    val type: ScaleType
)

data class RulerEdges(
    val horizontalLines: List<LineSegment>,
    val verticalLines: List<LineSegment>
)

enum class RulerType {
    WHITE_STRAIGHT_15CM,
    BLACK_STRAIGHT_20CM,
    FLEXIBLE_30CM
}

enum class ScaleType {
    MILLIMETER,
    CENTIMETER,
    MAJOR_MARK
}

class CalibrationException(message: String, cause: Throwable? = null) : Exception(message, cause)
