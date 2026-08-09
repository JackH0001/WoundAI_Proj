package com.woundmeasurement.app.data.dao

import androidx.room.*
import com.woundmeasurement.app.data.entity.MeasurementEntity
import kotlinx.coroutines.flow.Flow
import java.util.Date

@Dao
interface MeasurementDao {
    
    @Query("SELECT * FROM measurements ORDER BY timestamp DESC")
    fun getAllMeasurements(): Flow<List<MeasurementEntity>>

    /**
     * 一次性全表讀取（非 Flow）。給遷移測試與資料匯出用。
     *
     * UI **不要**用這個：時間軸必須依個案分組（`getMeasurementsByCase`），
     * 全表撈會把不同病患、不同部位的傷口混成同一條趨勢線。
     */
    @Query("SELECT * FROM measurements ORDER BY timestamp DESC")
    suspend fun getAllMeasurementsOnce(): List<MeasurementEntity>

    /**
     * 未歸戶紀錄（`caseId IS NULL`）——快速量測產生的那些。
     *
     * 這類紀錄不屬於任何傷口個案，所以不會出現在任何個案時間軸裡。先前它們存進 DB 之後
     * 就**再也叫不出來**（使用者反映「照片消失、只剩數據，不知道存到哪」）。
     * 給它一個明確的入口，比讓它們無聲堆積在資料庫裡好——後者遲早變成沒人知道
     * 是什麼、也不敢刪的資料。
     */
    @Query("SELECT * FROM measurements WHERE caseId IS NULL ORDER BY timestamp DESC")
    fun getUnassignedMeasurements(): kotlinx.coroutines.flow.Flow<List<MeasurementEntity>>

    @Query("SELECT * FROM measurements WHERE patientId = :patientId ORDER BY timestamp DESC")
    fun getMeasurementsByPatient(patientId: String): Flow<List<MeasurementEntity>>
    
    @Query("SELECT * FROM measurements WHERE isPatientIdentified = 0 ORDER BY timestamp DESC")
    fun getGeneralMeasurements(): Flow<List<MeasurementEntity>>
    
    @Query("SELECT * FROM measurements WHERE patientId = :patientId AND timestamp >= :startDate ORDER BY timestamp DESC")
    fun getMeasurementsByPatientAndDateRange(patientId: String, startDate: Date): Flow<List<MeasurementEntity>>
    
    // ---- Sprint N1：時間軸的正確分組單位是「傷口個案」，不是病患也不是全表 ----
    // 一位病患可能同時有薦骨壓瘡與右足潰瘍，癒合曲線完全不同；
    // 原本 WoundTimelineScreen 用 getAllMeasurements() 全表撈，會把不同病患的傷口畫成同一條線。
    @Query("SELECT * FROM measurements WHERE caseId = :caseId ORDER BY timestamp ASC")
    fun getMeasurementsByCase(caseId: Long): Flow<List<MeasurementEntity>>

    @Query("SELECT * FROM measurements WHERE caseId = :caseId ORDER BY timestamp DESC LIMIT 1")
    suspend fun getLatestByCase(caseId: Long): MeasurementEntity?

    @Query("SELECT * FROM measurements WHERE caseId = :caseId ORDER BY timestamp ASC LIMIT 1")
    suspend fun getFirstByCase(caseId: Long): MeasurementEntity?

    @Query("SELECT * FROM measurements WHERE id = :id")
    suspend fun getById(id: Long): MeasurementEntity?

    @Query("DELETE FROM measurements WHERE id = :id")
    suspend fun deleteById(id: Long)

    @Query("SELECT COUNT(*) FROM measurements WHERE caseId = :caseId")
    suspend fun getCountByCase(caseId: Long): Int

    // ---- v3：保存期限清理與補送標註 ----
    /**
     * 結案逾 N 天、且**還留著影像**的量測。保存期限清理用。
     * 只刪影像不刪列：面積與趨勢是病歷，不能因為逾期就毀掉。
     */
    @Query("""SELECT m.* FROM measurements m JOIN wound_cases c ON c.id = m.caseId
              WHERE c.closedAt IS NOT NULL AND c.closedAt < :cutoff
                AND m.imagePath IS NOT NULL AND m.imagePath != ''""")
    suspend fun getExpiredWithImages(cutoff: Date): List<MeasurementEntity>

    /** 清掉影像路徑（實體檔由 LocalImageStore 刪）。 */
    @Query("UPDATE measurements SET imagePath = '' WHERE id = :id")
    suspend fun clearImagePath(id: Long)

    /** 該個案還沒送出訓練標註、且具備補送條件（有輪廓與影像綁定）的紀錄。 */
    @Query("""SELECT * FROM measurements WHERE caseId = :caseId
              AND annotationSubmitted = 0 AND gtPolygon IS NOT NULL AND imageId IS NOT NULL
              ORDER BY timestamp DESC""")
    suspend fun getPendingAnnotation(caseId: Long): List<MeasurementEntity>

    @Query("UPDATE measurements SET annotationSubmitted = 1 WHERE id = :id")
    suspend fun markAnnotationSubmitted(id: Long)

    /** 最近有量測的個案 id（依最後量測時間排序）。主畫面「最近就診」用。 */
    @Query("""SELECT caseId FROM measurements WHERE caseId IS NOT NULL
              GROUP BY caseId ORDER BY MAX(timestamp) DESC LIMIT :limit""")
    suspend fun getRecentCaseIds(limit: Int): List<Long>

    @Query("SELECT * FROM measurements WHERE id = :measurementId")
    suspend fun getMeasurementById(measurementId: Long): MeasurementEntity?
    
    @Query("SELECT * FROM measurements WHERE patientId = :patientId ORDER BY timestamp DESC LIMIT 1")
    suspend fun getLatestMeasurementByPatient(patientId: String): MeasurementEntity?
    
    @Insert
    suspend fun insertMeasurement(measurement: MeasurementEntity): Long
    
    @Update
    suspend fun updateMeasurement(measurement: MeasurementEntity)
    
    @Delete
    suspend fun deleteMeasurement(measurement: MeasurementEntity)
    
    @Query("DELETE FROM measurements WHERE patientId = :patientId")
    suspend fun deleteAllMeasurementsByPatient(patientId: String)
    
    @Query("SELECT COUNT(*) FROM measurements WHERE patientId = :patientId")
    suspend fun getMeasurementCountByPatient(patientId: String): Int
    
    @Query("SELECT COUNT(*) FROM measurements WHERE isPatientIdentified = 0")
    suspend fun getGeneralMeasurementCount(): Int
    
    @Query("SELECT AVG(estimatedArea) FROM measurements WHERE patientId = :patientId AND estimatedArea IS NOT NULL")
    suspend fun getAverageAreaByPatient(patientId: String): Double?
    
    @Query("SELECT AVG(estimatedVolume) FROM measurements WHERE patientId = :patientId AND estimatedVolume IS NOT NULL")
    suspend fun getAverageVolumeByPatient(patientId: String): Double?
    
    @Query("SELECT MIN(estimatedArea) FROM measurements WHERE patientId = :patientId AND estimatedArea IS NOT NULL")
    suspend fun getMinAreaByPatient(patientId: String): Double?
    
    @Query("SELECT MAX(estimatedArea) FROM measurements WHERE patientId = :patientId AND estimatedArea IS NOT NULL")
    suspend fun getMaxAreaByPatient(patientId: String): Double?
} 