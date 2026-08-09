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
import com.woundmeasurement.app.data.repo.CaseRepository
import com.woundmeasurement.app.data.store.LocalImageStore
import java.text.SimpleDateFormat
import java.util.Locale
import kotlinx.coroutines.launch
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

    // 單筆刪除。**確認對話框放在這一層而不是每一列**——放在列裡的話，
    // 清單一長就有幾十個 AlertDialog 掛在 composition 上；而且 LazyColumn 回收列時
    // 對話框會跟著消失，使用者滑一下手指、確認框就不見了。
    val repo = remember { CaseRepository.from(WoundMeasurementDatabase.getDatabase(ctx)) }
    val scope = rememberCoroutineScope()
    var pendingDelete by remember { mutableStateOf<MeasurementEntity?>(null) }
    var msg by remember { mutableStateOf<String?>(null) }

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
            if (!unassignedOnly && asc.isNotEmpty()) {
                TissueTrendChart(
                    rows = asc,
                    labels = asc.map { fmtShort.format(it.timestamp) },
                    modifier = Modifier.fillMaxWidth().height(150.dp)
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
                        imageStore = imageStore, onOpen = onOpenRecord,
                        onDelete = { pendingDelete = m }
                    )
                }
            }
        }
        msg?.let { Text(it, style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant) }
        OutlinedButton(onBack, Modifier.fillMaxWidth()) { Text("返回") }
    }

    // 對話框對兩種情況說**不同的話**。做不到的時候只說「失敗」，
    // 使用者唯一能做的就是再按一次——而規則不會因為再按一次而改變。
    pendingDelete?.let { target ->
        val submitted = target.annotationSubmitted
        AlertDialog(
            onDismissRequest = { pendingDelete = null },
            title = { Text(if (submitted) "這筆不可刪除" else "刪除這筆量測？") },
            text = {
                Text(if (submitted)
                    "此筆已送出訓練標註，不可刪除；誤送請用主控台「誤送排除」。\n\n" +
                    "雲端的訓練佇列是唯讀累加的，本機刪掉之後兩邊對不上帳；" +
                    "而**撤回同意要靠這筆紀錄裡的影像代碼**才做得到——刪了就再也撤不回來。"
                else
                    "將刪除這筆量測，連同加密影像與組織柵格檔，無法復原。\n\n" +
                    "面積與趨勢也會一起消失，後面那一次的「較上次 %」會改跟更早的一次比。")
            },
            confirmButton = {
                if (submitted) {
                    TextButton({ pendingDelete = null }) { Text("知道了") }
                } else {
                    TextButton({
                        val id = target.id
                        pendingDelete = null
                        // repo 會再檢查一次 annotationSubmitted：對話框開著的時候
                        // 補送有可能剛好完成，那時這筆就不該刪了。
                        scope.launch {
                            msg = if (repo.deleteMeasurementIfUnsubmitted(id, imageStore))
                                "已刪除該筆量測。"
                            else "⚠ 未刪除：這筆在剛才已送出訓練標註。"
                        }
                    }) { Text("刪除", color = MaterialTheme.colorScheme.error) }
                }
            },
            dismissButton = { if (!submitted) TextButton({ pendingDelete = null }) { Text("取消") } }
        )
    }
}

