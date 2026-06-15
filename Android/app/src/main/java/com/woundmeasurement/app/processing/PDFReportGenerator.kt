package com.woundmeasurement.app.processing

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Paint
import android.graphics.pdf.PdfDocument
import android.util.Log
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.io.FileOutputStream
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * 傷口量測 PDF 報告產生器（Android 平台）
 * 使用 Android PdfDocument API 直接繪製 PDF 頁面。
 * 對應 iOS PDFReportGenerator。
 */
class PDFReportGenerator(private val context: Context) {

    companion object {
        private const val TAG        = "PDFReportGenerator"
        private const val PAGE_WIDTH  = 595   // A4 @72dpi
        private const val PAGE_HEIGHT = 842
        private const val MARGIN      = 40f
        private const val LINE_HEIGHT = 20f
    }

    // ── 設定 ──────────────────────────────────────────────────────────────────

    data class ReportConfig(
        val includeImages:    Boolean = true,
        val includeHistory:   Boolean = false,
        val hospitalName:     String  = "",
        val doctorName:       String  = "",
    )

    // ── 報告資料 ──────────────────────────────────────────────────────────────

    data class WoundReportData(
        val patientId:      String,
        val patientName:    String        = "",
        val measurementDate: Date         = Date(),
        val woundType:      String?       = null,
        val severityScore:  Float?        = null,
        val woundAreaCm2:   Double?       = null,
        val perimeterCm:    Double?       = null,
        val confidence:     Float         = 0f,
        val tissueGranulation: Float      = 0f,
        val tissueSlough:   Float         = 0f,
        val tissueNecrotic: Float         = 0f,
        val qualityScore:   Float?        = null,
        val imagePath:      String?       = null,
        val clinicianNote:  String?       = null,
        val modelVersion:   String        = "1.0.0",
    )

    // ── 主要方法 ──────────────────────────────────────────────────────────────

    /**
     * 產生 PDF 報告，寫入指定目錄，回傳檔案路徑。
     */
    suspend fun generateReport(
        data:      WoundReportData,
        outputDir: File,
        config:    ReportConfig = ReportConfig(),
    ): String = withContext(Dispatchers.IO) {
        outputDir.mkdirs()

        val dateStr  = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.getDefault()).format(data.measurementDate)
        val fileName = "WoundReport_${data.patientId}_$dateStr.pdf"
        val outFile  = File(outputDir, fileName)

        val document = PdfDocument()
        try {
            val pageInfo = PdfDocument.PageInfo.Builder(PAGE_WIDTH, PAGE_HEIGHT, 1).create()
            val page     = document.startPage(pageInfo)
            val canvas   = page.canvas

            val y = drawReport(canvas, data, config)
            _ = y  // suppress unused warning

            document.finishPage(page)
            FileOutputStream(outFile).use { document.writeTo(it) }
            Log.i(TAG, "報告已輸出：${outFile.absolutePath}")
        } finally {
            document.close()
        }

