package com.woundmeasurement.app.pipeline

import android.graphics.Bitmap
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.calculateCentroid
import androidx.compose.foundation.gestures.calculatePan
import androidx.compose.foundation.gestures.calculateZoom
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsPressedAsState
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

// internal（非 private）：參照圖 AnalysisPreview 要用同一份調色盤。
// 兩邊各定義一份的話，某天只改了其中一邊，畫面與修邊畫面的顏色就對不上了。
internal val T_NAMES = arrayOf("", "肉芽", "腐肉", "壞死", "上皮", "其他")

/**
 * 組織圖層配色。索引＝本畫面的組織碼（1 肉芽 / 2 腐肉 / 3 壞死 / 4 上皮 / 5 其他）。
 *
 * ## 為什麼肉芽從紅改成綠
 *
 * 舊配色把肉芽畫成**半透明紅疊在紅色肉芽上**——那是資訊量為零的疊圖。
 * 而且它的 α 只有 70/255（27%），是四類裡最低的，於是最需要看清楚的那一類最看不見。
 * 綠是紅的互補色，疊在肉芽上對比最大，而且不會被誤認成組織本身的顏色。
 *
 * ## 為什麼四類的 α 要一致
 *
 * 舊值是 70 / 110 / 130 / 110，沒有理由地不一致。後果不只是美觀：
 * **α 高的區域看起來「比較多」**，醫師在比較相鄰兩塊組織的範圍時會被透明度誤導，
 * 而他正在做的判斷會直接變成訓練用的 GT。一致的 α 讓面積比較只反映面積。
 *
 * 選 T_ALPHA=115（45%）：低到看得見底下的組織紋理（醫師要靠紋理判斷），
 * 高到在強光下的手機螢幕上仍分得出區塊。
 */
internal const val T_ALPHA = 115
internal val T_COLORS = intArrayOf(
    0,
    android.graphics.Color.argb(T_ALPHA, 29, 158, 117),    // 肉芽：綠（紅的互補色）
    android.graphics.Color.argb(T_ALPHA, 239, 159, 39),    // 腐肉：琥珀
    android.graphics.Color.argb(T_ALPHA, 60, 52, 137),     // 壞死：深紫（純黑會與陰影混淆）
    android.graphics.Color.argb(T_ALPHA, 237, 147, 177),   // 上皮：粉
    android.graphics.Color.argb(T_ALPHA, 180, 178, 169)    // 其他／未分類：灰
)
private val EDGE_COLOR = android.graphics.Color.argb(255, 0, 229, 255)
/** 組織碼上限。加「其他」之後所有 coerce 都要跟著走——寫成常數才不會漏掉其中一處。 */
internal const val T_MAX = 5
private const val MAX_MASK_DIM = 2200   // 擴張上限(記憶體防護)

/** 編輯狀態持久化:遮罩為唯一真相,跨回合原樣傳遞;cm2PerPx 首次鎖定(面積冪等)。 */
class EditRaster(
    val mask: ByteArray, val tissue: ByteArray, val origMask: ByteArray,
    val rx0: Float, val ry0: Float, val mw: Int, val mh: Int,
    val mScale: Float, val cm2PerPx: Double?,
    /**
     * 醫師實際重畫的組織像素數（tissue 與分類器建議 auto 不同的部分）。
     *
     * ⚠ **這個欄位決定這張遮罩能不能拿去訓練。**
     *
     * 醫師若完全沒動組織筆刷，遮罩就是 `TissueSeg` 色彩啟發式的原樣輸出。
     * 拿它當 GT 訓練＝**用模型自己的輸出訓練自己**：驗證指標會很漂亮，
     * 因為模型只是在學會複製它已經會做的事，而臨床表現不會有任何改善。
     *
     * 沒有這個計數的話，訓練集會安靜地被未經人看過的啟發式輸出填滿，
     * 而且從資料本身完全看不出來——又是那個「沒有錯誤、沒有警告、結果是錯的」形狀。
     */
    val tissueEditedPx: Int = 0,
    /** 遮罩內像素總數，用來算修改比例。 */
    val maskPx: Int = 0,
    /**
     * 產生這份柵格時的畫布尺寸。續編前必須比對——
     * 柵格座標是相對於當時那張畫布算的，尺寸不同就整份作廢，
     * 寧可退回由多邊形重建，也不要把醫師的判斷搬到錯的位置。
     */
    val canvasW: Int = 0,
    val canvasH: Int = 0
) {
    val tissueEdited: Boolean get() = tissueEditedPx > 0
    val tissueEditRatio: Double get() = if (maskPx > 0) tissueEditedPx.toDouble() / maskPx else 0.0
}

