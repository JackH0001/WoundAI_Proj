package com.woundmeasurement.app.pipeline

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier

/** Indeterminate progress: a receipt, not elapsed time, completes an upload. */
@Composable
internal fun AnnotationSubmitButton(busy: Boolean, enabled: Boolean, label: String, onClick: () -> Unit) {
    Column {
        Button(onClick = onClick, enabled = enabled && !busy, modifier = Modifier.fillMaxWidth()) {
            Text(if (busy) "正在確認標註保存…" else label)
        }
        if (busy) {
            LinearProgressIndicator(Modifier.fillMaxWidth())
            Text("正在上傳並等待後端確認，請留在此頁；完成前不要重複送出。", style = MaterialTheme.typography.bodySmall)
        }
    }
}
