package com.woundmeasurement.app.camera

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.ImageFormat
import android.graphics.Matrix
import android.hardware.camera2.*
import android.media.Image
import android.media.ImageReader
import android.os.Handler
import android.os.HandlerThread
import android.util.Log
import android.util.Size
import android.view.Surface
import android.graphics.SurfaceTexture
import androidx.core.content.ContextCompat
import kotlinx.coroutines.suspendCancellableCoroutine
import java.io.ByteArrayOutputStream
import java.util.concurrent.Semaphore
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/**
 * 進階相機模組 - 使用 Camera2 API
 * 提供高品質影像捕捉和即時品質評估
 */
class AdvancedCameraModule(private val context: Context) {
    
    companion object {
        private const val TAG = "AdvancedCameraModule"
        private const val MAX_PREVIEW_WIDTH = 1920
        private const val MAX_PREVIEW_HEIGHT = 1080
        private const val CAPTURE_WIDTH = 2048
        private const val CAPTURE_HEIGHT = 1536
    }

    private val cameraManager: CameraManager = context.getSystemService(Context.CAMERA_SERVICE) as CameraManager
    private var cameraDevice: CameraDevice? = null
    private var captureSession: CameraCaptureSession? = null
    private var imageReader: ImageReader? = null
    private var backgroundThread: HandlerThread? = null
    private var backgroundHandler: Handler? = null
    
    private val cameraOpenCloseLock = Semaphore(1)
    private var cameraId: String = ""
    private lateinit var previewSize: Size
    private lateinit var captureSize: Size
    
    // 品質評估器
    private val qualityAssessor = ImageQualityAssessor()
    
    /**
     * 初始化相機模組
     */
    suspend fun initialize(): Boolean {
        return try {
            startBackgroundThread()
            setupCamera()
            true
        } catch (e: Exception) {
            Log.e(TAG, "初始化相機模組失敗", e)
            false
        }
    }

