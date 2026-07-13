package com.woundmeasurement.app.processing

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Matrix
import android.util.Log
import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.FileInputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.channels.FileChannel
import kotlin.math.PI
import kotlin.math.exp
import kotlin.math.sqrt

/**
 * OnnxSegmentationModule — Sprint T3
 *
 * On-device wound segmentation using student_fp16.onnx (蒸餾學生, A∪U teacher).
 *
 * Model specs (same as Cloud service):
 *   Input:  NCHW [1, 3, 256, 256] float32, ImageNet RGB (SSOT student)
 *   Output: NCHW [1, 1, 256, 256] float32 logits → sigmoid → binary at threshold=0.40
 *   Optimisation: 4-fold TTA (orig + h-flip + v-flip + 90°CW) — same as server
 *
 * Area / volume formula (Sprint S3):
 *   area_cm2   = wound_px × (scale_mm_per_px / 10)²
 *   perim_cm   = perimeter_px × (scale_mm_per_px / 10)
 *   volume_cm3 = (2/3) × π × r² × depth,  r = √(area/π),  depth = r × 0.2
 *
 * Usage:
 *   val module = OnnxSegmentationModule(context)
 *   module.loadModel()                   // call once, e.g. in ViewModel.init()
 *   val result = module.analyze(bitmap, scaleMmPerPx = 0.1)
 */
class OnnxSegmentationModule(private val context: Context) {

    companion object {
        private const val TAG = "OnnxSeg"
        private const val MODEL_FILENAME = "student_fp16.onnx"  // 蒸餾學生(SSOT student)
        private const val INPUT_SIZE = 256          // SSOT student=256
        private const val OUTPUT_SIZE = 256
        private const val THRESHOLD = 0.40f          // SSOT student thr0.4
        private const val TTA_ENABLED = true         // 4-fold TTA
    }

    // ── Data classes ──────────────────────────────────────────────────────────

    data class SegmentationResult(
        /** Float32 probability map [OUTPUT_SIZE × OUTPUT_SIZE], values 0–1 */
        val probMap: FloatArray,
        /** Binary mask at THRESHOLD */
        val binaryMask: BooleanArray,
        /** Wound pixel count (before scaling to output size) */
        val woundPixels: Int,
        /** Estimated perimeter in pixels */
        val perimeterPixels: Int,
        /** Actual area in cm² (null if no calibration) */
        val woundAreaCm2: Double?,
        /** Actual perimeter in cm (null if no calibration) */
        val woundPerimeterCm: Double?,
        /** Estimated volume in cm³ — ellipsoid model (null if no calibration) */
        val woundVolumeCm3: Double?,
        /** Confidence: mean probability in wound region */
        val confidence: Float,
        /** Whether TTA was applied */
        val ttaUsed: Boolean,
    )

    // ── State ─────────────────────────────────────────────────────────────────

    private var ortEnv: OrtEnvironment? = null
    private var ortSession: OrtSession? = null
    private var isLoaded = false

    /** 端上模型是否已載入(供 UI 顯示狀態)。 */
    val loaded: Boolean get() = isLoaded

    // ── Lifecycle ─────────────────────────────────────────────────────────────

    /**
     * Load wsm.onnx from app assets.
     * Copy the model file to `Android/app/src/main/assets/wsm.onnx` before building.
     */
    suspend fun loadModel() = withContext(Dispatchers.IO) {
        try {
            val env = OrtEnvironment.getEnvironment()
            val modelBytes = context.assets.open(MODEL_FILENAME).use { it.readBytes() }
            val sessionOptions = OrtSession.SessionOptions().apply {
                setOptimizationLevel(OrtSession.SessionOptions.OptLevel.ALL_OPT)
                setIntraOpNumThreads(4)
            }
            ortEnv = env
            ortSession = env.createSession(modelBytes, sessionOptions)
            isLoaded = true
            Log.i(TAG, "wsm.onnx loaded — input: ${ortSession!!.inputInfo.keys}, " +
                    "output: ${ortSession!!.outputInfo.keys}")
        } catch (e: Exception) {
            Log.e(TAG, "Failed to load wsm.onnx from assets: ${e.message}", e)
            isLoaded = false
        }
    }

    fun release() {
        ortSession?.close()
        ortEnv?.close()
        ortSession = null
        ortEnv = null
        isLoaded = false
    }

    // ── Public API ────────────────────────────────────────────────────────────

