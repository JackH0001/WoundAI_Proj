package com.woundmeasurement.app.institution

import android.content.Context
import androidx.credentials.*
import com.google.android.libraries.identity.googleid.GetGoogleIdOption
import com.google.android.libraries.identity.googleid.GetSignInWithGoogleOption
import com.google.android.libraries.identity.googleid.GoogleIdTokenCredential
import androidx.compose.runtime.*
import androidx.compose.material3.*
import androidx.compose.ui.platform.LocalContext
import kotlinx.coroutines.*
import okhttp3.OkHttpClient
import okhttp3.Request
import java.security.SecureRandom
import java.util.concurrent.TimeUnit
import com.woundmeasurement.app.data.store.AppSettings

/** Google owns credential storage/refresh. This app persists only the chosen subject and opt-in. */
object InstitutionLogin {
    var busy by mutableStateOf(false); private set
    var status by mutableStateOf("Google 門禁：尚未驗證"); private set
    private var lastRestore = 0L
    private fun prefs(ctx: Context) = ctx.getSharedPreferences("mmh_google_identity", Context.MODE_PRIVATE)
    suspend fun restore(ctx: Context) {
        if (busy || InstitutionRegistry.snapshot()?.ready() == true || !prefs(ctx).getBoolean("restore", false)) return
        val now = android.os.SystemClock.elapsedRealtime()
        if (lastRestore != 0L && now-lastRestore < 60_000) return
        lastRestore = now
        authenticate(ctx, automatic = true)
    }
    suspend fun authenticate(ctx: Context, automatic: Boolean = false) {
        if (busy) return
        busy=true
        var next: InstitutionSession? = null
        // Invalidate active clients before asking for a new identity; retain encrypted credentials
        // until the Google subject has been verified. Explicit logout clears both gates.
        InstitutionRegistry.replace(null)
        status=if(automatic) "正在恢復 Google 登入…" else "正在開啟 Google 登入…"
        try {
            val manager=CredentialManager.create(ctx)
            val nonce=ByteArray(32).also { SecureRandom().nextBytes(it) }.joinToString("") { "%02x".format(it) }
            val builder=GetCredentialRequest.Builder()
            if(automatic) builder.addCredentialOption(GetGoogleIdOption.Builder()
                .setServerClientId(InstitutionConfig.AUDIENCE).setNonce(nonce)
                .setFilterByAuthorizedAccounts(true).setAutoSelectEnabled(true).build())
            else builder.addCredentialOption(GetSignInWithGoogleOption.Builder(InstitutionConfig.AUDIENCE).setNonce(nonce).build())
            val c=manager.getCredential(ctx,builder.build()).credential
            require(c is CustomCredential && c.type == GoogleIdTokenCredential.TYPE_GOOGLE_ID_TOKEN_CREDENTIAL)
            val google=GoogleIdTokenCredential.createFrom(c.data)
            val session=InstitutionSession.parse(google.idToken,nonce); next=session
            val previous=prefs(ctx).getString("subject",null)
            if (automatic && !InstitutionIdentityPolicy.mayRestore(previous, session.subject)) throw java.io.IOException("Google 帳號不同，請由設定明確切換帳號")
            status="Google 已登入，正在確認 MMH 入口…"
            withContext(Dispatchers.IO) {
                val http=OkHttpClient.Builder().followRedirects(false).followSslRedirects(false)
                    .retryOnConnectionFailure(false).callTimeout(90,TimeUnit.SECONDS)
                    .addInterceptor(InstitutionInterceptor(session)).build()
                http.newCall(Request.Builder().url(InstitutionConfig.ORIGIN+"/api/health?android_entry_probe=1").build()).execute().use {
                    InstitutionSession.verifyHealth(it.code,it.body?.string() ?: "")
                }
            }
            if(!InstitutionIdentityPolicy.preserveAppCredentials(previous, session.subject)) AppSettings.clearCredentials(ctx)
            session.markVerified(); InstitutionRegistry.replace(session); next=null
            prefs(ctx).edit().putString("subject",session.subject).putBoolean("restore",true).apply()
            status="Google 與機構入口驗證通過"
        } catch(e: CancellationException) { throw e }
        catch(e: java.io.IOException) { status=e.message ?: "機構入口未通過" }
        catch(_: Exception) { status="Google 登入未完成，請在設定重新登入；已保存的紀錄保留。" }
        finally { next?.invalidate(); busy=false }
    }
    suspend fun signOut(ctx: Context) {
        if(busy) return
        busy=true
        try {
            prefs(ctx).edit().clear().apply()
            InstitutionRegistry.replace(null); AppSettings.clearCredentials(ctx)
            runCatching { CredentialManager.create(ctx).clearCredentialState(ClearCredentialStateRequest()) }
            status="已登出 Google 與 WoundAI；不會自動恢復，直到您再次登入。"
        } finally { busy=false }
    }
}

@Composable
fun InstitutionSignIn(enabled: Boolean, onBusy: (Boolean) -> Unit, onChanged: (Boolean) -> Unit) {
    val ctx=LocalContext.current
    val scope=rememberCoroutineScope()
    var ready by remember { mutableStateOf(InstitutionRegistry.snapshot()?.ready()==true) }
    LaunchedEffect(Unit) { InstitutionLogin.restore(ctx) }
    LaunchedEffect(InstitutionLogin.busy) { onBusy(InstitutionLogin.busy) }
    LaunchedEffect(Unit) {
        while(true) { ready=InstitutionRegistry.snapshot()?.ready()==true; delay(1000) }
    }
    Text("1. Google 機構門禁 → 2. WoundAI 帳號與角色")
    Text("Google 確認機構入口資格；WoundAI 決定醫師／護理權限。入口權限由機構管理員管理。")
    Button(enabled=enabled && !InstitutionLogin.busy, onClick={ scope.launch {
        InstitutionLogin.authenticate(ctx); onChanged(InstitutionRegistry.snapshot()?.ready()==true)
    } }) { Text(if(InstitutionLogin.busy) "正在驗證…" else if(ready) "切換 Google 帳號" else "Sign in with Google") }
    if(InstitutionLogin.busy) LinearProgressIndicator()
    Text(if(ready) "Google 與機構入口驗證通過" else if(!InstitutionLogin.busy && InstitutionLogin.status == "Google 與機構入口驗證通過") "Google 門禁已到期，請重新驗證" else InstitutionLogin.status)
    Text("首次授權後，開啟 App 會嘗試恢復原 Google 帳號並驗證入口。同一帳號可沿用已儲存的 WoundAI 登入資料；撤銷授權、到期或切換帳號時可能需要重新登入。")
    if(ready) OutlinedButton(enabled=enabled && !InstitutionLogin.busy, onClick={ scope.launch {
        InstitutionLogin.signOut(ctx); ready=false; onChanged(false)
    } }) { Text("登出 Google 與 WoundAI") }
}
