package com.woundmeasurement.app.pipeline

import android.graphics.BitmapFactory
import androidx.test.platform.app.InstrumentationRegistry
import com.woundmeasurement.app.processing.OnnxSegmentationModule
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertTrue
import org.junit.Test
import android.util.Log

/**
 * Android 實機/模擬器 端到端「模擬驗證 + 檢錯」harness：
 * 載入範例圖(含乾淨 ArUco square_20mm_v2)→ student 分割 → ArUco 面積 → 組織v2 → PUSH，
 * 記錄結果並斷言合理範圍。供精確度初測與工作流檢錯。
 *
 * 前置(否則略過/失敗,屬預期)：
 *  1) build.gradle 加 OpenCV 4.7+ 與 onnxruntime-android;App 啟動 OpenCVLoader.initDebug()。
 *  2) student_fp16.onnx 置於 app/src/main/assets/。
 *  3) 範例圖置於 app/src/androidTest/assets/sample_std.jpg(用 標準化照片 其一)。
 *  4) 期望:ArUco 偵測成功、面積>0、PUSH 可計算;若貼紙未入鏡→面積 null(graceful)。
 */
class SampleValidationTest {

    @Test fun runSamplePipeline() = runBlocking {
        val ctx = InstrumentationRegistry.getInstrumentation().targetContext
        // 載入範例圖(androidTest assets)
        val bmp = ctx.assets.open("sample_std.jpg").use { BitmapFactory.decodeStream(it) }
        assertTrue("範例圖載入失敗", bmp != null)

        // 端上分割器 + ArUco + 協調器
        val student = OnnxSegmentationModule(ctx).apply { loadModel() }
        val aruco = ArucoDetector()                     // 需 OpenCV
        val analyzer = WoundAnalyzer(student)           // 單軌(無 wsm);可再傳 wsm 開雙軌

        val corners = aruco.detect(bmp, 7)
        Log.i("WoundValidation", "ArUco corners = ${corners?.joinToString()}")

        val r = analyzer.run(bitmap = bmp, markerCorners = corners, exudate = null)

        // 檢錯/精確度記錄
        Log.i("WoundValidation", "面積=${r.areaCm2} cm² route=${r.route} 信心=${r.confidence}")
        Log.i("WoundValidation", "組織=${r.tissueFrac}  PUSH area=${r.push.area} tissue=${r.push.tissue} partial=${r.push.partial}")

        // 斷言(寬鬆,屬煙霧測試):結果存在且 PUSH 組織子分介於 0..4
        assertTrue("組織子分超界", r.push.tissue in 0..4)
        if (corners != null) {
            assertTrue("有貼紙時面積應>0", (r.areaCm2 ?: 0.0) > 0.0)
            assertTrue("面積應在合理範圍(0.1–80cm²)", (r.areaCm2 ?: 0.0) in 0.1..80.0)
        }

        student.release()
    }
}
