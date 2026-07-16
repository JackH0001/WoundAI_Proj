package com.woundmeasurement.app.pipeline

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * 量測結果畫面(Jetpack Compose 骨架)：觀察 [MeasureViewModel] 狀態 → 顯示面積/組織/PUSH/信心度，
 * 並導向「醫師確認・修邊」或「存入時間軸」。輔助、非診斷、需醫師確認。
 */
@Composable
fun MeasureScreen(
    vm: MeasureViewModel,
    onReview: () -> Unit,
    onSaveToTimeline: () -> Unit,
    exudate: Int? = null,
    onExudate: ((Int) -> Unit)? = null   // 提供時:滲液顯示於結果卡下方,且未輸入前鎖定修邊/存檔(防呆)
) {
    val st by vm.state.collectAsState()
    Column(Modifier.fillMaxWidth().padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text("量測結果", fontSize = 20.sp, fontWeight = FontWeight.Bold)
        when {
            st.loading -> Box(Modifier.fillMaxWidth().padding(24.dp), Alignment.Center) { CircularProgressIndicator() }
            st.error != null -> Text("分析失敗：${st.error}", color = MaterialTheme.colorScheme.error)
            st.result != null -> {
                val r = st.result!!
                val g = pct(r.tissueFrac["granulation"]); val s = pct(r.tissueFrac["slough"]); val n = pct(r.tissueFrac["necrosis"])
                ElevatedCard(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        Text("面積：" + (r.areaCm2?.let { "%.2f cm²".format(it) } ?: "未校正(無貼紙)"),
                            fontSize = 18.sp, fontWeight = FontWeight.Bold)
                        Text("PUSH：" + (r.push.partial?.toString() ?: "-") +
                            (r.push.full?.let { "（含滲液 $it）" } ?: "（滲液待醫師輸入）"))
                        Text("組織：肉芽 $g · 腐肉 $s · 壞死 $n")
                        Text("路由：${r.route} · 信心 ${"%.0f".format(r.confidence * 100)}%")
                    }
                }
                // 滲液量(醫師輸入)——緊接量測結果;未輸入前修邊/存檔鎖定(防呆)
                val needExudate = onExudate != null && exudate == null
                if (onExudate != null) {
                    Text("滲液量 Exudate(PUSH):0=無 · 1=少量 · 2=中量 · 3=大量", fontSize = 12.sp,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        (0..3).forEach { v ->
                            FilterChip(selected = exudate == v, onClick = { onExudate(v) }, label = { Text("$v") })
                        }
                    }
                    if (needExudate)
                        Text("⚠ 請先輸入滲液量,才能進行「修邊」或「存入時間軸」",
                            fontSize = 12.sp, color = MaterialTheme.colorScheme.error)
                }
                if (r.confidence < 0.70)
                    AssistChip(onClick = onReview, label = { Text("信心度偏低，建議醫師確認") })
                Button(onReview, Modifier.fillMaxWidth(), enabled = !needExudate) { Text("醫師確認・修邊") }
                OutlinedButton(onSaveToTimeline, Modifier.fillMaxWidth(), enabled = !needExudate) { Text("存入個案時間軸") }
                Text(r.disclaimer, fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            else -> Text("尚無結果，請先拍攝。", color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

private fun pct(v: Double?): String = if (v == null) "0%" else "${(v * 100).toInt()}%"
