package com.woundmeasurement.app.pipeline

import android.graphics.Bitmap
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt
import kotlin.math.sqrt

/**
 * 醫師修邊(對齊原型 v_review):邊界筆刷(=GT) + 組織筆刷(遮罩內互斥塗蓋)。
 * 邊界:塗抹＋/擦除－ 增減傷口遮罩;面積=raster 像素計數即時重算。
 * 組織:肉芽/腐肉/壞死/上皮 四類筆刷,只作用於傷口遮罩內;比例即時 → 組織分/PUSH 連動。
 * 完成 → Moore 輪廓+RDP 產 GT polygon、correction_iou(遮罩IoU)、新面積、新組織比例。
 * 視圖:自動 ROI 50% 放大、－/＋/ROI/全圖、單指移動(移動模式)。輔助、非診斷、需醫師確認。
 */
private enum class EditTool { B_PAINT, B_ERASE, PAN, TISSUE }

private val T_KEYS = arrayOf("", "granulation", "slough", "necrosis", "epithelial")
private val T_NAMES = arrayOf("", "肉芽", "腐肉", "壞死", "上皮")
private val T_COLORS = intArrayOf(  // overlay ARGB(半透明)
    0,
    android.graphics.Color.argb(110, 220, 60, 60),    // 肉芽 紅
    android.graphics.Color.argb(120, 235, 210, 70),   // 腐肉 黃
    android.graphics.Color.argb(140, 40, 40, 40),     // 壞死 深灰
    android.graphics.Color.argb(120, 240, 150, 170)   // 上皮 粉
)

