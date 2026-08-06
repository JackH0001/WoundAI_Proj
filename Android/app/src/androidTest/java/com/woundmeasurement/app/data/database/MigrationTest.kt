package com.woundmeasurement.app.data.database

import android.content.Context
import android.database.sqlite.SQLiteDatabase
import androidx.room.Room
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

/**
 * 病歷資料庫遷移測試：**舊版有資料 → 升級後一筆都不能少**。
 *
 * ## 為什麼一定要有這個測試
 *
 * v1 曾經設了 `fallbackToDestructiveMigration()`——那個旗標的語意是「schema 一改就把整個
 * 資料庫刪掉重建」。放在一般 App 是方便，放在**病歷**上是資料滅失：開發者改個欄位，
 * 醫護手機上所有病患與量測紀錄就沒了，而且**不會有任何錯誤訊息**。
 * 旗標移除了，但沒有測試的話，下一個人加欄位忘了寫 Migration，症狀會是 App 崩潰（好一點）
 * 或有人為了讓它別崩而把旗標加回來（災難）。這個測試就是那道欄杆。
 *
 * ## 為什麼不用 MigrationTestHelper
 *
 * `MigrationTestHelper` 需要 `exportSchema = true` 並且**歷史每一版的 schema JSON 都要在**。
 * 這個專案的 v1/v2 JSON 從來沒導出過，事後補寫等於用手抄的 schema 去驗證手抄的 Migration，
 * 兩邊錯在一起就測不出來。
 *
 * 這裡改用更直接也更強的做法：**用原生 SQL 建出舊版資料庫、灌真實資料，再讓 Room 自己開啟**。
 * Room 開啟時會跑 Migration 並**用它自己從 Entity 產生的期望 schema 去驗證結果**
 * （`RoomOpenHelper.onValidateSchema`）。所以驗證基準來自 Entity 本身，不是我抄的。
 * 欄位型別、NOT NULL、索引任何一項對不上都會拋 `IllegalStateException`。
 *
 * ## 跑法
 *
 *     .\gradlew :app:connectedDebugAndroidTest
 *
 * 需要一台連線的裝置或模擬器。
 */
@RunWith(AndroidJUnit4::class)
class MigrationTest {

    private val TEST_DB = "migration_test.db"
    private lateinit var ctx: Context

    @Before
    fun setUp() {
        ctx = InstrumentationRegistry.getInstrumentation().targetContext
        // 用獨立的檔名，絕不碰真實病歷庫 wound_measurement_database
        ctx.deleteDatabase(TEST_DB)
    }

    // ---------- 舊版 schema（手寫，模擬升級前手機上的實際狀態） ----------

    /** v1：Sprint N1 之前，只有 patients + measurements 兩張表。 */
    private fun createV1(db: SQLiteDatabase) {
        db.execSQL(
            """CREATE TABLE `patients` (
                `id` TEXT PRIMARY KEY NOT NULL, `name` TEXT NOT NULL, `birthDate` TEXT NOT NULL,
                `gender` TEXT NOT NULL, `medicalRecordNumber` TEXT NOT NULL, `department` TEXT NOT NULL,
                `registrationTime` INTEGER NOT NULL, `lastVisitTime` INTEGER, `notes` TEXT)"""
        )
        db.execSQL(
            """CREATE TABLE `measurements` (
                `id` INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, `patientId` TEXT,
                `timestamp` INTEGER NOT NULL, `hasWound` INTEGER NOT NULL, `confidence` REAL NOT NULL,
                `estimatedArea` REAL, `estimatedVolume` REAL, `woundType` TEXT, `quality` TEXT NOT NULL,
                `processingTime` INTEGER NOT NULL, `imagePath` TEXT NOT NULL, `dataPath` TEXT NOT NULL,
                `notes` TEXT, `isPatientIdentified` INTEGER NOT NULL,
                FOREIGN KEY(`patientId`) REFERENCES `patients`(`id`)
                  ON UPDATE NO ACTION ON DELETE CASCADE)"""
        )
        db.version = 1
    }

    /** v2：跑完 MIGRATION_1_2 之後應有的狀態。直接沿用正式的 Migration，避免手抄失真。 */
    private fun upgradeToV2(db: SQLiteDatabase) {
        WoundMeasurementDatabase.MIGRATION_1_2.migrate(FrameworkDbWrapper(db))
        db.version = 2
    }

