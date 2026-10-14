package com.woundmeasurement.app.institution

import org.junit.Assert.*
import org.junit.Test
import okhttp3.Request
import okio.ByteString.Companion.encodeUtf8

class InstitutionSessionTest {
    private val claims="""{"sub":"synthetic-user","aud":"${InstitutionConfig.AUDIENCE}","iss":"https://accounts.google.com","exp":2000,"nonce":"test-nonce"}"""
    private fun session(c:String=claims)=InstitutionSession.parse("e30.${c.encodeUtf8().base64Url()}.signature","test-nonce") { 1000000L }
    private fun request(url:String=InstitutionConfig.ORIGIN+"/api/health")=Request.Builder().url(url).build()
    private fun reject(f:()->Unit) { try { f(); fail("must reject") } catch (_:java.io.IOException) {} }
    @Test fun preservesAppAuthAndRemovesAlternateIdentities() {
        val s=session(); s.markVerified()
        val r=s.authorize(request().newBuilder().header("Authorization","Bearer app-test")
            .header("Cookie","test-cookie").header("X-Serverless-Authorization","other").build())
        assertEquals("Bearer app-test",r.header("Authorization")); assertNull(r.header("Cookie"))
        assertNull(r.header("X-Serverless-Authorization")); assertTrue(r.header("Proxy-Authorization")!!.startsWith("Bearer "))
    }
    @Test fun blocksPasswordBeforeHealthVerified() {
        val s=session(); reject { s.authorize(request(InstitutionConfig.ORIGIN+"/api/auth/login")) }
    }
    @Test fun allowsOnlyHealthBeforeProbe() { assertNotNull(session().authorize(request())) }
    @Test fun rejectsWrongAudience() { reject { session(claims.replace(InstitutionConfig.AUDIENCE,"other")) } }
    @Test fun rejectsWrongIssuer() { reject { session(claims.replace("https://accounts.google.com","https://evil.example")) } }
    @Test fun rejectsNonceMismatch() { reject { session(claims.replace("test-nonce","other")) } }
    @Test fun rejectsExpiredToken() { reject { session(claims.replace("2000","1029")) } }
    @Test fun rejectsStringExpiry() { reject { session(claims.replace(":2000",":\"2000\"")) } }
    @Test fun rejectsMalformedJwt() { reject { InstitutionSession.parse("broken","test-nonce") } }
    @Test fun rejectsCandidateEvenAfterGoogleVerified() {
        val s=session(); s.markVerified()
        reject { s.authorize(request("https://candidate---woundai-backend-mmhps20261007-z4kgfkob4a-de.a.run.app/api/auth/login")) }
    }
    @Test fun rejectsCrossOrigin() { reject { session().authorize(request("https://evil.example/api/health")) } }
    @Test fun rejectsHttp() { reject { session().authorize(request(InstitutionConfig.ORIGIN.replace("https:","http:")+"/api/health")) } }
    @Test fun rejectsWrongPort() { reject { session().authorize(request(InstitutionConfig.ORIGIN+":8443/api/health")) } }
    @Test fun rejectsNonApi() { reject { session().authorize(request(InstitutionConfig.ORIGIN+"/console")) } }
    @Test fun rejectsEncodedPath() { reject { session().authorize(request(InstitutionConfig.ORIGIN+"/api/%2fhealth")) } }
    @Test fun invalidatedClientNeverRevives() { val s=session(); s.markVerified(); s.invalidate(); assertFalse(s.ready()); reject { s.authorize(request()) }; reject { s.markVerified() } }
    @Test fun rejectsHealthHtml() { reject { InstitutionSession.verifyHealth(200,"<html>login</html>") } }
    @Test fun rejectsHealthWrongService() { reject { InstitutionSession.verifyHealth(200,"""{"build":{"service":"other","revision":"r"}}""") } }
    @Test fun requiresHealth200() { reject { InstitutionSession.verifyHealth(302,"{}") } }
    @Test fun acceptsBoundHealth() { InstitutionSession.verifyHealth(200,"""{"build":{"service":"${InstitutionConfig.SERVICE}","revision":"r"}}""") }
    @Test fun replacingSessionInvalidatesOldClient() { val a=session(); InstitutionRegistry.replace(a); InstitutionRegistry.replace(session()); reject { a.authorize(request()) }; InstitutionRegistry.replace(null) }
}