@Composable
private fun TimelineRow(
    m: MeasurementEntity,
    prevArea: Double?,
    timeText: String,
    imageStore: LocalImageStore,
    onOpen: ((MeasurementEntity) -> Unit)?,
    /** 交給上層開確認框；這一列不自己決定刪不刪得掉。 */
    onDelete: (() -> Unit)? = null
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
                // 刪除入口對已送訓練的那些**也顯示**——按下去會說明為什麼不能刪、
                // 以及該走哪條路。藏起來的話，想刪的人只會反覆找，最後去刪整個個案。
                if (onDelete != null) TextButton(
                    onClick = onDelete,
                    contentPadding = PaddingValues(horizontal = 4.dp, vertical = 0.dp)
                ) { Text("刪除這筆", fontSize = 11.sp, color = MaterialTheme.colorScheme.error) }
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


/**
 * 組織比例趨勢（100% 堆疊柱狀圖）。
 *
 * ## 為什麼要與面積分開看
 *
 * 面積縮小不等於在癒合。一個被壞死組織覆蓋的傷口清創後**面積會變大**，
 * 那是好轉；而一個面積不變但肉芽轉為腐肉的傷口正在惡化。只看面積曲線，
 * 這兩種情況都會被讀反。組織比例是判讀癒合方向的另一半資訊。
 *
 * ## ⚠ 無資料 ≠ 0%
 *
 * v5 之前的紀錄沒有組織欄位（`MIGRATION_4_5` 刻意不給 DEFAULT 0）。
 * 那些柱子畫成灰色斜線的「無資料」，**不是**畫成一根 0% 的柱子——
 * 後者會被讀成「當時完全沒有肉芽」，是一個看起來完全合理的假數據。
 */
@Composable
private fun TissueTrendChart(
    rows: List<MeasurementEntity>,
    labels: List<String>,
    modifier: Modifier
) {
    // 五類的順序刻意由「好」到「壞」：上皮 → 肉芽 → 其他 → 腐肉 → 壞死。
    // 堆疊圖的閱讀方式是看色帶的消長，順序若隨意排，趨勢就看不出方向。
    val order = listOf(
        4 to "上皮", 1 to "肉芽", 5 to "其他", 2 to "腐肉", 3 to "壞死"
    )
    fun frac(m: MeasurementEntity, code: Int): Double? = when (code) {
        1 -> m.tissueGranulation; 2 -> m.tissueSlough; 3 -> m.tissueNecrosis
        4 -> m.tissueEpithelial; 5 -> m.tissueOther; else -> null
    }
    val hasAny = rows.any { m -> order.any { frac(m, it.first) != null } }

    Column(modifier) {
        Text("組織比例", style = MaterialTheme.typography.titleSmall)
        // ⚠ 這裡曾經寫成 `if (!hasAny) { Text(...); return@Column }`，而那會讓 App 崩潰。
        //
        // `Column` 是 inline composable。Compose 編譯器在 lambda 前後插入
        // startReplaceableGroup / endReplaceableGroup，而 `return@Column` 會**跳過**
        // 結尾那一個 —— 群組堆疊就此不平衡，在 composition 結束時炸掉：
        //
        //   java.lang.IndexOutOfBoundsException: Index -1 out of bounds for length 0
        //     at androidx.compose.runtime.Stack.pop(Stack.kt:26)
        //     at ComposerImpl.exitGroup / end / endGroup / endRoot
        //
        // 錯誤訊息裡**沒有任何一行是我們的程式碼**，所以看堆疊完全找不到兇手。
        //
        // 2026-08-07 實際發生：DB v5 之前建立的個案（tissue_* 全為 null）→ hasAny=false
        // → 走到 return → 進入時間軸即閃退。v5 之後的個案有組織資料，不走這條，
        // 所以問題潛伏了很久才被踩到——早期建立的病患正是最少被打開的那些。
        //
        // 正確寫法是 if/else。composable lambda 裡永遠不要提早 return。
        if (!hasAny) {
            Text("這個個案還沒有組織比例資料（App 版本 build 5 之後的量測才會記錄）。",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        } else {
        Row(Modifier.weight(1f).fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(4.dp),
            verticalAlignment = Alignment.Bottom) {
            rows.forEachIndexed { i, m ->
                val vals = order.map { (code, _) -> frac(m, code) }
                val sum = vals.filterNotNull().sum()
                Column(Modifier.weight(1f).fillMaxHeight(), horizontalAlignment = Alignment.CenterHorizontally) {
                    if (sum <= 0.0) {
                        // 無資料：灰底 + 說明，不畫成 0%
                        Box(Modifier.weight(1f).fillMaxWidth()
                            .background(MaterialTheme.colorScheme.surfaceVariant))
                        Text("無資料", fontSize = 9.sp, maxLines = 1,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                    } else {
                        order.forEachIndexed { k, (code, _) ->
                            val v = (vals[k] ?: 0.0) / sum
                            if (v > 0.0) Box(
                                Modifier.weight(v.toFloat()).fillMaxWidth()
                                    .background(Color(T_COLORS[code] or -0x1000000))
                            )
                        }
                        Text(labels.getOrElse(i) { "" }, fontSize = 9.sp, maxLines = 1)
                    }
                }
            }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(top = 4.dp)) {
            order.forEach { (code, name) ->
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Box(Modifier.size(9.dp).background(Color(T_COLORS[code] or -0x1000000)))
                    Text(" $name", fontSize = 10.sp)
                }
            }
        }
        }   // else（見上方說明：不可用 return@Column）
    }
}
