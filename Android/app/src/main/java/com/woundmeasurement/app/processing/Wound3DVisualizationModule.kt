package com.woundmeasurement.app.processing

import android.content.Context
import android.graphics.Bitmap
import android.opengl.GLSurfaceView
import android.util.Log
import android.view.MotionEvent
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import javax.microedition.khronos.egl.EGLConfig
import javax.microedition.khronos.opengles.GL10
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.FloatBuffer
import java.util.*

class Wound3DVisualizationModule(private val context: Context) {
    companion object {
        private const val TAG = "Wound3DVisualization"
        private const val VERTEX_SHADER = """
            attribute vec4 aPosition;
            attribute vec4 aColor;
            varying vec4 vColor;
            uniform mat4 uMVPMatrix;
            void main() {
                gl_Position = uMVPMatrix * aPosition;
                vColor = aColor;
            }
        """
        
        private const val FRAGMENT_SHADER = """
            precision mediump float;
            varying vec4 vColor;
            void main() {
                gl_FragColor = vColor;
            }
        """
    }

    // 狀態管理
    private val _isGenerating3D = MutableStateFlow(false)
    val isGenerating3D: StateFlow<Boolean> = _isGenerating3D.asStateFlow()

    private val _currentRotationX = MutableStateFlow(0f)
    val currentRotationX: StateFlow<Float> = _currentRotationX.asStateFlow()

    private val _currentRotationY = MutableStateFlow(0f)
    val currentRotationY: StateFlow<Float> = _currentRotationY.asStateFlow()

    private val _zoomScale = MutableStateFlow(1.0f)
    val zoomScale: StateFlow<Float> = _zoomScale.asStateFlow()

    // 3D場景數據
    private var depthData: ByteArray? = null
    private var woundArea: Double = 0.0
    private var sceneView: GLSurfaceView? = null

    // 處理佇列
    private val processingScope = CoroutineScope(Dispatchers.Default + SupervisorJob())

    /**
     * 生成3D視覺化
     */
    suspend fun generate3DVisualization(
        depthData: ByteArray,
        woundArea: Double
    ): Wound3DVisualizationResult {
        return withContext(Dispatchers.Default) {
            try {
                _isGenerating3D.value = true
                Log.d(TAG, "開始生成3D視覺化，傷口面積: $woundArea cm²")

                // 驗證深度數據
                if (!validateDepthData(depthData)) {
                    throw Wound3DVisualizationError.INVALID_DEPTH_DATA
                }

                // 處理深度數據
                val processedDepthData = processDepthData(depthData)
                
                // 生成3D模型
                val modelData = generate3DModel(processedDepthData, woundArea)
                
                // 計算統計信息
                val statistics = calculateDepthStatistics(processedDepthData, woundArea)

                val result = Wound3DVisualizationResult(
                    modelData = modelData,
                    statistics = statistics,
                    generationTime = Date(),
                    success = true
                )

                Log.d(TAG, "3D視覺化生成成功")
                result

            } catch (e: Exception) {
                Log.e(TAG, "3D視覺化生成失敗", e)
                Wound3DVisualizationResult(
                    modelData = null,
                    statistics = null,
                    generationTime = Date(),
                    success = false,
                    errorMessage = e.message
                )
            } finally {
                _isGenerating3D.value = false
            }
        }
    }

    /**
     * 創建3D視圖
     */
    fun create3DView(): GLSurfaceView {
        val glSurfaceView = WoundGLSurfaceView(context)
        glSurfaceView.setEGLContextClientVersion(2)
        glSurfaceView.setRenderer(WoundRenderer())
        glSurfaceView.renderMode = GLSurfaceView.RENDERMODE_WHEN_DIRTY
        
        sceneView = glSurfaceView
        return glSurfaceView
    }

    /**
     * 重置視圖
     */
    fun resetView() {
        _currentRotationX.value = 0f
        _currentRotationY.value = 0f
        _zoomScale.value = 1.0f
        
        sceneView?.requestRender()
        Log.d(TAG, "3D視圖已重置")
    }

