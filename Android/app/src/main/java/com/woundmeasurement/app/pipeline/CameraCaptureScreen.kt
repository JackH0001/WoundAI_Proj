package com.woundmeasurement.app.pipeline

import android.graphics.Bitmap
import android.graphics.Matrix
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.foundation.layout.*
import androidx.compose.material3.Button
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat

/**
 * CameraX 高解析拍攝(Compose)：預覽 + 全解析擷取 → Bitmap(已修正旋轉) → [MeasureViewModel].analyze。
 * 取代 TakePicturePreview 縮圖,供實拍量測(校正貼紙需清晰可見)。
 * 需求：build.gradle 加 androidx.camera(core/camera2/lifecycle/view);Manifest 加 CAMERA 權限並於執行期請求。
 */
@Composable
fun CameraCaptureScreen(vm: MeasureViewModel) {
    val ctx = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current
    val imageCapture = remember {
        ImageCapture.Builder().setCaptureMode(ImageCapture.CAPTURE_MODE_MAXIMIZE_QUALITY).build()
    }

    Box(Modifier.fillMaxSize()) {
        AndroidView(
            modifier = Modifier.fillMaxSize(),
            factory = { c ->
                val pv = PreviewView(c)
                val future = ProcessCameraProvider.getInstance(c)
                future.addListener({
                    val provider = future.get()
                    val preview = Preview.Builder().build().also { it.setSurfaceProvider(pv.surfaceProvider) }
                    provider.unbindAll()
                    provider.bindToLifecycle(
                        lifecycleOwner, CameraSelector.DEFAULT_BACK_CAMERA, preview, imageCapture
                    )
                }, ContextCompat.getMainExecutor(c))
                pv
            }
        )
        Button(
            onClick = {
                imageCapture.takePicture(
                    ContextCompat.getMainExecutor(ctx),
                    object : ImageCapture.OnImageCapturedCallback() {
                        override fun onCaptureSuccess(image: ImageProxy) {
                            val bmp = image.toBitmap()                       // CameraX 1.3+
                            val rot = image.imageInfo.rotationDegrees
                            image.close()
                            val fixed = if (rot != 0) rotate(bmp, rot) else bmp
                            vm.analyze(bitmap = fixed, exudate = null)
                        }
                        override fun onError(exc: ImageCaptureException) { /* TODO 顯示錯誤 */ }
                    }
                )
            },
            modifier = Modifier.align(Alignment.BottomCenter).padding(24.dp)
        ) { Text("拍攝") }
    }
}

private fun rotate(b: Bitmap, deg: Int): Bitmap =
    Bitmap.createBitmap(b, 0, 0, b.width, b.height, Matrix().apply { postRotate(deg.toFloat()) }, true)
