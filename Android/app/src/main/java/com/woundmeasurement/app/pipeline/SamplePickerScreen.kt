package com.woundmeasurement.app.pipeline

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp

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
    onSource: ((String) -> Unit)? = null
) {
    val ctx = LocalContext.current
    // 模式:false=端上、true=後端。有後端時預設走後端(端上 ONNX 原生庫在模擬器可能不相容)
    var useBackend by remember { mutableStateOf(backend != null) }

    fun dispatch(bmp: Bitmap) {
        // 模擬圖走**決定性色彩分割**(seg=color),完全不碰模型:
        // 印刷色塊是分布外樣本,模型本來就分不出來(實測空遮罩);而量測鏈驗證也不該用 AI 當量尺。
        val segMode = if (source == "phantom") "color" else null
        if (useBackend && backend != null)
            vm.analyzeViaBackend(bitmap = bmp, backend = backend, exudate = null, seg = segMode)
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
    val takePhoto = rememberLauncherForActivityResult(ActivityResultContracts.TakePicturePreview()) { bmp ->
        if (bmp != null) dispatch(bmp)
    }

    Column(Modifier.fillMaxWidth().padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Text("模擬驗證 / 檢錯", style = MaterialTheme.typography.titleLarge)
        // 端上 / 後端 切換(後端為 null 時禁用)
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(if (useBackend) "模式：後端 classify" else "模式：端上管線")
            Spacer(Modifier.weight(1f))
            Switch(checked = useBackend, enabled = backend != null, onCheckedChange = { useBackend = it })
        }
        if (backend == null) Text("(未設定後端,僅端上)", style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant)

        // 樣本來源必須在**分析前**決定:它同時決定分割走 AI 還是色彩法,以及日後計不計入臨床樣本數。
        // 放到送出前才問會太晚(那時已經用錯的分割跑完了),也會讓人重複選兩次。
        if (onSource != null) {
            Text("樣本來源(必選)", style = MaterialTheme.typography.bodySmall)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                listOf("clinical" to "臨床", "sample" to "範例", "phantom" to "模擬圖").forEach { (v, label) ->
                    if (source == v) Button({ onSource(v) }, Modifier.weight(1f)) { Text(label) }
                    else OutlinedButton({ onSource(v) }, Modifier.weight(1f)) { Text(label) }
                }
            }
            Text(when (source) {
                "clinical" -> "真實病人傷口 · AI 分割 · 計入臨床收案進度"
                "sample" -> "範例/示範圖 · AI 分割 · 不計入臨床樣本數"
                "phantom" -> "印刷模擬圖 · **色彩分割(非AI)** · 面積可驗尺度鏈,組織/PUSH 為顏料推算"
                else -> "⚠ 先選來源再載入影像:模擬圖會改走色彩分割,選錯會拿 AI 去量印刷色塊(必失敗)"
            }, style = MaterialTheme.typography.bodySmall,
                color = if (source == null) MaterialTheme.colorScheme.error
                        else MaterialTheme.colorScheme.onSurfaceVariant)
        }

        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button({ pickGallery.launch("image/*") }, Modifier.weight(1f),
                enabled = onSource == null || source != null) { Text("相簿") }
            OutlinedButton({ pickFile.launch(arrayOf("image/*")) }, Modifier.weight(1f),
                enabled = onSource == null || source != null) { Text("檔案") }
            OutlinedButton({ takePhoto.launch(null) }, Modifier.weight(1f),
                enabled = onSource == null || source != null) { Text("拍照") }
        }
        Text("「檔案」可瀏覽 Download 等資料夾(拖入模擬器的新圖選這個;相簿只列已入媒體庫的照片)",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
        Divider()
        MeasureScreen(vm = vm, onReview = onReview, onSaveToTimeline = onSaveToTimeline,
            exudate = exudate, onExudate = onExudate)
    }
}
