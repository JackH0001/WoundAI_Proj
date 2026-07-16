package com.woundmeasurement.app.pipeline

import android.graphics.Bitmap
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
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

/**
 * 醫師修邊(專屬全螢幕頁,對齊原型 v_review 邊界模式)。
 * 版面:畫布佔中間(weight),工具列/完成鈕固定底部(不被遮蔽、免捲動)。
 * 視圖:進入自動將 ROI(輪廓外框)放大至編修框 ~50%;「＋/－」縮放、「ROI/全圖」快速切換;
 *      單指拖「頂點」=修邊;拖「空白處」=平移。
 * 即時重算:面積(原始面積×新舊多邊形像素面積比)與 PUSH。免重傳後端。
 * 完成回傳:修正後 polygon、correction_iou、新面積。輔助、非診斷、需醫師確認。
 */
@Composable
fun WoundEditScreen(
    bitmap: Bitmap,
    initialPolygon: List<List<Int>>,
    originalArea: Double?,
    tissueFrac: Map<String, Double>,
    exudate: Int?,
    onCancel: () -> Unit,
    onDone: (edited: List<List<Int>>, correctionIou: Double?, newArea: Double?) -> Unit
) {
    val img = remember(bitmap) { bitmap.asImageBitmap() }
    val bw = bitmap.width.toFloat(); val bh = bitmap.height.toFloat()
    val initPts = remember(initialPolygon) { initialPolygon.map { Offset(it[0].toFloat(), it[1].toFloat()) } }
    var pts by remember { mutableStateOf(initPts) }
    var selectedIdx by remember { mutableStateOf(-1) }
    val undo = remember { mutableStateListOf<List<Offset>>() }
    val redo = remember { mutableStateListOf<List<Offset>>() }

    // 視圖狀態:boxSize=編修框px;k=base*viewScale;viewOffset=影像座標的視窗左上角
    var boxSize by remember { mutableStateOf(IntSize.Zero) }
    var viewScale by remember { mutableStateOf(1f) }
    var viewOffset by remember { mutableStateOf(Offset.Zero) }
    var viewInit by remember { mutableStateOf(false) }
    fun base(): Float = if (boxSize == IntSize.Zero) 1f else min(boxSize.width / bw, boxSize.height / bh)
    fun k(): Float = base() * viewScale

    fun fitFull() {
        viewScale = 1f
        val kk = k()
        viewOffset = Offset((bw - boxSize.width / kk) / 2f, (bh - boxSize.height / kk) / 2f)
    }
    fun fitRoi() {
        if (pts.isEmpty() || boxSize == IntSize.Zero) return
        val minX = pts.minOf { it.x }; val maxX = pts.maxOf { it.x }
        val minY = pts.minOf { it.y }; val maxY = pts.maxOf { it.y }
        val w = max(maxX - minX, 8f); val h = max(maxY - minY, 8f)
        // ROI 佔編修框 ~50%
        val kT = 0.5f * min(boxSize.width / w, boxSize.height / h)
        viewScale = (kT / base()).coerceIn(0.5f, 24f)
        val kk = k()
        viewOffset = Offset(
            (minX + maxX) / 2f - boxSize.width / (2f * kk),
            (minY + maxY) / 2f - boxSize.height / (2f * kk)
        )
    }
    fun zoomBy(f: Float) {
        if (boxSize == IntSize.Zero) return
        val c = Offset(boxSize.width / 2f, boxSize.height / 2f)
        val centerImg = viewOffset + c / k()
        viewScale = (viewScale * f).coerceIn(0.5f, 24f)
        viewOffset = centerImg - c / k()
    }
    // 首次量到框尺寸→自動 ROI 50%
    LaunchedEffect(boxSize) {
        if (!viewInit && boxSize != IntSize.Zero) { fitRoi(); viewInit = true }
    }

    val origPx = remember(initPts) { shoelace(initPts) }
    fun newArea(): Double? {
        if (originalArea == null || origPx <= 0.0) return originalArea
        return originalArea * shoelace(pts) / origPx
    }
    val liveArea = newArea()
    val livePush = WoundPipeline.push(liveArea, tissueFrac, exudate).partial

    Column(Modifier.fillMaxSize().navigationBarsPadding().padding(10.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text("修邊・邊界(=GT)   面積 ${liveArea?.let { "%.2f".format(it) } ?: "-"} cm² · PUSH ${livePush ?: "-"}",
            style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.primary)
        Text("拖紅點=修邊;拖空白=平移;－/＋縮放;ROI=放大傷口區", fontSize = 11.sp,
            color = MaterialTheme.colorScheme.onSurfaceVariant)

        // 編修框(佔中間;工具列固定在下,不會被擠出畫面)
        Box(
            Modifier
                .fillMaxWidth()
                .weight(1f)
                .clipToBounds()
                .onSizeChanged { boxSize = it }
        ) {
            Canvas(
                Modifier
                    .fillMaxSize()
                    .pointerInput(Unit) {
                        var startSnapshot: List<Offset>? = null
                        var mode = 0 // 0=none 1=vertex 2=pan
                        detectDragGestures(
                            onDragStart = { off ->
                                val kk = k()
                                val imgPt = off / kk + viewOffset
                                val i = pts.indices.minByOrNull { (pts[it] - imgPt).getDistanceSquared() } ?: -1
                                if (i >= 0 && (pts[i] - imgPt).getDistance() * kk <= 48f) {
                                    selectedIdx = i; mode = 1; startSnapshot = pts
                                } else { mode = 2 }
                            },
                            onDrag = { change, delta ->
                                change.consume()
                                val kk = k()
                                when (mode) {
                                    1 -> if (selectedIdx >= 0)
                                        pts = pts.toMutableList().also { it[selectedIdx] = it[selectedIdx] + delta / kk }
                                    2 -> viewOffset -= delta / kk
                                }
                            },
                            onDragEnd = {
                                if (mode == 1) startSnapshot?.let { undo.add(it); redo.clear() }
                                startSnapshot = null; mode = 0
                            },
                            onDragCancel = { startSnapshot = null; mode = 0 }
                        )
                    }
            ) {
                val kk = k()
                drawImage(
                    image = img,
                    srcOffset = IntOffset.Zero, srcSize = IntSize(bitmap.width, bitmap.height),
                    dstOffset = IntOffset((-viewOffset.x * kk).roundToInt(), (-viewOffset.y * kk).roundToInt()),
                    dstSize = IntSize((bw * kk).roundToInt(), (bh * kk).roundToInt())
                )
                fun sp(p: Offset) = (p - viewOffset) * kk
                for (i in pts.indices) {
                    drawLine(Color(0xFFFFEB00), sp(pts[i]), sp(pts[(i + 1) % pts.size]), strokeWidth = 4f)
                }
                pts.forEachIndexed { i, p ->
                    drawCircle(if (i == selectedIdx) Color(0xFF35C759) else Color(0xFFFF3030), 13f, sp(p))
                }
            }
        }

        // 縮放列
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            OutlinedButton({ zoomBy(1 / 1.3f) }, Modifier.weight(1f), contentPadding = PaddingValues(4.dp)) { Text("－") }
            OutlinedButton({ zoomBy(1.3f) }, Modifier.weight(1f), contentPadding = PaddingValues(4.dp)) { Text("＋") }
            OutlinedButton({ fitRoi() }, Modifier.weight(1f), contentPadding = PaddingValues(4.dp)) { Text("ROI") }
            OutlinedButton({ fitFull() }, Modifier.weight(1f), contentPadding = PaddingValues(4.dp)) { Text("全圖") }
        }
        // 編輯列
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            OutlinedButton({ if (undo.isNotEmpty()) { redo.add(pts); pts = undo.removeAt(undo.lastIndex) } },
                enabled = undo.isNotEmpty(), modifier = Modifier.weight(1f), contentPadding = PaddingValues(4.dp)) { Text("↺復原") }
            OutlinedButton({ if (redo.isNotEmpty()) { undo.add(pts); pts = redo.removeAt(redo.lastIndex) } },
                enabled = redo.isNotEmpty(), modifier = Modifier.weight(1f), contentPadding = PaddingValues(4.dp)) { Text("↩重做") }
            OutlinedButton({
                if (pts.size >= 2) {
                    var bi = 0; var bd = -1.0
                    for (i in pts.indices) { val d = (pts[i] - pts[(i + 1) % pts.size]).getDistanceSquared(); if (d > bd) { bd = d.toDouble(); bi = i } }
                    val mid = (pts[bi] + pts[(bi + 1) % pts.size]) / 2f
                    undo.add(pts); redo.clear()
                    pts = pts.toMutableList().also { it.add(bi + 1, mid) }
                }
            }, modifier = Modifier.weight(1f), contentPadding = PaddingValues(4.dp)) { Text("＋加點") }
            OutlinedButton({
                if (selectedIdx in pts.indices && pts.size > 3) {
                    undo.add(pts); redo.clear()
                    pts = pts.toMutableList().also { it.removeAt(selectedIdx) }; selectedIdx = -1
                }
            }, enabled = selectedIdx >= 0 && pts.size > 3, modifier = Modifier.weight(1f), contentPadding = PaddingValues(4.dp)) { Text("刪點") }
        }
        // 完成列(固定底部)
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            OutlinedButton(onCancel, Modifier.weight(1f)) { Text("取消") }
            Button({
                val edited = pts.map { listOf(it.x.roundToInt(), it.y.roundToInt()) }
                onDone(edited, maskIou(initialPolygon, edited, bitmap.width, bitmap.height), newArea())
            }, Modifier.weight(1f)) { Text("完成修邊") }
        }
    }
}

