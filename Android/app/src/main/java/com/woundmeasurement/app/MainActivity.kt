package com.woundmeasurement.app

import android.Manifest
import android.content.pm.PackageManager
import android.os.Bundle
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import kotlinx.coroutines.launch
import com.woundmeasurement.app.ui.theme.WoundMeasurementAppTheme
import com.woundmeasurement.app.camera.CaptureResult
import com.woundmeasurement.app.camera.AdvancedCameraModule
import com.woundmeasurement.app.camera.ImageQualityAssessor
import com.woundmeasurement.app.data.database.WoundMeasurementDatabase
import com.woundmeasurement.app.data.entity.WoundCaseEntity
import com.woundmeasurement.app.data.repo.CaseRepository
import com.woundmeasurement.app.pipeline.BackendSettingsScreen
import com.woundmeasurement.app.pipeline.CaseSelectScreen
import com.woundmeasurement.app.data.entity.MeasurementEntity
import com.woundmeasurement.app.data.store.LocalImageStore
import com.woundmeasurement.app.pipeline.MeasureValidationEntry
import com.woundmeasurement.app.pipeline.MeasurementReviewScreen
import com.woundmeasurement.app.pipeline.RecentActivityScreen
import com.woundmeasurement.app.pipeline.WoundTimelineScreen

class MainActivity : ComponentActivity() {
    
    companion object {
        private const val TAG = "MainActivity"
    }
    
    private val requestPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { isGranted: Boolean ->
        if (isGranted) {
            Log.d(TAG, "相機權限已授予")
        } else {
            Log.w(TAG, "相機權限被拒絕")
        }
    }
    
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Log.d(TAG, "onCreate: 應用程式啟動")
        
        // 檢查相機權限
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
            requestPermissionLauncher.launch(Manifest.permission.CAMERA)
        }
        
        setContent {
            WoundMeasurementAppTheme {
                Surface(
                    modifier = Modifier.fillMaxSize(),
                    color = MaterialTheme.colorScheme.background
                ) {
                    WoundMeasurementApp()
                }
            }
        }
    }
}

/**
 * 主畫面＝**臨床工作台**，不是功能清單。
 *
 * 2026-07-28 重排（見 `docs/app_information_architecture.md`）。改掉的四件事：
 *  1. 真正可用的完整臨床流程原本藏在「AI 量測驗證(模擬)」後面——名字還告訴使用者這是模擬的。
 *  2. 「開始量測」是另一條**不綁個案**的量測路徑，結果 `caseId=null`，專門生產孤兒紀錄
 *     （Sprint N1 剛把這個問題修掉，入口不收掉等於白修）。
 *  3. 「查看歷史紀錄」是全域趨勢圖，把不同病患、不同部位的傷口畫成一條線 → 會誤導臨床判斷。
 *     拆成：趨勢圖進個案（只畫單一傷口）、最近活動留主畫面（清單＋跳轉，不畫圖）。
 *  4. 「醫師標註系統」的密碼已被清成 `REMOVED_USE_BACKEND_AUTH`，誰都登不進去——
 *     留著只會讓人以為壞了。要恢復應改走後端認證，非本輪範圍。
 */
