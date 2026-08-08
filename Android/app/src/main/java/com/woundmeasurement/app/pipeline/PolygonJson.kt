package com.woundmeasurement.app.pipeline

import org.json.JSONArray

/**
 * 傷口輪廓的 JSON 格式：**同時支援單一與多個輪廓，且向後相容**。
 *
 * ## 為什麼要支援多個
 *
 * 同一肢體多處傷口是臨床常態（小腿同時有兩處潰瘍）。舊版整條鏈都只取
 * 最大連通元件——後端 classify 的 `wound_polygon`、App 完成修邊的輪廓追蹤、
 * 難例集成的比對，三處各自獨立地丟掉其餘元件。
 *
 * 2026-08-07 實測的後果：醫師在修邊畫面明明把兩個傷口都標了，
 * 送出的 GT 卻只有一個——**第二個傷口被標成背景，等於教模型「那不是傷口」**。
 * 比少收一筆資料糟得多。
 *
 * ## 兩種格式
 *
 *   單一：`[[x,y],[x,y],...]`          ← 舊格式，所有既有紀錄都長這樣
 *   多個：`[[[x,y],...],[[x,y],...]]`  ← 新格式
 *
 * [parsePolygons] 兩種都吃，靠**第一個元素的深度**分辨：
 * 點是 `[Int, Int]`，輪廓是 `[[Int,Int], ...]`。
 *
 * ⚠ 不要改成「一律用新格式」。DB 裡已經有大量舊格式紀錄，
 * 而它們沒有版本欄位可以區分——只能靠結構判斷。
 */
object PolygonJson {

    /** 多輪廓 → JSON。單一輪廓寫成**舊格式**，讓舊版程式與舊測試都還讀得懂。 */
    fun toJson(polygons: List<List<List<Int>>>): String? {
        val ps = polygons.filter { it.size >= 3 }
        if (ps.isEmpty()) return null
        if (ps.size == 1) return pointsJson(ps[0])
        val a = JSONArray()
        ps.forEach { a.put(JSONArray(pointsJson(it))) }
        return a.toString()
    }

    private fun pointsJson(pts: List<List<Int>>): String =
        pts.joinToString(",", "[", "]") {
            "[${it.getOrElse(0) { 0 }},${it.getOrElse(1) { 0 }}]"
        }

    /** JSON → 多輪廓。兩種格式都吃；解不開回空清單（不丟例外——呼叫端多半在畫面上）。 */
    fun parse(js: String?): List<List<List<Int>>> = runCatching {
        if (js.isNullOrBlank()) return emptyList()
        val a = JSONArray(js)
        if (a.length() == 0) return emptyList()
        // 深度判斷：a[0] 若是「數字的陣列」→ 單一輪廓；若是「陣列的陣列」→ 多輪廓。
        val first = a.optJSONArray(0) ?: return emptyList()
        val multi = first.optJSONArray(0) != null
        if (!multi) return listOf(points(a))
        val out = ArrayList<List<List<Int>>>()
        for (i in 0 until a.length()) {
            a.optJSONArray(i)?.let { p -> points(p).takeIf { it.size >= 3 }?.let(out::add) }
        }
        out
    }.getOrDefault(emptyList())

    private fun points(a: JSONArray): List<List<Int>> {
        val out = ArrayList<List<Int>>(a.length())
        for (i in 0 until a.length()) {
            val p = a.optJSONArray(i) ?: continue
            if (p.length() >= 2) out.add(listOf(p.optInt(0), p.optInt(1)))
        }
        return out
    }

    /** 相容取用：最大的那一個（由呼叫端保證已排序），空的話回空清單。 */
    fun largest(polygons: List<List<List<Int>>>): List<List<Int>> =
        polygons.maxByOrNull { it.size } ?: emptyList()
}

/** 便利別名，讓呼叫端讀起來像一般函式。 */
internal fun polygonsToJson(polygons: List<List<List<Int>>>): String? = PolygonJson.toJson(polygons)
internal fun parsePolygons(js: String?): List<List<List<Int>>> = PolygonJson.parse(js)
