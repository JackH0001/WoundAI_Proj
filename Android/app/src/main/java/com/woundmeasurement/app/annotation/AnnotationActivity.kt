package com.woundmeasurement.app.annotation

import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.Uri
import android.os.Bundle
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.*
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.woundmeasurement.app.ui.theme.WoundMeasurementAppTheme
import java.io.File
import java.io.FileOutputStream
import java.text.SimpleDateFormat
import java.util.*

class AnnotationActivity : ComponentActivity() {
    
    companion object {
        private const val TAG = "AnnotationActivity"
    }
    
    private var currentImageUri: Uri? = null
    private var currentBitmap: Bitmap? = null
    
    private val getContent = registerForActivityResult(ActivityResultContracts.GetContent()) { uri: Uri? ->
        uri?.let {
            currentImageUri = it
            try {
                val inputStream = contentResolver.openInputStream(it)
                currentBitmap = BitmapFactory.decodeStream(inputStream)
                inputStream?.close()
            } catch (e: Exception) {
                Log.e(TAG, "Error loading image: ${e.message}")
            }
        }
    }
    
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        
        val doctorId = intent.getStringExtra(DoctorAuthActivity.EXTRA_DOCTOR_ID) ?: ""
        val doctorName = intent.getStringExtra(DoctorAuthActivity.EXTRA_DOCTOR_NAME) ?: ""
        val hospital = intent.getStringExtra(DoctorAuthActivity.EXTRA_HOSPITAL) ?: ""
        
        setContent {
            WoundMeasurementAppTheme {
                Surface(
                    modifier = Modifier.fillMaxSize(),
                    color = MaterialTheme.colorScheme.background
                ) {
                    AnnotationScreen(
                        doctorId = doctorId,
                        doctorName = doctorName,
                        hospital = hospital,
                        currentBitmap = currentBitmap,
                        onSelectImage = { getContent.launch("image/*") },
                        onSaveAnnotation = { annotationData ->
                            saveAnnotationData(annotationData)
                        },
                        onBackToAuth = {
                            finish()
                        }
                    )
                }
            }
        }
    }
    
    private fun saveAnnotationData(annotationData: WoundAnnotationData) {
        // 這裡應該將標註資料上傳到雲端服務
        Log.d(TAG, "Saving annotation data: $annotationData")
        
        // 模擬上傳到雲端AI訓練資料庫
        uploadToCloudService(annotationData)
    }
    
    private fun uploadToCloudService(annotationData: WoundAnnotationData) {
        // 模擬上傳到雲端服務
        // 實際實作中應該使用 Retrofit 或其他網路庫
        Log.d(TAG, "Uploading to cloud service: ${annotationData.toJson()}")
    }
}

@Composable
fun AnnotationScreen(
    doctorId: String,
    doctorName: String,
    hospital: String,
    currentBitmap: Bitmap?,
    onSelectImage: () -> Unit,
    onSaveAnnotation: (WoundAnnotationData) -> Unit,
    onBackToAuth: () -> Unit
) {
    var currentTab by remember { mutableStateOf(0) }
    var annotationData by remember { mutableStateOf(WoundAnnotationData()) }
    
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp)
    ) {
        // 頂部標題和醫師資訊
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(bottom = 16.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Column {
                Text(
                    text = "傷口標註系統",
                    fontSize = 24.sp,
                    fontWeight = FontWeight.Bold
                )
                Text(
                    text = "$doctorName - $hospital",
                    fontSize = 14.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
            
            IconButton(onClick = onBackToAuth) {
                Icon(Icons.Default.ArrowBack, contentDescription = "返回")
            }
        }
        
        // 標籤頁
        TabRow(selectedTabIndex = currentTab) {
            Tab(
                selected = currentTab == 0,
                onClick = { currentTab = 0 },
                text = { Text("影像選擇") },
                icon = { Icon(Icons.Default.Image, contentDescription = null) }
            )
            Tab(
                selected = currentTab == 1,
                onClick = { currentTab = 1 },
                text = { Text("BJWAT評估") },
                icon = { Icon(Icons.Default.Assessment, contentDescription = null) }
            )
            Tab(
                selected = currentTab == 2,
                onClick = { currentTab = 2 },
                text = { Text("revPWAT評估") },
                icon = { Icon(Icons.Default.MedicalServices, contentDescription = null) }
            )
        }
        
        // 標籤頁內容
        when (currentTab) {
            0 -> ImageSelectionTab(
                currentBitmap = currentBitmap,
                onSelectImage = onSelectImage
            )
            1 -> BJWATEvaluationTab(
                annotationData = annotationData,
                onDataChange = { annotationData = it }
            )
            2 -> RevPWATEvaluationTab(
                annotationData = annotationData,
                onDataChange = { annotationData = it }
            )
        }
        
        // 底部操作按鈕
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(top = 16.dp),
            horizontalArrangement = Arrangement.SpaceEvenly
        ) {
            OutlinedButton(
                onClick = onBackToAuth,
                modifier = Modifier.weight(1f).padding(end = 8.dp)
            ) {
                Text("取消")
            }
            
            Button(
                onClick = { onSaveAnnotation(annotationData) },
                modifier = Modifier.weight(1f).padding(start = 8.dp),
                enabled = currentBitmap != null
            ) {
                Text("儲存標註")
            }
        }
    }
}

