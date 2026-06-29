package com.woundmeasurement.app.pipeline

import android.graphics.Bitmap
import org.opencv.android.Utils
import org.opencv.core.Mat
import org.opencv.imgproc.Imgproc
import org.opencv.objdetect.ArucoDetector as CvArucoDetector
import org.opencv.objdetect.Objdetect

/**
 * 校正貼紙 ArUco 偵測（OpenCV）→ 回傳 marker 四角影像座標 [x0,y0,x1,y1,x2,y2,x3,y3](TL,TR,BR,BL)。
 * 字典/ID 取自 SSOT(DICT_4X4_50, id 預設 7;見 Preproc.recommendedSticker / sticker_*).
 * 偵測演算法即 cv::aruco.detectMarkers，與後端 aruco_calibrate.detect_marker 同；
 * 面積以 WoundPipeline.areaCm2ByRatio(woundPx, markerPxArea, markerMm=Preproc.markerMmActive)。
 *
 * 需求：專案需含 OpenCV Android SDK(org.opencv:opencv 4.7+,內含 objdetect.ArucoDetector)。
 * 用法：val corners = ArucoDetector().detect(bitmap, wantId = 7); analyzer.run(bitmap, corners, exudate)
 */
class ArucoDetector(dictId: Int = Objdetect.DICT_4X4_50) {
    private val detector: CvArucoDetector =
        CvArucoDetector(Objdetect.getPredefinedDictionary(dictId))

    /** 回傳指定 id 的四角(8 值)或 null。 */
    fun detect(bitmap: Bitmap, wantId: Int = 7): FloatArray? {
        val rgba = Mat(); Utils.bitmapToMat(bitmap, rgba)
        val gray = Mat(); Imgproc.cvtColor(rgba, gray, Imgproc.COLOR_RGBA2GRAY)
        val corners = ArrayList<Mat>(); val ids = Mat()
        detector.detectMarkers(gray, corners, ids)
        if (ids.empty()) return null
        for (k in 0 until ids.rows()) {
            if (ids.get(k, 0)[0].toInt() == wantId) {
                val c = corners[k]                // 1x4, CV_32FC2 (TL,TR,BR,BL)
                val out = FloatArray(8)
                for (j in 0 until 4) { val p = c.get(0, j); out[2 * j] = p[0].toFloat(); out[2 * j + 1] = p[1].toFloat() }
                return out
            }
        }
        return null
    }
}
