package com.woundmeasurement.app.pipeline

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.woundmeasurement.app.data.store.GalleryExport
import kotlinx.coroutines.launch
import androidx.compose.runtime.rememberCoroutineScope

/**
 * 模擬驗證入口(Compose)：從相簿選範例圖 / 拍照 → 端上管線或後端 classify → 顯示 [MeasureScreen]。
 * 用途：載入標準化範例圖或實拍,即時看 面積/組織/PUSH/信心度,並可切「端上 / 後端」比對(對齊預言機)。
 * 需求：端上路徑需 build.gradle OpenCV/onnxruntime + assets student_fp16.onnx;
 *       後端路徑需傳入 [backend](BackendClient,baseUrl+JWT),後端 app.py 啟動中。
 */
@Composable
fun SamplePickerScreen(
    vm: MeasureViewModel,
    backend: BackendClient? = null,
    onReview: () -> Unit = {},
    onSaveToTimeline: () -> Unit = {},
    exudate: Int? = null,
    onExudate: ((Int) -> Unit)? = null,
    source: String? = null,                        // 樣本來源(分析前就要決定,見下方說明)
    /** null＝來源已由呼叫端鎖定(臨床模式),整組選擇器隱藏。 */
    onSource: ((String) -> Unit)? = null,
    /** false＝隱藏「臨床」選項。快速量測沒有個案,產生的臨床樣本一律是孤兒紀錄。 */
    allowClinicalSource: Boolean = true,
    /**
     * false＝停用載入影像(相簿/檔案/拍照)。臨床模式在**未取得①照護同意**時必須為 false——
     * 只靠入口擋不夠：閘門要放在「真的會產生資料的那一步」，否則任何新入口都可能繞過去。
     */
    measureEnabled: Boolean = true,
    /** measureEnabled=false 時顯示的原因。 */
    disabledReason: String? = null,
    careCodeProvider: suspend () -> String? = { null },
    /** 辨識參照圖，透傳給 MeasureScreen（顯示於量測數值與滲液輸入之間）。 */
    preview: @Composable () -> Unit = {}
) {
    val ctx = LocalContext.current
    val runtime = rememberMedicalRuntime()
    val captureScope = rememberCoroutineScope()
    // 模式:false=端上、true=後端。有後端時預設走後端(端上 ONNX 原生庫在模擬器可能不相容)
    val useBackend = backend != null
    val measurementState by vm.state.collectAsState()

    fun dispatch(bmp: Bitmap) {
        // 模擬圖走**決定性色彩分割**(seg=color),完全不碰模型:
        // 印刷色塊是分布外樣本,模型本來就分不出來(實測空遮罩);而量測鏈驗證也不該用 AI 當量尺。
        val segMode = if (source == "phantom") "color" else null
        if (useBackend && backend != null)
            vm.analyzeViaBackend(bitmap = bmp, backend = backend, exudate = null, seg = segMode,
                careCodeProvider = careCodeProvider)
        else vm.analyze(bitmap = bmp, exudate = null)
    }

    val pickGallery = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        uri?.let {
            val bmp = ctx.contentResolver.openInputStream(it)?.use { s -> BitmapFactory.decodeStream(s) }
            if (bmp != null) dispatch(bmp)
        }
    }
    // 檔案瀏覽(DocumentsUI):可選 Download 等任意資料夾(Photo Picker 看不到未入媒體庫的新圖,如拖入模擬器的 JPG)
    val pickFile = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        uri?.let {
            val bmp = ctx.contentResolver.openInputStream(it)?.use { s -> BitmapFactory.decodeStream(s) }
            if (bmp != null) dispatch(bmp)
        }
    }
    // 2026-10：拍照改走 [CameraCaptureScreen]（CameraX 全解析），不再用
    // ActivityResultContracts.TakePicturePreview——那個契約回傳的是**縮圖**，
    // 12mm 的 ArUco marker 在縮圖上根本量不準，尺度基準一糊，面積就不可能準。
    // 舊按鈕刻意移除而非並存：留著一個會產生錯誤量測結果的入口，遲早有人誤用。
    var showCamera by remember { mutableStateOf(false) }
    /** 上一次擷取的解析度與相簿去向。給使用者看，也是內測回報時最需要的那行字。 */
    var captureInfo by remember { mutableStateOf<String?>(null) }

    if (showCamera) {
        CameraCaptureScreen(
            onCaptured = { bmp ->
                showCamera = false
                val mp = bmp.width.toLong() * bmp.height / 1_000_000.0
                captureScope.launch {
                    // Re-read consent after returning from the camera, before a shared copy exists.
                    val consentAllowsCapture = source != "clinical" ||
                        runCatching { careCodeProvider() }.getOrNull() != null
                    if (!measureEnabled || !consentAllowsCapture) {
                        captureInfo = "⚠ 照護同意已失效或無法確認，未匯出影像或啟動量測。"
                        return@launch
                    }
                    val rel = kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) { GalleryExport.saveCapture(ctx, bmp, source, runtime.scope) }
                    captureInfo = "已擷取 ${bmp.width}×${bmp.height}（%.1f MP）".format(mp) +
                        if (com.woundmeasurement.app.BuildConfig.MMH_VALIDATION) {
                            if (rel != null) " · 已建立加密私密副本"
                            else if (com.woundmeasurement.app.data.store.PrivateExports(ctx, runtime.scope).enabled) " · 行政副本未能保存；量測可繼續"
                            else " · 行政副本功能關閉"
                        } else if (rel != null) " · 已存入相簿 $rel" else " · 未存入相簿（此來源或此建置不允許）"
                    dispatch(bmp)
                }
            },
            onCancel = { showCamera = false },
            onError = { msg -> showCamera = false; captureInfo = "⚠️ $msg" },
        )
        return
    }

    Column(Modifier.fillMaxWidth().padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        if (onSource != null) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("印刷模擬圖（色彩分割）", Modifier.weight(1f))
                Switch(checked = source == "phantom",
                    enabled = measureEnabled && !measurementState.loading && vm.lastBitmap == null,
                    onCheckedChange = { onSource(if (it) "phantom" else "sample") })
            }
            Text("模擬圖用於量測驗證，不使用 AI 模型；一般影像請關閉。",
                style = MaterialTheme.typography.bodySmall)
        }

        if (!measureEnabled && disabledReason != null) Text(
            disabledReason, style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.error)

        val canLoad = measureEnabled && !measurementState.loading && (onSource == null || source != null)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            // 臨床現場以「拍照」為主(現場拍攝是常態,事後從相簿補件有壓縮/裁切破壞尺度的風險)
            Button({ showCamera = true }, Modifier.weight(1f), enabled = canLoad) { Text(if (vm.lastBitmap == null) "拍照" else "重新拍照") }
            OutlinedButton({ pickGallery.launch("image/*") }, Modifier.weight(1f), enabled = canLoad) { Text("相簿") }
            OutlinedButton({ pickFile.launch(arrayOf("image/*")) }, Modifier.weight(1f), enabled = canLoad) { Text("檔案") }
        }
        Text("可從相簿或檔案選擇影像。",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
        captureInfo?.let {
            Text(it, style = MaterialTheme.typography.bodySmall,
                color = if (it.startsWith("⚠")) MaterialTheme.colorScheme.error
                        else MaterialTheme.colorScheme.primary)
        }
        Divider()
        MeasureScreen(vm = vm, onReview = onReview, onSaveToTimeline = onSaveToTimeline,
            exudate = exudate, onExudate = onExudate, preview = preview)
    }
}
