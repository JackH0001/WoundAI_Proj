package com.woundmeasurement.app.annotation

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.woundmeasurement.app.R
import com.woundmeasurement.app.ui.theme.WoundMeasurementAppTheme

class DoctorAuthActivity : ComponentActivity() {
    
    companion object {
        const val EXTRA_DOCTOR_ID = "doctor_id"
        const val EXTRA_DOCTOR_NAME = "doctor_name"
        const val EXTRA_HOSPITAL = "hospital"
    }
    
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        
        setContent {
            WoundMeasurementAppTheme {
                Surface(
                    modifier = Modifier.fillMaxSize(),
                    color = MaterialTheme.colorScheme.background
                ) {
                    DoctorAuthScreen(
                        onAuthSuccess = { doctorId, doctorName, hospital ->
                            val intent = Intent(this, AnnotationActivity::class.java).apply {
                                putExtra(EXTRA_DOCTOR_ID, doctorId)
                                putExtra(EXTRA_DOCTOR_NAME, doctorName)
                                putExtra(EXTRA_HOSPITAL, hospital)
                            }
                            startActivity(intent)
                            finish()
                        },
                        onBackToMain = {
                            finish()
                        }
                    )
                }
            }
        }
    }
}

@Composable
fun DoctorAuthScreen(
    onAuthSuccess: (String, String, String) -> Unit,
    onBackToMain: () -> Unit
) {
    var doctorId by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var hospital by remember { mutableStateOf("") }
    var isLoading by remember { mutableStateOf(false) }
    var errorMessage by remember { mutableStateOf("") }
    
    val authFailedMsg = stringResource(id = R.string.auth_failed)

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center
    ) {
        Text(
            text = stringResource(id = R.string.doctor_auth_title),
            fontSize = 28.sp,
            fontWeight = FontWeight.Bold,
            modifier = Modifier.padding(bottom = 8.dp)
        )
        
        Text(
            text = stringResource(id = R.string.doctor_auth_subtitle),
            fontSize = 16.sp,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(bottom = 32.dp)
        )
        
        OutlinedTextField(
            value = doctorId,
            onValueChange = { doctorId = it },
            label = { Text(stringResource(id = R.string.doctor_license_number)) },
            modifier = Modifier.fillMaxWidth().padding(bottom = 16.dp),
            singleLine = true
        )
        
        OutlinedTextField(
            value = password,
            onValueChange = { password = it },
            label = { Text(stringResource(id = R.string.password)) },
            visualTransformation = PasswordVisualTransformation(),
            modifier = Modifier.fillMaxWidth().padding(bottom = 16.dp),
            singleLine = true
        )
        
        OutlinedTextField(
            value = hospital,
            onValueChange = { hospital = it },
            label = { Text(stringResource(id = R.string.hospital_name)) },
            modifier = Modifier.fillMaxWidth().padding(bottom = 24.dp),
            singleLine = true
        )
        
        if (errorMessage.isNotEmpty()) {
            Text(
                text = errorMessage,
                color = MaterialTheme.colorScheme.error,
                fontSize = 14.sp,
                modifier = Modifier.padding(bottom = 16.dp)
            )
        }
        
        Button(
            onClick = {
                isLoading = true
                errorMessage = ""
                
                if (validateDoctor(doctorId, password, hospital)) {
                    onAuthSuccess(doctorId, "Dr. $doctorId", hospital)
                } else {
                    errorMessage = authFailedMsg
                    isLoading = false
                }
            },
            modifier = Modifier
                .fillMaxWidth()
                .height(56.dp)
                .padding(bottom = 16.dp),
            enabled = !isLoading && doctorId.isNotEmpty() && password.isNotEmpty() && hospital.isNotEmpty()
        ) {
            if (isLoading) {
                CircularProgressIndicator(modifier = Modifier.size(24.dp), color = MaterialTheme.colorScheme.onPrimary)
            } else {
                Text(stringResource(id = R.string.auth_button), fontSize = 18.sp)
            }
        }
        
        OutlinedButton(
            onClick = onBackToMain,
            modifier = Modifier.fillMaxWidth().height(48.dp)
        ) {
            Text(stringResource(id = R.string.back_to_main), fontSize = 16.sp)
        }
        
        Text(
            text = stringResource(id = R.string.doctor_auth_footer),
            fontSize = 12.sp,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(top = 32.dp),
            textAlign = androidx.compose.ui.text.style.TextAlign.Center
        )
    }
}

private fun validateDoctor(doctorId: String, password: String, hospital: String): Boolean {
    val validDoctors = mapOf(
        "D001" to "REMOVED_USE_BACKEND_AUTH",
        "D002" to "REMOVED_USE_BACKEND_AUTH",
        "D003" to "REMOVED_USE_BACKEND_AUTH"
    )
    return validDoctors[doctorId] == password && hospital.isNotEmpty()
}
