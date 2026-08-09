package com.woundmeasurement.app.data.dao

import androidx.room.*
import com.woundmeasurement.app.data.entity.WoundCaseEntity

@Dao
interface WoundCaseDao {

    // ⚠ 參數不可命名為 `case`:Room 2.6.1 預設產 **Java** 程式碼(room.generateKotlin 到 2.7 才預設開),
    // `case` 是 Java 保留字 → JavaPoet 的 SourceVersion.isName() 直接拒絕,kspDebugKotlin 會掛。
    @Insert
    suspend fun insert(woundCase: WoundCaseEntity): Long

    @Update
    suspend fun update(woundCase: WoundCaseEntity)

    @Query("SELECT * FROM wound_cases WHERE patientId = :patientId AND closedAt IS NULL ORDER BY createdAt DESC")
    suspend fun getOpenCasesByPatient(patientId: String): List<WoundCaseEntity>

    @Query("SELECT * FROM wound_cases WHERE patientId = :patientId ORDER BY createdAt DESC")
    suspend fun getAllCasesByPatient(patientId: String): List<WoundCaseEntity>

    @Query("SELECT * FROM wound_cases WHERE id = :id")
    suspend fun getById(id: Long): WoundCaseEntity?

    /** 由去識別代碼回查個案——撤回同意、對帳雲端樣本時用得到。 */
    @Query("SELECT * FROM wound_cases WHERE wdCode = :wdCode")
    suspend fun getByWdCode(wdCode: String): WoundCaseEntity?

    /** 該病患名下所有 wdCode：撤回訓練同意時要把這些一併送後端排除。 */
    @Query("SELECT wdCode FROM wound_cases WHERE patientId = :patientId")
    suspend fun getWdCodesByPatient(patientId: String): List<String>

    @Query("SELECT COUNT(*) FROM wound_cases WHERE wdCode = :wdCode")
    suspend fun countByWdCode(wdCode: String): Int

    @Query("DELETE FROM wound_cases WHERE id = :id")
    suspend fun deleteById(id: Long)

    @Query("UPDATE wound_cases SET closedAt = :closedAt WHERE id = :id")
    suspend fun close(id: Long, closedAt: java.util.Date)
}