    /**
     * Run wound segmentation on [bitmap] (any size, any format).
     *
     * @param scaleMmPerPx  Calibration scale in mm/px.  Pass null for pixel-only output.
     * @param useTta        Override TTA flag (default = [TTA_ENABLED]).
     */
    suspend fun analyze(
        bitmap: Bitmap,
        scaleMmPerPx: Double? = null,
        useTta: Boolean = TTA_ENABLED,
    ): SegmentationResult = withContext(Dispatchers.Default) {
        if (!isLoaded || ortSession == null || ortEnv == null) {
            Log.w(TAG, "Model not loaded — returning empty result")
            return@withContext emptyResult()
        }

        val probMap: FloatArray = if (useTta) {
            runTta(bitmap)
        } else {
            runInferenceSingle(bitmap)
        }

        // Build binary mask at threshold
        val binary = BooleanArray(probMap.size) { probMap[it] > THRESHOLD }

        val woundCount = binary.count { it }
        val perimPx    = estimatePerimeter(binary, OUTPUT_SIZE, OUTPUT_SIZE)
        val confidence = if (woundCount > 0) {
            probMap.filterIndexed { i, _ -> binary[i] }.average().toFloat()
        } else 0f

        // Physical measurements
        val (areaCm2, perimCm, volCm3) = if (scaleMmPerPx != null && scaleMmPerPx > 0.0) {
            val pxToCm = scaleMmPerPx / 10.0
            val area   = woundCount * (pxToCm * pxToCm)
            val perim  = perimPx * pxToCm
            val r      = sqrt(area / PI)
            val depth  = r * 0.2
            val vol    = (2.0 / 3.0) * PI * r * r * depth
            Triple(area, perim, vol)
        } else {
            Triple(null, null, null)
        }

        SegmentationResult(
            probMap          = probMap,
            binaryMask       = binary,
            woundPixels      = woundCount,
            perimeterPixels  = perimPx,
            woundAreaCm2     = areaCm2,
            woundPerimeterCm = perimCm,
            woundVolumeCm3   = volCm3,
            confidence       = confidence,
            ttaUsed          = useTta,
        )
    }

    // ── TTA ───────────────────────────────────────────────────────────────────

    /**
     * 4-fold TTA: original + h-flip + v-flip + 90°CW.
     * Matches the server-side `_run_onnx_tta()` in wound_segmentation.py.
     */
    private fun runTta(bitmap: Bitmap): FloatArray {
        val orig = runInferenceSingle(bitmap)

        val bmpHFlip = bitmap.flip(horizontal = true)
        val bmpVFlip = bitmap.flip(horizontal = false)
        val bmpRot90 = bitmap.rotate90CW()

        var probHf = runInferenceSingle(bmpHFlip)
        var probVf = runInferenceSingle(bmpVFlip)
        var probR9 = runInferenceSingle(bmpRot90)

        // Undo geometric transforms on probability maps
        probHf = flipProbMap(probHf, OUTPUT_SIZE, horizontal = true)
        probVf = flipProbMap(probVf, OUTPUT_SIZE, horizontal = false)
        probR9 = rotateProbMap90CCW(probR9, OUTPUT_SIZE)

        // Average the four maps
        val avg = FloatArray(orig.size)
        for (i in orig.indices) {
            avg[i] = (orig[i] + probHf[i] + probVf[i] + probR9[i]) / 4f
        }
        return avg
    }

    // ── ONNX Inference ────────────────────────────────────────────────────────

    /**
     * Run a single forward pass.
     * Returns a float32 probability map resized to [OUTPUT_SIZE × OUTPUT_SIZE].
     */
    private fun runInferenceSingle(bitmap: Bitmap): FloatArray {
        val session = ortSession ?: return FloatArray(OUTPUT_SIZE * OUTPUT_SIZE)
        val env     = ortEnv    ?: return FloatArray(OUTPUT_SIZE * OUTPUT_SIZE)

        // Resize to INPUT_SIZE×INPUT_SIZE (SSOT wsm=224)
        val scaled = Bitmap.createScaledBitmap(bitmap, INPUT_SIZE, INPUT_SIZE, true)
        val pixels = IntArray(INPUT_SIZE * INPUT_SIZE)
        scaled.getPixels(pixels, 0, INPUT_SIZE, 0, 0, INPUT_SIZE, INPUT_SIZE)

        // Build NCHW float32 tensor — SSOT student = ImageNet RGB (planar);輸出 logits→sigmoid
        val mean = floatArrayOf(0.485f, 0.456f, 0.406f)
        val sd   = floatArrayOf(0.229f, 0.224f, 0.225f)
        val hw = INPUT_SIZE * INPUT_SIZE
        val chw = FloatArray(3 * hw)
        for (i in pixels.indices) {
            val px = pixels[i]
            val r = ((px shr 16) and 0xFF) / 255.0f
            val g = ((px shr 8)  and 0xFF) / 255.0f
            val b = ( px         and 0xFF) / 255.0f
            chw[0 * hw + i] = (r - mean[0]) / sd[0]   // R plane
            chw[1 * hw + i] = (g - mean[1]) / sd[1]   // G plane
            chw[2 * hw + i] = (b - mean[2]) / sd[2]   // B plane
        }
        val tensorBuf = ByteBuffer.allocateDirect(chw.size * 4).order(ByteOrder.nativeOrder())
        tensorBuf.asFloatBuffer().put(chw); tensorBuf.rewind()

        val inputName = session.inputNames.first()
        val tensor = OnnxTensor.createTensor(
            env, tensorBuf,
            longArrayOf(1, 3, INPUT_SIZE.toLong(), INPUT_SIZE.toLong())   // NCHW
        )

        val outputs = session.run(mapOf(inputName to tensor))
        tensor.close()

        // Output NCHW [1, 1, INPUT_SIZE, INPUT_SIZE] float32 logits → sigmoid
        val logits = (outputs[0].value as Array<Array<Array<FloatArray>>>)[0][0]  // [H][W]
        val sigMap = FloatArray(hw)
        for (h in 0 until INPUT_SIZE) {
            for (w in 0 until INPUT_SIZE) {
                val logit = logits[h][w].coerceIn(-88f, 88f)
                sigMap[h * INPUT_SIZE + w] = 1f / (1f + exp(-logit))
            }
        }
        outputs.close()

        // Bilinear resize to OUTPUT_SIZE
        return bilinearResize(sigMap, INPUT_SIZE, INPUT_SIZE, OUTPUT_SIZE, OUTPUT_SIZE)
    }

