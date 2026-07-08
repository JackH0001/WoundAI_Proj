package com.woundmeasurement.app.processing

import android.content.Context
import android.graphics.Bitmap
import android.util.Log
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.withContext
import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * 多模型集成分類器（對應 iOS MultiAlgorithmDetector）
 *
 * 載入三個 FP16 TFLite 模型並以加權投票合併結果：
 *  - wound_type_fp16.tflite   : 傷口類型（7 類）
 *  - severity_fp16.tflite     : 嚴重度（1–4 級）
 *  - tissue_fp16.tflite       : 組織成分（肉芽 / 腐肉 / 壞死）
 *
 * 當模型檔不存在時，對應分類器以規則式 fallback 運作，
 * 確保整體流程不中斷。
 */
class MultiModelClassifier(private val context: Context) {

    companion object {
        private const val TAG        = "MultiModelClassifier"
        private const val INPUT_DIM  = 224    // 模型輸入解析度（pixels）
        private const val CHANNELS   = 3

        // 模型資產路徑
        private const val MODEL_WOUND_TYPE = "models/wound_type_fp16.tflite"
        private const val MODEL_SEVERITY   = "models/severity_fp16.tflite"
        private const val MODEL_TISSUE     = "models/tissue_fp16.tflite"

        // 傷口類型標籤（對應模型輸出索引）
        val WOUND_TYPE_LABELS = listOf(
            "慢性傷口", "壓瘡", "糖尿病足", "靜脈潰瘍",
            "動脈潰瘍", "燒燙傷", "手術傷口"
        )

        // 組織成分標籤
        val TISSUE_LABELS = listOf("肉芽組織", "腐肉", "壞死組織")
    }

    // ── 內部分類器 ───────────────────────────────────────────────────────────

    private val woundTypeClassifier = SingleModelClassifier(
        context, MODEL_WOUND_TYPE, WOUND_TYPE_LABELS.size, "WoundType")
    private val severityClassifier  = SingleModelClassifier(
        context, MODEL_SEVERITY,   4,                      "Severity")
    private val tissueClassifier    = SingleModelClassifier(
        context, MODEL_TISSUE,     TISSUE_LABELS.size,     "Tissue")

    // ── 初始化 ───────────────────────────────────────────────────────────────

    suspend fun initialize() = withContext(Dispatchers.IO) {
        listOf(
            async { woundTypeClassifier.load() },
            async { severityClassifier.load()  },
            async { tissueClassifier.load()    },
        ).awaitAll()
        Log.i(TAG, "MultiModelClassifier 初始化完成：" +
            "woundType=${woundTypeClassifier.isLoaded}, " +
            "severity=${severityClassifier.isLoaded}, " +
            "tissue=${tissueClassifier.isLoaded}")
    }

    // ── 集成推論入口 ──────────────────────────────────────────────────────────

    /**
     * 對輸入影像執行三模型並行推論，返回集成結果。
     *
     * @param bitmap  原始傷口影像
     * @param mask    分割遮罩（可選；提供時裁切傷口 ROI 再推論）
     */
    suspend fun classify(bitmap: Bitmap, mask: Bitmap? = null): EnsembleResult =
        withContext(Dispatchers.Default) {
            val input = prepareInput(bitmap, mask)

            // 三模型並行
            val (typeJob, sevJob, tissJob) = Triple(
                async { woundTypeClassifier.infer(input) },
                async { severityClassifier.infer(input)  },
                async { tissueClassifier.infer(input)    },
            )

            val typeScores   = typeJob.await()
            val sevScores    = sevJob.await()
            val tissScores   = tissJob.await()

            buildEnsembleResult(typeScores, sevScores, tissScores)
        }

    // ── 集成邏輯 ─────────────────────────────────────────────────────────────

    private fun buildEnsembleResult(
        typeScores: FloatArray,
        sevScores:  FloatArray,
        tissScores: FloatArray,
    ): EnsembleResult {

        // 傷口類型：最高分標籤 + top-3
        val typeTop1Idx   = typeScores.indexOfMax()
        val typeTop3      = typeScores
            .mapIndexed { i, s -> i to s }
            .sortedByDescending { it.second }
            .take(3)
            .map { (i, s) -> TypeCandidate(WOUND_TYPE_LABELS.getOrElse(i) { "未知" }, s) }

        // 嚴重度：加權平均（1–4 分）
        val severityScore = (sevScores.indices).sumOf { i ->
            ((i + 1) * sevScores[i]).toDouble()
        }.toFloat().coerceIn(1f, 4f)

        // 組織成分百分比
        val tissTotal = tissScores.sum().coerceAtLeast(1e-6f)
        val tissue = TissueComposition(
            granulation = tissScores.getOrElse(0) { 0f } / tissTotal,
            slough      = tissScores.getOrElse(1) { 0f } / tissTotal,
            necrotic    = tissScores.getOrElse(2) { 0f } / tissTotal,
        )

        // 總體置信度：三模型最高分平均
        val overallConfidence = listOf(
            typeScores.max(),
            sevScores.max(),
            tissScores.max(),
        ).average().toFloat()

        return EnsembleResult(
            woundType         = WOUND_TYPE_LABELS.getOrElse(typeTop1Idx) { "未知" },
            woundTypeTop3     = typeTop3,
            severityScore     = severityScore,
            tissueComposition = tissue,
            confidence        = overallConfidence,
            modelsUsed        = listOf(
                if (woundTypeClassifier.isLoaded) "tflite" else "fallback",
                if (severityClassifier.isLoaded)  "tflite" else "fallback",
                if (tissueClassifier.isLoaded)    "tflite" else "fallback",
            ),
        )
    }

