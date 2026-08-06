package com.woundmeasurement.app.pipeline

import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView

/**
 * 使用說明書。內容是 `assets/manual.html`，用 WebView 顯示。
 *
 * ## 為什麼放在 assets 而不是連到後端
 *
 * 病房的 Wi-Fi 常常不穩，而**最需要看手冊的時刻正是操作卡住的時候**——
 * 那時如果手冊還要連線才打得開，它就等於不存在。
 * 放進 APK 只多幾十 KB，換來離線可用。
 *
 * 代價是改手冊要重新出一版 APK。這是可接受的：手冊的內容取自程式碼裡的
 * 按鈕名稱與訊息文字，本來就該與 APK 同一版——手冊比 App 新或舊都會誤導人。
 *
 * ## WebView 的設定
 *
 * 只開 JavaScript（角色切換需要），不開檔案存取、不開網路。
 * 手冊是我們自己的靜態檔，沒有理由讓它有更多能力。
 */
@Composable
fun ManualScreen(onBack: () -> Unit) {
    BackHandler(onBack = onBack)
    Column(Modifier.fillMaxSize()) {
        Row(
            Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp),
            verticalAlignment = androidx.compose.ui.Alignment.CenterVertically
        ) {
            Text("使用說明書", style = MaterialTheme.typography.titleMedium,
                modifier = Modifier.weight(1f))
            OutlinedButton(onBack) { Text("返回") }
        }
        Divider()
        AndroidView(
            modifier = Modifier.fillMaxSize(),
            factory = { ctx ->
                WebView(ctx).apply {
                    // 本機檔案，不需要也不應該有網路能力。
                    webViewClient = WebViewClient()
                    settings.javaScriptEnabled = true      // 角色切換
                    settings.allowFileAccess = false
                    settings.allowContentAccess = false
                    settings.domStorageEnabled = false
                    // 手冊的 CSS 已針對手機寬度寫好，不要讓 WebView 再套一次縮放，
                    // 否則字會小到看不清楚而使用者只會覺得「這個手冊很難看」。
                    settings.useWideViewPort = false
                    settings.loadWithOverviewMode = false
                    loadUrl("file:///android_asset/manual.html")
                }
            }
        )
    }
}
