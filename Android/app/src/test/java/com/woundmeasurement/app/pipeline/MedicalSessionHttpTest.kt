package com.woundmeasurement.app.pipeline

import okhttp3.OkHttpClient
import okhttp3.Response
import okhttp3.Protocol
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Test
import org.junit.Assert.*
import java.io.IOException
import java.util.concurrent.atomic.AtomicInteger

/** HTTP receipt and epoch guards exercised through the actual BackendClient; no network/token required. */
class MedicalSessionHttpTest {
    private val body="""{"session_contract":"account-revision-v1","identity":"mmhps20261007:dr01","org":"mmhps20261007","user":"dr01","role":"physician","role_zh":"醫師","perms":["measure.sample"],"expires_at":9999999999}"""
    private fun client(code: () -> Int={200},text: () -> String={body},before: () -> Unit={}) = BackendClient(
        "https://synthetic.invalid",java.util.UUID.randomUUID().toString(),OkHttpClient.Builder()
            .addNetworkInterceptor {error("Tests must not access network")}
            .addInterceptor { chain ->
                // The production epoch guard runs before this deterministic synthetic response.
                before();Response.Builder().request(chain.request()).protocol(Protocol.HTTP_1_1).code(code()).message("fixture")
                    .body(text().toResponseBody("application/json".toMediaType())).build()
            }.build())
    @Test fun oldClientDoesNotDispatchAfterAccountChange() {
        org.junit.Assume.assumeTrue(com.woundmeasurement.app.BuildConfig.MMH_VALIDATION)
        val calls=AtomicInteger();val c=client(before={calls.incrementAndGet()})
        MedicalSessionEpoch.invalidate()
        try {c.validateMedicalSession();fail()}catch (_:IOException){}
        assertEquals(0,calls.get());assertFalse(c.hasSessionToken);assertNull(c.identity)
    }
    @Test fun accountChangeDuringResponseRejectsIt() {
        org.junit.Assume.assumeTrue(com.woundmeasurement.app.BuildConfig.MMH_VALIDATION)
        val c=client(before={MedicalSessionEpoch.invalidate()})
        try {c.validateMedicalSession();fail()}catch (_:IOException){}
        assertFalse(c.hasSessionToken);assertNull(c.identity)
    }
    @Test fun successfulReceiptUpdatesIdentity() {val c=client();assertEquals("dr01",c.validateMedicalSession().user);assertNotNull(c.identity)}
    @Test fun unauthorizedClearsTokenAndIdentity() {val c=client(code={401});try {c.validateMedicalSession();fail()}catch (_:IOException){};assertFalse(c.hasSessionToken);assertNull(c.identity)}
    @Test fun temporaryFailureKeepsTokenButClearsIdentity() {var code=200;val c=client(code={code});c.validateMedicalSession();code=503;try {c.validateMedicalSession();fail()}catch (_:IOException){};assertTrue(c.hasSessionToken);assertNull(c.identity)}
    @Test fun malformedSuccessClearsToken() {val c=client(text={"{}"});try {c.validateMedicalSession();fail()}catch (_:IOException){};assertFalse(c.hasSessionToken)}
    @Test fun legacyEndpointCannotAuthorizeSession() {val c=client(code={404});try {c.validateMedicalSession();fail()}catch (e:IOException){assertTrue(e.message!!.contains("尚未支援"))};assertNull(c.identity)}
}