@Composable
fun WoundMeasurementApp() {
    // rememberSaveable:轉螢幕會重建 Activity,用 remember 的話畫面直接跳回主選單
    var currentScreen by rememberSaveable { mutableStateOf("main") }
    // 從哪裡進來的就回哪裡去。硬寫 "cases" 的話,從「最近就診」進來按返回會掉到個案管理頁,
    // 而那頁是全新 composable(病患未選)→ 使用者失去脈絡。
    var backTo by rememberSaveable { mutableStateOf("main") }
    val context = LocalContext.current
    val repo = remember { CaseRepository.from(WoundMeasurementDatabase.getDatabase(context)) }
    // 個案選定後在畫面間傳遞。**只傳個案不傳同意**——同意是閘門條件,由 MeasureValidationEntry
    // 從 DB 重讀(傳遞只會多一份可能過期的快照,而且會出現「case 屬 A、consent 屬 B」的脆弱狀態)。
    // rememberSaveable(需 @Parcelize):轉螢幕若丟了 case,量測到一半的結果會被靜默彈回個案清單
    var chosenCase by rememberSaveable { mutableStateOf<WoundCaseEntity?>(null) }
    // 從時間軸點進來要檢視/重修的那一筆。刻意用 remember(不 saveable):
    // MeasurementEntity 未 Parcelable,轉螢幕就退回時間軸重新點,比硬塞序列化安全。
    var reviewRecord by remember { mutableStateOf<MeasurementEntity?>(null) }
    val imageStore = remember { LocalImageStore(context) }

    // 保存期限清理:結案逾 90 天者刪影像但**保留面積與趨勢**(病歷不可因逾期而毀)。
    // 放 App 啟動時跑一次;失敗不影響使用(下次啟動再試)。
    LaunchedEffect(Unit) {
        runCatching { repo.purgeExpiredImages(imageStore, days = 90) }
        // 背景喚醒 Cloud Run（min-instances=0 時冷啟動 10–30 秒）。
        // 放在**開 App 的當下**而不是量測前：使用者接下來要選病患、確認同意、選傷口，
        // 那段時間足夠容器啟動完成。量測前才喚醒等於把等待搬到最不能等的那一刻。
        runCatching { com.woundmeasurement.app.pipeline.BackendWarmup.ping(context) }
    }

    // 系統返回鍵:沒有這個的話,在任何子畫面按返回都直接結束 App
    BackHandler(enabled = currentScreen != "main") {
        currentScreen = when (currentScreen) {
            "measure", "timeline" -> backTo
            "review" -> if (backTo == "quickHistory") "quickHistory" else "timeline"
            else -> "main"
        }
    }

    // 只顯示當前畫面:主選單 或 全螢幕子畫面(子畫面各自有返回鈕,避免被選單擠壓/無法捲動)
    when (currentScreen) {
        // 臨床主線：個案 → 量測（source 鎖 clinical）
        "cases" -> CaseSelectScreen(
            repo = repo,
            onCaseChosen = { c -> chosenCase = c; backTo = "cases"; currentScreen = "measure" },
            onTimeline = { c -> chosenCase = c; backTo = "cases"; currentScreen = "timeline" },
            onBack = { currentScreen = "main" }
        )
        // 用 if/else 而非 `?.let{} ?: `:elvis 依賴「let 回 Unit?」這個隱式性質,
        // 日後只要有人在 lambda 尾端加一個可能回 null 的運算式,兩邊分支就會同時執行。
        "measure" -> {
            val c = chosenCase
            if (c != null) MeasureValidationEntry(
                clinicalMode = true, initialCase = c,
                onBack = { currentScreen = backTo }
            ) else LaunchedEffect(Unit) { currentScreen = backTo }   // 沒個案不該進臨床量測
        }
        "timeline" -> {
            val c = chosenCase
            if (c != null) WoundTimelineScreen(
                onBack = { currentScreen = backTo },
                caseId = c.id,
                caseLabel = "${c.bodySite}・${c.woundType} ${c.wdCode}",
                // 點單筆 → 回頭修邊/補送標註,不必重測
                onOpenRecord = { m -> reviewRecord = m; currentScreen = "review" }
            // caseId=null 會退回全域混畫的趨勢圖——那正是設計文件明文禁止的圖表,寧可退回上一頁
            ) else LaunchedEffect(Unit) { currentScreen = backTo }
        }
        "review" -> {
            val r = reviewRecord
            if (r != null) MeasurementReviewScreen(
                record = r,
                onBack = {
                    reviewRecord = null
                    currentScreen = if (backTo == "quickHistory") "quickHistory" else "timeline"
                }
            ) else LaunchedEffect(Unit) { currentScreen = "timeline" }
        }
        "recent" -> RecentActivityScreen(
            repo = repo,
            onOpenCase = { c -> chosenCase = c; backTo = "recent"; currentScreen = "measure" },
            onTimeline = { c -> chosenCase = c; backTo = "recent"; currentScreen = "timeline" },
            onBack = { currentScreen = "main" }
        )
        // 非臨床：範例/模擬圖驗證與檢錯（不綁個案，隱藏「臨床」來源）
        // 快速量測（範例／模擬圖）。它產生的紀錄 caseId=null，不屬於任何個案，
        // 先前存進 DB 後就再也叫不出來——所以這裡要給一個明確的查看入口。
        "quick" -> MeasureValidationEntry(
            clinicalMode = false,
            onBack = { currentScreen = "main" },
            onViewQuickHistory = { currentScreen = "quickHistory" }
        )
        "quickHistory" -> WoundTimelineScreen(
            onBack = { currentScreen = "quick" },
            unassignedOnly = true,
            onOpenRecord = { m -> reviewRecord = m; backTo = "quickHistory"; currentScreen = "review" }
        )
        "settings" -> BackendSettingsScreen(onBack = { currentScreen = "main" })
        else -> Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(16.dp),
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            Text(
                text = stringResource(id = R.string.app_title),
                fontSize = 24.sp,
                fontWeight = FontWeight.Bold,
                modifier = Modifier.padding(bottom = 24.dp)
            )
            MainButton("個案（病患・同意書・個案傷口・量測）") { currentScreen = "cases" }
            MainButton("最近就診") { currentScreen = "recent" }
            MainButton("快速量測（範例／模擬圖・不綁個案）") { currentScreen = "quick" }
            MainButton("設定（後端連線・帳號・佇列健康度）") { currentScreen = "settings" }
            Text(
                "臨床量測請由「個案」進入：紀錄才會歸戶到正確的傷口，代碼也才穩定（回診沿用同一組）。",
                fontSize = 13.sp,
                modifier = Modifier.padding(top = 8.dp)
            )
        }
    }
}

