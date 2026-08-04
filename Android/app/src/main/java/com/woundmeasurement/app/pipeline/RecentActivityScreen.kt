package com.woundmeasurement.app.pipeline

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.woundmeasurement.app.data.entity.WoundCaseEntity
import com.woundmeasurement.app.data.repo.CaseRepository

/**
 * 最近就診（主畫面用）。
 *
 * 這是把舊「查看歷史紀錄」拆開後留在主畫面的那一半：
 *  - **趨勢分析**（面積折線）→ 已移進個案內（`WoundTimelineScreen(caseId=…)`），
 *    因為把不同病患、不同部位的傷口畫成一條線，是會誤導臨床判斷的圖表。
 *  - **最近就診**（誰最近量過、哪個個案久未追蹤）→ 留在這裡，但它是**導覽與提醒**，
 *    所以只給清單與跳轉，**不畫任何趨勢圖**。
 *
 * 回診情境：開 App → 最近就診 → 點該個案 → 直接接續量測（兩步）。
 *
 * ⚠ **同意閘門在這裡同樣要生效**：這是個案清單以外的第二個量測入口，
 * 若只在個案清單擋 `consentCare`，這條路就成了繞道。
 */
@Composable
fun RecentActivityScreen(
    repo: CaseRepository,
    onOpenCase: (WoundCaseEntity) -> Unit,
    onTimeline: (WoundCaseEntity) -> Unit,
    onBack: () -> Unit = {}
) {
    var rows by remember { mutableStateOf<List<CaseRepository.RecentRow>>(emptyList()) }
    var loaded by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }

    LaunchedEffect(Unit) {
        // 缺 migration / DB 損毀 / Keystore 失效都會從這裡拋出來；
        // 不攔的話會沿 composition 往上炸掉整個 App，而且畫面上沒有任何線索。
        try {
            rows = repo.recentRows(10)
        } catch (e: Exception) {
            error = "讀取失敗：${e.message}"
        }
        loaded = true
    }

    Column(
        Modifier.fillMaxSize().padding(16.dp).verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(10.dp)
    ) {
        Text("最近就診", style = MaterialTheme.typography.titleLarge)
        Text("依最後量測時間排序（已結案者不列出）。點個案即可接續量測；趨勢圖在個案的時間軸內。",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
        Divider()

        error?.let {
            Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodyMedium)
        }
        if (loaded && error == null && rows.isEmpty()) {
            Text("尚無量測紀錄。請由主畫面「個案」建立病患與個案傷口後開始量測。",
                style = MaterialTheme.typography.bodyMedium)
        }

        rows.forEach { r ->
            val s = r.summary
            Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                // 病歷號遮蔽後顯示:同病房兩位病患都可能是「薦骨・壓瘡」,
                // 而 WD-code 是隨機碼肉眼不可辨——沒有辨識線索就會量錯個案。
                Button(
                    onClick = { onOpenCase(r.case) },
                    enabled = r.canMeasure,
                    modifier = Modifier.fillMaxWidth()
                ) { Text("${r.patientHint}  ${r.case.bodySite}・${r.case.woundType}") }
                Text("  ${r.case.wdCode}", style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)

                if (!r.canMeasure) Text("  ⚠ 未取得①照護同意（或已撤回），不得量測",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error)

                Text(
                    buildString {
                        append("  上次 ")
                        append(s.lastArea?.let { "%.2f cm²".format(it) } ?: "—")
                        s.changePct?.let { append("・較首次 %+.0f%%".format(it)) }
                        s.daysSinceLast?.let { append("・$it 天前") }
                        append("・共 ${s.count} 次")
                    },
                    style = MaterialTheme.typography.bodySmall,
                    color = when {
                        s.changePct == null -> MaterialTheme.colorScheme.onSurfaceVariant
                        s.changePct!! < -10 -> MaterialTheme.colorScheme.primary
                        s.changePct!! > 10 -> MaterialTheme.colorScheme.error
                        else -> MaterialTheme.colorScheme.onSurfaceVariant
                    }
                )
                // 久未追蹤提醒:壓瘡/糖尿病足的照護週期通常以週計,超過兩週該回訪
                s.daysSinceLast?.let { d ->
                    if (d >= 14) Text("  ⚠ 已 $d 天未追蹤", style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.error)
                }
                TextButton({ onTimeline(r.case) }, Modifier.fillMaxWidth()) { Text("查看此傷口時間軸") }
            }
        }

        Divider()
        OutlinedButton(onBack, Modifier.fillMaxWidth()) { Text("返回") }
    }
}
