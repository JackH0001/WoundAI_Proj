package com.woundmeasurement.app.data.entity

import androidx.room.Entity
import androidx.room.ForeignKey
import androidx.room.Index
import androidx.room.PrimaryKey
import java.util.Date

/**
 * 知情同意紀錄（雙層，依 `docs/regulatory/IRB_consent_templates.md:23-26`）。
 *
 * 修正的問題：先前 `consent_train` 在 `BackendClient` 是**硬編碼 true**——
 * 我們送給後端的每一筆都宣稱「已取得訓練同意」，但從來沒有人真的勾選過。
 * 這是實作與同意書不符，屬法規級缺陷。現在後端收到的值來自本表。
 *
 * 撤回採**留痕不刪除**（`withdrawnAt`），與後端 `withdrawn.jsonl` 墓碑機制一致：
 * 稽核軌跡必須看得出「曾經同意、何時撤回」，直接刪掉紀錄反而不合規。
 */
@Entity(
    tableName = "consents",
    foreignKeys = [ForeignKey(
        entity = PatientEntity::class,
        parentColumns = ["id"],
        childColumns = ["patientId"],
        onDelete = ForeignKey.CASCADE
    )],
    indices = [Index("patientId")]
)
data class ConsentEntity(
    @PrimaryKey(autoGenerate = true)
    val id: Long = 0,
    val patientId: String,
    /** ①照護同意（必填）。false 就不該做任何量測——UI 以此擋住量測入口。 */
    val consentCare: Boolean,
    /** ②訓練同意（選填、可撤回）。這才是送後端 annotation 的 `consent_train` 真值。 */
    val consentTrain: Boolean,
    /** 手寫簽名點陣（PNG bytes）。存本機，不隨標註上傳。 */
    val signaturePng: ByteArray? = null,
    val signedAt: Date = Date(),
    val signerRole: String = "patient",   // patient / legal_guardian
    val witnessStaff: String? = null,     // 見證醫護
    /** 同意書版本：改版後要看得出舊同意是依哪一版簽的（是否仍有效需人工判定）。 */
    val templateVersion: String = "IRB_consent_v1",
    val withdrawnAt: Date? = null,
    val withdrawReason: String? = null
) {
    /** 目前是否有效的訓練同意（勾了且未撤回）。送訓練標註前一律以此為準。 */
    val trainEffective: Boolean get() = consentTrain && withdrawnAt == null

    // ByteArray 欄位讓 data class 的 equals/hashCode 變成參考比較 → 明確覆寫，
    // 否則 Room/測試裡「內容相同的兩筆」會被判為不相等，除錯時很難察覺。
    override fun equals(other: Any?): Boolean {
        if (this === other) return true
        if (other !is ConsentEntity) return false
        return id == other.id && patientId == other.patientId &&
            consentCare == other.consentCare && consentTrain == other.consentTrain &&
            signedAt == other.signedAt && withdrawnAt == other.withdrawnAt &&
            templateVersion == other.templateVersion &&
            (signaturePng?.contentEquals(other.signaturePng) ?: (other.signaturePng == null))
    }

    override fun hashCode(): Int {
        var r = id.hashCode()
        r = 31 * r + patientId.hashCode()
        r = 31 * r + consentCare.hashCode()
        r = 31 * r + consentTrain.hashCode()
        r = 31 * r + signedAt.hashCode()
        r = 31 * r + (withdrawnAt?.hashCode() ?: 0)
        r = 31 * r + templateVersion.hashCode()
        r = 31 * r + (signaturePng?.contentHashCode() ?: 0)
        return r
    }
}
