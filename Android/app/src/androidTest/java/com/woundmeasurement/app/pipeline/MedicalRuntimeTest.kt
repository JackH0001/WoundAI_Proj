package com.woundmeasurement.app.pipeline

import android.content.Context
import android.content.ContextWrapper
import android.content.SharedPreferences
import androidx.test.core.app.ApplicationProvider
import com.woundmeasurement.app.data.store.MedicalDataScope
import com.woundmeasurement.app.data.store.MedicalAccountPreferences.ConsentQueue
import kotlinx.coroutines.*
import okhttp3.*
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.*
import org.junit.Test
import java.io.IOException
import java.util.UUID
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

class MedicalRuntimeTest {
    private class TestContext(base: Context) : ContextWrapper(base) {
        val prefix = "runtime-${UUID.randomUUID()}-"
        override fun getApplicationContext(): Context = this
        override fun getSharedPreferences(name: String, mode: Int): SharedPreferences = super.getSharedPreferences(prefix+name,mode)
        fun clear() { MedicalDataScope.entries.forEach { super.deleteSharedPreferences(prefix+it.settingsName) } }
    }
    private fun context() = TestContext(ApplicationProvider.getApplicationContext())
    private fun client(origin: String, current: () -> Boolean, review: Boolean, before: (Request) -> Unit = {}): BackendClient {
        val org = if(review) "mmh-review-demo" else "mmhps20261007"
        val http = OkHttpClient.Builder().addInterceptor { chain ->
            val r=chain.request(); before(r)
            val body = if(r.url.encodedPath.endsWith("/login")) """{"access_token":"synthetic-token","identity":"$org:dr01","org":"$org","user":"dr01","role":"physician","perms":["measure.sample","annotation.submit"]}"""
                else """{"session_contract":"account-revision-v1","identity":"$org:dr01","org":"$org","user":"dr01","role":"physician","role_zh":"醫師","perms":["measure.sample","annotation.submit"],"expires_at":9999999999}"""
            Response.Builder().request(r).protocol(Protocol.HTTP_1_1).code(200).message("Fixture").body(body.toResponseBody()).build()
        }.build()
        return BackendClient(origin,"",http,current,!review,org)
    }
    @Test fun conflictRetryUsesPinnedRevisionWithoutFetchingNewState()=runBlocking {
        val ctx=context();val paths=mutableListOf<String>();val posted=mutableListOf<String>()
        try {
            val runtime=MedicalRuntime(ctx,MedicalDataScope.SYNTHETIC_REVIEW) { o,c,_ ->
                val org="mmh-review-demo"
                val http=OkHttpClient.Builder().addInterceptor { chain ->
                    val r=chain.request();val path=r.url.encodedPath
                    val body=when {
                        path.endsWith("/login") -> """{"access_token":"synthetic-token","identity":"$org:dr01","org":"$org","user":"dr01","role":"physician","perms":["measure.sample","annotation.submit"]}"""
                        path.endsWith("/state") -> {paths.add(path);"""{"protocol":"medical-consent/2","code":"synthetic","revision":"${"a".repeat(64)}"}"""}
                        path.endsWith("/restore") -> {paths.add(path);val b=okio.Buffer();r.body!!.writeTo(b);posted.add(b.readUtf8());"{}"}
                        else -> """{"session_contract":"account-revision-v1","identity":"$org:dr01","org":"$org","user":"dr01","role":"physician","role_zh":"醫師","perms":["measure.sample","annotation.submit"],"expires_at":9999999999}"""
                    }
                    Response.Builder().request(r).protocol(Protocol.HTTP_1_1).code(if(path.endsWith("/restore"))409 else 200).message("Fixture").body(body.toResponseBody()).build()
                }.build()
                BackendClient(o,"",http,c,false,org)
            }
            runtime.setCredentials("dr01",UUID.randomUUID().toString())
            assertEquals(listOf("synthetic"),ConsentWithdrawal.restoreOnBackend(ctx,listOf("synthetic"),runtime).pending)
            runtime.accounts.deferRetry(ConsentQueue.RESTORE,0)
            ConsentWithdrawal.retryPending(ctx,runtime)
            assertEquals(listOf("/api/v1/consent/state","/api/v1/consent/restore","/api/v1/consent/restore"),paths)
            assertEquals(2,posted.size);assertEquals(posted[0],posted[1])
        } finally {ctx.clear()}
    }
    @Test fun accountChangeAndClosingRejectOldClients() {
        val ctx=context()
        try {
            val runtime=MedicalRuntime(ctx,MedicalDataScope.SYNTHETIC_REVIEW, { o,c,v -> client(o,c,v) })
            assertTrue(runtime.setCredentials("dr01",UUID.randomUUID().toString()))
            val old=runtime.acquire(); assertEquals("mmh-review-demo",old.identity?.org)
            assertTrue(runtime.setCredentials("dr02",UUID.randomUUID().toString()))
            try { old.validateMedicalSession();fail("Stale account accepted") } catch(_:IOException) {}
            runtime.deactivate()
            try { runtime.makeBackend().login("dr01",UUID.randomUUID().toString());fail("Closed context accepted") } catch(_:IOException) {}
        } finally {ctx.clear()}
    }
    @Test fun googleEpochOnlyInvalidatesMMHAndSettingsStaySeparate() {
        val ctx=context()
        try {
            val mmh=MedicalRuntime(ctx,MedicalDataScope.MMH, { o,c,v -> client(o,c,v) })
            val review=MedicalRuntime(ctx,MedicalDataScope.SYNTHETIC_REVIEW, { o,c,v -> client(o,c,v) })
            mmh.setCredentials("dr01",UUID.randomUUID().toString())
            assertFalse(review.hasCredentials())
            review.setCredentials("dr01",UUID.randomUUID().toString())
            val a=mmh.acquire();val b=review.acquire();MedicalSessionEpoch.invalidate()
            try {a.validateMedicalSession();fail("MMH old Google epoch accepted")}catch(_:IOException){}
            assertEquals("mmh-review-demo",b.validateMedicalSession().org)
            review.clearCredentials(); assertTrue(mmh.hasCredentials())
            assertNotEquals(mmh.backendUrl(),review.backendUrl())
        } finally {ctx.clear()}
    }
    @Test fun withdrawalDuringRestoreLoginPreventsStaleRestoreSend() = runBlocking {
        val ctx=context();val reached=CountDownLatch(1);val release=CountDownLatch(1)
        val sent=java.util.concurrent.CopyOnWriteArrayList<String>()
        try {
            val runtime=MedicalRuntime(ctx,MedicalDataScope.SYNTHETIC_REVIEW) { o,c,v ->
                client(o,c,v) { request ->
                    if(request.url.encodedPath.endsWith("/login")) { reached.countDown();check(release.await(10,TimeUnit.SECONDS)) }
                    if(request.url.encodedPath.startsWith("/api/v1/consent/")) sent.add(request.url.encodedPath)
                }
            }
            runtime.setCredentials("dr01",UUID.randomUUID().toString())
            val work=async(Dispatchers.Default) {ConsentWithdrawal.restoreOnBackend(ctx,listOf("SYNTH-CONSENT"),runtime)}
            assertTrue(reached.await(10,TimeUnit.SECONDS))
            runtime.addPendingWithdrawals(listOf("SYNTH-CONSENT"));release.countDown();work.await()
            assertTrue(sent.isEmpty());assertTrue(runtime.accounts.pending(ConsentQueue.RESTORE).isEmpty())
            assertEquals(setOf("SYNTH-CONSENT"),runtime.accounts.pending(ConsentQueue.WITHDRAWAL))
        } finally {release.countDown();ctx.clear()}
    }
    @Test fun closingDuringConsentLoginKeepsPendingOnlyInOriginalEnvironment() = runBlocking {
        val ctx=context();val reached=CountDownLatch(1);val release=CountDownLatch(1)
        try {
            val runtime=MedicalRuntime(ctx,MedicalDataScope.SYNTHETIC_REVIEW) { origin,current,review ->
                client(origin,current,review) { reached.countDown();check(release.await(10,TimeUnit.SECONDS)) }
            }
            runtime.setCredentials("dr01",UUID.randomUUID().toString())
            val work=async(Dispatchers.Default) { ConsentWithdrawal.pushToBackend(ctx,listOf("SYNTH-CONSENT"),runtime) }
            assertTrue(reached.await(10,TimeUnit.SECONDS))
            assertEquals(setOf("SYNTH-CONSENT"),runtime.accounts.pending(ConsentQueue.WITHDRAWAL))
            runtime.deactivate();release.countDown()
            assertEquals(listOf("SYNTH-CONSENT"),work.await().pending)
            assertEquals(setOf("SYNTH-CONSENT"),MedicalRuntime(ctx,MedicalDataScope.SYNTHETIC_REVIEW).accounts.pending(ConsentQueue.WITHDRAWAL))
            assertTrue(MedicalRuntime(ctx).accounts.pending(ConsentQueue.WITHDRAWAL).isEmpty())
        } finally {release.countDown();ctx.clear()}
    }
}
