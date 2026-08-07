import Foundation

/**
 極簡 JSON 讀取器，語意刻意對齊 Android 的 `org.json.JSONObject.opt*`。

 ## 為什麼不用 Codable

 後端回應有三種「沒有值」：鍵不存在、值為 `null`、值為空字串，而它們在不同欄位上代表
 不同的事（`image_id: null` ＝ 不得送訓練標註；`stage3b_colorcal` 鍵不存在 ＝ 舊版後端）。
 `Codable` 的 `decodeIfPresent` 把前兩者合併，第三種要另外處理；而一旦某個必填欄位缺席，
 整個 `decode` 會拋錯——App 一連上舊版後端，`classify` 就整支失敗。

 Android 端正是用 `optJSONObject` 逐鍵取值來吸收這種版本差異（`BackendClient.kt` 的註解
 明確記錄了這個決定）。iOS 端照做，兩邊的容錯行為才會一致。
 */
struct JSONAny {
    let raw: Any?

    init(_ raw: Any?) { self.raw = raw }

    init?(data: Data) {
        guard let obj = try? JSONSerialization.jsonObject(with: data, options: [.fragmentsAllowed]) else {
            return nil
        }
        self.raw = obj
    }

    // MARK: - 導覽

    subscript(key: String) -> JSONAny {
        guard let dict = raw as? [String: Any] else { return JSONAny(nil) }
        return JSONAny(dict[key])
    }

    subscript(index: Int) -> JSONAny {
        guard let arr = raw as? [Any], index >= 0, index < arr.count else { return JSONAny(nil) }
        return JSONAny(arr[index])
    }

    var array: [JSONAny] {
        guard let arr = raw as? [Any] else { return [] }
        return arr.map { JSONAny($0) }
    }

    /// 鍵存在且值不是 JSON null。對應 `!JSONObject.isNull(name)`。
    var exists: Bool {
        if raw == nil { return false }
        if raw is NSNull { return false }
        return true
    }

    var isNull: Bool { return !exists }

    // MARK: - 取值（存在才回值，否則 nil）

    var string: String? {
        if let s = raw as? String { return s }
        if let n = raw as? NSNumber { return n.stringValue }
        return nil
    }

    var double: Double? {
        if let n = raw as? NSNumber { return n.doubleValue }
        if let s = raw as? String { return Double(s) }
        return nil
    }

    var int: Int? {
        if let n = raw as? NSNumber { return n.intValue }
        if let s = raw as? String { return Int(s) }
        return nil
    }

    var bool: Bool? {
        if let n = raw as? NSNumber { return n.boolValue }
        if let s = raw as? String {
            let l = s.lowercased()
            if l == "true" || l == "1" || l == "yes" { return true }
            if l == "false" || l == "0" || l == "no" { return false }
        }
        return nil
    }

    // MARK: - 取值（帶預設，對應 opt*(name, fallback)）

    func string(_ fallback: String) -> String { return string ?? fallback }
    func double(_ fallback: Double) -> Double { return double ?? fallback }
    func int(_ fallback: Int) -> Int { return int ?? fallback }
    func bool(_ fallback: Bool) -> Bool { return bool ?? fallback }

    /// 空白字串視同不存在。後端有幾個欄位會回 `""` 表示「沒有備註」。
    var nonBlankString: String? {
        guard let s = string else { return nil }
        let t = s.trimmingCharacters(in: .whitespacesAndNewlines)
        return t.isEmpty ? nil : s
    }

    /// `[[x, y], ...]` → `[[Int]]`。任何一點格式不合就整個略過該點。
    var intPointList: [[Int]] {
        return array.compactMap { pt in
            let xs = pt.array
            guard xs.count >= 2, let x = xs[0].int, let y = xs[1].int else { return nil }
            return [x, y]
        }
    }
}