@Composable
fun WoundEditScreen(
    bitmap: Bitmap,
    initialPolygon: List<List<Int>>,
    originalArea: Double?,
    tissueFrac: Map<String, Double>,
    exudate: Int?,
    onCancel: () -> Unit,
    onDone: (edited: List<List<Int>>, correctionIou: Double?, newArea: Double?, tissue: Map<String, Double>) -> Unit
) {
    val img = remember(bitmap) { bitmap.asImageBitmap() }
    val bw = bitmap.width; val bh = bitmap.height

    // ---- raster 兩層:mask(傷口) + tissue(組織類別 1..4) ----
    val mScale = remember { min(1f, 640f / max(bw, bh)) }
    val mw = remember { max(8, (bw * mScale).roundToInt()) }
    val mh = remember { max(8, (bh * mScale).roundToInt()) }
    val mask = remember { ByteArray(mw * mh) }
    val tissue = remember { ByteArray(mw * mh) }
    val initMask = remember { ByteArray(mw * mh) }
    var maskCount by remember { mutableStateOf(0) }
    var initCount by remember { mutableStateOf(0) }
    val tCounts = remember { intArrayOf(0, 0, 0, 0, 0) }
    var version by remember { mutableStateOf(0) }
    val overlay = remember { Bitmap.createBitmap(mw, mh, Bitmap.Config.ARGB_8888) }

    class Snap(val m: ByteArray, val t: ByteArray, val c: Int, val tc: IntArray)
    val undo = remember { mutableStateListOf<Snap>() }
    val redo = remember { mutableStateListOf<Snap>() }
    fun snap() = Snap(mask.copyOf(), tissue.copyOf(), maskCount, tCounts.copyOf())
    fun restore(s: Snap) {
        System.arraycopy(s.m, 0, mask, 0, mask.size)
        System.arraycopy(s.t, 0, tissue, 0, tissue.size)
        maskCount = s.c
        System.arraycopy(s.tc, 0, tCounts, 0, 5)
    }

    val defaultClass = remember {  // 初始組織=AI 判定的主導類(無資訊時=肉芽)
        val cand = listOf("granulation" to 1, "slough" to 2, "necrosis" to 3, "epithelial" to 4)
        (cand.maxByOrNull { tissueFrac[it.first] ?: 0.0 }?.takeIf { (tissueFrac[it.first] ?: 0.0) > 0.0 }?.second) ?: 1
    }

    fun syncOverlayAll() {
        val px = IntArray(mw * mh)
        for (i in mask.indices) px[i] = if (mask[i].toInt() != 0) T_COLORS[tissue[i].toInt().coerceIn(0, 4).coerceAtLeast(1)] else 0
        overlay.setPixels(px, 0, mw, 0, 0, mw, mh)
    }
    remember(initialPolygon) {
        java.util.Arrays.fill(mask, 0); java.util.Arrays.fill(tissue, 0); tCounts.fill(0)
        scanlineFill(initialPolygon, mScale, mw, mh, mask)
        var c = 0
        for (i in mask.indices) if (mask[i].toInt() != 0) { c++; tissue[i] = defaultClass.toByte() }
        maskCount = c; initCount = c; tCounts[defaultClass] = c
        System.arraycopy(mask, 0, initMask, 0, mask.size)
        syncOverlayAll(); version++
        true
    }

    // ---- 視圖 ----
    var boxSize by remember { mutableStateOf(IntSize.Zero) }
    var viewScale by remember { mutableStateOf(1f) }
    var viewOffset by remember { mutableStateOf(Offset.Zero) }
    var viewInit by remember { mutableStateOf(false) }
    fun base(): Float = if (boxSize == IntSize.Zero) 1f else min(boxSize.width / bw.toFloat(), boxSize.height / bh.toFloat())
    fun k(): Float = base() * viewScale
    fun fitFull() { viewScale = 1f; val kk = k(); viewOffset = Offset((bw - boxSize.width / kk) / 2f, (bh - boxSize.height / kk) / 2f) }
    fun fitRoi() {
        if (initialPolygon.size < 3 || boxSize == IntSize.Zero) return
        val xs = initialPolygon.map { it[0] }; val ys = initialPolygon.map { it[1] }
        val w = max((xs.max() - xs.min()).toFloat(), 8f); val h = max((ys.max() - ys.min()).toFloat(), 8f)
        val kT = 0.5f * min(boxSize.width / w, boxSize.height / h)
        viewScale = (kT / base()).coerceIn(0.5f, 24f)
        val kk = k()
        viewOffset = Offset((xs.min() + xs.max()) / 2f - boxSize.width / (2f * kk),
                            (ys.min() + ys.max()) / 2f - boxSize.height / (2f * kk))
    }
    fun zoomBy(f: Float) {
        if (boxSize == IntSize.Zero) return
        val c = Offset(boxSize.width / 2f, boxSize.height / 2f)
        val ci = viewOffset + c / k()
        viewScale = (viewScale * f).coerceIn(0.5f, 24f)
        viewOffset = ci - c / k()
    }
    LaunchedEffect(boxSize) { if (!viewInit && boxSize != IntSize.Zero) { fitRoi(); viewInit = true } }

    // ---- 工具 ----
    var tool by remember { mutableStateOf(EditTool.B_PAINT) }
    var curTissue by remember { mutableStateOf(2) }        // 組織筆刷預設=腐肉(常見要標的)
    var brushScreen by remember { mutableStateOf(36f) }
    var cursor by remember { mutableStateOf<Offset?>(null) }

    fun stamp(imgPt: Offset) {
        val r = max(1f, (brushScreen / k()) * mScale)
        val cx = imgPt.x * mScale; val cy = imgPt.y * mScale
        val r2 = r * r
        val x0 = max(0, (cx - r).toInt()); val x1 = min(mw - 1, (cx + r).toInt())
        val y0 = max(0, (cy - r).toInt()); val y1 = min(mh - 1, (cy + r).toInt())
        for (y in y0..y1) for (x in x0..x1) {
            val dx = x - cx; val dy = y - cy
            if (dx * dx + dy * dy > r2) continue
            val i = y * mw + x
            when (tool) {
                EditTool.B_PAINT -> if (mask[i].toInt() == 0) {
                    mask[i] = 1; maskCount++
                    tissue[i] = defaultClass.toByte(); tCounts[defaultClass]++
                    overlay.setPixel(x, y, T_COLORS[defaultClass])
                }
                EditTool.B_ERASE -> if (mask[i].toInt() != 0) {
                    mask[i] = 0; maskCount--
                    val tc = tissue[i].toInt(); if (tc in 1..4) tCounts[tc]--
                    tissue[i] = 0
                    overlay.setPixel(x, y, 0)
                }
                EditTool.TISSUE -> if (mask[i].toInt() != 0 && tissue[i].toInt() != curTissue) {
                    val tc = tissue[i].toInt(); if (tc in 1..4) tCounts[tc]--
                    tissue[i] = curTissue.toByte(); tCounts[curTissue]++
                    overlay.setPixel(x, y, T_COLORS[curTissue])
                }
                EditTool.PAN -> {}
            }
        }
        version++
    }
    fun stampLine(a: Offset, b: Offset) {
        val d = b - a; val len = sqrt(d.x * d.x + d.y * d.y)
        val stepPx = max(1f, (brushScreen / k()) * 0.5f)
        val n = max(1, (len / stepPx).toInt())
        for (i in 0..n) stamp(a + d * (i.toFloat() / n))
    }

    fun liveFrac(): Map<String, Double> {
        val tot = max(1, maskCount)
        return mapOf(
            "granulation" to tCounts[1].toDouble() / tot, "slough" to tCounts[2].toDouble() / tot,
            "necrosis" to tCounts[3].toDouble() / tot, "epithelial" to tCounts[4].toDouble() / tot,
            "other" to 0.0
        )
    }
    @Suppress("UNUSED_EXPRESSION") version
    val liveArea = if (originalArea != null && initCount > 0) originalArea * maskCount / initCount else originalArea
    val lf = liveFrac()
    val livePush = WoundPipeline.push(liveArea, lf, exudate).partial

    Column(Modifier.fillMaxSize().navigationBarsPadding().padding(10.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text("修邊(=GT)  面積 ${liveArea?.let { "%.2f".format(it) } ?: "-"} cm² · PUSH ${livePush ?: "-"}",
            style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.primary)
        Text("組織  肉芽${(lf["granulation"]!! * 100).toInt()}% · 腐肉${(lf["slough"]!! * 100).toInt()}% · " +
             "壞死${(lf["necrosis"]!! * 100).toInt()}% · 上皮${(lf["epithelial"]!! * 100).toInt()}%",
            fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)

        Box(Modifier.fillMaxWidth().weight(1f).clipToBounds().onSizeChanged { boxSize = it }) {
            Canvas(
                Modifier.fillMaxSize().pointerInput(Unit) {
                    var strokeSnapshot: Snap? = null
                    var last: Offset? = null
                    detectDragGestures(
                        onDragStart = { off ->
                            val kk = k(); val imgPt = off / kk + viewOffset
                            cursor = off
                            if (tool != EditTool.PAN) {
                                strokeSnapshot = snap()
                                stamp(imgPt); last = imgPt
                            }
                        },
                        onDrag = { change, delta ->
                            change.consume()
                            val kk = k()
                            cursor = change.position
                            if (tool == EditTool.PAN) viewOffset -= delta / kk
                            else {
                                val cur = change.position / kk + viewOffset
                                last?.let { stampLine(it, cur) }; last = cur
                            }
                        },
                        onDragEnd = {
                            strokeSnapshot?.let { if (undo.size >= 12) undo.removeAt(0); undo.add(it); redo.clear() }
                            strokeSnapshot = null; last = null; cursor = null
                        },
                        onDragCancel = { strokeSnapshot = null; last = null; cursor = null }
                    )
                }
            ) {
                @Suppress("UNUSED_EXPRESSION") version
                val kk = k()
                val dstOff = IntOffset((-viewOffset.x * kk).roundToInt(), (-viewOffset.y * kk).roundToInt())
                val dstSz = IntSize((bw * kk).roundToInt(), (bh * kk).roundToInt())
                drawImage(img, srcOffset = IntOffset.Zero, srcSize = IntSize(bw, bh), dstOffset = dstOff, dstSize = dstSz)
                drawImage(overlay.asImageBitmap(), srcOffset = IntOffset.Zero, srcSize = IntSize(mw, mh),
                    dstOffset = dstOff, dstSize = dstSz)
                cursor?.let {
                    val col = when (tool) {
                        EditTool.B_ERASE -> Color(0xFFFF5050)
                        EditTool.TISSUE -> Color(T_COLORS[curTissue] or -0x1000000)
                        else -> Color(0xFF35C759)
                    }
                    drawCircle(col, radius = brushScreen, center = it, alpha = 0.9f,
                        style = androidx.compose.ui.graphics.drawscope.Stroke(width = 3f))
                }
            }
        }

        // 模式列:邊界＋/邊界－/移動/組織
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            FilterChip(tool == EditTool.B_PAINT, { tool = EditTool.B_PAINT }, { Text("邊界＋") }, modifier = Modifier.weight(1f))
            FilterChip(tool == EditTool.B_ERASE, { tool = EditTool.B_ERASE }, { Text("邊界－") }, modifier = Modifier.weight(1f))
            FilterChip(tool == EditTool.PAN, { tool = EditTool.PAN }, { Text("移動") }, modifier = Modifier.weight(1f))
            FilterChip(tool == EditTool.TISSUE, { tool = EditTool.TISSUE }, { Text("組織🖌") }, modifier = Modifier.weight(1f))
        }
        // 組織類別列(組織模式時)
        if (tool == EditTool.TISSUE) {
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                (1..4).forEach { c ->
                    FilterChip(curTissue == c, { curTissue = c }, { Text(T_NAMES[c]) }, modifier = Modifier.weight(1f))
                }
            }
        }
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Text("筆刷", fontSize = 12.sp)
            Slider(value = brushScreen, onValueChange = { brushScreen = it }, valueRange = 10f..90f, modifier = Modifier.weight(1f))
            Text("${brushScreen.toInt()}", fontSize = 12.sp)
        }
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            OutlinedButton({ zoomBy(1 / 1.3f) }, Modifier.weight(1f), contentPadding = PaddingValues(2.dp)) { Text("－") }
            OutlinedButton({ zoomBy(1.3f) }, Modifier.weight(1f), contentPadding = PaddingValues(2.dp)) { Text("＋") }
            OutlinedButton({ fitRoi() }, Modifier.weight(1f), contentPadding = PaddingValues(2.dp)) { Text("ROI") }
            OutlinedButton({ fitFull() }, Modifier.weight(1f), contentPadding = PaddingValues(2.dp)) { Text("全圖") }
            OutlinedButton({
                if (undo.isNotEmpty()) { redo.add(snap()); restore(undo.removeAt(undo.lastIndex)); syncOverlayAll(); version++ }
            }, enabled = undo.isNotEmpty(), modifier = Modifier.weight(1f), contentPadding = PaddingValues(2.dp)) { Text("↺") }
            OutlinedButton({
                if (redo.isNotEmpty()) { undo.add(snap()); restore(redo.removeAt(redo.lastIndex)); syncOverlayAll(); version++ }
            }, enabled = redo.isNotEmpty(), modifier = Modifier.weight(1f), contentPadding = PaddingValues(2.dp)) { Text("↩") }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            OutlinedButton(onCancel, Modifier.weight(1f)) { Text("取消") }
            Button({
                val boundary = traceLargestBoundary(mask, mw, mh)
                if (boundary.size >= 3) {
                    val simplified = rdp(boundary, 1.5)
                    val poly = simplified.map { listOf((it[0] / mScale).roundToInt(), (it[1] / mScale).roundToInt()) }
                    var inter = 0; var uni = 0
                    for (i in mask.indices) {
                        val a = initMask[i].toInt() != 0; val b = mask[i].toInt() != 0
                        if (a || b) uni++; if (a && b) inter++
                    }
                    val iou = if (uni == 0) 1.0 else inter.toDouble() / uni
                    onDone(poly, iou, liveArea, liveFrac())
                }
            }, Modifier.weight(1f), enabled = maskCount > 0) { Text("完成修邊") }
        }
    }
}

