package com.woundmeasurement.app.pipeline

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.woundmeasurement.app.data.database.WoundMeasurementDatabase
import com.woundmeasurement.app.processing.OnnxSegmentationModule
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
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
    onBack: () -> Unit = {},
    /**
     * true＝**臨床量測**：由個案進入，`source` 鎖定 `clinical` 且不再詢問（已選個案就不可能是範例）。
     * false＝**快速量測**：不綁個案，只做範例/模擬圖驗證與檢錯，**隱藏「臨床」選項**——
     * 沒有個案的臨床樣本就是孤兒紀錄，從入口擋掉比事後提示有效。
     */
    clinicalMode: Boolean = false,
    initialCase: com.woundmeasurement.app.data.entity.WoundCaseEntity? = null
) {
    val ctx = LocalContext.current
    val db = remember { WoundMeasurementDatabase.getDatabase(ctx) }
    val dao = remember { db.measurementDao() }
    val repo = remember { com.woundmeasurement.app.data.repo.CaseRepository.from(db) }
    val seg = remember { OnnxSegmentationModule(ctx) }
    val vm = remember { MeasureViewModel(WoundAnalyzer(seg), null) }
    val backend = remember { BackendClient(backendBaseUrl) }
    var loginState by remember { mutableStateOf("後端登入中…") }
    var modelState by remember { mutableStateOf("端上模型載入中…") }
    var editing by remember { mutableStateOf(false) }
    var exudate by remember { mutableStateOf<Int?>(null) }
    // 樣本來源:載入影像前就要選(決定分割走 AI 還是色彩法),送出標註時沿用同一個值。
    // 臨床模式直接鎖 clinical——已經從個案進來了,再問一次只是多一個選錯的機會。
    var source by rememberSaveable { mutableStateOf<String?>(if (clinicalMode) "clinical" else null) }
    // Sprint N1:選定的傷口個案與其同意紀錄。臨床收案必須先選個案,
    // 否則量測存不進正確的時間軸、送出的 code 也不穩定(回診串不起來)。
    // rememberSaveable:轉螢幕若丟了「切換後的個案」,會悄悄換回進來時那個,接著存檔就寫進錯的時間軸
    var case by rememberSaveable { mutableStateOf(initialCase) }
    // 同意一律從 DB 重讀(見下方 LaunchedEffect),不由呼叫端傳入——傳進來的只會是可能過期的快照
    var consent by remember { mutableStateOf<com.woundmeasurement.app.data.entity.ConsentEntity?>(null) }
    var managingCase by remember { mutableStateOf(false) }

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

    var consentLoaded by remember { mutableStateOf(false) }
    // 同意狀態一律從 DB 重讀。key 除了 case 還要帶 managingCase——
    // 否則「進量測 → 切換個案頁把同意撤回 → 返回」時 case 沒變、key 沒變,
    // consent 就是舊快照,閘門會用已撤回的同意繼續放行。
    LaunchedEffect(case?.id, managingCase) {
        val c = case
        if (c != null && !managingCase) {
            consentLoaded = false
            // Room/Keystore 例外若不攔會沿 composition 炸掉整個 App,且畫面無線索。
            // catch 內一律 fail-closed(consent=null → 閘門關閉),不可反過來放行。
            consent = try { repo.activeConsent(c.patientId) } catch (e: Exception) { null }
            consentLoaded = true
        }
    }

    // ①照護同意是量測的硬前提（`IRB_consent_templates.md:23`）。沒有它連拍照都不該讓按。
    val careOk = case != null && consentLoaded &&
        consent?.consentCare == true && consent?.withdrawnAt == null

    // ⚠ 這裡**刻意不做任何釋放**,兩條都試過而且都更糟:
    //   (a) `seg.release()` 會 close 掉 **process 級**的 OrtEnvironment 單例 →
    //       反覆進出量測頁等於把整個 App 的 ORT 環境關掉;
    //   (b) `viewModelScope.cancel()` 會連**存檔與送標註**一起殺掉 →
    //       醫師按完「存入時間軸」立刻返回,Room 寫入被取消、紀錄靜默消失、畫面已離開連錯誤都看不到。
    // 殘留的成本只是一個 ViewModel 隨 composition 被 GC;正解是改用 `viewModel()` 讓
    // onCleared() 自動處理,但那要先補 lifecycle-viewmodel-compose 相依,列為後續。

    val st by vm.state.collectAsState()
    // 重要狀態(✅/ℹ️/⚠️)改彈出視窗,點「確認」才關閉
    var dlg by remember { mutableStateOf<String?>(null) }
    var seenStatus by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(st.submitStatus) {
        val s = st.submitStatus
        if (s != null && s != seenStatus && (s.startsWith("✅") || s.startsWith("ℹ️") || s.startsWith("⚠️"))) {
            dlg = s; seenStatus = s
        }
    }
    dlg?.let { msg ->
        AlertDialog(
            onDismissRequest = { dlg = null },
            confirmButton = { TextButton({ dlg = null }) { Text("確認") } },
            title = { Text(if (msg.startsWith("⚠️")) "注意" else "完成") },
            text = { Text(msg) }
        )
    }
    // 子畫面各自吃返回鍵:沒有這些的話,在修邊頁按系統返回會直接跳出整個量測流程,
    // 醫師手畫的遮罩全丟且沒有任何確認。
    var confirmDiscardEdit by remember { mutableStateOf(false) }
    BackHandler(enabled = editing || managingCase) {
        if (editing) confirmDiscardEdit = true else managingCase = false
    }
    if (confirmDiscardEdit) AlertDialog(
        onDismissRequest = { confirmDiscardEdit = false },
        title = { Text("放棄修邊?") },
        text = { Text("尚未按「完成修邊」,目前的筆畫會全部捨棄。") },
        confirmButton = { TextButton({ confirmDiscardEdit = false; editing = false }) { Text("放棄") } },
        dismissButton = { TextButton({ confirmDiscardEdit = false }) { Text("繼續修邊") } }
    )
    val eb = vm.lastBitmap
    if (managingCase) {
        CaseSelectScreen(
            repo = repo,
            onCaseChosen = { c -> case = c; source = "clinical"; managingCase = false },
            onBack = { managingCase = false }
        )
    } else if (editing && eb != null) {
        // 專屬全螢幕修邊頁(對齊原型 v_review:邊界+組織筆刷)
        WoundEditScreen(
            bitmap = eb,
            initialPolygon = vm.lastPolygon,
            originalArea = st.result?.areaCm2,
            tissueFrac = st.result?.tissueFrac ?: emptyMap(),
            exudate = exudate,
            mmPerPx = vm.lastMmPerPx,  // ArUco 尺度直傳(面積=像素×(mm/px)²)
            resume = vm.editRaster,   // 同影像續編:原樣載回上次遮罩(零損耗)
            onCancel = { editing = false },
            onDone = { poly, iou, newA, tis, raster ->
                vm.editRaster = raster
                vm.applyEditedPolygon(poly, iou, newA, exudate, tis); editing = false
            }
        )
    } else {
        Column(
            Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            Text(loginState, style = MaterialTheme.typography.bodySmall)
            Text(modelState, style = MaterialTheme.typography.bodySmall)

            // ---- 個案綁定列（臨床模式常駐；快速量測不顯示，因為它本來就不綁個案）----
            if (clinicalMode) {
                Divider()
                Text(
                    case?.let { "個案:${it.bodySite}・${it.woundType}  ${it.wdCode}" +
                            (if (consent?.trainEffective == true) "  訓練同意✓" else "  訓練同意✗(不可送標註)") }
                        ?: "⚠ 未選個案:臨床量測必須先選,否則紀錄無法歸戶、代碼也不穩定",
                    style = MaterialTheme.typography.bodySmall,
                    color = if (case == null) MaterialTheme.colorScheme.error
                            else MaterialTheme.colorScheme.primary
                )
                OutlinedButton({ managingCase = true }, Modifier.fillMaxWidth()) {
                    Text(if (case == null) "選擇個案" else "切換個案")
                }
            }

            // 選了模擬圖卻沒走到色彩分割 → 後端沒收到/不認得 seg=color,幾乎一定是**後端沒重啟**
            // (Flask debug=False 不熱載,改了 app.py 不重啟就不生效)。
            // 沒有這個檢查的話,畫面只會顯示「面積 0.00、路由 student」,要靠人去比對 route 才看得出來。
            if (source == "phantom" && st.result != null &&
                vm.lastRoute?.startsWith("phantom_color") != true) {
                Text("⚠ 已選「模擬圖」但後端路由是 ${vm.lastRoute ?: "?"}(應為 phantom_color)——" +
                     "色彩分割沒有生效,通常是**後端未重啟**(Flask 不熱載)。請重跑 app.py 後再試一次。",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.error)
            }

            // 選錯來源的救援提示:AI 空手但後端色彩分割抓得到 → 這幾乎一定是印刷模擬圖被選成臨床/範例。
            // 沒有這個提示,使用者只會看到「AI 未偵測到傷口」,誤以為模型壞了而去手畫。
            if (vm.lastPhantomHint && source != "phantom") {
                Text("⚠ AI 沒偵測到傷口,但影像中有明顯的印刷色塊——這看起來是**印刷模擬圖**。" +
                     "請把來源改選「模擬圖」重新載入,會改走色彩分割,初始輪廓一次到位。",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.error)
            }

            Divider()
            SamplePickerScreen(
                vm = vm, backend = backend,
                // AI 空遮罩(如印刷OOD/難例全失敗)也可進修邊:醫師從零手畫,ArUco 尺度(mm/px)仍有效
                onReview = { if (vm.lastBitmap != null) editing = true },
                // 量測綁個案 → 時間軸才畫得出單一傷口的癒合曲線
                onSaveToTimeline = {
                    // 存檔同樣受①照護同意管:載入影像後才去撤回同意的話,
                    // 只擋「載入」那一步是攔不住的(影像已經在手上了)。
                    if (clinicalMode && !careOk)
                        vm.reportSubmitBlocked("⚠️ 此病患未取得①照護同意(或已撤回),不得存入病歷")
                    else vm.saveToTimeline(dao, exudate, case, source)
                },
                exudate = exudate, onExudate = { exudate = it },
                // 臨床模式 source 已鎖定 → 傳 onSource=null 讓選擇器整組隱藏（少一次必選、也少一次選錯）
                source = source, onSource = if (clinicalMode) null else ({ s: String -> source = s }),
                allowClinicalSource = clinicalMode,   // 快速量測不得產生臨床樣本(沒有個案就是孤兒紀錄)
                // ⚠ 同意閘門放在「真的會產生資料的那一步」,不是只放在入口——
                // 只擋入口的話,任何新入口(例如最近就診)都可能繞過去。
                measureEnabled = !clinicalMode || careOk,
                disabledReason = when {
                    !clinicalMode -> null
                    case == null -> "⚠ 請先選擇傷口個案"
                    // 載入完成前不要指控「未取得同意」——擋住是對的,但訊息是假的
                    !consentLoaded -> "同意狀態載入中…"
                    else -> "⚠ 此病患尚未取得①照護同意（或已撤回），不得進行量測。請至個案管理簽署。"
                }
            )
            // 飛輪送出:滲液已填 + 上一步(修邊確認或存檔)完成後才自動顯示
            if (st.result != null) {
                Divider()
                if (exudate != null && (st.edited || st.saved)) {
                    DoctorFlywheelSubmit(vm = vm, backend = backend, exudate = exudate,
                        source = source, case = case, consent = consent, repo = repo)
                } else {
                    Text("(輸入滲液並完成「修邊確認」或「存入時間軸」後,將顯示送出訓練標註)",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            OutlinedButton(onBack, Modifier.fillMaxWidth()) { Text("返回主畫面") }
        }
    }
}

/**
 * 醫師確認・送出訓練標註(飛輪閉環 UI)。量測有結果後出現:選滲液 → 送出 →
 * 以後端回傳(或修邊後)傷口輪廓當 GT，POST /api/v1/annotation(doctor_verified/deidentified/consent_train=true)。
 * 修邊(拖曳頂點)為後續 C2b;此處先打通「確認→去識別代碼→守門→再訓練佇列」閉環。
 */
@Composable
private fun DoctorFlywheelSubmit(
    vm: MeasureViewModel, backend: BackendClient, exudate: Int?, source: String?,
    case: com.woundmeasurement.app.data.entity.WoundCaseEntity? = null,
    consent: com.woundmeasurement.app.data.entity.ConsentEntity? = null,
    repo: com.woundmeasurement.app.data.repo.CaseRepository? = null
) {
    val st by vm.state.collectAsState()
    val scope = rememberCoroutineScope()
    if (st.result == null) return
    // 臨床樣本必須有個案(穩定 wdCode)與有效訓練同意才可送;範例/模擬圖無受試者,不受此限。
    val isClinical = source == "clinical" || source == null
    val trainOk = if (isClinical) consent?.trainEffective == true else true
    val caseOk = if (isClinical) case != null else true
    // 來源在**載入影像前**就選好(SamplePickerScreen),因為它同時決定分割走 AI 還是色彩法。
    // 這裡只顯示、不重選,避免兩處狀態不一致(選 phantom 跑色彩分割、送出卻標 clinical)。
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text("醫師確認・送出訓練標註(飛輪)", style = MaterialTheme.typography.titleSmall)
        Text("來源 ${when (source) {
                "clinical" -> "臨床"; "sample" -> "範例"; "phantom" -> "模擬圖(色彩分割)"; else -> "未選" }} · " +
             (case?.let { "個案 ${it.wdCode} · " } ?: "") +
             "滲液 $exudate · 修邊${if (st.edited) "✓" else "—"} · 存檔${if (st.saved) "✓" else "—"}",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
        if (source == "phantom") Text(
            "⚠ 模擬圖樣本僅供量測鏈驗證,不計入臨床樣本數、不作模型訓練",
            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
        if (!caseOk) Text("⚠ 臨床樣本需先選傷口個案(代碼要穩定,回診才串得起來)",
            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
        if (caseOk && !trainOk) Text("⚠ 此病患未勾選②訓練同意(或已撤回),不得送出訓練標註",
            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)

        // 送出狀態放按鈕「上方」,按下即可見
        st.submitStatus?.let {
            Text(it, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.primary)
        }
        Button(
            onClick = {
                // 臨床樣本用個案的**穩定** wdCode(回診沿用同一組);範例/模擬圖沒有個案才另發,
                // 但同樣不可用 timestamp 尾碼(27.8 小時就循環、跨日必碰撞)。
                val code = case?.wdCode
                    ?: com.woundmeasurement.app.data.entity.WoundCaseEntity.newWdCode()
                scope.launch {
                    // ⚠ 送出當下**重新讀取**同意真值,不用畫面上的快照:
                    // 醫師可能剛在個案管理頁撤回訓練同意,而快照仍是舊的 true——
                    // 那就等於又回到「宣稱已同意但其實沒有」的原始缺陷,只是換了個形式。
                    val fresh = if (isClinical && case != null && repo != null)
                        repo.activeConsent(case.patientId)?.trainEffective == true
                    else trainOk
                    if (!fresh) vm.reportSubmitBlocked("⚠️ 此病患的訓練同意已撤回或失效,不得送出訓練標註")
                    else vm.submitAnnotation(backend, code, exudate,
                        careNote = "app confirm", source = source, consentTrain = true)
                }
            },
            enabled = source != null && caseOk && trainOk,
            modifier = Modifier.fillMaxWidth()
        ) { Text("醫師確認・送出標註 → 再訓練佇列") }
    }
}
