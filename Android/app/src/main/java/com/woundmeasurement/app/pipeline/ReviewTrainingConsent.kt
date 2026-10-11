package com.woundmeasurement.app.pipeline

/** Only an explicitly stored, unlinked phantom has no patient consent to read.
 * A linked record always re-reads its patient's consent, even if tagged phantom.
 * Sample/external/legacy records are not assumed to be synthetic.
 */
internal object ReviewTrainingConsent {
    fun isUnlinkedPhantom(source: String?, caseId: Long?) = source == "phantom" && caseId == null

    suspend fun allowed(source: String?, caseId: Long?, readPatientConsent: suspend (Long) -> Boolean): Boolean {
        if (source !in setOf(null, "clinical", "phantom", "sample", "external")) return false
        if (isUnlinkedPhantom(source, caseId)) return true
        val id = caseId?.takeIf { it > 0 } ?: return false
        return readPatientConsent(id)
    }

    fun blockedMessage(source: String?, caseId: Long?): String = when {
        source !in setOf(null, "clinical", "phantom", "sample", "external") ->
            "⚠ 此筆樣本來源無法確認，已停止送出；請保留原紀錄並核對來源。"
        caseId == null ->
            "⚠ 此筆紀錄沒有可核對同意的病患個案，且不是明確標記的模擬圖，已停止送出。"
        else -> "⚠ 此病患目前無有效的②訓練同意（或已撤回），已停止送出。"
    }
}
