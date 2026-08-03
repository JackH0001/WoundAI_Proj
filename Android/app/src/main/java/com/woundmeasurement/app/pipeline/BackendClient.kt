package com.woundmeasurement.app.pipeline

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit

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
    val pushPartial: Int?, val pushFull: Int?, val confidence: Double, val route: String,
    val escalated: Boolean = false,
    val woundPolygon: List<List<Int>> = emptyList(),  // 傷口輪廓(供醫師修邊/飛輪標註)
    val mmPerPx: Double? = null,                      // ArUco 尺度(mm/影像px):修邊面積=像素數×(mm/px)²
    // 飛輪資料鏈:image_id=後端已存影像的內容雜湊;imageW/H=polygon 與修邊 GT 的座標空間。
    // 送標註時必須帶回,否則後端收到的是無影像、無尺寸的孤兒 GT(不可訓練)。
    val imageId: String? = null,
    val imageW: Int = 0,
    val imageH: Int = 0,
    val segModel: String? = null,
    /** true = AI 沒抓到但色彩分割抓得到 → 幾乎確定把印刷模擬圖誤選成了臨床/範例。 */
    val phantomHint: Boolean = false,
    /** 模擬圖模式實際用了哪一段:strict / gray_world_wb(偏色時已自動白平衡重試)。 */
    val phantomPass: String? = null
)

class BackendClient(private val baseUrl: String, jwt: String = "") {
    // escalate 的 classify 需跑 student+A∪U 兩模型,首次冷啟>10s;拉長逾時避免 timeout
    private val http = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(90, TimeUnit.SECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        .callTimeout(120, TimeUnit.SECONDS)
        .build()
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
    /**
     * @param seg null/"auto"=AI 分割(student→難例 A∪U);"color"=印刷模擬圖走決定性 HSV 色彩分割,
     *   **完全不碰模型**。印刷色塊是分布外樣本,模型實測回空遮罩;而驗證量測鏈也不該拿 AI 當量尺。
     */
    fun classify(jpeg: ByteArray, cmPerPixel: Double? = null, seg: String? = null): ClassifyResult {
        val bodyBuilder = MultipartBody.Builder().setType(MultipartBody.FORM)
            .addFormDataPart("image", "wound.jpg", jpeg.toRequestBody("image/jpeg".toMediaType()))
        if (cmPerPixel != null) bodyBuilder.addFormDataPart("cm_per_pixel", cmPerPixel.toString())
        if (seg != null) bodyBuilder.addFormDataPart("seg", seg)
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
            val poly = ArrayList<List<Int>>()
            if (!s2.isNull("wound_polygon")) {
                val pa = s2.getJSONArray("wound_polygon")
                for (i in 0 until pa.length()) {
                    val pt = pa.getJSONArray(i); poly.add(listOf(pt.getInt(0), pt.getInt(1)))
                }
            }
            return ClassifyResult(
                areaCm2 = if (s3.isNull("area_cm2")) null else s3.getDouble("area_cm2"),
                tissueFrac = tissue,
                pushPartial = if (s5.isNull("total_partial_img")) null else s5.getInt("total_partial_img"),
                pushFull = if (s5.isNull("total_full")) null else s5.getInt("total_full"),
                confidence = if (s2.isNull("confidence")) 0.0 else s2.getDouble("confidence"),
                // 後端雙軌路由:student(端上主力) 或 cloud_escalated(AU)(難例自動上雲集成)
                route = if (s2.isNull("route")) "cloud" else s2.getString("route"),
                escalated = !s2.isNull("escalated") && s2.getBoolean("escalated"),
                woundPolygon = poly,
                mmPerPx = if (s3.isNull("mm_per_px")) null else s3.getDouble("mm_per_px"),
                imageId = if (j.isNull("image_id")) null else j.getString("image_id"),
                imageW = j.optInt("image_w", 0),
                imageH = j.optInt("image_h", 0),
                segModel = if (s2.isNull("model")) null else s2.getString("model"),
                phantomHint = j.optBoolean("phantom_hint", false),
                // isNull() 已涵蓋「鍵不存在」與「值為 JSON null」兩種情形,故此處可直接 getString。
                // 不用 optString(name, null):org.json 把 fallback 宣告為非 null,傳 null 會讓
                // Kotlin 推導出 Nothing? 而發出型別警告(執行期雖可行,但那是靠平台型別的漏洞)。
                phantomPass = if (j.isNull("phantom_pass")) null else j.getString("phantom_pass")
            )
        }
    }