/**
 * 用 [TissueSeg] 重算整個柵格的分類底稿（[RasterState.auto]）。
 *
 * 在**取樣網格**上分類再最近鄰放大，不在柵格解析度上逐像素跑：
 * 2200² 的柵格要 484 萬次 HSV 換算，而且結果是椒鹽狀雜點；
 * 512² 上算完再放大既快又平滑，代價只是分區邊界精細度——那本來就要靠醫師修。
 */
private fun seedAuto(st: RasterState, src: Bitmap) {
    val x0 = st.rx0.roundToInt().coerceIn(0, src.width - 2)
    val y0 = st.ry0.roundToInt().coerceIn(0, src.height - 2)
    val x1 = (st.rx0 + st.mw / st.mScale).roundToInt().coerceIn(x0 + 2, src.width)
    val y1 = (st.ry0 + st.mh / st.mScale).roundToInt().coerceIn(y0 + 2, src.height)
    val (gw, gh) = TissueSeg.grid(x1 - x0, y1 - y0)
    // 只在遮罩內分類。遮罩外的像素是皮膚與背景，分類它們既浪費又會把白平衡增益拉偏。
    val inside = ByteArray(gw * gh)
    for (gy in 0 until gh) for (gx in 0 until gw) {
        val mx = (gx * st.mw / gw).coerceIn(0, st.mw - 1)
        val my = (gy * st.mh / gh).coerceIn(0, st.mh - 1)
        if (st.mask[my * st.mw + mx].toInt() != 0) inside[gy * gw + gx] = 1
    }
    // 遮罩太小（例如 AI 沒抓到）時網格上可能一格都不落——那就整片算，之後由筆刷決定範圍。
    if (inside.none { it.toInt() != 0 }) java.util.Arrays.fill(inside, 1.toByte())

    val g = TissueSeg.classify(src, x0, y0, x1, y1, gw, gh, inside) ?: return
    for (y in 0 until st.mh) {
        val gy = (y * gh / st.mh).coerceIn(0, gh - 1)
        for (x in 0 until st.mw) {
            val gx = (x * gw / st.mw).coerceIn(0, gw - 1)
            st.auto[y * st.mw + x] = g[gy * gw + gx]
        }
    }
}

