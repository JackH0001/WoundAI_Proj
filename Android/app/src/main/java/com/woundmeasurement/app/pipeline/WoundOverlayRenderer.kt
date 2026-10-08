package com.woundmeasurement.app.pipeline

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RectF
import android.graphics.Typeface
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

/**
 * 把一次完成的量測畫成一張**可離線審閱的疊圖**：原圖 ＋ 醫師確認的組織分區
 * ＋ 傷口輪廓 ＋ ArUco 校正框 ＋ 底部的量測結果標註帶。
 *
 * ## 真值來源是 [EditRaster]，不是色彩啟發式
 *
 * [AnalysisPreview] 的 `buildTissueOverlay` 會**當場重跑** [TissueSeg] 的色彩啟發式。
 * 那張圖適合在結果頁即時預覽，但**不能拿來當匯出的證據**：醫師在修邊畫面改過的分區
 * 不在裡面，於是匯出的圖會跟存進病歷的數字對不上。
 *
 * 本檔一律從 [EditRaster]（醫師按下「完成修邊」當下的 mask + tissue）重建，
 * 所以圖上看到的分區，就是算出那些百分比的那一份。沒有 raster 就不畫組織層，
 * 也不假裝有——見 [render] 的 `raster == null` 分支。
 *
 * ## 為什麼在 raster 的畫布座標系渲染，而不是原圖解析度
 *
 * [EditRaster] 的座標是相對於**產生它的那張畫布**算的（[EditRaster.canvasW]/[EditRaster.canvasH]）。
 * 端上模式那是原圖，後端模式是 ≤2048 的 work 縮圖，而 `lastBitmap` 可能是 4.9 MP 的
 * 相機原圖。兩者直接疊就是錯位，而錯位的疊圖**看起來只是「標得有點歪」**，
 * 不會有任何錯誤訊息——又是那個最危險的失敗形狀。
 *
 * 所以先把底圖縮到 raster 的畫布尺寸，之後所有座標 1:1。輪廓多邊形本來就在
 * 同一個空間（後端回的 image_w/image_h），一併對上。
 *
 * ## 為什麼標註帶是「加高畫布」而不是「找空位擺」
 *
 * 需求是「標註框在 ROI 外」。用擺放啟發式（試下方、上方、左右）在傷口很大或
 * 貼紙貼在角落時會找不到位置，然後只能疊上去——而那正是它要避免的事。
 * 在影像下方外加一條帶子，「不覆蓋任何一個影像像素」就變成**構造上成立**，
 * 不需要驗證，也不會因為某張照片的構圖而失效。
 */
object WoundOverlayRenderer {

    /** 標註帶要印的欄位。全部由呼叫端從**已存進紀錄的值**帶入，這裡不重算任何數字。 */
    data class Info(
        val wdCode: String?,
        val source: String?,
        val areaCm2: Double?,
        val pushPartial: Int?,
        val pushFull: Int?,
        val exudate: Int?,
        val tissueFrac: Map<String, Double>,
        val mmPerPx: Double?,
        val calibMethod: String?,
        val route: String?,
        val confidence: Double?,
        val doctorVerified: Boolean,
        val tissueEdited: Boolean,
        val takenAt: Date = Date(),
    )

    /** 組織百分比的顯示順序。與修邊畫面的組織碼 1..5 對齊。 */
    private val TISSUE_KEYS = arrayOf("granulation", "slough", "necrosis", "epithelial", "other")

