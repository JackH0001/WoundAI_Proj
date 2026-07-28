package com.woundmeasurement.app.pipeline

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
    onCaseChosen: (WoundCaseEntity, ConsentEntity?) -> Unit,
    onBack: () -> Unit = {}
) {
    val scope = rememberCoroutineScope()
    var patients by remember { mutableStateOf<List<PatientEntity>>(emptyList()) }
    var selected by remember { mutableStateOf<PatientEntity?>(null) }
    var consent by remember { mutableStateOf<ConsentEntity?>(null) }
    var cases by remember { mutableStateOf<List<WoundCaseEntity>>(emptyList()) }
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
    }

    LaunchedEffect(Unit) { patients = repo.listPatients() }

    if (signing && selected != null) {
        ConsentSignatureScreen(
            patientLabel = "${selected!!.name}（${PhiCrypto.maskMrn(selected!!.medicalRecordNumber)}）",
            onCancel = { signing = false },
            onSigned = { care, train, png ->
                scope.launch {
                    repo.signConsent(selected!!.id, care, train, png)
                    reloadPatient(selected!!)
                    signing = false
                    msg = "✅ 同意書已簽署（照護${if (care) "✓" else "✗"}・訓練${if (train) "✓" else "✗"}）"
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
            if (sel) Button({ scope.launch { reloadPatient(p) } }, Modifier.fillMaxWidth()) {
                Text("${p.name}  ${PhiCrypto.maskMrn(p.medicalRecordNumber)}")
            } else OutlinedButton({ scope.launch { reloadPatient(p) } }, Modifier.fillMaxWidth()) {
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
                        val codes = repo.withdrawTraining(p.id, "病患要求")
                        reloadPatient(p)
                        msg = "已撤回訓練同意。⚠ 尚須對後端撤回這些代碼:${codes.joinToString()}" +
                              "(本機留痕只是一半,雲端沒排除的話資料照樣會進訓練集)"
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
            OutlinedButton(
                onClick = { onCaseChosen(c, consent) },
                enabled = care,
                modifier = Modifier.fillMaxWidth()
            ) { Text("${c.bodySite}・${c.woundType}   ${c.wdCode}") }
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
                    val c = repo.createCase(p.id, site.trim(), wtype.trim())
                    cases = repo.openCases(p.id); site = ""; wtype = ""
                    msg = "✅ 已建立個案 ${c.wdCode}(此代碼固定不變,回診沿用同一組)"
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
