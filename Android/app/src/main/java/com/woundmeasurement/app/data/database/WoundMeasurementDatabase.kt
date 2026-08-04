package com.woundmeasurement.app.data.database

import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.room.TypeConverters
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase
import android.content.Context
import com.woundmeasurement.app.data.dao.ConsentDao
import com.woundmeasurement.app.data.dao.PatientDao
import com.woundmeasurement.app.data.dao.MeasurementDao
import com.woundmeasurement.app.data.dao.WoundCaseDao
import com.woundmeasurement.app.data.entity.ConsentEntity
import com.woundmeasurement.app.data.entity.PatientEntity
import com.woundmeasurement.app.data.entity.MeasurementEntity
import com.woundmeasurement.app.data.entity.WoundCaseEntity
import com.woundmeasurement.app.data.converter.DateConverter

/**
 * 本機病歷資料庫。
 *
 * ⚠ **絕對不可再加回 `fallbackToDestructiveMigration()`**（v1 時期的設定，Sprint N1 移除）。
 * 那個旗標的語意是「schema 一改就把整個資料庫刪掉重建」——放在一般 App 是方便，
 * 放在**病歷**上是資料滅失：開發者改個欄位，醫護手機上所有病患與量測紀錄就沒了，
 * 而且不會有任何錯誤訊息。每次改 schema 都必須寫對應的 [Migration]。
 */
@Database(
    entities = [
        PatientEntity::class,
        MeasurementEntity::class,
        WoundCaseEntity::class,
        ConsentEntity::class
    ],
    version = 4,
    exportSchema = false
)
@TypeConverters(DateConverter::class)
abstract class WoundMeasurementDatabase : RoomDatabase() {

    abstract fun patientDao(): PatientDao
    abstract fun measurementDao(): MeasurementDao
    abstract fun woundCaseDao(): WoundCaseDao
    abstract fun consentDao(): ConsentDao

