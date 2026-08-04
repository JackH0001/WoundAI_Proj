package com.woundmeasurement.app.pipeline

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.Image
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.woundmeasurement.app.data.database.WoundMeasurementDatabase
import com.woundmeasurement.app.data.entity.MeasurementEntity
import com.woundmeasurement.app.data.store.LocalImageStore
import java.text.SimpleDateFormat
import java.util.Locale
import kotlinx.coroutines.withContext
import kotlin.math.abs

/**
 * 傷口時間軸（讀本機 Room）。趨勢固定上方 + 歷次量測可捲動。
 *
 * 2026-07-30 強化：
 *  - 單筆右側**縮圖**（從加密影像即時解碼），點擊可回頭修邊／補送標註 → **不必重測一遍**
 *  - 每筆標示「較上次 ±x%」，不必自己心算
 *  - **筆數無條件顯示**（原本藏在趨勢摘要裡，n=1 就完全看不到）
 *  - 折線圖補上日期軌與 Y 軸數字（原本只有形狀，看不出量級）
 */
@Composable
fun WoundTimelineScreen(
    onBack: () -> Unit,
    /**
     * 傷口個案 id。給了就**只畫這個傷口**的癒合曲線。
     *
     * null 是相容用的「全部紀錄」模式——注意那條線在臨床上沒有意義:
     * 一位病患可能同時有薦骨壓瘡與右足潰瘍,不同病患的傷口也會被混進同一條線。
     * 真實收案請務必從個案進來。
     */
    caseId: Long? = null,
    caseLabel: String? = null,
    /**
     * true＝只列**未歸戶**（`caseId IS NULL`）的紀錄，也就是快速量測產生的那些。
     * 它們不屬於任何個案，先前存進 DB 後就再也叫不出來。
     */
    unassignedOnly: Boolean = false,
    /** 點縮圖／「重新修邊」→ 回頭編輯該筆並可補送標註。null 則不顯示該入口。 */
    onOpenRecord: ((MeasurementEntity) -> Unit)? = null
) {
    val ctx = LocalContext.current
    val dao = remember { WoundMeasurementDatabase.getDatabase(ctx).measurementDao() }
    val imageStore = remember { LocalImageStore(ctx) }
    val flow = remember(caseId, unassignedOnly) {
        when {
            caseId != null -> dao.getMeasurementsByCase(caseId)
            unassignedOnly -> dao.getUnassignedMeasurements()
            else -> dao.getAllMeasurements()
        }
    }
    val measurements by flow.collectAsState(initial = emptyList())
    val fmt = remember { SimpleDateFormat("MM/dd HH:mm", Locale.getDefault()) }
    val fmtShort = remember { SimpleDateFormat("MM/dd", Locale.getDefault()) }

    Column(Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Text(when {
            caseId != null -> "傷口時間軸 — ${caseLabel ?: "個案 #$caseId"}"
            unassignedOnly -> "快速量測紀錄（未歸戶）"
            else -> "傷口時間軸 / 歷史紀錄(全部)"
        }, style = MaterialTheme.typography.titleLarge)
        if (unassignedOnly) Text(
            "這些是快速量測（範例／模擬圖）的紀錄，不屬於任何個案傷口，" +
            "因此不會出現在個案時間軸，也不計入臨床收案進度。趨勢圖在此無意義（不同影像混在一起）。",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
        else if (caseId == null) Text(
            "⚠ 未指定個案:此處把所有量測混在一起,趨勢線不代表任何單一傷口。請由個案管理進入。",
            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)

        val asc = remember(measurements) { measurements.sortedBy { it.timestamp.time } }
        val areas = asc.mapNotNull { it.estimatedArea }

        // 筆數**無條件顯示**:原本只在趨勢摘要裡出現(需 n≥2),n=1 時畫面完全看不到累計了幾次
        Text("累計 ${asc.size} 次量測" +
             (if (asc.size < 2) "（趨勢圖需 2 次以上）" else ""),
            style = MaterialTheme.typography.bodyMedium)

        if (asc.isEmpty()) {
            Text("尚無紀錄。由主畫面「個案」選定個案傷口 → 量測 → 按「存入個案時間軸」即可累積。",
                style = MaterialTheme.typography.bodyMedium)
        } else {
            if (!unassignedOnly && areas.size >= 2 && areas.first() > 0.0) {
                val delta = (areas.last() - areas.first()) / areas.first() * 100.0
                val dir = if (delta <= 0) "↓" else "↑"
                Text("面積趨勢:%.2f → %.2f cm²  (較首次 %s%.0f%%)"
                    .format(areas.first(), areas.last(), dir, abs(delta)),
                    style = MaterialTheme.typography.bodyMedium,
                    color = if (delta <= -10) MaterialTheme.colorScheme.primary
                            else if (delta >= 10) MaterialTheme.colorScheme.error
                            else MaterialTheme.colorScheme.onSurface)
            }
            if (!unassignedOnly && areas.size >= 2) {
                AreaTrendChart(
                    areas = areas,
                    labels = asc.mapNotNull { m -> m.estimatedArea?.let { fmtShort.format(m.timestamp) } },
                    modifier = Modifier.fillMaxWidth().height(170.dp)
                )
            }
            Divider()
            LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.weight(1f)) {
                // 依「新→舊」顯示,但變化%要跟時間上的前一次比,所以用 asc 的索引來查前值
                items(measurements, key = { it.id }) { m ->
                    val idx = asc.indexOfFirst { it.id == m.id }
                    val prevArea = if (idx > 0) asc[idx - 1].estimatedArea else null
                    TimelineRow(
                        m = m, prevArea = prevArea, timeText = fmt.format(m.timestamp),
                        imageStore = imageStore, onOpen = onOpenRecord
                    )
                }
            }
        }
        OutlinedButton(onBack, Modifier.fillMaxWidth()) { Text("返回") }
    }
}