@Composable
fun ImageSelectionTab(
    currentBitmap: Bitmap?,
    onSelectImage: () -> Unit
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        if (currentBitmap != null) {
            // 顯示選中的影像
            Image(
                bitmap = currentBitmap.asImageBitmap(),
                contentDescription = "傷口影像",
                modifier = Modifier
                    .fillMaxWidth()
                    .height(300.dp)
                    .clip(RoundedCornerShape(8.dp)),
                contentScale = ContentScale.Fit
            )
            
            Text(
                text = "影像已載入",
                fontSize = 16.sp,
                modifier = Modifier.padding(top = 16.dp)
            )
        } else {
            // 選擇影像按鈕
            Card(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(300.dp),
                elevation = CardDefaults.cardElevation(defaultElevation = 4.dp)
            ) {
                Box(
                    modifier = Modifier.fillMaxSize(),
                    contentAlignment = Alignment.Center
                ) {
                    Column(
                        horizontalAlignment = Alignment.CenterHorizontally
                    ) {
                        Icon(
                            Icons.Default.AddPhotoAlternate,
                            contentDescription = null,
                            modifier = Modifier.size(64.dp),
                            tint = MaterialTheme.colorScheme.primary
                        )
                        Text(
                            text = "選擇傷口影像",
                            fontSize = 18.sp,
                            modifier = Modifier.padding(top = 16.dp)
                        )
                    }
                }
            }
        }
        
        Button(
            onClick = onSelectImage,
            modifier = Modifier
                .fillMaxWidth()
                .padding(top = 16.dp)
        ) {
            Icon(Icons.Default.PhotoCamera, contentDescription = null)
            Spacer(modifier = Modifier.width(8.dp))
            Text("選擇影像")
        }
    }
}

@Composable
fun BJWATEvaluationTab(
    annotationData: WoundAnnotationData,
    onDataChange: (WoundAnnotationData) -> Unit
) {
    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp)
    ) {
        item {
            Text(
                text = "BJWAT 評估標準",
                fontSize = 20.sp,
                fontWeight = FontWeight.Bold,
                modifier = Modifier.padding(bottom = 16.dp)
            )
        }
        
        // 傷口大小
        item {
            EvaluationItem(
                title = "1. 傷口大小 (Size)",
                description = "0 (< 0.25 cm²) 到 5 (> 12 cm²)",
                value = annotationData.bjwatSize,
                onValueChange = { 
                    onDataChange(annotationData.copy(bjwatSize = it))
                }
            )
        }
        
        // 傷口深度
        item {
            EvaluationItem(
                title = "2. 傷口深度 (Depth)",
                description = "0 (表皮) 到 4 (骨)",
                value = annotationData.bjwatDepth,
                onValueChange = { 
                    onDataChange(annotationData.copy(bjwatDepth = it))
                }
            )
        }
        
        // 邊緣
        item {
            EvaluationItem(
                title = "3. 邊緣 (Edges)",
                description = "0 (平滑) 到 2 (明顯增生)",
                value = annotationData.bjwatEdges,
                onValueChange = { 
                    onDataChange(annotationData.copy(bjwatEdges = it))
                }
            )
        }
        
        // 壞死組織類型
        item {
            EvaluationItem(
                title = "4. 壞死組織類型 (Necrotic Type)",
                description = "0 (無) 到 3 (厚黑痂)",
                value = annotationData.bjwatNecroticType,
                onValueChange = { 
                    onDataChange(annotationData.copy(bjwatNecroticType = it))
                }
            )
        }
        
        // 壞死量
        item {
            EvaluationItem(
                title = "5. 壞死量 (Necrotic Amount)",
                description = "0 (0%) 到 4 (> 75%)",
                value = annotationData.bjwatNecroticAmount,
                onValueChange = { 
                    onDataChange(annotationData.copy(bjwatNecroticAmount = it))
                }
            )
        }
        
        // 分泌物量
        item {
            EvaluationItem(
                title = "6. 分泌物量 (Exudate Amount)",
                description = "0 (無) 到 4 (極多)",
                value = annotationData.bjwatExudateAmount,
                onValueChange = { 
                    onDataChange(annotationData.copy(bjwatExudateAmount = it))
                }
            )
        }
        
        // 分泌物類型
        item {
            EvaluationItem(
                title = "7. 分泌物類型 (Exudate Type)",
                description = "0 (無) 到 3 (膿性)",
                value = annotationData.bjwatExudateType,
                onValueChange = { 
                    onDataChange(annotationData.copy(bjwatExudateType = it))
                }
            )
        }
        
        // 組織顏色
        item {
            EvaluationItem(
                title = "8. 組織顏色 (Tissue Color)",
                description = "0 (紅) 到 3 (紫)",
                value = annotationData.bjwatTissueColor,
                onValueChange = { 
                    onDataChange(annotationData.copy(bjwatTissueColor = it))
                }
            )
        }
        
        // 肉芽
        item {
            EvaluationItem(
                title = "9. 肉芽 (Granulation)",
                description = "0 (無) 到 3 (過度生長)",
                value = annotationData.bjwatGranulation,
                onValueChange = { 
                    onDataChange(annotationData.copy(bjwatGranulation = it))
                }
            )
        }
        
        // 再上皮化
        item {
            EvaluationItem(
                title = "10. 再上皮化 (Epithelialization)",
                description = "0 (無) 到 3 (> 50%)",
                value = annotationData.bjwatEpithelialization,
                onValueChange = { 
                    onDataChange(annotationData.copy(bjwatEpithelialization = it))
                }
            )
        }
        
        // 周邊皮膚
        item {
            EvaluationItem(
                title = "11. 周邊皮膚 (Peri-skin)",
                description = "0 (正常) 到 3 (硬結)",
                value = annotationData.bjwatPeriSkin,
                onValueChange = { 
                    onDataChange(annotationData.copy(bjwatPeriSkin = it))
                }
            )
        }
    }
}

