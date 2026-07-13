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
import kotlin.math.roundToInt

/**
 * 醫師修邊(修邊即標註)：顯示傷口原圖 + AI 傷口輪廓,可拖曳紅點修正邊界。
 * 完成 → 回傳修正後 polygon(影像座標)與 correction_iou(與原始遮罩 IoU,1.0=未改)。
 * 修正後 polygon 雜湊不同 → 飛輪不會誤判重複、正常入列;correction_iou 記錄修正幅度。
 * 輔助、非診斷、需醫師確認。
 */
@Composable
fun WoundEditScreen(
    bitmap: Bitmap,
    initialPolygon: List<List<Int>>,
    onCancel: () -> Unit,
    onDone: (List<List<Int>>, Double?) -> Unit
) {
    val img = remember(bitmap) { bitmap.asImageBitmap() }
    var pts by remember { mutableStateOf(initialPolygon.map { Offset(it[0].toFloat(), it[1].toFloat()) }) }
    var dragIdx by remember { mutableStateOf(-1) }

    Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("醫師修邊:拖曳紅點修正傷口邊界(改完按「完成修邊」)", style = MaterialTheme.typography.titleSmall)
        Canvas(
            modifier = Modifier
                .fillMaxWidth()
                .aspectRatio(bitmap.width.toFloat() / bitmap.height.coerceAtLeast(1))
                .pointerInput(Unit) {
                    detectDragGestures(
                        onDragStart = { off ->
                            val sc = size.width.toFloat() / bitmap.width
                            val imgPt = Offset(off.x / sc, off.y / sc)
                            val idx = pts.indices.minByOrNull { (pts[it] - imgPt).getDistanceSquared() } ?: -1
                            dragIdx = if (idx >= 0 && (pts[idx] - imgPt).getDistance() * sc <= 60f) idx else -1
                        },
                        onDrag = { change, delta ->
                            change.consume()
                            if (dragIdx >= 0) {
                                val sc = size.width.toFloat() / bitmap.width
                                val d = Offset(delta.x / sc, delta.y / sc)
                                pts = pts.toMutableList().also { it[dragIdx] = it[dragIdx] + d }
                            }
                        },
                        onDragEnd = { dragIdx = -1 },
                        onDragCancel = { dragIdx = -1 }
                    )
                }
        ) {
            val sc = size.width / bitmap.width
            drawImage(
                image = img,
                srcOffset = IntOffset.Zero,
                srcSize = IntSize(bitmap.width, bitmap.height),
                dstOffset = IntOffset.Zero,
                dstSize = IntSize(size.width.roundToInt(), size.height.roundToInt())
            )
            for (i in pts.indices) {
                val a = pts[i] * sc
                val b = pts[(i + 1) % pts.size] * sc
                drawLine(Color(0xFFFFEB00), a, b, strokeWidth = 4f)
            }
            pts.forEach { drawCircle(Color(0xFFFF3030), radius = 12f, center = it * sc) }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            OutlinedButton(onCancel, Modifier.weight(1f)) { Text("取消") }
            Button({
                val edited = pts.map { listOf(it.x.roundToInt(), it.y.roundToInt()) }
                onDone(edited, maskIou(initialPolygon, edited, bitmap.width, bitmap.height))
            }, Modifier.weight(1f)) { Text("完成修邊") }
        }
    }
}

/** 點是否在多邊形內(ray casting)。 */
private fun pointInPoly(x: Float, y: Float, poly: List<List<Int>>): Boolean {
    var inside = false
    var j = poly.size - 1
    for (i in poly.indices) {
        val xi = poly[i][0].toFloat(); val yi = poly[i][1].toFloat()
        val xj = poly[j][0].toFloat(); val yj = poly[j][1].toFloat()
        if (((yi > y) != (yj > y)) && (x < (xj - xi) * (y - yi) / (yj - yi) + xi)) inside = !inside
        j = i
    }
    return inside
}

/** 兩多邊形遮罩 IoU(粗網格取樣)→ correction_iou。1.0=幾乎未改;越小=修正越大。 */
private fun maskIou(a: List<List<Int>>, b: List<List<Int>>, w: Int, h: Int): Double {
    if (a.size < 3 || b.size < 3) return 1.0
    val step = maxOf(1, maxOf(w, h) / 120)
    var inter = 0; var uni = 0
    var y = 0
    while (y < h) {
        var x = 0
        while (x < w) {
            val ina = pointInPoly(x.toFloat(), y.toFloat(), a)
            val inb = pointInPoly(x.toFloat(), y.toFloat(), b)
            if (ina || inb) uni++
            if (ina && inb) inter++
            x += step
        }
        y += step
    }
    return if (uni == 0) 1.0 else inter.toDouble() / uni
}
