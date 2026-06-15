package com.woundmeasurement.app.processing

import android.graphics.Bitmap
import android.util.Log
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.opencv.android.Utils
import org.opencv.core.Core
import org.opencv.core.CvType
import org.opencv.core.Mat
import org.opencv.core.MatOfPoint
import org.opencv.core.MatOfPoint2f
import org.opencv.core.Scalar
import org.opencv.core.Size
import org.opencv.imgproc.Imgproc

/**
 * Real OpenCV-based wound analyzer.
 *
 *  1. Convert to HSV and mask red/pink wound tissue.
 *  2. Clean up with morphological open/close.
 *  3. findContours → pick largest → area, perimeter, length/width (minAreaRect).
 *  4. Convert pixels → centimetres using pixelPerMM.
 *  5. Estimate depth from intensity shadow inside the wound (proxy).
 *  6. Draw contour overlay on a copy of the bitmap for UI feedback.
 */
class OpenCVProcessor {

    companion object {
        private const val TAG = "OpenCVProcessor"
    }

    suspend fun processWoundImage(
        bitmap: Bitmap,
        pixelPerMM: Double
    ): WoundAnalysisResult = withContext(Dispatchers.Default) {
        try {
            Log.d(TAG, "開始處理傷口圖像，像素比例: $pixelPerMM px/mm")

            val safePxPerMm = if (pixelPerMM > 0.0) pixelPerMM else 10.0

            val src = Mat()
            Utils.bitmapToMat(bitmap, src)

            // Drop alpha -> BGR for OpenCV-friendly ops
            val bgr = Mat()
            if (src.channels() == 4) {
                Imgproc.cvtColor(src, bgr, Imgproc.COLOR_RGBA2BGR)
            } else {
                src.copyTo(bgr)
            }

            // HSV colour filtering for red/pink wound tissue
            val hsv = Mat()
            Imgproc.cvtColor(bgr, hsv, Imgproc.COLOR_BGR2HSV)

            val mask1 = Mat()
            val mask2 = Mat()
            val maskYellow = Mat()
            Core.inRange(hsv, Scalar(0.0, 40.0, 40.0), Scalar(15.0, 255.0, 255.0), mask1)
            Core.inRange(hsv, Scalar(160.0, 40.0, 40.0), Scalar(180.0, 255.0, 255.0), mask2)
            Core.inRange(hsv, Scalar(15.0, 40.0, 80.0), Scalar(35.0, 255.0, 255.0), maskYellow)
            val mask = Mat()
            Core.bitwise_or(mask1, mask2, mask)
            Core.bitwise_or(mask, maskYellow, mask)

            // Morphology cleanup
            val kernel = Imgproc.getStructuringElement(Imgproc.MORPH_ELLIPSE, Size(5.0, 5.0))
            Imgproc.morphologyEx(mask, mask, Imgproc.MORPH_OPEN, kernel)
            Imgproc.morphologyEx(mask, mask, Imgproc.MORPH_CLOSE, kernel, org.opencv.core.Point(-1.0, -1.0), 2)

            // Contour detection
            val contours = mutableListOf<MatOfPoint>()
            val hierarchy = Mat()
            Imgproc.findContours(mask, contours, hierarchy, Imgproc.RETR_EXTERNAL, Imgproc.CHAIN_APPROX_NONE)

            val pxPerCm = safePxPerMm * 10.0
            val pxAreaPerCm2 = pxPerCm * pxPerCm

            var largest: MatOfPoint? = null
            var largestArea = 0.0
            for (c in contours) {
                val a = Imgproc.contourArea(c)
                if (a > largestArea) {
                    largestArea = a
                    largest = c
                }
            }

            // Defaults when no contour detected
            var areaPx = 0.0
            var perimPx = 0.0
            var lengthCm = 0.0
            var widthCm = 0.0
            val outContours = mutableListOf<WoundContour>()
            val overlayBgr = bgr.clone()

            if (largest != null && largestArea > 10.0) {
                areaPx = largestArea
                val c2f = MatOfPoint2f(*largest.toArray())
                perimPx = Imgproc.arcLength(c2f, true)
                val rect = Imgproc.minAreaRect(c2f)
                val w = rect.size.width
                val h = rect.size.height
                lengthCm = maxOf(w, h) / pxPerCm
                widthCm = minOf(w, h) / pxPerCm

                // Build WoundContour
                val points = largest.toArray().map { Point(it.x, it.y) }
                outContours.add(
                    WoundContour(
                        points = points,
                        area = areaPx / pxAreaPerCm2,
                        perimeter = perimPx / pxPerCm
                    )
                )

                // Overlay contour for UI
                Imgproc.drawContours(
                    overlayBgr,
                    listOf(largest),
                    -1,
                    Scalar(0.0, 255.0, 0.0),
                    3
                )
            }

            val areaCm2 = areaPx / pxAreaPerCm2
            val perimCm = perimPx / pxPerCm

            // Depth proxy: mean darkness inside wound region (in cm, rough)
            val depthCm = estimateDepthCm(bgr, mask)

            // Rough confidence: how compact the wound is
            val confidence = if (perimPx > 0.0) {
                val circularity = 4.0 * Math.PI * areaPx / (perimPx * perimPx)
                (0.5 + 0.5 * circularity.coerceIn(0.0, 1.0)).coerceIn(0.0, 1.0)
            } else 0.0

            // Convert overlay back to bitmap
            val overlayRgba = Mat()
            Imgproc.cvtColor(overlayBgr, overlayRgba, Imgproc.COLOR_BGR2RGBA)
            val outBitmap = Bitmap.createBitmap(bitmap.width, bitmap.height, Bitmap.Config.ARGB_8888)
            Utils.matToBitmap(overlayRgba, outBitmap)

            // Release native Mats
            listOf(src, bgr, hsv, mask1, mask2, maskYellow, mask, hierarchy, overlayBgr, overlayRgba)
                .forEach { it.release() }

            val result = WoundAnalysisResult(
                area = areaCm2,
                perimeter = perimCm,
                depth = depthCm,
                pixelPerMM = safePxPerMm,
                confidence = confidence,
                processedImage = outBitmap,
                contours = outContours,
                measurements = WoundMeasurements(
                    length = lengthCm,
                    width = widthCm,
                    area = areaCm2,
                    perimeter = perimCm,
                    depth = depthCm
                )
            )

            Log.d(
                TAG,
                "傷口分析完成 - 面積: ${"%.2f".format(areaCm2)}cm², 周長: ${"%.2f".format(perimCm)}cm, " +
                    "長寬: ${"%.2f".format(lengthCm)}×${"%.2f".format(widthCm)}cm, 信心: ${"%.2f".format(confidence)}"
            )
            result
        } catch (e: UnsatisfiedLinkError) {
            Log.e(TAG, "OpenCV native library not loaded – returning fallback", e)
            fallbackResult(bitmap, pixelPerMM)
        } catch (e: Exception) {
            Log.e(TAG, "傷口圖像處理失敗", e)
            throw WoundProcessingException("傷口圖像處理失敗: ${e.message}", e)
        }
    }

