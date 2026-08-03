package com.woundmeasurement.app.data.entity

import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey
import java.util.Date

/**
 * 病患主檔。**PII 永不離開本機**（設計文件 §1.1）：後端只認 `WoundCaseEntity.wdCode`。
 *
 * `name` / `medicalRecordNumber` 以 Android Keystore AES-GCM 加密後入庫，DB 檔被取走也是密文。
 * `mrnHash` 供查重比對，不可還原。
 *
 * ⚠ 加解密**刻意不做成 Room TypeConverter**：Room 的 TypeConverter 是依「型別」套用的，
 * 掛在 `String` 上會把全庫每一個字串欄位都加密（包含 notes、量測的 route…），
 * 完全不是我們要的。改由 [com.woundmeasurement.app.data.repo.CaseRepository] 明確加解密，
 * 所以**請一律經由該 Repository 存取本表，不要直接呼叫 PatientDao**。
 */
@Entity(tableName = "patients", indices = [Index("mrnHash")])
data class PatientEntity(
    @PrimaryKey
    val id: String,
    /** 加密欄位（由 CaseRepository 加解密；經由 Repository 取得時是明文）。 */
    val name: String,
    val birthDate: String,
    val gender: String,
    /** 加密欄位。顯示時請用 `PhiCrypto.maskMrn()` 遮蔽，不要把完整病歷號攤在清單上。 */
    val medicalRecordNumber: String,
    val department: String,
    val registrationTime: Date,
    val lastVisitTime: Date? = null,
    val notes: String? = null,
    /**
     * 病歷號的不可逆雜湊，查重用。以 `PhiCrypto.hashMrn()` 填入。
     *
     * ⚠ 實作是 **Keystore HMAC-SHA256**（金鑰不可匯出），**不是**「加鹽 SHA-256」。
     * 這個區別是實質的：病歷號熵極低（院內多為固定長度數字），而鹽寫在原始碼裡對攻擊者是公開的，
     * 字典攻擊幾秒就能反查回明文；HMAC 的金鑰躺在 Keystore 無法取出，才擋得住離線爆破。
     * （稽核文件對照時會查這一條，請勿把註解改回「加鹽 SHA-256」。）
     */
    val mrnHash: String? = null
)