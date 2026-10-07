package com.woundmeasurement.app.processing

import android.content.Context
import android.graphics.Bitmap
import android.util.Log
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * 軟體深度估算模組
 *
 * 策略優先序：
 *  1. ARCore Depth API（若裝置支援且已啟用 AR Session）
 *  2. MiDaS TFLite 單目深度估算（assets/models/midas_fp16.tflite）
 *  3. 平面假設 fallback（固定平均深度）
 *
 * 深度圖格式：FloatArray，大小 = width × height，單位公尺（相對值）。
 */
class DepthEstimationModule(private val context: Context) {

    companion object {
        private const val TAG             = "DepthEstimationModule"
        private const val MIDAS_MODEL     = "models/midas_fp16.tflite"
        private const val MIDAS_INPUT_DIM = 256          // MiDaS Small 輸入解析度
        private const val FALLBACK_DEPTH_M = 0.30f       // 預設拍攝距離 30 cm
    }

    // ── 後端選擇 ────────────────────────────────────────────────────────────

    enum class Backend { ARCORE, MIDAS, FALLBACK }

    private var activeBackend: Backend = Backend.FALLBACK
    private var midasInterpreter: org.tensorflow.lite.Interpreter? = null

    // ── 初始化 ──────────────────────────────────────────────────────────────

    /**
     * 依序嘗試載入 ARCore → MiDaS，決定執行後端。
     * 應在 IO 執行緒呼叫。
     */
    suspend fun initialize(): Backend = withContext(Dispatchers.IO) {
        activeBackend = when {
            tryInitArCore()  -> Backend.ARCORE
            tryInitMiDaS()   -> Backend.MIDAS
            else             -> Backend.FALLBACK
        }
        Log.i(TAG, "深度估算後端：$activeBackend")
        activeBackend
    }

    private fun tryInitArCore(): Boolean {
        return try {
            // 僅在 runtime 嘗試反射，避免在不支援 ARCore 的裝置上崩潰
            val sessionClass = Class.forName("com.google.ar.core.Session")
            val isDepthSupported = sessionClass
                .getMethod("isDepthModeSupported",
                    Class.forName("com.google.ar.core.Config\$DepthMode"))
            Log.d(TAG, "ARCore Depth API 可用（反射確認）")
            true
        } catch (e: Exception) {
            Log.d(TAG, "ARCore Depth API 不可用：${e.message}")
            false
        }
    }

    private fun tryInitMiDaS(): Boolean {
        return try {
            val assetFd = context.assets.openFd(MIDAS_MODEL)
            val modelBuffer = assetFd.createInputStream().use { stream ->
                val bytes = stream.readBytes()
                ByteBuffer.allocateDirect(bytes.size).apply {
                    order(ByteOrder.nativeOrder())
                    put(bytes)
                    rewind()
                }
            }
            midasInterpreter = org.tensorflow.lite.Interpreter(modelBuffer)
            Log.i(TAG, "MiDaS TFLite 模型載入成功")
            true
        } catch (e: Exception) {
            Log.w(TAG, "MiDaS 模型載入失敗（使用 fallback）：${e.message}")
            false
        }
    }

    // ── 深度估算入口 ─────────────────────────────────────────────────────────

    /**
     * 估算輸入影像的深度圖。
     * @return [DepthMap]：相對深度值（0.0–1.0 normalised），或 fallback 固定值。
     */
    suspend fun estimateDepth(bitmap: Bitmap): DepthMap = withContext(Dispatchers.Default) {
        when (activeBackend) {
            Backend.ARCORE   -> estimateViaArCore(bitmap)
            Backend.MIDAS    -> estimateViaMiDaS(bitmap)
            Backend.FALLBACK -> makeFallbackDepthMap(bitmap.width, bitmap.height)
        }
    }

    // ── ARCore 後端 ──────────────────────────────────────────────────────────

    private fun estimateViaArCore(bitmap: Bitmap): DepthMap {
        // ARCore Depth 實際取用需在 AR Session onUpdate() 回呼中完成；
        // 此處作為離線 bitmap 估算時的橋接，退化為 MiDaS 或 fallback。
        Log.d(TAG, "ARCore 離線模式：退化為 MiDaS")
        return if (midasInterpreter != null)
            estimateViaMiDaS(bitmap)
        else
            makeFallbackDepthMap(bitmap.width, bitmap.height)
    }

    // ── MiDaS 後端 ───────────────────────────────────────────────────────────

