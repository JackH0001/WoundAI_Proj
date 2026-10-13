package com.woundmeasurement.app.institution

import com.google.gson.JsonParser
import okhttp3.Interceptor
import okhttp3.Request
import okhttp3.Response
import okio.ByteString.Companion.decodeBase64
import java.io.IOException
import java.util.concurrent.atomic.AtomicBoolean

object InstitutionConfig {
    const val ORIGIN = "https://candidate---woundai-backend-mmhps20261007-z4kgfkob4a-de.a.run.app"
    const val AUDIENCE = "421209514056-c62hg9vv5p7g2l96n2efig269kcsads6.apps.googleusercontent.com"
    const val SERVICE = "woundai-backend-mmhps20261007"
}

/** Local routing checks only. Google/IAP verifies signatures and IAM; tokens never reach disk/logs. */
class InstitutionSession private constructor(private val token: String, val subject: String,
    private val expires: Double, private val clock: () -> Long) {
    private val active = AtomicBoolean(true)
    private val verified = AtomicBoolean(false)
    fun invalidate() { active.set(false); verified.set(false) }
    fun ready() = active.get() && verified.get() && expires > clock() / 1000.0 + 30
    fun markVerified() { if (!active.get()) throw IOException("請重新登入 Google"); verified.set(true) }
    fun authorize(request: Request): Request {
        val u = request.url
        if (u.scheme != "https" || u.host != InstitutionConfig.ORIGIN.removePrefix("https://") ||
            u.port != 443 || u.username.isNotEmpty() || u.password.isNotEmpty() || u.fragment != null ||
            !u.encodedPath.startsWith("/api/") || u.encodedPath.contains('%'))
            throw IOException("位址與 MMH 私有入口不符，未傳送憑證")
        if (!active.get() || expires <= clock() / 1000.0 + 30)
            throw IOException("Google 登入已到期，請至設定重新登入；本機紀錄保留")
        if (!verified.get() && !(request.method == "GET" && u.encodedPath == "/api/health"))
            throw IOException("請先完成 Google 與機構入口驗證")
        return request.newBuilder().removeHeader("Cookie").removeHeader("X-Serverless-Authorization")
            .header("Proxy-Authorization", "Bearer $token").build()
    }
    companion object {
        fun parse(raw: String, nonce: String, clock: () -> Long = System::currentTimeMillis): InstitutionSession {
            try {
                require(raw.length in 1..32768 && raw.all { it.code in 33..126 })
                val p = raw.split('.'); require(p.size == 3 && p.all { it.isNotEmpty() })
                val j = JsonParser.parseString(requireNotNull(p[1].decodeBase64()).utf8()).asJsonObject
                fun string(k: String): String { val v=j.get(k).asJsonPrimitive; require(v.isString); return v.asString }
                require(string("aud") == InstitutionConfig.AUDIENCE)
                require(string("iss") in listOf("accounts.google.com", "https://accounts.google.com"))
                require(nonce.isNotBlank() && string("nonce") == nonce)
                val sub=string("sub"); require(sub.isNotBlank())
                val exp=j.get("exp").asJsonPrimitive; require(exp.isNumber)
                val expiry=exp.asDouble; require(expiry.isFinite() && expiry > clock()/1000.0+30)
                return InstitutionSession(raw, sub, expiry, clock)
            } catch (_: Exception) { throw IOException("Google 登入回覆無法確認，請重新登入") }
        }
        fun verifyHealth(code: Int, body: String) {
            if (code != 200) throw IOException("Google 已登入，機構入口未通過（HTTP $code）")
            try {
                val build=JsonParser.parseString(body).asJsonObject.getAsJsonObject("build")
                require(build.get("service").asJsonPrimitive.isString && build.get("service").asString == InstitutionConfig.SERVICE)
                require(build.get("revision").asJsonPrimitive.isString && build.get("revision").asString.isNotBlank())
            } catch (_: Exception) { throw IOException("機構入口回覆不是預期 MMH 服務") }
        }
    }
}

object InstitutionRegistry {
    @Volatile private var session: InstitutionSession? = null
    @Synchronized fun replace(next: InstitutionSession?) { com.woundmeasurement.app.pipeline.MedicalSessionEpoch.invalidate(); session?.invalidate(); session=next }
    fun snapshot() = session
}

/** Bound to one login session, never silently switches identity on a pending client. */
class InstitutionInterceptor(private val session: InstitutionSession?) : Interceptor {
    override fun intercept(chain: Interceptor.Chain): Response {
        val s=session ?: throw IOException("請先至設定登入 Google 機構入口")
        val response=chain.proceed(s.authorize(chain.request()))
        if (response.code in 300..399) { response.close(); throw IOException("機構入口重新導向已拒絕") }
        try { s.authorize(chain.request()) } catch (e: IOException) { response.close(); throw e }
        return response
    }
}

/** Remembering identity never constitutes authorization; IAP is checked before installation. */
internal object InstitutionIdentityPolicy {
    fun mayRestore(previous: String?, next: String) = !previous.isNullOrBlank() && next == previous
    fun preserveAppCredentials(previous: String?, next: String) = mayRestore(previous, next)
}
