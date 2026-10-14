package com.woundmeasurement.app.institution

import com.google.gson.JsonParser
import okhttp3.Interceptor
import okhttp3.Request
import okhttp3.Response
import java.io.IOException

/** Fixed demo service. Never obtains a Google token or retries a write. */
class SyntheticReviewInterceptor(private val current: () -> Boolean = { true }) : Interceptor {
    companion object {
        const val ORIGIN = "https://woundai-mmh-review-demo-421209514056.asia-east1.run.app"
        const val SERVICE = "woundai-mmh-review-demo"
        fun validate(request: Request) {
            val u = request.url
            if (u.scheme != "https" || u.host != ORIGIN.removePrefix("https://") || u.port != 443 ||
                u.username.isNotEmpty() || u.password.isNotEmpty() || u.fragment != null ||
                !u.encodedPath.startsWith("/api/") || '%' in u.encodedPath)
                throw IOException("位址與合成示範環境不符，未傳送憑證")
        }
        fun verifyHealth(code: Int, body: String) {
            try {
                require(code == 200)
                val j = JsonParser.parseString(body).asJsonObject
                val build = j.getAsJsonObject("build")
                require(j.get("deployment_environment").asJsonPrimitive.let { it.isString && it.asString == "synthetic-review" })
                require(build.get("service").asJsonPrimitive.let { it.isString && it.asString == SERVICE })
                require(build.get("revision").asJsonPrimitive.let { it.isString && it.asString.isNotBlank() })
            } catch (_: Exception) { throw IOException("示範環境尚未確認，未傳送帳號密碼") }
        }
    }
    @Volatile private var verified = false
    private fun checkCurrent() { if (!current()) throw IOException("原環境已關閉，請在目前環境重新登入") }
    private fun clean(request: Request) = request.newBuilder()
        .removeHeader("Proxy-Authorization").removeHeader("X-Serverless-Authorization").removeHeader("Cookie").build()
    override fun intercept(chain: Interceptor.Chain): Response {
        val request = chain.request(); validate(request); checkCurrent()
        synchronized(this) {
            if (!verified) {
                val probe = Request.Builder().url("$ORIGIN/api/health").get().build()
                chain.proceed(probe).use { response ->
                    checkCurrent()
                    if (response.request.url != probe.url) throw IOException("示範入口重新導向已拒絕")
                    verifyHealth(response.code, response.body?.string() ?: "")
                }
                verified = true
            }
        }
        checkCurrent()
        val response = chain.proceed(clean(request))
        try {
            checkCurrent()
            if (response.code in 300..399 || response.request.url != request.url)
                throw IOException("示範入口重新導向已拒絕")
        } catch (e: Exception) { response.close(); throw e }
        return response
    }
}
