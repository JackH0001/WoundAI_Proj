package com.woundmeasurement.app.pipeline

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject

/**
 * 後端 HTTP 客戶端骨架（對接 Backend/Flask app.py）。回應 schema 見 docs/api_contract_classify；
 * 契約測試 engineering/phase2/test_api_contract.py。需 JWT。輔助、非診斷。
 *
 * 端點：
 *  POST /api/v1/classify         image[, cm_per_pixel]  → 五階段(面積/組織/PUSH)
 *  POST /api/v1/segment/escalate image                  → 雲端 A∪U 遮罩(b64)  ※雙軌難例
 *  POST /api/v1/annotation       gt/classmap/exudate…   → 飛輪(需去識別+同意)
 *  POST /api/v1/consent/withdraw {case/code}            → 撤回→下架排除訓練
 */
data class ClassifyResult(
    val areaCm2: Double?, val tissueFrac: Map<String, Double>,
    val pushPartial: Int?, val pushFull: Int?, val confidence: Double, val route: String
)

class BackendClient(private val baseUrl: String, jwt: String = "") {
    private val http = OkHttpClient()
    @Volatile private var jwt: String = jwt

    /** 登入取得 JWT(後端 /api/auth/login)。成功回 true 並存 token 供後續呼叫。同步阻塞,請於 IO 執行。 */
    fun login(username: String, password: String): Boolean {
        val body = JSONObject(mapOf("username" to username, "password" to password)).toString()
            .toRequestBody("application/json".toMediaType())
        val req = Request.Builder().url("$baseUrl/api/auth/login").post(body).build()
        http.newCall(req).execute().use { resp ->
            if (!resp.isSuccessful) return false
            val tok = JSONObject(resp.body!!.string()).optString("access_token", "")
            if (tok.isEmpty()) return false
            jwt = tok
            return true
        }
    }

    /** 呼叫 /api/v1/classify;回傳解析後結果(對齊後端契約)。 */
    fun classify(jpeg: ByteArray, cmPerPixel: Double? = null): ClassifyResult {
        val bodyBuilder = MultipartBody.Builder().setType(MultipartBody.FORM)
            .addFormDataPart("image", "wound.jpg", jpeg.toRequestBody("image/jpeg".toMediaType()))
        if (cmPerPixel != null) bodyBuilder.addFormDataPart("cm_per_pixel", cmPerPixel.toString())
        val req = Request.Builder().url("$baseUrl/api/v1/classify")
            .header("Authorization", "Bearer $jwt").post(bodyBuilder.build()).build()
        http.newCall(req).execute().use { resp ->
            if (!resp.isSuccessful) throw RuntimeException("classify HTTP ${resp.code}")
            val j = JSONObject(resp.body!!.string())
            val s3 = j.getJSONObject("stage3_calibrate")
            val s4 = j.getJSONObject("stage4_tissue").getJSONObject("tissue_frac")
            val s5 = j.getJSONObject("stage5_severity")
            val s2 = j.getJSONObject("stage2_segment")
            val tissue = listOf("necrosis","slough","granulation","epithelial","other")
                .associateWith { if (s4.isNull(it)) 0.0 else s4.getDouble(it) }
            return ClassifyResult(
                areaCm2 = if (s3.isNull("area_cm2")) null else s3.getDouble("area_cm2"),
                tissueFrac = tissue,
                pushPartial = if (s5.isNull("total_partial_img")) null else s5.getInt("total_partial_img"),
                pushFull = if (s5.isNull("total_full")) null else s5.getInt("total_full"),
                confidence = if (s2.isNull("confidence")) 0.0 else s2.getDouble("confidence"),
                route = "cloud"
            )
        }
    }

    /** 撤回同意 → 後端下架、排除訓練、稽核。 */
    fun withdrawConsent(code: String): Boolean {
        val body = JSONObject(mapOf("code" to code)).toString()
            .toRequestBody("application/json".toMediaType())
        val req = Request.Builder().url("$baseUrl/api/v1/consent/withdraw")
            .header("Authorization", "Bearer $jwt").post(body).build()
        http.newCall(req).execute().use { return it.isSuccessful }
    }
}
