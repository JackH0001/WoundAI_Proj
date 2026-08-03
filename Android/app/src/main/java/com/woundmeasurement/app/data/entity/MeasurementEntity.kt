package com.woundmeasurement.app.data.entity

import androidx.room.Entity
import androidx.room.ForeignKey
import androidx.room.Index
import androidx.room.PrimaryKey
import java.util.Date

/**
 * 單次量測紀錄。Sprint N1 起以 [caseId] 綁定傷口個案（時間軸的分組依據）。
 *
 * ⚠ `caseId` 刻意**只建索引、不加外鍵**：SQLite 無法對既有表 ALTER 加 FK，
 * 要加就得整表重建搬資料。這張表是真實病歷，重建的風險高於它換來的好處
 * （而我們這一輪的 P0 正是「資料庫升級不可滅失資料」）。參照完整性改由
 * `CaseRepository` 在寫入時把關；日後若真要加 FK，請併大版本一次重建。
 */
@Entity(
    tableName = "measurements",
    foreignKeys = [
        ForeignKey(
            entity = PatientEntity::class,
            parentColumns = ["id"],
            childColumns = ["patientId"],
            onDelete = ForeignKey.CASCADE
        )
    ],
    indices = [Index("patientId"), Index("caseId")]
)
data class MeasurementEntity(
    @PrimaryKey(autoGenerate = true)
    val id: Long = 0,
    val patientId: String?,
    val timestamp: Date,
    val hasWound: Boolean,
    val confidence: Double,
    val estimatedArea: Double?,
    val estimatedVolume: Double?,
    val woundType: String?,
    val quality: String,
    val processingTime: Long,
    val imagePath: String,
    val dataPath: String,
    val notes: String? = null,
    val isPatientIdentified: Boolean = patientId != null,
    // ---- Sprint N1 新增：綁定傷口個案與雲端影像 ----
    /** 傷口個案 id。時間軸依此分組——沒有它，兩位病患的傷口會畫在同一條趨勢線。 */
    val caseId: Long? = null,
    /** 送出當下的去識別代碼快照（來自 WoundCaseEntity.wdCode）。 */
    val wdCode: String? = null,
    /** 後端 classify 的影像雜湊：讓本機病歷與雲端飛輪樣本對得起來。 */
    val imageId: String? = null,
    val mmPerPx: Double? = null,
    val route: String? = null,
    /** clinical / sample / phantom / external。與飛輪 source 同義。 */
    val source: String? = null,
    // ---- v3：讓「回頭修邊」與「補送標註」不必重測一遍 ----
    /**
     * 醫師修邊後的 GT 輪廓，JSON `[[x,y],…]`，座標空間＝[imageW]×[imageH]。
     *
     * ⚠ 這是 v2 漏掉的關鍵欄位：先前輪廓只活在 `MeasureViewModel.lastPolygon`（記憶體），
     * 離開量測頁就消失 → 重簽同意後要補送訓練標註，只能整個重測。
     */
    val gtPolygon: String? = null,
    val imageW: Int? = null,
    val imageH: Int? = null,
    /** 醫師輸入的滲液 0–3（PUSH 子分）。補送標註時要一起帶。 */
    val exudate: Int? = null,
    /** 修邊後與 AI 原始遮罩的 IoU（1.0＝未改）。 */
    val correctionIou: Double? = null,
    /** 是否已送出訓練標註（避免重複送、也讓時間軸看得出哪幾筆進了飛輪）。 */
    val annotationSubmitted: Boolean = false
)