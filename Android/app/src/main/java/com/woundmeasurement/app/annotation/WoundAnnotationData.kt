package com.woundmeasurement.app.annotation

import android.graphics.Bitmap
import android.net.Uri
import com.google.gson.Gson
import com.google.gson.annotations.SerializedName
import java.text.SimpleDateFormat
import java.util.*

data class WoundAnnotationData(
    // 基本資訊
    @SerializedName("annotation_id")
    val annotationId: String = UUID.randomUUID().toString(),
    
    @SerializedName("doctor_id")
    val doctorId: String = "",
    
    @SerializedName("doctor_name")
    val doctorName: String = "",
    
    @SerializedName("hospital")
    val hospital: String = "",
    
    @SerializedName("annotation_date")
    val annotationDate: String = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.getDefault()).format(Date()),
    
    @SerializedName("image_uri")
    val imageUri: String = "",
    
    // BJWAT 評估標準 (11項)
    @SerializedName("bjwat_size")
    val bjwatSize: Int = 0, // 0-5: 傷口大小
    
    @SerializedName("bjwat_depth")
    val bjwatDepth: Int = 0, // 0-4: 傷口深度
    
    @SerializedName("bjwat_edges")
    val bjwatEdges: Int = 0, // 0-2: 邊緣
    
    @SerializedName("bjwat_necrotic_type")
    val bjwatNecroticType: Int = 0, // 0-3: 壞死組織類型
    
    @SerializedName("bjwat_necrotic_amount")
    val bjwatNecroticAmount: Int = 0, // 0-4: 壞死量
    
    @SerializedName("bjwat_exudate_amount")
    val bjwatExudateAmount: Int = 0, // 0-4: 分泌物量
    
    @SerializedName("bjwat_exudate_type")
    val bjwatExudateType: Int = 0, // 0-3: 分泌物類型
    
    @SerializedName("bjwat_tissue_color")
    val bjwatTissueColor: Int = 0, // 0-3: 組織顏色
    
    @SerializedName("bjwat_granulation")
    val bjwatGranulation: Int = 0, // 0-3: 肉芽
    
    @SerializedName("bjwat_epithelialization")
    val bjwatEpithelialization: Int = 0, // 0-3: 再上皮化
    
    @SerializedName("bjwat_peri_skin")
    val bjwatPeriSkin: Int = 0, // 0-3: 周邊皮膚
    
    // revPWAT 評估標準 (6項)
    @SerializedName("revpwat_necrosis")
    val revPwatNecrosis: Int = 0, // 1-3: 壞死組織
    
    @SerializedName("revpwat_slough")
    val revPwatSlough: Int = 0, // 1-3: Slough (腐肉)
    
    @SerializedName("revpwat_granulation")
    val revPwatGranulation: Int = 0, // 1-3: 肉芽組織
    
    @SerializedName("revpwat_exudate")
    val revPwatExudate: Int = 0, // 1-4: 分泌物
    
    @SerializedName("revpwat_color")
    val revPwatColor: Int = 0, // 0-2: 顏色
    
    @SerializedName("revpwat_depth")
    val revPwatDepth: Int = 0, // 1-3: 深度
    
    // 計算得出的分數
    @SerializedName("bjwat_total_score")
    val bjwatTotalScore: Int = 0,
    
    @SerializedName("revpwat_total_score")
    val revPwatTotalScore: Int = 0,
    
    // 標註品質控制
    @SerializedName("annotation_quality")
    val annotationQuality: String = "pending", // pending, approved, rejected
    
    @SerializedName("reviewer_id")
    val reviewerId: String = "",
    
    @SerializedName("review_notes")
    val reviewNotes: String = "",
    
    // 影像處理相關
    @SerializedName("image_width")
    val imageWidth: Int = 0,
    
    @SerializedName("image_height")
    val imageHeight: Int = 0,
    
    @SerializedName("image_format")
    val imageFormat: String = "",
    
    @SerializedName("file_size_bytes")
    val fileSizeBytes: Long = 0
) {
    
    /**
     * 計算 BJWAT 總分
     */
    fun calculateBJWATScore(): Int {
        return bjwatSize + bjwatDepth + bjwatEdges + bjwatNecroticType + 
               bjwatNecroticAmount + bjwatExudateAmount + bjwatExudateType + 
               bjwatTissueColor + bjwatGranulation + bjwatEpithelialization + 
               bjwatPeriSkin
    }
    
    /**
     * 計算 revPWAT 總分
     */
    fun calculateRevPWATScore(): Int {
        return revPwatNecrosis + revPwatSlough + revPwatGranulation + 
               revPwatExudate + revPwatColor + revPwatDepth
    }
    
    /**
     * 取得 BJWAT 嚴重程度評估
     */
    fun getBJWATSeverity(): String {
        val score = calculateBJWATScore()
        return when {
            score <= 10 -> "輕微"
            score <= 20 -> "中度"
            score <= 30 -> "重度"
            else -> "極重度"
        }
    }
    
    /**
     * 取得 revPWAT 嚴重程度評估
     */
    fun getRevPWATSeverity(): String {
        val score = calculateRevPWATScore()
        return when {
            score <= 6 -> "輕微"
            score <= 12 -> "中度"
            score <= 18 -> "重度"
            else -> "極重度"
        }
    }
    
    /**
     * 檢查標註完整性
     */
    fun isComplete(): Boolean {
        return doctorId.isNotEmpty() && 
               hospital.isNotEmpty() && 
               imageUri.isNotEmpty() &&
               bjwatSize >= 0 && bjwatDepth >= 0 && bjwatEdges >= 0 &&
               bjwatNecroticType >= 0 && bjwatNecroticAmount >= 0 &&
               bjwatExudateAmount >= 0 && bjwatExudateType >= 0 &&
               bjwatTissueColor >= 0 && bjwatGranulation >= 0 &&
               bjwatEpithelialization >= 0 && bjwatPeriSkin >= 0 &&
               revPwatNecrosis >= 0 && revPwatSlough >= 0 &&
               revPwatGranulation >= 0 && revPwatExudate >= 0 &&
               revPwatColor >= 0 && revPwatDepth >= 0
    }
    
    /**
     * 轉換為 JSON 格式
     */
    fun toJson(): String {
        return Gson().toJson(this)
    }
    
    /**
     * 從 JSON 建立物件
     */
    companion object {
        fun fromJson(json: String): WoundAnnotationData {
            return Gson().fromJson(json, WoundAnnotationData::class.java)
        }
    }
    
    /**
     * 建立 COCO 格式的標註資料
     */
    fun toCocoFormat(): Map<String, Any> {
        return mapOf(
            "image_id" to annotationId,
            "category_id" to 1, // 傷口類別
            "segmentation" to listOf<Int>(), // 需要實作分割遮罩
            "area" to 0.0, // 需要實作面積計算
            "bbox" to listOf(0, 0, 0, 0), // 需要實作邊界框
            "iscrowd" to 0,
            "attributes" to mapOf(
                "bjwat_size" to bjwatSize,
                "bjwat_depth" to bjwatDepth,
                "bjwat_edges" to bjwatEdges,
                "bjwat_necrotic_type" to bjwatNecroticType,
                "bjwat_necrotic_amount" to bjwatNecroticAmount,
                "bjwat_exudate_amount" to bjwatExudateAmount,
                "bjwat_exudate_type" to bjwatExudateType,
                "bjwat_tissue_color" to bjwatTissueColor,
                "bjwat_granulation" to bjwatGranulation,
                "bjwat_epithelialization" to bjwatEpithelialization,
                "bjwat_peri_skin" to bjwatPeriSkin,
                "revpwat_necrosis" to revPwatNecrosis,
                "revpwat_slough" to revPwatSlough,
                "revpwat_granulation" to revPwatGranulation,
                "revpwat_exudate" to revPwatExudate,
                "revpwat_color" to revPwatColor,
                "revpwat_depth" to revPwatDepth,
                "bjwat_total_score" to calculateBJWATScore(),
                "revpwat_total_score" to calculateRevPWATScore(),
                "bjwat_severity" to getBJWATSeverity(),
                "revpwat_severity" to getRevPWATSeverity()
            )
        )
    }
}

/**
 * 標註品質控制狀態
 */
enum class AnnotationQuality {
    PENDING,    // 待審核
    APPROVED,   // 已核准
    REJECTED,   // 已拒絕
    NEEDS_REVIEW // 需要重新審核
}

/**
 * 醫師認證狀態
 */
enum class DoctorAuthStatus {
    PENDING,    // 待認證
    APPROVED,   // 已認證
    SUSPENDED,  // 已暫停
    REVOKED     // 已撤銷
}

/**
 * 上傳狀態
 */
enum class UploadStatus {
    PENDING,    // 待上傳
    UPLOADING,  // 上傳中
    SUCCESS,    // 上傳成功
    FAILED      // 上傳失敗
} 