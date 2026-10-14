package com.woundmeasurement.app.pipeline

import com.google.gson.JsonParser

/** A transport success is not evidence of saved research data. */
internal object AnnotationReceipt {
    fun validate(httpCode: Int, body: String, code: String, imageId: String,
                 woundSHA: String? = null, tissueSHA: String? = null): Pair<Boolean, String> {
        fun reject(reason: String) = false to "$reason；尚未確認保存，請保留本機紀錄後重試。"
        if (httpCode != 200) return reject("上傳回覆 HTTP $httpCode")
        return try {
            val root = JsonParser.parseString(body)
            if (!root.isJsonObject) return reject("保存回覆格式錯誤")
            val obj = root.asJsonObject
            fun text(key: String): String? = obj.get(key)?.takeIf {
                it.isJsonPrimitive && it.asJsonPrimitive.isString
            }?.asString
            if (obj.has("error") || obj.has("issues")) return reject("後端回報保存異常")
            if (text("code") != code || text("image_id") != imageId)
                return reject("保存回覆的個案或影像不符")
            if (text("annotation_receipt_id")?.matches(Regex("[0-9a-f]{16}")) != true)
                return reject("缺少有效修訂收據；請核對後端版本")
            if (woundSHA != null || tissueSHA != null) {
                if (woundSHA?.matches(Regex("[0-9a-f]{64}")) != true ||
                    tissueSHA?.matches(Regex("[0-9a-f]{64}")) != true ||
                    text("mask_contract") != "woundai.annotation-masks/1" ||
                    text("wound_mask_sha256") != woundSHA || text("tissue_mask_sha256") != tissueSHA)
                    return reject("傷口或組織遮罩保存回覆不符")
            }
            when (text("status")) {
                "enqueued" -> true to "標註保存已確認"
                "duplicate_skipped" -> true to "duplicate:相同修訂已保存，未重複新增"
                else -> reject("後端尚未確認標註保存")
            }
        } catch (_: Exception) { reject("無法解析保存回覆") }
    }
}
