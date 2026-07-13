package com.woundmeasurement.app.pipeline

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
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
    val seg = remember { OnnxSegmentationModule(ctx) }
    val vm = remember { MeasureViewModel(WoundAnalyzer(seg), null) }
    val backend = remember { BackendClient(backendBaseUrl) }
    var loginState by remember { mutableStateOf("後端登入中…") }
    var modelState by remember { mutableStateOf("端上模型載入中…") }

    LaunchedEffect(Unit) {
        loginState = try {
            val ok = withContext(Dispatchers.IO) { backend.login("admin", "woundai-admin") }
            if (ok) "✅ 後端已連線(admin) — 可切「後端」模式驗證"
            else "⚠️ 後端登入失敗(請確認 app.py 已啟動於主機 5000)"
        } catch (e: Exception) {
            "⚠️ 後端連線錯誤:${e.message}"
        }
        // 端上 ONNX 原生庫(libonnxruntime.so)在部分模擬器/裝置(16KB 分頁/ABI)不相容,
        // 自動載入會拋 UnsatisfiedLinkError(Error 非 Exception,攔不到)→ App 閃退。
        // 故「不自動載入」;demo 走後端(不需原生庫)。端上點亮待實機(或加 16KB 對齊庫)驗證。
        modelState = "端上模型:未自動載入(避免模擬器原生庫閃退;demo 走後端模式)"
    }

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp)
    ) {
        Text(loginState, style = MaterialTheme.typography.bodySmall)
        Text(modelState, style = MaterialTheme.typography.bodySmall)
        Divider()
        SamplePickerScreen(vm = vm, backend = backend)
        Divider()
        DoctorFlywheelSubmit(vm = vm, backend = backend)
        OutlinedButton(onBack, Modifier.fillMaxWidth()) { Text("返回主畫面") }
    }
}

/**
 * 醫師確認・送出訓練標註(飛輪閉環 UI)。量測有結果後出現:選滲液 → 送出 →
 * 以後端回傳(或修邊後)傷口輪廓當 GT，POST /api/v1/annotation(doctor_verified/deidentified/consent_train=true)。
 * 修邊(拖曳頂點)為後續 C2b;此處先打通「確認→去識別代碼→守門→再訓練佇列」閉環。
 */
@Composable
private fun DoctorFlywheelSubmit(vm: MeasureViewModel, backend: BackendClient) {
    val st by vm.state.collectAsState()
    if (st.result == null) return
    var exudate by remember { mutableStateOf<Int?>(null) }
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text("醫師確認・送出訓練標註(飛輪)", style = MaterialTheme.typography.titleSmall)
        // 滲液量參考標準(NPUAP PUSH tool:Exudate Amount)
        Text("滲液量 Exudate(PUSH 標準):0=無(None) · 1=少量(Light) · 2=中量(Moderate) · 3=大量(Heavy)",
            style = MaterialTheme.typography.bodySmall)
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            (0..3).forEach { v ->
                FilterChip(selected = exudate == v, onClick = { exudate = v }, label = { Text("$v") })
            }
        }
        // 送出狀態放按鈕「上方」,按下即可見(先前在下方、被捲出畫面外)
        st.submitStatus?.let {
            Text(it, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.primary)
        }
        Button(
            onClick = {
                val code = "WD-" + System.currentTimeMillis().toString().takeLast(8)
                vm.submitAnnotation(backend, code, exudate, careNote = "emulator demo confirm")
            },
            modifier = Modifier.fillMaxWidth()
        ) { Text("醫師確認・送出標註 → 再訓練佇列") }
    }
}
