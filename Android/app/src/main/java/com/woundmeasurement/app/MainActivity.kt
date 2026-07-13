package com.woundmeasurement.app

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import kotlinx.coroutines.launch
import com.woundmeasurement.app.ui.theme.WoundMeasurementAppTheme
import com.woundmeasurement.app.camera.CaptureResult
import com.woundmeasurement.app.camera.AdvancedCameraModule
import com.woundmeasurement.app.camera.ImageQualityAssessor
import com.woundmeasurement.app.annotation.DoctorAuthActivity
import com.woundmeasurement.app.pipeline.MeasureValidationEntry

class MainActivity : ComponentActivity() {
    
    companion object {
        private const val TAG = "MainActivity"
    }
    
    private val requestPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { isGranted: Boolean ->
        if (isGranted) {
            Log.d(TAG, "相機權限已授予")
        } else {
            Log.w(TAG, "相機權限被拒絕")
        }
    }
    
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Log.d(TAG, "onCreate: 應用程式啟動")
        
        // 檢查相機權限
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
            requestPermissionLauncher.launch(Manifest.permission.CAMERA)
        }
        
        setContent {
            WoundMeasurementAppTheme {
                Surface(
                    modifier = Modifier.fillMaxSize(),
                    color = MaterialTheme.colorScheme.background
                ) {
                    WoundMeasurementApp()
                }
            }
        }
    }
}

@Composable
fun WoundMeasurementApp() {
    var currentScreen by remember { mutableStateOf("main") }
    val context = LocalContext.current
    
    // 只顯示當前畫面:主選單 或 全螢幕子畫面(子畫面各自有返回鈕,避免被選單擠壓/無法捲動)
    when (currentScreen) {
        "capture" -> CaptureScreen(onBack = { currentScreen = "main" })
        "history" -> HistoryScreen(onBack = { currentScreen = "main" })
        "settings" -> SettingsScreen(onBack = { currentScreen = "main" })
        "validate" -> MeasureValidationEntry(onBack = { currentScreen = "main" })
        else -> Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(16.dp),
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            Text(
                text = stringResource(id = R.string.app_title),
                fontSize = 24.sp,
                fontWeight = FontWeight.Bold,
                modifier = Modifier.padding(bottom = 32.dp)
            )
            MainButton(stringResource(id = R.string.start_measurement)) { currentScreen = "capture" }
            MainButton(stringResource(id = R.string.view_history)) { currentScreen = "history" }
            MainButton(stringResource(id = R.string.settings)) { currentScreen = "settings" }
            MainButton(stringResource(id = R.string.doctor_annotation_system)) {
                val intent = Intent(context, DoctorAuthActivity::class.java)
                context.startActivity(intent)
            }
            MainButton("AI 量測驗證(模擬)") { currentScreen = "validate" }
        }
    }
}

@Composable
fun MainButton(text: String, onClick: () -> Unit) {
    Button(
        onClick = onClick,
        modifier = Modifier
            .fillMaxWidth()
            .height(56.dp)
            .padding(bottom = 16.dp)
    ) {
        Text(text, fontSize = 18.sp)
    }
}

@Composable
fun CaptureScreen(onBack: () -> Unit) {
    var cameraLoading by remember { mutableStateOf(true) }
    var isEmulator by remember { mutableStateOf(false) }
    var captureResult by remember { mutableStateOf<CaptureResult?>(null) }
    var qualityAssessment by remember { mutableStateOf<String?>(null) }
    var isCapturing by remember { mutableStateOf(false) }
    
    val context = LocalContext.current
    val advancedCamera = remember { AdvancedCameraModule(context) }
    
    LaunchedEffect(Unit) {
        // 檢查是否為模擬器
        isEmulator = android.os.Build.FINGERPRINT.contains("generic") || android.os.Build.FINGERPRINT.contains("sdk")
        
        if (!isEmulator) {
            try {
                if (advancedCamera.initialize()) {
                    advancedCamera.openCamera()
                }
            } catch (e: Exception) {
                Log.e("MainActivity", "相機初始化錯誤", e)
            }
        }
        
        kotlinx.coroutines.delay(1000)
        cameraLoading = false
    }
    
    DisposableEffect(Unit) {
        onDispose { advancedCamera.release() }
    }
    
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Text(
            text = stringResource(id = R.string.capture_wound),
            fontSize = 20.sp,
            fontWeight = FontWeight.Bold,
            modifier = Modifier.padding(bottom = 16.dp)
        )
        
        if (isEmulator) {
            Text(stringResource(id = R.string.emulator_mode), fontWeight = FontWeight.Bold)
            Text(stringResource(id = R.string.emulator_description), fontSize = 14.sp)
            Button(onClick = { /* 模擬 */ }, modifier = Modifier.fillMaxWidth().padding(top = 8.dp)) {
                Text(stringResource(id = R.string.simulate_photo))
            }
        } else {
            Text(if (cameraLoading) stringResource(id = R.string.camera_loading) else stringResource(id = R.string.camera_ready))
            
            if (!cameraLoading) {
                val coroutineScope = rememberCoroutineScope()
                Button(
                    onClick = {
                        coroutineScope.launch {
                            isCapturing = true
                            try {
                                val result = advancedCamera.captureHighQualityPhoto()
                                if (result != null) {
                                    captureResult = result
                                    val recommendations = ImageQualityAssessor().getQualityRecommendations(result.qualityScore)
                                    qualityAssessment = if (result.qualityScore.isAcceptable) {
                                        "✅ 品值合格: ${"%.1f".format(result.qualityScore.overallScore)}"
                                    } else {
                                        "⚠️ 品值不足: ${"%.1f".format(result.qualityScore.overallScore)}\n${recommendations.firstOrNull()}"
                                    }
                                }
                            } finally {
                                isCapturing = false
                            }
                        }
                    },
                    enabled = !isCapturing,
                    modifier = Modifier.fillMaxWidth().padding(16.dp)
                ) {
                    Text(if (isCapturing) stringResource(id = R.string.capturing) else stringResource(id = R.string.high_quality_capture))
                }
                
                qualityAssessment?.let {
                    Card(modifier = Modifier.fillMaxWidth().padding(16.dp)) {
                        Text(it, modifier = Modifier.padding(16.dp), fontSize = 14.sp)
                    }
                }
            }
        }
        
        Button(onClick = onBack, modifier = Modifier.padding(top = 16.dp)) {
            Text(stringResource(id = R.string.back_to_main))
        }
    }
}

@Composable
fun HistoryScreen(onBack: () -> Unit) {
    Column(modifier = Modifier.fillMaxSize().padding(16.dp), horizontalAlignment = Alignment.CenterHorizontally) {
        Text(stringResource(id = R.string.history_records), fontSize = 20.sp, fontWeight = FontWeight.Bold)
        Text(stringResource(id = R.string.database_loading), modifier = Modifier.padding(16.dp))
        Button(onClick = onBack) { Text(stringResource(id = R.string.back_to_main)) }
    }
}

@Composable
fun SettingsScreen(onBack: () -> Unit) {
    Column(modifier = Modifier.fillMaxSize().padding(16.dp), horizontalAlignment = Alignment.CenterHorizontally) {
        Text(stringResource(id = R.string.settings), fontSize = 20.sp, fontWeight = FontWeight.Bold)
        Text(stringResource(id = R.string.settings_loading), modifier = Modifier.padding(16.dp))
        Button(onClick = onBack) { Text(stringResource(id = R.string.back_to_main)) }
    }
}
