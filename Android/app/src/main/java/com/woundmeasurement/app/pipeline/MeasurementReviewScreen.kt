package com.woundmeasurement.app.pipeline

import android.graphics.Bitmap
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.activity.compose.BackHandler
import com.woundmeasurement.app.data.database.WoundMeasurementDatabase
import com.woundmeasurement.app.data.entity.MeasurementEntity
import com.woundmeasurement.app.data.repo.CaseRepository
import com.woundmeasurement.app.data.store.AppSettings
import com.woundmeasurement.app.data.store.LocalImageStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * 從時間軸回頭檢視單筆紀錄：**重新修邊** 與 **補送訓練標註**，都不必重測一遍。
 *
 * 這解決的問題：v2 的時間軸只存面積與 `imageId`，沒存 GT 輪廓，也沒存影像
 * （`imagePath` 一直是空字串）→ 病患事後才簽②訓練同意時，醫師得整個重新拍照量測。
 * v3 起輪廓（`gtPolygon`）與加密影像都落地，這頁就能直接把它們載回來。
 *
 * 補送標註**不需要重新上傳影像**：後端靠 `imageId` 就找得到當初 classify 存下的那張。
 */
/**
 * 讀取②訓練同意的**當下真值**。
 *
 * 刻意寫成頂層函式而非畫面內的區域函式：這個值必須在「進畫面時」與「按下送出時」各讀一次，
 * 兩處共用同一份邏輯才不會有一邊忘了重讀。畫面停留期間病患隨時可能撤回同意，
 * 只信進來時的快照等於閘門有洞。
 */
private suspend fun readTrainConsent(repo: CaseRepository, caseId: Long?): Boolean {
    val cid = caseId ?: return false
    return withContext(Dispatchers.IO) {
        runCatching { repo.getCase(cid)?.let { repo.canSubmitTraining(it.patientId) } ?: false }
            .getOrDefault(false)
    }
}

