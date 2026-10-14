package com.woundmeasurement.app.pipeline

import com.woundmeasurement.app.data.dao.MeasurementDao
import com.woundmeasurement.app.data.entity.MeasurementEntity

/** The saved local revision is the upload boundary; a server receipt must acknowledge that revision. */
object SavedAnnotationUpload {
    data class Result(val message: String, val acknowledged: Boolean = false)

    suspend fun submit(dao: MeasurementDao, expected: MeasurementEntity,
                       rasterMatches: suspend () -> Boolean,
                       upload: suspend () -> Pair<Boolean, String>): Result {
        val latest = dao.getById(expected.id)
        if (latest == null || latest.copy(annotationSubmitted = expected.annotationSubmitted) != expected)
            return Result("時間軸紀錄已變更，請返回時間軸核對最新修訂；未送出。")
        if (!rasterMatches())
            return Result("組織遮罩尚未完整存入時間軸，請先存檔；未送出。")
        if (latest.annotationSubmitted)
            return Result("此修訂已確認保存，無需重送。", true)
        val (ok, message) = upload()
        if (!ok) return Result("⚠️ 被守門擋下：$message")
        val marked = try { dao.acknowledgeAnnotationIfUnchanged(expected) }
            catch (cancelled: kotlinx.coroutines.CancellationException) { throw cancelled }
            catch (_: Exception) {
                return Result("標註已保存，但本機保存狀態未能更新；請返回時間軸核對，不必重新拍攝。")
            }
        if (!marked)
            return Result("標註已保存，但本機修訂已變更，未標記其他版本；請返回時間軸核對。")
        return Result(if (message.contains("duplicate"))
            "ℹ️ 此修訂已確認保存（去重），未重複新增。"
            else "✅ 此修訂標註已確認保存。", true)
    }
}
