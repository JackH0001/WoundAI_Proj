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
 * 醫師修邊(對齊原型 v_review):邊界筆刷(=GT)+組織筆刷(互斥塗蓋)+亮青邊界線。
 * 柵格:ROI 高解析(≈原圖像素);**筆刷靠近框緣自動擴張視窗**(同解析度、內容像素級搬移,
 * cm²/px 係數不變)→ AI 初始只抓到局部時,傷口其餘部分照樣塗得到(修正「灰框限制修邊」)。
 * 遮罩跨回合持久化(EditRaster)零損耗;面積=像素數×鎖定係數,冪等。輔助、非診斷、需醫師確認。
 */
private enum class EditTool { B_PAINT, B_ERASE, PAN, TISSUE }

private val T_NAMES = arrayOf("", "肉芽", "腐肉", "壞死", "上皮")
private val T_COLORS = intArrayOf(
    0,
    android.graphics.Color.argb(70, 220, 60, 60),
    android.graphics.Color.argb(110, 235, 210, 70),
    android.graphics.Color.argb(130, 40, 40, 40),
    android.graphics.Color.argb(110, 240, 150, 170)
)
private val EDGE_COLOR = android.graphics.Color.argb(255, 0, 229, 255)
private const val MAX_MASK_DIM = 2200   // 擴張上限(記憶體防護)

/** 編輯狀態持久化:遮罩為唯一真相,跨回合原樣傳遞;cm2PerPx 首次鎖定(面積冪等)。 */
class EditRaster(
    val mask: ByteArray, val tissue: ByteArray, val origMask: ByteArray,
    val rx0: Float, val ry0: Float, val mw: Int, val mh: Int,
    val mScale: Float, val cm2PerPx: Double?
)

/** 可擴張柵格(非 Compose 狀態;變更後由呼叫端 version++ 觸發重繪)。 */
private class RasterState(
    var rx0: Float, var ry0: Float, var mw: Int, var mh: Int,
    val mScale: Float, val bw: Int, val bh: Int
) {
    var mask = ByteArray(mw * mh)
    var tissue = ByteArray(mw * mh)
    var orig = ByteArray(mw * mh)
    var overlay: Bitmap = Bitmap.createBitmap(mw, mh, Bitmap.Config.ARGB_8888)
    var maskCount = 0
    val tCounts = intArrayOf(0, 0, 0, 0, 0)
    var cm2PerPx: Double? = null

    fun colorAt(x: Int, y: Int): Int {
        val i = y * mw + x
        if (mask[i].toInt() == 0) return 0
        val edge = x == 0 || y == 0 || x == mw - 1 || y == mh - 1 ||
                mask[i - 1].toInt() == 0 || mask[i + 1].toInt() == 0 ||
                mask[i - mw].toInt() == 0 || mask[i + mw].toInt() == 0
        return if (edge) EDGE_COLOR else T_COLORS[tissue[i].toInt().coerceIn(1, 4)]
    }
    fun syncAll() {
        val px = IntArray(mw * mh)
        for (y in 0 until mh) for (x in 0 until mw) px[y * mw + x] = colorAt(x, y)
        overlay.setPixels(px, 0, mw, 0, 0, mw, mh)
    }
    fun refresh(rx: Int, ry: Int, rx1: Int, ry1: Int) {
        val a0 = max(0, rx); val b0 = max(0, ry)
        val a1 = min(mw - 1, rx1); val b1 = min(mh - 1, ry1)
        for (y in b0..b1) for (x in a0..a1) overlay.setPixel(x, y, colorAt(x, y))
    }
    fun recount() {
        tCounts.fill(0); var c = 0
        for (i in mask.indices) if (mask[i].toInt() != 0) { c++; tCounts[tissue[i].toInt().coerceIn(1, 4)]++ }
        maskCount = c
    }
    /** 視需要向外擴張(維持 mScale;內容整格搬移,像素級無損)。回傳是否擴張。 */
    fun expandIfNeeded(cxM: Float, cyM: Float, rM: Float): Boolean {
        val margin = rM + 6f
        var gL = 0; var gR = 0; var gT = 0; var gB = 0
        val grow = max(64, max(mw, mh) / 2)
        if (cxM - margin < 0) gL = grow
        if (cxM + margin > mw) gR = grow
        if (cyM - margin < 0) gT = grow
        if (cyM + margin > mh) gB = grow
        if (gL + gR + gT + gB == 0) return false
        // 邊界夾擠:不可超出影像、不可超過總尺寸上限
        gL = min(gL, (rx0 * mScale).toInt().coerceAtLeast(0))
        gT = min(gT, (ry0 * mScale).toInt().coerceAtLeast(0))
        val rightRoom = ((bw - (rx0 + mw / mScale)) * mScale).toInt().coerceAtLeast(0)
        val bottomRoom = ((bh - (ry0 + mh / mScale)) * mScale).toInt().coerceAtLeast(0)
        gR = min(gR, rightRoom); gB = min(gB, bottomRoom)
        if (mw + gL + gR > MAX_MASK_DIM) { val over = mw + gL + gR - MAX_MASK_DIM; gR = (gR - over).coerceAtLeast(0); }
        if (mh + gT + gB > MAX_MASK_DIM) { val over = mh + gT + gB - MAX_MASK_DIM; gB = (gB - over).coerceAtLeast(0); }
        if (gL + gR + gT + gB == 0) return false
        val nw = mw + gL + gR; val nh = mh + gT + gB
        fun move(src: ByteArray): ByteArray {
            val d = ByteArray(nw * nh)
            for (y in 0 until mh) System.arraycopy(src, y * mw, d, (y + gT) * nw + gL, mw)
            return d
        }
        mask = move(mask); tissue = move(tissue); orig = move(orig)
        rx0 -= gL / mScale; ry0 -= gT / mScale
        mw = nw; mh = nh
        overlay = Bitmap.createBitmap(mw, mh, Bitmap.Config.ARGB_8888)
        syncAll()
        return true
    }
    fun maskBBoxImg(): FloatArray? {  // [x0,y0,x1,y1] 影像座標
        var x0 = Int.MAX_VALUE; var y0 = Int.MAX_VALUE; var x1 = -1; var y1 = -1
        for (y in 0 until mh) for (x in 0 until mw) if (mask[y * mw + x].toInt() != 0) {
            if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y
        }
        if (x1 < 0) return null
        return floatArrayOf(rx0 + x0 / mScale, ry0 + y0 / mScale, rx0 + x1 / mScale, ry0 + y1 / mScale)
    }
}

