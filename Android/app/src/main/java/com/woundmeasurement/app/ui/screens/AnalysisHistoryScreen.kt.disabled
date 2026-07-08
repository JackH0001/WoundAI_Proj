package com.woundmeasurement.app.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.woundmeasurement.app.processing.AnalysisHistoryModule
import com.woundmeasurement.app.processing.PatientIdentificationModule

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AnalysisHistoryScreen(
    analysisHistoryModule: AnalysisHistoryModule,
    currentPatient: PatientIdentificationModule.PatientInfo?,
    onBackPressed: () -> Unit
) {
    var selectedTimeRange by remember { mutableStateOf(AnalysisHistoryModule.TimeRange.WEEK) }
    
    val historicalData by analysisHistoryModule.historicalData.collectAsState()
    val trendAnalysis by analysisHistoryModule.trendAnalysis.collectAsState()
    
    LaunchedEffect(currentPatient?.id, selectedTimeRange) {
        currentPatient?.id?.let { patientId ->
            analysisHistoryModule.loadHistoricalData(patientId, selectedTimeRange)
        }
    }
    
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp)
    ) {
        // 標題和病患信息
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(bottom = 16.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Column {
                Text(
                    text = "歷史分析",
                    fontSize = 24.sp,
                    fontWeight = FontWeight.Bold
                )
                currentPatient?.let { patient ->
                    Text(
                        text = "病患: ${patient.name} (${patient.id})",
                        fontSize = 16.sp,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
            }
            
            IconButton(onClick = onBackPressed) {
                Icon(
                    imageVector = androidx.compose.material.icons.Icons.Default.ArrowBack,
                    contentDescription = "返回"
                )
            }
        }
        
        // 時間範圍選擇器
        TimeRangeSelector(
            selectedRange = selectedTimeRange,
            onRangeSelected = { selectedTimeRange = it }
        )
        
        // 內容區域
        LazyColumn(
            modifier = Modifier.weight(1f),
            verticalArrangement = Arrangement.spacedBy(16.dp)
        ) {
            // 當前測量與歷史比較
            item {
                CurrentVsHistoryCard(
                    currentResult = null, // 需要從外部傳入當前測量結果
                    historicalData = historicalData
                )
            }
            
            // 趨勢圖表
            if (historicalData.isNotEmpty()) {
                item {
                    TrendChartsView(
                        data = historicalData,
                        timeRange = selectedTimeRange
                    )
                }
            }
            
            // 統計摘要
            item {
                StatisticsSummaryView(
                    data = historicalData,
                    currentResult = null
                )
            }
            
            // 癒合進度分析
            item {
                HealingProgressView(
                    data = historicalData,
                    currentResult = null
                )
            }
            
            // 建議和警告
            item {
                RecommendationsView(
                    data = historicalData,
                    currentResult = null
                )
            }
        }
    }
}

@Composable
fun TimeRangeSelector(
    selectedRange: AnalysisHistoryModule.TimeRange,
    onRangeSelected: (AnalysisHistoryModule.TimeRange) -> Unit
) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .padding(bottom = 16.dp)
    ) {
        Column(
            modifier = Modifier.padding(16.dp)
        ) {
            Text(
                text = "時間範圍",
                fontSize = 18.sp,
                fontWeight = FontWeight.Bold,
                modifier = Modifier.padding(bottom = 12.dp)
            )
            
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceEvenly
            ) {
                AnalysisHistoryModule.TimeRange.values().forEach { timeRange ->
                    FilterChip(
                        selected = selectedRange == timeRange,
                        onClick = { onRangeSelected(timeRange) },
                        label = { Text(timeRange.displayName) }
                    )
                }
            }
        }
    }
}

@Composable
fun CurrentVsHistoryCard(
    currentResult: Any?, // 需要定義具體的類型
    historicalData: List<AnalysisHistoryModule.HistoricalMeasurement>
) {
    Card(
        modifier = Modifier.fillMaxWidth()
    ) {
        Column(
            modifier = Modifier.padding(16.dp)
        ) {
            Text(
                text = "當前測量與歷史比較",
                fontSize = 18.sp,
                fontWeight = FontWeight.Bold,
                modifier = Modifier.padding(bottom = 12.dp)
            )
            
            if (historicalData.isNotEmpty()) {
                val latestMeasurement = historicalData.first()
                val previousMeasurement = historicalData.getOrNull(1)
                
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween
                ) {
                    Column {
                        Text("最新測量", fontWeight = FontWeight.Bold)
                        Text("面積: ${latestMeasurement.area} cm²")
                        Text("體積: ${latestMeasurement.volume} cm³")
                        Text("深度: ${latestMeasurement.averageDepth} cm")
                    }
                    
                    previousMeasurement?.let { previous ->
                        Column {
                            Text("上次測量", fontWeight = FontWeight.Bold)
                            Text("面積: ${previous.area} cm²")
                            Text("體積: ${previous.volume} cm³")
                            Text("深度: ${previous.averageDepth} cm")
                        }
                    }
                }
            } else {
                Text("無歷史數據")
            }
        }
    }
}

@Composable
fun TrendChartsView(
    data: List<AnalysisHistoryModule.HistoricalMeasurement>,
    timeRange: AnalysisHistoryModule.TimeRange
) {
    Card(
        modifier = Modifier.fillMaxWidth()
    ) {
        Column(
            modifier = Modifier.padding(16.dp)
        ) {
            Text(
                text = "趨勢圖表",
                fontSize = 18.sp,
                fontWeight = FontWeight.Bold,
                modifier = Modifier.padding(bottom = 12.dp)
            )
            
            // 這裡應該實作MPAndroidChart
            // 暫時顯示簡單的數據列表
            LazyColumn {
                items(data.take(10)) { measurement ->
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(vertical = 4.dp),
                        horizontalArrangement = Arrangement.SpaceBetween
                    ) {
                        Text(measurement.timestamp.toString())
                        Text("${measurement.area} cm²")
                    }
                }
            }
        }
    }
}

