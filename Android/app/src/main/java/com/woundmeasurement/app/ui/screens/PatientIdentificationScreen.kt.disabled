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
import com.woundmeasurement.app.processing.PatientIdentificationModule
import com.woundmeasurement.app.data.entity.PatientEntity

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun PatientIdentificationScreen(
    patientIdentificationModule: PatientIdentificationModule,
    onPatientIdentified: (PatientIdentificationModule.PatientInfo) -> Unit,
    onBackPressed: () -> Unit
) {
    var showBarcodeScanner by remember { mutableStateOf(false) }
    var showManualInput by remember { mutableStateOf(false) }
    var searchQuery by remember { mutableStateOf("") }
    
    val currentPatient by patientIdentificationModule.currentPatient.collectAsState()
    val recentPatients by patientIdentificationModule.recentPatients.collectAsState()
    val scanResult by patientIdentificationModule.scanResult.collectAsState()
    
    LaunchedEffect(scanResult) {
        when (scanResult) {
            is PatientIdentificationModule.ScanResult.SUCCESS -> {
                onPatientIdentified(scanResult.patientInfo)
            }
            is PatientIdentificationModule.ScanResult.INVALID_FORMAT -> {
                // 顯示錯誤訊息
            }
            is PatientIdentificationModule.ScanResult.INVALID_DATA -> {
                // 顯示錯誤訊息
            }
            is PatientIdentificationModule.ScanResult.ERROR -> {
                // 顯示錯誤訊息
            }
            null -> {}
        }
    }
    
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp)
    ) {
        // 標題
        Text(
            text = "病患識別",
            fontSize = 24.sp,
            fontWeight = FontWeight.Bold,
            modifier = Modifier.padding(bottom = 24.dp)
        )
        
        // 當前病患信息
        currentPatient?.let { patient ->
            Card(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(bottom = 16.dp),
                colors = CardDefaults.cardColors(
                    containerColor = MaterialTheme.colorScheme.primaryContainer
                )
            ) {
                Column(
                    modifier = Modifier.padding(16.dp)
                ) {
                    Text(
                        text = "當前病患",
                        fontSize = 16.sp,
                        fontWeight = FontWeight.Bold,
                        color = MaterialTheme.colorScheme.onPrimaryContainer
                    )
                    Spacer(modifier = Modifier.height(8.dp))
                    Text("姓名: ${patient.name}")
                    Text("ID: ${patient.id}")
                    Text("醫療記錄號: ${patient.medicalRecordNumber}")
                    Text("科別: ${patient.department}")
                    
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween
                    ) {
                        Button(
                            onClick = { patientIdentificationModule.clearCurrentPatient() }
                        ) {
                            Text("清除")
                        }
                        Button(
                            onClick = { onPatientIdentified(patient) }
                        ) {
                            Text("確認使用")
                        }
                    }
                }
            }
        }
        
        // 識別方式選擇
        Card(
            modifier = Modifier
                .fillMaxWidth()
                .padding(bottom = 16.dp)
        ) {
            Column(
                modifier = Modifier.padding(16.dp)
            ) {
                Text(
                    text = "選擇識別方式",
                    fontSize = 18.sp,
                    fontWeight = FontWeight.Bold,
                    modifier = Modifier.padding(bottom = 16.dp)
                )
                
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceEvenly
                ) {
                    Button(
                        onClick = { showBarcodeScanner = true }
                    ) {
                        Text("掃描條碼")
                    }
                    
                    Button(
                        onClick = { showManualInput = true }
                    ) {
                        Text("手動輸入")
                    }
                }
            }
        }
        
        // 最近病患列表
        if (recentPatients.isNotEmpty()) {
            Card(
                modifier = Modifier
                    .fillMaxWidth()
                    .weight(1f)
            ) {
                Column(
                    modifier = Modifier.padding(16.dp)
                ) {
                    Text(
                        text = "最近病患",
                        fontSize = 18.sp,
                        fontWeight = FontWeight.Bold,
                        modifier = Modifier.padding(bottom = 16.dp)
                    )
                    
                    LazyColumn {
                        items(recentPatients) { patient ->
                            PatientItem(
                                patient = patient,
                                onClick = { onPatientIdentified(patient) }
                            )
                        }
                    }
                }
            }
        }
    }
    
    // 手動輸入對話框
    if (showManualInput) {
        ManualInputDialog(
            onDismiss = { showManualInput = false },
            onConfirm = { patientInfo ->
                // 處理手動輸入
                showManualInput = false
            }
        )
    }
    
    // 條碼掃描器
    if (showBarcodeScanner) {
        BarcodeScannerDialog(
            onDismiss = { showBarcodeScanner = false },
            onBarcodeScanned = { barcodeData ->
                // 處理條碼掃描結果
                showBarcodeScanner = false
            }
        )
    }
}