    /**
     * 更新旋轉
     */
    fun updateRotation(deltaX: Float, deltaY: Float) {
        _currentRotationX.value += deltaX
        _currentRotationY.value += deltaY
        sceneView?.requestRender()
    }

    /**
     * 更新縮放
     */
    fun updateZoom(scale: Float) {
        _zoomScale.value = scale.coerceIn(0.5f, 3.0f)
        sceneView?.requestRender()
    }

    /**
     * 驗證深度數據
     */
    private fun validateDepthData(depthData: ByteArray): Boolean {
        return depthData.isNotEmpty() && depthData.size >= 1024
    }

    /**
     * 處理深度數據
     */
    private fun processDepthData(depthData: ByteArray): ProcessedDepthData {
        // 將原始深度數據轉換為浮點數數組
        val floatBuffer = ByteBuffer.wrap(depthData)
            .order(ByteOrder.LITTLE_ENDIAN)
            .asFloatBuffer()
        
        val depthValues = FloatArray(floatBuffer.remaining())
        floatBuffer.get(depthValues)
        
        // 過濾無效值
        val validDepths = depthValues.filter { it > 0 && it < 1000 }
        
        return ProcessedDepthData(
            depthValues = validDepths.toFloatArray(),
            width = 64, // 假設64x64深度圖
            height = 64,
            minDepth = validDepths.minOrNull() ?: 0f,
            maxDepth = validDepths.maxOrNull() ?: 0f
        )
    }

    /**
     * 生成3D模型
     */
    private fun generate3DModel(
        depthData: ProcessedDepthData,
        woundArea: Double
    ): Wound3DModelData {
        val vertices = mutableListOf<Float>()
        val colors = mutableListOf<Float>()
        val indices = mutableListOf<Int>()
        
        val width = depthData.width
        val height = depthData.height
        
        // 生成頂點
        for (y in 0 until height) {
            for (x in 0 until width) {
                val index = y * width + x
                if (index < depthData.depthValues.size) {
                    val depth = depthData.depthValues[index]
                    val normalizedDepth = (depth - depthData.minDepth) / 
                                        (depthData.maxDepth - depthData.minDepth)
                    
                    // 頂點位置
                    vertices.add(x.toFloat() / width - 0.5f) // X
                    vertices.add(y.toFloat() / height - 0.5f) // Y
                    vertices.add(normalizedDepth * 0.5f) // Z
                    
                    // 頂點顏色 (基於深度)
                    colors.add(normalizedDepth) // R
                    colors.add(0.5f) // G
                    colors.add(1.0f - normalizedDepth) // B
                    colors.add(1.0f) // A
                }
            }
        }
        
        // 生成索引 (三角形)
        for (y in 0 until height - 1) {
            for (x in 0 until width - 1) {
                val topLeft = y * width + x
                val topRight = topLeft + 1
                val bottomLeft = (y + 1) * width + x
                val bottomRight = bottomLeft + 1
                
                // 第一個三角形
                indices.add(topLeft)
                indices.add(bottomLeft)
                indices.add(topRight)
                
                // 第二個三角形
                indices.add(topRight)
                indices.add(bottomLeft)
                indices.add(bottomRight)
            }
        }
        
        return Wound3DModelData(
            vertices = vertices.toFloatArray(),
            colors = colors.toFloatArray(),
            indices = indices.toIntArray(),
            vertexCount = vertices.size / 3,
            indexCount = indices.size
        )
    }

    /**
     * 計算深度統計信息
     */
    private fun calculateDepthStatistics(
        depthData: ProcessedDepthData,
        woundArea: Double
    ): DepthStatistics {
        val depths = depthData.depthValues
        
        val averageDepth = depths.average().toFloat()
        val depthVariance = depths.map { (it - averageDepth) * (it - averageDepth) }.average().toFloat()
        val depthStandardDeviation = kotlin.math.sqrt(depthVariance.toDouble()).toFloat()
        
        return DepthStatistics(
            averageDepth = averageDepth,
            minDepth = depthData.minDepth,
            maxDepth = depthData.maxDepth,
            depthVariance = depthVariance,
            depthStandardDeviation = depthStandardDeviation,
            estimatedVolume = calculateEstimatedVolume(depths, woundArea),
            surfaceRoughness = calculateSurfaceRoughness(depths)
        )
    }

