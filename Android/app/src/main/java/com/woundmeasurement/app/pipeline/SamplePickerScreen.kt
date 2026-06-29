package com.woundmeasurement.app.pipeline

import android.graphics.BitmapFactory
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp

/**
 * 模擬驗證入口(Compose)：從相簿選範例圖 / 拍照 → 跑 [MeasureViewModel] 端上管線 → 顯示 [MeasureScreen]。
 * 用途：載入標準化範例圖或實拍,即時看 面積/組織/PUSH/信心度,做精確度初測與檢錯。
 * 需求：build.gradle 已加 OpenCV/onnxruntime、App 已 OpenCVLoader.initDebug()、assets 有 student_fp16.onnx。
 */
@Composable
fun SamplePickerScreen(vm: MeasureViewModel) {
    val ctx = LocalContext.current

    // 相簿選圖
    val pickGallery = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        uri?.let {
            val bmp = ctx.contentResolver.openInputStream(it)?.use { s -> BitmapFactory.decodeStream(s) }
            if (bmp != null) vm.analyze(bitmap = bmp, exudate = null)
        }
    }
    // 拍照(縮圖預覽;正式拍攝建議 CameraX 高解析)
    val takePhoto = rememberLauncherForActivityResult(ActivityResultContracts.TakePicturePreview()) { bmp ->
        if (bmp != null) vm.analyze(bitmap = bmp, exudate = null)
    }

    Column(Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Text("模擬驗證 / 檢錯", style = MaterialTheme.typography.titleLarge)
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Button({ pickGallery.launch("image/*") }, Modifier.weight(1f)) { Text("載入範例圖") }
            OutlinedButton({ takePhoto.launch(null) }, Modifier.weight(1f)) { Text("拍照") }
        }
        Divider()
        MeasureScreen(vm = vm, onReview = { /* TODO 導向修邊 */ }, onSaveToTimeline = { /* TODO 存時間軸 */ })
    }
}
