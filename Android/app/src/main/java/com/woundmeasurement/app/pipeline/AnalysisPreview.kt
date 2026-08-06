package com.woundmeasurement.app.pipeline

import android.graphics.Bitmap
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

/**
 * 量測結果的參照畫面：原圖 ＋ 傷口輪廓 ＋ 組織分類 ＋ **ArUco 校正框**。
 *
 * ## 為什麼校正框是這一頁最重要的東西
 *
 * ArUco 偵測沒有「認錯了」這個錯誤狀態——它要嘛回一個四邊形，要嘛回 null。
 * 若它把反光、地磚接縫或別處的印刷圖案認成標記，`mm_per_px` 就是錯的，
 * 而**每一筆面積都會安靜地錯**：後端回 200、畫面顯示一個看起來完全合理的數字、
 * 沒有任何警告。這是本專案最危險的失敗形狀（同一形狀今天已經出現過五次）。
 *
 * 程式沒有辦法自己判斷「這個四邊形是不是真的貼紙」。唯一實際可行的防線是**讓人看一眼**：
 * 框畫在照片上，貼歪了或框到別的東西，三秒就看得出來。
 *
 * ## 為什麼組織圖層要能關掉
 *
 * 任何配色都不可能同時滿足「看得清楚分區」與「看得清楚底下的組織紋理」——
 * 那是同一塊像素的兩種用途。與其折衷到兩邊都不好，不如讓使用者一鍵切換。
 */

/** 顯示層級開關。預設三層全開：第一眼就要看到校正框對不對。 */
private data class Layers(val outline: Boolean = true, val tissue: Boolean = true, val marker: Boolean = true)

private const val PREVIEW_MAX = 900          // 預覽長邊上限（記憶體與繪製成本）

/**
 * 在傷口多邊形內做逐像素組織分類，回傳一張與 [preview] 同尺寸的半透明 ARGB 疊圖。
 *
 * 分類本身在 [TissueSeg]——**與修邊畫面共用同一份實作**。兩邊各寫一份的話，
 * 某天只改了其中一邊，醫師就會看到「結果頁說腐肉、修邊頁說肉芽」而無從判斷該信哪個。
 *
 * ⚠ 這是**色彩啟發式**，不是模型。它與後端 `stage4_tissue` 用的是同一套規則
 * （`TissueClassifierV2`），所以畫面與數字一致；但兩者都只是輔助，最終以醫師修邊為準。
 */
private fun buildTissueOverlay(src: Bitmap, preview: Bitmap, polygon: List<List<Int>>): Bitmap? {
    if (polygon.size < 3) return null
    return runCatching {
        var x0 = Int.MAX_VALUE; var y0 = Int.MAX_VALUE; var x1 = 0; var y1 = 0
        polygon.forEach { p -> x0 = min(x0, p[0]); y0 = min(y0, p[1]); x1 = max(x1, p[0]); y1 = max(y1, p[1]) }
        x0 = x0.coerceIn(0, src.width - 2); y0 = y0.coerceIn(0, src.height - 2)
        x1 = x1.coerceIn(x0 + 2, src.width); y1 = y1.coerceIn(y0 + 2, src.height)
        val bw = x1 - x0; val bh = y1 - y0

        val (gw, gh) = TissueSeg.grid(bw, bh)
        val inside = TissueSeg.rasterizePolygon(polygon, x0, y0, bw, bh, gw, gh)
        val cls = TissueSeg.classify(src, x0, y0, x1, y1, gw, gh, inside, wbGains) ?: return null

        val out = IntArray(gw * gh)
        for (i in out.indices) out[i] = if (cls[i].toInt() in 1..T_MAX) T_COLORS[cls[i].toInt()] else 0
        val tint = Bitmap.createBitmap(gw, gh, Bitmap.Config.ARGB_8888)
        tint.setPixels(out, 0, gw, 0, 0, gw, gh)

        val full = Bitmap.createBitmap(preview.width, preview.height, Bitmap.Config.ARGB_8888)
        val c = android.graphics.Canvas(full)
        val k = preview.width.toFloat() / src.width
        c.drawBitmap(tint, null, android.graphics.RectF(x0 * k, y0 * k, x1 * k, y1 * k),
            android.graphics.Paint(android.graphics.Paint.FILTER_BITMAP_FLAG))
        tint.recycle()
        full
    }.getOrNull()
}

