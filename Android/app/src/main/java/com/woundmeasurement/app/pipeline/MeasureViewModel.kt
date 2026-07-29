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
    // 最近分析的原圖(供修邊畫布顯示)
    @Volatile var lastBitmap: Bitmap? = null
        private set
    // 醫師修邊後與原始遮罩的 IoU(修正幅度;1.0=未改),隨標註送出
    @Volatile var lastCorrectionIou: Double? = null
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
     * 換一張影像時清空所有「上一次分析」的殘留。
     *
     * 沒有這個重置會出人命等級的錯:後端模式跑完 A 圖後切端上模式跑 B 圖,
     * lastImageId/lastBitmap/lastPolygon 仍指向 A → 修邊畫面開的是 A 圖、
     * 送出的標註綁 A 的 image_id、時間軸還會用 lastSavedId 覆寫 A 那筆。
     * 端上路徑(analyze)沒有後端影像綁定,故 imageId 一律為 null,送標註時會被守門擋下。
     */
    private fun clearBackendBinding(alsoBitmap: Boolean = false) {
        lastPolygon = emptyList()
        lastCorrectionIou = null
        lastMmPerPx = null
        lastImageId = null; lastImageW = 0; lastImageH = 0
        lastRoute = null; lastSegModel = null; lastPhantomHint = false
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
                var mmCap: Double? = null
                var idCap: String? = null; var wCap = 0; var hCap = 0
                var routeCap: String? = null; var modelCap: String? = null
                var hintCap = false
                val r = withContext(Dispatchers.IO) {
                    val jpeg = work.toJpeg()
                    val c = backend.classify(jpeg, cppWork, seg)
                    polyCap = c.woundPolygon
                    mmCap = c.mmPerPx
                    idCap = c.imageId; wCap = c.imageW; hCap = c.imageH
                    routeCap = c.route; modelCap = c.segModel; hintCap = c.phantomHint
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
                lastPolygon = polyCap
                lastCorrectionIou = null   // 新分析→重置修邊修正量
                lastMmPerPx = mmCap
                lastImageId = idCap; lastImageW = wCap; lastImageH = hCap
                lastRoute = routeCap; lastSegModel = modelCap; lastPhantomHint = hintCap
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
                        imageId = lastImageId, imageW = lastImageW, imageH = lastImageH,
                        mmPerPx = lastMmPerPx, route = lastRoute, segModel = lastSegModel,
                        correctionIou = lastCorrectionIou, careNote = careNote, source = source,
                        consentTrain = consentTrain
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
        tissue: Map<String, Double>? = null
    ) {
        lastPolygon = edited
        lastCorrectionIou = correctionIou
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
        source: String? = null
    ) {
        val r = _state.value.result ?: return
        _state.value = _state.value.copy(submitStatus = "存入時間軸中…")
        viewModelScope.launch {
            try {
                fun pct(k: String) = ((r.tissueFrac[k] ?: 0.0) * 100).toInt()
                val notes = "PUSH ${r.push.partial ?: "-"}; 肉芽${pct("granulation")}% 腐肉${pct("slough")}% 壞死${pct("necrosis")}%; 滲液${exudate ?: "-"}; route ${r.route}" +
                        (lastCorrectionIou?.let { "; 修邊IoU %.2f".format(it) } ?: "")
                val (id, updatedRow) = withContext(Dispatchers.IO) {
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
                            route = lastRoute ?: exist.route, source = source ?: exist.source
                        ))
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
                            imagePath = "",
                            dataPath = "",
                            notes = notes,
                            // 綁定個案與雲端影像:本機病歷與飛輪樣本靠 wdCode/imageId 對得起來
                            caseId = case?.id, wdCode = case?.wdCode,
                            imageId = lastImageId, mmPerPx = lastMmPerPx,
                            route = lastRoute, source = source
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