/** 多邊形 scanline 填充(even-odd)→ mask(工作解析度)。 */
private fun scanlineFill(poly: List<List<Int>>, s: Float, mw: Int, mh: Int, out: ByteArray) {
    if (poly.size < 3) return
    val xs = FloatArray(poly.size) { poly[it][0] * s }
    val ys = FloatArray(poly.size) { poly[it][1] * s }
    val cuts = ArrayList<Float>(16)
    for (y in 0 until mh) {
        val yc = y + 0.5f
        cuts.clear()
        var j = poly.size - 1
        for (i in poly.indices) {
            val yi = ys[i]; val yj = ys[j]
            if ((yi > yc) != (yj > yc)) cuts.add(xs[i] + (yc - yi) * (xs[j] - xs[i]) / (yj - yi))
            j = i
        }
        cuts.sort()
        var t = 0
        while (t + 1 < cuts.size) {
            val x0 = max(0, cuts[t].roundToInt()); val x1 = min(mw - 1, cuts[t + 1].roundToInt())
            for (x in x0..x1) out[y * mw + x] = 1
            t += 2
        }
    }
}

/** 最大連通元件外邊界(Moore-neighbor)。 */
private fun traceLargestBoundary(mask: ByteArray, mw: Int, mh: Int): List<FloatArray> {
    val label = IntArray(mw * mh)
    var bestLbl = 0; var bestCnt = 0; var lbl = 0
    val stack = IntArray(mw * mh)
    for (start in mask.indices) {
        if (mask[start].toInt() != 0 && label[start] == 0) {
            lbl++; var top = 0; stack[top++] = start; label[start] = lbl; var cnt = 0
            while (top > 0) {
                val p = stack[--top]; cnt++
                val px = p % mw; val py = p / mw
                if (px > 0 && mask[p - 1].toInt() != 0 && label[p - 1] == 0) { label[p - 1] = lbl; stack[top++] = p - 1 }
                if (px < mw - 1 && mask[p + 1].toInt() != 0 && label[p + 1] == 0) { label[p + 1] = lbl; stack[top++] = p + 1 }
                if (py > 0 && mask[p - mw].toInt() != 0 && label[p - mw] == 0) { label[p - mw] = lbl; stack[top++] = p - mw }
                if (py < mh - 1 && mask[p + mw].toInt() != 0 && label[p + mw] == 0) { label[p + mw] = lbl; stack[top++] = p + mw }
            }
            if (cnt > bestCnt) { bestCnt = cnt; bestLbl = lbl }
        }
    }
    if (bestLbl == 0) return emptyList()
    fun on(x: Int, y: Int) = x in 0 until mw && y in 0 until mh && label[y * mw + x] == bestLbl
    var sx = -1; var sy = -1
    outer@ for (y in 0 until mh) for (x in 0 until mw) if (on(x, y)) { sx = x; sy = y; break@outer }
    val dirs = arrayOf(intArrayOf(0, -1), intArrayOf(1, -1), intArrayOf(1, 0), intArrayOf(1, 1),
                       intArrayOf(0, 1), intArrayOf(-1, 1), intArrayOf(-1, 0), intArrayOf(-1, -1))
    val pts = ArrayList<FloatArray>()
    var cx = sx; var cy = sy; var d = 6
    val cap = 4 * (mw + mh) * 4
    var steps = 0
    do {
        pts.add(floatArrayOf(cx.toFloat(), cy.toFloat()))
        var found = false
        for (i in 0 until 8) {
            val nd = (d + i) % 8
            val nx = cx + dirs[nd][0]; val ny = cy + dirs[nd][1]
            if (on(nx, ny)) { cx = nx; cy = ny; d = (nd + 6) % 8; found = true; break }
        }
        if (!found) break
        steps++
    } while ((cx != sx || cy != sy) && steps < cap)
    return pts
}

