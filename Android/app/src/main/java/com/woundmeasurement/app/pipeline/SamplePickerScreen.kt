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
fun SamplePickerScreen(vm: MeasureViewModel, backend: BackendClient? = null) {
    val ctx = LocalContext.current
    // 模式:false=端上、true=後端;後端不可用則固定端上
    var useBackend by remember { mutableStateOf(false) }

    fun dispatch(bmp: Bitmap) {
        if (useBackend && backend != null) vm.analyzeViaBackend(bitmap = bmp, backend = backend, exudate = null)
        else vm.analyze(bitmap = bmp, exudate = null)
    }

    val pickGallery = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        uri?.let {
            val bmp = ctx.contentResolver.openInputStream(it)?.use { s -> BitmapFactory.decodeStream(s) }
            if (bmp != null) dispatch(bmp)
        }
    }
    val takePhoto = rememberLauncherForActivityResult(ActivityResultContracts.TakePicturePreview()) { bmp ->
        if (bmp != null) dispatch(bmp)
    }

    Column(Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Text("模擬驗證 / 檢錯", style = MaterialTheme.typography.titleLarge)
        // 端上 / 後端 切換(後端為 null 時禁用)
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(if (useBackend) "模式：後端 classify" else "模式：端上管線")
            Spacer(Modifier.weight(1f))
            Switch(checked = useBackend, enabled = backend != null, onCheckedChange = { useBackend = it })
        }
        if (backend == null) Text("(未設定後端,僅端上)", style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Button({ pickGallery.launch("image/*") }, Modifier.weight(1f)) { Text("載入範例圖") }
            OutlinedButton({ takePhoto.launch(null) }, Modifier.weight(1f)) { Text("拍照") }
        }
        Divider()
        MeasureScreen(vm = vm, onReview = { /* TODO 導向修邊 */ }, onSaveToTimeline = { /* TODO 存時間軸 */ })
    }
}