@Composable
fun AnalysisPreview(
    bitmap: Bitmap,
    polygon: List<List<Int>>,
    markerQuad: List<List<Int>>?,
    mmPerPx: Double?,
    calibMethod: String?,
    /** 後端色卡白平衡增益 [R,G,B]；與結果欄、修邊底稿必須是同一組。 */
    wbGains: DoubleArray? = null,
    modifier: Modifier = Modifier
) {
    var layers by remember { mutableStateOf(Layers()) }

    val preview = remember(bitmap) {
        val s = min(1f, PREVIEW_MAX.toFloat() / max(bitmap.width, bitmap.height))
        if (s >= 1f) bitmap
        else Bitmap.createScaledBitmap(bitmap,
            (bitmap.width * s).roundToInt(), (bitmap.height * s).roundToInt(), true)
    }
    // 逐像素分類跑在背景執行緒。
    //
    // 它只有幾十毫秒，但那是在**合成執行緒**上——結果頁第一次出現時會多卡一幀，
    // 而醫師拿到的印象會是「按完量測畫面頓一下」。overlay 晚一瞬間出現不影響判讀，
    // 卡頓卻會被記住。用 produceState 讓它自己補上來。
    val tissue by produceState<Bitmap?>(initialValue = null, bitmap, polygon) {
        value = withContext(Dispatchers.Default) { buildTissueOverlay(bitmap, preview, polygon) }
    }
    val img = remember(preview) { preview.asImageBitmap() }
    val tImg = remember(tissue) { tissue?.asImageBitmap() }

    Column(verticalArrangement = Arrangement.spacedBy(6.dp), modifier = modifier) {
        Text("辨識參照圖", style = MaterialTheme.typography.titleSmall)

        BoxWithConstraints(Modifier.fillMaxWidth()) {
            val ar = preview.height.toFloat() / preview.width
            Canvas(Modifier.fillMaxWidth().height(maxWidth * ar)) {
                val k = size.width / bitmap.width          // 影像座標 → 畫布座標
                drawImage(img, dstOffset = IntOffset.Zero,
                    dstSize = IntSize(size.width.roundToInt(), size.height.roundToInt()))
                if (layers.tissue && tImg != null)
                    drawImage(tImg, dstOffset = IntOffset.Zero,
                        dstSize = IntSize(size.width.roundToInt(), size.height.roundToInt()))

                if (layers.outline && polygon.size >= 3) {
                    val path = Path().apply {
                        moveTo(polygon[0][0] * k, polygon[0][1] * k)
                        for (i in 1 until polygon.size) lineTo(polygon[i][0] * k, polygon[i][1] * k)
                        close()
                    }
                    // 先描一圈深色再描亮色：亮青在淺色皮膚上會消失，雙描邊在任何背景都看得見。
                    drawPath(path, Color(0x99000000), style = Stroke(width = 6f))
                    drawPath(path, Color(0xFF00E5FF), style = Stroke(width = 3f))
                }

                if (layers.marker && markerQuad != null && markerQuad.size == 4) {
                    val q = markerQuad
                    val path = Path().apply {
                        moveTo(q[0][0] * k, q[0][1] * k)
                        for (i in 1..3) lineTo(q[i][0] * k, q[i][1] * k)
                        close()
                    }
                    drawPath(path, Color(0x99000000), style = Stroke(width = 7f))
                    drawPath(path, Color(0xFF39FF6A), style = Stroke(width = 4f))
                    // 角點標示方位：貼紙被部分遮住時，四個角有沒有都落在紙上一眼可辨。
                    q.forEach { drawCircle(Color(0xFF39FF6A), 7f, Offset(it[0] * k, it[1] * k)) }
                    q.forEach { drawCircle(Color(0xCC000000), 7f, Offset(it[0] * k, it[1] * k),
                        style = Stroke(width = 2f)) }
                }
            }
        }

        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            FilterChip(layers.outline, { layers = layers.copy(outline = !layers.outline) },
                { Text("傷口輪廓") }, modifier = Modifier.weight(1f))
            FilterChip(layers.tissue, { layers = layers.copy(tissue = !layers.tissue) },
                { Text("組織") }, modifier = Modifier.weight(1f))
            FilterChip(layers.marker, { layers = layers.copy(marker = !layers.marker) },
                { Text("校正框") }, modifier = Modifier.weight(1f))
        }

        // 校正狀態。這一段的措辭刻意直白——它要求的是一個動作（看一眼），不是一則資訊。
        if (markerQuad != null && markerQuad.size == 4) {
            Text("🟩 綠框＝ArUco 校正貼紙的辨識位置" +
                 (mmPerPx?.let { "　尺度 %.4f mm/px".format(it) } ?: ""),
                fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Text("⚠ 請確認綠框**確實框住貼紙本身**。框錯目標（反光、其他方形圖案）時，" +
                 "面積會整個錯掉，而系統不會有任何警告——它只知道自己找到了一個四邊形。",
                fontSize = 12.sp, color = MaterialTheme.colorScheme.error)
        } else {
            Text("ℹ 未偵測到 ArUco 貼紙" +
                 (calibMethod?.takeIf { it != "none" }?.let { "（改用 $it）" } ?: "（面積未校正）") +
                 "。貼紙要完整入鏡、不反光、與傷口大致同一平面。",
                fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Text("組織分區為色彩啟發式（與後端同一套規則），**非模型輸出、非診斷**；以醫師修邊為準。",
            fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}
