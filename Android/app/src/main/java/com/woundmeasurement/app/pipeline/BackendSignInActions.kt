package com.woundmeasurement.app.pipeline

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier

/** A saved password is not a verified session. */
@Composable
internal fun BackendSignInActions(
    busy: Boolean, authenticated: Boolean, identityLabel: String?,
    hasSaved: Boolean, entryReady: Boolean, enabled: Boolean,
    onSignIn: () -> Unit, onSignOut: () -> Unit
) {
    Text(when {
        !entryReady -> "WoundAI：尚未登入，請先完成 Google 門禁"
        busy && authenticated -> "WoundAI：已登入（${identityLabel ?: "身分已驗證"}），正在讀取雲端狀態…"
        busy -> "WoundAI：正在驗證登入…"
        authenticated -> "WoundAI：已登入（${identityLabel ?: "身分已驗證"}）"
        hasSaved -> "WoundAI：已儲存帳號，尚未驗證登入"
        else -> "WoundAI：尚未登入"
    }, style = MaterialTheme.typography.bodyMedium)
    Button(onClick = onSignIn, enabled = enabled && entryReady && !busy,
        modifier = Modifier.fillMaxWidth()) {
        Text(if (busy && authenticated && entryReady) "正在讀取雲端狀態…" else if (busy) "正在登入…" else if (authenticated && entryReady) "重新驗證連線" else "儲存並登入")
    }
    if (authenticated && entryReady && !busy) OutlinedButton(
        onClick = onSignOut, enabled = enabled, modifier = Modifier.fillMaxWidth()
    ) { Text("登出 WoundAI 帳號") }
}