    /**
     * 送出醫師驗證標註 → 飛輪 retrain 佇列(POST /api/v1/annotation)。回 (成功, 訊息)。
     * 後端守門:code 需 WD-*、gt_polygon 非空、doctor_verified/deidentified/consent_train 皆 true。
     * 醫師修邊後把修正 polygon 帶入 gtPolygon;correctionIou 記錄修正幅度。同步阻塞,請於 IO 執行。
     */
    fun submitAnnotation(
        code: String, gtPolygon: List<List<Int>>, exudate: Int?,
        imageId: String?, imageW: Int, imageH: Int,
        mmPerPx: Double? = null, route: String? = null, segModel: String? = null,
        correctionIou: Double? = null, careNote: String? = null,
        source: String? = null,  // clinical(預設)/sample/phantom/external;範例集不可灌進臨床樣本數
        /**
         * ②訓練同意的**真值**,來自本機 `ConsentEntity.trainEffective`。
         * ⚠ 這裡曾經硬編碼 true——等於每筆送出都謊稱已取得訓練同意,而實際上沒人勾過。
         * 呼叫端沒有有效同意就不該呼叫本函式;真的呼叫了,後端也會以 400 擋下。
         */
        consentTrain: Boolean
    ): Pair<Boolean, String> {
        // 沒有 image_id/尺寸就送出 = 產生孤兒 GT(後端會 400)。提早在端上擋下並給明確訊息。
        if (imageId.isNullOrEmpty() || imageW <= 0 || imageH <= 0) {
            return Pair(false, "缺影像綁定(image_id/尺寸);請重新以後端模式量測一次再送出")
        }
        val poly = JSONArray()
        for (p in gtPolygon) {
            val pt = JSONArray(); pt.put(p.getOrElse(0) { 0 }); pt.put(p.getOrElse(1) { 0 }); poly.put(pt)
        }
        val obj = JSONObject()
            .put("code", code)
            .put("gt_polygon", poly)
            .put("exudate", if (exudate != null) exudate else JSONObject.NULL)
            .put("doctor_verified", true)
            .put("deidentified", true)
            .put("consent_train", consentTrain)   // 由本機同意紀錄決定,不再硬編碼
            .put("image_id", imageId)
            .put("image_w", imageW)
            .put("image_h", imageH)
        if (mmPerPx != null) obj.put("mm_per_px", mmPerPx)
        if (route != null) obj.put("route", route)
        if (segModel != null) obj.put("seg_model", segModel)
        if (correctionIou != null) obj.put("correction_iou", correctionIou)
        if (careNote != null) obj.put("care_note", careNote)
        if (source != null) obj.put("source", source)
        val req = Request.Builder().url("$baseUrl/api/v1/annotation")
            .header("Authorization", "Bearer $jwt")
            .post(obj.toString().toRequestBody("application/json".toMediaType())).build()
        http.newCall(req).execute().use { resp ->
            val body = resp.body?.string() ?: ""
            // 直接把 raw JSON 丟給醫師看會變成一整串 \uXXXX 逃脫碼(實機截圖確認過,完全不可讀)
            // → 解析出 issues 逐條顯示中文
            return Pair(resp.isSuccessful, summarize(body))
        }
    }

    /** 把後端回應整理成人看得懂的一行字;解析失敗才退回原文。 */
    private fun summarize(body: String): String = try {
        val j = JSONObject(body)
        when {
            j.has("issues") -> {
                val a = j.getJSONArray("issues")
                (0 until a.length()).joinToString("；") { a.getString(it) }
            }
            j.has("error") -> j.getString("error")
            j.optString("status") == "duplicate_skipped" -> "duplicate:" + j.optString("note")
            j.has("note") && !j.isNull("note") -> j.getString("note")
            else -> j.optString("status", body.take(200))
        }
    } catch (e: Exception) {
        body.take(200)
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
