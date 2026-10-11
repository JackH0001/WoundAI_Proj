package com.woundmeasurement.app.institution

import android.content.MutableContextWrapper
import androidx.credentials.CredentialManager
import androidx.credentials.GetCredentialRequest
import androidx.credentials.ClearCredentialStateRequest
import androidx.credentials.CustomCredential
import com.google.android.libraries.identity.googleid.GetSignInWithGoogleOption
import com.google.android.libraries.identity.googleid.GoogleIdTokenCredential
import androidx.compose.runtime.*
import androidx.compose.material3.*
import androidx.compose.ui.platform.LocalContext
import androidx.activity.compose.BackHandler
import kotlinx.coroutines.delay
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.coroutines.launch
import kotlinx.coroutines.CancellationException
import okhttp3.OkHttpClient
import okhttp3.Request
import java.security.SecureRandom
import java.util.concurrent.TimeUnit

@Composable
fun InstitutionSignIn(enabled: Boolean, onBusy: (Boolean) -> Unit, onChanged: (Boolean) -> Unit) {
    val ctx=LocalContext.current
    val scope=rememberCoroutineScope()
    var busy by remember { mutableStateOf(false) }
    var status by remember { mutableStateOf(if (InstitutionRegistry.snapshot()?.ready()==true) "Google 與機構入口驗證通過" else "請先登入 Google 機構入口") }
    var ready by remember { mutableStateOf(InstitutionRegistry.snapshot()?.ready()==true) }
    LaunchedEffect(Unit) {
        while (true) {
            delay(1000)
            val now=InstitutionRegistry.snapshot()?.ready()==true
            if (ready && !now && !busy) status="Google 門禁已到期，請重新登入"
            ready=now
        }
    }
    val manager=remember { CredentialManager.create(ctx) }
    BackHandler(busy) {}
    Text("1. Google 機構門禁 → 2. WoundAI 帳號與角色")
    Text("Google 確認你有權進入 MMH 私有服務；WoundAI 醫師／護理帳號決定可執行的操作。Google 入口由管理者在 GCP IAP 管理。")
    Button(enabled=enabled && !busy, onClick={
        busy=true; ready=false; onBusy(true); InstitutionRegistry.replace(null); onChanged(false)
        status="正在開啟 Google 登入…"
        scope.launch {
            var next: InstitutionSession? = null
            try {
                val nonce=ByteArray(32).also { SecureRandom().nextBytes(it) }.joinToString("") { "%02x".format(it) }
                val option=GetSignInWithGoogleOption.Builder(InstitutionConfig.AUDIENCE).setNonce(nonce).build()
                val response=manager.getCredential(MutableContextWrapper(ctx),GetCredentialRequest.Builder().addCredentialOption(option).build())
                val c=response.credential
                require(c is CustomCredential && c.type == GoogleIdTokenCredential.TYPE_GOOGLE_ID_TOKEN_CREDENTIAL)
                val google=GoogleIdTokenCredential.createFrom(c.data)
                val session=InstitutionSession.parse(google.idToken,nonce); next=session
                status="Google 已登入，正在確認 MMH 入口…"
                withContext(Dispatchers.IO) {
                    val http=OkHttpClient.Builder().followRedirects(false).followSslRedirects(false)
                        .retryOnConnectionFailure(false).callTimeout(90,TimeUnit.SECONDS)
                        .addInterceptor(InstitutionInterceptor(session)).build()
                    http.newCall(Request.Builder().url(InstitutionConfig.ORIGIN+"/api/health?android_entry_probe=1").build()).execute().use {
                        InstitutionSession.verifyHealth(it.code,it.body?.string() ?: "")
                    }
                }
                session.markVerified(); InstitutionRegistry.replace(session); next=null
                ready=true; status="Google 與機構入口驗證通過"; onChanged(true)
            } catch (e: CancellationException) { throw e }
            catch (e: java.io.IOException) { status=e.message ?: "機構入口未通過" }
            catch (_: Exception) { status="Google 登入未完成。請確認 Android OAuth 套件／簽章已登記，並在 Google 視窗選擇有入口權限的帳號。" }
            finally { next?.invalidate(); busy=false; onBusy(false) }
        }
    }) { Text(if(busy) "正在驗證…" else if(ready) "切換 Google 帳號" else "Sign in with Google") }
    if(busy) LinearProgressIndicator()
    Text(status)
    Text("此驗證版不保存 Google token；重啟 App 或授權到期時須重新登入，未完成上傳的本機紀錄保留。重新登入、切換或登出 Google 後，需要再次輸入 WoundAI 帳密。")
    if (ready) OutlinedButton(enabled=enabled && !busy, onClick={
        InstitutionRegistry.replace(null); ready=false; onChanged(false); status="已登出 Google 與本機 WoundAI 帳號"
        scope.launch { runCatching { manager.clearCredentialState(ClearCredentialStateRequest()) } }
    }) { Text("登出 Google 門禁與 WoundAI") }
}