/** 多邊形面積(Shoelace,像素)。 */
private fun shoelace(p: List<Offset>): Double {
    if (p.size < 3) return 0.0
    var s = 0.0
    for (i in p.indices) { val j = (i + 1) % p.size; s += p[i].x.toDouble() * p[j].y - p[j].x.toDouble() * p[i].y }
    return abs(s) / 2.0
}

private fun pointInPoly(x: Float, y: Float, poly: List<List<Int>>): Boolean {
    var inside = false; var j = poly.size - 1
    for (i in poly.indices) {
        val xi = poly[i][0].toFloat(); val yi = poly[i][1].toFloat()
        val xj = poly[j][0].toFloat(); val yj = poly[j][1].toFloat()
        if (((yi > y) != (yj > y)) && (x < (xj - xi) * (y - yi) / (yj - yi) + xi)) inside = !inside
        j = i
    }
    return inside
}

/** 兩多邊形遮罩 IoU(粗網格)→ correction_iou。 */
private fun maskIou(a: List<List<Int>>, b: List<List<Int>>, w: Int, h: Int): Double {
    if (a.size < 3 || b.size < 3) return 1.0
    val step = maxOf(1, maxOf(w, h) / 120)
    var inter = 0; var uni = 0; var y = 0
    while (y < h) {
        var x = 0
        while (x < w) {
            val ina = pointInPoly(x.toFloat(), y.toFloat(), a); val inb = pointInPoly(x.toFloat(), y.toFloat(), b)
            if (ina || inb) uni++; if (ina && inb) inter++
            x += step
        }
        y += step
    }
    return if (uni == 0) 1.0 else inter.toDouble() / uni
}