        outFile.absolutePath
    }

    /**
     * 批次產生報告（每筆 BatchItemResult 對應一份）
     */
    suspend fun generateBatchReports(
        results:   List<BatchProcessingService.BatchItemResult>,
        patientId: String,
        outputDir: File,
        config:    ReportConfig = ReportConfig(),
    ): List<String> {
        val paths = mutableListOf<String>()
        for (item in results.filter { it.success }) {
            val reportData = WoundReportData(
                patientId       = patientId,
                woundType       = item.woundType,
                severityScore   = item.severityScore,
                confidence      = item.confidence,
                tissueGranulation = item.tissueGranulation,
                tissueSlough    = item.tissueSlough,
                tissueNecrotic  = item.tissueNecrotic,
                qualityScore    = item.qualityScore,
                imagePath       = item.filePath,
            )
            paths.add(generateReport(reportData, outputDir, config))
        }
        return paths
    }

    // ── PDF 繪製 ──────────────────────────────────────────────────────────────

    private fun drawReport(
        canvas: android.graphics.Canvas,
        data:   WoundReportData,
        config: ReportConfig,
    ): Float {
        var y = MARGIN

        val titlePaint  = Paint().apply { textSize = 20f; isFakeBoldText = true; color = 0xFF1976D2.toInt() }
        val headPaint   = Paint().apply { textSize = 14f; isFakeBoldText = true; color = 0xFF1976D2.toInt() }
        val bodyPaint   = Paint().apply { textSize = 11f; color = 0xFF333333.toInt() }
        val labelPaint  = Paint().apply { textSize = 11f; isFakeBoldText = true; color = 0xFF555555.toInt() }
        val smallPaint  = Paint().apply { textSize = 9f;  color = 0xFF888888.toInt() }
        val linePaint   = Paint().apply { color = 0xFF1976D2.toInt(); strokeWidth = 1.5f }

        val df = SimpleDateFormat("yyyy 年 MM 月 dd 日 HH:mm", Locale.getDefault())

        // ── 標題 ─────────────────────────────────────────────────────────────
        if (config.hospitalName.isNotEmpty()) {
            canvas.drawText(config.hospitalName, MARGIN, y, headPaint); y += LINE_HEIGHT
        }
        canvas.drawText("傷口量測分析報告", MARGIN, y, titlePaint); y += LINE_HEIGHT + 4f
        canvas.drawLine(MARGIN, y, PAGE_WIDTH - MARGIN, y, linePaint); y += 8f

        // ── 患者資料 ─────────────────────────────────────────────────────────
        canvas.drawText("患者資料", MARGIN, y, headPaint); y += LINE_HEIGHT
        row(canvas, "患者編號", data.patientId,               y, labelPaint, bodyPaint); y += LINE_HEIGHT
        if (data.patientName.isNotEmpty()) {
            row(canvas, "患者姓名", data.patientName,         y, labelPaint, bodyPaint); y += LINE_HEIGHT
        }
        row(canvas, "量測時間", df.format(data.measurementDate), y, labelPaint, bodyPaint); y += LINE_HEIGHT + 6f

        // ── 量測結果 ─────────────────────────────────────────────────────────
        canvas.drawText("量測結果", MARGIN, y, headPaint); y += LINE_HEIGHT
        row(canvas, "傷口類型",  data.woundType ?: "未知",    y, labelPaint, bodyPaint); y += LINE_HEIGHT
        row(canvas, "嚴重度",    data.severityScore?.let { "%.0f / 4 級".format(it) } ?: "—",
            y, labelPaint, bodyPaint); y += LINE_HEIGHT
        row(canvas, "傷口面積",  data.woundAreaCm2?.let { "%.2f cm²".format(it) } ?: "—",
            y, labelPaint, bodyPaint); y += LINE_HEIGHT
        row(canvas, "傷口周長",  data.perimeterCm?.let { "%.2f cm".format(it) } ?: "—",
            y, labelPaint, bodyPaint); y += LINE_HEIGHT
        row(canvas, "AI 置信度","%.0f%%".format(data.confidence * 100),
            y, labelPaint, bodyPaint); y += LINE_HEIGHT + 6f

        // ── 組織成分 ─────────────────────────────────────────────────────────
        canvas.drawText("組織成分", MARGIN, y, headPaint); y += LINE_HEIGHT
        drawBar(canvas, "肉芽組織", data.tissueGranulation, 0xFF4CAF50.toInt(), y, bodyPaint); y += LINE_HEIGHT
        drawBar(canvas, "腐肉",     data.tissueSlough,      0xFFFF9800.toInt(), y, bodyPaint); y += LINE_HEIGHT
        drawBar(canvas, "壞死組織", data.tissueNecrotic,    0xFFF44336.toInt(), y, bodyPaint); y += LINE_HEIGHT + 6f

        // ── 影像（可選）─────────────────────────────────────────────────────
        if (config.includeImages && data.imagePath != null) {
            val bmp = runCatching { BitmapFactory.decodeFile(data.imagePath) }.getOrNull()
            if (bmp != null) {
                val maxW = (PAGE_WIDTH - MARGIN * 2).toInt()
                val scale = minOf(1f, maxW.toFloat() / bmp.width)
                val dstW = (bmp.width  * scale).toInt()
                val dstH = (bmp.height * scale).toInt()
                canvas.drawBitmap(
                    Bitmap.createScaledBitmap(bmp, dstW, dstH, true),
                    MARGIN, y, null)
                y += dstH + 8f
            }
        }

        // ── 臨床備注 ─────────────────────────────────────────────────────────
        if (!data.clinicianNote.isNullOrBlank()) {
            canvas.drawText("臨床備注", MARGIN, y, headPaint); y += LINE_HEIGHT
            canvas.drawText(data.clinicianNote, MARGIN, y, bodyPaint); y += LINE_HEIGHT + 6f
        }

        // ── 頁尾 ─────────────────────────────────────────────────────────────
        val footerY = PAGE_HEIGHT - MARGIN - LINE_HEIGHT
        canvas.drawLine(MARGIN, footerY - 4f, PAGE_WIDTH - MARGIN, footerY - 4f, linePaint)
        canvas.drawText("本報告由 WoundAI 智慧傷口量測系統自動產生，僅供臨床參考。",
            MARGIN, footerY, smallPaint)
        canvas.drawText("模型版本：${data.modelVersion}",
            PAGE_WIDTH - MARGIN - 120f, footerY, smallPaint)

        return y
    }

    private fun row(
        canvas: android.graphics.Canvas,
        label: String, value: String,
        y: Float, lp: Paint, vp: Paint,
    ) {
        canvas.drawText("$label：", MARGIN, y, lp)
        canvas.drawText(value, MARGIN + 100f, y, vp)
    }

    private fun drawBar(
        canvas: android.graphics.Canvas,
        label: String, ratio: Float,
        color: Int, y: Float, paint: Paint,
    ) {
        canvas.drawText("$label：${"%.1f".format(ratio * 100)}%", MARGIN, y, paint)
        val barX  = MARGIN + 110f
        val barW  = PAGE_WIDTH - barX - MARGIN
        val barH  = 10f
        val barY  = y - barH

        val bgPaint  = Paint().apply { this.color = 0xFFEEEEEE.toInt() }
        val fillPaint= Paint().apply { this.color = color }
        canvas.drawRect(barX, barY, barX + barW, barY + barH, bgPaint)
        canvas.drawRect(barX, barY, barX + barW * ratio.coerceIn(0f, 1f), barY + barH, fillPaint)
    }
}

private operator fun Unit.not(): Unit = Unit  // suppress unused assignment warning
