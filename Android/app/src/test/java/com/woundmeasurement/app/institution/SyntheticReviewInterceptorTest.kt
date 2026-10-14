package com.woundmeasurement.app.institution

import okhttp3.*
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.*
import org.junit.Test
import java.io.IOException

class SyntheticReviewInterceptorTest {
    private fun health(service: String = SyntheticReviewInterceptor.SERVICE, env: String = "synthetic-review") =
        """{"deployment_environment":"$env","build":{"service":"$service","revision":"fixture-r1"}}"""
    private fun response(request: Request, code: Int, body: String) = Response.Builder().request(request)
        .protocol(Protocol.HTTP_1_1).code(code).message("Fixture").body(body.toResponseBody()).build()
    private fun client(current: () -> Boolean = { true }, handler: (Request) -> Response) = OkHttpClient.Builder()
        .followRedirects(false).followSslRedirects(false).retryOnConnectionFailure(false)
        .addInterceptor(SyntheticReviewInterceptor(current)).addInterceptor { handler(it.request()) }.build()
    private fun login() = Request.Builder().url(SyntheticReviewInterceptor.ORIGIN + "/api/auth/login")
        .header("Authorization", "Bearer synthetic-app-token").header("Cookie", "strip")
        .header("Proxy-Authorization", "strip").header("X-Serverless-Authorization", "strip").build()
    private fun rejected(f: () -> Unit) { try { f(); fail("Expected rejection") } catch (_: IOException) {} }
    @Test fun healthBeforeCredentialsAndGoogleHeadersRemoved() {
        val requests = mutableListOf<Request>()
        val c = client { r -> requests.add(r); response(r,200,if (r.url.encodedPath=="/api/health") health() else "{}") }
        c.newCall(login()).execute().close()
        assertEquals(listOf("/api/health","/api/auth/login"), requests.map { it.url.encodedPath })
        assertNull(requests[0].header("Authorization"))
        assertEquals("Bearer synthetic-app-token",requests[1].header("Authorization"))
        for (r in requests) for (h in listOf("Cookie","Proxy-Authorization","X-Serverless-Authorization")) assertNull(r.header(h))
        c.newCall(login()).execute().close(); assertEquals(3,requests.size)
    }
    @Test fun foreignServiceOrEnvironmentNeverReceivesLogin() {
        for (body in listOf(health(service="woundai-backend-mmhps20261007"),health(env="medical"),"{}")) {
            var calls=0
            val c=client { r -> calls++; assertEquals("/api/health",r.url.encodedPath); response(r,200,body) }
            rejected { c.newCall(login()).execute().close() }; assertEquals(1,calls)
        }
    }
    @Test fun untrustedDestinationRejectedWithoutNetwork() {
        val c=client { error("No HTTP permitted") }
        for (u in listOf(InstitutionConfig.ORIGIN+"/api/health", SyntheticReviewInterceptor.ORIGIN+".invalid/api/health",
            SyntheticReviewInterceptor.ORIGIN+":8443/api/health",SyntheticReviewInterceptor.ORIGIN+"/console",
            SyntheticReviewInterceptor.ORIGIN+"/api/%2Fhealth",SyntheticReviewInterceptor.ORIGIN+"/api/health#fragment")) {
            rejected { c.newCall(Request.Builder().url(u).build()).execute().close() }
        }
    }
    @Test fun failedProbeHasNoAutomaticRetry() {
        for (status in listOf(302,401,503)) {
            var calls=0
            val c=client { r -> calls++; response(r,status,health()) }
            rejected { c.newCall(login()).execute().close() }; assertEquals(1,calls)
        }
    }
    @Test fun closingDuringHealthStopsBeforeAppRequest() {
        var active=true;var calls=0
        val c=client({active}) { r -> calls++;active=false;response(r,200,health()) }
        rejected { c.newCall(login()).execute().close() }; assertEquals(1,calls)
        rejected { c.newCall(login()).execute().close() }; assertEquals(1,calls)
    }
    @Test fun closingDuringAppRequestRejectsLateResponse() {
        var active=true;var calls=0
        val c=client({active}) { r -> calls++; if(calls==2)active=false;response(r,200,health()) }
        rejected { c.newCall(login()).execute().close() };assertEquals(2,calls)
    }
    @Test fun appRedirectIsRejected() {
        var calls=0
        val c=client { r -> calls++; response(r,if(calls==1)200 else 302,health()) }
        rejected { c.newCall(login()).execute().close() };assertEquals(2,calls)
    }
}
