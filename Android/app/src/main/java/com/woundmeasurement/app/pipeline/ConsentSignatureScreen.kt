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
import androidx.compose.ui.text.font.FontWeight
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
                ConsentDetail("詳細說明", CARE_DETAIL)
            }
        }
        Row(verticalAlignment = androidx.compose.ui.Alignment.Top) {
            Checkbox(checked = train, onCheckedChange = { train = it })
            Column(Modifier.padding(top = 12.dp)) {
                Text("② 研究／訓練同意（選填，可隨時撤回）",
                    style = MaterialTheme.typography.titleSmall)
                // ⚠ 這裡曾經寫成 `**不同意亦不影響照護權益**`。
                // Compose 的 Text **不解析 Markdown**，病患看到的是帶著星號的字串——
                // 一份法律文件上出現排版符號，會讓人合理懷疑其他部分是不是也沒人看過。
                // 要強調就用字體與顏色，不要用星號。
                Text("同意將去識別化後的影像與醫師標註，用於本系統之演算法改良。",
                    style = MaterialTheme.typography.bodySmall)
                Text("不同意不影響您的照護權益。",
                    style = MaterialTheme.typography.bodySmall,
                    fontWeight = FontWeight.Bold,
                    color = MaterialTheme.colorScheme.primary)
                ConsentDetail("詳細說明（請務必閱讀）", TRAIN_DETAIL)
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

/**
 * 可展開的詳細說明。
 *
 * ## 為什麼是可展開，而不是全部攤開
 *
 * 完整揭露有六段，全部攤開的話簽名框會被推到第三頁——而實務上**沒有人會捲到那裡**。
 * 結果是「揭露得很完整」與「沒有人讀過」同時成立，那不是知情同意。
 *
 * 摺疊起來至少讓兩件事都可能：想快的人看得到摘要，想細看的人點得開。
 * 預設收合、但②訓練同意那一段的標題明寫「請務必閱讀」。
 */
@Composable
private fun ConsentDetail(title: String, body: String) {
    var open by remember { mutableStateOf(false) }
    TextButton(onClick = { open = !open }, contentPadding = PaddingValues(0.dp)) {
        Text((if (open) "▾ " else "▸ ") + title, style = MaterialTheme.typography.labelLarge)
    }
    if (open) {
        Surface(
            color = MaterialTheme.colorScheme.surfaceVariant,
            shape = MaterialTheme.shapes.small,
            modifier = Modifier.fillMaxWidth()
        ) {
            Text(body, style = MaterialTheme.typography.bodySmall,
                modifier = Modifier.padding(10.dp))
        }
    }
}

private const val CARE_DETAIL =
    "・拍什麼：您的傷口照片，以及一張放在傷口旁的方形校正貼紙（用來換算實際尺寸）。\n" +
    "・存在哪：這支手機內，加密保存。姓名與病歷號一併加密，不會傳到網路上。\n" +
    "・誰看得到：您的照護團隊（醫師、護理師、助理）。\n" +
    "・用途：計算傷口面積、組織比例與癒合趨勢，供臨床照護判斷參考。\n" +
    "・這是輔助工具：所有數值都需要醫師確認，不會取代醫師的診斷。\n" +
    "・不同意的話：無法使用本系統進行量測，您的傷口照護不受影響，會以原本的方式進行。"

/**
 * ⚠ 這段文字是對病患的承諾，改動前請確認系統真的做得到。
 *
 * 特別是「已訓練的模型無法反悔」那一條：撤回可以讓資料退出未來的訓練，
 * 但**已經學進模型參數裡的東西拿不回來**。這是機器學習的性質，不是實作偷懶。
 * 不寫的話病患會以為撤回等於完全抹除，而那不是事實——
 * 一份說了做不到的事的同意書，比沒有同意書更糟。
 */
private const val TRAIN_DETAIL =
    "・會離開手機的是什麼：傷口照片、醫師畫的傷口範圍與組織標記、面積與滲液量、" +
    "一組隨機代碼（例如 WD-1A2B3C4D）、手機型號。\n" +
    "・不會離開手機的是什麼：您的姓名、病歷號、簽名。這三項永遠只存在這支手機。\n" +
    "・存在哪裡：台灣境內的雲端主機。傳輸與儲存皆加密。\n" +
    "・誰看得到：本系統的研究與工程人員。他們看得到傷口照片，但看不到您是誰——" +
    "他們手上只有那組隨機代碼。\n" +
    "・保留多久：影像自最後一次量測起保留約 90 天後清除；由影像算出的數值（面積、" +
    "組織比例）會留在本機病歷中，作為您的照護紀錄。\n" +
    "・用途限於：改進本系統辨識傷口與組織的準確度。不會用於商業廣告，不會轉售，" +
    "不會用於與傷口照護無關的目的。\n" +
    "\n" +
    "・您可以隨時撤回：告訴任何一位照護人員即可，不需要理由，也不影響您的照護。\n" +
    "・撤回之後會發生什麼：您的影像會立即從資料庫中隔離，不再用於任何後續的訓練。\n" +
    "・⚠ 但有一件事要誠實告訴您：如果在您撤回之前，系統已經用您的資料訓練過某個版本的" +
    "模型，那個已完成的模型無法「忘記」學過的東西。撤回能停止未來的使用，" +
    "但無法回溯移除已經完成的訓練。這是這類技術的性質，我們認為您有權在同意前知道。\n" +
    "\n" +
    "・有疑問想問誰：請洽您的主治醫師或本研究的聯絡窗口。"

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
