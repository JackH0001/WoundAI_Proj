import Foundation

/**
 傷口輪廓的 JSON 格式：**同時支援單一與多個輪廓，且向後相容**（對等 Android `PolygonJson.kt`）。

 ## 為什麼要支援多個

 同一肢體多處傷口是臨床常態（小腿同時有兩處潰瘍）。舊版整條鏈都只取最大連通元件——
 後端 classify 的 `wound_polygon`、App 完成修邊的輪廓追蹤、難例集成的比對，
 三處各自獨立地丟掉其餘元件。

 Android 端 2026-08-07 實測的後果：醫師在修邊畫面明明把兩個傷口都標了，送出的 GT
 卻只有一個——**第二個傷口被標成背景，等於教模型「那不是傷口」**。比少收一筆資料糟得多。

 ## 兩種格式

     單一：[[x,y],[x,y],...]           ← 舊格式，DB 既有紀錄都長這樣
     多個：[[[x,y],...],[[x,y],...]]   ← 新格式

 `parse` 兩種都吃，靠**第一個元素的深度**分辨：點是 `[Int, Int]`，輪廓是 `[[Int,Int], …]`。

 ⚠ 不要改成「一律用新格式」。DB 裡的舊格式紀錄沒有版本欄位可區分，只能靠結構判斷；
 而 `toJson` 在單一輪廓時**刻意寫舊格式**，讓舊版程式與舊測試都還讀得懂。
 兩端的 DB 內容要能互相匯入，這裡的格式規則必須與 Android 逐字元一致。
 */
enum PolygonJson {

    /// 多輪廓 → JSON。單一輪廓寫成**舊格式**。全部退化（<3 點）時回 nil。
    static func toJson(_ polygons: [[[Int]]]) -> String? {
        let ps = polygons.filter { $0.count >= 3 }
        if ps.isEmpty { return nil }
        if ps.count == 1 { return pointsJson(ps[0]) }
        return "[" + ps.map(pointsJson).joined(separator: ",") + "]"
    }

    /// 手組字串而不走 JSONSerialization：格式要與 Android `pointsJson` 位元組級一致
    /// （無空白、逐點 `[x,y]`），後端佇列的去重比對才不會把同一份輪廓當成兩份。
    private static func pointsJson(_ pts: [[Int]]) -> String {
        return "[" + pts.map { p in
            "[\(p.count > 0 ? p[0] : 0),\(p.count > 1 ? p[1] : 0)]"
        }.joined(separator: ",") + "]"
    }

    /// JSON → 多輪廓。兩種格式都吃；解不開回空清單（不丟例外——呼叫端多半在畫面上）。
    static func parse(_ js: String?) -> [[[Int]]] {
        guard let js, !js.trimmingCharacters(in: .whitespaces).isEmpty,
              let d = js.data(using: .utf8), let j = JSONAny(data: d) else { return [] }
        let a = j.array
        guard let first = a.first, !first.array.isEmpty else { return [] }
        // 深度判斷：first[0] 是陣列 → 多輪廓；是數字 → 單一輪廓。
        let multi = !first.array[0].array.isEmpty
        if !multi {
            let pts = j.intPointList
            return pts.isEmpty ? [] : [pts]
        }
        return a.compactMap { p in
            let pts = p.intPointList
            return pts.count >= 3 ? pts : nil
        }
    }

    /// 相容取用：最大的那一個（點數最多），空的話回空清單。
    static func largest(_ polygons: [[[Int]]]) -> [[Int]] {
        return polygons.max(by: { $0.count < $1.count }) ?? []
    }
}