@Composable
fun MainButton(text: String, onClick: () -> Unit) {
    Button(
        onClick = onClick,
        modifier = Modifier
            .fillMaxWidth()
            .height(56.dp)
            .padding(bottom = 16.dp)
    ) {
        Text(text, fontSize = 18.sp)
    }
}

/**
 * ⚠ **2026-07-28 起已無入口**（見 `docs/app_information_architecture.md` P0-3）。
 *
 * 這是舊的「開始量測」路徑：不綁個案、結果不進時間軸，會產生 `caseId=null` 的孤兒紀錄，
 * 正是 Sprint N1 要根治的問題。臨床拍攝改由「個案 → 量測 → 拍照」進行
 * （`SamplePickerScreen` 已含拍照入口，且會帶個案綁定）。
 *
 * 暫時保留程式碼是因為它含 `AdvancedCameraModule` 的用法示範；確認不再需要後應整段刪除。
 */
@Composable
fun CaptureScreen(onBack: () -> Unit) {
    var cameraLoading by remember { mutableStateOf(true) }
    var isEmulator by remember { mutableStateOf(false) }
    var captureResult by remember { mutableStateOf<CaptureResult?>(null) }
    var qualityAssessment by remember { mutableStateOf<String?>(null) }
    var isCapturing by remember { mutableStateOf(false) }
    
    val context = LocalContext.current
    val advancedCamera = remember { AdvancedCameraModule(context) }
    
    LaunchedEffect(Unit) {
        // 檢查是否為模擬器
        isEmulator = android.os.Build.FINGERPRINT.contains("generic") || android.os.Build.FINGERPRINT.contains("sdk")
        
        if (!isEmulator) {
            try {
                if (advancedCamera.initialize()) {
                    advancedCamera.openCamera()
                }
            } catch (e: Exception) {
                Log.e("MainActivity", "相機初始化錯誤", e)
            }
        }
        
        kotlinx.coroutines.delay(1000)
        cameraLoading = false
    }
    
    DisposableEffect(Unit) {
        onDispose { advancedCamera.release() }
    }
    
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Text(
            text = stringResource(id = R.string.capture_wound),
            fontSize = 20.sp,
            fontWeight = FontWeight.Bold,
            modifier = Modifier.padding(bottom = 16.dp)
        )
        
        if (isEmulator) {
            Text(stringResource(id = R.string.emulator_mode), fontWeight = FontWeight.Bold)
            Text(stringResource(id = R.string.emulator_description), fontSize = 14.sp)
            Button(onClick = { /* 模擬 */ }, modifier = Modifier.fillMaxWidth().padding(top = 8.dp)) {
                Text(stringResource(id = R.string.simulate_photo))
            }
        } else {
            Text(if (cameraLoading) stringResource(id = R.string.camera_loading) else stringResource(id = R.string.camera_ready))
            
            if (!cameraLoading) {
                val coroutineScope = rememberCoroutineScope()
                Button(
                    onClick = {
                        coroutineScope.launch {
                            isCapturing = true
                            try {
                                val result = advancedCamera.captureHighQualityPhoto()
                                if (result != null) {
                                    captureResult = result
                                    val recommendations = ImageQualityAssessor().getQualityRecommendations(result.qualityScore)
                                    qualityAssessment = if (result.qualityScore.isAcceptable) {
                                        "✅ 品值合格: ${"%.1f".format(result.qualityScore.overallScore)}"
                                    } else {
                                        "⚠️ 品值不足: ${"%.1f".format(result.qualityScore.overallScore)}\n${recommendations.firstOrNull()}"
                                    }
                                }
                            } finally {
                                isCapturing = false
                            }
                        }
                    },
                    enabled = !isCapturing,
                    modifier = Modifier.fillMaxWidth().padding(16.dp)
                ) {
                    Text(if (isCapturing) stringResource(id = R.string.capturing) else stringResource(id = R.string.high_quality_capture))
                }
                
                qualityAssessment?.let {
                    Card(modifier = Modifier.fillMaxWidth().padding(16.dp)) {
                        Text(it, modifier = Modifier.padding(16.dp), fontSize = 14.sp)
                    }
                }
            }
        }
        
        Button(onClick = onBack, modifier = Modifier.padding(top = 16.dp)) {
            Text(stringResource(id = R.string.back_to_main))
        }
    }
}

