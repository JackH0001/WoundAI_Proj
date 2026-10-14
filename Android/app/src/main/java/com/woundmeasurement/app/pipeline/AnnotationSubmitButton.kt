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
            Text(if (busy) "正在保存研究待傳資料…" else label)
        }
        if (busy) {
            LinearProgressIndicator(Modifier.fillMaxWidth())
            Text("正在核對並保存待傳工作，請等候入列完成；尚未確認雲端保存。", style = MaterialTheme.typography.bodySmall)
        }
    }
}
