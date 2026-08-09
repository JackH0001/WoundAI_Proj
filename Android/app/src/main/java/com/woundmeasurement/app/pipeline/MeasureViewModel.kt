package com.woundmeasurement.app.pipeline

import android.graphics.Bitmap
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.woundmeasurement.app.data.dao.MeasurementDao
import com.woundmeasurement.app.data.entity.MeasurementEntity
import java.util.Date
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.ByteArrayOutputStream

/**
 * 量測畫面 ViewModel(MVVM)：拍攝後 → ArUco 偵測 → WoundAnalyzer(分割→雙軌→面積→組織v2→PUSH) → UI 狀態。
 * UI 觀察 [state]；結果含面積/組織/PUSH/route/信心度。輔助、非診斷、需醫師確認。
 */
data class MeasureUiState(
    val loading: Boolean = false,
    val result: MeasureResult? = null,
    val error: String? = null,
    val submitStatus: String? = null,   // 飛輪標註送出狀態(醫師修邊→再訓練佇列)
    val edited: Boolean = false,        // 醫師已完成修邊(閘門:送出標註前置條件之一)
    val saved: Boolean = false          // 已存入時間軸(閘門:同上)
)

class MeasureViewModel(
    private val analyzer: WoundAnalyzer,
    private val aruco: ArucoDetector? = null
) : ViewModel() {

    private val _state = MutableStateFlow(MeasureUiState())
    val state: StateFlow<MeasureUiState> = _state.asStateFlow()

    // 後端 classify 回傳的傷口輪廓(供醫師修邊/飛輪標註送出);修邊 UI 可覆寫此值
    @Volatile var lastPolygon: List<List<Int>> = emptyList()
        private set
    /**
     * **所有**傷口輪廓（由大到小）。同一肢體多處傷口是臨床常態。
     *
     * ⚠ [lastPolygon] 只是它的第一個元素，保留給只吃單一輪廓的舊路徑。
     * 送訓練標註與畫參照圖都要用這一個——只用 lastPolygon 的話，
     * 第二個傷口會被標成背景，而那是在**教模型「那不是傷口」**。
     */
    @Volatile var lastPolygons: List<List<List<Int>>> = emptyList()
        private set
    // 最近分析的原圖(供修邊畫布顯示)
    @Volatile var lastBitmap: Bitmap? = null
        private set
    // 醫師修邊後與原始遮罩的 IoU(修正幅度;1.0=未改),隨標註送出
    @Volatile var lastCorrectionIou: Double? = null
        private set
    /**
     * 醫師是否**真的按下「完成修邊」**（而非取消或根本沒進修邊頁）。
     *
     * ⚠ 這是 `doctor_verified` 的真值來源。先前它在 BackendClient 裡硬編碼 true，
     * 而修邊頁按「取消」之後畫面仍留著 AI 的原始輪廓、存檔與送出照樣可按——
     * 於是一筆從未被人看過的 AI 輸出會以「醫師已驗證」的身分進入訓練集。
     * 飛輪的整個前提是 GT 來自人的判斷；這個旗標一旦說謊，
     * 後續所有以它為基礎的模型評估都失去意義。
     */
    @Volatile var lastDoctorVerified: Boolean = false
        private set
    // 同一次影像去重存檔:影像雜湊 + 已存的 row id(同影像重存→更新同筆,不新增)
    @Volatile private var lastImageHash: Int? = null
    @Volatile private var lastSavedId: Long? = null
    // editRaster 所依附的畫布尺寸(端上=原圖、後端=≤2048 縮圖);尺寸變了柵格座標就對不上
    @Volatile private var lastCanvasW: Int = 0
    @Volatile private var lastCanvasH: Int = 0
    // 修邊遮罩持久化(同影像再進修邊→原樣續編,免多邊形往返損耗;換影像清除)
    @Volatile var editRaster: EditRaster? = null
    // ArUco 尺度(mm/影像px,後端直傳):修邊面積=像素數×(mm/px)²,不依賴 AI 初始面積
    @Volatile var lastMmPerPx: Double? = null
    /** ArUco 校正框角點與方法，供結果頁的參照圖目視複核（見 ClassifyResult.markerQuad）。 */
    @Volatile var lastMarkerQuad: List<List<Int>>? = null
    /**
     * 後端由校正貼紙算出的白平衡增益 [R,G,B]。
     *
     * ⚠ 端上的組織分類（修邊底稿、參照圖）**必須用這一組**，不可自行算灰世界。
     * 後端的 tissueFrac 已經是用它算的；兩邊不一致的話結果欄與修邊畫面會顯示
     * 兩個不同的答案，而醫師是在修邊畫面上做判斷的。
     */
    @Volatile var lastWbGains: DoubleArray? = null
    @Volatile var lastCalibMethod: String? = null
        private set
    // 飛輪資料鏈綁定:後端 classify 存下的影像雜湊 + polygon 座標空間尺寸 + 路由/模型(溯源)。
    // 缺這些,送出的標註就是無影像的孤兒 GT,永遠訓練不了(2026-07-28 稽核發現舊佇列 8/8 皆如此)。
    @Volatile var lastImageId: String? = null
        private set
    @Volatile var lastImageW: Int = 0
        private set
    @Volatile var lastImageH: Int = 0
        private set
    @Volatile var lastRoute: String? = null
        private set
    @Volatile var lastSegModel: String? = null
        private set
    /** 後端提示:AI 空手但色彩分割有料 → 使用者很可能把印刷模擬圖選成了臨床/範例。 */
    @Volatile var lastPhantomHint: Boolean = false
        private set
    /**
     * 後端提示:這批**完全相同的位元組**先前已上傳過。
     *
     * 真實回診照片不可能與上次逐位元相同,所以臨床模式看到 true 幾乎必然是重複量測同一張範例圖。
     * 實測發生過:在臨床個案裡選了先前用過的範例圖 → 那筆以 `source=clinical` 進了訓練佇列,
     * 同時讓該傷口的癒合曲線變成「兩張不同的傷口相比」而顯示假的 ↓38%。
     */
    @Volatile var lastImageReused: Boolean = false
        private set
    /** 被自動排除的「貼紙誤認傷口」數（AI 把 ArUco 印刷方塊當壞死組織）。UI 要明講並可在修邊補畫。 */
    @Volatile var lastMarkerDropped: Int = 0
        private set
    /**
     * classify 當下算出的影像品質指標，原樣保留待送出。
     *
     * ⚠ 一定要跟著標註回送：訓練集匯出的品質門檻對**沒有這些欄位的紀錄一律放行**
     * （舊紀錄本來就沒有，擋掉會把早期樣本整批丟掉）。所以不送不是「少一點資訊」，
     * 而是這一筆變成篩不掉的——模糊、過曝、角度過斜的樣本會照樣進訓練集。
     */
    @Volatile var lastQuality: Map<String, Double> = emptyMap()
        private set

    /**
     * 換一張影像時清空所有「上一次分析」的殘留。
     *
     * 沒有這個重置會出人命等級的錯:後端模式跑完 A 圖後切端上模式跑 B 圖,
     * lastImageId/lastBitmap/lastPolygon 仍指向 A → 修邊畫面開的是 A 圖、
     * 送出的標註綁 A 的 image_id、時間軸還會用 lastSavedId 覆寫 A 那筆。
     * 端上路徑(analyze)沒有後端影像綁定,故 imageId 一律為 null,送標註時會被守門擋下。
     */
    private fun clearBackendBinding(alsoBitmap: Boolean = false) {
        lastPolygon = emptyList(); lastPolygons = emptyList()
        lastCorrectionIou = null
        lastMmPerPx = null; lastMarkerQuad = null; lastCalibMethod = null; lastWbGains = null
        lastImageId = null; lastImageW = 0; lastImageH = 0
        lastRoute = null; lastSegModel = null; lastPhantomHint = false; lastImageReused = false
        lastDoctorVerified = false   // 新影像＝尚未經醫師確認,不可沿用上一張的驗證狀態
        // 分析失敗時也要丟掉上一張的原圖,否則修邊入口可能開到「上一位病人」的影像
        if (alsoBitmap) {
            lastBitmap = null; lastImageHash = null; lastSavedId = null; editRaster = null
            lastCanvasW = 0; lastCanvasH = 0
        }
    }

    /**
     * 綁定本次分析的影像。
     * @param identity 影像身分一律以**原圖**計算雜湊——端上路徑拿不到縮圖,若用縮圖當基準,
     *   同一張照片在「端上 ↔ 後端」切換比對時雜湊必不同,修邊遮罩會被誤清、時間軸重複插入。
     * @param canvas 實際顯示/編輯用的點陣圖(後端路徑是 ≤2048 縮圖,polygon 座標即此空間)。
     *   畫布尺寸變了就不能沿用 editRaster(柵格座標對不上)。
     */
    private fun bindImage(identity: Bitmap, canvas: Bitmap) {
        val hh = quickHash(identity)
        val sameImage = (hh == lastImageHash)
        if (!sameImage) lastSavedId = null                       // 換影像→時間軸另存新筆
        if (!sameImage || canvas.width != lastCanvasW || canvas.height != lastCanvasH)
            editRaster = null                                    // 換影像或換畫布尺寸→修邊遮罩不可沿用
        lastImageHash = hh
        lastBitmap = canvas
        lastCanvasW = canvas.width; lastCanvasH = canvas.height
    }

    private fun resetBinding(bitmap: Bitmap) {
        clearBackendBinding()
        bindImage(bitmap, bitmap)
    }

    private fun quickHash(b: Bitmap): Int {
        var h = 17
        val sx = maxOf(1, b.width / 32); val sy = maxOf(1, b.height / 32)
        var y = 0
        while (y < b.height) {
            var x = 0
            while (x < b.width) { h = 31 * h + b.getPixel(x, y); x += sx }
            y += sy
        }
        return h
    }

    /**
     * @param bitmap 拍攝原圖(含校正貼紙)
     * @param exudate 滲液(醫師輸入 0–3)或 null
     * @param cloudEscalate 難例上雲(呼叫 /api/v1/segment/escalate)；null 則純端上
     */
    fun analyze(
        bitmap: Bitmap,
        exudate: Int?,
        cloudEscalate: (suspend (Bitmap) -> BooleanArray)? = null
    ) {
        _state.value = _state.value.copy(loading = true, error = null)
        resetBinding(bitmap)   // 端上路徑無後端影像綁定 → 清掉上一次(可能是後端模式)的殘留
        viewModelScope.launch {
            try {
                val corners = aruco?.detect(bitmap, 7)   // null → 面積未校正(graceful)
                val r = analyzer.run(
                    bitmap = bitmap,
                    markerCorners = corners,
                    exudate = exudate,
                    cloudEscalate = cloudEscalate
                )
                _state.value = MeasureUiState(loading = false, result = r)
            } catch (e: Exception) {
                _state.value = MeasureUiState(loading = false, error = e.message ?: "分析失敗")
            }
        }
    }

    /**
     * 後端驗證路徑(最短閉環)：bitmap → JPEG → POST /api/v1/classify → 映射為 [MeasureResult] 顯示。
     * 用途：與端上結果並列比對(對齊預言機),確認 App↔後端 面積/PUSH/組織 一致。
     * @param cmPerPixel 無 ArUco 時手動校正(cm/px);有貼紙則後端自動 ArUco 校正。
     */
    fun analyzeViaBackend(
        bitmap: Bitmap,
        backend: BackendClient,
        exudate: Int? = null,
        cmPerPixel: Double? = null,
        seg: String? = null            // "color"=印刷模擬圖走色彩分割(不碰模型);null=AI
    ) {
        _state.value = _state.value.copy(loading = true, error = null)
        clearBackendBinding()   // 先清舊綁定:失敗時不可留著上一張的 image_id 讓醫師誤送
        viewModelScope.launch {
            try {
                // 長邊縮到 ≤2048:模型輸入僅256、ArUco 於2048仍清晰、比例法尺度不變;
                // 記憶體(5712寬原圖≈70MB ARGB)與上傳大減,避免反覆編修 OOM 閃退
                val mx = maxOf(bitmap.width, bitmap.height)
                val scale = if (mx > 2048) 2048.0 / mx else 1.0
                val work = withContext(Dispatchers.Default) {
                    if (scale < 1.0)
                        Bitmap.createScaledBitmap(bitmap, (bitmap.width * scale).toInt(), (bitmap.height * scale).toInt(), true)
                    else bitmap
                }
                // ⚠ cmPerPixel 是「原圖」的 cm/px;縮圖後每個像素涵蓋更多原圖像素 → 必須除以 scale,
                // 否則後端會用原圖尺度乘縮圖像素數,面積差 1/scale²,而且錯的尺度會被寫進訓練集。
                val cppWork = cmPerPixel?.let { it / scale }
                var polyCap: List<List<Int>> = emptyList()
                var polysCap: List<List<List<Int>>> = emptyList()
                var mmCap: Double? = null
                var quadCap: List<List<Int>>? = null; var calibCap: String? = null
                var wbCap: DoubleArray? = null
                var idCap: String? = null; var wCap = 0; var hCap = 0
                var routeCap: String? = null; var modelCap: String? = null
                var hintCap = false; var reusedCap = false
                var qualCap: Map<String, Double> = emptyMap()
                val r = withContext(Dispatchers.IO) {
                    val jpeg = work.toJpeg()
                    val c = backend.classify(jpeg, cppWork, seg)
                    polyCap = c.woundPolygon
                    polysCap = c.woundPolygons
                    mmCap = c.mmPerPx
                    quadCap = c.markerQuad
                    calibCap = c.calibMethod
                    wbCap = c.wbGains
                    idCap = c.imageId; wCap = c.imageW; hCap = c.imageH
                    routeCap = c.route; modelCap = c.segModel; hintCap = c.phantomHint
                    reusedCap = c.imageReused
                    qualCap = c.quality
                    MeasureResult(
                        areaCm2 = c.areaCm2,
                        tissueFrac = c.tissueFrac,
                        push = PushScore(
                            area = null, tissue = 0, exudate = exudate,
                            partial = c.pushPartial,
                            full = c.pushFull ?: c.pushPartial?.let { p -> exudate?.let { p + it } }
                        ),
                        route = c.route,
                        confidence = c.confidence
                    )
                }
                bindImage(identity = bitmap, canvas = work)   // 編輯/顯示用縮圖(polygon 座標即此圖座標)
                // ArUco 認到貼紙時，剔除「大部分壓在貼紙上」的輪廓（≥70% 點落在外擴 15% 框內）。
                // 只沾到邊的不動；剔了一定說出來（lastMarkerDropped）且可在修邊畫面手動補畫。
                lastMarkerDropped = 0
                quadCap?.takeIf { it.size == 4 }?.let { q ->
                    val x0 = q.minOf { it[0] }.toDouble(); val x1 = q.maxOf { it[0] }.toDouble()
                    val y0 = q.minOf { it[1] }.toDouble(); val y1 = q.maxOf { it[1] }.toDouble()
                    val ex = (x1 - x0) * 0.15; val ey = (y1 - y0) * 0.15
                    fun onMarker(poly: List<List<Int>>): Boolean {
                        if (poly.isEmpty()) return false
                        val inside = poly.count {
                            it[0] >= x0 - ex && it[0] <= x1 + ex && it[1] >= y0 - ey && it[1] <= y1 + ey
                        }
                        return inside.toDouble() / poly.size >= 0.7
                    }
                    val kept = polysCap.filter { !onMarker(it) }
                    lastMarkerDropped = polysCap.size - kept.size
                    if (lastMarkerDropped > 0) {
                        polysCap = kept
                        polyCap = kept.maxByOrNull { it.size } ?: emptyList()
                    }
                }
                lastPolygon = polyCap
                // 後端已改為回傳所有連通元件；舊後端只有一個時退回單一輪廓。
                lastPolygons = polysCap.ifEmpty {
                    if (polyCap.size >= 3) listOf(polyCap) else emptyList()
                }
                lastCorrectionIou = null   // 新分析→重置修邊修正量
                lastDoctorVerified = false // 新分析→醫師尚未確認
                lastMmPerPx = mmCap
                lastMarkerQuad = quadCap; lastCalibMethod = calibCap; lastWbGains = wbCap
                lastImageId = idCap; lastImageW = wCap; lastImageH = hCap
                lastRoute = routeCap; lastSegModel = modelCap; lastPhantomHint = hintCap
                lastImageReused = reusedCap
                lastQuality = qualCap
                _state.value = MeasureUiState(loading = false, result = r)
            } catch (e: Exception) {
                clearBackendBinding(alsoBitmap = true)
                _state.value = MeasureUiState(loading = false, error = e.message ?: "後端分析失敗")
            }
        }
    }

    /**
     * 醫師確認・送出訓練標註(飛輪閉環)：以後端回傳(或修邊後)的傷口輪廓為 GT → POST /api/v1/annotation。
     * 送 doctor_verified/deidentified/consent_train=true;後端守門不合則回訊息。
     * @param code 去識別代碼(WD-*);@param exudate 醫師輸入滲液 0–3
     */
    /** 由 UI 端的守門(如同意已撤回)擋下時,用同一個狀態管道回報,才會走到既有的彈窗流程。 */
    fun reportSubmitBlocked(message: String) {
        _state.value = _state.value.copy(submitStatus = message)
    }

    fun submitAnnotation(
        backend: BackendClient, code: String, exudate: Int?, careNote: String? = null,
        source: String? = null,  // 內建範例圖請傳 "sample";真實病人留 null(後端預設 clinical)
        /**
         * ②訓練同意真值。臨床個案請帶 `ConsentEntity.trainEffective`;
         * 範例/模擬圖無受試者,視同已同意(它們本來就不含 PHI)。
         */
        consentTrain: Boolean = true
    ) {
        val poly = lastPolygon
        if (poly.isEmpty()) {
            _state.value = _state.value.copy(submitStatus = "⚠️ 無傷口輪廓可送(請先量測)"); return
        }
        // 醫師沒按過「完成修邊」就送出 = 把 AI 的原始輸出當成人工 GT 灌進訓練集。
        // 後端也會擋（doctor_verified=false → 400），但在這裡就擋下才給得出有用的訊息。
        if (!lastDoctorVerified) {
            _state.value = _state.value.copy(
                submitStatus = "⚠️ 尚未完成醫師修邊確認,不得送訓練標註。\n" +
                               "請按「醫師確認・修邊」並完成後再送(按取消不算確認)。"); return
        }
        // 端上模式(未經後端 classify)沒有 image_id → 送了也只是孤兒 GT,先擋
        if (lastImageId.isNullOrEmpty()) {
            _state.value = _state.value.copy(
                submitStatus = "⚠️ 此結果未綁定後端影像(端上模式);請切「後端」重新量測後再送訓練標註"); return
        }
        _state.value = _state.value.copy(submitStatus = "送出中…")
        viewModelScope.launch {
            try {
                val (ok, msg) = withContext(Dispatchers.IO) {
                    backend.submitAnnotation(
                        code, poly, exudate,
                        // 多處傷口：只送 poly 的話第二個傷口會被標成背景。
                        allPolygons = lastPolygons,
                        // 面積以**遮罩像素**為真值，不讓後端由多邊形反算——
                        // RDP 簡化 + 多輪廓合計，兩邊各算一次必然對不上。
                        areaCm2 = _state.value.result?.areaCm2,
                        imageId = lastImageId, imageW = lastImageW, imageH = lastImageH,
                        mmPerPx = lastMmPerPx, route = lastRoute, segModel = lastSegModel,
                        // 品質指標原樣回送。不送的話這一筆在訓練集匯出時篩不掉——
                        // 缺欄位的紀錄門檻一律放行。
                        quality = lastQuality,
                        correctionIou = lastCorrectionIou, careNote = careNote, source = source,
                        // 醫師修邊後的組織比例（含「其他」）。用 state 裡的值而不是 lastXxx——
                        // 修邊完成時 applyEditedPolygon 已把它寫回 result，那才是醫師確認過的版本。
                        tissueFrac = _state.value.result?.tissueFrac,
                        // 組織分割 GT。editRaster 為 null（醫師沒進修邊）時整組不送——
                        // 沒有柵格就沒有遮罩，硬用 AI 輪廓補一張出來只會製造假 GT。
                        tissueMaskPng = editRaster?.let {
                            TissueMaskCodec.encode(it.tissue, it.mask, it.mw, it.mh)
                        },
                        tissueRaster = editRaster,
                        consentTrain = consentTrain, doctorVerified = lastDoctorVerified
                    )
                }
                _state.value = _state.value.copy(
                    submitStatus = when {
                        !ok -> "⚠️ 被守門擋下:$msg"
                        msg.contains("duplicate") -> "ℹ️ 相同影像的相同遮罩已在佇列,已自動略過(去重)"
                        msg.contains("修訂") -> "✅ 已送出($code);同影像已有舊標註,本筆視為修訂版,匯出訓練集時取最新"
                        else -> "✅ 已送出,進再訓練佇列($code)"
                    }
                )
            } catch (e: Exception) {
                _state.value = _state.value.copy(submitStatus = "⚠️ 送出失敗:${e.message}")
            }
        }
    }

    /**
     * 醫師修邊完成:覆寫 GT polygon、記 correction_iou,以修邊後「面積+組織比例」重算 PUSH → 更新結果卡。
     * newArea/tissue 由編輯頁 raster 像素計數即時換算(免重傳)。
     */
    fun applyEditedPolygon(
        edited: List<List<Int>>, correctionIou: Double?, newArea: Double?, exudate: Int?,
        tissue: Map<String, Double>? = null,
        /** 所有輪廓。null＝呼叫端還沒支援多傷口，退回單一輪廓。 */
        allPolygons: List<List<List<Int>>>? = null
    ) {
        lastPolygon = edited
        lastPolygons = allPolygons?.takeIf { it.isNotEmpty() }
            ?: (if (edited.size >= 3) listOf(edited) else emptyList())
        lastCorrectionIou = correctionIou
        // 只有走到這裡（按下「完成修邊」）才算醫師確認過。取消不會呼叫本函式。
        lastDoctorVerified = true
        val r = _state.value.result
        val updated = if (r != null) {
            val frac = tissue ?: r.tissueFrac
            val area = newArea ?: r.areaCm2
            r.copy(areaCm2 = area, tissueFrac = frac, push = WoundPipeline.push(area, frac, exudate))
        } else r
        _state.value = _state.value.copy(
            result = updated,
            edited = true,
            submitStatus = "已套用修邊(面積 ${newArea?.let { "%.2f".format(it) } ?: "-"} cm²,修正 IoU=${correctionIou?.let { "%.2f".format(it) } ?: "-"})"
        )
    }

    /**
     * 存入個案時間軸(本機 Room/SQLite)。
     *
     * Sprint N1 起接受 [case]:量測綁定**傷口個案**。沒綁的話時間軸只能全表撈,
     * 兩位病患的傷口會被畫進同一條趨勢線(舊行為,`patientId` 恆為 null)。
     * 相容起見 case 可為 null(範例/模擬圖驗證不需要個案),但真實收案務必帶。
     *
     * 同一次影像去重:同影像(雜湊相同)重存/修邊後再存 → 更新同一筆,不重複新增(避免時間軸資料誤差)。
     */
    fun saveToTimeline(
        dao: MeasurementDao,
        exudate: Int?,
        case: com.woundmeasurement.app.data.entity.WoundCaseEntity? = null,
        source: String? = null,
        /**
         * 給了就把 work 影像加密存本機。**這是「回頭修邊」與「補送標註不必重測」的前提**——
         * v2 時 imagePath 一直是空字串,離開量測頁影像與輪廓就都沒了。
         */
        imageStore: com.woundmeasurement.app.data.store.LocalImageStore? = null
    ) {
        val r = _state.value.result ?: return
        _state.value = _state.value.copy(submitStatus = "存入時間軸中…")
        viewModelScope.launch {
            try {
                fun pct(k: String) = ((r.tissueFrac[k] ?: 0.0) * 100).toInt()
                val notes = "PUSH ${r.push.partial ?: "-"}; 肉芽${pct("granulation")}% 腐肉${pct("slough")}% 壞死${pct("necrosis")}%; 滲液${exudate ?: "-"}; route ${r.route}" +
                        (lastCorrectionIou?.let { "; 修邊IoU %.2f".format(it) } ?: "")
                // 多處傷口要存**全部**輪廓。單一輪廓仍寫成舊格式，DB 裡既有紀錄照樣讀得懂。
                val polyJson = polygonsToJson(lastPolygons.ifEmpty {
                    if (lastPolygon.size >= 3) listOf(lastPolygon) else emptyList()
                })
                val (id, updatedRow) = withContext(Dispatchers.IO) {
                    val imgName = if (imageStore != null) lastBitmap?.let { imageStore.save(it) } else null
                    // v6：把修邊柵格一起存下來。沒有它，從時間軸回頭修邊時醫師畫的組織分區
                    // 會整批消失、面積也會每進出一次漂移 0.5%（見 EditRasterCodec 的說明）。
                    val rasterPair = editRaster?.let { EditRasterCodec.encode(it) }
                    val rasterName = if (imageStore != null && rasterPair != null)
                        imageStore.save(rasterPair.first) else null
                    val existId = lastSavedId
                    val exist = existId?.let { dao.getMeasurementById(it) }
                    if (exist != null) {
                        dao.updateMeasurement(exist.copy(
                            timestamp = Date(), confidence = r.confidence, estimatedArea = r.areaCm2,
                            woundType = "AI(${r.route})", notes = notes,
                            hasWound = (r.areaCm2 ?: 0.0) > 0.0 || r.tissueFrac.values.any { it > 0.0 },
                            // 先無個案存檔、之後才選個案時要把 patientId 一起補上,
                            // 否則刪病患的 CASCADE 帶不走這筆,會留下孤兒紀錄
                            patientId = case?.patientId ?: exist.patientId,
                            isPatientIdentified = (case?.patientId ?: exist.patientId) != null,
                            caseId = case?.id ?: exist.caseId, wdCode = case?.wdCode ?: exist.wdCode,
                            imageId = lastImageId ?: exist.imageId, mmPerPx = lastMmPerPx ?: exist.mmPerPx,
                            route = lastRoute ?: exist.route, source = source ?: exist.source,
                            // 修邊後再存 → 輪廓要更新(這正是補送標註要用的 GT)
                            gtPolygon = polyJson ?: exist.gtPolygon,
                            imageW = (lastImageW.takeIf { it > 0 }) ?: exist.imageW,
                            imageH = (lastImageH.takeIf { it > 0 }) ?: exist.imageH,
                            exudate = exudate ?: exist.exudate,
                            correctionIou = lastCorrectionIou ?: exist.correctionIou,
                            // 只增不減:同一筆若曾經確認過,重存(未再修邊)不應把它退回未確認
                            doctorVerified = lastDoctorVerified || exist.doctorVerified,
                            // ⚠ 影像**一律以本次畫布重存**,絕不沿用舊檔。
                            //
                            // 舊作法是「已有檔就沿用、刪掉剛存的」,看起來省事,但同一張照片先走端上
                            // (畫布＝相機原圖,可能 4000px)、再切後端(畫布＝work ≤2048px)時:
                            // quickHash 比對的是**原圖**,兩次相同 → lastSavedId 保留 → 走這條 update 分支
                            // → 檔案還是 4000px 的原圖,而下面的 gtPolygon/imageW/imageH 已更新成 2048 空間。
                            // 後果:回頭修邊時輪廓縮在左上角(醫師只會覺得「AI 框錯了」,不會想到是座標空間),
                            // 而重算面積是拿 2048 空間的 mmPerPx 去乘 4000 空間的像素數 → 高估 (4000/2048)²≈3.8 倍,
                            // 且這個錯誤會**靜默**寫進 estimatedArea 汙染癒合趨勢。
                            // 多存一份幾百 KB 的密文,換掉整類「圖與輪廓不同空間」的錯誤,這筆帳很划算。
                            imagePath = imgName ?: exist.imagePath,
                            // 柵格與 meta 必須成對更新。只更新其中一個，座標就會對到錯的影像。
                            rasterPath = rasterName ?: exist.rasterPath,
                            rasterMeta = if (rasterName != null) rasterPair?.second else exist.rasterMeta,
                            // v5 組織比例。**update 分支曾經整組漏掉**——insert 有、update 沒有，
                            // 於是「修邊後再存一次」會把新的組織比例丟掉，時間軸卡片與趨勢圖
                            // 顯示的是第一次量測的值。這種漏欄位不會報錯，也不會有任何徵兆。
                            tissueGranulation = r.tissueFrac["granulation"] ?: exist.tissueGranulation,
                            tissueSlough = r.tissueFrac["slough"] ?: exist.tissueSlough,
                            tissueNecrosis = r.tissueFrac["necrosis"] ?: exist.tissueNecrosis,
                            tissueEpithelial = r.tissueFrac["epithelial"] ?: exist.tissueEpithelial,
                            tissueOther = r.tissueFrac["other"] ?: exist.tissueOther
                        ))
                        // 先寫 DB 再刪舊檔:順序反過來的話,update 失敗就會留下指向不存在檔案的死路徑。
                        if (imgName != null && exist.imagePath.isNotEmpty() && exist.imagePath != imgName) {
                            imageStore?.delete(exist.imagePath)
                        }
                        // 同上：先寫 DB 再刪舊檔。反過來的話 update 失敗就留下死路徑。
                        if (rasterName != null && !exist.rasterPath.isNullOrEmpty()
                            && exist.rasterPath != rasterName) {
                            imageStore?.delete(exist.rasterPath)
                        }
                        Pair(exist.id, true)
                    } else {
                        val nid = dao.insertMeasurement(MeasurementEntity(
                            patientId = case?.patientId,
                            timestamp = Date(),
                            hasWound = (r.areaCm2 ?: 0.0) > 0.0 || r.tissueFrac.values.any { it > 0.0 },
                            confidence = r.confidence,
                            estimatedArea = r.areaCm2,
                            estimatedVolume = null,
                            woundType = "AI(${r.route})",
                            quality = "backend",
                            processingTime = 0L,
                            imagePath = imgName ?: "",
                            rasterPath = rasterName,
                            rasterMeta = rasterPair?.second,
                            dataPath = "",
                            notes = notes,
                            // 綁定個案與雲端影像:本機病歷與飛輪樣本靠 wdCode/imageId 對得起來
                            caseId = case?.id, wdCode = case?.wdCode,
                            imageId = lastImageId, mmPerPx = lastMmPerPx,
                            route = lastRoute, source = source,
                            gtPolygon = polyJson,
                            imageW = lastImageW.takeIf { it > 0 },
                            imageH = lastImageH.takeIf { it > 0 },
                            exudate = exudate,
                            correctionIou = lastCorrectionIou,
                            doctorVerified = lastDoctorVerified,
                            // v5：組織比例存在量測當下。原始影像會依 90 天政策清除，
                            // 沒有現在存下來就永遠補不回來（見 MeasurementEntity 的說明）。
                            tissueGranulation = r.tissueFrac["granulation"],
                            tissueSlough = r.tissueFrac["slough"],
                            tissueNecrosis = r.tissueFrac["necrosis"],
                            tissueEpithelial = r.tissueFrac["epithelial"],
                            tissueOther = r.tissueFrac["other"]
                        ))
                        Pair(nid, false)
                    }
                }
                lastSavedId = id
                _state.value = _state.value.copy(
                    saved = true,
                    submitStatus = if (updatedRow) "ℹ️ 同一次影像→已更新同筆紀錄(#$id),未重複新增"
                                   else "✅ 已存入個案時間軸(#$id)"
                )
            } catch (e: Exception) {
                _state.value = _state.value.copy(submitStatus = "⚠️ 存入失敗:${e.message}")
            }
        }
    }
}

private fun Bitmap.toJpeg(quality: Int = 95): ByteArray =
    ByteArrayOutputStream().use { bos -> compress(Bitmap.CompressFormat.JPEG, quality, bos); bos.toByteArray() }
