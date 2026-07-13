package com.woundmeasurement.app.pipeline

import android.graphics.Bitmap
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlin.math.abs
import kotlin.math.roundToInt

/**
 * 醫師修邊(專屬全螢幕頁,對齊原型 v_review 邊界模式)。
 * 邊界=GT:拖控制點改邊界、＋加節點(最長邊中點)、刪節點(選取後)、undo/redo;
 * 即時重算面積(以原始面積×新舊多邊形像素面積比)與 PUSH(WoundPipeline)。免重傳後端。
 * 完成回傳:修正後 polygon、correction_iou(與原始遮罩 IoU)、新面積。
 * 組織筆刷(重標記各組織)為下一輪 B。輔助、非診斷、需醫師確認。
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
    val initPts = remember(initialPolygon) { initialPolygon.map { Offset(it[0].toFloat(), it[1].toFloat()) } }
    var pts by remember { mutableStateOf(initPts) }
    var selectedIdx by remember { mutableStateOf(-1) }
    val undo = remember { mutableStateListOf<List<Offset>>() }
    val redo = remember { mutableStateListOf<List<Offset>>() }

    val origPx = remember(initPts) { shoelace(initPts) }
    fun newArea(): Double? {
        if (originalArea == null || origPx <= 0.0) return originalArea
        return originalArea * shoelace(pts) / origPx
    }
    fun pushPartial(a: Double?): Int? = WoundPipeline.push(a, tissueFrac, exudate).partial

    val liveArea = newArea()
    val livePush = pushPartial(liveArea)

    Column(Modifier.fillMaxSize().padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("醫師修邊・邊界(=GT)", style = MaterialTheme.typography.titleMedium)
        Text("面積 ${liveArea?.let { "%.2f".format(it) } ?: "-"} cm²  ·  PUSH ${livePush ?: "-"}",
            style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.primary)
        Text("拖紅點改邊界;選取後可刪點;＋加點於最長邊中點。", fontSize = 12.sp,
            color = MaterialTheme.colorScheme.onSurfaceVariant)

        Canvas(
            modifier = Modifier
                .fillMaxWidth()
                .aspectRatio(bitmap.width.toFloat() / bitmap.height.coerceAtLeast(1))
                .pointerInput(Unit) {
                    var startSnapshot: List<Offset>? = null
                    detectDragGestures(
                        onDragStart = { off ->
                            val sc = size.width.toFloat() / bitmap.width
                            val imgPt = Offset(off.x / sc, off.y / sc)
                            val i = pts.indices.minByOrNull { (pts[it] - imgPt).getDistanceSquared() } ?: -1
                            selectedIdx = if (i >= 0 && (pts[i] - imgPt).getDistance() * sc <= 60f) i else -1
                            startSnapshot = if (selectedIdx >= 0) pts else null
                        },
                        onDrag = { change, delta ->
                            change.consume()
                            if (selectedIdx >= 0) {
                                val sc = size.width.toFloat() / bitmap.width
                                val d = Offset(delta.x / sc, delta.y / sc)
                                pts = pts.toMutableList().also { it[selectedIdx] = it[selectedIdx] + d }
                            }
                        },
                        onDragEnd = { startSnapshot?.let { undo.add(it); redo.clear() }; startSnapshot = null },
                        onDragCancel = { startSnapshot = null }
                    )
                }
        ) {
            val sc = size.width / bitmap.width
            drawImage(
                image = img,
                srcOffset = IntOffset.Zero, srcSize = IntSize(bitmap.width, bitmap.height),
                dstOffset = IntOffset.Zero, dstSize = IntSize(size.width.roundToInt(), size.height.roundToInt())
            )
            for (i in pts.indices) {
                drawLine(Color(0xFFFFEB00), pts[i] * sc, pts[(i + 1) % pts.size] * sc, strokeWidth = 4f)
            }
            pts.forEachIndexed { i, p ->
                drawCircle(if (i == selectedIdx) Color(0xFF35C759) else Color(0xFFFF3030), 13f, p * sc)
            }
        }

        // 工具列:undo / redo / ＋加點 / 刪點
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            OutlinedButton({ if (undo.isNotEmpty()) { redo.add(pts); pts = undo.removeAt(undo.lastIndex) } },
                enabled = undo.isNotEmpty(), modifier = Modifier.weight(1f)) { Text("↺ 復原") }
            OutlinedButton({ if (redo.isNotEmpty()) { undo.add(pts); pts = redo.removeAt(redo.lastIndex) } },
                enabled = redo.isNotEmpty(), modifier = Modifier.weight(1f)) { Text("↩ 重做") }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            OutlinedButton({
                // 於最長邊中點插入節點
                if (pts.size >= 2) {
                    var bi = 0; var bd = -1.0
                    for (i in pts.indices) { val d = (pts[i] - pts[(i + 1) % pts.size]).getDistanceSquared(); if (d > bd) { bd = d.toDouble(); bi = i } }
                    val mid = (pts[bi] + pts[(bi + 1) % pts.size]) / 2f
                    undo.add(pts); redo.clear()
                    pts = pts.toMutableList().also { it.add(bi + 1, mid) }
                }
            }, modifier = Modifier.weight(1f)) { Text("＋ 加點") }
            OutlinedButton({
                if (selectedIdx in pts.indices && pts.size > 3) {
                    undo.add(pts); redo.clear()
                    pts = pts.toMutableList().also { it.removeAt(selectedIdx) }; selectedIdx = -1
                }
            }, enabled = selectedIdx >= 0 && pts.size > 3, modifier = Modifier.weight(1f)) { Text("刪點") }
        }

        Spacer(Modifier.weight(1f))
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
