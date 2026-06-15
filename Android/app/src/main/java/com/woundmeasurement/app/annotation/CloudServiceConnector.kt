package com.woundmeasurement.app.annotation

import android.content.Context
import android.graphics.Bitmap
import android.net.Uri
import android.util.Log
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.asRequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.text.SimpleDateFormat
import java.util.*

class CloudServiceConnector(private val context: Context) {
    
    companion object {
        private const val TAG = "CloudServiceConnector"
        
        // 雲端服務端點 (實際部署時需要修改)
        private const val BASE_URL = "https://your-cloud-service.com/api"
        private const val UPLOAD_ENDPOINT = "$BASE_URL/annotations/upload"
        private const val AUTH_ENDPOINT = "$BASE_URL/auth/doctor"
        private const val STATUS_ENDPOINT = "$BASE_URL/annotations/status"
        
        // 本地儲存路徑
        private const val LOCAL_STORAGE_PATH = "wound_annotations"
    }
    
    private val client = OkHttpClient.Builder()
        .connectTimeout(30, java.util.concurrent.TimeUnit.SECONDS)
        .readTimeout(60, java.util.concurrent.TimeUnit.SECONDS)
        .writeTimeout(60, java.util.concurrent.TimeUnit.SECONDS)
        .build()
    
    /**
     * 上傳標註資料到雲端服務
     */
    suspend fun uploadAnnotation(
        annotationData: WoundAnnotationData,
        imageUri: Uri?,
        onProgress: (Float) -> Unit = {}
    ): UploadResult = withContext(Dispatchers.IO) {
        try {
            Log.d(TAG, "開始上傳標註資料: ${annotationData.annotationId}")
            
            // 1. 準備影像檔案
            val imageFile = imageUri?.let { prepareImageFile(it) }
            
            // 2. 建立多部分請求
            val requestBody = MultipartBody.Builder()
                .setType(MultipartBody.FORM)
                .addFormDataPart(
                    "annotation_data",
                    "annotation.json",
                    annotationData.toJson().toRequestBody("application/json".toMediaType())
                )
                
            // 3. 添加影像檔案
            imageFile?.let { file ->
                requestBody.addFormDataPart(
                    "image",
                    file.name,
                    file.asRequestBody("image/*".toMediaType())
                )
            }
            
            // 4. 添加醫師認證資訊
            requestBody.addFormDataPart("doctor_id", annotationData.doctorId)
            requestBody.addFormDataPart("hospital", annotationData.hospital)
            requestBody.addFormDataPart("timestamp", System.currentTimeMillis().toString())
            
            // 5. 建立請求
            val request = Request.Builder()
                .url(UPLOAD_ENDPOINT)
                .post(requestBody.build())
                .addHeader("Authorization", "Bearer ${getAuthToken()}")
                .build()
            
            // 6. 執行上傳
            val response = client.newCall(request).execute()
            
            if (response.isSuccessful) {
                val responseBody = response.body?.string()
                Log.d(TAG, "上傳成功: $responseBody")
                
                // 儲存到本地資料庫
                saveToLocalDatabase(annotationData)
                
                UploadResult.Success(
                    annotationId = annotationData.annotationId,
                    message = "標註資料上傳成功",
                    serverResponse = responseBody ?: ""
                )
            } else {
                Log.e(TAG, "上傳失敗: ${response.code} - ${response.message}")
                UploadResult.Failed(
                    errorCode = response.code,
                    errorMessage = response.message,
                    annotationId = annotationData.annotationId
                )
            }
            
        } catch (e: Exception) {
            Log.e(TAG, "上傳過程中發生錯誤", e)
            
            // 儲存到本地待上傳佇列
            saveToPendingQueue(annotationData)
            
            UploadResult.Failed(
                errorCode = -1,
                errorMessage = e.message ?: "未知錯誤",
                annotationId = annotationData.annotationId
            )
        }
    }
    
    /**
     * 驗證醫師身份
     */
    suspend fun validateDoctor(
        doctorId: String,
        password: String,
        hospital: String
    ): AuthResult = withContext(Dispatchers.IO) {
        try {
            val requestBody = JSONObject().apply {
                put("doctor_id", doctorId)
                put("password", password)
                put("hospital", hospital)
            }.toString().toRequestBody("application/json".toMediaType())
            
            val request = Request.Builder()
                .url(AUTH_ENDPOINT)
                .post(requestBody)
                .build()
            
            val response = client.newCall(request).execute()
            
            if (response.isSuccessful) {
                val responseBody = response.body?.string()
                val jsonResponse = JSONObject(responseBody ?: "{}")
                
                if (jsonResponse.optBoolean("success", false)) {
                    val token = jsonResponse.optString("token", "")
                    saveAuthToken(token)
                    
                    AuthResult.Success(
                        doctorId = doctorId,
                        doctorName = jsonResponse.optString("doctor_name", "Dr. $doctorId"),
                        hospital = hospital,
                        token = token
                    )
                } else {
                    AuthResult.Failed(
                        errorMessage = jsonResponse.optString("message", "認證失敗")
                    )
                }
            } else {
                AuthResult.Failed(
                    errorMessage = "伺服器錯誤: ${response.code}"
                )
            }
            
        } catch (e: Exception) {
            Log.e(TAG, "醫師認證失敗", e)
            AuthResult.Failed(
                errorMessage = e.message ?: "網路連線錯誤"
            )
        }
    }
    
