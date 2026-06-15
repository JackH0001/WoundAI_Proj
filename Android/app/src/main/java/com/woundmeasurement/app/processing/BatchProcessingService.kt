package com.woundmeasurement.app.processing

import android.content.Context
import android.graphics.BitmapFactory
import android.util.Log
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.io.File

/**
 * 批次影像處理服務
 * 對指定目錄或檔案清單依序執行品質評估 → 多模型分析。
 * 對應 iOS BatchProcessingService。
 */
class BatchProcessingService(private val context: Context) {

    companion object {
        private const val TAG = "BatchProcessingService"
        private val IMAGE_EXTENSIONS = setOf("jpg", "jpeg", "png", "bmp")
    }

    // ── 狀態 ──────────────────────────────────────────────────────────────────

    private val _state = MutableStateFlow<BatchState>(BatchState.Idle)
    val state: StateFlow<BatchState> = _state.asStateFlow()

    private val _progress = MutableStateFlow(BatchProgress())
    val progress: StateFlow<BatchProgress> = _progress.asStateFlow()

    private val _results = MutableStateFlow<List<BatchItemResult>>(emptyList())
    val results: StateFlow<List<BatchItemResult>> = _results.asStateFlow()

    private var processingJob: Job? = null

    // ── 設定 ──────────────────────────────────────────────────────────────────

    data class BatchConfig(
        val skipLowQuality:  Boolean = true,
        val minQualityScore: Float   = 60f,
        val maxConcurrency:  Int     = 1,           // Android 通常序列處理
    )

    // ── 主要方法 ──────────────────────────────────────────────────────────────

    /** 批次處理目錄下所有影像 */
    fun processDirectory(
        directory: File,
        config:    BatchConfig    = BatchConfig(),
        scope:     CoroutineScope,
    ) {
        val files = directory.listFiles()
            ?.filter { it.extension.lowercase() in IMAGE_EXTENSIONS }
            ?.toList() ?: emptyList()
        processFiles(files, config, scope)
    }

    /** 批次處理指定檔案清單 */
    fun processFiles(
        files:  List<File>,
        config: BatchConfig    = BatchConfig(),
        scope:  CoroutineScope,
    ) {
        if (files.isEmpty()) {
            _state.value = BatchState.Completed
            return
        }

        processingJob?.cancel()
        processingJob = scope.launch {
            _state.value = BatchState.Processing
            _progress.value = BatchProgress(total = files.size)
            val accumulator = mutableListOf<BatchItemResult>()

            for (file in files) {
                if (!isActive) break
                _progress.value = _progress.value.copy(currentFile = file.name)

                val result = processSingle(file, config)
                accumulator.add(result)

                val prev = _progress.value
                _progress.value = prev.copy(
                    processed = prev.processed + 1,
                    failed    = prev.failed + (if (result.success) 0 else 1),
                )
            }

            _results.value  = accumulator
            _state.value    = BatchState.Completed
            Log.i(TAG, "批次完成：${accumulator.count { it.success }}/${files.size} 成功")
        }
    }

    fun cancel() {
        processingJob?.cancel()
        _state.value = BatchState.Cancelled
    }

    // ── 單檔處理 ──────────────────────────────────────────────────────────────

    private suspend fun processSingle(
        file:   File,
        config: BatchConfig,
    ): BatchItemResult = withContext(Dispatchers.Default) {
        val start = System.currentTimeMillis()
        try {
            val bitmap = BitmapFactory.decodeFile(file.absolutePath)
                ?: return@withContext BatchItemResult(
                    filePath      = file.absolutePath,
                    success       = false,
                    errorMessage  = "無法解碼影像",
                    processingMs  = System.currentTimeMillis() - start,
                )

            // 1. 品質評估（使用 ImageQualityAssessor）
            val qualityScore = assessQuality(bitmap)
            if (config.skipLowQuality && qualityScore < config.minQualityScore) {
                return@withContext BatchItemResult(
                    filePath      = file.absolutePath,
                    success       = false,
                    qualityScore  = qualityScore,
                    errorMessage  = "品質不足 (${"%.1f".format(qualityScore)} < ${config.minQualityScore})",
                    processingMs  = System.currentTimeMillis() - start,
                )
            }

            // 2. 多模型分析
            val classifier = MultiModelClassifier(context)
            classifier.initialize()
            val ensemble = classifier.classify(bitmap)
            classifier.release()

            BatchItemResult(
                filePath        = file.absolutePath,
                success         = true,
                qualityScore    = qualityScore,
                woundType       = ensemble.woundType,
                severityScore   = ensemble.severityScore,
                confidence      = ensemble.confidence,
                tissueGranulation = ensemble.tissueComposition.granulation,
                tissueSlough      = ensemble.tissueComposition.slough,
                tissueNecrotic    = ensemble.tissueComposition.necrotic,
                processingMs    = System.currentTimeMillis() - start,
            )
        } catch (e: Exception) {
            Log.e(TAG, "批次單檔失敗：${file.name}", e)
            BatchItemResult(
                filePath     = file.absolutePath,
                success      = false,
                errorMessage = e.message,
                processingMs = System.currentTimeMillis() - start,
            )
        }
    }

    private fun assessQuality(bitmap: android.graphics.Bitmap): Float {
        // 簡化版：以解析度為品質代理（正式應呼叫 ImageQualityAssessor）
        val pixels = bitmap.width * bitmap.height
        return when {
            pixels >= 1920 * 1080 -> 90f
            pixels >= 1280 * 720  -> 75f
            pixels >= 640  * 480  -> 55f
            else                  -> 30f
        }
    }

    // ── 統計 ─────────────────────────────────────────────────────────────────

    fun summarise(items: List<BatchItemResult>): BatchSummary {
        val ok = items.filter { it.success }
        return BatchSummary(
            total              = items.size,
            succeeded          = ok.size,
            failed             = items.size - ok.size,
            averageConfidence  = if (ok.isEmpty()) 0f else ok.map { it.confidence }.average().toFloat(),
            averageQualityScore= if (ok.isEmpty()) 0f else ok.mapNotNull { it.qualityScore }.average().toFloat(),
        )
    }

    // ── 資料類別 ──────────────────────────────────────────────────────────────

    sealed class BatchState {
        object Idle      : BatchState()
        object Processing: BatchState()
        object Completed : BatchState()
        object Cancelled : BatchState()
        data class Error(val message: String) : BatchState()
    }

    data class BatchProgress(
        val total       : Int    = 0,
        val processed   : Int    = 0,
        val failed      : Int    = 0,
        val currentFile : String = "",
    ) {
        val progressPct: Float get() = if (total == 0) 0f else processed.toFloat() / total * 100f
    }

    data class BatchItemResult(
        val filePath          : String,
        val success           : Boolean,
        val qualityScore      : Float?  = null,
        val woundType         : String? = null,
        val severityScore     : Float?  = null,
        val confidence        : Float   = 0f,
        val tissueGranulation : Float   = 0f,
        val tissueSlough      : Float   = 0f,
        val tissueNecrotic    : Float   = 0f,
        val errorMessage      : String? = null,
        val processingMs      : Long    = 0L,
    )

    data class BatchSummary(
        val total              : Int,
        val succeeded          : Int,
        val failed             : Int,
        val averageConfidence  : Float,
        val averageQualityScore: Float,
    )
}
