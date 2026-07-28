package com.woundmeasurement.app.data.dao

import androidx.room.*
import com.woundmeasurement.app.data.entity.ConsentEntity
import java.util.Date

@Dao
interface ConsentDao {

    @Insert
    suspend fun insert(consent: ConsentEntity): Long

    /** 該病患**目前有效**的同意（最新一筆未撤回者）。量測前與送訓練標註前都以此為準。 */
    @Query("""SELECT * FROM consents WHERE patientId = :patientId AND withdrawnAt IS NULL
              ORDER BY signedAt DESC LIMIT 1""")
    suspend fun getActive(patientId: String): ConsentEntity?

    /** 完整歷程（含已撤回）——稽核要看得出「曾經同意、何時撤回」。 */
    @Query("SELECT * FROM consents WHERE patientId = :patientId ORDER BY signedAt DESC")
    suspend fun getHistory(patientId: String): List<ConsentEntity>

    /**
     * 撤回：**留痕不刪除**（與後端 withdrawn.jsonl 墓碑機制一致）。
     * 直接 DELETE 反而不合規：稽核軌跡會看不出這位受試者曾經同意過。
     */
    @Query("UPDATE consents SET withdrawnAt = :at, withdrawReason = :reason WHERE id = :id")
    suspend fun withdraw(id: Long, at: Date, reason: String?)

    @Query("SELECT COUNT(*) FROM consents WHERE patientId = :patientId AND consentCare = 1 AND withdrawnAt IS NULL")
    suspend fun countActiveCare(patientId: String): Int
}
