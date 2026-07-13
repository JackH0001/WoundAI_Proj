package com.woundmeasurement.app.pipeline

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.woundmeasurement.app.data.database.WoundMeasurementDatabase
import java.text.SimpleDateFormat
import java.util.Locale
import kotlin.math.abs

/**
 * 傷口時間軸 / 歷史紀錄(讀本機 Room)。整合趨勢固定上方(面積折線)+ 歷次量測可捲動。
 * 對齊全流程原型 v_timeline:面積↓% 摘要、面積趨勢圖、歷次列表。輔助、非診斷。
 */
@Composable
fun WoundTimelineScreen(onBack: () -> Unit) {
    val ctx = LocalContext.current
    val dao = remember { WoundMeasurementDatabase.getDatabase(ctx).measurementDao() }
    val measurements by dao.getAllMeasurements().collectAsState(initial = emptyList())
    val fmt = remember { SimpleDateFormat("MM/dd HH:mm", Locale.getDefault()) }

    Column(Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Text("傷口時間軸 / 歷史紀錄", style = MaterialTheme.typography.titleLarge)

        val asc = remember(measurements) { measurements.sortedBy { it.timestamp.time } }
        val areas = asc.mapNotNull { it.estimatedArea }

        if (asc.isEmpty()) {
            Text("尚無紀錄。到「AI 量測驗證(模擬)」量測後,按「存入個案時間軸」即可累積。",
                style = MaterialTheme.typography.bodyMedium)
        } else {
            if (areas.size >= 2 && areas.first() > 0.0) {
                val delta = (areas.last() - areas.first()) / areas.first() * 100.0
                val dir = if (delta <= 0) "↓" else "↑"
                Text("面積趨勢:%.2f → %.2f cm²  (%s%.0f%%,共 %d 次)"
                    .format(areas.first(), areas.last(), dir, abs(delta), asc.size),
                    style = MaterialTheme.typography.bodyMedium)
            }
            if (areas.size >= 2) {
                AreaTrendChart(areas, Modifier.fillMaxWidth().height(140.dp))
            }
            Divider()
            LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.weight(1f)) {
                items(measurements) { m ->
                    ElevatedCard(Modifier.fillMaxWidth()) {
                        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                            Text(fmt.format(m.timestamp), fontSize = 13.sp,
                                color = MaterialTheme.colorScheme.onSurfaceVariant)
                            Text("面積:" + (m.estimatedArea?.let { "%.2f cm²".format(it) } ?: "未校正"),
                                style = MaterialTheme.typography.titleMedium)
                            m.notes?.let { Text(it, fontSize = 12.sp) }
                            Text(m.woundType ?: "", fontSize = 11.sp,
                                color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                }
            }
        }
        OutlinedButton(onBack, Modifier.fillMaxWidth()) { Text("返回主畫面") }
    }
}

/** 面積趨勢折線圖(Compose Canvas;舊→新)。 */
@Composable
private fun AreaTrendChart(areas: List<Double>, modifier: Modifier) {
    val maxA = (areas.maxOrNull() ?: 1.0).coerceAtLeast(0.001)
    Canvas(modifier) {
        val n = areas.size
        if (n < 2) return@Canvas
        val padL = 10f; val padB = 10f; val padT = 10f
        val w = size.width - padL * 2
        val h = size.height - padB - padT
        fun px(i: Int) = padL + w * i / (n - 1)
        fun py(v: Double) = padT + h * (1f - (v / maxA).toFloat())
        for (i in 0 until n - 1) {
            drawLine(Color(0xFF3A5A8C), Offset(px(i), py(areas[i])), Offset(px(i + 1), py(areas[i + 1])), strokeWidth = 5f)
        }
        for (i in 0 until n) drawCircle(Color(0xFFC0453B), 7f, Offset(px(i), py(areas[i])))
    }
}