    companion object {
        @Volatile
        private var INSTANCE: WoundMeasurementDatabase? = null

        /**
         * v1 → v2（Sprint N1 個案化醫療紀錄）。
         *
         * 全部是**加法**：新表 + 既有表加可空欄位 + 建索引。
         * 沒有任何 DROP/重建，既有病患與量測紀錄原樣保留（這正是移除
         * destructive migration 的意義）。
         *
         * `measurements.caseId` 只建索引不加外鍵：SQLite 無法對既有表 ALTER 加 FK，
         * 要加就得整表重建搬資料，對真實病歷風險太高（見 MeasurementEntity 註解）。
         */
        val MIGRATION_1_2 = object : Migration(1, 2) {
            override fun migrate(db: SupportSQLiteDatabase) {
                // 1) 傷口個案：時間軸的分組單位；wdCode 是唯一會離開本機的識別子
                db.execSQL(
                    """CREATE TABLE IF NOT EXISTS `wound_cases` (
                        `id` INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                        `patientId` TEXT NOT NULL,
                        `wdCode` TEXT NOT NULL,
                        `bodySite` TEXT NOT NULL,
                        `woundType` TEXT NOT NULL,
                        `onsetDate` INTEGER,
                        `createdAt` INTEGER NOT NULL,
                        `closedAt` INTEGER,
                        `notes` TEXT,
                        FOREIGN KEY(`patientId`) REFERENCES `patients`(`id`)
                          ON UPDATE NO ACTION ON DELETE CASCADE )"""
                )
                db.execSQL("CREATE UNIQUE INDEX IF NOT EXISTS `index_wound_cases_wdCode` ON `wound_cases` (`wdCode`)")
                db.execSQL("CREATE INDEX IF NOT EXISTS `index_wound_cases_patientId` ON `wound_cases` (`patientId`)")

                // 2) 知情同意：雙層 + 簽名 + 撤回留痕
                db.execSQL(
                    """CREATE TABLE IF NOT EXISTS `consents` (
                        `id` INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                        `patientId` TEXT NOT NULL,
                        `consentCare` INTEGER NOT NULL,
                        `consentTrain` INTEGER NOT NULL,
                        `signaturePng` BLOB,
                        `signedAt` INTEGER NOT NULL,
                        `signerRole` TEXT NOT NULL,
                        `witnessStaff` TEXT,
                        `templateVersion` TEXT NOT NULL,
                        `withdrawnAt` INTEGER,
                        `withdrawReason` TEXT,
                        FOREIGN KEY(`patientId`) REFERENCES `patients`(`id`)
                          ON UPDATE NO ACTION ON DELETE CASCADE )"""
                )
                db.execSQL("CREATE INDEX IF NOT EXISTS `index_consents_patientId` ON `consents` (`patientId`)")

                // 3) 病患加病歷號雜湊（查重用，不可還原）
                db.execSQL("ALTER TABLE `patients` ADD COLUMN `mrnHash` TEXT")
                // 查重是每次新增病患都會做的動作,沒索引就是全表掃描
                db.execSQL("CREATE INDEX IF NOT EXISTS `index_patients_mrnHash` ON `patients` (`mrnHash`)")

                // 4) 量測綁個案與雲端影像
                db.execSQL("ALTER TABLE `measurements` ADD COLUMN `caseId` INTEGER")
                db.execSQL("ALTER TABLE `measurements` ADD COLUMN `wdCode` TEXT")
                db.execSQL("ALTER TABLE `measurements` ADD COLUMN `imageId` TEXT")
                db.execSQL("ALTER TABLE `measurements` ADD COLUMN `mmPerPx` REAL")
                db.execSQL("ALTER TABLE `measurements` ADD COLUMN `route` TEXT")
                db.execSQL("ALTER TABLE `measurements` ADD COLUMN `source` TEXT")
                db.execSQL("CREATE INDEX IF NOT EXISTS `index_measurements_patientId` ON `measurements` (`patientId`)")
                db.execSQL("CREATE INDEX IF NOT EXISTS `index_measurements_caseId` ON `measurements` (`caseId`)")
            }
        }

        /**
         * v2 → v3：讓時間軸的單筆紀錄**自己就足以補送標註與回頭修邊**。
         *
         * v2 只存了面積與 `imageId`，沒存 GT 輪廓 → 醫師重簽同意後要補送訓練標註，
         * 只能整個重測一遍（輪廓只活在記憶體，離開量測頁就沒了）。
         * 影像本體不進 DB，改存在 app 私有目錄的加密檔（`LocalImageStore`），
         * `imagePath` 存檔名——v2 時它一直是空字串。
         *
         * 同樣全是加法，不動既有資料。
         */
        val MIGRATION_2_3 = object : Migration(2, 3) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("ALTER TABLE `measurements` ADD COLUMN `gtPolygon` TEXT")
                db.execSQL("ALTER TABLE `measurements` ADD COLUMN `imageW` INTEGER")
                db.execSQL("ALTER TABLE `measurements` ADD COLUMN `imageH` INTEGER")
                db.execSQL("ALTER TABLE `measurements` ADD COLUMN `exudate` INTEGER")
                db.execSQL("ALTER TABLE `measurements` ADD COLUMN `correctionIou` REAL")
                // NOT NULL + DEFAULT：既有列會被填 0(false)，符合「舊紀錄未曾送出標註」的事實
                db.execSQL("ALTER TABLE `measurements` ADD COLUMN `annotationSubmitted` INTEGER NOT NULL DEFAULT 0")
            }
        }

        /**
         * v3 → v4：`doctor_verified` 的真值落地。
         *
         * 先前送往飛輪的 `doctor_verified` 是硬編碼 true，而醫師在修邊頁按「取消」之後
         * 仍可存檔與送出——等於一筆從未被人看過的 AI 輸出會以「醫師已驗證」進入訓練集。
         *
         * 既有列一律填 **0（未驗證）**。這在語意上是保守但正確的：那些紀錄產生時
         * 系統並沒有記錄醫師是否確認過，填 1 等於憑空捏造一個驗證事實。
         * 代價是舊紀錄無法補送訓練標註——那正是應該的，寧可少一筆也不要一筆假的。
         */
        val MIGRATION_3_4 = object : Migration(3, 4) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("ALTER TABLE `measurements` ADD COLUMN `doctorVerified` INTEGER NOT NULL DEFAULT 0")
            }
        }

        fun getDatabase(context: Context): WoundMeasurementDatabase {
            return INSTANCE ?: synchronized(this) {
                val instance = Room.databaseBuilder(
                    context.applicationContext,
                    WoundMeasurementDatabase::class.java,
                    "wound_measurement_database"
                )
                    .addMigrations(MIGRATION_1_2, MIGRATION_2_3, MIGRATION_3_4)
                    // 刻意不加 fallbackToDestructiveMigration:寧可在開發期因缺 migration 而崩潰,
                    // 也不要在醫護手機上默默刪光病歷。
                    .build()
                INSTANCE = instance
                instance
            }
        }
    }
}
