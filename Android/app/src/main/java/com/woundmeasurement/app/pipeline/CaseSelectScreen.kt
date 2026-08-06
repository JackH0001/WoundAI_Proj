package com.woundmeasurement.app.pipeline

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.woundmeasurement.app.data.crypto.PhiCrypto
import com.woundmeasurement.app.data.entity.ConsentEntity
import com.woundmeasurement.app.data.entity.PatientEntity
import com.woundmeasurement.app.data.entity.WoundCaseEntity
import com.woundmeasurement.app.data.repo.CaseRepository
import kotlinx.coroutines.launch

/**
 * 個案管理（最小可收案版）：病患 → 知情同意 → 傷口個案 → 量測。
 *
 * 設計重點（見 `docs/sprint_N1_case_management_design.md`）：
 *  - 清單只顯示**遮蔽後**的病歷號（`A12***789`），不把完整 PII 攤在螢幕上。
 *  - 未取得①照護同意 → 個案與量測入口一律停用（不是提示而已，是真的按不下去）。
 *  - 每個傷口一個個案、一組**穩定** WD-code；量測結果依 caseId 分組，
 *    這樣時間軸才畫得出單一傷口的癒合曲線。
 */
@Composable
fun CaseSelectScreen(
    repo: CaseRepository,
    /** 選定個案。**刻意不回傳同意**:同意是閘門條件,由使用端從 DB 重讀,傳遞只會多一份過期快照。 */
    onCaseChosen: (WoundCaseEntity) -> Unit,
    onBack: () -> Unit = {},
    /** 開啟該個案的時間軸（只畫這個傷口的癒合曲線）。null 則不顯示時間軸按鈕。 */
    onTimeline: ((WoundCaseEntity) -> Unit)? = null
) {
    val scope = rememberCoroutineScope()
    var patients by remember { mutableStateOf<List<PatientEntity>>(emptyList()) }
    var selected by remember { mutableStateOf<PatientEntity?>(null) }
    var consent by remember { mutableStateOf<ConsentEntity?>(null) }
    var cases by remember { mutableStateOf<List<WoundCaseEntity>>(emptyList()) }
    // 個案摘要(上次面積/變化%/天數):醫護真正要的決策資訊,只有部位＋代碼看不出傷口在不在癒合
    var summaries by remember { mutableStateOf<Map<Long, CaseRepository.CaseSummary>>(emptyMap()) }
    var signing by remember { mutableStateOf(false) }
    var msg by remember { mutableStateOf<String?>(null) }
    /**
     * 目前選取的個案傷口。
     *
     * 先前點一下傷口就**直接跳到量測**，於是「選取」這個狀態根本不存在——
     * 時間軸只能靠底下一個小小的文字按鈕進入，而且新增表單永遠佔著版面。
     * 改成兩段式（選取 → 決定要量測還是看時間軸）之後，畫面在選取時可以收起新增表單，
     * 而且「這個傷口我要做什麼」變成一個明確的選擇，不是靠按到哪一顆按鈕決定。
     */
    var selectedCaseId by remember { mutableStateOf<Long?>(null) }

    val ctx = androidx.compose.ui.platform.LocalContext.current
    /** 尚未同步到雲端的撤回。沒有完成的撤回必須**一直看得見**，不能只閃一次訊息。 */
    var pendingWd by remember { mutableStateOf<String?>(null) }
    // 新增病患欄位
    var newName by remember { mutableStateOf("") }
    var newMrn by remember { mutableStateOf("") }
    // 新增個案欄位
    var site by remember { mutableStateOf("") }
    var wtype by remember { mutableStateOf("") }

    suspend fun reloadPatient(p: PatientEntity) {
        // 換病患一定要清掉傷口選取，否則畫面會停在上一位病患的傷口摘要上——
        // 那種錯誤在臨床上很危險：看起來像是這位病患的資料。
        if (selected?.id != p.id) selectedCaseId = null
        selected = p
        consent = repo.activeConsent(p.id)
        cases = repo.openCases(p.id)
        summaries = cases.associate { it.id to repo.caseSummary(it.id) }
    }

    LaunchedEffect(Unit) {
        // Keystore 失效/DB 損毀會從這裡拋,不攔會炸掉整個 App 且畫面無線索
        try { patients = repo.listPatients() } catch (e: Exception) { msg = "⚠ 讀取病患失敗:${e.message}" }
        // 補做上次沒送成功的撤回。這裡是「使用者剛好有網路而且在等畫面」的時刻，
        // 補做不會被感知到；而沒有完成的撤回代表病患的資料還留在雲端訓練佇列裡。
        runCatching { ConsentWithdrawal.retryPending(ctx) }
        pendingWd = ConsentWithdrawal.pendingBanner(ctx)
    }

    // 返回鍵逐層退出:簽名 → 傷口選取 → 病患選取 → 才真的離開。
    // 全部一次退掉的話,使用者按返回只是想「取消選取」卻整個跳出個案管理,
    // 回來還要重走一次選病患與同意檢查。
    BackHandler(enabled = signing || selectedCaseId != null || selected != null) {
        when {
            signing -> signing = false
            selectedCaseId != null -> selectedCaseId = null
            else -> { selected = null; consent = null; cases = emptyList() }
        }
    }

    if (signing && selected != null) {
        ConsentSignatureScreen(
            patientLabel = "${selected!!.name}（${PhiCrypto.maskMrn(selected!!.medicalRecordNumber)}）",
            onCancel = { signing = false },
            onSigned = { care, train, png ->
                scope.launch {
                    // signConsent → PhiCrypto.encryptBytes 會主動拋(刻意的:不默默存明文簽名)
                    try {
                        repo.signConsent(selected!!.id, care, train, png)
                        reloadPatient(selected!!)
                        signing = false
                        val base = "✅ 同意書已簽署（照護${if (care) "✓" else "✗"}・訓練${if (train) "✓" else "✗"}）"
                        // ⚠ 勾了②訓練同意就必須對雲端**解除撤回封鎖**。
                        //
                        // 沒有這一步的話：App 顯示「訓練同意✓」、重新修邊都成功，
                        // 而每一次補送標註都被擋下並印出
                        //   「被守門擋下：代碼 WD-xxxx 已撤回訓練同意。
                        //     重新取得同意請先呼叫 /api/v1/consent/restore」
                        // ——那不是給臨床人員看的東西，而且他也沒有辦法自己呼叫它。
                        // 2026-08-07 實測到這個死局（test001）。
                        msg = if (!train) base else {
                            val codes = cases.mapNotNull { it.wdCode }
                            if (codes.isEmpty()) base else {
                                val r = ConsentWithdrawal.restoreOnBackend(ctx, codes)
                                if (r.allOk)
                                    "$base\n雲端已解除撤回封鎖：${r.done.joinToString("、")}"
                                else
                                    "$base\n⚠ 但雲端尚未解除封鎖：${r.pending.joinToString("、")}\n" +
                                    "這些代碼在雲端仍被標記為已撤回，**補送訓練標註會被擋下**。" +
                                    "已記錄待重試，下次連上後端時會自動再試。"
                            }
                        }
                        pendingWd = ConsentWithdrawal.pendingBanner(ctx)
                    } catch (e: Exception) { msg = "⚠ 簽署失敗:${e.message}"; signing = false }
                }
            }
        )
        return
    }

    Column(
        Modifier.fillMaxSize().padding(16.dp).verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(10.dp)
    ) {
        Text("個案管理", style = MaterialTheme.typography.titleLarge)
        msg?.let { Text(it, color = MaterialTheme.colorScheme.primary,
            style = MaterialTheme.typography.bodyMedium) }
        // 未完成的撤回：**常駐顯示直到真的完成**。
        // 一次性的訊息會被下一則蓋掉或被滑走，而這件事沒做完就等於同意書的承諾沒兌現。
        pendingWd?.let {
            Surface(color = MaterialTheme.colorScheme.errorContainer,
                shape = MaterialTheme.shapes.small, modifier = Modifier.fillMaxWidth()) {
                Column(Modifier.padding(10.dp)) {
                    Text(it, style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onErrorContainer)
                    TextButton(onClick = {
                        scope.launch {
                            val r = ConsentWithdrawal.retryPending(ctx)
                            pendingWd = ConsentWithdrawal.pendingBanner(ctx)
                            msg = if (r.allOk) "✅ 待重試的撤回已全部同步到雲端"
                                  else "⚠ 仍有 ${r.pending.size} 筆未完成，請確認網路與後端帳密"
                        }
                    }) { Text("立即重試") }
                }
            }
        }

        // ---------- 病患 ----------
        Text("病患", style = MaterialTheme.typography.titleSmall)
        patients.forEach { p ->
            val sel = selected?.id == p.id
            // 再點一次＝取消選取。沒有這個的話，選錯病患只能返回上一頁重進，
            // 而在臨床現場「退出再進」通常意味著要重走一次同意檢查。
            val toggle = {
                if (sel) { selected = null; consent = null; cases = emptyList(); selectedCaseId = null }
                else scope.launch { runCatching { reloadPatient(p) } }
                Unit
            }
            val label = "${p.name}  ${PhiCrypto.maskMrn(p.medicalRecordNumber)}" +
                        (if (sel) "   ✓" else "")
            if (sel) Button(toggle, Modifier.fillMaxWidth()) { Text(label) }
            else OutlinedButton(toggle, Modifier.fillMaxWidth()) { Text(label) }
        }
        if (patients.isEmpty()) Text("(尚無病患,請於下方新增)",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant)

        // 選定病患後收起新增表單：收案時的動作是「選既有病患」，
        // 而「新增」只在建檔那一次用得到。兩者同時攤在畫面上只是讓人多滑一段。
        if (selected == null) {
        OutlinedTextField(newName, { newName = it }, label = { Text("姓名") },
            singleLine = true, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(newMrn, { newMrn = it }, label = { Text("病歷號") },
            singleLine = true, modifier = Modifier.fillMaxWidth())
        Button(
            onClick = {
                scope.launch {
                    // PhiCrypto 加密失敗會拋例外(刻意的:絕不默默存明文 PHI),
                    // 這裡必須攔下來,否則 Keystore 異常會讓整個 App 閃退且畫面毫無線索
                    try {
                        val dup = repo.findByMrn(newMrn)
                        if (dup != null) { msg = "⚠ 病歷號已存在:${dup.name}"; reloadPatient(dup) }
                        else {
                            val np = repo.createPatient(newName.trim(), newMrn.trim())
                            patients = repo.listPatients(); reloadPatient(np)
                            msg = "✅ 已新增病患(姓名/病歷號已加密存於本機)"
                        }
                        newName = ""; newMrn = ""
                    } catch (e: Exception) {
                        msg = "⚠ 建立失敗(PHI 加密異常):${e.message}"
                    }
                }
            },
            enabled = newName.isNotBlank() && newMrn.isNotBlank(),
            modifier = Modifier.fillMaxWidth()
        ) { Text("＋新增病患") }
        Text("姓名與病歷號以裝置金鑰加密後存於本機,**不會上傳雲端**;雲端只看得到 WD-代碼。",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
        } else {
            Text("（已選定病患。再點一次可取消選取並新增其他病患）",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        }

        val p = selected
        if (p != null) {
        Divider()

        // ---------- 同意 ----------
        Text("知情同意", style = MaterialTheme.typography.titleSmall)
        val care = consent?.consentCare == true && consent?.withdrawnAt == null
        val train = consent?.trainEffective == true
        Text(
            when {
                consent == null -> "⚠ 尚未簽署同意書"
                consent!!.withdrawnAt != null -> "⚠ 已撤回(${consent!!.withdrawnAt})"
                else -> "照護${if (care) "✓" else "✗"}・訓練${if (train) "✓" else "✗"}(簽署於 ${consent!!.signedAt})"
            },
            color = if (care) MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.error,
            style = MaterialTheme.typography.bodySmall
        )
        OutlinedButton({ signing = true }, Modifier.fillMaxWidth()) {
            Text(if (consent == null) "簽署同意書" else "重新簽署 / 更新同意")
        }
        if (consent != null && train) {
            OutlinedButton(
                onClick = {
                    scope.launch {
                        try {
                        // 1) 本機立即生效。病患的撤回是**立即生效的權利**，
                        //    不能因為手機當下沒網路就拒絕他。
                        val codes = repo.withdrawTraining(p.id, "病患要求")
                        reloadPatient(p)
                        // 2) 雲端撤回。以前這裡只印一行「尚須對後端撤回」就結束了——
                        //    而 BackendClient.withdrawConsent() 根本沒有呼叫端。
                        //    結果是同意書寫著「撤回後不再納入後續訓練」，
                        //    而系統實際上做不到那件事。
                        msg = "已撤回訓練同意（本機）。正在同步到雲端…"
                        val r = ConsentWithdrawal.pushToBackend(ctx, codes)
                        msg = if (r.allOk)
                            "✅ 已撤回訓練同意，雲端也已排除：${r.done.joinToString("、")}\n" +
                            "這些代碼的影像已移入隔離區，不再進入任何訓練集。"
                        else
                            // 3) 失敗**不可以**顯示成功。誠實地說「本機已撤回、雲端尚未完成」，
                            //    至少有人會去處理；顯示「已撤回」則沒有人會再看它一眼。
                            "⚠ 本機已撤回，但雲端尚未完成：${r.pending.joinToString("、")}\n" +
                            "這些代碼的資料**目前仍在雲端訓練佇列中**。已記錄待重試，" +
                            "下次連上後端時會自動再試；也可請管理者到主控台手動撤回。"
                        pendingWd = ConsentWithdrawal.pendingBanner(ctx)
                        } catch (e: Exception) { msg = "⚠ 撤回失敗:${e.message}" }
                    }
                },
                modifier = Modifier.fillMaxWidth()
            ) { Text("撤回訓練同意") }
        }

        Divider()
        // ---------- 個案傷口 ----------
        Text("個案傷口", style = MaterialTheme.typography.titleSmall)
        if (!care) {
            Text("⚠ 需先取得①照護同意才能建立傷口與量測",
                color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall)
        }
        cases.forEach { c ->
            val s = summaries[c.id]
            val picked = selectedCaseId == c.id
            Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                // 點一下＝選取(不再直接跳量測)，再點一次＝取消選取。
                val toggleCase = {
                    selectedCaseId = if (picked) null else c.id
                }
                if (picked) Button(toggleCase, Modifier.fillMaxWidth(), enabled = care) {
                    Text("${c.bodySite}・${c.woundType}   ${c.wdCode}   ✓")
                } else OutlinedButton(toggleCase, Modifier.fillMaxWidth(), enabled = care) {
                    Text("${c.bodySite}・${c.woundType}   ${c.wdCode}")
                }
                // 決策資訊:上次面積、相對首次的變化(負=縮小=在癒合)、距上次天數
                Text(
                    if (s == null || s.count == 0) "  尚無量測"
                    else buildString {
                        append("  上次 ")
                        append(s.lastArea?.let { "%.2f cm²".format(it) } ?: "—")
                        s.changePct?.let { append("・較首次 %+.0f%%".format(it)) }
                        s.daysSinceLast?.let { append("・$it 天前") }
                        append("・共 ${s.count} 次")
                    },
                    style = MaterialTheme.typography.bodySmall,
                    color = when {
                        s?.changePct == null -> MaterialTheme.colorScheme.onSurfaceVariant
                        s.changePct!! < -10 -> MaterialTheme.colorScheme.primary       // 明顯縮小
                        s.changePct!! > 10 -> MaterialTheme.colorScheme.error          // 明顯變大
                        else -> MaterialTheme.colorScheme.onSurfaceVariant
                    }
                )
                // 選取後才展開動作。把「量測」與「看時間軸」並列成明確的選擇，
                // 而不是靠使用者記得哪一顆按鈕會跳去哪裡。
                if (picked) {
                    Button({ onCaseChosen(c) }, Modifier.fillMaxWidth(), enabled = care) {
                        Text("開始量測")
                    }
                    if (onTimeline != null) {
                        OutlinedButton({ onTimeline(c) }, Modifier.fillMaxWidth()) {
                            Text(if ((s?.count ?: 0) > 0) "查看此傷口時間軸（${s?.count} 次）"
                                 else "查看此傷口時間軸（尚無紀錄）")
                        }
                    }
                    Text("（再點一次上方傷口可取消選取，並顯示「新增個案傷口」）",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        }
        if (cases.isEmpty() && care) Text("(尚無開立中的傷口)",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant)

        // 選定傷口後收起新增表單。回診時的動作是「選既有傷口」，
        // 新增只在第一次建立時用得到——兩者同時攤開只是讓人多滑一段。
        if (selectedCaseId == null) {
        OutlinedTextField(site, { site = it }, label = { Text("部位(如 薦骨/右足跟)") },
            singleLine = true, enabled = care, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(wtype, { wtype = it }, label = { Text("類型(如 壓瘡/糖尿病足)") },
            singleLine = true, enabled = care, modifier = Modifier.fillMaxWidth())
        Button(
            onClick = {
                scope.launch {
                    // createCase 在 WD-code 連續碰撞時會主動拋
                    try {
                        val c = repo.createCase(p.id, site.trim(), wtype.trim())
                        reloadPatient(p)   // cases 與 summaries 同進同出,避免兩者不同步
                        site = ""; wtype = ""
                        msg = "✅ 已建立傷口 ${c.wdCode}(此代碼固定不變,回診沿用同一組)"
                    } catch (e: Exception) { msg = "⚠ 建立失敗:${e.message}" }
                }
            },
            enabled = care && site.isNotBlank() && wtype.isNotBlank(),
            modifier = Modifier.fillMaxWidth()
        ) { Text("＋新增個案傷口") }
        }
        }   // end if (p != null)

        Divider()
        OutlinedButton(onBack, Modifier.fillMaxWidth()) { Text("返回") }
    }
}
