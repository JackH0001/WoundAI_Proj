package com.woundmeasurement.app.pipeline

import com.woundmeasurement.app.BuildConfig
import com.woundmeasurement.app.institution.InstitutionConfig
import com.woundmeasurement.app.institution.InstitutionRegistry
import com.woundmeasurement.app.institution.InstitutionSession
import java.io.IOException
import java.net.InetAddress
import java.net.ServerSocket
import java.net.SocketTimeoutException
import okio.ByteString.Companion.encodeUtf8
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Test

/** The public MMH constructor must reject requests before opening any socket. */
class InstitutionTransportBoundaryTest {
    private fun assertBlocked(expected: String) {
        ServerSocket(0, 1, InetAddress.getByName("127.0.0.1")).use { server ->
            server.soTimeout = 200
            val client = BackendClient("http://127.0.0.1:${server.localPort}", "synthetic-backend-token")
            // A valid paired-mask payload must reach the transport boundary;
            // missing masks are rejected locally before any HTTP interceptor.
            val mask = ByteArray(64) { 1 }; val tissue = ByteArray(64) { 1 }
            val raster = EditRaster(mask,tissue,mask.copyOf(),0f,0f,8,8,.8f,null,
                maskPx=64,canvasW=10,canvasH=10,autoBaseline=tissue.copyOf())
            val tissuePng = requireNotNull(TissueMaskCodec.encode(tissue,mask,8,8))
            val error = assertThrows(IOException::class.java) {
                client.submitAnnotation(code = "WD-synthetic",
                    gtPolygon = listOf(listOf(1,1), listOf(8,1), listOf(1,8)),
                    exudate = 0, imageId = "aaaaaaaaaaaaaaaa", imageW = 10, imageH = 10,
                    consentTrain = true, doctorVerified = true, source = "phantom",
                    tissueMaskPng = tissuePng, tissueRaster = raster)
            }
            assertTrue(error.message.orEmpty().contains(expected))
            assertThrows(SocketTimeoutException::class.java) { server.accept().use { } }
        }
    }

    @Test fun publicMmhClientRequiresGoogleSessionBeforeNetwork() {
        assumeTrue("MMH-only production gateway boundary", BuildConfig.MMH_VALIDATION)
        InstitutionRegistry.replace(null)
        try { assertBlocked("登入 Google") } finally { InstitutionRegistry.replace(null) }
    }

    @Test fun verifiedSessionCannotSendTokensToLoopback() {
        assumeTrue("MMH-only production gateway boundary", BuildConfig.MMH_VALIDATION)
        val nonce = "synthetic-local-nonce"
        val claims = JSONObject().put("aud", InstitutionConfig.AUDIENCE)
            .put("iss", "https://accounts.google.com").put("sub", "synthetic-subject")
            .put("nonce", nonce).put("exp", System.currentTimeMillis()/1000 + 3600)
        // Not a Google-signed token; parsed only into the local routing fixture.
        val session = InstitutionSession.parse(
            "e30.${claims.toString().encodeUtf8().base64Url()}.synthetic-signature", nonce)
        session.markVerified()
        InstitutionRegistry.replace(session)
        try { assertBlocked("位址與 MMH 私有入口不符") }
        finally { InstitutionRegistry.replace(null) }
    }
}