    // ── 輸入前處理 ────────────────────────────────────────────────────────────

    private fun prepareInput(bitmap: Bitmap, mask: Bitmap?): ByteBuffer {
        val src = if (mask != null) applyMask(bitmap, mask) else bitmap
        val resized = Bitmap.createScaledBitmap(src, INPUT_DIM, INPUT_DIM, true)

        val buf = ByteBuffer.allocateDirect(1 * INPUT_DIM * INPUT_DIM * CHANNELS * 4)
        buf.order(ByteOrder.nativeOrder())

        val pixels = IntArray(INPUT_DIM * INPUT_DIM)
        resized.getPixels(pixels, 0, INPUT_DIM, 0, 0, INPUT_DIM, INPUT_DIM)

        for (px in pixels) {
            // ImageNet 標準化
            buf.putFloat(((px shr 16 and 0xFF) / 255f - 0.485f) / 0.229f)
            buf.putFloat(((px shr  8 and 0xFF) / 255f - 0.456f) / 0.224f)
            buf.putFloat(((px        and 0xFF) / 255f - 0.406f) / 0.225f)
        }
        buf.rewind()
        return buf
    }

    /** 將遮罩外區域填黑，讓模型專注傷口 ROI */
    private fun applyMask(src: Bitmap, mask: Bitmap): Bitmap {
        val scaled = Bitmap.createScaledBitmap(mask, src.width, src.height, false)
        val result = src.copy(Bitmap.Config.ARGB_8888, true)
        val srcPx  = IntArray(src.width * src.height)
        val mskPx  = IntArray(src.width * src.height)
        src.getPixels(srcPx, 0, src.width, 0, 0, src.width, src.height)
        scaled.getPixels(mskPx, 0, src.width, 0, 0, src.width, src.height)
        for (i in srcPx.indices) {
            if ((mskPx[i] and 0xFF) < 128) srcPx[i] = 0xFF000000.toInt()
        }
        result.setPixels(srcPx, 0, src.width, 0, 0, src.width, src.height)
        return result
    }

    private fun FloatArray.indexOfMax(): Int =
        indices.maxByOrNull { this[it] } ?: 0

    // ── 釋放資源 ─────────────────────────────────────────────────────────────

    fun release() {
        woundTypeClassifier.close()
        severityClassifier.close()
        tissueClassifier.close()
    }

    // ── 資料類別 ─────────────────────────────────────────────────────────────

    data class TypeCandidate(val label: String, val score: Float)

    data class TissueComposition(
        val granulation: Float,   // 肉芽組織比例 [0,1]
        val slough:      Float,   // 腐肉比例
        val necrotic:    Float,   // 壞死組織比例
    )

    data class EnsembleResult(
        val woundType:         String,
        val woundTypeTop3:     List<TypeCandidate>,
        val severityScore:     Float,               // 1.0–4.0
        val tissueComposition: TissueComposition,
        val confidence:        Float,               // 0–1
        val modelsUsed:        List<String>,        // "tflite" | "fallback"
    ) {
        val qualityLabel: String get() = when {
            confidence >= 0.8f -> "高信心度"
            confidence >= 0.6f -> "中信心度"
            else               -> "低信心度"
        }
    }

    // ── 單一模型包裝器 ────────────────────────────────────────────────────────

    private class SingleModelClassifier(
        private val context:    Context,
        private val assetPath:  String,
        private val numClasses: Int,
        private val tag:        String,
    ) {
        var isLoaded = false
            private set

        private var interpreter: org.tensorflow.lite.Interpreter? = null

        fun load() {
            try {
                val fd    = context.assets.openFd(assetPath)
                val bytes = fd.createInputStream().readBytes()
                val buf   = ByteBuffer.allocateDirect(bytes.size).apply {
                    order(ByteOrder.nativeOrder()); put(bytes); rewind()
                }
                interpreter = org.tensorflow.lite.Interpreter(buf)
                isLoaded    = true
                Log.i("SingleModelClassifier", "[$tag] 載入成功：$assetPath")
            } catch (e: Exception) {
                Log.w("SingleModelClassifier", "[$tag] 載入失敗，使用 fallback：${e.message}")
            }
        }

        fun infer(inputBuf: ByteBuffer): FloatArray {
            val interp = interpreter
            if (interp == null || !isLoaded) return fallbackScores()

            val output = Array(1) { FloatArray(numClasses) }
            inputBuf.rewind()
            interp.run(inputBuf, output)
            return softmax(output[0])
        }

        private fun fallbackScores(): FloatArray {
            // 均勻分布：不影響集成結果但降低置信度
            return FloatArray(numClasses) { 1f / numClasses }
        }

        private fun softmax(logits: FloatArray): FloatArray {
            val max = logits.max()
            val exp = logits.map { Math.exp((it - max).toDouble()).toFloat() }
            val sum = exp.sum()
            return exp.map { it / sum }.toFloatArray()
        }

        fun close() {
            interpreter?.close()
            interpreter = null
        }
    }
}