@Composable
fun WoundEditScreen(
    bitmap: Bitmap,
    initialPolygon: List<List<Int>>,
    originalArea: Double?,
    tissueFrac: Map<String, Double>,
    exudate: Int?,
    mmPerPx: Double? = null,      // ArUco 尺度直傳:面積=像素數×(mm/px)²(優先;不依賴 AI 初始面積)
    resume: EditRaster? = null,
    onCancel: () -> Unit,
    onDone: (edited: List<List<Int>>, correctionIou: Double?, newArea: Double?, tissue: Map<String, Double>, raster: EditRaster) -> Unit
) {
    val img = remember(bitmap) { bitmap.asImageBitmap() }
    val bw = bitmap.width; val bh = bitmap.height

    val defaultClass = remember {
        val cand = listOf("granulation" to 1, "slough" to 2, "necrosis" to 3, "epithelial" to 4)
        (cand.maxByOrNull { tissueFrac[it.first] ?: 0.0 }?.takeIf { (tissueFrac[it.first] ?: 0.0) > 0.0 }?.second) ?: 1
    }
    var version by remember { mutableStateOf(0) }

    val st = remember(initialPolygon, resume) {
        if (resume != null) {
            RasterState(resume.rx0, resume.ry0, resume.mw, resume.mh, resume.mScale, bw, bh).apply {
                System.arraycopy(resume.mask, 0, mask, 0, mask.size)
                System.arraycopy(resume.tissue, 0, tissue, 0, tissue.size)
                System.arraycopy(resume.origMask, 0, orig, 0, orig.size)
                cm2PerPx = if (mmPerPx != null) (mmPerPx * mmPerPx / 100.0) / (resume.mScale * resume.mScale).toDouble()
                           else resume.cm2PerPx
                recount(); syncAll()
            }
        } else {
            // 初始 ROI=AI 遮罩外框+60% 邊距(AI 低估時仍可自動擴張,不受限)
            val xs = initialPolygon.map { it[0] }; val ys = initialPolygon.map { it[1] }
            val hasPoly = initialPolygon.size >= 3
            val w = if (hasPoly) (xs.max() - xs.min()).coerceAtLeast(16) else bw
            val h = if (hasPoly) (ys.max() - ys.min()).coerceAtLeast(16) else bh
            val mgx = (w * 0.6f).roundToInt().coerceAtLeast(48); val mgy = (h * 0.6f).roundToInt().coerceAtLeast(48)
            val x0 = if (hasPoly) (xs.min() - mgx).coerceAtLeast(0) else 0
            val y0 = if (hasPoly) (ys.min() - mgy).coerceAtLeast(0) else 0
            val x1 = if (hasPoly) (xs.max() + mgx).coerceAtMost(bw - 1) else bw - 1
            val y1 = if (hasPoly) (ys.max() + mgy).coerceAtMost(bh - 1) else bh - 1
            val rw = x1 - x0 + 1; val rh = y1 - y0 + 1
            val sc = min(1f, 1024f / max(rw, rh))
            RasterState(x0.toFloat(), y0.toFloat(), max(8, (rw * sc).roundToInt()), max(8, (rh * sc).roundToInt()), sc, bw, bh).apply {
                scanlineFill(initialPolygon, mScale, mw, mh, mask, rx0, ry0)
                var c = 0
                for (i in mask.indices) if (mask[i].toInt() != 0) { c++; tissue[i] = defaultClass.toByte() }
                maskCount = c; tCounts[defaultClass] = c
                System.arraycopy(mask, 0, orig, 0, mask.size)
                // 係數優先序:ArUco 尺度直傳(精確,=(mm/px)²/100/mScale²) > AI面積/像素數(後備)
                cm2PerPx = if (mmPerPx != null) (mmPerPx * mmPerPx / 100.0) / (mScale * mScale).toDouble()
                           else if (originalArea != null && c > 0) originalArea / c else null
                syncAll()
            }
        }
    }
    val initOrigCount = remember(st) { st.orig.count { it.toInt() != 0 } }

    // ---- 視圖 ----
    var boxSize by remember { mutableStateOf(IntSize.Zero) }
    var viewScale by remember { mutableStateOf(1f) }
    var viewOffset by remember { mutableStateOf(Offset.Zero) }
    var viewInit by remember { mutableStateOf(false) }
    fun base(): Float = if (boxSize == IntSize.Zero) 1f else min(boxSize.width / bw.toFloat(), boxSize.height / bh.toFloat())
    fun k(): Float = base() * viewScale
    fun fitFull() { viewScale = 1f; val kk = k(); viewOffset = Offset((bw - boxSize.width / kk) / 2f, (bh - boxSize.height / kk) / 2f) }
    fun fitRoi() {
        val bb = st.maskBBoxImg() ?: return
        if (boxSize == IntSize.Zero) return
        val w = max(bb[2] - bb[0], 8f); val h = max(bb[3] - bb[1], 8f)
        val kT = 0.5f * min(boxSize.width / w, boxSize.height / h)
        viewScale = (kT / base()).coerceIn(0.5f, 24f)
        val kk = k()
        viewOffset = Offset((bb[0] + bb[2]) / 2f - boxSize.width / (2f * kk),
                            (bb[1] + bb[3]) / 2f - boxSize.height / (2f * kk))
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
    var curTissue by remember { mutableStateOf(2) }
    var brushScreen by remember { mutableStateOf(36f) }
    var cursor by remember { mutableStateOf<Offset?>(null) }

    class Snap(val m: ByteArray, val t: ByteArray, val mw: Int, val mh: Int, val rx0: Float, val ry0: Float)
    val undo = remember(st) { mutableStateListOf<Snap>() }
    val redo = remember(st) { mutableStateListOf<Snap>() }
    fun snap() = Snap(st.mask.copyOf(), st.tissue.copyOf(), st.mw, st.mh, st.rx0, st.ry0)
    fun restore(s: Snap): Boolean {
        if (s.mw != st.mw || s.mh != st.mh) return false   // 擴張後尺寸不同→無法還原(已於擴張時清空)
        System.arraycopy(s.m, 0, st.mask, 0, st.mask.size)
        System.arraycopy(s.t, 0, st.tissue, 0, st.tissue.size)
        st.recount(); st.syncAll(); return true
    }

    fun stamp(imgPt: Offset) {
        val rM = max(1f, (brushScreen / k()) * st.mScale)
        var cx = (imgPt.x - st.rx0) * st.mScale
        var cy = (imgPt.y - st.ry0) * st.mScale
        if (tool == EditTool.B_PAINT || tool == EditTool.B_ERASE || tool == EditTool.TISSUE) {
            if (st.expandIfNeeded(cx, cy, rM)) {           // 視窗擴張(內容無損);undo 尺寸失效→清空
                undo.clear(); redo.clear()
                cx = (imgPt.x - st.rx0) * st.mScale; cy = (imgPt.y - st.ry0) * st.mScale
            }
        }
        val r2 = rM * rM
        val x0 = max(0, (cx - rM).toInt()); val x1 = min(st.mw - 1, (cx + rM).toInt())
        val y0 = max(0, (cy - rM).toInt()); val y1 = min(st.mh - 1, (cy + rM).toInt())
        for (y in y0..y1) for (x in x0..x1) {
            val dx = x - cx; val dy = y - cy
            if (dx * dx + dy * dy > r2) continue
            val i = y * st.mw + x
            when (tool) {
                EditTool.B_PAINT -> if (st.mask[i].toInt() == 0) {
                    st.mask[i] = 1; st.maskCount++
                    st.tissue[i] = defaultClass.toByte(); st.tCounts[defaultClass]++
                }
                EditTool.B_ERASE -> if (st.mask[i].toInt() != 0) {
                    st.mask[i] = 0; st.maskCount--
                    val tc = st.tissue[i].toInt(); if (tc in 1..4) st.tCounts[tc]--
                    st.tissue[i] = 0
                }
                EditTool.TISSUE -> if (st.mask[i].toInt() != 0 && st.tissue[i].toInt() != curTissue) {
                    val tc = st.tissue[i].toInt(); if (tc in 1..4) st.tCounts[tc]--
                    st.tissue[i] = curTissue.toByte(); st.tCounts[curTissue]++
                }
                EditTool.PAN -> {}
            }
        }
        st.refresh(x0 - 1, y0 - 1, x1 + 1, y1 + 1)
        version++
    }
    fun stampLine(a: Offset, b: Offset) {
        val d = b - a; val len = sqrt(d.x * d.x + d.y * d.y)
        val stepPx = max(1f, (brushScreen / k()) * 0.5f)
        val n = max(1, (len / stepPx).toInt())
        for (i in 0..n) stamp(a + d * (i.toFloat() / n))
    }

    fun liveFrac(): Map<String, Double> {
        val tot = max(1, st.maskCount)
        return mapOf(
            "granulation" to st.tCounts[1].toDouble() / tot, "slough" to st.tCounts[2].toDouble() / tot,
            "necrosis" to st.tCounts[3].toDouble() / tot, "epithelial" to st.tCounts[4].toDouble() / tot,
            "other" to 0.0
        )
    }
    @Suppress("UNUSED_EXPRESSION") version
    val liveArea = st.cm2PerPx?.let { it * st.maskCount } ?: originalArea
    val lf = liveFrac()
    val livePush = WoundPipeline.push(liveArea, lf, exudate).partial

    Column(Modifier.fillMaxSize().navigationBarsPadding().padding(10.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text("修邊(=GT)  面積 ${liveArea?.let { "%.2f".format(it) } ?: "-"} cm² · PUSH ${livePush ?: "-"}" +
             "  [尺度:${if (mmPerPx != null) "ArUco✓" else "AI後備⚠"}]",
            style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.primary)
        Text("組織  肉芽${(lf["granulation"]!! * 100).toInt()}% · 腐肉${(lf["slough"]!! * 100).toInt()}% · " +
             "壞死${(lf["necrosis"]!! * 100).toInt()}% · 上皮${(lf["epithelial"]!! * 100).toInt()}%  (框會隨筆刷自動擴張)",
            fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        if (st.maskCount == 0)
            Text("⚠ AI 未偵測到傷口:請用「邊界＋」從零塗抹;ArUco 尺度仍有效,面積照常精確計算",
                fontSize = 12.sp, color = MaterialTheme.colorScheme.error)

        Box(Modifier.fillMaxWidth().weight(1f).clipToBounds().onSizeChanged { boxSize = it }) {
            Canvas(
                Modifier.fillMaxSize().pointerInput(Unit) {
                    var strokeSnapshot: Snap? = null
                    var last: Offset? = null
                    detectDragGestures(
                        onDragStart = { off ->
                            val kk = k(); val imgPt = off / kk + viewOffset
                            cursor = off
                            if (tool != EditTool.PAN) { strokeSnapshot = snap(); stamp(imgPt); last = imgPt }
                        },
                        onDrag = { change, delta ->
                            change.consume()
                            val kk = k(); cursor = change.position
                            if (tool == EditTool.PAN) viewOffset -= delta / kk
                            else { val cur = change.position / kk + viewOffset; last?.let { stampLine(it, cur) }; last = cur }
                        },
                        onDragEnd = {
                            strokeSnapshot?.let {
                                if (it.mw == st.mw && it.mh == st.mh) {   // 擴張過的舊快照丟棄
                                    if (undo.size >= 8) undo.removeAt(0); undo.add(it); redo.clear()
                                }
                            }
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
                val ovOff = IntOffset(((st.rx0 - viewOffset.x) * kk).roundToInt(), ((st.ry0 - viewOffset.y) * kk).roundToInt())
                val ovW = (st.mw / st.mScale * kk).roundToInt(); val ovH = (st.mh / st.mScale * kk).roundToInt()
                drawImage(st.overlay.asImageBitmap(), srcOffset = IntOffset.Zero, srcSize = IntSize(st.mw, st.mh),
                    dstOffset = ovOff, dstSize = IntSize(ovW, ovH))
                drawRect(Color(0x44888888),
                    topLeft = Offset(ovOff.x.toFloat(), ovOff.y.toFloat()),
                    size = androidx.compose.ui.geometry.Size(ovW.toFloat(), ovH.toFloat()),
                    style = androidx.compose.ui.graphics.drawscope.Stroke(width = 2f))
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

        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            FilterChip(tool == EditTool.B_PAINT, { tool = EditTool.B_PAINT }, { Text("邊界＋") }, modifier = Modifier.weight(1f))
            FilterChip(tool == EditTool.B_ERASE, { tool = EditTool.B_ERASE }, { Text("邊界－") }, modifier = Modifier.weight(1f))
            FilterChip(tool == EditTool.PAN, { tool = EditTool.PAN }, { Text("移動") }, modifier = Modifier.weight(1f))
            FilterChip(tool == EditTool.TISSUE, { tool = EditTool.TISSUE }, { Text("組織🖌") }, modifier = Modifier.weight(1f))
        }
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
                if (undo.isNotEmpty()) {
                    val cur = snap()
                    if (restore(undo.removeAt(undo.lastIndex))) { redo.add(cur); version++ }
                }
            }, enabled = undo.isNotEmpty(), modifier = Modifier.weight(1f), contentPadding = PaddingValues(2.dp)) { Text("↺") }
            OutlinedButton({
                if (redo.isNotEmpty()) {
                    val cur = snap()
                    if (restore(redo.removeAt(redo.lastIndex))) { undo.add(cur); version++ }
                }
            }, enabled = redo.isNotEmpty(), modifier = Modifier.weight(1f), contentPadding = PaddingValues(2.dp)) { Text("↩") }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            OutlinedButton(onCancel, Modifier.weight(1f)) { Text("取消") }
            Button({
                try {
                    val boundary = traceLargestBoundary(st.mask, st.mw, st.mh)
                    if (boundary.size >= 3) {
                        val simplified = rdp(boundary, 1.5)
                        val poly = simplified.map {
                            listOf((it[0] / st.mScale + st.rx0).roundToInt(), (it[1] / st.mScale + st.ry0).roundToInt())
                        }
                        var inter = 0; var uni = 0
                        for (i in st.mask.indices) {
                            val a = st.orig[i].toInt() != 0; val b = st.mask[i].toInt() != 0
                            if (a || b) uni++; if (a && b) inter++
                        }
                        val iou = if (uni == 0) 1.0 else inter.toDouble() / uni
                        val raster = EditRaster(st.mask.copyOf(), st.tissue.copyOf(), st.orig.copyOf(),
                            st.rx0, st.ry0, st.mw, st.mh, st.mScale, st.cm2PerPx)
                        onDone(poly, iou, liveArea, liveFrac(), raster)
                    }
                } catch (_: Exception) { onCancel() }
            }, Modifier.weight(1f), enabled = st.maskCount > 0) { Text("完成修邊") }
        }
    }
    // 供編譯期保留(初始 orig 計數目前僅供除錯/未來擴充)
    @Suppress("UNUSED_EXPRESSION") initOrigCount
}

/** 多邊形 scanline 填充(even-odd)→ mask(ROI 局部座標,工作解析度)。 */
private fun scanlineFill(poly: List<List<Int>>, s: Float, mw: Int, mh: Int, out: ByteArray, ox: Float = 0f, oy: Float = 0f) {
    if (poly.size < 3) return
    val xs = FloatArray(poly.size) { (poly[it][0] - ox) * s }
    val ys = FloatArray(poly.size) { (poly[it][1] - oy) * s }
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
