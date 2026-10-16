package com.woundmeasurement.app.data

import com.google.gson.Gson
import com.google.gson.JsonParser

internal data class MedicalOption(val code:String,val label:String)
internal data class MedicalSite(val code:String,val label:String,val region:String,
    val lateralities:List<String>,val surfaces:List<String>,val digits:List<String>)
internal data class MedicalEtiology(val code:String,val label:String,val instruments:List<String>)
internal data class MedicalInstrument(val code:String,val label:String,val automatic:String,val manual:String,val status:String)
internal data class MedicalCatalog(val version:String,val regions:List<MedicalOption>,
    val lateralities:List<MedicalOption>,val surfaces:List<MedicalOption>,val sites:List<MedicalSite>,
    val etiologies:List<MedicalEtiology>,val factors:List<MedicalOption>,val instruments:List<MedicalInstrument>) {
    fun guidance(etiology:String):List<MedicalInstrument> = etiologies.firstOrNull { it.code==etiology }?.instruments.orEmpty()
        .mapNotNull { key -> instruments.firstOrNull { it.code==key } }
    companion object { val shared:MedicalCatalog = Gson().fromJson(MedicalWoundCatalogData.json,MedicalCatalog::class.java) }
}

/** Codes only. Supplemental notes remain local and are never attached to research payloads. */
internal data class MedicalWoundProfile(val schema_version:String=MedicalCatalog.shared.version,
    val site:String="",val side:String="",val surface:String="",val digit:String="not_applicable",
    val etiology:String="",val factors:List<String> = emptyList()) {
    val valid:Boolean get() = runCatching {
        val c=MedicalCatalog.shared;val s=c.sites.firstOrNull { it.code==site } ?: return@runCatching false
        schema_version==c.version && side in s.lateralities && surface in s.surfaces && digit in s.digits &&
            c.etiologies.any { it.code==etiology } && factors.distinct().size==factors.size &&
            factors.all { f -> c.factors.any { it.code==f } } &&
            (etiology!="diabetic_foot" || site in listOf("heel","forefoot","midfoot","hindfoot","toe","interdigital_foot","toe_tip"))
    }.getOrDefault(false)
    val bodyLabel:String get() {
        val c=MedicalCatalog.shared
        return listOf(c.lateralities.firstOrNull{it.code==side}?.label.orEmpty(),c.sites.firstOrNull{it.code==site}?.label.orEmpty(),
            if(surface=="not_applicable") "" else c.surfaces.firstOrNull{it.code==surface}?.label.orEmpty(),
            if(digit=="not_applicable") "" else if(digit=="unknown") "指趾未判定" else "第${digit}指／趾").filter{it.isNotEmpty()}.joinToString("・")
    }
    val typeLabel:String get() = MedicalCatalog.shared.etiologies.firstOrNull{it.code==etiology}?.label.orEmpty()
    fun encoded():String { require(valid){"請完成並核對部位與傷口成因選單"};return Gson().toJson(copy(factors=factors.sorted())) }
    companion object {
        fun decode(raw:String?):MedicalWoundProfile? = runCatching {
            if (raw == null) return@runCatching null
            val root = JsonParser.parseString(raw)
            if (!root.isJsonObject) return@runCatching null
            val obj = root.asJsonObject
            val keys = setOf("schema_version", "site", "side", "surface", "digit", "etiology", "factors")
            if (obj.keySet() != keys) return@runCatching null
            if ((keys - "factors").any { !obj[it].isJsonPrimitive || !obj[it].asJsonPrimitive.isString }) return@runCatching null
            if (!obj["factors"].isJsonArray || obj["factors"].asJsonArray.any { !it.isJsonPrimitive || !it.asJsonPrimitive.isString }) return@runCatching null
            Gson().fromJson(obj, MedicalWoundProfile::class.java)?.takeIf { it.valid }
        }.getOrNull()
    }
}
