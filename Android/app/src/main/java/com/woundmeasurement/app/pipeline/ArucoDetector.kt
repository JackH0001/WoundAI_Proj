package com.woundmeasurement.app.pipeline

import android.graphics.Bitmap

/**
 * ArUco 偵測占位(stub)。
 * 原生 ArUco 需 OpenCV 4.7+ 的 org.opencv.objdetect.ArucoDetector;本專案 OpenCV 為 4.5.3(無此 API)。
 * 目前端上量測走「後端 classify(伺服器端 ArUco 自動校正)」路徑,故此處回 null(未偵測)不影響驗證。
 * 待升級 OpenCV 4.7+(objdetect.ArucoDetector)或改用 org.opencv.aruco(contrib)後,再補回原生偵測。
 * 簽名保留供 [MeasureViewModel] 使用(該路徑傳入 null,不會呼叫 detect)。
 */
class ArucoDetector(@Suppress("UNUSED_PARAMETER") dictId: Int = 0) {
    /** stub:一律回 null(未偵測)。回 marker 四角(8 值)之簽名保留。 */
    fun detect(@Suppress("UNUSED_PARAMETER") bitmap: Bitmap, @Suppress("UNUSED_PARAMETER") wantId: Int = 7): FloatArray? = null
}
