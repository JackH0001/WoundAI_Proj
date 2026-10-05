package com.woundmeasurement.app.pipeline

import android.graphics.Bitmap
import android.graphics.Matrix
import android.util.Log
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.core.resolutionselector.AspectRatioStrategy
import androidx.camera.core.resolutionselector.ResolutionSelector
import androidx.camera.core.resolutionselector.ResolutionStrategy
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.foundation.layout.*
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat

private const val TAG = "CameraCapture"

/**
 * CameraX **全解析**拍攝（Compose）：預覽 → 擷取 → Bitmap（已修正旋轉）→ [onCaptured]。
 *
 * ## 為什麼一定要全解析（這不是「比較好」，是「不這樣就不成立」）
 *
 * 量測的尺度基準來自 ArUco 貼紙，而 `square_20mm_v2` 的 marker 只有 **12 mm**。
 * marker 在影像上的像素數決定了 mm/px 的解析度，進而決定整張圖的面積誤差下限。
 * 以縮圖（`ActivityResultContracts.TakePicturePreview` 的回傳值）去偵測 12mm marker，
 * 尺度基準本身就是糊的——後面不管分割多準、描邊多細，面積都不會準。
 *
 * 這就是本檔存在的原因，也是為什麼 2026-10 把 `SamplePickerScreen` 的「拍照」
 * 從 TakePicturePreview 換成這支：**影像紀錄必須以高清為基礎，醫療級精確度才談得上。**
 *
 * ## 這支刻意不呼叫 vm.analyze
 *
 * 分析前要先決定樣本來源（`phantom` 走決定性色彩分割、其餘走 AI），還要決定端上或後端。
 * 那些邏輯在 [SamplePickerScreen] 的 `dispatch()` 裡。這支若自己呼叫 `vm.analyze`，
 * 就會**繞過 phantom 的色彩分割分支**，模擬圖會被丟去 AI 模型（實測是空遮罩）。
 * 所以這裡只負責「把 Bitmap 交出去」，路由由呼叫端決定。
 *
 * ## ⚠ 記憶體
 *
 * 全解析在高階機上可能是 50 MP；`ARGB_8888` 下 50 MP ≈ **200 MB**，很接近甚至超過
 * 一般 App 的 heap 上限。這裡對 [OutOfMemoryError] 做了明確捕捉並回報，
 * 而不是讓它變成一次無訊息的閃退——真的撞到時要看得見原因，才知道該不該退一階解析度。
 *
 * 需求：build.gradle 的 androidx.camera(core/camera2/lifecycle/view)；
 *      Manifest 的 CAMERA 權限並於執行期請求（MainActivity 已處理）。
 *
 * @param onCaptured 擷取成功。Bitmap 已修正旋轉。
 * @param onCancel   使用者放棄拍攝。
 * @param onError    擷取失敗（含 OOM）。訊息給人看，不是給程式判斷用。
 */
@Composable
fun CameraCaptureScreen(
    onCaptured: (Bitmap) -> Unit,
    onCancel: () -> Unit = {},
    onError: (String) -> Unit = {},
) {
    val ctx = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current
    var capturing by remember { mutableStateOf(false) }

    val imageCapture = remember {
        // HIGHEST_AVAILABLE：要感光元件給得出的最大尺寸，不接受 CameraX 的「合理」預設。
        // 4:3 fallback auto：最大解析度通常落在 4:3（全感光元件面）；16:9 是裁切過的，
        // 會把視野邊緣切掉——而貼紙常常就放在傷口旁邊的邊緣位置。
        val selector = ResolutionSelector.Builder()
            .setAspectRatioStrategy(AspectRatioStrategy.RATIO_4_3_FALLBACK_AUTO_STRATEGY)
            .setResolutionStrategy(ResolutionStrategy.HIGHEST_AVAILABLE_STRATEGY)
            .build()
        ImageCapture.Builder()
            .setCaptureMode(ImageCapture.CAPTURE_MODE_MAXIMIZE_QUALITY)
            .setResolutionSelector(selector)
            .build()
    }

    Box(Modifier.fillMaxSize()) {
        AndroidView(
            modifier = Modifier.fillMaxSize(),
            factory = { c ->
                val pv = PreviewView(c)
                val future = ProcessCameraProvider.getInstance(c)
                future.addListener({
                    try {
                        val provider = future.get()
                        val preview = Preview.Builder().build()
                            .also { it.setSurfaceProvider(pv.surfaceProvider) }
                        provider.unbindAll()
                        provider.bindToLifecycle(
                            lifecycleOwner, CameraSelector.DEFAULT_BACK_CAMERA, preview, imageCapture
                        )
                    } catch (e: Exception) {
                        // 綁定失敗（權限未授予、相機被佔用）要說出來，
                        // 否則畫面只是一片黑，使用者會回報成「App 當掉」。
                        Log.e(TAG, "bindToLifecycle 失敗", e)
                        onError("相機無法啟動：${e.message ?: e::class.simpleName}")
                    }
                }, ContextCompat.getMainExecutor(c))
                pv
            }
        )

        Row(
            Modifier.align(Alignment.BottomCenter).padding(24.dp),
            horizontalArrangement = Arrangement.spacedBy(12.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            OutlinedButton(onClick = onCancel, enabled = !capturing) { Text("取消") }
            Button(
                enabled = !capturing,
                onClick = {
                    capturing = true
                    imageCapture.takePicture(
                        ContextCompat.getMainExecutor(ctx),
                        object : ImageCapture.OnImageCapturedCallback() {
                            override fun onCaptureSuccess(image: ImageProxy) {
                                val rot = image.imageInfo.rotationDegrees
                                val bmp = try {
                                    image.toBitmap()                     // CameraX 1.3+
                                } catch (oom: OutOfMemoryError) {
                                    // 全解析下這是真實風險，不是理論風險。明確回報，
                                    // 不要讓它變成一次看不出原因的閃退。
                                    Log.e(TAG, "toBitmap OOM", oom)
                                    null
                                } finally {
                                    image.close()
                                }
                                capturing = false
                                if (bmp == null) {
                                    onError("影像過大，記憶體不足。請回報此訊息（需調降拍攝解析度）。")
                                    return
                                }
                                val fixed = if (rot != 0) rotate(bmp, rot) else bmp
                                Log.i(TAG, "擷取 ${fixed.width}x${fixed.height} " +
                                        "(${"%.1f".format(fixed.width.toLong() * fixed.height / 1_000_000.0)} MP)")
                                onCaptured(fixed)
                            }

                            override fun onError(exc: ImageCaptureException) {
                                capturing = false
                                Log.e(TAG, "takePicture 失敗", exc)
                                onError("拍攝失敗：${exc.message ?: "未知錯誤"}")
                            }
                        }
                    )
                }
            ) { Text(if (capturing) "擷取中…" else "拍攝") }
            if (capturing) CircularProgressIndicator(Modifier.size(24.dp))
        }

        Text(
            "請讓 ArUco 校正貼紙與傷口同框，貼紙盡量平貼不要翹起",
            style = MaterialTheme.typography.bodySmall,
            modifier = Modifier.align(Alignment.TopCenter).padding(16.dp)
        )
    }
}

private fun rotate(b: Bitmap, deg: Int): Bitmap =
    Bitmap.createBitmap(b, 0, 0, b.width, b.height, Matrix().apply { postRotate(deg.toFloat()) }, true)
