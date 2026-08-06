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
    /**
     * null＝**從設定讀**（正常路徑）。傳值只給測試與預覽覆寫用。
     *
     * ⚠ 這裡曾經是 `= "http://10.0.2.2:5000"` 的硬編碼預設值。`10.0.2.2` 是模擬器專用的
     * loopback 別名，真機上不存在——而 n=20 臨床收案必須在真機做。留著這個預設值等於
     * 「在醫院現場一定失敗，且只會顯示『後端未連線』」。
     */
    backendBaseUrl: String? = null,
    onBack: () -> Unit = {},
    /**
     * true＝**臨床量測**：由個案進入，`source` 鎖定 `clinical` 且不再詢問（已選個案就不可能是範例）。
     * false＝**快速量測**：不綁個案，只做範例/模擬圖驗證與檢錯，**隱藏「臨床」選項**——
     * 沒有個案的臨床樣本就是孤兒紀錄，從入口擋掉比事後提示有效。
     */
    clinicalMode: Boolean = false,
    initialCase: com.woundmeasurement.app.data.entity.WoundCaseEntity? = null,
    /** 快速量測專用：查看未歸戶紀錄。臨床模式不顯示（那些紀錄都在個案時間軸裡）。 */
    onViewQuickHistory: (() -> Unit)? = null
) {
    val ctx = LocalContext.current
    val db = remember { WoundMeasurementDatabase.getDatabase(ctx) }
    val dao = remember { db.measurementDao() }
    val repo = remember { com.woundmeasurement.app.data.repo.CaseRepository.from(db) }
    val imageStore = remember { com.woundmeasurement.app.data.store.LocalImageStore(ctx) }
    val seg = remember { OnnxSegmentationModule(ctx) }
    val vm = remember { MeasureViewModel(WoundAnalyzer(seg), null) }
    val baseUrl = remember(backendBaseUrl) {
        backendBaseUrl ?: com.woundmeasurement.app.data.store.AppSettings.backendUrl(ctx)
    }
    val backend = remember(baseUrl) { BackendClient(baseUrl) }
    var loginState by remember { mutableStateOf("後端登入中…") }
    /** 登入身分。null＝尚未登入 → 一律以「最少權限」呈現（fail-closed）。 */
    var me by remember { mutableStateOf<LoginIdentity?>(null) }
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
    /** 最近一次寫進共用相簿的相對路徑（快速量測才會有值）。 */
    var galleryPath by remember { mutableStateOf<String?>(null) }

    LaunchedEffect(baseUrl) {
        // ⚠ 憑證改由「設定」頁存在本機(Keystore 加密),**不再硬編碼**。
        // 先前是 login("admin","woundai-admin")——明文編進 APK,反編譯就拿得到,
        // 帶去醫院等於把後端鑰匙一起帶出門。
        val settings = com.woundmeasurement.app.data.store.AppSettings
        val u = settings.backendUser(ctx)
        val p = settings.backendPassword(ctx)
        loginState = if (u.isBlank() || p.isBlank()) {
            "⚠️ 尚未設定後端帳密 — 請到主畫面「設定」填入後端位址與帳號密碼"
        } else try {
            val ok = withContext(Dispatchers.IO) { backend.login(u, p) }
            // 登入成功本身就把容器叫醒了，順手記下時間，讓下方的冷啟動提示判斷得準。
            if (ok) { me = backend.identity; BackendWarmup.ping(ctx) } else me = null
            // 「連不上」與「帳密錯」是兩種處置,訊息要分開(現場才知道要調網路還是調帳號)
            if (ok) "✅ 後端已連線($u) @ $baseUrl"
            else "⚠️ 連得到後端但帳密不正確 — 請到「設定」確認帳號密碼"
        } catch (e: Exception) {
            "⚠️ 連不到後端 $baseUrl：${e.message}\n請到「設定」確認位址，並檢查 app.py 是否啟動、是否同網段"
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

            // 共用手機時最常見的錯誤是「用上一個人的登入做事」。身分常駐顯示才會被發現。
            me?.let { u ->
                Text("👤 ${u.label()}", style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.primary)
                if (clinicalMode && !u.can("gt.verify")) Text(
                    "此角色可量測與存入病歷，但**修邊不會產生「醫師已驗證」**，也不得送訓練標註。" +
                    "GT 的背書須由醫師完成——這是後端強制的，換帳號才會改變。",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                if (clinicalMode && !u.can("record.save")) Text(
                    "⚠ 此角色不得存入個案時間軸，請由護理師或醫師完成存檔。",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error)
            }

            // 冷啟動提示。Cloud Run 閒置會縮到零，第一次量測要等容器載入 82MB 模型。
            // 不講的話醫師會以為是 App 當掉——而它其實正在正常運作，只是第一次比較久。
            if (BackendWarmup.sinceLastOkMs() > 15 * 60 * 1000L) {
                Text("ℹ 後端可能處於休眠（雲端閒置會自動縮容）。第一次量測約需 10–30 秒喚醒，之後即正常。",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }

            // 醫師修邊確認狀態。**取消不算確認** —— 先前按取消後畫面仍留著 AI 的原始輪廓，
            // 而「存入時間軸」與「送訓練標註」照樣可按，等於把沒人看過的 AI 輸出
            // 當成人工 GT。存檔仍允許（那是一筆合法的 AI 初步量測紀錄），
            // 但**送訓練標註必須擋下**，並且要讓醫師看得出現在是什麼狀態。
            if (st.result != null) {
                if (vm.lastDoctorVerified) {
                    Text("✓ 已完成醫師修邊確認 — 可送訓練標註",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.primary)
                } else {
                    Text("尚未完成醫師修邊確認：此結果為 AI 原始輸出。" +
                         "可存入時間軸作為初步量測，但**不得送訓練標註**——" +
                         "訓練集的 GT 必須來自人的判斷。請按「醫師確認・修邊」並完成（按取消不算）。",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.error)
                }
            }

            // 範例圖被當成臨床樣本的偵測。
            //
            // 後端以內容 sha1 當 image_id,回報這批位元組先前是否已上傳過。真實回診照不可能與上次
            // 逐位元相同(光線、角度、時間戳都會變),所以臨床模式看到這個訊號,幾乎必然是選到了
            // 先前用過的範例/示範圖。實測後果有兩層,兩層都是靜默的:
            //   (a) 訓練集:該筆以 source=clinical 進佇列 → 灌水臨床收案進度、拿非臨床影像去訓練;
            //   (b) 病歷:時間軸把兩張不同的傷口畫成一條癒合曲線(實測顯示過假的「↓38%」)。
            // 程式無法判斷照片裡是不是真人,但「同一批位元組再次出現」是可靠且可實作的代理訊號。
            // 不硬擋(臨床上可能有正當的重新分析需求),但要讓醫師看得見並主動確認。
            if (clinicalMode && vm.lastImageReused) {
                Text("⚠ 這張影像後端先前已收過(逐位元相同)。回診照片不可能與上次完全一樣——" +
                     "這多半是**先前用過的範例圖**。若確實如此請勿存入病歷:" +
                     "它會被計入臨床收案進度,也會讓這個傷口的癒合曲線變成兩張不同傷口相比。",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.error)
            }

            if (!clinicalMode) {
                galleryPath?.let {
                    Text("🖼 原始影像已存入相簿：$it",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.primary)
                }
                onViewQuickHistory?.let { go ->
                    OutlinedButton(go, Modifier.fillMaxWidth()) { Text("查看快速量測紀錄（未歸戶）") }
                }
                Text("快速量測的紀錄不屬於任何個案，不會出現在個案時間軸，也不計入臨床收案進度。",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }

            Divider()
            SamplePickerScreen(
                vm = vm, backend = backend,
                // AI 空遮罩(如印刷OOD/難例全失敗)也可進修邊:醫師從零手畫,ArUco 尺度(mm/px)仍有效
                onReview = { if (vm.lastBitmap != null) editing = true },
                // 量測綁個案 → 時間軸才畫得出單一傷口的癒合曲線
                onSaveToTimeline = {
                    // UI 閘門只是為了給出可讀的理由；真正的拒絕在後端（見 docs/rbac_design.md §5）。
                    if (clinicalMode && me != null && !me!!.can("record.save")) {
                        vm.reportSubmitBlocked(
                            "⚠️ 角色「${me!!.roleZh}」不得存入個案時間軸。\n" +
                            "請由護理師或醫師完成存檔（量測結果不會遺失）。")
                        return@SamplePickerScreen
                    }
                    // 快速量測：把原始影像另存一份到共用相簿，供事後比對或匯入個案。
                    // ⚠ **只有** sample/phantom 會真的寫入——GalleryExport 自己會拒絕其他來源，
                    // 因為共用相簿不受本 App 的加密、保存期限與撤回同意約束。
                    if (!clinicalMode) {
                        vm.lastBitmap?.let { bmp ->
                            val rel = com.woundmeasurement.app.data.store.GalleryExport
                                .saveForQuickMeasure(ctx, bmp, source)
                            if (rel != null) galleryPath = rel
                        }
                    }
                    // 存檔同樣受①照護同意管:載入影像後才去撤回同意的話,
                    // 只擋「載入」那一步是攔不住的(影像已經在手上了)。
                    if (clinicalMode && !careOk)
                        vm.reportSubmitBlocked("⚠️ 此病患未取得①照護同意(或已撤回),不得存入病歷")
                    else vm.saveToTimeline(dao, exudate, case, source, imageStore)
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
                    case == null -> "⚠ 請先選擇個案傷口"
                    // 載入完成前不要指控「未取得同意」——擋住是對的,但訊息是假的
                    !consentLoaded -> "同意狀態載入中…"
                    else -> "⚠ 此病患尚未取得①照護同意（或已撤回），不得進行量測。請至個案管理簽署。"
                },
                // 辨識參照圖：原圖 ＋ 傷口輪廓 ＋ 組織分區 ＋ ArUco 校正框。
                //
                // 位置在**量測數值之下、滲液輸入之上**（見 MeasureScreen 的 preview 插槽說明）：
                // 面積與 PUSH 完全依賴 ArUco 認對了貼紙、分割抓對了範圍，而兩者出錯時
                // 系統都不會有任何警告。複核的機會要緊接在數字後面。
                preview = {
                    vm.lastBitmap?.let { bmp ->
                        AnalysisPreview(
                            bitmap = bmp,
                            polygon = vm.lastPolygon,
                            markerQuad = vm.lastMarkerQuad,
                            mmPerPx = vm.lastMmPerPx,
                            calibMethod = vm.lastCalibMethod,
                            modifier = Modifier.fillMaxWidth()
                        )
                    }
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
        if (!caseOk) Text("⚠ 臨床樣本需先選個案傷口(代碼要穩定,回診才串得起來)",
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
