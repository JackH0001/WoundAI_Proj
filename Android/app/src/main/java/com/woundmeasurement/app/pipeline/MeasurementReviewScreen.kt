package com.woundmeasurement.app.pipeline

import android.graphics.Bitmap
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.activity.compose.BackHandler
import com.woundmeasurement.app.data.database.WoundMeasurementDatabase
import com.woundmeasurement.app.data.entity.MeasurementEntity
import com.woundmeasurement.app.data.repo.CaseRepository
import com.woundmeasurement.app.data.store.LocalImageStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray

/**
 * 從時間軸回頭檢視單筆紀錄：**重新修邊** 與 **補送訓練標註**，都不必重測一遍。
 *
 * 這解決的問題：v2 的時間軸只存面積與 `imageId`，沒存 GT 輪廓，也沒存影像
 * （`imagePath` 一直是空字串）→ 病患事後才簽②訓練同意時，醫師得整個重新拍照量測。
 * v3 起輪廓（`gtPolygon`）與加密影像都落地，這頁就能直接把它們載回來。
 *
 * 補送標註**不需要重新上傳影像**：後端靠 `imageId` 就找得到當初 classify 存下的那張。
 */
@Composable
fun MeasurementReviewScreen(
    record: MeasurementEntity,
    backendBaseUrl: String = "http://10.0.2.2:5000",
    onBack: () -> Unit
) {
    val ctx = LocalContext.current
    val db = remember { WoundMeasurementDatabase.getDatabase(ctx) }
    val dao = remember { db.measurementDao() }
    val repo = remember { CaseRepository.from(db) }
    val store = remember { LocalImageStore(ctx) }
    val backend = remember { BackendClient(backendBaseUrl) }
    val scope = rememberCoroutineScope()

    var bmp by remember { mutableStateOf<Bitmap?>(null) }
    var loading by remember { mutableStateOf(true) }
    var editing by remember { mutableStateOf(false) }
    var msg by remember { mutableStateOf<String?>(null) }
    var cur by remember { mutableStateOf(record) }
    var trainOk by remember { mutableStateOf(false) }
    var loggedIn by remember { mutableStateOf(false) }

    // 輪廓 JSON → List<List<Int>>（座標空間＝imageW×imageH，也就是存下來的那張 work 影像）
    val poly = remember(cur.gtPolygon) {
        val out = ArrayList<List<Int>>()
        runCatching {
            cur.gtPolygon?.let { js ->
                val a = JSONArray(js)
                for (i in 0 until a.length()) {
                    val p = a.getJSONArray(i); out.add(listOf(p.getInt(0), p.getInt(1)))
                }
            }
        }
        out
    }

    LaunchedEffect(cur.id) {
        loading = true
        bmp = withContext(Dispatchers.IO) { runCatching { store.loadFull(cur.imagePath) }.getOrNull() }
        // 訓練同意要即時查(病患可能剛簽或剛撤回),不能用進來時的快照
        val cid = cur.caseId
        trainOk = if (cid != null) withContext(Dispatchers.IO) {
            runCatching {
                repo.getCase(cid)?.let { repo.canSubmitTraining(it.patientId) } ?: false
            }.getOrDefault(false)
        } else false
        loggedIn = withContext(Dispatchers.IO) {
            runCatching { backend.login("admin", "woundai-admin") }.getOrDefault(false)
        }
        loading = false
    }

    BackHandler(enabled = editing) { editing = false }

    val b = bmp
    if (editing && b != null) {
        WoundEditScreen(
            bitmap = b,
            initialPolygon = poly,
            originalArea = cur.estimatedArea,
            tissueFrac = emptyMap(),
            exudate = cur.exudate,
            mmPerPx = cur.mmPerPx,
            resume = null,          // 從 DB 載回時沒有柵格快照,由 polygon 重建
            onCancel = { editing = false },
            onDone = { newPoly, iou, newArea, _, _ ->
                scope.launch {
                    val js = newPoly.joinToString(",", "[", "]") {
                        "[${it.getOrElse(0) { 0 }},${it.getOrElse(1) { 0 }}]"
                    }
                    val updated = cur.copy(
                        gtPolygon = js,
                        estimatedArea = newArea ?: cur.estimatedArea,
                        correctionIou = iou ?: cur.correctionIou,
                        // 輪廓改了就不能再算「已送出」——要重新送才會讓雲端拿到新 GT
                        annotationSubmitted = false
                    )
                    runCatching { withContext(Dispatchers.IO) { dao.updateMeasurement(updated) } }
                        .onSuccess { cur = updated; msg = "✅ 已更新此筆紀錄的輪廓與面積" }
                        .onFailure { msg = "⚠ 更新失敗：${it.message}" }
                    editing = false
                }
            }
        )
        return
    }

    Column(
        Modifier.fillMaxSize().padding(16.dp).verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(10.dp)
    ) {
        Text("紀錄檢視 / 補送標註", style = MaterialTheme.typography.titleLarge)
        Text("代碼 ${cur.wdCode ?: "—"}・面積 ${cur.estimatedArea?.let { "%.2f cm²".format(it) } ?: "未校正"}" +
             "・滲液 ${cur.exudate ?: "—"}",
            style = MaterialTheme.typography.bodyMedium)
        msg?.let { Text(it, color = MaterialTheme.colorScheme.primary) }

        if (loading) Text("載入影像中…", style = MaterialTheme.typography.bodySmall)

        val noImage = !loading && bmp == null
        if (noImage) Text(
            // v3 之前的舊紀錄沒有影像；或保存期限清理已把影像刪掉（面積與趨勢仍保留）
            "⚠ 此筆沒有本機影像（可能是舊紀錄，或已逾保存期限清理）。無法重新修邊；" +
            if (cur.gtPolygon != null && cur.imageId != null) "但輪廓與影像綁定都在，仍可補送標註。"
            else "也缺輪廓或影像綁定，無法補送標註。",
            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)

        Button(
            onClick = { editing = true },
            enabled = bmp != null,
            modifier = Modifier.fillMaxWidth()
        ) { Text("重新修邊（載回原影像與輪廓）") }

        Divider()
        Text("補送訓練標註", style = MaterialTheme.typography.titleSmall)
        Text(
            when {
                cur.annotationSubmitted -> "此筆已送出過訓練標註。重新修邊後可再送（雲端會視為醫師修訂版）。"
                cur.gtPolygon == null -> "⚠ 此筆沒有 GT 輪廓，不能補送（請先重新修邊）。"
                cur.imageId == null -> "⚠ 此筆沒有後端影像綁定（當初可能走端上模式），不能補送。"
                !trainOk -> "⚠ 此病患未取得②訓練同意（或已撤回），不得送出。"
                !loggedIn -> "⚠ 後端未連線，無法送出。"
                else -> "可補送：後端靠 image_id 就找得到當初的影像，**不需要重新上傳或重測**。"
            },
            style = MaterialTheme.typography.bodySmall,
            color = if (cur.gtPolygon != null && cur.imageId != null && trainOk && loggedIn)
                MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.error
        )
        Button(
            onClick = {
                scope.launch {
                    val res = runCatching {
                        withContext(Dispatchers.IO) {
                            backend.submitAnnotation(
                                code = cur.wdCode!!, gtPolygon = poly, exudate = cur.exudate,
                                imageId = cur.imageId, imageW = cur.imageW ?: 0, imageH = cur.imageH ?: 0,
                                mmPerPx = cur.mmPerPx, route = cur.route, segModel = null,
                                correctionIou = cur.correctionIou, careNote = "resubmit from timeline",
                                source = cur.source ?: "clinical", consentTrain = true
                            )
                        }
                    }
                    res.onSuccess { (ok, m) ->
                        if (ok) {
                            runCatching { withContext(Dispatchers.IO) { dao.markAnnotationSubmitted(cur.id) } }
                            cur = cur.copy(annotationSubmitted = true)
                            msg = if (m.contains("duplicate")) "ℹ️ 相同影像的相同遮罩已在佇列（去重）"
                                  else "✅ 已補送訓練標註（${cur.wdCode}）"
                        } else msg = "⚠ 被守門擋下：$m"
                    }.onFailure { msg = "⚠ 送出失敗：${it.message}" }
                }
            },
            enabled = !cur.annotationSubmitted && cur.gtPolygon != null && cur.imageId != null &&
                      cur.wdCode != null && trainOk && loggedIn,
            modifier = Modifier.fillMaxWidth()
        ) { Text("補送訓練標註 → 再訓練佇列") }

        Divider()
        OutlinedButton(onBack, Modifier.fillMaxWidth()) { Text("返回時間軸") }
    }
}