    /**
     * @param base      原始擷取影像（任何解析度；會被縮到 raster 的畫布尺寸）
     * @param raster    醫師修邊後的柵格。null＝沒進過修邊 → 不畫組織層
     * @param polygons  傷口輪廓（raster 畫布座標）
     * @param markerQuad ArUco 四角（raster 畫布座標）。null＝未偵測到
     */
    fun render(
        base: Bitmap,
        raster: EditRaster?,
        polygons: List<List<List<Int>>>,
        markerQuad: List<List<Int>>?,
        info: Info,
    ): Bitmap? = runCatching {
        // ── 畫布尺寸：以 raster 為準；沒有 raster 時退回原圖（但長邊設上限，避免 OOM）
        val cw: Int; val ch: Int
        if (raster != null && raster.canvasW > 0 && raster.canvasH > 0) {
            cw = raster.canvasW; ch = raster.canvasH
        } else {
            val k = min(1.0, 2048.0 / max(base.width, base.height))
            cw = (base.width * k).roundToInt().coerceAtLeast(1)
            ch = (base.height * k).roundToInt().coerceAtLeast(1)
        }

        // 標註帶高度由內容決定（見 layoutBand）：要先知道有幾列、折了幾行，
        // 才知道 bitmap 要開多高。
        val layout = layoutBand(cw, info, raster)
        val band = layout.height
        val out = Bitmap.createBitmap(cw, ch + band, Bitmap.Config.ARGB_8888)
        val c = Canvas(out)
        c.drawColor(Color.BLACK)

        // ── 1. 底圖
        c.drawBitmap(base, null, RectF(0f, 0f, cw.toFloat(), ch.toFloat()),
            Paint(Paint.FILTER_BITMAP_FLAG))

        // ── 2. 組織層（只畫遮罩內、且分類碼合法的像素）
        if (raster != null && raster.mw > 0 && raster.mh > 0) {
            val px = IntArray(raster.mw * raster.mh)
            for (i in px.indices) {
                val inMask = raster.mask.getOrNull(i)?.toInt() ?: 0
                val t = raster.tissue.getOrNull(i)?.toInt() ?: 0
                px[i] = if (inMask != 0 && t in 1..T_MAX) T_COLORS[t] else 0
            }
            val tint = Bitmap.createBitmap(raster.mw, raster.mh, Bitmap.Config.ARGB_8888)
            tint.setPixels(px, 0, raster.mw, 0, 0, raster.mw, raster.mh)
            // raster → 影像座標：x = rx0 + rasterX / mScale（見 WoundEditScreen.seedAuto）
            val dst = RectF(
                raster.rx0, raster.ry0,
                raster.rx0 + raster.mw / raster.mScale,
                raster.ry0 + raster.mh / raster.mScale)
            c.drawBitmap(tint, null, dst, Paint(Paint.FILTER_BITMAP_FLAG))
            tint.recycle()
        }

        // ── 3. 輪廓（深色描邊打底再畫亮線；底圖深淺不一時單一顏色會看不見）
        val unit = max(cw, ch) / 400f          // 線寬基準：解析度無關
        val halo = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            style = Paint.Style.STROKE; color = Color.argb(0x99, 0, 0, 0)
            strokeWidth = unit * 2.4f
        }
        val edge = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            style = Paint.Style.STROKE; color = Color.rgb(0, 229, 255)   // 與修邊畫面同色
            strokeWidth = unit * 1.2f
        }
        polygons.filter { it.size >= 3 }.forEach { poly ->
            val p = Path()
            p.moveTo(poly[0][0].toFloat(), poly[0][1].toFloat())
            for (i in 1 until poly.size) p.lineTo(poly[i][0].toFloat(), poly[i][1].toFloat())
            p.close()
            c.drawPath(p, halo); c.drawPath(p, edge)
        }

        // ── 4. ArUco 校正框。**這是審閱者第一個要看的東西**：框歪了，每一筆面積都是錯的。
        markerQuad?.takeIf { it.size >= 4 }?.let { q ->
            val p = Path()
            p.moveTo(q[0][0].toFloat(), q[0][1].toFloat())
            for (i in 1 until q.size) p.lineTo(q[i][0].toFloat(), q[i][1].toFloat())
            p.close()
            c.drawPath(p, halo)
            c.drawPath(p, Paint(edge).apply { color = Color.rgb(57, 255, 106) })
            val dot = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.rgb(57, 255, 106) }
            q.forEach { c.drawCircle(it[0].toFloat(), it[1].toFloat(), unit * 2f, dot) }
        }

        drawBand(c, cw, ch, layout)
        out
    }.getOrNull()

    private const val BAND_MIN_PX = 240          // 標註帶高度下限（小圖也要讀得出字）

    /** 一個組織色塊＋它的百分比標籤。顏色在排版時就去掉 alpha（T_COLORS 是半透明疊色用的）。 */
    private class Chip(val color: Int, val label: String)

    /**
     * 標註帶的一列。[chips] 非空時這一列畫組織色塊，否則畫 [lines] 的文字。
     *
     * 字級由**影像寬度**推導，不由標註帶高度推導。反過來做會循環：帶子要多高取決於
     * 有幾列、每列多高取決於字級、字級又取決於帶子多高。實際上也更合理——帶子與影像
     * 同寬，可讀性取決於寬度，與帶子自己多厚無關。
     */
    private class Row(
        val lines: List<String>,
        val paint: Paint,
        val lead: Float,
        val chips: List<List<Chip>> = emptyList(),
    )

    /** 排版結果。[height] 是**由內容算出來的**，所以構造上不可能裁掉任何一列。 */
    private class Band(val height: Int, val pad: Float, val rows: List<Row>, val swatch: Float)

    /** 一行佔的高度（含行距）。排版與繪製共用同一個公式，否則兩邊會慢慢偏掉。 */
    private fun lineH(p: Paint): Float {
        val fm = p.fontMetrics
        return (-fm.ascent + fm.descent) * 1.22f
    }

    /** 文字基線相對於這一行頂端的位移。 */
    private fun baseOf(p: Paint): Float = -p.fontMetrics.ascent

    /**
     * 把一行文字折成數行，每行都 ≤ [maxW]。
     *
     * 用 [Paint.breakText] 而不是按空白切：中文沒有空白，按空白切等於不折。
     * 切點盡量落在分隔符之後，否則 "0.1132" 這種數字會被硬切成兩截。
     */
    private fun wrap(text: String, p: Paint, maxW: Float): List<String> {
        if (text.isEmpty()) return listOf("")
        if (p.measureText(text) <= maxW) return listOf(text)
        val out = ArrayList<String>()
        var rest = text
        while (rest.isNotEmpty()) {
            var n = p.breakText(rest, true, maxW, null)
            if (n <= 0) n = 1                   // 連一個字都放不下也要前進，否則無窮迴圈
            if (n < rest.length) {
                val cut = max(rest.lastIndexOf(' ', n - 1), rest.lastIndexOf('·', n - 1))
                if (cut > n / 2) n = cut + 1     // 只在切點不會讓這一行幾乎空掉時才退讓
            }
            out.add(rest.substring(0, n).trim())
            rest = rest.substring(n)
        }
        return out
    }

    /**
     * 先排版、再決定畫布要加高多少。
     *
     * 2026-10-06 回報「標註部分好像沒有很清楚」：帶子高度原本是 `ch * 0.21`（與內容無關），
     * 且完全沒有量文字寬度，於是「尺度／滲液／路由」那一列被右緣裁掉、免責那一列被下緣裁掉。
     * 固定高度加上不量寬度，「被裁掉」就只是時間問題——換一張長寬比不同的照片、或一個長一點
     * 的 calibMethod 字串就會再發生，而**被裁掉的是哪一段完全不可預期**。對一張要離開 App
     * 當證據用的圖，少了哪一行比整張難看嚴重得多。
     *
     * 所以反過來做：先把每一列按實際量到的寬度折行、加總算出需要的高度，才去開 bitmap。
     */
    private fun layoutBand(cw: Int, info: Info, raster: EditRaster?): Band {
        val pad = max(10f, cw * 0.030f)
        val maxW = cw - pad * 2
        fun paint(size: Float, c: Int, bold: Boolean = false) = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            textSize = size; color = c
            if (bold) typeface = Typeface.create(Typeface.DEFAULT, Typeface.BOLD)
        }
        val h1 = max(18f, cw / 34f)
        val h2 = max(14f, cw / 46f)
        val h3 = max(12f, cw / 54f)
        val white = paint(h1, Color.WHITE, bold = true)
        val grey = paint(h2, Color.rgb(205, 205, 210))
        val dim = paint(h3, Color.rgb(150, 150, 155))
        // 驗證狀態上色。夾在三行灰字中間的第四行灰字不會有人多看一眼，而這一列
        // 決定這張圖能不能當 GT 用。
        val flag = paint(h2,
            if (info.doctorVerified) Color.rgb(90, 230, 130) else Color.rgb(255, 193, 71), bold = true)

        val rows = ArrayList<Row>()
        fun text(s: String, p: Paint, lead: Float = 0f) { rows.add(Row(wrap(s, p, maxW), p, lead)) }

        // ── 1. 面積與 PUSH。措辭與結果頁的卡片一致（MeasureScreen:57）：PUSH 印的是
        //    **部分分數**，括號裡才是含滲液的總分。先前只印 pushFull，於是畫面寫 14、
        //    匯出的圖寫 16，看圖的人無從知道那是同一筆量測。
        val area = info.areaCm2?.let { "%.2f cm²".format(it) } ?: "無（未校正）"
        val push = (info.pushPartial?.toString() ?: "—") +
            (info.pushFull?.let { "（含滲液 $it）" } ?: "（滲液未輸入）")
        text("面積 $area    PUSH $push", white)

        // ── 2. 組織百分比。色塊用修邊畫面同一份調色盤（T_COLORS）。
        //    百分比一律 toInt() 截尾：紀錄卡片、時間軸 notes、修邊畫面都是截尾，
        //    這裡改四捨五入會讓匯出的圖比病歷多一個百分點。
        val sw = h2 * 0.92f
        val chipLines = ArrayList<List<Chip>>()
        var line = ArrayList<Chip>(); var used = 0f
        for ((idx, key) in TISSUE_KEYS.withIndex()) {
            val v = info.tissueFrac[key] ?: continue
            if (v <= 0.0005) continue
            val code = idx + 1
            val label = "${T_NAMES[code]} ${(v * 100).toInt()}%"
            val w = sw * 1.3f + grey.measureText(label) + sw * 1.2f
            if (line.isNotEmpty() && used + w > maxW) { chipLines.add(line); line = ArrayList(); used = 0f }
            line.add(Chip(Color.rgb(
                Color.red(T_COLORS[code]), Color.green(T_COLORS[code]), Color.blue(T_COLORS[code])), label))
            used += w
        }
        if (line.isNotEmpty()) chipLines.add(line)
        if (chipLines.isNotEmpty()) rows.add(Row(emptyList(), grey, h2 * 0.45f, chipLines))

        // ── 3. 校正尺度**獨佔一列**。它決定每一筆面積對不對，而 calibMethod 可以很長
        //    （"aruco(marker 12.0mm)"）——跟別的欄位擠一列，就是這次被裁掉的那一列。
        val calib = info.mmPerPx?.let { "%.4f mm/px".format(it) } ?: "無校正（面積不可用）"
        text("尺度 $calib" + (info.calibMethod?.takeIf { it != "none" }?.let { "（$it）" } ?: ""),
            grey, h2 * 0.55f)

        // ── 4. 滲液／路由／信心
        text("滲液 ${info.exudate?.toString() ?: "未輸入"}    路由 ${info.route ?: "—"}" +
            (info.confidence?.let { "    信心 ${(it * 100).roundToInt()}%" } ?: ""), grey)

        // ── 5. 歸戶與時間
        val stamp = SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.TAIWAN).format(info.takenAt)
        text("${info.wdCode ?: "未歸戶"}    ${info.source ?: "?"}    $stamp", grey)

        // ── 6. 驗證狀態
        text((if (info.doctorVerified) "醫師已驗證" else "未經醫師驗證") + "    " + when {
            raster == null -> "未修邊（組織分區為演算法輸出）"
            info.tissueEdited -> "組織經醫師修改"
            else -> "組織未經醫師修改（為演算法底稿）"
        }, flag, h2 * 0.3f)

        // ── 7. 免責。與畫面上那句一致——匯出的圖會離開 App，它必須自己帶著這句話。
        text("輔助、非診斷，需醫師確認；本圖之組織分區以醫師修邊為準", dim, h3 * 0.45f)

        var h = pad
        rows.forEach { r ->
            h += r.lead
            h += if (r.chips.isNotEmpty()) r.chips.size * (sw * 1.5f)
                 else r.lines.size * lineH(r.paint)
        }
        return Band(max(BAND_MIN_PX, (h + pad).roundToInt()), pad, rows, sw)
    }

    /** 底部標註帶。不覆蓋影像——它畫在加高出來的區域裡。 */
    private fun drawBand(c: Canvas, cw: Int, ch: Int, b: Band) {
        val top = ch.toFloat()
        c.drawRect(0f, top, cw.toFloat(), (ch + b.height).toFloat(),
            Paint().apply { color = Color.rgb(18, 18, 20) })
        c.drawLine(0f, top, cw.toFloat(), top,
            Paint().apply { color = Color.rgb(0, 229, 255); strokeWidth = max(2f, cw / 500f) })

        var y = top + b.pad
        b.rows.forEach { r ->
            y += r.lead
            if (r.chips.isNotEmpty()) {
                r.chips.forEach { row ->
                    var x = b.pad
                    val base = y + b.swatch
                    row.forEach { chip ->
                        c.drawRect(x, base - b.swatch, x + b.swatch, base,
                            Paint().apply { color = chip.color })
                        c.drawText(chip.label, x + b.swatch * 1.3f, base, r.paint)
                        x += b.swatch * 1.3f + r.paint.measureText(chip.label) + b.swatch * 1.2f
                    }
                    y += b.swatch * 1.5f
                }
            } else {
                r.lines.forEach { l ->
                    c.drawText(l, b.pad, y + baseOf(r.paint), r.paint)
                    y += lineH(r.paint)
                }
            }
        }
    }
}