    /**
     * 設定相機參數
     */
    private fun setupCamera() {
        try {
            for (id in cameraManager.cameraIdList) {
                val characteristics = cameraManager.getCameraCharacteristics(id)
                val facing = characteristics.get(CameraCharacteristics.LENS_FACING)
                
                // 優先使用後置相機
                if (facing != null && facing == CameraCharacteristics.LENS_FACING_BACK) {
                    cameraId = id
                    
                    val map = characteristics.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP)
                    
                    // 設定預覽尺寸
                    previewSize = chooseOptimalSize(
                        map?.getOutputSizes(SurfaceTexture::class.java) ?: arrayOf(),
                        MAX_PREVIEW_WIDTH, MAX_PREVIEW_HEIGHT
                    )
                    
                    // 設定拍攝尺寸  
                    captureSize = chooseOptimalSize(
                        map?.getOutputSizes(ImageFormat.JPEG) ?: arrayOf(),
                        CAPTURE_WIDTH, CAPTURE_HEIGHT
                    )
                    
                    Log.d(TAG, "選擇相機 $cameraId, 預覽尺寸: $previewSize, 拍攝尺寸: $captureSize")
                    return
                }
            }
        } catch (e: CameraAccessException) {
            Log.e(TAG, "設定相機時發生錯誤", e)
        }
    }

    /**
     * 選擇最佳尺寸
     */
    private fun chooseOptimalSize(choices: Array<Size>, textureViewWidth: Int, textureViewHeight: Int): Size {
        val bigEnough = mutableListOf<Size>()
        val notBigEnough = mutableListOf<Size>()
        
        val w = textureViewWidth
        val h = textureViewHeight
        
        for (option in choices) {
            if (option.width <= w && option.height <= h) {
                if (option.width >= textureViewWidth && option.height >= textureViewHeight) {
                    bigEnough.add(option)
                } else {
                    notBigEnough.add(option)
                }
            }
        }
        
        return when {
            bigEnough.isNotEmpty() -> bigEnough.minByOrNull { it.width * it.height } ?: choices[0]
            notBigEnough.isNotEmpty() -> notBigEnough.maxByOrNull { it.width * it.height } ?: choices[0]
            else -> choices[0]
        }
    }

    /**
     * 開啟相機裝置
     */
    suspend fun openCamera(): Boolean {
        return suspendCancellableCoroutine { continuation ->
            try {
                if (ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) 
                    != PackageManager.PERMISSION_GRANTED) {
                    continuation.resumeWithException(SecurityException("缺少相機權限"))
                    return@suspendCancellableCoroutine
                }

                if (!cameraOpenCloseLock.tryAcquire()) {
                    continuation.resumeWithException(RuntimeException("相機正在使用中"))
                    return@suspendCancellableCoroutine
                }

                val stateCallback = object : CameraDevice.StateCallback() {
                    override fun onOpened(camera: CameraDevice) {
                        cameraOpenCloseLock.release()
                        cameraDevice = camera
                        Log.d(TAG, "相機裝置已開啟")
                        continuation.resume(true)
                    }

                    override fun onDisconnected(camera: CameraDevice) {
                        cameraOpenCloseLock.release()
                        camera.close()
                        cameraDevice = null
                        Log.w(TAG, "相機裝置連接中斷")
                        continuation.resume(false)
                    }

                    override fun onError(camera: CameraDevice, error: Int) {
                        cameraOpenCloseLock.release()
                        camera.close()
                        cameraDevice = null
                        Log.e(TAG, "相機裝置錯誤: $error")
                        continuation.resumeWithException(RuntimeException("相機錯誤: $error"))
                    }
                }

                cameraManager.openCamera(cameraId, stateCallback, backgroundHandler)
                
            } catch (e: CameraAccessException) {
                cameraOpenCloseLock.release()
                continuation.resumeWithException(e)
            }
        }
    }

    /**
     * 開始預覽
     */
    suspend fun startPreview(surface: Surface): Boolean {
        return try {
            val device = cameraDevice ?: return false
            
            imageReader = ImageReader.newInstance(captureSize.width, captureSize.height, ImageFormat.JPEG, 1)
            
            val outputs = listOf(surface, imageReader!!.surface)
            
            suspendCancellableCoroutine { continuation ->
                device.createCaptureSession(
                    outputs,
                    object : CameraCaptureSession.StateCallback() {
                        override fun onConfigured(session: CameraCaptureSession) {
                            captureSession = session
                            
                            try {
                                val previewRequestBuilder = device.createCaptureRequest(CameraDevice.TEMPLATE_PREVIEW)
                                previewRequestBuilder.addTarget(surface)
                                
                                // 設定自動對焦
                                previewRequestBuilder.set(
                                    CaptureRequest.CONTROL_AF_MODE, 
                                    CaptureRequest.CONTROL_AF_MODE_CONTINUOUS_PICTURE
                                )
                                
                                // 設定自動曝光
                                previewRequestBuilder.set(
                                    CaptureRequest.CONTROL_AE_MODE,
                                    CaptureRequest.CONTROL_AE_MODE_ON_AUTO_FLASH
                                )
                                
                                // 設定自動白平衡
                                previewRequestBuilder.set(
                                    CaptureRequest.CONTROL_AWB_MODE,
                                    CaptureRequest.CONTROL_AWB_MODE_AUTO
                                )
                                
                                val previewRequest = previewRequestBuilder.build()
                                session.setRepeatingRequest(previewRequest, null, backgroundHandler)
                                
                                Log.d(TAG, "預覽已開始")
                                continuation.resume(true)
                                
                            } catch (e: CameraAccessException) {
                                Log.e(TAG, "開始預覽失敗", e)
                                continuation.resume(false)
                            }
                        }

                        override fun onConfigureFailed(session: CameraCaptureSession) {
                            Log.e(TAG, "配置相機會話失敗")
                            continuation.resume(false)
                        }
                    },
                    backgroundHandler
                )
            }
        } catch (e: Exception) {
            Log.e(TAG, "開始預覽時發生錯誤", e)
            false
        }
    }

    /**
     * 拍攝高品質照片
     */
    suspend fun captureHighQualityPhoto(): CaptureResult? {
        return suspendCancellableCoroutine { continuation ->
            try {
                val device = cameraDevice
                val session = captureSession
                val reader = imageReader
                
                if (device == null || session == null || reader == null) {
                    continuation.resume(null)
                    return@suspendCancellableCoroutine
                }

                // 設定圖像讀取監聽
                reader.setOnImageAvailableListener({ reader ->
                    val image = reader.acquireLatestImage()
                    try {
                        val buffer = image.planes[0].buffer
                        val bytes = ByteArray(buffer.remaining())
                        buffer.get(bytes)
                        
                        // 轉換為 Bitmap
                        val bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
                        val rotatedBitmap = rotateBitmap(bitmap, 90f) // 根據需要調整旋轉角度
                        
                        // 進行品質評估
                        val qualityScore = qualityAssessor.assessImageQuality(rotatedBitmap)
                        
                        val result = CaptureResult(
                            bitmap = rotatedBitmap,
                            qualityScore = qualityScore,
                            timestamp = System.currentTimeMillis(),
                            width = rotatedBitmap.width,
                            height = rotatedBitmap.height
                        )
                        
                        Log.d(TAG, "照片拍攝完成，品質分數: ${qualityScore.overallScore}")
                        continuation.resume(result)
                        
                    } catch (e: Exception) {
                        Log.e(TAG, "處理拍攝影像時發生錯誤", e)
                        continuation.resume(null)
                    } finally {
                        image.close()
                    }
                }, backgroundHandler)

                // 建立拍攝請求
                val captureBuilder = device.createCaptureRequest(CameraDevice.TEMPLATE_STILL_CAPTURE)
                captureBuilder.addTarget(reader.surface)
                
                // 設定高品質拍攝參數
                captureBuilder.set(CaptureRequest.CONTROL_AF_MODE, CaptureRequest.CONTROL_AF_MODE_CONTINUOUS_PICTURE)
                captureBuilder.set(CaptureRequest.CONTROL_AE_MODE, CaptureRequest.CONTROL_AE_MODE_ON_AUTO_FLASH)
                captureBuilder.set(CaptureRequest.JPEG_QUALITY, 95.toByte()) // 高品質 JPEG
                
                // 執行拍攝
                session.capture(captureBuilder.build(), object : CameraCaptureSession.CaptureCallback() {
                    override fun onCaptureCompleted(
                        session: CameraCaptureSession,
                        request: CaptureRequest,
                        result: TotalCaptureResult
                    ) {
                        Log.d(TAG, "拍攝請求完成")
                    }
                    
                    override fun onCaptureFailed(
                        session: CameraCaptureSession,
                        request: CaptureRequest,
                        failure: CaptureFailure
                    ) {
                        Log.e(TAG, "拍攝失敗: ${failure.reason}")
                        continuation.resume(null)
                    }
                }, backgroundHandler)
                
            } catch (e: CameraAccessException) {
                Log.e(TAG, "拍攝照片時發生錯誤", e)
                continuation.resume(null)
            }
        }
    }

    /**
     * 旋轉 Bitmap
     */
    private fun rotateBitmap(source: Bitmap, degrees: Float): Bitmap {
        val matrix = Matrix()
        matrix.postRotate(degrees)
        return Bitmap.createBitmap(source, 0, 0, source.width, source.height, matrix, true)
    }

    /**
     * 關閉相機
     */
    fun closeCamera() {
        try {
            cameraOpenCloseLock.acquire()
            captureSession?.close()
            captureSession = null
            cameraDevice?.close()
            cameraDevice = null
            imageReader?.close()
            imageReader = null
        } catch (e: InterruptedException) {
            Log.e(TAG, "關閉相機時被中斷", e)
        } finally {
            cameraOpenCloseLock.release()
        }
    }

    /**
     * 開始背景執行緒
     */
    private fun startBackgroundThread() {
        backgroundThread = HandlerThread("CameraBackground").also { it.start() }
        backgroundHandler = Handler(backgroundThread!!.looper)
    }

    /**
     * 停止背景執行緒  
     */
    private fun stopBackgroundThread() {
        backgroundThread?.quitSafely()
        try {
            backgroundThread?.join()
            backgroundThread = null
            backgroundHandler = null
        } catch (e: InterruptedException) {
            Log.e(TAG, "停止背景執行緒時發生錯誤", e)
        }
    }

    /**
     * 釋放資源
     */
    fun release() {
        closeCamera()
        stopBackgroundThread()
    }
}

/**
 * 拍攝結果資料類
 */
data class CaptureResult(
    val bitmap: Bitmap,
    val qualityScore: ImageQualityScore,
    val timestamp: Long,
    val width: Int,
    val height: Int
)