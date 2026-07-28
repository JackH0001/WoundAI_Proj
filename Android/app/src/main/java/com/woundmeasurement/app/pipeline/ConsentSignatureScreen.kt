package com.woundmeasurement.app.pipeline

import android.graphics.Bitmap
import android.graphics.Canvas as AndroidCanvas
import android.graphics.Paint
import android.graphics.Path as AndroidPath
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.unit.dp
import java.io.ByteArrayOutputStream

/**
 * 知情同意（雙層）＋ 手寫簽名。
 *
 * 修正的法規缺陷：先前 `BackendClient` 把 `consent_train` **硬編碼成 true**，
 * 每一筆送出的標註都宣稱「已取得訓練同意」，但從來沒有人真的勾選過。
 * `IRB_consent_templates.md:37` 要求「勾選＋電子簽名＋時間戳須系統留存供稽核」。
 *
 * 雙層語意（同上 :23-26）：
 *  ① 照護同意 = 必填。沒有它連量測都不該做。
 *  ② 訓練同意 = 選填、可撤回。**拒絕訓練不影響照護**，這句話必須讓病患看見。
 */
@Composable
fun ConsentSignatureScreen(
    patientLabel: String,
    onCancel: () -> Unit,
    onSigned: (care: Boolean, train: Boolean, signaturePng: ByteArray?) -> Unit
) {
    var care by remember { mutableStateOf(false) }
    var train by remember { mutableStateOf(false) }
    val strokes = remember { mutableStateListOf<List<Offset>>() }
    var current by remember { mutableStateOf<List<Offset>>(emptyList()) }
    var canvasW by remember { mutableStateOf(0) }
    var canvasH by remember { mutableStateOf(0) }
    val hasSignature = strokes.isNotEmpty() || current.isNotEmpty()

    Column(
        Modifier.fillMaxSize().padding(16.dp).verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(10.dp)
    ) {
        Text("知情同意書", style = MaterialTheme.typography.titleLarge)
        Text("受試者：$patientLabel", style = MaterialTheme.typography.bodyMedium)

        Divider()
        Row(verticalAlignment = androidx.compose.ui.Alignment.Top) {
            Checkbox(checked = care, onCheckedChange = { care = it })
            Column(Modifier.padding(top = 12.dp)) {
                Text("① 照護同意（必填）", style = MaterialTheme.typography.titleSmall)
                Text("同意以手機拍攝傷口影像，作為本人傷口照護之量測與紀錄用途。",
                    style = MaterialTheme.typography.bodySmall)
            }
        }
        Row(verticalAlignment = androidx.compose.ui.Alignment.Top) {
            Checkbox(checked = train, onCheckedChange = { train = it })
            Column(Modifier.padding(top = 12.dp)) {
                Text("② 研究/訓練同意（選填，可隨時撤回）", style = MaterialTheme.typography.titleSmall)
                Text("同意將去識別化後的影像與醫師標註，用於本系統之演算法改良。" +
                     "影像不含姓名、病歷號等可識別資訊。**不同意亦不影響照護權益**，" +
                     "日後可隨時撤回，撤回後不再納入後續訓練。",
                    style = MaterialTheme.typography.bodySmall)
            }
        }

        Divider()
        Text("受試者簽名", style = MaterialTheme.typography.titleSmall)
        Text("請於下方框內簽名", style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
        Canvas(
            Modifier
                .fillMaxWidth()
                .height(180.dp)
                .background(Color.White)
                // 尺寸用 onSizeChanged 取,**不要在 draw lambda 裡寫 state**——
                // 那會在每次繪製時觸發重組,形成無限迴圈。
                .onSizeChanged { canvasW = it.width; canvasH = it.height }
                .pointerInput(Unit) {
                    detectDragGestures(
                        onDragStart = { current = listOf(it) },
                        // consume():畫布在 verticalScroll 內,不消費事件的話垂直筆畫會變成捲頁
                        onDrag = { change, _ -> change.consume(); current = current + change.position },
                        onDragEnd = { if (current.size > 1) strokes.add(current); current = emptyList() }
                    )
                }
        ) {
            drawRect(Color(0xFFF2F2F2))
            (strokes + listOf(current)).forEach { pts ->
                if (pts.size > 1) {
                    val p = Path().apply {
                        moveTo(pts[0].x, pts[0].y)
                        pts.drop(1).forEach { lineTo(it.x, it.y) }
                    }
                    drawPath(p, Color.Black, style = Stroke(width = 4f, join = StrokeJoin.Round))
                }
            }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton({ strokes.clear(); current = emptyList() }, Modifier.weight(1f)) { Text("清除") }
            OutlinedButton(onCancel, Modifier.weight(1f)) { Text("取消") }
        }

        if (!care) Text("⚠ 未勾選照護同意即無法進行量測", color = MaterialTheme.colorScheme.error,
            style = MaterialTheme.typography.bodySmall)
        if (!hasSignature) Text("⚠ 尚未簽名", color = MaterialTheme.colorScheme.error,
            style = MaterialTheme.typography.bodySmall)

        Button(
            onClick = {
                onSigned(care, train, renderSignature(strokes, canvasW, canvasH))
            },
            // 照護同意與簽名皆為必要；訓練同意可為 false（那才是真正的「選填」）
            enabled = care && hasSignature,
            modifier = Modifier.fillMaxWidth()
        ) { Text("確認簽署") }

        Text("簽署後將記錄簽名影像與時間戳於本機病歷；PII 不上傳雲端。",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

/** 把筆畫轉成 PNG bytes（存本機同意紀錄；不隨標註上傳）。 */
private fun renderSignature(strokes: List<List<Offset>>, w: Int, h: Int): ByteArray? {
    if (strokes.isEmpty() || w <= 0 || h <= 0) return null
    val bmp = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888)
    val c = AndroidCanvas(bmp)
    c.drawColor(android.graphics.Color.WHITE)
    val paint = Paint().apply {
        color = android.graphics.Color.BLACK
        strokeWidth = 4f
        style = Paint.Style.STROKE
        strokeJoin = Paint.Join.ROUND
        strokeCap = Paint.Cap.ROUND
        isAntiAlias = true
    }
    strokes.forEach { pts ->
        if (pts.size > 1) {
            val p = AndroidPath().apply {
                moveTo(pts[0].x, pts[0].y)
                pts.drop(1).forEach { lineTo(it.x, it.y) }
            }
            c.drawPath(p, paint)
        }
    }
    return ByteArrayOutputStream().use { out ->
        bmp.compress(Bitmap.CompressFormat.PNG, 100, out); out.toByteArray()
    }
}