@Composable
fun StatisticsSummaryView(
    data: List<AnalysisHistoryModule.HistoricalMeasurement>,
    currentResult: Any?
) {
    Card(
        modifier = Modifier.fillMaxWidth()
    ) {
        Column(
            modifier = Modifier.padding(16.dp)
        ) {
            Text(
                text = "統計摘要",
                fontSize = 18.sp,
                fontWeight = FontWeight.Bold,
                modifier = Modifier.padding(bottom = 12.dp)
            )
            
            if (data.isNotEmpty()) {
                val areas = data.map { it.area }
                val volumes = data.map { it.volume }
                
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween
                ) {
                    Column {
                        Text("面積統計", fontWeight = FontWeight.Bold)
                        Text("平均: ${areas.average()} cm²")
                        Text("最大: ${areas.maxOrNull()} cm²")
                        Text("最小: ${areas.minOrNull()} cm²")
                    }
                    
                    Column {
                        Text("體積統計", fontWeight = FontWeight.Bold)
                        Text("平均: ${volumes.average()} cm³")
                        Text("最大: ${volumes.maxOrNull()} cm³")
                        Text("最小: ${volumes.minOrNull()} cm³")
                    }
                }
            } else {
                Text("無統計數據")
            }
        }
    }
}

@Composable
fun HealingProgressView(
    data: List<AnalysisHistoryModule.HistoricalMeasurement>,
    currentResult: Any?
) {
    Card(
        modifier = Modifier.fillMaxWidth()
    ) {
        Column(
            modifier = Modifier.padding(16.dp)
        ) {
            Text(
                text = "癒合進度",
                fontSize = 18.sp,
                fontWeight = FontWeight.Bold,
                modifier = Modifier.padding(bottom = 12.dp)
            )
            
            if (data.size >= 2) {
                val firstMeasurement = data.last()
                val latestMeasurement = data.first()
                val areaReduction = ((firstMeasurement.area - latestMeasurement.area) / firstMeasurement.area * 100)
                
                LinearProgressIndicator(
                    progress = (areaReduction / 100f).coerceIn(0f, 1f),
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(vertical = 8.dp)
                )
                
                Text("面積減少: ${String.format("%.1f", areaReduction)}%")
                
                when {
                    areaReduction >= 50 -> Text("癒合進展良好", color = MaterialTheme.colorScheme.primary)
                    areaReduction >= 20 -> Text("癒合進展穩定", color = MaterialTheme.colorScheme.secondary)
                    areaReduction > 0 -> Text("癒合進展緩慢", color = MaterialTheme.colorScheme.tertiary)
                    else -> Text("需要關注", color = MaterialTheme.colorScheme.error)
                }
            } else {
                Text("需要更多數據來分析癒合進度")
            }
        }
    }
}

@Composable
fun RecommendationsView(
    data: List<AnalysisHistoryModule.HistoricalMeasurement>,
    currentResult: Any?
) {
    Card(
        modifier = Modifier.fillMaxWidth()
    ) {
        Column(
            modifier = Modifier.padding(16.dp)
        ) {
            Text(
                text = "建議和警告",
                fontSize = 18.sp,
                fontWeight = FontWeight.Bold,
                modifier = Modifier.padding(bottom = 12.dp)
            )
            
            if (data.size >= 2) {
                val firstMeasurement = data.last()
                val latestMeasurement = data.first()
                val areaChange = latestMeasurement.area - firstMeasurement.area
                
                when {
                    areaChange > 0 -> {
                        Card(
                            colors = CardDefaults.cardColors(
                                containerColor = MaterialTheme.colorScheme.errorContainer
                            )
                        ) {
                            Column(
                                modifier = Modifier.padding(12.dp)
                            ) {
                                Text(
                                    "⚠️ 警告",
                                    fontWeight = FontWeight.Bold,
                                    color = MaterialTheme.colorScheme.onErrorContainer
                                )
                                Text(
                                    "傷口面積增加，建議立即就醫檢查",
                                    color = MaterialTheme.colorScheme.onErrorContainer
                                )
                            }
                        }
                    }
                    areaChange < -5 -> {
                        Card(
                            colors = CardDefaults.cardColors(
                                containerColor = MaterialTheme.colorScheme.primaryContainer
                            )
                        ) {
                            Column(
                                modifier = Modifier.padding(12.dp)
                            ) {
                                Text(
                                    "✅ 良好",
                                    fontWeight = FontWeight.Bold,
                                    color = MaterialTheme.colorScheme.onPrimaryContainer
                                )
                                Text(
                                    "癒合進展良好，繼續保持",
                                    color = MaterialTheme.colorScheme.onPrimaryContainer
                                )
                            }
                        }
                    }
                    else -> {
                        Card(
                            colors = CardDefaults.cardColors(
                                containerColor = MaterialTheme.colorScheme.secondaryContainer
                            )
                        ) {
                            Column(
                                modifier = Modifier.padding(12.dp)
                            ) {
                                Text(
                                    "ℹ️ 信息",
                                    fontWeight = FontWeight.Bold,
                                    color = MaterialTheme.colorScheme.onSecondaryContainer
                                )
                                Text(
                                    "癒合進展穩定，繼續觀察",
                                    color = MaterialTheme.colorScheme.onSecondaryContainer
                                )
                            }
                        }
                    }
                }
            } else {
                Text("需要更多數據來生成建議")
            }
        }
    }
} 