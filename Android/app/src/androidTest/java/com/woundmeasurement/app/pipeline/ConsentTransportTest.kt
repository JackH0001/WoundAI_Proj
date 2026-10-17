package com.woundmeasurement.app.pipeline

import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Response
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.*
import org.junit.Test

/** Synthetic transport only: real receipt parsing without transmitting personal data. */
class ConsentTransportTest {
    private fun client(status:Int,body:String)=BackendClient("https://synthetic.invalid","",OkHttpClient.Builder()
        .addNetworkInterceptor { error("No live network in consent tests") }
        .addInterceptor { chain -> Response.Builder().request(chain.request()).protocol(Protocol.HTTP_1_1)
            .code(status).message("Synthetic").header("Retry-After","300").body(body.toResponseBody()).build() }.build())
    @Test fun onlyMatchingWithdrawnReceiptCompletes() {
        assertTrue(client(200,"""{"status":"withdrawn","code":"synthetic"}""").withdrawConsent("synthetic"))
        for(body in listOf("{}","<html>login</html>","""{"status":"restored","code":"synthetic"}""",
            """{"status":"withdrawn","code":"other"}""")) assertFalse(client(200,body).withdrawConsent("synthetic"))
        assertFalse(client(202,"""{"status":"withdrawn","code":"synthetic"}""").withdrawConsent("synthetic"))
    }
    @Test fun restoreRequiresMatchingRestoredReceipt() {
        assertTrue(client(200,"""{"status":"restored","code":"synthetic"}""").restoreConsent("synthetic"))
        assertFalse(client(200,"""{"status":"withdrawn","code":"synthetic"}""").restoreConsent("synthetic"))
    }
    @Test fun busyResponsePreservesDeadlineForBothDirections() {
        for(status in listOf(429,503)) for(restore in listOf(false,true)) {
            val before=System.currentTimeMillis()
            try {
                val c=client(status,"{}");if(restore)c.restoreConsent("synthetic") else c.withdrawConsent("synthetic")
                fail("Busy response cannot acknowledge consent")
            } catch(wait:ResearchCapacityException) {assertTrue(wait.notBefore>=before+300_000)}
        }
    }
}
