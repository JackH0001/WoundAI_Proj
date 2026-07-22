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
    // 修邊遮罩持久化(同影像再進修邊→原樣續編,免多邊形往返損耗;換影像清除)
    @Volatile var editRaster: EditRaster? = null

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
        cmPerPixel: Double? = null
    ) {
        _state.value = _state.value.copy(loading = true, error = null)
        viewModelScope.launch {
            try {
                // 長邊縮到 ≤2048:模型輸入僅256、ArUco 於2048仍清晰、比例法尺度不變;
                // 記憶體(5712寬原圖≈70MB ARGB)與上傳大減,避免反覆編修 OOM 閃退
                val work = withContext(Dispatchers.Default) {
                    val mx = maxOf(bitmap.width, bitmap.height)
                    if (mx > 2048) {
                        val s = 2048f / mx
                        Bitmap.createScaledBitmap(bitmap, (bitmap.width * s).toInt(), (bitmap.height * s).toInt(), true)
                    } else bitmap
                }
                var polyCap: List<List<Int>> = emptyList()
                val r = withContext(Dispatchers.IO) {
                    val jpeg = work.toJpeg()
                    val c = backend.classify(jpeg, cmPerPixel)
                    polyCap = c.woundPolygon
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
                lastPolygon = polyCap
                lastBitmap = work          // 編輯/顯示一律用縮圖(polygon 座標即此圖座標)
                lastCorrectionIou = null   // 新分析→重置修邊修正量
                val hh = quickHash(work)
                if (hh != lastImageHash) { lastSavedId = null; editRaster = null }  // 換影像→重置去重id與修邊遮罩
                lastImageHash = hh
                _state.value = MeasureUiState(loading = false, result = r)
            } catch (e: Exception) {
                _state.value = MeasureUiState(loading = false, error = e.message ?: "後端分析失敗")
            }
        }
    }

    /**
     * 醫師確認・送出訓練標註(飛輪閉環)：以後端回傳(或修邊後)的傷口輪廓為 GT → POST /api/v1/annotation。
     * 送 doctor_verified/deidentified/consent_train=true;後端守門不合則回訊息。
     * @param code 去識別代碼(WD-*);@param exudate 醫師輸入滲液 0–3
     */
    fun submitAnnotation(
        backend: BackendClient, code: String, exudate: Int?, careNote: String? = null
    ) {
        val poly = lastPolygon
        if (poly.isEmpty()) {
            _state.value = _state.value.copy(submitStatus = "⚠️ 無傷口輪廓可送(請先量測)"); return
        }
        _state.value = _state.value.copy(submitStatus = "送出中…")
        viewModelScope.launch {
            try {
                val (ok, msg) = withContext(Dispatchers.IO) {
                    backend.submitAnnotation(code, poly, exudate, correctionIou = lastCorrectionIou, careNote = careNote)
                }
                _state.value = _state.value.copy(
                    submitStatus = when {
                        !ok -> "⚠️ 被守門擋下:$msg"
                        msg.contains("duplicate") -> "ℹ️ 相同傷口遮罩已在佇列,已自動略過(去重)"
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
     * 存入個案時間軸(本機 Room/SQLite)。一般量測 patientId=null。
     * 同一次影像去重:同影像(雜湊相同)重存/修邊後再存 → 更新同一筆,不重複新增(避免時間軸資料誤差)。
     */
    fun saveToTimeline(dao: MeasurementDao, exudate: Int?) {
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
                            hasWound = (r.areaCm2 ?: 0.0) > 0.0 || r.tissueFrac.values.any { it > 0.0 }
                        ))
                        Pair(exist.id, true)
                    } else {
                        val nid = dao.insertMeasurement(MeasurementEntity(
                            patientId = null,
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
                            notes = notes
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
