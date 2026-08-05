package com.woundmeasurement.app.pipeline

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import android.content.Intent
import android.net.Uri
import com.woundmeasurement.app.data.store.AppSettings
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * 後端連線設定：位址、憑證、連線測試、飛輪佇列健康度。
 *
 * ## 為什麼這頁是 n=20 臨床收案的前置
 *
 * 在此之前後端位址寫死 `http://10.0.2.2:5000`（模擬器專用 loopback 別名），帳密寫死
 * `admin`/`woundai-admin`。兩者都只在模擬器成立，而臨床收案**必須在真機上做**
 * （模擬器沒有相機、拍不到 ArUco 標記）。沒有這一頁，帶著手機到病房就是 classify 直接失敗，
 * 而畫面只會說「後端未連線」，完全看不出是位址問題。
 *
 * ## 這頁刻意做的事
 *
 * - **連線測試與登入分開回報**。「連不上」和「連得上但帳密錯」是兩個完全不同的問題，
 *   合成一句「登入失敗」會讓人在醫院現場亂猜（改防火牆？改密碼？）。
 * - **真機上使用 `10.0.2.2` 會被明確警告**，不是等到量測失敗才發現。
 * - **佇列健康度按來源拆開**。`by_source.clinical` 才是 n=20 的分母；
 *   範例與模擬圖走同一條管線收進來，混在一起看會讓收案進度虛胖。
 */
/** 以外部瀏覽器開啟。失敗（無瀏覽器）時靜默略過——這是輔助入口，不該讓設定頁崩潰。 */
private fun openUrl(ctx: android.content.Context, url: String) {
    runCatching {
        ctx.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }
}

