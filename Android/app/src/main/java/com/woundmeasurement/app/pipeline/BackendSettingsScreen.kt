package com.woundmeasurement.app.pipeline

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.ui.platform.LocalFocusManager
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
import kotlinx.coroutines.delay
import kotlinx.coroutines.CancellationException
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

internal fun installedAppVersionLabel(ctx: android.content.Context): String {
    @Suppress("DEPRECATION")
    val info = ctx.packageManager.getPackageInfo(ctx.packageName, 0)
    @Suppress("DEPRECATION")
    val code = if (android.os.Build.VERSION.SDK_INT >= 28) info.longVersionCode else info.versionCode.toLong()
    return "App ${info.versionName ?: "未知"} (build $code)"
}

@Composable
fun BackendSettingsScreen(onBack: () -> Unit) {
    val ctx = LocalContext.current
    val runtime = rememberMedicalRuntime()
    val installedVersion = remember(ctx) { installedAppVersionLabel(ctx) }
    val scope = rememberCoroutineScope()
    val focus = LocalFocusManager.current

    var url by remember { mutableStateOf(runtime.backendUrl()) }
    var user by remember { mutableStateOf(runtime.backendUser()) }
    var pass by remember { mutableStateOf("") }
    // 已存過憑證但密碼欄留空 → 代表「沿用已存的密碼」，不要讓使用者以為沒存到
    var hasSaved by remember { mutableStateOf(runtime.hasCredentials()) }
    var testing by remember { mutableStateOf(false) }
    var result by remember { mutableStateOf<String?>(null) }
    var stats by remember { mutableStateOf<String?>(null) }
    var me by remember { mutableStateOf<LoginIdentity?>(null) }
    // 連線測試成功時留下這個 client（帶著有效 token），「開啟主控台」才拿得到一次性登入碼。
    var client by remember { mutableStateOf<BackendClient?>(null) }
    var opening by remember { mutableStateOf(false) }

    val mmh = runtime.needsGoogle
    var googleBusy by remember { mutableStateOf(false) }
    var googleReady by remember { mutableStateOf(com.woundmeasurement.app.institution.InstitutionRegistry.snapshot()?.ready()==true) }
    val onEmulator = remember { AppSettings.looksLikeEmulator() }
    val loopbackOnDevice = !onEmulator && AppSettings.isEmulatorLoopback(url)

    fun invalidateVerification() { me = null; client = null; stats = null; result = null }
    suspend fun connect(saveInput: Boolean) {
        if (testing || opening || googleBusy) return
        if (mmh && com.woundmeasurement.app.institution.InstitutionRegistry.snapshot()?.ready() != true) {
            result = "請先完成 Google 機構門禁登入。"; return
        }
        val norm = AppSettings.normalizeUrl(url)
        val u = user.trim()
        // Never reuse a saved password with another account or server.
        val p = pass.ifEmpty {
            if (u == runtime.backendUser() && norm == runtime.backendUrl())
                runtime.backendPassword() else ""
        }
        if (u.isBlank() || p.isBlank()) {
            invalidateVerification(); result = "請輸入帳號與密碼；切換帳號或位址時需要重新輸入密碼。"; return
        }
        testing = true; invalidateVerification()
        try {
            if (saveInput) {
                if (!runtime.setCredentials(u, p)) {
                    result = "密碼未能安全儲存，請重試；本機量測紀錄保留。"; return
                }
                runtime.setBackendUrl(norm)
                url = norm; user = u; pass = ""; hasSaved = true
            }
            val c = withContext(Dispatchers.IO) {
                if (runtime.needsLiveSession) runtime.acquire() else runtime.makeBackend(norm)
            }
            val ok = if (runtime.needsLiveSession) c.identity != null else withContext(Dispatchers.IO) { c.login(u, p) }
            if (!ok) {
                result = "登入未通過，請確認 WoundAI 帳號、密碼與機構入口。"; return
            }
            if (mmh && com.woundmeasurement.app.institution.InstitutionRegistry.snapshot()?.ready() != true) {
                result = "Google 門禁已到期，請重新登入。"; return
            }
            me = c.identity; client = c
            runtime.research.wake()
            result = "✅ WoundAI 已登入（${c.identity?.label() ?: u}）"
            val health = withContext(Dispatchers.IO) { c.health() }
            if (health?.degraded == true)
                result += "\n⚠ 雲端服務降級，請通知管理者；量測結果暫不作參考。"
            val queue = withContext(Dispatchers.IO) { c.flywheelStats() }
            stats = if (queue.first) queue.second else "佇列狀態暫時無法讀取；登入驗證已通過。"
        } catch (e: CancellationException) { throw e }
        catch (_: Exception) {
            if (me != null && (!mmh || com.woundmeasurement.app.institution.InstitutionRegistry.snapshot()?.ready() == true)) {
                result = "WoundAI 登入已驗證；雲端健康度或佇列狀態暫時無法讀取，請稍後重新驗證連線。"
            } else {
                invalidateVerification()
                result = "連線或登入未完成，請確認網路及登入授權後重試；本機紀錄保留。"
            }
        } finally { testing = false }
    }
    // Re-entering settings verifies saved credentials; saving is not proof of login.
    RestoreBackendAfterEntry(entryReady = !mmh || googleReady, entryBusy = googleBusy, hasSaved = hasSaved) {
        connect(false)
    }
    LaunchedEffect(mmh) {
        if (mmh) while (true) {
            delay(1000)
            val ready = com.woundmeasurement.app.institution.InstitutionRegistry.snapshot()?.ready() == true
            if (googleReady != ready) {
                googleReady = ready
                if (!ready) { invalidateVerification(); result = "Google 門禁未登入或已到期，請先登入 Google。" }
            }
        }
    }

    Column(
        Modifier.fillMaxSize().padding(16.dp).verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(10.dp)
    ) {
        Text("後端連線設定", style = MaterialTheme.typography.titleLarge)
        if (runtime.fixedOrigin) Text(runtime.title, style = MaterialTheme.typography.titleMedium)
        // App 版本。排錯的第一個問題永遠是「你裝的是哪一版」——
        // 沒有這一行，答案只能靠測試者回去翻手機的應用程式資訊，而多數人不會翻。
        Text(installedVersion +
             if (com.woundmeasurement.app.BuildConfig.DEBUG) " · DEBUG 版（不可用於真實個案）" else "",
            style = MaterialTheme.typography.bodySmall,
            color = if (com.woundmeasurement.app.BuildConfig.DEBUG) MaterialTheme.colorScheme.error
                    else MaterialTheme.colorScheme.onSurfaceVariant)

        if (mmh) com.woundmeasurement.app.institution.InstitutionSignIn(
            enabled = !testing && !opening,
            onBusy = { googleBusy = it },
            onChanged = { ready ->
                googleReady = ready
                user=runtime.backendUser(); pass=""; hasSaved=runtime.hasCredentials(); me=null; client=null; stats=null; result=null
            })
        OutlinedTextField(
            value = url, onValueChange = { url = it; invalidateVerification() },
            label = { Text("後端位址") },
            placeholder = { Text("http://192.168.1.50:5000") },
            singleLine = true, enabled = !runtime.fixedOrigin && !testing && !opening, modifier = Modifier.fillMaxWidth(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, imeAction = ImeAction.Next)
        )
        Text(
            if (runtime.scope == com.woundmeasurement.app.data.store.MedicalDataScope.SYNTHETIC_REVIEW) "合成示範固定入口，請使用專用 WoundAI 示範帳號；不需要 Google，MMH 帳號不能用於此環境。"
            else if (mmh) "MMH 合成驗收專用入口，位址固定；此版不供真實個案使用。"
            else if (onEmulator)
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
            "WoundAI 帳號決定醫師、護理或管理權限。密碼加密保存在此裝置；已儲存不代表登入已通過。",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
        OutlinedTextField(
            value = user, onValueChange = { user = it; invalidateVerification() },
            label = { Text("帳號") }, singleLine = true, enabled = !testing && !opening && !googleBusy && (!mmh || googleReady), modifier = Modifier.fillMaxWidth(),
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Next)
        )
        OutlinedTextField(
            value = pass, onValueChange = { pass = it; invalidateVerification() },
            label = { Text(if (hasSaved) "密碼（留空＝沿用已儲存的）" else "密碼") },
            singleLine = true, enabled = !testing && !opening && !googleBusy && (!mmh || googleReady), modifier = Modifier.fillMaxWidth(),
            visualTransformation = PasswordVisualTransformation(),
            keyboardActions = KeyboardActions(onDone = { focus.clearFocus() }),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password, imeAction = ImeAction.Done)
        )

        ResearchOutboxPanel(runtime)

        BackendSignInActions(
            busy = testing, authenticated = me != null && (!mmh || googleReady),
            identityLabel = me?.label(), hasSaved = hasSaved,
            entryReady = !mmh || googleReady, enabled = !opening && !googleBusy,
            onSignIn = { focus.clearFocus(); scope.launch { connect(true) } },
            onSignOut = {
                runtime.clearCredentials()
                user = ""; pass = ""; hasSaved = false; invalidateVerification()
                result = "已登出此裝置的 WoundAI 帳號。本機量測紀錄保留；瀏覽器需另行登出。"
                focus.clearFocus()
            }
        )

        if (testing) {
            LinearProgressIndicator(Modifier.fillMaxWidth())
            Text("正在連線並確認帳號；雲端主機啟動可能需要一點時間，請勿重複送出。")
        }
        TextButton({ focus.clearFocus() }) { Text("收起鍵盤") }
        if (!runtime.needsLiveSession) Text("MMH 私有入口另需 Google 授權。本 Android 版本尚未接入 Google/IAP，不能只改後端網址就當成 MMH 專用版。", style = MaterialTheme.typography.bodySmall)

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

        if (hasSaved && me == null) {
            Divider()
            OutlinedButton(
                enabled = !testing && !opening && !googleBusy,
                onClick = { runtime.clearCredentials(); hasSaved = false; user = ""; pass = ""; invalidateVerification()
                            result = "已清除本機儲存的後端帳密" },
                modifier = Modifier.fillMaxWidth()
            ) { Text("清除已儲存的帳密") }
        }

        Divider()
        Text(
            "重新進入設定會再次核對登入狀態。App 更新或重啟後，MMH Google 門禁需重新驗證；這不會刪除量測紀錄。",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
        PrivateExportSettings(runtime)
        OutlinedButton(onBack, Modifier.fillMaxWidth(), enabled = !testing && !opening && !googleBusy) { Text("返回") }
    }
}