@Composable
fun MeasurementReviewScreen(
    record: MeasurementEntity,
    /** null＝從設定讀（正常路徑）。硬編碼 10.0.2.2 只在模擬器成立，真機補送標註會全數失敗。 */
    backendBaseUrl: String? = null,
    onBack: () -> Unit
) {
    val ctx = LocalContext.current
    val db = remember { WoundMeasurementDatabase.getDatabase(ctx) }
    val dao = remember { db.measurementDao() }
    val repo = remember { CaseRepository.from(db) }
    val store = remember { LocalImageStore(ctx) }
    val baseUrl = remember(backendBaseUrl) { backendBaseUrl ?: AppSettings.backendUrl(ctx) }
    val backend = remember(baseUrl) { BackendClient(baseUrl) }
    val scope = rememberCoroutineScope()

    var bmp by remember { mutableStateOf<Bitmap?>(null) }
    var loading by remember { mutableStateOf(true) }
    var editing by remember { mutableStateOf(false) }
    var msg by remember { mutableStateOf<String?>(null) }
    var cur by remember { mutableStateOf(record) }
    var trainOk by remember { mutableStateOf(false) }
    var loggedIn by remember { mutableStateOf(false) }
    /** 送出前的人工確認彈窗。資料離開手機是不可逆動作，不採「按了就送」。 */
    var confirmSubmit by remember { mutableStateOf(false) }

    // 輪廓 JSON → 多輪廓（座標空間＝imageW×imageH，也就是存下來的那張 work 影像）。
    //
    // ⚠ 一定要用 PolygonJson 解析。舊寫法直接 `a.getJSONArray(i)` 當成點讀，
    // 碰到多輪廓格式 `[[[x,y],...],[[x,y],...]]` 會把整個**輪廓**當成一個點，
    // getInt(0) 拿到的是 JSONArray → 例外 → runCatching 吞掉 → 輪廓變空，
    // 而畫面只會顯示「沒有 GT 輪廓，不能補送」。
    val polys = remember(cur.gtPolygon) { parsePolygons(cur.gtPolygon) }
    val poly = remember(polys) { PolygonJson.largest(polys) }

    /**
     * 影像尺寸與 `gtPolygon` 座標空間是否一致。
     *
     * ⚠ 這是**必要的防線**,不是保險。曾有一個路徑會產生「檔案是相機原圖、輪廓卻是 2048 work 空間」
     * 的紀錄(同一張照片先端上後後端;已於 MeasureViewModel 修掉根因)。這種列如果放行修邊:
     * 輪廓會縮在左上角(醫師只會覺得 AI 框錯),而重算面積用的是 work 空間的 mmPerPx 乘原圖像素數,
     * 面積會被**靜默**高估數倍並寫進癒合趨勢。寧可擋下並說清楚,也不要產生一個看起來正常的錯數字。
     */
    var spaceMismatch by remember { mutableStateOf<String?>(null) }
    /**
     * 上次修邊的柵格快照（v6）。**這是修掉兩個資料損失 bug 的關鍵。**
     *
     * 舊版這裡固定傳 `resume = null`，柵格由多邊形重建，後果是：
     *   · 醫師畫的組織分區整批消失（多邊形不含組織資訊）
     *   · 面積每進出一次漂移約 0.5%（RDP 簡化 + 重新柵格化是有損往返）
     *
     * 有快照就原樣載回：像素數不變 → 面積不變，組織分區也還在。
     * 載不到（舊紀錄、畫布尺寸變了）才退回重建——那是降級，不是預設。
     */
    var resumeRaster by remember { mutableStateOf<EditRaster?>(null) }

    LaunchedEffect(cur.id) {
        loading = true
        val b = withContext(Dispatchers.IO) { runCatching { store.loadFull(cur.imagePath) }.getOrNull() }
        spaceMismatch = when {
            b == null -> null
            cur.imageW == null || cur.imageH == null -> null   // 沒有宣告座標空間,不做比對
            b.width != cur.imageW || b.height != cur.imageH ->
                "影像 ${b.width}×${b.height} 與輪廓座標空間 ${cur.imageW}×${cur.imageH} 不一致"
            else -> null
        }
        bmp = b
        resumeRaster = if (b == null) null else withContext(Dispatchers.IO) {
            runCatching {
                EditRasterCodec.decode(
                    store.rawBytes(cur.rasterPath), cur.rasterMeta, b.width, b.height)
            }.getOrNull()
        }
        trainOk = readTrainConsent(repo, cur.caseId)
        // 憑證來自「設定」頁（Keystore 加密存本機），不再硬編碼 admin/woundai-admin。
        val u = AppSettings.backendUser(ctx)
        val p = AppSettings.backendPassword(ctx)
        loggedIn = if (u.isBlank() || p.isBlank()) false else withContext(Dispatchers.IO) {
            runCatching { backend.login(u, p) }.getOrDefault(false)
        }
        loading = false
    }

    BackHandler(enabled = editing) { editing = false }

    val b = bmp
    if (editing && b != null) {
        WoundEditScreen(
            bitmap = b,
            initialPolygon = poly,
            // 從時間軸回頭修邊：DB 裡存的可能是多輪廓（見 PolygonJson）。
            initialPolygons = polys,
            originalArea = cur.estimatedArea,
            // v5 起組織比例有存下來。傳 emptyMap 會讓 defaultClass 退回 1（肉芽），
            // 而那是「AI 覺得最多的那一類」的猜測——有真值就不該用猜的。
            tissueFrac = mapOf(
                "granulation" to (cur.tissueGranulation ?: 0.0),
                "slough" to (cur.tissueSlough ?: 0.0),
                "necrosis" to (cur.tissueNecrosis ?: 0.0),
                "epithelial" to (cur.tissueEpithelial ?: 0.0),
                "other" to (cur.tissueOther ?: 0.0)
            ),
            exudate = cur.exudate,
            mmPerPx = cur.mmPerPx,
            // v6：有柵格快照就原樣載回（組織分區保留、面積不漂移）；
            // 載不到才退回由 polygon 重建——那條路徑仍然可用，只是有損。
            resume = resumeRaster,
            // 白平衡增益隨柵格一起存（見 EditRaster.wbGains）。這裡沒有後端回應可拿，
            // 拿不到就退回灰世界——底稿會與當初量測時的分區不同，
            // 而醫師會以為是自己上次標錯了，然後把對的改成錯的。
            wbGains = resumeRaster?.wbGains,
            onCancel = { editing = false },
            // ⚠ 這五個參數**全都要用**。曾經寫成 `{ newPoly, iou, newArea, _, _ ->` ——
            // 後兩個（組織比例、修邊柵格）被直接丟棄，造成三重資料損失，而且沒有任何錯誤：
            //   1. 柵格沒存 → 下次進來 decode 不到 → 退回由 polygon 重建 → 醫師的組織分區
            //      整批消失，畫面回到顏色啟發式的猜測（而醫師會以為是自己沒存到）
            //   2. tissue* 欄位沒更新 → 時間軸卡片與趨勢圖顯示的是**修邊前**的組織比例
            //   3. 沒有柵格就送不出組織遮罩 → 補送標註只送得出傷口輪廓，
            //      醫師花時間標的組織分區從來沒有進過訓練集
            // 三個後果都是安靜的。修邊當下看起來完全正常。
            onDone = { newPoly, allPolys, iou, newArea, tis, raster ->
                scope.launch {
                    // ⚠ 多處傷口時要存**全部**輪廓。只存 newPoly 的話，
                    // 第二個傷口在下次載回與補送標註時都會消失。
                    // 格式與 MeasureViewModel.polyJson 一致：單一輪廓存成點陣列（相容舊紀錄），
                    // 多輪廓存成「輪廓的陣列」，由 parsePolygons 兩種都吃。
                    val js = polygonsToJson(allPolys.ifEmpty {
                        if (newPoly.size >= 3) listOf(newPoly) else emptyList()
                    })
                    val oldArea = cur.estimatedArea
                    val finalArea = newArea ?: oldArea
                    // 面積變了,notes 裡的 PUSH 與組織%就不能原封不動留著——否則時間軸卡片會出現
                    // 「新面積 + 舊 PUSH + 舊組織%」並列的自相矛盾紀錄。
                    // 面積子分是純面積的函式,可以正確重算;組織比例本輪沒有落地(v3 未存 tissueFrac),
                    // 重算不了就**明講它是修邊前的值**,不要假裝它還成立。
                    val subOld = WoundPipeline.areaSubscore(oldArea)
                    val subNew = WoundPipeline.areaSubscore(finalArea)
                    // 柵格落地。存檔失敗不可讓整筆更新一起失敗——輪廓與面積仍該存下來，
                    // 但也**不可**假裝成功：rasterName 為 null 時下面會沿用舊值，
                    // 而畫面訊息會明說組織分區這次沒存到。
                    val rasterPair = raster?.let { runCatching { EditRasterCodec.encode(it) }.getOrNull() }
                    val rasterName = rasterPair?.let {
                        runCatching { withContext(Dispatchers.IO) { store.save(it.first) } }.getOrNull()
                    }
                    val stamp = SimpleDateFormat("MM/dd HH:mm", Locale.getDefault()).format(Date())
                    // ⚠ 組織比例要寫進修訂行。
                    //
                    // v5 起 tissue* 欄位在下面**確實會更新**（趨勢圖的柱狀圖讀的就是它們），
                    // 但時間軸卡片顯示的是 notes 字串，而 notes 的開頭那行是第一次量測時寫的。
                    // 舊版這裡還加了一句「上列組織比例為修邊前之值」——在欄位真的會更新之後，
                    // 那句話變成**錯的**，而且卡片上會同時出現兩組互相矛盾的數字：
                    // 文字說肉芽 61%、柱狀圖畫 56%，醫師不知道該信哪個。
                    fun pct(k: String) = ((tis[k] ?: 0.0) * 100).toInt()
                    val tisTxt = "組織 肉芽${pct("granulation")}% 腐肉${pct("slough")}% " +
                        "壞死${pct("necrosis")}% 上皮${pct("epithelial")}% 其他${pct("other")}%"
                    val revision = "⟳ $stamp 醫師重新修邊:面積 " +
                        (oldArea?.let { "%.2f".format(it) } ?: "—") + "→" +
                        (finalArea?.let { "%.2f".format(it) } ?: "—") + " cm²" +
                        (if (subOld != null && subNew != null) "(PUSH面積子分 $subOld→$subNew)" else "") +
                        (iou?.let { "; 本次相對前版 IoU %.2f".format(it) } ?: "") +
                        "; " + tisTxt +
                        "。**以本行為準**——最上方那行是首次量測的數值"
                    val updated = cur.copy(
                        gtPolygon = js,
                        estimatedArea = finalArea,
                        // AI 空手、醫師從零畫出傷口的情形:hasWound 原本會停在 false
                        hasWound = (finalArea ?: 0.0) > 0.0 || cur.hasWound,
                        // 走到這裡代表醫師按了「完成修邊」（取消不會呼叫 onDone）
                        doctorVerified = true,
                        // ⚠ correctionIou **刻意不覆寫**。它的定義是「與 AI 原始遮罩的 IoU」,
                        // 是評估模型修正幅度的指標並會送進飛輪(BackendClient correction_iou)。
                        // 從本畫面進來時,修邊的起點是**已經修過的 GT** 而非 AI 原圖遮罩,
                        // 覆寫上去會讓這個指標系統性趨近 1.0,看起來像「模型幾乎不用修」——失真的自我評分。
                        // 本次相對前版的 IoU 改記在 notes,保留可追溯性但不污染指標。
                        notes = listOfNotNull(cur.notes?.takeIf { it.isNotBlank() }, revision).joinToString("\n"),
                        // 輪廓改了就不能再算「已送出」——要重新送才會讓雲端拿到新 GT
                        annotationSubmitted = false,
                        // v6：柵格與 meta 必須**成對**更新。只更新其中一個，
                        // 座標就會對到錯的影像，而載回來時看起來只是「遮罩位置怪怪的」。
                        rasterPath = rasterName ?: cur.rasterPath,
                        rasterMeta = if (rasterName != null) rasterPair?.second else cur.rasterMeta,
                        // v5：組織比例。原始影像會依 90 天政策清除，沒有現在存下來就永遠補不回來。
                        tissueGranulation = tis["granulation"] ?: cur.tissueGranulation,
                        tissueSlough = tis["slough"] ?: cur.tissueSlough,
                        tissueNecrosis = tis["necrosis"] ?: cur.tissueNecrosis,
                        tissueEpithelial = tis["epithelial"] ?: cur.tissueEpithelial,
                        tissueOther = tis["other"] ?: cur.tissueOther
                    )
                    runCatching { withContext(Dispatchers.IO) { dao.updateMeasurement(updated) } }
                        .onSuccess {
                            // 先寫 DB 再刪舊檔。反過來的話 update 失敗就留下指向不存在檔案的死路徑，
                            // 而那個路徑會讓下次進來 decode 失敗——又退回顏色啟發式。
                            if (rasterName != null && !cur.rasterPath.isNullOrEmpty()
                                && cur.rasterPath != rasterName) {
                                runCatching { withContext(Dispatchers.IO) { store.delete(cur.rasterPath!!) } }
                            }
                            cur = updated
                            // 記憶體中的快照也要換掉。不換的話**不離開這頁**再按一次「重新修邊」，
                            // 載回的還是上一輪的柵格——剛畫的又不見了，而且只在這條路徑上發生。
                            resumeRaster = raster ?: resumeRaster
                            msg = "✅ 已更新此筆紀錄的輪廓與面積\n" +
                                  "面積 " + (oldArea?.let { "%.2f".format(it) } ?: "—") + " → " +
                                  (finalArea?.let { "%.2f".format(it) } ?: "—") + " cm²。\n" +
                                  (if (raster != null && rasterName == null)
                                      "⚠️ 但組織分區**未能存檔**，下次進來會退回 AI 的猜測。\n" else "") +
                                  "輪廓已變更，此筆需**重新送出**才會讓雲端拿到新的 GT。"
                        }
                        .onFailure { msg = "⚠️ 更新失敗：${it.message}" }
                    editing = false
                }
            }
        )
        return
    }

    Column(
        Modifier.fillMaxSize().padding(16.dp).verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(10.dp)
    ) {
        Text("紀錄檢視 / 補送標註", style = MaterialTheme.typography.titleLarge)
        Text("代碼 ${cur.wdCode ?: "—"}・面積 ${cur.estimatedArea?.let { "%.2f cm²".format(it) } ?: "未校正"}" +
             "・滲液 ${cur.exudate ?: "—"}",
            style = MaterialTheme.typography.bodyMedium)
        msg?.let { Text(it, color = MaterialTheme.colorScheme.primary) }

        if (loading) Text("載入影像中…", style = MaterialTheme.typography.bodySmall)

        val noImage = !loading && bmp == null
        if (noImage) Text(
            // v3 之前的舊紀錄沒有影像；或保存期限清理已把影像刪掉（面積與趨勢仍保留）
            "⚠ 此筆沒有本機影像（可能是舊紀錄，或已逾保存期限清理）。無法重新修邊；" +
            if (cur.gtPolygon != null && cur.imageId != null) "但輪廓與影像綁定都在，仍可補送標註。"
            else "也缺輪廓或影像綁定，無法補送標註。",
            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)

        spaceMismatch?.let {
            Text("⚠ 影像與輪廓座標空間不符（$it），已停用重新修邊。\n" +
                 "在此修邊會得到錯誤的面積且不會有任何警告。此筆請重新量測一次；" +
                 "既有面積數值仍可作為病歷保留。",
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
        }

        Button(
            onClick = { editing = true },
            enabled = bmp != null && spaceMismatch == null,
            modifier = Modifier.fillMaxWidth()
        ) { Text("重新修邊（載回原影像與輪廓）") }

        Divider()
        Text("補送訓練標註", style = MaterialTheme.typography.titleSmall)
        Text(
            when {
                cur.annotationSubmitted -> "此筆已送出過訓練標註。重新修邊後可再送（雲端會視為醫師修訂版）。"
                cur.gtPolygon == null -> "⚠ 此筆沒有 GT 輪廓，不能補送（請先重新修邊）。"
                cur.imageId == null -> "⚠ 此筆沒有後端影像綁定（當初可能走端上模式），不能補送。"
                !cur.doctorVerified -> "⚠ 此筆未經醫師完成修邊確認，不得送訓練標註。\n" +
                    "請先按「重新修邊」並完成（按取消不算確認）。舊版紀錄一律視為未確認。"
                !trainOk -> "⚠ 此病患未取得②訓練同意（或已撤回），不得送出。"
                !loggedIn -> "⚠ 後端未連線或尚未設定帳密 — 請到主畫面「設定」填入後端位址與帳號密碼（目前：$baseUrl）。"
                else -> "可補送：後端靠 image_id 就找得到當初的影像，**不需要重新上傳或重測**。"
            },
            style = MaterialTheme.typography.bodySmall,
            color = if (cur.gtPolygon != null && cur.imageId != null && trainOk && loggedIn)
                MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.error
        )
        Button(
            onClick = { confirmSubmit = true },
            enabled = !cur.annotationSubmitted && cur.gtPolygon != null && cur.imageId != null &&
                      cur.wdCode != null && cur.doctorVerified && trainOk && loggedIn,
            modifier = Modifier.fillMaxWidth()
        ) { Text("補送訓練標註 → 再訓練佇列") }

        Divider()
        OutlinedButton(onBack, Modifier.fillMaxWidth()) { Text("返回時間軸") }
    }

    // ---- 送出前的人工確認 ----
    //
    // 這一步是**資料離開這台手機**進入訓練集,是整條流程裡最不可逆的動作(雲端佇列 append-only,
    // 事後只能靠撤回排除,無法真的收回)。因此不採「按了就送、事後看一行綠字」——
    // 而是把**實際會離開手機的每一個欄位**攤開來,讓醫師逐項確認後才送。
    // 同時明確標示 PII 不在其中:這是雙層同意書對病患的承諾,醫師要能當場看見它成立。
    if (confirmSubmit) AlertDialog(
        onDismissRequest = { confirmSubmit = false },
        title = { Text("確認送出訓練標註？") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text("以下內容將離開本機、進入雲端再訓練佇列：", style = MaterialTheme.typography.bodyMedium)
                Text(
                    "· 去識別代碼　${cur.wdCode}\n" +
                    "· 影像綁定　　${cur.imageId}（後端既有，不重新上傳）\n" +
                    "· 傷口輪廓　　" + (if (polys.size > 1) "${polys.size} 處傷口・共 ${polys.sumOf { it.size }} 個點"
                                       else "${poly.size} 個點") + " @ ${cur.imageW}×${cur.imageH}\n" +
                    "· 面積　　　　${cur.estimatedArea?.let { "%.2f cm²".format(it) } ?: "未校正"}\n" +
                    "· 滲液　　　　${cur.exudate ?: "—"}\n" +
                    "· 樣本來源　　${cur.source ?: "clinical"}",
                    style = MaterialTheme.typography.bodySmall
                )
                // 組織遮罩到底有沒有跟著送,醫師要能在按下去之前看見。
                // 只顯示「已送出」而不說送了什麼,等於要人相信一個看不見的結果。
                val rr = resumeRaster
                val edPx = rr?.tissueEditedPx ?: 0
                Text(
                    when {
                        rr == null -> "· 組織遮罩　　✗ 不送（本筆沒有修邊柵格，請先「重新修邊」）"
                        edPx <= 0 -> "· 組織遮罩　　△ 會送出，但標記為「未經醫師修正」\n" +
                                     "　　　　　　　→ 不會進入組織分割訓練集（未修正的遮罩是 AI 自己的輸出）"
                        else -> "· 組織遮罩　　✓ 含醫師修正 ${edPx} 像素，會進入組織分割訓練集"
                    },
                    style = MaterialTheme.typography.bodySmall,
                    color = if (rr != null && edPx > 0) MaterialTheme.colorScheme.primary
                            else MaterialTheme.colorScheme.error
                )
                Text("✓ 姓名、病歷號等個資不在其中，且永不離開本機。",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.primary)
                if ((cur.source ?: "clinical") == "clinical") Text(
                    "⚠ 來源為「臨床」：這筆會計入臨床收案進度。若這其實是範例／模擬圖，請取消。",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error)
            }
        },
        confirmButton = {
            TextButton({
                confirmSubmit = false
                scope.launch {
                    // ⚠ 同意真值必須在**按下的當下重讀**,不可用進畫面時的快照,更不可硬編碼 true。
                    // 硬編碼 true 等於每筆送出都謊稱已取得訓練同意——這正是 BackendClient 註解裡
                    // 記載過的法規級缺陷,不能在新畫面重蹈。閘門要放在每一個會產生資料的動作上。
                    val okNow = readTrainConsent(repo, cur.caseId)
                    trainOk = okNow
                    if (!okNow) {
                        msg = "⚠️ 此病患目前無有效的②訓練同意（可能剛撤回），已停止送出"
                        return@launch
                    }
                    val res = runCatching {
                        withContext(Dispatchers.IO) {
                            backend.submitAnnotation(
                                code = cur.wdCode!!, gtPolygon = poly, exudate = cur.exudate,
                                imageId = cur.imageId, imageW = cur.imageW ?: 0, imageH = cur.imageH ?: 0,
                                // 多處傷口：只送 poly 的話其餘傷口在訓練集裡是背景。
                                allPolygons = polys,
                                // 面積以本機紀錄為真值（來自遮罩像素數），不讓後端由多邊形反算。
                                areaCm2 = cur.estimatedArea,
                                mmPerPx = cur.mmPerPx, route = cur.route, segModel = null,
                                correctionIou = cur.correctionIou, careNote = "resubmit from timeline",
                                source = cur.source ?: "clinical", consentTrain = okNow,
                                // 組織比例與分割 GT。這裡曾經**完全沒送**——醫師從時間軸修好的
                                // 組織分區永遠進不了訓練集，而畫面照樣顯示「✅ 已補送」。
                                // 失敗方向是安全的（沒送假 GT），但等於白做。
                                tissueFrac = mapOf(
                                    "granulation" to (cur.tissueGranulation ?: 0.0),
                                    "slough" to (cur.tissueSlough ?: 0.0),
                                    "necrosis" to (cur.tissueNecrosis ?: 0.0),
                                    "epithelial" to (cur.tissueEpithelial ?: 0.0),
                                    "other" to (cur.tissueOther ?: 0.0)
                                ).takeIf { m -> m.values.any { it > 0.0 } },
                                // 沒有柵格就整組不送。硬用輪廓補一張遮罩出來只會製造假 GT，
                                // 而 tissue_edited 由 BackendClient 從柵格自己算——
                                // 未經醫師修正的啟發式輸出後端會擋在訓練集外。
                                tissueMaskPng = resumeRaster?.let {
                                    TissueMaskCodec.encode(it.tissue, it.mask, it.mw, it.mh)
                                },
                                tissueRaster = resumeRaster,
                                // 真值來自本機紀錄。v3 以前的舊紀錄一律 false（當時系統沒記錄
                                // 醫師是否確認過，填 true 等於憑空捏造一個驗證事實）。
                                doctorVerified = cur.doctorVerified
                            )
                        }
                    }
                    res.onSuccess { (ok, m) ->
                        if (ok) {
                            // 本機標記失敗時**不可**假裝成功:畫面顯示「已送出」而 DB 沒記,
                            // 重開這頁又變成可補送,醫師會以為系統壞了(雲端會去重,但人不知道)。
                            val marked = runCatching {
                                withContext(Dispatchers.IO) { dao.markAnnotationSubmitted(cur.id) }
                            }.isSuccess
                            if (marked) cur = cur.copy(annotationSubmitted = true)
                            val base = if (m.contains("duplicate")) "ℹ️ 相同影像的相同遮罩已在佇列（去重），未重複新增"
                                       else "✅ 已補送訓練標註（${cur.wdCode}）\n本筆已進入雲端再訓練佇列。"
                            msg = if (marked) base
                                  else "$base\n⚠️ 但本機狀態未更新成功，此筆仍會顯示為「可補送」"
                        } else msg = "⚠️ 被守門擋下：$m"
                    }.onFailure { msg = "⚠️ 送出失敗：${it.message}" }
                }
            }) { Text("確認送出") }
        },
        dismissButton = { TextButton({ confirmSubmit = false }) { Text("取消") } }
    )

    // ---- 結果彈窗 ----
    //
    // 原本結果只是頁面上一行綠字,實測反映**容易錯過**(醫師的視線在按鈕上,訊息在頁首)。
    // 送出訓練標註與更新病歷都是需要人「知道它發生了」的動作,所以改成必須按確認才關閉。
    //
    // 用**獨立於 msg 的 dlg 狀態**:兩者共用一個變數的話,關掉彈窗會連頁面上那行紀錄一起清掉,
    // 醫師事後想再看一眼就沒了。彈窗負責「強制知悉」,頁面那行負責「留在畫面上可回看」。
    // seen 用來避免同一則訊息在重組時反覆彈出。
    // 只攔 ✅/ℹ️/⚠️ 開頭的重要狀態,「載入影像中…」之類的過場不彈。
    var dlg by remember { mutableStateOf<String?>(null) }
    var seen by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(msg) {
        val m = msg
        if (m != null && m != seen && (m.startsWith("✅") || m.startsWith("ℹ️") || m.startsWith("⚠️"))) {
            dlg = m; seen = m
        }
    }
    dlg?.let { m ->
        AlertDialog(
            onDismissRequest = { dlg = null },
            title = { Text(if (m.startsWith("⚠️")) "注意" else "完成") },
            text = { Text(m) },
            confirmButton = { TextButton({ dlg = null }) { Text("確認") } }
        )
    }
}