@Composable
fun BackendSettingsScreen(onBack: () -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()

    var url by remember { mutableStateOf(AppSettings.backendUrl(ctx)) }
    var user by remember { mutableStateOf(AppSettings.backendUser(ctx)) }
    var pass by remember { mutableStateOf("") }
    // 已存過憑證但密碼欄留空 → 代表「沿用已存的密碼」，不要讓使用者以為沒存到
    var hasSaved by remember { mutableStateOf(AppSettings.hasCredentials(ctx)) }
    var testing by remember { mutableStateOf(false) }
    var result by remember { mutableStateOf<String?>(null) }
    var stats by remember { mutableStateOf<String?>(null) }
    var me by remember { mutableStateOf<LoginIdentity?>(null) }
    // 連線測試成功時留下這個 client（帶著有效 token），「開啟主控台」才拿得到一次性登入碼。
    var client by remember { mutableStateOf<BackendClient?>(null) }
    var opening by remember { mutableStateOf(false) }

    val onEmulator = remember { AppSettings.looksLikeEmulator() }
    val loopbackOnDevice = !onEmulator && AppSettings.isEmulatorLoopback(url)

    Column(
        Modifier.fillMaxSize().padding(16.dp).verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(10.dp)
    ) {
        Text("後端連線設定", style = MaterialTheme.typography.titleLarge)

        OutlinedTextField(
            value = url, onValueChange = { url = it },
            label = { Text("後端位址") },
            placeholder = { Text("http://192.168.1.50:5000") },
            singleLine = true, modifier = Modifier.fillMaxWidth(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, imeAction = ImeAction.Next)
        )
        Text(
            if (onEmulator)
                "模擬器：10.0.2.2 對映到你開發機的 127.0.0.1，維持預設即可。"
            else
                "真機：請填開發機/伺服器在同一網段的實際 IP（例如 http://192.168.1.50:5000），" +
                "並確認防火牆放行 5000 埠。手機與電腦要在同一個 Wi-Fi。",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
        if (loopbackOnDevice) Text(
            "⚠ 這台不是模擬器，而位址是 10.0.2.2 —— 那是模擬器專用的別名，真機上不存在。" +
            "維持這個設定的話，量測與補送標註都會失敗且只會顯示「後端未連線」。",
            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error
        )

        Divider()
        Text("後端帳號", style = MaterialTheme.typography.titleSmall)
        Text(
            "帳密**存在這台手機**（密碼以 Keystore 加密），不再編進 APK。" +
            "先前版本把 admin 帳密寫死在程式裡——APK 可反編譯，等於把後端鑰匙一起發出去。",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
        OutlinedTextField(
            value = user, onValueChange = { user = it },
            label = { Text("帳號") }, singleLine = true, modifier = Modifier.fillMaxWidth(),
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Next)
        )
        OutlinedTextField(
            value = pass, onValueChange = { pass = it },
            label = { Text(if (hasSaved) "密碼（留空＝沿用已儲存的）" else "密碼") },
            singleLine = true, modifier = Modifier.fillMaxWidth(),
            visualTransformation = PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password, imeAction = ImeAction.Done)
        )

        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
            Button(
                onClick = {
                    val norm = AppSettings.normalizeUrl(url)
                    url = norm
                    AppSettings.setBackendUrl(ctx, norm)
                    // 密碼留空＝沿用已存的；只有真的輸入了才覆寫（否則每次改位址都要重打密碼）
                    result = if (pass.isNotEmpty()) {
                        if (AppSettings.setCredentials(ctx, user, pass)) {
                            hasSaved = true; pass = ""; "✅ 已儲存（密碼已加密）"
                        } else {
                            // 加密失敗寧可不存也不存明文，並且要說出來——靜默失敗會讓人以為存好了
                            "⚠ 密碼加密失敗，**未儲存**。位址已存。請重試；若持續失敗請重裝 App 重建金鑰。"
                        }
                    } else "✅ 位址已儲存（密碼沿用先前儲存的）"
                },
                modifier = Modifier.weight(1f)
            ) { Text("儲存") }

            OutlinedButton(
                onClick = {
                    testing = true; result = null; stats = null
                    val norm = AppSettings.normalizeUrl(url)
                    val u = user.trim()
                    val p = pass.ifEmpty { AppSettings.backendPassword(ctx) }
                    scope.launch {
                        // 明確標注型別:三個分支各自推導出的 Triple 第三格分別是 Pair / Nothing?，
                        // 讓編譯器自己統一容易在 Kotlin 1.9 上踩到推導失敗。
                        val r: Triple<Boolean, String, Pair<Boolean, String>?> =
                            withContext(Dispatchers.IO) {
                                val c = BackendClient(norm)
                                // 「連不上」與「連得上但帳密錯」要分開講:在醫院現場這是兩種完全不同的處置
                                //（改網段/防火牆 vs 改帳密），合成一句「登入失敗」只會讓人亂試。
                                try {
                                    if (c.login(u, p)) {
                                        me = c.identity
                                        client = c
                                        Triple(true, "✅ 連線成功（${c.identity?.label() ?: u}）",
                                               c.flywheelStats())
                                    }
                                    else Triple(
                                        false,
                                        "⚠ 連得到後端，但**帳密不正確**。位址沒問題，請確認帳號密碼。", null)
                                } catch (e: Exception) {
                                    Triple(
                                        false,
                                        "⚠ **連不到後端**：${e.message}\n" +
                                        "請檢查：① app.py 是否啟動 ② 位址與埠號 " +
                                        "③ 手機與電腦是否同網段 ④ 防火牆是否放行 5000 埠", null)
                                }
                            }
                        result = r.second
                        stats = r.third?.let { (ok, m) -> if (ok) m else "佇列狀態讀取失敗：$m" }
                        testing = false
                    }
                },
                enabled = !testing, modifier = Modifier.weight(1f)
            ) { Text(if (testing) "測試中…" else "連線測試") }
        }

        me?.let { u ->
            Divider()
            Text("目前身分", style = MaterialTheme.typography.titleSmall)
            Text("${u.label()}　${u.identity}", style = MaterialTheme.typography.bodyMedium)

            // 主控台連結依角色分流。
            //
            // ⚠ **GCP 專案權限 ≠ 應用程式權限**。給臨床角色 GCP Console 的入口，
            // 會誘導人去申請 IAM——而有了 IAM 就能直接讀儲存桶裡的原始傷口影像、
            // 看 Secret、刪資源，完全繞過本 App 所有閘門。
            // 臨床角色的正確目的地是我們自己的 /console：唯讀、只顯示去識別資料。
            // 同一個 /console，但依角色展開不同分區——所以按鈕文字也要講清楚
            // 按下去會看到什麼。管理者看到的是會**改變授權狀態**的介面，
            // 用「唯讀」當標籤會讓人以為隨便按都沒關係。
            // 開啟主控台：先跟後端要一個**一次性登入碼**，附在 URL fragment 帶過去，
            // 瀏覽器那邊自動換成 session，醫師不必再打一次密碼。
            //
            // ⚠ 絕不可改成把 jwt 放進查詢字串：Cloud Run 會把完整 URL 寫進 Cloud Logging，
            // token 就以明文留在日誌裡，效期還有 24 小時。fragment 不會送到伺服器。
            //
            // 拿不到代碼時仍然開啟網址，只是要手動登入——比整個按鈕失效好。
            fun openConsole(tab: String?) {
                val base = AppSettings.normalizeUrl(url)
                opening = true
                scope.launch {
                    val code = withContext(Dispatchers.IO) { client?.oneTimeCode() }
                    opening = false
                    val frag = when {
                        code != null && tab != null -> "#c=$code&t=$tab"
                        code != null -> "#c=$code"
                        tab != null -> "#$tab"
                        else -> ""
                    }
                    if (code == null) result = "ℹ 取不到一次性登入碼，已開啟主控台但需手動登入。"
                    openUrl(ctx, "$base/console$frag")
                }
            }

            if (u.can("flywheel.stats")) {
                OutlinedButton({ openConsole("recs") }, Modifier.fillMaxWidth(), enabled = !opening) {
                    Text(if (opening) "準備登入…" else "開啟「我的送件」（可自行標記排除誤送）")
                }
            }
            if (u.can("user.manage") || u.can("audit.read")) {
                OutlinedButton({ openConsole(null) }, Modifier.fillMaxWidth(), enabled = !opening) {
                    Text(if (u.can("user.manage")) "開啟管理主控台（帳號管理・稽核・系統狀態）"
                         else "開啟主控台（稽核・系統狀態・佇列）")
                }
                if (u.can("user.manage")) Text(
                    "帳號管理在瀏覽器而不在 App：開帳號要看得到完整清單與稽核軌跡，" +
                    "手機版面塞不下；而且**新密碼只顯示一次**，需要能立刻複製貼上傳給本人。" +
                    "刪除刻意不提供——稽核軌跡引用著那些識別碼，離職請停用。",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
            Text("主控台以一次性登入碼自動登入（60 秒有效、用過即失效，且放在網址的 # 之後——" +
                 "那一段不會送到伺服器，因此不會留在雲端日誌裡）。",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            if (u.can("gcp.console")) {
                OutlinedButton({ openUrl(ctx, "https://console.cloud.google.com/run?project=woundai-jackh001") },
                    Modifier.fillMaxWidth()) { Text("開啟 GCP 雲端主控台（需 Google 帳號授權）") }
                Text("GCP 主控台需要你的 Google 帳號另具 IAM 權限；沒有的話會顯示權限不足。" +
                     "它與 App 的角色是兩套獨立的權限系統。",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }

        result?.let {
            Text(it, style = MaterialTheme.typography.bodyMedium,
                color = if (it.startsWith("✅")) MaterialTheme.colorScheme.primary
                        else MaterialTheme.colorScheme.error)
        }

        stats?.let {
            Divider()
            Text("飛輪佇列健康度", style = MaterialTheme.typography.titleSmall)
            Text(it, style = MaterialTheme.typography.bodyMedium)
            Text(
                "「臨床」那一欄才是 n=20 收案進度的分母。範例與模擬圖走同一條管線收進來，" +
                "但不計入臨床樣本數——訓練時也要排除（範例是 escalate 路由的驗收基準，" +
                "拿去訓練等於考卷當講義）。",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
        }

        if (hasSaved) {
            Divider()
            OutlinedButton(
                onClick = { AppSettings.clearCredentials(ctx); hasSaved = false; user = ""; pass = ""
                            result = "已清除本機儲存的後端帳密" },
                modifier = Modifier.fillMaxWidth()
            ) { Text("清除已儲存的帳密") }
        }

        Divider()
        Text(
            "誠實邊界：本頁做到「憑證不隨 APK 散佈、不以明文落地」。它不是完整的身分驗證方案——" +
            "正解是後端改走院內 SSO 並發短效 token，讓 App 完全不碰密碼。列為後續。",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
        OutlinedButton(onBack, Modifier.fillMaxWidth()) { Text("返回") }
    }
}