    private fun seedPatientAndMeasurements(db: SQLiteDatabase, n: Int) {
        // ⚠ 一定要**列出欄位名**。用 `INSERT INTO patients VALUES (...)` 的話，
        // 欄位數要跟當下 schema 完全一致——而這個 helper 同時服務 v1（9 欄）與
        // 「已跑完 MIGRATION_1_2」的 v2（多了 mrnHash，10 欄），值的數量對不上就會炸。
        // 這正是 v2 那條測試第一次執行時失敗的原因。
        db.execSQL(
            """INSERT INTO patients
               (id,name,birthDate,gender,medicalRecordNumber,department,registrationTime,lastVisitTime,notes)
               VALUES ('p1','enc1:NAME','1950-01-01','M','enc1:MRN','整外',1000,NULL,NULL)"""
        )
        for (i in 1..n) {
            db.execSQL(
                """INSERT INTO measurements
                   (patientId,timestamp,hasWound,confidence,estimatedArea,estimatedVolume,woundType,
                    quality,processingTime,imagePath,dataPath,notes,isPatientIdentified)
                   VALUES ('p1',?,1,0.9,?,NULL,'DM','good',120,'','','第${i}次',1)""",
                arrayOf<Any>(1000L + i, 6.0 + i)
            )
        }
    }

    private fun openRoom(): WoundMeasurementDatabase =
        Room.databaseBuilder(ctx, WoundMeasurementDatabase::class.java, TEST_DB)
            .addMigrations(
                WoundMeasurementDatabase.MIGRATION_1_2,
                WoundMeasurementDatabase.MIGRATION_2_3,
                WoundMeasurementDatabase.MIGRATION_3_4,
                WoundMeasurementDatabase.MIGRATION_4_5,
                WoundMeasurementDatabase.MIGRATION_5_6
            )
            // 刻意**不加** fallbackToDestructiveMigration：這個測試存在的意義就是抓住
            // 「缺 Migration 導致資料被清空」。加了它，測試會靜默通過而資料早就沒了。
            .build()

    // ---------- 測試 ----------

    @Test
    fun v2WithData_migratesToV3_withoutLosingRows() {
        val path = ctx.getDatabasePath(TEST_DB)
        path.parentFile?.mkdirs()
        SQLiteDatabase.openOrCreateDatabase(path, null).use { db ->
            createV1(db)
            upgradeToV2(db)
            seedPatientAndMeasurements(db, 3)
        }

        // Room 開啟 → 跑 MIGRATION_2_3 → 用 Entity 產生的期望 schema 驗證結果。
        // 欄位型別/NOT NULL/索引任一對不上都會在這裡拋 IllegalStateException。
        val room = openRoom()
        try {
            val rows = runBlocking { room.measurementDao().getAllMeasurementsOnce() }
            assertEquals("升級後量測筆數必須不變", 3, rows.size)

            val sorted = rows.sortedBy { it.timestamp.time }
            assertEquals("面積不可被改動", 7.0, sorted[0].estimatedArea!!, 1e-9)
            assertEquals("備註不可被改動", "第1次", sorted[0].notes)
            assertEquals("病患綁定不可斷", "p1", sorted[0].patientId)

            // v3 新欄位：既有列應為「沒有值」，而 annotationSubmitted 是 NOT NULL DEFAULT 0，
            // 對應事實「舊紀錄從未送出過訓練標註」——不是 null，也不該是 true。
            assertEquals(null, sorted[0].gtPolygon)
            assertEquals(null, sorted[0].imageW)
            assertEquals(false, sorted[0].annotationSubmitted)
            // v4：既有列一律視為「未經醫師確認」。填 true 等於憑空捏造一個驗證事實——
            // 那些紀錄產生時，系統根本沒有記錄醫師是否確認過。
            assertEquals(false, sorted[0].doctorVerified)
            // v5：組織比例必須是 **null 而不是 0.0**。
            // 0.0 會被時間軸讀成「當時完全沒有肉芽」——一個看起來完全合理的假數據，
            // 而且沒有任何跡象顯示它其實是「沒量」。這是本次 migration 唯一容易做錯的地方。
            assertEquals(null, sorted[0].tissueGranulation)
            assertEquals(null, sorted[0].tissueOther)
            // v6：舊紀錄沒有柵格快照。續編時會退回由多邊形重建（有損但可用），
            // 而 null 正是「沒有快照」的正確表示——空字串會讓載入端去找一個不存在的檔。
            assertEquals(null, sorted[0].rasterPath)
            assertEquals(null, sorted[0].rasterMeta)
        } finally {
            room.close()
        }
    }

