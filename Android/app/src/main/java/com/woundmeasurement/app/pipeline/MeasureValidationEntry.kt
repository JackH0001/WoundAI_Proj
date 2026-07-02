package com.woundmeasurement.app.pipeline

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.woundmeasurement.app.processing.OnnxSegmentationModule
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * 量測驗證入口(模擬器/實機可跑)：自動登入後端 → 顯示 [SamplePickerScreen](端上/後端可切)。
 * 後端路徑:載入範例圖 → POST /api/v1/classify → 顯示面積/PUSH/組織(對齊預言機手機端版)。
 * 需求:後端 app.py 已啟動;模擬器用 10.0.2.2 對映主機 127.0.0.1;Manifest 需 INTERNET + cleartext。
 * 端上路徑另需 assets/student_fp16.onnx + module.loadModel();此入口預設走後端,缺端上模型不影響。
 */
@Composable
fun MeasureValidationEntry(
    backendBaseUrl: String = "http://10.0.2.2:5000",
    onBack: () -> Unit = {}
) {
    val ctx = LocalContext.current
    val vm = remember { MeasureViewModel(WoundAnalyzer(OnnxSegmentationModule(ctx)), null) }
    val backend = remember { BackendClient(backendBaseUrl) }
    var loginState by remember { mutableStateOf("後端登入中…") }

    LaunchedEffect(Unit) {
        loginState = try {
            val ok = withContext(Dispatchers.IO) { backend.login("admin", "woundai-admin") }
            if (ok) "✅ 後端已連線(admin) — 可切「後端」模式驗證"
            else "⚠️ 後端登入失敗(請確認 app.py 已啟動於主機 5000)"
        } catch (e: Exception) {
            "⚠️ 後端連線錯誤:${e.message}"
        }
    }

    Column(Modifier.fillMaxSize().padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(loginState, style = MaterialTheme.typography.bodySmall)
        Divider()
        SamplePickerScreen(vm = vm, backend = backend)
        OutlinedButton(onBack, Modifier.fillMaxWidth()) { Text("返回主畫面") }
    }
}