@Composable
fun HistoryScreen(onBack: () -> Unit) {
    Column(modifier = Modifier.fillMaxSize().padding(16.dp), horizontalAlignment = Alignment.CenterHorizontally) {
        Text(stringResource(id = R.string.history_records), fontSize = 20.sp, fontWeight = FontWeight.Bold)
        Text(stringResource(id = R.string.database_loading), modifier = Modifier.padding(16.dp))
        Button(onClick = onBack) { Text(stringResource(id = R.string.back_to_main)) }
    }
}

/**
 * ⚠ **2026-08-03 起已無入口** — 由 [com.woundmeasurement.app.pipeline.BackendSettingsScreen] 取代
 * （後端位址可設定、憑證加密存本機、連線測試、飛輪佇列健康度）。
 *
 * 保留空殼只是為了讓還在引用 `R.string.settings` 的地方不必一次改完；確認無引用後應整段刪除。
 */
@Deprecated("改用 BackendSettingsScreen", level = DeprecationLevel.WARNING)
@Composable
fun SettingsScreen(onBack: () -> Unit) {
    Column(modifier = Modifier.fillMaxSize().padding(16.dp), horizontalAlignment = Alignment.CenterHorizontally) {
        Text(stringResource(id = R.string.settings), fontSize = 20.sp, fontWeight = FontWeight.Bold)
        Text("此頁已由「後端連線設定」取代。", modifier = Modifier.padding(16.dp))
        Button(onClick = onBack) { Text(stringResource(id = R.string.back_to_main)) }
    }
}