    /**
     * 檢查上傳狀態
     */
    suspend fun checkUploadStatus(annotationId: String): StatusResult = withContext(Dispatchers.IO) {
        try {
            val request = Request.Builder()
                .url("$STATUS_ENDPOINT/$annotationId")
                .addHeader("Authorization", "Bearer ${getAuthToken()}")
                .build()
            
            val response = client.newCall(request).execute()
            
            if (response.isSuccessful) {
                val responseBody = response.body?.string()
                val jsonResponse = JSONObject(responseBody ?: "{}")
                
                StatusResult.Success(
                    annotationId = annotationId,
                    status = jsonResponse.optString("status", "unknown"),
                    message = jsonResponse.optString("message", ""),
                    timestamp = jsonResponse.optLong("timestamp", System.currentTimeMillis())
                )
            } else {
                StatusResult.Failed(
                    errorMessage = "無法取得狀態: ${response.code}"
                )
            }
            
        } catch (e: Exception) {
            Log.e(TAG, "檢查狀態失敗", e)
            StatusResult.Failed(
                errorMessage = e.message ?: "網路連線錯誤"
            )
        }
    }
    
    /**
     * 準備影像檔案
     */
    private fun prepareImageFile(uri: Uri): File? {
        return try {
            val inputStream = context.contentResolver.openInputStream(uri)
            val timestamp = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.getDefault()).format(Date())
            val fileName = "wound_image_$timestamp.jpg"
            
            val file = File(context.getDir(LOCAL_STORAGE_PATH, Context.MODE_PRIVATE), fileName)
            val outputStream = FileOutputStream(file)
            
            inputStream?.use { input ->
                outputStream.use { output ->
                    input.copyTo(output)
                }
            }
            
            file
        } catch (e: Exception) {
            Log.e(TAG, "準備影像檔案失敗", e)
            null
        }
    }
    
    /**
     * 儲存認證 Token
     */
    private fun saveAuthToken(token: String) {
        context.getSharedPreferences("auth_prefs", Context.MODE_PRIVATE)
            .edit()
            .putString("auth_token", token)
            .putLong("token_timestamp", System.currentTimeMillis())
            .apply()
    }
    
    /**
     * 取得認證 Token
     */
    private fun getAuthToken(): String {
        return context.getSharedPreferences("auth_prefs", Context.MODE_PRIVATE)
            .getString("auth_token", "") ?: ""
    }
    
    /**
     * 儲存到本地資料庫
     */
    private fun saveToLocalDatabase(annotationData: WoundAnnotationData) {
        // 這裡應該實作 Room 資料庫儲存
        // 暫時使用 SharedPreferences 作為簡單儲存
        val prefs = context.getSharedPreferences("annotations_db", Context.MODE_PRIVATE)
        prefs.edit()
            .putString("annotation_${annotationData.annotationId}", annotationData.toJson())
            .apply()
        
        Log.d(TAG, "標註資料已儲存到本地: ${annotationData.annotationId}")
    }
    
    /**
     * 儲存到待上傳佇列
     */
    private fun saveToPendingQueue(annotationData: WoundAnnotationData) {
        val prefs = context.getSharedPreferences("pending_uploads", Context.MODE_PRIVATE)
        val pendingList = prefs.getStringSet("pending_annotations", mutableSetOf())?.toMutableSet() ?: mutableSetOf()
        pendingList.add(annotationData.annotationId)
        
        prefs.edit()
            .putStringSet("pending_annotations", pendingList)
            .putString("pending_${annotationData.annotationId}", annotationData.toJson())
            .apply()
        
        Log.d(TAG, "標註資料已加入待上傳佇列: ${annotationData.annotationId}")
    }
}

/**
 * 上傳結果
 */
sealed class UploadResult {
    data class Success(
        val annotationId: String,
        val message: String,
        val serverResponse: String
    ) : UploadResult()
    
    data class Failed(
        val errorCode: Int,
        val errorMessage: String,
        val annotationId: String
    ) : UploadResult()
}

/**
 * 認證結果
 */
sealed class AuthResult {
    data class Success(
        val doctorId: String,
        val doctorName: String,
        val hospital: String,
        val token: String
    ) : AuthResult()
    
    data class Failed(
        val errorMessage: String
    ) : AuthResult()
}

/**
 * 狀態檢查結果
 */
sealed class StatusResult {
    data class Success(
        val annotationId: String,
        val status: String,
        val message: String,
        val timestamp: Long
    ) : StatusResult()
    
    data class Failed(
        val errorMessage: String
    ) : StatusResult()
} 