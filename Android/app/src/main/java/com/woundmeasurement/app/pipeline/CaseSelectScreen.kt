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

    // 新增病患欄位
    var newName by remember { mutableStateOf("") }
    var newMrn by remember { mutableStateOf("") }
    // 新增個案欄位
    var site by remember { mutableStateOf("") }
    var wtype by remember { mutableStateOf("") }

    suspend fun reloadPatient(p: PatientEntity) {
        selected = p
        consent = repo.activeConsent(p.id)
        cases = repo.openCases(p.id)
        summaries = cases.associate { it.id to repo.caseSummary(it.id) }
    }

    LaunchedEffect(Unit) {
        // Keystore 失效/DB 損毀會從這裡拋,不攔會炸掉整個 App 且畫面無線索
        try { patients = repo.listPatients() } catch (e: Exception) { msg = "⚠ 讀取病患失敗:${e.message}" }
    }

    // 簽名頁自己吃返回鍵:否則簽到一半按返回會直接跳出整個個案管理
    BackHandler(enabled = signing) { signing = false }

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
                        msg = "✅ 同意書已簽署（照護${if (care) "✓" else "✗"}・訓練${if (train) "✓" else "✗"}）"
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

        // ---------- 病患 ----------
        Text("病患", style = MaterialTheme.typography.titleSmall)
        patients.forEach { p ->
            val sel = selected?.id == p.id
            if (sel) Button({ scope.launch { runCatching { reloadPatient(p) } } }, Modifier.fillMaxWidth()) {
                Text("${p.name}  ${PhiCrypto.maskMrn(p.medicalRecordNumber)}")
            } else OutlinedButton({ scope.launch { runCatching { reloadPatient(p) } } }, Modifier.fillMaxWidth()) {
                Text("${p.name}  ${PhiCrypto.maskMrn(p.medicalRecordNumber)}")
            }
        }
        if (patients.isEmpty()) Text("(尚無病患,請於下方新增)",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant)

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
                        val codes = repo.withdrawTraining(p.id, "病患要求")
                        reloadPatient(p)
                        msg = "已撤回訓練同意。⚠ 尚須對後端撤回這些代碼:${codes.joinToString()}" +
                              "(本機留痕只是一半,雲端沒排除的話資料照樣會進訓練集)"
                        } catch (e: Exception) { msg = "⚠ 撤回失敗:${e.message}" }
                    }
                },
                modifier = Modifier.fillMaxWidth()
            ) { Text("撤回訓練同意") }
        }

        Divider()
        // ---------- 傷口個案 ----------
        Text("傷口個案", style = MaterialTheme.typography.titleSmall)
        if (!care) {
            Text("⚠ 需先取得①照護同意才能建立個案與量測",
                color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall)
        }
        cases.forEach { c ->
            val s = summaries[c.id]
            Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                OutlinedButton(
                    onClick = { onCaseChosen(c) },
                    enabled = care,
                    modifier = Modifier.fillMaxWidth()
                ) { Text("${c.bodySite}・${c.woundType}   ${c.wdCode}") }
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
                if (onTimeline != null && (s?.count ?: 0) > 0) {
                    TextButton({ onTimeline(c) }, Modifier.fillMaxWidth()) {
                        Text("查看此傷口時間軸")
                    }
                }
            }
        }
        if (cases.isEmpty() && care) Text("(尚無開立中的傷口個案)",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant)

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
                        msg = "✅ 已建立個案 ${c.wdCode}(此代碼固定不變,回診沿用同一組)"
                    } catch (e: Exception) { msg = "⚠ 建立個案失敗:${e.message}" }
                }
            },
            enabled = care && site.isNotBlank() && wtype.isNotBlank(),
            modifier = Modifier.fillMaxWidth()
        ) { Text("＋新增傷口個案") }
        }   // end if (p != null)

        Divider()
        OutlinedButton(onBack, Modifier.fillMaxWidth()) { Text("返回") }
    }
}
