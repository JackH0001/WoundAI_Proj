package com.woundmeasurement.app.pipeline

import org.junit.Assert.*
import org.junit.Test
import org.json.JSONObject
import java.net.ServerSocket
import java.net.InetAddress
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import okhttp3.OkHttpClient
import com.woundmeasurement.app.BuildConfig

/** Exercise the actual OkHttp + Android JSON call path, entirely on loopback. */
class AnnotationTransportTest {
    private val imageId = "aaaaaaaaaaaaaaaa"
    private val receipt = """{"status":"enqueued","code":"WD-synthetic","image_id":"aaaaaaaaaaaaaaaa","annotation_receipt_id":"0123456789abcdef"}"""
    private fun upload(response: String, status: Int = 200, paired: Boolean = true, echoMasks: Boolean = true): Pair<Boolean,String> {
        ServerSocket(0, 1, InetAddress.getByName("127.0.0.1")).use { server ->
            server.soTimeout = 5000
            val executor = Executors.newSingleThreadExecutor()
            try {
                val handled = executor.submit<Unit> {
                    server.accept().use { socket ->
                        socket.soTimeout = 5000
                        val reader = socket.getInputStream().bufferedReader()
                        assertEquals("POST /api/v1/annotation HTTP/1.1", reader.readLine())
                        var length = 0
                        while (true) {
                            val line = reader.readLine() ?: error("incomplete HTTP headers")
                            if (line.isEmpty()) break
                            if (line.startsWith("Content-Length:", true)) length = line.substringAfter(':').trim().toInt()
                        }
                        assertTrue(length in 1..100000)
                        val chars = CharArray(length); var n = 0
                        while (n < length) { val got = reader.read(chars, n, length - n); check(got > 0); n += got }
                        val body = JSONObject(String(chars))
                        assertEquals(imageId, body.getString("image_id"))
                        assertEquals("WD-synthetic", body.getString("code"))
                        assertTrue(body.getBoolean("doctor_verified"))
                        val output = if (paired && echoMasks && response.startsWith("{")) {
                            assertEquals("woundai.annotation-masks/1",body.getString("mask_contract"))
                            // Synthetic request only, for independent backend codec validation.
                            val cache = androidx.test.platform.app.InstrumentationRegistry.getInstrumentation().targetContext.cacheDir
                            java.io.File(cache,"native-mask-contract-android.json").writeText(body.toString())
                            fun hash(name: String) = java.security.MessageDigest.getInstance("SHA-256")
                                .digest(android.util.Base64.decode(body.getString(name),android.util.Base64.DEFAULT))
                                .joinToString("") { "%02x".format(it.toInt() and 0xff) }
                            assertEquals(hash("wound_mask_png"),body.getString("wound_mask_sha256"))
                            JSONObject(response).put("mask_contract",body.getString("mask_contract"))
                                .put("wound_mask_sha256",hash("wound_mask_png"))
                                .put("tissue_mask_sha256",hash("tissue_mask_png")).toString()
                        } else response
                        val bytes = output.toByteArray(Charsets.UTF_8)
                        val header = "HTTP/1.1 $status Test\r\nContent-Type: application/json\r\nContent-Length: ${bytes.size}\r\nConnection: close\r\n\r\n"
                        socket.getOutputStream().apply { write(header.toByteArray()); write(bytes); flush() }
                    }
                }
                // MMH deliberately rejects loopback in the production transport. Inject
                // a local-only transport for these receipt tests; separate tests
                // in InstitutionTransportBoundaryTest exercise the guarded public
                // constructor without relaxing it.
                val client = if (BuildConfig.MMH_VALIDATION) {
                    BackendClient("http://127.0.0.1:${server.localPort}", "synthetic-test-token",
                        OkHttpClient.Builder().callTimeout(5, TimeUnit.SECONDS).build())
                } else BackendClient("http://127.0.0.1:${server.localPort}", "synthetic-test-token")
                val mask = ByteArray(64) { if (it == 27) 0 else 1 }
                val tissue = ByteArray(64) { if (it == 27 || it == 9) 0 else 3 }
                val raster = if (paired) EditRaster(mask,tissue,mask.copyOf(),0f,0f,8,8,.8f,null,
                    maskPx=63,canvasW=10,canvasH=10,autoBaseline=tissue.copyOf()) else null
                val tissuePng = if (paired) TissueMaskCodec.encode(tissue,mask,8,8) else null
                val result = client.submitAnnotation(
                    code = "WD-synthetic", gtPolygon = listOf(listOf(1,1),listOf(8,1),listOf(1,8)),
                    exudate = 0, imageId = imageId, imageW = 10, imageH = 10,
                    consentTrain = true, doctorVerified = true, source = "phantom",
                    tissueMaskPng=tissuePng, tissueRaster=raster)
                handled.get(6, TimeUnit.SECONDS)
                return result
            } finally { executor.shutdownNow() }
        }
    }
    @Test fun actualClientAcceptsBoundSavedReceipt() { assertTrue(upload(receipt).first) }
    @Test fun actualClientRecognizesDuplicate() {
        val result=upload(receipt.replace("enqueued","duplicate_skipped"))
        assertTrue(result.first); assertTrue(result.second.startsWith("duplicate:"))
    }
    @Test fun actualClientRejectsHttp200LoginPage() { assertFalse(upload("<html>Login required</html>").first) }
    @Test fun actualClientRejectsWrongImage() { assertFalse(upload(receipt.replace(imageId,"bbbbbbbbbbbbbbbb")).first) }
    @Test fun actualClientRejectsLegacyUnboundReceipt() { assertFalse(upload("""{"status":"enqueued","code":"WD-synthetic"}""").first) }
    @Test fun actualClientRejectsServerFailureWithSuccessBody() { assertFalse(upload(receipt,503).first) }
    @Test fun pairedTransportRequiresBothStoredMasks() { assertTrue(upload(receipt,paired=true).first) }
    @Test fun pairedTransportRejectsOldBackendSuccess() { assertFalse(upload(receipt,paired=true,echoMasks=false).first) }
    @Test fun pairedTransportDuplicateUsesSameHashes() { assertTrue(upload(receipt.replace("enqueued","duplicate_skipped"),paired=true).first) }
}