    /**
     * 計算估算體積
     */
    private fun calculateEstimatedVolume(depths: FloatArray, woundArea: Double): Double {
        val averageDepth = depths.average()
        return woundArea * averageDepth * 0.1 // 簡化的體積計算
    }

    /**
     * 計算表面粗糙度
     */
    private fun calculateSurfaceRoughness(depths: FloatArray): Double {
        val averageDepth = depths.average()
        val roughness = depths.map { kotlin.math.abs(it - averageDepth) }.average()
        return roughness
    }

    /**
     * 清理資源
     */
    fun cleanup() {
        processingScope.cancel()
        sceneView = null
    }

    // 數據類別
    data class Wound3DVisualizationResult(
        val modelData: Wound3DModelData?,
        val statistics: DepthStatistics?,
        val generationTime: Date,
        val success: Boolean,
        val errorMessage: String? = null
    )

    data class ProcessedDepthData(
        val depthValues: FloatArray,
        val width: Int,
        val height: Int,
        val minDepth: Float,
        val maxDepth: Float
    )

    data class Wound3DModelData(
        val vertices: FloatArray,
        val colors: FloatArray,
        val indices: IntArray,
        val vertexCount: Int,
        val indexCount: Int
    )

    data class DepthStatistics(
        val averageDepth: Float,
        val minDepth: Float,
        val maxDepth: Float,
        val depthVariance: Float,
        val depthStandardDeviation: Float,
        val estimatedVolume: Double,
        val surfaceRoughness: Double
    )

    enum class Wound3DVisualizationError : Exception() {
        INVALID_DEPTH_DATA,
        PROCESSING_FAILED
    }

    // OpenGL渲染器
    private inner class WoundRenderer : GLSurfaceView.Renderer {
        private var vertexBuffer: FloatBuffer? = null
        private var colorBuffer: FloatBuffer? = null
        private var indexBuffer: ByteBuffer? = null
        private var program: Int = 0
        private var positionHandle: Int = 0
        private var colorHandle: Int = 0
        private var mvpMatrixHandle: Int = 0

        override fun onSurfaceCreated(gl: GL10?, config: EGLConfig?) {
            // 初始化OpenGL程序
            program = createProgram(VERTEX_SHADER, FRAGMENT_SHADER)
            positionHandle = gl.glGetAttribLocation(program, "aPosition")
            colorHandle = gl.glGetAttribLocation(program, "aColor")
            mvpMatrixHandle = gl.glGetUniformLocation(program, "uMVPMatrix")
            
            gl.glClearColor(0.0f, 0.0f, 0.0f, 1.0f)
            gl.glEnable(GL10.GL_DEPTH_TEST)
        }

        override fun onSurfaceChanged(gl: GL10?, width: Int, height: Int) {
            gl.glViewport(0, 0, width, height)
        }

        override fun onDrawFrame(gl: GL10?) {
            gl.glClear(GL10.GL_COLOR_BUFFER_BIT or GL10.GL_DEPTH_BUFFER_BIT)
            
            // 這裡應該渲染3D模型
            // 簡化版本，實際實作需要完整的OpenGL渲染代碼
        }

        private fun createProgram(vertexShader: String, fragmentShader: String): Int {
            // 簡化的著色器程序創建
            return 1 // 實際實作需要完整的OpenGL著色器編譯
        }
    }

    // 自定義GLSurfaceView
    private inner class WoundGLSurfaceView(context: Context) : GLSurfaceView(context) {
        override fun onTouchEvent(event: MotionEvent): Boolean {
            when (event.action) {
                MotionEvent.ACTION_MOVE -> {
                    val deltaX = event.x - lastX
                    val deltaY = event.y - lastY
                    updateRotation(deltaX * 0.5f, deltaY * 0.5f)
                    lastX = event.x
                    lastY = event.y
                    return true
                }
            }
            return super.onTouchEvent(event)
        }

        companion object {
            private var lastX = 0f
            private var lastY = 0f
        }
    }
} 