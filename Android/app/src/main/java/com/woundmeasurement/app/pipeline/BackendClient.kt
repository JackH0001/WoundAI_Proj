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
    val woundPolygon: List<List<Int>> = emptyList(),  // 最大的那一個(相容用)
    /**
     * **所有**傷口輪廓（由大到小）。同一肢體多處傷口是臨床常態。
     *
     * ⚠ 只用 [woundPolygon] 的話，AI 分割到的第二個傷口在修邊畫面就不會有初始輪廓，
     * 醫師得自己補畫；沒注意到的話那個傷口在訓練集裡會被標成背景。
     */
    val woundPolygons: List<List<List<Int>>> = emptyList(),
    val mmPerPx: Double? = null,                      // ArUco 尺度(mm/影像px):修邊面積=像素數×(mm/px)²
    /**
     * ArUco 標記的四個角點（TL,TR,BR,BL，與 imageW/H 同一座標空間）。
     *
     * 存在的理由不是好看：**ArUco 偵測沒有「認錯了」這個錯誤狀態**——它要嘛回四邊形、
     * 要嘛回 null。認錯目標（反光、其他方形圖案）時 mmPerPx 就是錯的，
     * 而每一筆面積都會安靜地錯，服務照回 200。程式判斷不了，只能讓人看一眼。
     */
    val markerQuad: List<List<Int>>? = null,
    val markerMm: Double? = null,
    val calibMethod: String? = null,
    /**
     * 影像品質指標：`focus_lapvar`（清晰度）、`clipped_frac`（過曝／死黑比例）、
     * `roi_short_px`（傷口短邊像素）、`marker_frac`／`marker_skew`（貼紙大小與斜視角）。
     *
     * ⚠ 這些要**跟著標註一起送回後端**，不是拿來給端上判斷用的。
     * 匯出訓練集時 `dataset manifest` 會依門檻篩掉模糊、過曝、角度過斜的樣本——
     * 而缺這幾個欄位的紀錄一律**不擋**（舊紀錄沒有欄位，擋掉會把早期樣本整批丟掉）。
     * 結果就是：Android 不送的話，品質門檻**只篩得掉 iOS 收的樣本**，
     * 兩端收的資料在同一個門檻下受到不同待遇，而報表上看不出來。
     */
    val quality: Map<String, Double> = emptyMap(),
    /**
     * 後端由**校正貼紙中性色塊**算出的白平衡增益 [R,G,B]（含曝光係數）。
     *
     * ⚠ 端上一定要用這一組，不可自行算灰世界。後端的組織比例已經是用它算的，
     * 端上若走另一套白平衡，結果欄與修邊畫面會顯示**兩個不同的答案**——
     * 而醫師的修正是在修邊畫面上做的，那份 GT 會進訓練集。
     *
     * null＝這張沒有可用的色卡（貼紙沒入鏡／過曝／過暗）。此時退回灰世界，
     * 而灰世界在以傷口為主體的近拍照上會把紅色壓掉約 22%（肉芽被低估）。
     */
    val wbGains: DoubleArray? = null,
    /** 色準校正的診斷訊息。null＝校正成功且無警告。 */
    val colorCalNote: String? = null,
    // 飛輪資料鏈:image_id=後端已存影像的內容雜湊;imageW/H=polygon 與修邊 GT 的座標空間。
    // 送標註時必須帶回,否則後端收到的是無影像、無尺寸的孤兒 GT(不可訓練)。
    val imageId: String? = null,
    val imageW: Int = 0,
    val imageH: Int = 0,
    val segModel: String? = null,
    /** true = AI 沒抓到但色彩分割抓得到 → 幾乎確定把印刷模擬圖誤選成了臨床/範例。 */
    val phantomHint: Boolean = false,
    /**
     * 這批完全相同的位元組後端先前已收過。
     *
     * 真實回診照片不可能與上次逐位元相同（光線、角度、時間戳都會變），
     * 所以臨床模式下出現 true 幾乎必然是**重複量測同一張範例／示範圖**。
     */
    val imageReused: Boolean = false,
    /** 模擬圖模式實際用了哪一段:strict / gray_world_wb(偏色時已自動白平衡重試)。 */
    val phantomPass: String? = null
)

/**
 * 登入後拿到的身分。**這只是給 UI 用的**——真正的閘門在伺服器端。
 *
 * ⚠ App 依 [perms] 隱藏或停用功能是為了讓人看得懂自己能做什麼，
 * 不是存取控制。APK 可以被反編譯、HTTP 請求可以直接偽造，
 * 所以每一項權限在後端都有對應的檢查（見 docs/rbac_design.md §5）。
 */