@Composable
fun RevPWATEvaluationTab(
    annotationData: WoundAnnotationData,
    onDataChange: (WoundAnnotationData) -> Unit
) {
    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp)
    ) {
        item {
            Text(
                text = "revPWAT 評估標準",
                fontSize = 20.sp,
                fontWeight = FontWeight.Bold,
                modifier = Modifier.padding(bottom = 16.dp)
            )
        }
        
        // 壞死組織
        item {
            EvaluationItem(
                title = "1. 壞死組織 (Necrosis)",
                description = "1 (< 10%) 到 3 (> 50%)",
                value = annotationData.revPwatNecrosis,
                onValueChange = { 
                    onDataChange(annotationData.copy(revPwatNecrosis = it))
                }
            )
        }
        
        // Slough (腐肉)
        item {
            EvaluationItem(
                title = "2. Slough (腐肉)",
                description = "1 (< 10%) 到 3 (> 50%)",
                value = annotationData.revPwatSlough,
                onValueChange = { 
                    onDataChange(annotationData.copy(revPwatSlough = it))
                }
            )
        }
        
        // 肉芽組織
        item {
            EvaluationItem(
                title = "3. 肉芽組織 (Granulation)",
                description = "1 (< 10%) 到 3 (> 50%)",
                value = annotationData.revPwatGranulation,
                onValueChange = { 
                    onDataChange(annotationData.copy(revPwatGranulation = it))
                }
            )
        }
        
        // 分泌物
        item {
            EvaluationItem(
                title = "4. 分泌物 (Exudate)",
                description = "1 (干燥) 到 4 (多)",
                value = annotationData.revPwatExudate,
                onValueChange = { 
                    onDataChange(annotationData.copy(revPwatExudate = it))
                }
            )
        }
        
        // 顏色
        item {
            EvaluationItem(
                title = "5. 顏色 (Color)",
                description = "0 (正常) 到 2 (灰紫)",
                value = annotationData.revPwatColor,
                onValueChange = { 
                    onDataChange(annotationData.copy(revPwatColor = it))
                }
            )
        }
        
        // 深度
        item {
            EvaluationItem(
                title = "6. 深度 (Depth)",
                description = "1 (< 5mm) 到 3 (> 15mm)",
                value = annotationData.revPwatDepth,
                onValueChange = { 
                    onDataChange(annotationData.copy(revPwatDepth = it))
                }
            )
        }
    }
}

@Composable
fun EvaluationItem(
    title: String,
    description: String,
    value: Int,
    onValueChange: (Int) -> Unit
) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 4.dp),
        elevation = CardDefaults.cardElevation(defaultElevation = 2.dp)
    ) {
        Column(
            modifier = Modifier.padding(16.dp)
        ) {
            Text(
                text = title,
                fontSize = 16.sp,
                fontWeight = FontWeight.Medium
            )
            
            Text(
                text = description,
                fontSize = 12.sp,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(top = 4.dp)
            )
            
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(top = 8.dp),
                horizontalArrangement = Arrangement.SpaceEvenly
            ) {
                for (i in 0..4) {
                    FilterChip(
                        selected = value == i,
                        onClick = { onValueChange(i) },
                        label = { Text(i.toString()) }
                    )
                }
            }
        }
    }
} 