    @Test
    fun v1WithData_migratesAllTheWayToV3() {
        val path = ctx.getDatabasePath(TEST_DB)
        path.parentFile?.mkdirs()
        SQLiteDatabase.openOrCreateDatabase(path, null).use { db ->
            createV1(db)
            seedPatientAndMeasurements(db, 2)
        }

        // 跨兩支 Migration 連續升級。真實情境：久未更新的裝置一次跳好幾版。
        val room = openRoom()
        try {
            val rows = runBlocking { room.measurementDao().getAllMeasurementsOnce() }
            assertEquals("v1→v3 量測筆數必須不變", 2, rows.size)

            // v2 才出現的表要能用（Room 驗證過 schema，這裡再確認真的可寫）
            val caseId = runBlocking {
                room.woundCaseDao().insert(
                    com.woundmeasurement.app.data.entity.WoundCaseEntity(
                        patientId = "p1",
                        wdCode = com.woundmeasurement.app.data.entity.WoundCaseEntity.newWdCode(),
                        bodySite = "薦骨", woundType = "壓瘡"
                    )
                )
            }
            assertTrue("wound_cases 應可寫入", caseId > 0)
            assertNotNull(runBlocking { room.woundCaseDao().getById(caseId) })
        } finally {
            room.close()
        }
    }

    @Test
    fun freshInstall_createsV3Directly() {
        // 全新安裝不跑 Migration，Room 直接依 Entity 建表。這條確保 Migration 的結果
        // 與「從零建立」的 schema 一致——兩者分岔的話，只有部分使用者會遇到問題，最難查。
        val room = openRoom()
        try {
            val rows = runBlocking { room.measurementDao().getAllMeasurementsOnce() }
            assertEquals(0, rows.size)
        } finally {
            room.close()
        }
    }

    /**
     * 把 framework 的 [SQLiteDatabase] 包成 Room 的 `SupportSQLiteDatabase`，
     * 這樣就能直接呼叫正式的 Migration 物件來建出 v2，而不是再手抄一份 SQL。
     *
     * 只實作 `execSQL`——Migration 只用得到它。其餘方法一律拋例外：**刻意讓它壞得大聲**，
     * 免得日後有人在 Migration 裡用了別的 API，測試卻靜默給出錯誤的結果。
     */
    private class FrameworkDbWrapper(private val db: SQLiteDatabase) :
        androidx.sqlite.db.SupportSQLiteDatabase {
        override fun execSQL(sql: String) = db.execSQL(sql)
        override fun execSQL(sql: String, bindArgs: Array<out Any?>) = db.execSQL(sql, bindArgs)
        private fun no(): Nothing = throw UnsupportedOperationException(
            "測試用包裝只支援 execSQL；Migration 用到了其他 API，請擴充這個包裝"
        )
        override val attachedDbs get() = no()
        override val isDatabaseIntegrityOk get() = no()
        override val isDbLockedByCurrentThread get() = no()
        override val isOpen get() = db.isOpen
        override val isReadOnly get() = no()
        override val isWriteAheadLoggingEnabled get() = no()
        override val maximumSize get() = no()
        override var pageSize: Long
            get() = no()
            set(_) = no()
        override val path: String? get() = db.path
        override var version: Int
            get() = db.version
            set(v) { db.version = v }
        override fun beginTransaction() = db.beginTransaction()
        override fun beginTransactionNonExclusive() = db.beginTransactionNonExclusive()
        override fun beginTransactionWithListener(
            transactionListener: android.database.sqlite.SQLiteTransactionListener
        ) = no()
        override fun beginTransactionWithListenerNonExclusive(
            transactionListener: android.database.sqlite.SQLiteTransactionListener
        ) = no()
        override fun close() = db.close()
        override fun compileStatement(sql: String) = no()
        override fun delete(table: String, whereClause: String?, whereArgs: Array<out Any?>?) = no()
        override fun disableWriteAheadLogging() = no()
        override fun enableWriteAheadLogging() = no()
        override fun endTransaction() = db.endTransaction()
        override fun inTransaction() = db.inTransaction()
        override fun insert(table: String, conflictAlgorithm: Int, values: android.content.ContentValues) = no()
        override fun needUpgrade(newVersion: Int) = no()
        override fun query(query: String) = db.rawQuery(query, null)
        override fun query(query: String, bindArgs: Array<out Any?>) = no()
        override fun query(query: androidx.sqlite.db.SupportSQLiteQuery) = no()
        override fun query(
            query: androidx.sqlite.db.SupportSQLiteQuery,
            cancellationSignal: android.os.CancellationSignal?
        ) = no()
        override fun setForeignKeyConstraintsEnabled(enabled: Boolean) = no()
        override fun setLocale(locale: java.util.Locale) = no()
        override fun setMaxSqlCacheSize(cacheSize: Int) = no()
        override fun setMaximumSize(numBytes: Long) = no()
        override fun setTransactionSuccessful() = db.setTransactionSuccessful()
        override fun update(
            table: String, conflictAlgorithm: Int,
            values: android.content.ContentValues, whereClause: String?, whereArgs: Array<out Any?>?
        ) = no()
        override fun yieldIfContendedSafely() = no()
        override fun yieldIfContendedSafely(sleepAfterYieldDelayMillis: Long) = no()
    }
}
