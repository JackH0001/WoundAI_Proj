package com.woundmeasurement.app.data.entity

import android.os.Parcelable
import androidx.room.Entity
import androidx.room.ForeignKey
import androidx.room.Index
import androidx.room.PrimaryKey
import kotlinx.parcelize.Parcelize
import java.security.SecureRandom
import java.util.Date

/**
 * 傷口個案：一位病患名下的**單一傷口**，是量測與時間軸的分組單位。
 *
 * 為什麼需要這一層(見設計文件 §1.2)：一位病患可能同時有薦骨壓瘡與右足潰瘍，
 * 兩者癒合曲線完全不同。原本只有 Patient→Measurement 兩層，時間軸把所有量測
 * 混成一條線，臨床上沒有意義。
 *
 * `wdCode` 是**唯一會離開本機**的識別子：後端飛輪只認它，永遠看不到姓名/病歷號。
 */
@Entity(
    tableName = "wound_cases",
    foreignKeys = [ForeignKey(
        entity = PatientEntity::class,
        parentColumns = ["id"],
        childColumns = ["patientId"],
        onDelete = ForeignKey.CASCADE
    )],
    indices = [Index(value = ["wdCode"], unique = true), Index("patientId")]
)
@Parcelize
data class WoundCaseEntity(
    @PrimaryKey(autoGenerate = true)
    val id: Long = 0,
    val patientId: String,
    /** 去識別代碼,建立時產生後**永不改變**——同一傷口回診仍是同一組,時間軸才串得起來。 */
    val wdCode: String,
    val bodySite: String,          // 薦骨 / 右足跟 / 左小腿…
    val woundType: String,         // 壓瘡 / 糖尿病足 / 燒燙傷…
    val onsetDate: Date? = null,
    val createdAt: Date = Date(),
    val closedAt: Date? = null,    // 結案(癒合/轉出)
    val notes: String? = null      // 建議記紙本同意書編號,雙軌保險
) : Parcelable {
    companion object {
        /**
         * 產生穩定且不易碰撞的 WD-code。
         *
         * ⚠ **不可**再用 `System.currentTimeMillis().takeLast(8)`(舊作法):
         * 10^8 毫秒 ≈ 27.8 小時就循環一次,跨日必碰撞,而匯出時同 code 會覆寫樣本。
         * 這裡用 SecureRandom 取 4 bytes → 8 碼大寫十六進位,並由 DB UNIQUE 索引把關。
         * 格式須符合後端 `^WD-[A-Za-z0-9_-]{1,32}$`。
         */
        fun newWdCode(): String {
            val b = ByteArray(4).also { SecureRandom().nextBytes(it) }
            return "WD-" + b.joinToString("") { "%02X".format(it) }
        }
    }
}