/** 可擴張柵格(非 Compose 狀態;變更後由呼叫端 version++ 觸發重繪)。 */
private class RasterState(
    var rx0: Float, var ry0: Float, var mw: Int, var mh: Int,
    val mScale: Float, val bw: Int, val bh: Int
) {
    var mask = ByteArray(mw * mh)
    var tissue = ByteArray(mw * mh)
    var orig = ByteArray(mw * mh)
    /**
     * 分類器對每個像素的建議（修邊畫面碼；0＝尚未算過）。
     *
     * 這不是遮罩，是「AI 覺得這裡是什麼」的底稿。用途有二：
     *  1. 進入修邊時把 tissue 初始化成逐像素分區，而不是整片同一色
     *  2. 用「邊界＋」把新區域畫進遮罩時，新像素自動帶上分類，而不是繼承某個預設類別
     *
     * ⚠ 舊版把整片遮罩填成 `defaultClass`（比例最高的那一類），於是醫師按下「完成修邊」
     * 送出的組織 GT 是「整個傷口都是肉芽」——把後端算對的 54/38/6 覆蓋成 100/0/0。
     * 畫面看起來正常（一片合理的顏色），資料卻是錯的。
     */
    var auto = ByteArray(mw * mh)
    var overlay: Bitmap = Bitmap.createBitmap(mw, mh, Bitmap.Config.ARGB_8888)
    var maskCount = 0
    /**
     * 組織填色是否顯示。**邊界不受此影響。**
     *
     * ⚠ 舊版把開關做在「整張 overlay 要不要畫」，而 overlay 同時承載邊界色與組織色——
     * 於是關掉圖層時**連遮罩邊界一起消失**，醫師用「邊界＋／－」等於在畫看不見的東西。
     * 他關圖層的用意是看清底下的組織紋理，不是放棄邊界回饋。
     */
    var showTissue = true
    val tCounts = IntArray(T_MAX + 1)   // 索引 0 不用；1..T_MAX 對應組織碼
    var cm2PerPx: Double? = null

    fun colorAt(x: Int, y: Int): Int {
        val i = y * mw + x
        if (mask[i].toInt() == 0) return 0
        val edge = x == 0 || y == 0 || x == mw - 1 || y == mh - 1 ||
                mask[i - 1].toInt() == 0 || mask[i + 1].toInt() == 0 ||
                mask[i - mw].toInt() == 0 || mask[i + mw].toInt() == 0
        if (edge) return EDGE_COLOR
        // 隱藏組織時內部畫成透明，邊界仍在——這是「看得見自己在畫什麼」的最低要求。
        return if (showTissue) T_COLORS[tissue[i].toInt().coerceIn(1, T_MAX)] else 0
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
        for (i in mask.indices) if (mask[i].toInt() != 0) { c++; tCounts[tissue[i].toInt().coerceIn(1, T_MAX)]++ }
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
        mask = move(mask); tissue = move(tissue); orig = move(orig); auto = move(auto)
        rx0 -= gL / mScale; ry0 -= gT / mScale
        mw = nw; mh = nh
        // ⚠ 舊 overlay 必須明確回收再配新的。ARGB_8888 在 2200² 是 19.4 MB;
        // 只丟參照的話要等 GC 決定何時回收,而擴張常常是連續發生的(筆刷一路往外畫),
        // 新舊兩份同時在 heap 上就是 39 MB 的瞬時尖峰 —— 低階機正是在這裡當掉。
        overlay.recycle()
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
                seedAuto(this, bitmap)   // 續編時 auto 不隨 EditRaster 保存,重算即可(便宜且必然一致)
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
                // 逐像素分區作為修邊起點。auto 算不出來時（極小遮罩、影像異常）才退回
                // 單一 defaultClass——那是降級，不是預設行為。
                seedAuto(this, bitmap)
                var c = 0
                for (i in mask.indices) if (mask[i].toInt() != 0) {
                    c++
                    tissue[i] = if (auto[i].toInt() in 1..T_MAX) auto[i] else defaultClass.toByte()
                }
                maskCount = c
                recount()
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
    // 組織圖層開關。
    //
    // 醫師抱怨「遮罩顏色影響背景組織的分類判斷」，而任何配色都不可能同時滿足
    // 「看得清楚分區」與「看得清楚底下的紋理」——那是同一塊像素的兩種用途。
    // 與其在透明度上折衷到兩邊都不好，不如讓他一鍵切掉去看原圖。
    var showTissue by remember { mutableStateOf(true) }
    /**
     * 「按住看原圖」的按壓狀態。宣告在這裡而不是按鈕旁邊，因為畫布的手勢迴圈
     * （在版面上方）也要讀它——peek 期間**第二根手指不得作畫**。
     * 手指壓在按鈕上已經擋掉大部分誤觸，但擋不掉另一手；而「看不見卻塗得下去」
     * 的那一筆會直接進 GT，沒有錯誤也沒有警告。
     */
    val peekSrc = remember { MutableInteractionSource() }
    val peeking by peekSrc.collectIsPressedAsState()
    // 柵格換新（換影像／續編載回）時把圖層旗標同步過去。
    // 宣告位置必須在 showTissue 之後——Kotlin 不允許向前引用區域變數。
    LaunchedEffect(st) { if (st.showTissue != showTissue) { st.showTissue = showTissue; st.syncAll(); version++ } }
    // peek 開關。只在狀態真的翻轉時 syncAll——每次重組都重畫整張覆蓋圖會明顯卡頓。
    LaunchedEffect(peeking) {
        showTissue = !peeking
        st.showTissue = showTissue
        st.syncAll(); version++
    }

    class Snap(val m: ByteArray, val t: ByteArray, val mw: Int, val mh: Int, val rx0: Float, val ry0: Float)
    val undo = remember(st) { mutableStateListOf<Snap>() }
    val redo = remember(st) { mutableStateListOf<Snap>() }

    /**
     * undo 深度改由**位元組預算**決定，不再固定 8 筆。
     *
     * 每筆快照是 `mask + tissue` 兩份 `mw*mh` 陣列。小傷口時遮罩可能只有 400²＝0.32 MB／筆，
     * 8 筆才 2.6 MB；但遮罩會隨筆刷擴張，到上限 2200² 時單筆就是 **9.7 MB**，8 筆＝77 MB，
     * 再加上活動中的 mask/tissue/orig（14.5 MB）與 ARGB overlay（19.4 MB）、
     * 從 DB 載回的全尺寸點陣圖（約 12 MB），峰值超過 120 MB。
     * minSdk 24 的裝置點陣圖在 Java heap 上，低階機的 heap 上限就在這個量級——
     * **而大面積傷口正是最需要修邊的情境**，會當在最不該當的時候。
     *
     * 這裡用固定的記憶體預算換取「小傷口 undo 很深、大傷口 undo 較淺但絕不 OOM」。
     * 上限仍保留 8（小遮罩時不會無限成長），下限保 2 —— 只剩 1 步可復原太難用，
     * 而 2 筆在 2200² 是 19.4 MB，仍在安全範圍。
     */
    val undoBudgetBytes = 24 * 1024 * 1024
    fun maxUndoDepth(): Int {
        val perSnap = st.mw.toLong() * st.mh * 2      // mask + tissue
        if (perSnap <= 0) return 8
        return ((undoBudgetBytes / perSnap).toInt()).coerceIn(2, 8)
    }
    /** 加入一筆快照並依目前預算修剪最舊的。redo 堆疊同樣要受限——它一樣是整份遮罩複本。 */
    fun push(stack: MutableList<Snap>, s: Snap) {
        stack.add(s)
        val cap = maxUndoDepth()
        while (stack.size > cap) stack.removeAt(0)
    }
    /** 新的一筆編輯：進 undo，並讓 redo 失效（分岔後的未來已不成立）。 */
    fun pushUndo(s: Snap) { push(undo, s); redo.clear() }
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
                seedAuto(st, bitmap)                       // 新擴出來的區域還沒有底稿
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
                    // 新畫進遮罩的像素帶上分類器的建議，而不是繼承某個預設類別——
                    // 否則醫師每往外補一筆，GT 裡就多一塊「其實沒人判斷過」的組織。
                    val a = st.auto[i].toInt()
                    val nc = if (a in 1..T_MAX) a else defaultClass
                    st.tissue[i] = nc.toByte(); st.tCounts[nc]++
                }
                EditTool.B_ERASE -> if (st.mask[i].toInt() != 0) {
                    st.mask[i] = 0; st.maskCount--
                    val tc = st.tissue[i].toInt(); if (tc in 1..T_MAX) st.tCounts[tc]--
                    st.tissue[i] = 0
                }
                EditTool.TISSUE -> if (st.mask[i].toInt() != 0 && st.tissue[i].toInt() != curTissue) {
                    val tc = st.tissue[i].toInt(); if (tc in 1..T_MAX) st.tCounts[tc]--
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
            // ⚠ 這裡原本寫死 0.0。分類器本來就會產生「其他」（TissueClassifierV2 的 code 5，
            // 也就是不符合前四類的落點），寫死 0 等於在送出 GT 的最後一步把它抹掉——
            // 於是訓練資料永遠學不到「這塊我也不知道是什麼」，而那正是最該讓醫師覆核的部分。
            "other" to st.tCounts[5].toDouble() / tot
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
             "壞死${(lf["necrosis"]!! * 100).toInt()}% · 上皮${(lf["epithelial"]!! * 100).toInt()}% · " +
             "其他${(lf["other"]!! * 100).toInt()}%  (框會隨筆刷自動擴張)",
            fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        // 「其他」偏高時要說出來。它代表的是**分類器不知道那是什麼**（肌腱、異物、血水、
        // 遮蔽物、或只是反光），而那正是最需要醫師看一眼的部分——不是可以忽略的殘差。
        // 這些像素若被硬歸到四類之一，訓練資料就會學到錯的東西；標成「其他」則是誠實的標籤。
        if ((lf["other"] ?: 0.0) > 0.10) Text(
            "ℹ 有 ${((lf["other"]!!) * 100).toInt()}% 判不出類別（肌腱／異物／血水／遮蔽物／反光）。" +
            "請用「組織🖌 → 其他」確認範圍，或改標成正確的組織——這一塊會照原樣進訓練集。",
            fontSize = 12.sp, color = MaterialTheme.colorScheme.tertiary)
        // 這條提示不是催促，是說明後果：不動筆刷不是錯，只是這一筆不會成為組織訓練樣本。
        // 醫師有權說「AI 分得對，我沒意見」——但那時的遮罩是啟發式輸出，不是人的判斷，
        // 拿去訓練會變成模型自我確認。所以要讓他知道這個選擇的意義。
        run {
            var ed = 0
            for (i in st.mask.indices) if (st.mask[i].toInt() != 0 && st.tissue[i] != st.auto[i]) ed++
            if (st.maskCount > 0 && ed == 0) Text(
                "ℹ 尚未修正任何組織分區。面積與邊界照常送出；但組織遮罩會標記為「未經醫師修正」，" +
                "**不會進入組織分割訓練集**——未修正的遮罩是 AI 自己的輸出，拿去訓練等於自我確認。",
                fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        if (st.maskCount == 0)
            Text("⚠ AI 未偵測到傷口:請用「邊界＋」從零塗抹;ArUco 尺度仍有效,面積照常精確計算",
                fontSize = 12.sp, color = MaterialTheme.colorScheme.error)

        Box(Modifier.fillMaxWidth().weight(1f).clipToBounds().onSizeChanged { boxSize = it }) {
            Canvas(
                Modifier.fillMaxSize().pointerInput(Unit) {
                    // ⚠ 不能用 detectDragGestures + detectTransformGestures 兩個 pointerInput 疊起來：
                    // 先註冊的那個會把事件吃掉，於是雙指縮放永遠不會觸發（或反過來，畫不了圖）。
                    // 手勢必須在**同一個迴圈**裡依觸點數分流。
                    awaitEachGesture {
                        val down = awaitFirstDown(requireUnconsumed = false)
                        var strokeSnapshot: Snap? = null
                        var last: Offset? = null
                        var multi = false          // 一旦進入雙指模式，這一輪就不再回頭去畫
                        cursor = down.position

                        // peek（按住看原圖）期間一律不作畫，只允許平移縮放。
                        // 使用者的手指在按鈕上，但**另一手仍可能碰到畫布**——
                        // 而此刻組織圖層是關的，他看不見自己塗了什麼。
                        // 那一筆會直接進 GT，沒有錯誤、沒有警告。
                        val canPaint = tool != EditTool.PAN && !peeking

                        if (canPaint) {
                            strokeSnapshot = snap()
                            val p0 = down.position / k() + viewOffset
                            stamp(p0); last = p0
                        }

                        do {
                            val ev = awaitPointerEvent()
                            val pressed = ev.changes.count { it.pressed }

                            if (pressed >= 2) {
                                // ── 進入雙指：縮放 + 平移 ──
                                if (!multi) {
                                    multi = true
                                    // 第一指落下時已經畫了一筆。使用者的意圖是縮放，不是畫圖——
                                    // 用既有的快照把那一筆還原，否則每次縮放都會在傷口上留一個點，
                                    // 而那個點會直接進入 GT。
                                    strokeSnapshot?.let { if (it.mw == st.mw && it.mh == st.mh) restore(it) }
                                    strokeSnapshot = null; last = null; cursor = null
                                    version++
                                }
                                val zoom = ev.calculateZoom()
                                val pan = ev.calculatePan()
                                val centroid = ev.calculateCentroid(useCurrent = true)
                                if (centroid != Offset.Unspecified) {
                                    // 以雙指中心為錨點縮放：螢幕點 p 對應影像點 p/k + off，
                                    // 要讓錨點下的影像位置不動 → off' = off + c/k - c/k'
                                    val kOld = k()
                                    val ci = centroid / kOld + viewOffset
                                    if (zoom != 1f) viewScale = (viewScale * zoom).coerceIn(0.5f, 24f)
                                    val kNew = k()
                                    viewOffset = ci - centroid / kNew - pan / kNew
                                }
                                ev.changes.forEach { it.consume() }
                            } else if (!multi && pressed == 1) {
                                // ── 單指：維持原本行為（依工具決定塗抹或平移）──
                                val ch = ev.changes.firstOrNull { it.pressed } ?: continue
                                val delta = ch.position - ch.previousPosition
                                cursor = ch.position
                                if (!canPaint) viewOffset -= delta / k()   // 含 peek：只平移，不作畫
                                else { val cur = ch.position / k() + viewOffset; last?.let { stampLine(it, cur) }; last = cur }
                                ch.consume()
                            } else {
                                ev.changes.forEach { it.consume() }
                            }
                        } while (ev.changes.any { it.pressed })

                        strokeSnapshot?.let {
                            // 擴張過的舊快照尺寸不同，還原會錯位 → 丟棄
                            if (it.mw == st.mw && it.mh == st.mh) pushUndo(it)
                        }
                        cursor = null      // last 是迴圈區域變數，這一輪結束即消失，不必再清
                    }
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
            FilterChip(tool == EditTool.TISSUE, { tool = EditTool.TISSUE },
                { Text("組織🖌") }, modifier = Modifier.weight(1f))
            // ── 圖層：**按住才隱藏，放開就回來** ──
            //
            // 舊版是切換式，有三個實測回報的問題：
            //  1. 關掉圖層時強制切到「移動」→ 下面那排組織分類按鈕整列消失
            //     → Column 重新排版 → 畫布拿到更多高度 → **影像跳一下變大**。
            //     再切回組織又跳回去。醫師以為是遮罩在動，其實是版面在動。
            //  2. 關著的時候筆刷仍然可用（切到移動只是降低機率，不是杜絕）——
            //     看不見自己塗了什麼卻照樣能塗，那一筆會直接進 GT。
            //  3. 要看一眼原圖得按兩次，而且中間狀態是可以誤操作的。
            //
            // 改成 peek 之後三個問題同時消失：工具完全不變（不重排版、不跳動），
            // 手指壓在這顆鈕上的期間本來就碰不到畫布，放開立刻回到組織圖層。
            // （peekSrc / peeking 宣告在畫布手勢迴圈之前，見上方。）
            FilterChip(
                selected = !peeking,
                onClick = {},           // 行為在按壓狀態上，不在點擊上
                label = { Text(if (peeking) "原圖🚫" else "按住看原圖") },
                interactionSource = peekSrc,
                modifier = Modifier.weight(1f)
            )
        }
        // ⚠ 這一列的顯示條件**只看工具，不看 peek**。
        // 若寫成 `tool == TISSUE && !peeking`，peek 期間整列會消失並讓畫布長高，
        // 那正是要修掉的跳動。空間必須恆定。
        if (tool == EditTool.TISSUE) {
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                // 1..T_MAX：包含「其他」。沒有這個筆刷的話，醫師看得到判不出來的區塊，
                // 卻只能把它硬塞進四類之一——那等於強迫他為訓練集捏造一個標籤。
                (1..T_MAX).forEach { c ->
                    FilterChip(curTissue == c, { curTissue = c }, { Text(T_NAMES[c]) },
                        enabled = !peeking, modifier = Modifier.weight(1f))
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
                    if (restore(undo.removeAt(undo.lastIndex))) { push(redo, cur); version++ }
                }
            }, enabled = undo.isNotEmpty(), modifier = Modifier.weight(1f), contentPadding = PaddingValues(2.dp)) { Text("↺") }
            OutlinedButton({
                if (redo.isNotEmpty()) {
                    val cur = snap()
                    // 這裡不能用 pushUndo：它會清掉 redo，等於按一次「重做」就再也重做不了下一步
                    if (restore(redo.removeAt(redo.lastIndex))) { push(undo, cur); version++ }
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
                        // 醫師實際重畫了多少組織像素。auto 是分類器的建議，tissue 是最終結果——
                        // 兩者相同代表這一格他沒有表示意見。見 EditRaster.tissueEditedPx 的說明。
                        var edited = 0
                        for (i in st.mask.indices) if (st.mask[i].toInt() != 0 &&
                            st.tissue[i] != st.auto[i]) edited++
                        val raster = EditRaster(st.mask.copyOf(), st.tissue.copyOf(), st.orig.copyOf(),
                            st.rx0, st.ry0, st.mw, st.mh, st.mScale, st.cm2PerPx,
                            tissueEditedPx = edited, maskPx = st.maskCount,
                            canvasW = bw, canvasH = bh)
                        // ⚠ 沒動過邊界就**不要動那個數字**。
                        //
                        // 由多邊形重建柵格是有損的（RDP 簡化 + 重新掃描線填充 + ROI 外框改變），
                        // 每進出一次面積就漂移約 0.5%。醫師什麼都沒改卻看到面積變了，
                        // 那個數字就失去意義——而一個會自己緩慢變動的臨床數值
                        // 比明顯的錯誤更難察覺。
                        val areaOut = if (iou >= 0.9999 && originalArea != null) originalArea
                                      else liveArea
                        onDone(poly, iou, areaOut, liveFrac(), raster)
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
