package com.woundmeasurement.app.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import com.woundmeasurement.app.processing.Wound3DVisualizationModule
import android.opengl.GLSurfaceView

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun Wound3DVisualizationScreen(
    wound3DVisualizationModule: Wound3DVisualizationModule,
    depthData: ByteArray,
    woundArea: Double,
    onBackPressed: () -> Unit
) {
    var isGenerating3D by remember { mutableStateOf(false) }
    var visualizationResult by remember { mutableStateOf<Wound3DVisualizationModule.Wound3DVisualizationResult?>(null) }
    
    val isGenerating3DState by wound3DVisualizationModule.isGenerating3D.collectAsState()
    val currentRotationX by wound3DVisualizationModule.currentRotationX.collectAsState()
    val currentRotationY by wound3DVisualizationModule.currentRotationY.collectAsState()
    val zoomScale by wound3DVisualizationModule.zoomScale.collectAsState()
    
    LaunchedEffect(depthData, woundArea) {
        isGenerating3D = true
        try {
            val result = wound3DVisualizationModule.generate3DVisualization(depthData, woundArea)
            visualizationResult = result
        } finally {
            isGenerating3D = false
        }
    }
    
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp)
    ) {
        // 標題和控制
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(bottom = 16.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text(
                text = "3D深度視覺化",
                fontSize = 24.sp,
                fontWeight = FontWeight.Bold
            )
            
            Row {
                IconButton(onClick = { wound3DVisualizationModule.resetView() }) {
                    Icon(
                        imageVector = androidx.compose.material.icons.Icons.Default.Refresh,
                        contentDescription = "重置視圖"
                    )
                }
                
                IconButton(onClick = onBackPressed) {
                    Icon(
                        imageVector = androidx.compose.material.icons.Icons.Default.ArrowBack,
                        contentDescription = "返回"
                    )
                }
            }
        }
        
        // 操作提示
        Card(
            modifier = Modifier
                .fillMaxWidth()
                .padding(bottom = 16.dp),
            colors = CardDefaults.cardColors(
                containerColor = MaterialTheme.colorScheme.secondaryContainer
            )
        ) {
            Column(
                modifier = Modifier.padding(12.dp)
            ) {
                Text(
                    text = "操作提示",
                    fontSize = 16.sp,
                    fontWeight = FontWeight.Bold,
                    color = MaterialTheme.colorScheme.onSecondaryContainer
                )
                Text(
                    text = "滑動旋轉 • 捏合縮放",
                    fontSize = 14.sp,
                    color = MaterialTheme.colorScheme.onSecondaryContainer
                )
            }
        }
        
        // 3D場景視圖
        Card(
            modifier = Modifier
                .fillMaxWidth()
                .weight(1f)
        ) {
            if (isGenerating3D || isGenerating3DState) {
                Box(
                    modifier = Modifier.fillMaxSize(),
                    contentAlignment = Alignment.Center
                ) {
                    Column(
                        horizontalAlignment = Alignment.CenterHorizontally
                    ) {
                        CircularProgressIndicator(
                            modifier = Modifier.size(48.dp)
                        )
                        Spacer(modifier = Modifier.height(16.dp))
                        Text("生成3D模型中...")
                    }
                }
            } else {
                // 3D視圖
                AndroidView(
                    factory = { context ->
                        wound3DVisualizationModule.create3DView()
                    },
                    modifier = Modifier.fillMaxSize()
                )
            }
        }
        
        // 深度統計信息
        visualizationResult?.statistics?.let { statistics ->
            DepthStatisticsView(
                statistics = statistics,
                woundArea = woundArea
            )
        }
    }
}

@Composable
fun DepthStatisticsView(
    statistics: Wound3DVisualizationModule.DepthStatistics,
    woundArea: Double
) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .padding(top = 16.dp)
    ) {
        Column(
            modifier = Modifier.padding(16.dp)
        ) {
            Text(
                text = "深度統計信息",
                fontSize = 18.sp,
                fontWeight = FontWeight.Bold,
                modifier = Modifier.padding(bottom = 12.dp)
            )
            
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Column {
                    Text("平均深度", fontWeight = FontWeight.Bold)
                    Text("${String.format("%.2f", statistics.averageDepth)} cm")
                }
                
                Column {
                    Text("最小深度", fontWeight = FontWeight.Bold)
                    Text("${String.format("%.2f", statistics.minDepth)} cm")
                }
                
                Column {
                    Text("最大深度", fontWeight = FontWeight.Bold)
                    Text("${String.format("%.2f", statistics.maxDepth)} cm")
                }
            }
            
            Spacer(modifier = Modifier.height(12.dp))
            
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Column {
                    Text("估算體積", fontWeight = FontWeight.Bold)
                    Text("${String.format("%.2f", statistics.estimatedVolume)} cm³")
                }
                
                Column {
                    Text("表面粗糙度", fontWeight = FontWeight.Bold)
                    Text("${String.format("%.2f", statistics.surfaceRoughness)}")
                }
                
                Column {
                    Text("深度變異", fontWeight = FontWeight.Bold)
                    Text("${String.format("%.2f", statistics.depthVariance)}")
                }
            }
            
            Spacer(modifier = Modifier.height(12.dp))
            
            // 深度分佈圖表
            DepthDistributionChart(statistics = statistics)
        }
    }
}

@Composable
fun DepthDistributionChart(
    statistics: Wound3DVisualizationModule.DepthStatistics
) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceVariant
        )
    ) {
        Column(
            modifier = Modifier.padding(12.dp)
        ) {
            Text(
                text = "深度分佈",
                fontSize = 16.sp,
                fontWeight = FontWeight.Bold,
                modifier = Modifier.padding(bottom = 8.dp)
            )
            
            // 簡化的深度分佈條形圖
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceEvenly,
                verticalAlignment = Alignment.Bottom
            ) {
                // 最小深度
                Column(
                    horizontalAlignment = Alignment.CenterHorizontally
                ) {
                    Box(
                        modifier = Modifier
                            .width(20.dp)
                            .height((statistics.minDepth * 10).dp)
                            .background(MaterialTheme.colorScheme.primary)
                    )
                    Text(
                        text = "最小",
                        fontSize = 12.sp
                    )
                }
                
                // 平均深度
                Column(
                    horizontalAlignment = Alignment.CenterHorizontally
                ) {
                    Box(
                        modifier = Modifier
                            .width(20.dp)
                            .height((statistics.averageDepth * 10).dp)
                            .background(MaterialTheme.colorScheme.secondary)
                    )
                    Text(
                        text = "平均",
                        fontSize = 12.sp
                    )
                }
                
                // 最大深度
                Column(
                    horizontalAlignment = Alignment.CenterHorizontally
                ) {
                    Box(
                        modifier = Modifier
                            .width(20.dp)
                            .height((statistics.maxDepth * 10).dp)
                            .background(MaterialTheme.colorScheme.tertiary)
                    )
                    Text(
                        text = "最大",
                        fontSize = 12.sp
                    )
                }
            }
        }
    }
}

@Composable
fun Box(
    modifier: Modifier = Modifier,
    contentAlignment: Alignment = Alignment.TopStart,
    content: @Composable () -> Unit
) {
    androidx.compose.foundation.layout.Box(
        modifier = modifier,
        contentAlignment = contentAlignment,
        content = content
    )
}

@Composable
fun background(color: androidx.compose.ui.graphics.Color) {
    // 這裡應該實作背景顏色
    // 暫時使用空的Modifier
    Modifier
} 