@Composable
fun PatientItem(
    patient: PatientIdentificationModule.PatientInfo,
    onClick: () -> Unit
) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 4.dp),
        onClick = onClick
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Column(
                modifier = Modifier.weight(1f)
            ) {
                Text(
                    text = patient.name,
                    fontSize = 16.sp,
                    fontWeight = FontWeight.Bold
                )
                Text(
                    text = "ID: ${patient.id}",
                    fontSize = 14.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
                Text(
                    text = "科別: ${patient.department}",
                    fontSize = 14.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
            
            Icon(
                imageVector = androidx.compose.material.icons.Icons.Default.ArrowForward,
                contentDescription = "選擇"
            )
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ManualInputDialog(
    onDismiss: () -> Unit,
    onConfirm: (PatientIdentificationModule.PatientInfo) -> Unit
) {
    var name by remember { mutableStateOf("") }
    var id by remember { mutableStateOf("") }
    var birthDate by remember { mutableStateOf("") }
    var gender by remember { mutableStateOf("") }
    var department by remember { mutableStateOf("") }
    
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("手動輸入病患信息") },
        text = {
            Column {
                OutlinedTextField(
                    value = name,
                    onValueChange = { name = it },
                    label = { Text("姓名") },
                    modifier = Modifier.fillMaxWidth()
                )
                Spacer(modifier = Modifier.height(8.dp))
                OutlinedTextField(
                    value = id,
                    onValueChange = { id = it },
                    label = { Text("病患ID") },
                    modifier = Modifier.fillMaxWidth()
                )
                Spacer(modifier = Modifier.height(8.dp))
                OutlinedTextField(
                    value = birthDate,
                    onValueChange = { birthDate = it },
                    label = { Text("出生日期") },
                    modifier = Modifier.fillMaxWidth()
                )
                Spacer(modifier = Modifier.height(8.dp))
                OutlinedTextField(
                    value = gender,
                    onValueChange = { gender = it },
                    label = { Text("性別") },
                    modifier = Modifier.fillMaxWidth()
                )
                Spacer(modifier = Modifier.height(8.dp))
                OutlinedTextField(
                    value = department,
                    onValueChange = { department = it },
                    label = { Text("科別") },
                    modifier = Modifier.fillMaxWidth()
                )
            }
        },
        confirmButton = {
            Button(
                onClick = {
                    val patientInfo = PatientIdentificationModule.PatientInfo(
                        id = id,
                        name = name,
                        birthDate = birthDate,
                        gender = gender,
                        department = department
                    )
                    onConfirm(patientInfo)
                }
            ) {
                Text("確認")
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) {
                Text("取消")
            }
        }
    )
}

@Composable
fun BarcodeScannerDialog(
    onDismiss: () -> Unit,
    onBarcodeScanned: (String) -> Unit
) {
    // 這裡應該實作條碼掃描器
    // 暫時顯示一個簡單的對話框
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("條碼掃描") },
        text = { Text("條碼掃描功能需要整合ZXing庫") },
        confirmButton = {
            Button(onClick = onDismiss) {
                Text("確定")
            }
        }
    )
} 