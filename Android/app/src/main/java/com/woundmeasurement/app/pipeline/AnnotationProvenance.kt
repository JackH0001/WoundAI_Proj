package com.woundmeasurement.app.pipeline

import com.google.gson.Gson
import com.google.gson.JsonParser

/** Original request metadata, persisted before leaving the capture screen. Null is unknown. */
data class AnnotationProvenance(
    val segModel: String?,
    val quality: Map<String, Double>?,
    val careNote: String?
) {
    fun encode(): String {
        require(quality?.values?.all { it.isFinite() } != false)
        return Gson().toJson(mapOf("schema" to 1, "seg_model" to segModel,
            "quality" to quality, "care_note" to careNote))
    }
    companion object {
        const val CARE_NOTE = "app confirm"
        fun decode(value: String?): AnnotationProvenance? = runCatching {
            if (value == null) return null
            val j = JsonParser.parseString(value).asJsonObject
            require(j.get("schema")?.asString == "1")
            fun text(key: String): String? {
                val v = j.get(key) ?: return null
                if (v.isJsonNull) return null
                require(v.isJsonPrimitive && v.asJsonPrimitive.isString)
                return v.asString
            }
            val q = j.get("quality")?.takeUnless { it.isJsonNull }?.asJsonObject
                ?.entrySet()?.associate { (k, v) ->
                    require(v.isJsonPrimitive && v.asJsonPrimitive.isNumber)
                    val number = v.asDouble
                    require(number.isFinite())
                    k to number
                }
            AnnotationProvenance(text("seg_model"), q, text("care_note"))
        }.getOrNull()
    }
}