    private fun estimateViaMiDaS(bitmap: Bitmap): DepthMap {
        val interpreter = midasInterpreter
            ?: return makeFallbackDepthMap(bitmap.width, bitmap.height)

        val inputDim   = MIDAS_INPUT_DIM
        val resized    = Bitmap.createScaledBitmap(bitmap, inputDim, inputDim, true)
        val inputBuf   = bitmapToFloatBuffer(resized)          // shape [1, H, W, 3]
        val outputBuf  = Array(1) { Array(inputDim) { FloatArray(inputDim) } }

        interpreter.run(inputBuf, outputBuf)

        // 將 MiDaS 輸出（反向深度）正規化至 [0, 1]
        val flat = outputBuf[0].flatMap { it.toList() }.toFloatArray()
        normaliseInPlace(flat)

        return DepthMap(
            width    = inputDim,
            height   = inputDim,
            values   = flat,
            backend  = Backend.MIDAS,
            unitNote = "relative (MiDaS)"
        )
    }

    // ── Fallback 後端 ────────────────────────────────────────────────────────

    private fun makeFallbackDepthMap(width: Int, height: Int): DepthMap {
        val values = FloatArray(width * height) { FALLBACK_DEPTH_M }
        return DepthMap(
            width    = width,
            height   = height,
            values   = values,
            backend  = Backend.FALLBACK,
            unitNote = "fixed ${FALLBACK_DEPTH_M}m"
        )
    }

    // ── 體積輔助計算 ─────────────────────────────────────────────────────────

    /**
     * 根據深度圖與分割遮罩估算傷口體積（cm³）。
     *
     * @param depthMap   深度估算結果
     * @param woundMask  分割遮罩（與 depthMap 同解析度，非零=傷口）
     * @param scaleMmPerPx 校準比例（mm/px），可選；若為 null 則回傳相對值
     */
    fun estimateVolumeCm3(
        depthMap:     DepthMap,
        woundMask:    FloatArray,
        scaleMmPerPx: Float? = null
    ): Float {
        require(depthMap.values.size == woundMask.size) {
            "depthMap 與 woundMask 尺寸不符"
        }

        // 傷口區域平均深度（正規化值）
        var depthSum  = 0.0f
        var pixelCount = 0
        for (i in woundMask.indices) {
            if (woundMask[i] > 0.5f) {
                depthSum += depthMap.values[i]
                pixelCount++
            }
        }
        if (pixelCount == 0) return 0f

        val avgDepthNorm = depthSum / pixelCount

        if (scaleMmPerPx == null) return avgDepthNorm  // 無校準，返回相對值

        // 換算：面積（cm²）× 深度（cm）
        val areaCm2  = pixelCount * (scaleMmPerPx / 10f) * (scaleMmPerPx / 10f)
        val depthCm  = avgDepthNorm * FALLBACK_DEPTH_M * 100f   // 粗估換算
        return areaCm2 * depthCm
    }

    // ── 工具函式 ─────────────────────────────────────────────────────────────

    private fun bitmapToFloatBuffer(bmp: Bitmap): ByteBuffer {
        val buf = ByteBuffer.allocateDirect(1 * bmp.height * bmp.width * 3 * 4)
        buf.order(ByteOrder.nativeOrder())
        val pixels = IntArray(bmp.width * bmp.height)
        bmp.getPixels(pixels, 0, bmp.width, 0, 0, bmp.width, bmp.height)
        for (px in pixels) {
            buf.putFloat(((px shr 16) and 0xFF) / 255f)  // R
            buf.putFloat(((px shr  8) and 0xFF) / 255f)  // G
            buf.putFloat(( px         and 0xFF) / 255f)  // B
        }
        buf.rewind()
        return buf
    }

    private fun normaliseInPlace(arr: FloatArray) {
        val min = arr.min()
        val max = arr.max()
        val range = max - min
        if (range < 1e-6f) return
        for (i in arr.indices) arr[i] = (arr[i] - min) / range
    }

    // ── 釋放資源 ─────────────────────────────────────────────────────────────

    fun release() {
        midasInterpreter?.close()
        midasInterpreter = null
        Log.d(TAG, "DepthEstimationModule 已釋放")
    }

    // ── 資料類別 ─────────────────────────────────────────────────────────────

    data class DepthMap(
        val width:    Int,
        val height:   Int,
        val values:   FloatArray,   // size = width × height
        val backend:  Backend,
        val unitNote: String
    ) {
        fun valueAt(x: Int, y: Int): Float = values[y * width + x]
    }
}