    // ── Geometry helpers ──────────────────────────────────────────────────────

    private fun flipProbMap(map: FloatArray, size: Int, horizontal: Boolean): FloatArray {
        val out = FloatArray(map.size)
        for (h in 0 until size) {
            for (w in 0 until size) {
                val srcW = if (horizontal) size - 1 - w else w
                val srcH = if (horizontal) h            else size - 1 - h
                out[h * size + w] = map[srcH * size + srcW]
            }
        }
        return out
    }

    /** Rotate a square probability map 90° counter-clockwise (undo 90°CW). */
    private fun rotateProbMap90CCW(map: FloatArray, size: Int): FloatArray {
        val out = FloatArray(map.size)
        for (h in 0 until size) {
            for (w in 0 until size) {
                // 90°CCW: out[h][w] = src[w][size-1-h]
                out[h * size + w] = map[w * size + (size - 1 - h)]
            }
        }
        return out
    }

    private fun Bitmap.flip(horizontal: Boolean): Bitmap {
        val m = Matrix().apply {
            if (horizontal) postScale(-1f, 1f, width / 2f, height / 2f)
            else            postScale(1f, -1f, width / 2f, height / 2f)
        }
        return Bitmap.createBitmap(this, 0, 0, width, height, m, true)
    }

    private fun Bitmap.rotate90CW(): Bitmap {
        val m = Matrix().apply { postRotate(90f) }
        return Bitmap.createBitmap(this, 0, 0, width, height, m, true)
    }

    /** Bilinear resize of a float32 probability map. */
    private fun bilinearResize(
        src: FloatArray, srcH: Int, srcW: Int, dstH: Int, dstW: Int
    ): FloatArray {
        val out = FloatArray(dstH * dstW)
        val scaleH = srcH.toFloat() / dstH
        val scaleW = srcW.toFloat() / dstW
        for (y in 0 until dstH) {
            val srcY = y * scaleH
            val y0   = srcY.toInt().coerceIn(0, srcH - 1)
            val y1   = (y0 + 1).coerceIn(0, srcH - 1)
            val fy   = srcY - y0
            for (x in 0 until dstW) {
                val srcX = x * scaleW
                val x0   = srcX.toInt().coerceIn(0, srcW - 1)
                val x1   = (x0 + 1).coerceIn(0, srcW - 1)
                val fx   = srcX - x0
                val tl   = src[y0 * srcW + x0]
                val tr   = src[y0 * srcW + x1]
                val bl   = src[y1 * srcW + x0]
                val br   = src[y1 * srcW + x1]
                out[y * dstW + x] =
                    tl * (1 - fx) * (1 - fy) +
                    tr * fx       * (1 - fy) +
                    bl * (1 - fx) * fy       +
                    br * fx       * fy
            }
        }
        return out
    }

    /**
     * Simple boundary-scan perimeter estimation.
     * A wound pixel is on the perimeter if at least one of its 4-neighbours is not wound.
     */
    private fun estimatePerimeter(binary: BooleanArray, height: Int, width: Int): Int {
        var perim = 0
        for (h in 0 until height) {
            for (w in 0 until width) {
                if (!binary[h * width + w]) continue
                val isEdge = (h == 0 || !binary[(h - 1) * width + w]) ||
                             (h == height - 1 || !binary[(h + 1) * width + w]) ||
                             (w == 0 || !binary[h * width + w - 1]) ||
                             (w == width - 1 || !binary[h * width + w + 1])
                if (isEdge) perim++
            }
        }
        return perim
    }

    private fun emptyResult() = SegmentationResult(
        probMap          = FloatArray(OUTPUT_SIZE * OUTPUT_SIZE),
        binaryMask       = BooleanArray(OUTPUT_SIZE * OUTPUT_SIZE),
        woundPixels      = 0,
        perimeterPixels  = 0,
        woundAreaCm2     = null,
        woundPerimeterCm = null,
        woundVolumeCm3   = null,
        confidence       = 0f,
        ttaUsed          = false,
    )
}
