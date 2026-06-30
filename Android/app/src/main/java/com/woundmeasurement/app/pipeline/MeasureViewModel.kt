package com.woundmeasurement.app.pipeline

import android.graphics.Bitmap
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
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
    val error: String? = null
)

class MeasureViewModel(
    private val analyzer: WoundAnalyzer,
    private val aruco: ArucoDetector? = null
) : ViewModel() {

    private val _state = MutableStateFlow(MeasureUiState())
    val state: StateFlow<MeasureUiState> = _state.asStateFlow()

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
                val r = withContext(Dispatchers.IO) {
                    val jpeg = bitmap.toJpeg()
                    val c = backend.classify(jpeg, cmPerPixel)
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
                _state.value = MeasureUiState(loading = false, result = r)
            } catch (e: Exception) {
                _state.value = MeasureUiState(loading = false, error = e.message ?: "後端分析失敗")
            }
        }
    }
}

private fun Bitmap.toJpeg(quality: Int = 95): ByteArray =
    ByteArrayOutputStream().use { bos -> compress(Bitmap.CompressFormat.JPEG, quality, bos); bos.toByteArray() }
