import Foundation

/**
 # ⚠ 這個專案有**兩套組織碼**，而它們的 1 和 3 是相反的

 | 碼空間 | 1 | 2 | 3 | 4 | 5 | 用在哪 |
 |---|---|---|---|---|---|---|
 | **分類器碼** | 壞死 | 腐肉 | 肉芽 | 上皮 | 其他 | `TissueClassifierV2.classifyPixel` 的回傳值；金標 `tissue_golden.json` |
 | **修邊畫面碼**（＝**資料集正典**） | 肉芽 | 腐肉 | 壞死 | 上皮 | 其他 | `EditRaster.tissue`、上傳的 `tissue_mask_png`、訓練腳本、主控台預覽 |

 ## 轉換只發生一次，而且只有一個方向

 `分類器碼 → 修邊碼`（[TissueSeg.clsToEdit]），發生在把自動分類結果餵進修邊畫布的那一刻。

 **上傳時不要再轉回去。** `tissue_mask_png` 的合約值就是修邊碼，由四個獨立的消費者確認：

 - `engineering/phase2/train_tissue_seg.py`（`CLASSES = ["背景","肉芽","腐肉","壞死","上皮","其他"]`）
 - `engineering/phase2/import_public_tissue.py`（把公開資料集 `granulation→1`、`necrosis→3` 映射進來）
 - `engineering/phase2/analyze_interrater.py`
 - `Backend/Flask/api_flywheel.py` 的 `TISSUE_NAMES`（主控台「看標註」預覽的配色）

 後端 `wound_classifier.py` 裡的 `1=necrosis` **不是**上傳遮罩的映射——那組整數只活在伺服器端
 啟發式函式內部，`tissue_proxy()` 一回傳就換成具名 key（`{"necrosis":…, "granulation":…}`），
 從不外流。把它當成遮罩的正典會得出「肉芽與壞死互換」的錯誤結論。

 ## 為什麼要把這段寫下來

 兩套碼、只轉一次、方向單一——任何一處弄反，畫面看起來都完全正常（都是合理的顏色分布），
 只有送去訓練的 GT 是錯的，而壞死與肉芽恰好是臨床後果最相反的兩類（壞死→清創，肉芽→癒合中）。
 這種錯誤沒有任何執行期徵兆，只能靠文件與測試釘住。對應測試見 `TissueCodeContractTests`。
 */
enum TissueCode {

    /// 修邊畫面碼上限。加類別時所有 clamp 都要跟著走——寫成常數才不會漏掉其中一處。
    static let maxCode = 5

    /// 修邊畫面碼 → 中文名。索引 0 ＝ 遮罩外／未分類。
    static let editNames = ["", "肉芽", "腐肉", "壞死", "上皮", "其他"]

    /// 修邊畫面碼 → 後端具名 key（送 `tissue_frac` 用）。索引 0 不用。
    static let editToKey = ["", "granulation", "slough", "necrosis", "epithelial", "other"]

    /// 後端具名 key → 修邊畫面碼。
    static let keyToEdit: [String: Int] = [
        "granulation": 1, "slough": 2, "necrosis": 3, "epithelial": 4, "other": 5
    ]

    /// 分類器碼（`TissueClassifierV2`）→ 後端具名 key。索引 0 不用。
    static let classifierToKey = ["", "necrosis", "slough", "granulation", "epithelial", "other"]
}

enum TissueSeg {

    /**
     分類器碼 → 修邊畫面碼。索引 0 不用。

     `[0, 3, 2, 1, 4, 5]`：分類器的 1(壞死) → 修邊的 3(壞死)；分類器的 3(肉芽) → 修邊的 1(肉芽)。
     這是全系統**唯一**一次碼轉換。
     */
    static let clsToEdit: [UInt8] = [0, 3, 2, 1, 4, 5]

    /// 取樣網格長邊上限。全解析度逐像素跑 HSV 規則會得到椒鹽狀雜點，且成本與像素數成正比。
    static let gridMax = 512

    /// 依區域大小挑取樣網格（長邊 ≤ `gridMax`）。
    static func grid(_ bw: Int, _ bh: Int) -> (Int, Int) {
        let s = min(1.0, Double(gridMax) / Double(max(bw, bh)))
        return (max(2, Int((Double(bw) * s).rounded())), max(2, Int((Double(bh) * s).rounded())))
    }

    /**
     多邊形掃描線填充成 `gw × gh` 遮罩（座標先線性映射到取樣網格）。

     與 Android `TissueSeg.rasterizePolygon` 逐行對應——兩端要在同一張圖上得到同一個遮罩，
     否則同一位醫師在兩個平台上修同一張圖會得到不同的面積。
     */
    static func rasterizePolygon(_ polygon: [[Int]], x0: Int, y0: Int,
                                 bw: Int, bh: Int, gw: Int, gh: Int) -> [UInt8] {
        var inside = [UInt8](repeating: 0, count: gw * gh)
        guard bw > 0, bh > 0, gw > 0, gh > 0, polygon.count >= 3 else { return inside }
        let sx = Double(gw) / Double(bw)
        let sy = Double(gh) / Double(bh)
        let poly: [(Double, Double)] = polygon.map {
            (Double(($0.count > 0 ? $0[0] : 0) - x0) * sx, Double(($0.count > 1 ? $0[1] : 0) - y0) * sy)
        }
        for y in 0..<gh {
            let yc = Double(y) + 0.5
            var xs: [Double] = []
            var j = poly.count - 1
            for i in 0..<poly.count {
                let a = poly[i], b = poly[j]
                if (a.1 > yc) != (b.1 > yc) {
                    xs.append(a.0 + (yc - a.1) / (b.1 - a.1) * (b.0 - a.0))
                }
                j = i
            }
            xs.sort()
            var k = 0
            while k + 1 < xs.count {
                let xa = max(0, Int(xs[k].rounded()))
                let xb = min(gw - 1, Int(xs[k + 1].rounded()))
                if xa <= xb {
                    for x in xa...xb { inside[y * gw + x] = 1 }
                }
                k += 2
            }
        }
        return inside
    }
}