@Composable
private fun TimelineRow(
    m: MeasurementEntity,
    prevArea: Double?,
    timeText: String,
    imageStore: LocalImageStore,
    onOpen: ((MeasurementEntity) -> Unit)?
) {
    // 縮圖在背景解密＋降採樣;不快取整張 2048 影像(清單捲幾列就 OOM)
    var thumb by remember(m.id, m.imagePath) { mutableStateOf<android.graphics.Bitmap?>(null) }
    // 要分辨「還在解」與「解不開」:兩者都畫「…」的話,App 重裝/還原備份使 Keystore 金鑰換新後,
    // 整條時間軸會停在一排「…」,看起來像永遠載入中,而實際是影像已不可解密。
    var thumbState by remember(m.id, m.imagePath) { mutableStateOf(0) } // 0=載入中 1=成功 2=失敗/無檔
    // File.exists() 是 syscall,放在 composition 裡等於每次重組都在主執行緒打磁碟 → 一併移到 IO
    var fileOk by remember(m.id, m.imagePath) { mutableStateOf(false) }
    LaunchedEffect(m.id, m.imagePath) {
        val r = withContext(kotlinx.coroutines.Dispatchers.IO) {
            val ok = imageStore.exists(m.imagePath)
            ok to (if (ok) runCatching { imageStore.loadThumbnail(m.imagePath, 200) }.getOrNull() else null)
        }
        fileOk = r.first
        thumb = r.second
        thumbState = if (r.second != null) 1 else 2
    }
    val canOpen = onOpen != null && fileOk && m.imageW != null

    ElevatedCard(
        Modifier.fillMaxWidth().then(
            if (canOpen) Modifier.clickable { onOpen?.invoke(m) } else Modifier
        )
    ) {
        Row(Modifier.padding(12.dp), verticalAlignment = Alignment.Top) {
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text(timeText, fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("面積:" + (m.estimatedArea?.let { "%.2f cm²".format(it) } ?: "未校正"),
                        style = MaterialTheme.typography.titleMedium)
                    // 較上次 ±%:臨床看的是「這次跟上次比有沒有變化」,不該讓醫師自己心算
                    val a = m.estimatedArea
                    if (a != null && prevArea != null && prevArea > 0.0) {
                        val d = (a - prevArea) / prevArea * 100.0
                        Spacer(Modifier.width(8.dp))
                        Text("較上次 %+.0f%%".format(d), fontSize = 13.sp,
                            color = if (d <= -10) MaterialTheme.colorScheme.primary
                                    else if (d >= 10) MaterialTheme.colorScheme.error
                                    else MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
                m.notes?.let { Text(it, fontSize = 12.sp) }
                Row {
                    Text(m.woundType ?: "", fontSize = 11.sp,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                    if (m.annotationSubmitted) {
                        Spacer(Modifier.width(6.dp))
                        Text("已送訓練", fontSize = 11.sp, color = MaterialTheme.colorScheme.primary)
                    } else if (m.gtPolygon != null && m.imageId != null) {
                        Spacer(Modifier.width(6.dp))
                        Text("可補送標註", fontSize = 11.sp, color = MaterialTheme.colorScheme.tertiary)
                    }
                }
                if (canOpen) Text("點卡片可回頭修邊／補送標註（不必重測）", fontSize = 11.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Spacer(Modifier.width(10.dp))
            Box(
                Modifier.size(72.dp).background(MaterialTheme.colorScheme.surfaceVariant),
                contentAlignment = Alignment.Center
            ) {
                val t = thumb
                if (t != null) Image(
                    bitmap = t.asImageBitmap(), contentDescription = "傷口縮圖",
                    contentScale = ContentScale.Crop, modifier = Modifier.fillMaxSize()
                ) else Text(
                    when {
                        // v3 之前的舊紀錄從未存過影像
                        m.imagePath.isEmpty() -> "無影像"
                        thumbState == 0 -> "…"
                        // 檔案還在卻解不開 = 金鑰換新(App 重裝／還原備份);與「已依保存期限清除」是兩回事
                        fileOk -> "無法解密"
                        else -> "已清除"
                    },
                    fontSize = 10.sp, textAlign = TextAlign.Center,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
        }
    }
}

/**
 * 面積趨勢折線圖（舊→新）。
 * 補上 Y 軸數字與日期軌——原本只有線條與圓點，看得出形狀但看不出量級與時間間隔。
 */
@Composable
private fun AreaTrendChart(areas: List<Double>, labels: List<String>, modifier: Modifier) {
    val maxA = (areas.maxOrNull() ?: 1.0).coerceAtLeast(0.001)
    Column(modifier) {
        Row(Modifier.weight(1f)) {
            // Y 軸刻度(0 / 中 / 最大)
            Column(
                Modifier.width(44.dp).fillMaxHeight(),
                verticalArrangement = Arrangement.SpaceBetween
            ) {
                Text("%.1f".format(maxA), fontSize = 10.sp)
                Text("%.1f".format(maxA / 2), fontSize = 10.sp)
                Text("0", fontSize = 10.sp)
            }
            Canvas(Modifier.weight(1f).fillMaxHeight()) {
                val n = areas.size
                if (n < 2) return@Canvas
                val padL = 6f; val padB = 6f; val padT = 6f
                val w = size.width - padL * 2
                val h = size.height - padB - padT
                fun px(i: Int) = padL + w * i / (n - 1)
                fun py(v: Double) = padT + h * (1f - (v / maxA).toFloat())
                // 水平參考線(0 / 半 / 滿),讓量級看得出來
                val grid = Color(0x22000000)
                listOf(0.0, maxA / 2, maxA).forEach { v ->
                    drawLine(grid, Offset(padL, py(v)), Offset(padL + w, py(v)), strokeWidth = 2f)
                }
                for (i in 0 until n - 1) {
                    drawLine(Color(0xFF3A5A8C), Offset(px(i), py(areas[i])),
                        Offset(px(i + 1), py(areas[i + 1])), strokeWidth = 5f)
                }
                for (i in 0 until n) drawCircle(Color(0xFFC0453B), 7f, Offset(px(i), py(areas[i])))
            }
        }
        // 日期軌:只標首/末(中間點多了會疊在一起,反而看不清)
        if (labels.size >= 2) Row(Modifier.fillMaxWidth().padding(start = 44.dp)) {
            Text(labels.first(), fontSize = 10.sp)
            Spacer(Modifier.weight(1f))
            if (labels.size > 2) {
                Text("… ${labels.size} 點 …", fontSize = 10.sp)
                Spacer(Modifier.weight(1f))
            }
            Text(labels.last(), fontSize = 10.sp)
        }
    }
}