data class LoginIdentity(
    val identity: String,     // <org>:<user>
    val org: String,
    val user: String,
    val role: String,         // physician / nurse / assistant / engineer / admin
    val roleZh: String,
    val displayName: String?,
    val perms: Set<String>
) {
    fun can(perm: String) = perm in perms
    /** 標題列顯示用。共用手機時最常見的錯誤是用上一個人的登入做事——看得到才會發現。 */
    fun label() = "${displayName ?: user}（$roleZh）"
}

class BackendClient(private val baseUrl: String, jwt: String = "") {
    // escalate 的 classify 需跑 student+A∪U 兩模型,首次冷啟>10s;拉長逾時避免 timeout
    private val http = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(90, TimeUnit.SECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        .callTimeout(120, TimeUnit.SECONDS)
        .build()
    @Volatile private var jwt: String = jwt

    /** 登入後的身分；未登入為 null。 */
    @Volatile var identity: LoginIdentity? = null
        private set

    /** 登入取得 JWT(後端 /api/auth/login)。成功回 true 並存 token 供後續呼叫。同步阻塞,請於 IO 執行。 */
    /**
     * `/api/health` 的結果。[degraded] 為 true 時 [degradedReason] 一定有值。
     */
    data class Health(
        val status: String,                  // healthy / degraded
        val degraded: Boolean,
        val degradedReason: String?,
        val revision: String?                // Cloud Run 版次，用來確認「部署到底上去了沒」
    )

    /**
     * `GET /api/health`（**免認證**）。
     *
     * 免認證是刻意的：連線測試要能在「帳密還沒設對」的情況下分辨
     * 「連不到」與「連得到但服務降級」。如果它需要 token，那兩件事就永遠混在一起。
     *
     * ⚠ degraded 不是「慢一點」，是**分割模型或色準模組沒載到**——
     * 服務照樣回 200、面積照樣有數字，只是那個數字不具參考價值。
     * 這正是必須講出來的那一類故障：它不會自己現形。
     */
    fun health(): Health? = try {
        val req = Request.Builder().url("$baseUrl/api/health").get().build()
        http.newCall(req).execute().use { resp ->
            if (!resp.isSuccessful) null else {
                val j = JSONObject(resp.body!!.string())
                val st = j.optString("status", "unknown")
                Health(
                    status = st,
                    degraded = st == "degraded",
                    degradedReason = j.optString("degraded_reason").ifBlank { null },
                    revision = j.optJSONObject("build")?.optString("revision")?.ifBlank { null }
                )
            }
        }
    } catch (e: Exception) {
        null      // 連不上與降級是兩件事，這裡只回報「問不到」，由呼叫端區分
    }

    fun login(username: String, password: String): Boolean {
        val body = JSONObject(mapOf("username" to username, "password" to password)).toString()
            .toRequestBody("application/json".toMediaType())
        val req = Request.Builder().url("$baseUrl/api/auth/login").post(body).build()
        http.newCall(req).execute().use { resp ->
            if (!resp.isSuccessful) { identity = null; return false }
            val j = JSONObject(resp.body!!.string())
            val tok = j.optString("access_token", "")
            if (tok.isEmpty()) { identity = null; return false }
            jwt = tok
            val pa = j.optJSONArray("perms")
            identity = LoginIdentity(
                identity = j.optString("identity", username),
                org = j.optString("org", "default"),
                user = j.optString("user", username),
                role = j.optString("role", ""),
                roleZh = j.optString("role_zh", j.optString("role", "")),
                displayName = if (j.isNull("display_name")) null else j.optString("display_name"),
                perms = buildSet { if (pa != null) for (i in 0 until pa.length()) add(pa.getString(i)) }
            )
            return true
        }
    }

    /**
     * 飛輪佇列健康度(GET /api/v1/flywheel/stats)。回 (成功, 訊息)。需先 [login]。
     *
     * 設定頁用它顯示**臨床收案進度**:`by_source.clinical` 就是 n=20 的分母來源。
     * 特別把 clinical 與其他來源分開顯示——範例/模擬圖走同一條管線收進來,
     * 混在一起看會讓收案進度看起來比實際多。
     */
    /**
     * 取一個一次性登入碼，用來把「已登入的身分」交給同一台裝置上的瀏覽器。
     *
     * ⚠ **不可改成直接把 jwt 放進網址。** Cloud Run 會把完整請求 URL 寫進 Cloud Logging，
     * 查詢字串裡的 token 就以明文躺在日誌裡，任何具 log viewer 權限的人都能複製它
     * 冒用這位醫師的身分，而效期還有 24 小時。
     *
     * 一次性碼放在 URL **fragment**（`#c=...`）——fragment 不會送到伺服器，
     * 因此不進任何伺服器日誌；效期 60 秒、用過即失效，且後端擋住它去打任何一般端點。
     *
     * 回 null 表示拿不到（後端太舊、網路失敗、token 過期）；呼叫端應退回「開網址但要手動登入」。
     */
    fun oneTimeCode(): String? {
        return try {
            val req = Request.Builder().url("$baseUrl/api/v1/auth/onetime")
                .header("Authorization", "Bearer $jwt")
                .post("{}".toRequestBody("application/json".toMediaType())).build()
            http.newCall(req).execute().use { resp ->
                if (!resp.isSuccessful) return null
                JSONObject(resp.body?.string() ?: "").optString("code").takeIf { it.isNotBlank() }
            }
        } catch (e: Exception) { null }
    }

    fun flywheelStats(): Pair<Boolean, String> {
        val req = Request.Builder().url("$baseUrl/api/v1/flywheel/stats")
            .header("Authorization", "Bearer $jwt").get().build()
        http.newCall(req).execute().use { resp ->
            val body = resp.body?.string() ?: ""
            if (!resp.isSuccessful) return Pair(false, summarize(body))
            return try {
                val s = JSONObject(body).optJSONObject("stats") ?: JSONObject(body)
                val bySrc = s.optJSONObject("by_source")
                Pair(true, buildString {
                    append("可訓練 ${s.optInt("trainable")} 筆／佇列共 ${s.optInt("total")} 筆\n")
                    if (bySrc != null) append(
                        "臨床 ${bySrc.optInt("clinical")}・範例 ${bySrc.optInt("sample")}" +
                        "・模擬圖 ${bySrc.optInt("phantom")}・外部 ${bySrc.optInt("external")}\n")
                    append("已撤回 ${s.optInt("withdrawn")}・被取代 ${s.optInt("superseded")}")
                    append("・孤兒GT ${s.optInt("orphan_no_image")}・影像遺失 ${s.optInt("image_file_missing")}")
                })
            } catch (e: Exception) { Pair(false, "回應解析失敗：${e.message}") }
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
            fun pts(a: JSONArray): List<List<Int>> {
                val o = ArrayList<List<Int>>(a.length())
                for (i in 0 until a.length()) {
                    val pt = a.optJSONArray(i) ?: continue
                    if (pt.length() >= 2) o.add(listOf(pt.optInt(0), pt.optInt(1)))
                }
                return o
            }
            val poly = if (s2.isNull("wound_polygon")) emptyList()
                       else pts(s2.getJSONArray("wound_polygon"))
            // 舊後端沒有這個鍵 → optJSONArray 回 null，退回單一輪廓。
            // 用 getJSONArray 的話 App 一連上舊後端整個 classify 就拋例外。
            val polys = s2.optJSONArray("wound_polygons")?.let { arr ->
                (0 until arr.length()).mapNotNull { i ->
                    arr.optJSONArray(i)?.let { pts(it) }?.takeIf { it.size >= 3 }
                }
            } ?: emptyList()
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
                woundPolygons = polys.ifEmpty { if (poly.size >= 3) listOf(poly) else emptyList() },
                mmPerPx = if (s3.isNull("mm_per_px")) null else s3.getDouble("mm_per_px"),
                markerQuad = if (s3.isNull("marker_quad")) null else runCatching {
                    val qa = s3.getJSONArray("marker_quad")
                    (0 until qa.length()).map { i ->
                        val pt = qa.getJSONArray(i); listOf(pt.getInt(0), pt.getInt(1))
                    }.takeIf { it.size == 4 }
                }.getOrNull(),
                markerMm = if (s3.isNull("marker_mm")) null else s3.getDouble("marker_mm"),
                calibMethod = if (s3.isNull("method")) null else s3.getString("method"),
                // 品質指標。鍵是後端決定的，端上不要硬編一份清單去挑——
                // 後端加了新指標而端上沒跟著改，那個指標就永遠不會落盤，
                // 而且沒有任何地方會報錯。全收就沒有這個問題。
                quality = j.optJSONObject("quality")?.let { q ->
                    val m = LinkedHashMap<String, Double>()
                    q.keys().forEach { k ->
                        val v = q.opt(k)
                        if (v is Number) m[k] = v.toDouble()
                    }
                    m
                } ?: emptyMap(),
                // 色準校正（stage3b）。舊版後端沒有這個鍵——用 optJSONObject 而非
                // getJSONObject，否則 App 一連上舊後端就整個 classify 拋例外。
                wbGains = j.optJSONObject("stage3b_colorcal")?.takeIf { it.optBoolean("ok") }
                    ?.let { c ->
                        val e = c.optDouble("exposure", 1.0)
                        doubleArrayOf(c.optDouble("gain_r", 1.0) * e,
                                      c.optDouble("gain_g", 1.0) * e,
                                      c.optDouble("gain_b", 1.0) * e)
                    },
                colorCalNote = j.optJSONObject("stage3b_colorcal")?.let { c ->
                    if (c.optBoolean("ok")) c.optString("reason", "").takeIf { it.isNotBlank() }
                    else c.optString("reason", "色準校正未執行").takeIf { it.isNotBlank() }
                },
                imageId = if (j.isNull("image_id")) null else j.getString("image_id"),
                imageW = j.optInt("image_w", 0),
                imageH = j.optInt("image_h", 0),
                segModel = if (s2.isNull("model")) null else s2.getString("model"),
                phantomHint = j.optBoolean("phantom_hint", false),
                imageReused = j.optBoolean("image_reused", false),
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
        /**
         * **所有**傷口輪廓。同一肢體多處傷口是臨床常態。
         *
         * ⚠ 空的話只送 [gtPolygon]（最大的那一個），而其餘傷口在訓練集裡會被
         * 標成背景——那是在教模型「那不是傷口」，比少收一筆資料糟得多。
         */
        allPolygons: List<List<List<Int>>> = emptyList(),
        /**
         * 面積（cm²）。來自**遮罩像素數 × 鎖定係數**，是唯一真值。
         *
         * 不給的話後端會由多邊形反算，而那個數字與 App 顯示的必然不同：
         * RDP 簡化有損、多輪廓還要合計。兩個都合理、都沒有警告的數字最難查。
         */
        areaCm2: Double? = null,
        imageId: String?, imageW: Int, imageH: Int,
        mmPerPx: Double? = null, route: String? = null, segModel: String? = null,
        tissueFrac: Map<String, Double>? = null,
        /** 組織分割 GT（base64 PNG，值＝組織碼）與其柵格→影像座標的仿射參數。 */
        tissueMaskPng: String? = null,
        tissueRaster: EditRaster? = null,
        /**
         * classify 當下算出的影像品質指標，原樣回送。
         * 不送的話這筆在訓練集匯出時**篩不掉也擋不住**——見 [ClassifyResult.quality]。
         */
        quality: Map<String, Double>? = null,
        correctionIou: Double? = null, careNote: String? = null,
        source: String? = null,  // clinical(預設)/sample/phantom/external;範例集不可灌進臨床樣本數
        /**
         * ②訓練同意的**真值**,來自本機 `ConsentEntity.trainEffective`。
         * ⚠ 這裡曾經硬編碼 true——等於每筆送出都謊稱已取得訓練同意,而實際上沒人勾過。
         * 呼叫端沒有有效同意就不該呼叫本函式;真的呼叫了,後端也會以 400 擋下。
         */
        consentTrain: Boolean,
        /**
         * 醫師是否**真的完成過修邊確認**。
         *
         * ⚠ 這裡曾經硬編碼 `true`——與 `consent_train` 是同一類缺陷：
         * App 對後端聲稱了它沒有驗證過的事。醫師在修邊頁按「取消」後仍可送出，
         * 於是一筆從未被人看過的 AI 輸出會以「醫師已驗證」的身分進入訓練集。
         * 飛輪的整個前提是 GT 來自人的判斷，這個欄位說謊會讓後續所有模型評估失去意義。
         */
        doctorVerified: Boolean
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
            .put("doctor_verified", doctorVerified)   // 真值來自 MeasureViewModel.lastDoctorVerified
            .put("deidentified", true)
            .put("consent_train", consentTrain)   // 由本機同意紀錄決定,不再硬編碼
            .put("image_id", imageId)
            .put("image_w", imageW)
            .put("image_h", imageH)
        // 多輪廓。gt_polygon 保留最大的那一個供舊後端使用——新舊後端都收得下。
        val polysOut = allPolygons.filter { it.size >= 3 }
        if (polysOut.size > 1) {
            val arr = JSONArray()
            polysOut.forEach { p ->
                val pa = JSONArray()
                p.forEach { pt -> pa.put(JSONArray().put(pt.getOrElse(0) { 0 }).put(pt.getOrElse(1) { 0 })) }
                arr.put(pa)
            }
            obj.put("gt_polygons", arr)
        }
        if (areaCm2 != null) obj.put("area_cm2", areaCm2)
        if (mmPerPx != null) obj.put("mm_per_px", mmPerPx)
        if (route != null) obj.put("route", route)
        if (segModel != null) obj.put("seg_model", segModel)
        if (!quality.isNullOrEmpty()) {
            val qo = JSONObject()
            quality.forEach { (k, v) -> qo.put(k, v) }
            obj.put("quality", qo)
        }
        if (correctionIou != null) obj.put("correction_iou", correctionIou)
        if (careNote != null) obj.put("care_note", careNote)
        if (source != null) obj.put("source", source)
        // WoundAI3D 預留：現在一律 none，但**明確標記**比欄位缺席好——
        // 日後分析時「沒拍深度」與「拍了但沒存」要分得出來。
        // capture_device 現在就有值，且它是日後查「某機型面積系統性偏高」的唯一依據。
        obj.put("depth_source", "none")
        obj.put("capture_device", "${android.os.Build.MANUFACTURER} ${android.os.Build.MODEL}")
        // 醫師修邊後的組織比例（含「其他」）。不是分割 GT，是未來訓練組織分類的種子——
        // 影像會依保存政策清除，事後無法回溯重算，所以現在就收。
        if (tissueFrac != null && tissueFrac.isNotEmpty()) {
            obj.put("tissue_frac", JSONObject().also { t ->
                tissueFrac.forEach { (k2, v) -> t.put(k2, v) }
            })
        }
        // 組織分割 GT。**tissue_edited 一定要跟著送**——後端靠它決定這張遮罩
        // 能不能進訓練集。少了它，未經醫師修正的啟發式輸出會安靜地混進去，
        // 而模型就在學習複製它自己已經會做的事（見 EditRaster.tissueEditedPx）。
        if (tissueMaskPng != null && tissueRaster != null) {
            obj.put("tissue_mask_png", tissueMaskPng)
            obj.put("tissue_raster", JSONObject().apply {
                put("rx0", tissueRaster.rx0.toDouble()); put("ry0", tissueRaster.ry0.toDouble())
                put("mw", tissueRaster.mw); put("mh", tissueRaster.mh)
                put("m_scale", tissueRaster.mScale.toDouble())
            })
            obj.put("tissue_edited", tissueRaster.tissueEdited)
            obj.put("tissue_edit_px", tissueRaster.tissueEditedPx)
            obj.put("tissue_edit_ratio", tissueRaster.tissueEditRatio)
        }
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
    /**
     * 重新取得同意（re-consent）→ 解除雲端的撤回封鎖。
     *
     * ⚠ 沒有這一支，撤回就是**死局**：病患改變主意重新簽署後，App 端顯示
     * 「訓練同意✓」，而雲端仍以「已撤回訓練同意」擋下每一次送出——
     * 錯誤訊息還會直接把內部端點路徑印給醫師看：
     *   「被守門擋下：代碼 WD-xxxx 已撤回訓練同意。重新取得同意請先呼叫
     *     /api/v1/consent/restore」
     * 那不是給臨床人員看的東西，而且他也沒有辦法自己呼叫它。
     */
    fun restoreConsent(code: String): Boolean {
        val body = JSONObject(mapOf("code" to code)).toString()
            .toRequestBody("application/json".toMediaType())
        val req = Request.Builder().url("$baseUrl/api/v1/consent/restore")
            .header("Authorization", "Bearer $jwt").post(body).build()
        http.newCall(req).execute().use { return it.isSuccessful }
    }

    fun withdrawConsent(code: String): Boolean {
        val body = JSONObject(mapOf("code" to code)).toString()
            .toRequestBody("application/json".toMediaType())
        val req = Request.Builder().url("$baseUrl/api/v1/consent/withdraw")
            .header("Authorization", "Bearer $jwt").post(body).build()
        http.newCall(req).execute().use { return it.isSuccessful }
    }
}