/** Ramer–Douglas–Peucker 精簡。 */
private fun rdp(pts: List<FloatArray>, eps: Double): List<FloatArray> {
    if (pts.size < 8) return pts
    val keep = BooleanArray(pts.size)
    keep[0] = true; keep[pts.size - 1] = true
    val stack = ArrayDeque<IntArray>(); stack.add(intArrayOf(0, pts.size - 1))
    while (stack.isNotEmpty()) {
        val seg = stack.removeLast(); val a = seg[0]; val b = seg[1]
        var maxD = 0.0; var idx = -1
        val ax = pts[a][0]; val ay = pts[a][1]; val bx = pts[b][0]; val by = pts[b][1]
        val dx = bx - ax; val dy = by - ay
        val len = sqrt((dx * dx + dy * dy).toDouble()).coerceAtLeast(1e-6)
        for (i in a + 1 until b) {
            val dist = abs((pts[i][0] - ax) * dy - (pts[i][1] - ay) * dx) / len
            if (dist > maxD) { maxD = dist; idx = i }
        }
        if (maxD > eps && idx > 0) { keep[idx] = true; stack.add(intArrayOf(a, idx)); stack.add(intArrayOf(idx, b)) }
    }
    return pts.filterIndexed { i, _ -> keep[i] }
}
