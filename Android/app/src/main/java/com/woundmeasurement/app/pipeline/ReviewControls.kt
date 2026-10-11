package com.woundmeasurement.app.pipeline

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier

/** Do not let edit completion race the asynchronous identity lookup. */
@Composable
internal fun ReviewEditEntry(
    loading: Boolean, submitting: Boolean, imageReady: Boolean,
    canVerify: Boolean, onEdit: () -> Unit
) {
    Button(onClick = onEdit, enabled = !loading && !submitting && imageReady,
        modifier = Modifier.fillMaxWidth()) {
        Text(if (loading) "正在驗證登入權限…" else "修邊標註（檢視／修正遮罩）")
    }
    if (!loading && imageReady) Text(
        if (canVerify) "確認遮罩後請按「完成修邊」；移動、縮放或取消不代表醫師確認。"
        else "目前身分只能修邊與存本機，不會產生醫師確認。",
        style = MaterialTheme.typography.bodySmall)
}

/** Presentation only: preserve original notes verbatim in storage. */
@Composable
internal fun ReviewNotes(notes: String) {
    if (notes.isBlank()) return
    var expanded by remember(notes) { mutableStateOf(false) }
    val revisions = remember(notes) { notes.lineSequence().count { it.startsWith("⟳ ") } }
    TextButton(onClick = { expanded = !expanded }) {
        Text(if (expanded) "收合修訂紀錄／原始備註"
            else "查看修訂紀錄（${revisions} 次）／原始備註")
    }
    if (expanded) {
        Text("以下包含首次量測與歷次修訂；目前面積以上方卡片為準。",
            style = MaterialTheme.typography.bodySmall)
        Text(notes, style = MaterialTheme.typography.bodySmall)
    }
}