    /**
     * Estimate wound depth (cm) from relative darkness inside the wound.
     * Darker pixels ↔ deeper shadow → deeper wound.  Purely heuristic.
     */
    private fun estimateDepthCm(bgr: Mat, mask: Mat): Double {
        if (Core.countNonZero(mask) == 0) return 0.0
        val gray = Mat()
        Imgproc.cvtColor(bgr, gray, Imgproc.COLOR_BGR2GRAY)
        val mean = Core.mean(gray, mask)
        gray.release()
        val darkness = 1.0 - (mean.`val`[0] / 255.0)
        // Map 0..1 darkness → 0..2.5 cm depth
        return (darkness * 2.5).coerceIn(0.0, 5.0)
    }

    private fun fallbackResult(bitmap: Bitmap, pixelPerMM: Double): WoundAnalysisResult =
        WoundAnalysisResult(
            area = 0.0,
            perimeter = 0.0,
            depth = 0.0,
            pixelPerMM = pixelPerMM,
            confidence = 0.0,
            processedImage = bitmap,
            contours = emptyList(),
            measurements = WoundMeasurements(0.0, 0.0, 0.0, 0.0, 0.0)
        )
}

// 數據類別定義
data class WoundAnalysisResult(
    val area: Double, // 平方公分
    val perimeter: Double, // 公分
    val depth: Double, // 公分
    val pixelPerMM: Double,
    val confidence: Double,
    val processedImage: Bitmap,
    val contours: List<WoundContour>,
    val measurements: WoundMeasurements
)

data class WoundMeasurements(
    val length: Double, // 公分
    val width: Double, // 公分
    val area: Double, // 平方公分
    val perimeter: Double, // 公分
    val depth: Double // 公分
)

data class WoundContour(
    val points: List<Point>,
    val area: Double,
    val perimeter: Double
)

data class Point(
    val x: Double,
    val y: Double
)

class WoundProcessingException(message: String, cause: Throwable? = null) : Exception(message, cause)
