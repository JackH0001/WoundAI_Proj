import Foundation
import CoreGraphics
#if canImport(UIKit)
import UIKit
#endif

/**
 修邊編輯狀態。**遮罩是唯一真相**，跨回合原樣傳遞；`cm2PerPx` 首次鎖定（面積冪等）。

 對等 Android `WoundEditScreen.kt` 的 `EditRaster`。三個平行陣列長度皆為 `mw * mh`：

 | 陣列 | 內容 | 值域 |
 |---|---|---|
 | `mask`     | 目前遮罩 | 0 / 1 |
 | `tissue`   | 組織分區（**修邊畫面碼**，見 `TissueCode`） | 0…5 |
 | `origMask` | AI 原始遮罩（算 `correction_iou` 用） | 0 / 1 |
 */
struct EditRaster {
    var mask: [UInt8]
    var tissue: [UInt8]
    var origMask: [UInt8]

    /// 柵格原點在**影像座標**中的位置。
    var rx0: Double
    var ry0: Double
    var mw: Int
    var mh: Int
    /// 柵格 → 影像的縮放比。影像座標矩形 ＝ `(rx0, ry0, mw / mScale, mh / mScale)`。
    var mScale: Double

    /// 每柵格像素對應的 cm²。首次鎖定後不再重算——否則面積會隨進出畫面漂移。
    var cm2PerPx: Double?

    /**
     醫師實際重畫的組織像素數（`tissue` 與分類器建議不同的部分）。

     ⚠ **這個欄位決定這張遮罩能不能拿去訓練。**

     醫師若完全沒動組織筆刷，遮罩就是色彩啟發式的原樣輸出。拿它當 GT 訓練
     ＝**用模型自己的輸出訓練自己**：驗證指標會很漂亮，因為模型只是在學會複製它已經
     會做的事，而臨床表現不會有任何改善。

     沒有這個計數，訓練集會安靜地被未經人看過的啟發式輸出填滿，而且從資料本身完全
     看不出來。後端 `/dataset/manifest` 預設 `require_edited=1` 就是靠它把關。
     */
    var tissueEditedPx: Int = 0

    /// 遮罩內像素總數，用來算修改比例。
    var maskPx: Int = 0

    /**
     產生這份柵格時的畫布尺寸。續編前必須比對——柵格座標是相對於當時那張畫布算的，
     尺寸不同就整份作廢，寧可退回由多邊形重建，也不要把醫師的判斷搬到錯的位置。
     */
    var canvasW: Int = 0
    var canvasH: Int = 0

    /**
     產生這份柵格時用的白平衡增益 `[R, G, B]`（來自後端色卡校正）。

     跟著柵格一起存，是因為從時間軸回頭修邊時**沒有後端回應可拿**。沒有它，自動分區會
     退回灰世界，底稿與當初量測時的分區對不上——而醫師會以為是自己上次標錯了，
     然後把對的改成錯的。
     */
    var wbGains: [Double]?

    var tissueEdited: Bool { return tissueEditedPx > 0 }
    var tissueEditRatio: Double { return maskPx > 0 ? Double(tissueEditedPx) / Double(maskPx) : 0.0 }

    /// 遮罩在影像座標中的矩形。與後端 `_raster_rect()` 同一條公式。
    var imageRect: CGRect {
        guard mScale > 0 else { return .zero }
        return CGRect(x: rx0, y: ry0, width: Double(mw) / mScale, height: Double(mh) / mScale)
    }

    /// 遮罩面積（cm²）。**用像素數 × cm²/px 直接算，不要拿 AI 初始面積乘修正比例**——
    /// 後者會累積換算鏈偏差。
    var areaCm2: Double? {
        guard let c = cm2PerPx, c > 0 else { return nil }
        var n = 0
        for v in mask where v != 0 { n += 1 }
        return Double(n) * c
    }

    /// 與 AI 原始遮罩的 IoU，即 `correction_iou`。1.0 ＝ 醫師沒改動。
    var correctionIou: Double? {
        guard mask.count == origMask.count, !mask.isEmpty else { return nil }
        var inter = 0, uni = 0
        for i in 0..<mask.count {
            let a = mask[i] != 0, b = origMask[i] != 0
            if a || b { uni += 1 }
            if a && b { inter += 1 }
        }
        return uni == 0 ? 1.0 : Double(inter) / Double(uni)
    }

    /// 遮罩內各組織比例，key ＝ 後端具名欄位。**遮罩外不計入。**
    func tissueFrac() -> [String: Double] {
        var counts = [Int](repeating: 0, count: TissueCode.maxCode + 1)
        var total = 0
        let n = min(mask.count, tissue.count)
        for i in 0..<n where mask[i] != 0 {
            let c = Int(tissue[i])
            if c >= 1 && c <= TissueCode.maxCode { counts[c] += 1 }
            total += 1
        }
        let denom = max(1, total)
        var out: [String: Double] = [:]
        for c in 1...TissueCode.maxCode {
            out[TissueCode.editToKey[c]] = Double(counts[c]) / Double(denom)
        }
        return out
    }
}

// MARK: - 柵格持久化

/**
 修邊柵格的本機持久化（對等 Android `EditRasterCodec`）。

 ## 為什麼一定要存

 若只保存多邊形（`Measurement.gtPolygon`），從時間軸回頭修邊時柵格得由多邊形重建，
 後果有兩個，都是實質的資料損失：

 ### 1. 醫師畫的組織分區整批消失

 多邊形只描述**傷口外緣**，不含任何組織資訊。重建之後組織圖層退回色彩啟發式的預設值
 ——醫師上一次逐塊修正的判斷全部不見，而畫面看起來完全正常（一片合理的顏色）。
 更糟的是它**看起來像是他沒做過**，於是他會再做一次，然後再消失一次。

 ### 2. 面積每進出一次就漂移一點

 往返是有損的：`遮罩 →(邊界追蹤)→ 輪廓 →(RDP 簡化)→ 多邊形 →(掃描線填充)→ 遮罩'`。
 RDP 會把轉角切掉；重建時 ROI 外框依新多邊形重算，`mScale` 也可能不同，於是柵格化的
 像素數跟著變。Android 端實測 51.55 → 51.23 cm²（−0.62%），而醫師**什麼都沒改**。
 一個會自己緩慢變動的臨床數值比明顯的錯誤更難察覺。

 存下柵格之後，續編是**原樣載回**：像素數不變 → 面積不變。

 ## 格式

 單張無損 PNG，三個通道各放一份 8-bit 資料：R ＝ `tissue`（0…5）、G ＝ `mask`（0/1）、
 B ＝ `origMask`（0/1）。

 ⚠ **必須無損。** JPEG 會把 0/1 糊成 0.4，解碼端只能猜，而猜錯的那些像素正好都在邊界上。
 仿射參數（`rx0`/`ry0`/`mScale`…）放在另一個 JSON 欄位，因為它們是浮點數，
 塞進像素會失去精度。
 */
enum EditRasterCodec {

    /// - Returns: `(PNG 位元組, meta JSON)`，失敗回 nil。
    static func encode(_ r: EditRaster) -> (Data, String)? {
        #if canImport(UIKit)
        let n = r.mw * r.mh
        guard n > 0, r.mask.count >= n, r.tissue.count >= n, r.origMask.count >= n else { return nil }

        var px = [UInt8](repeating: 0, count: n * 4)   // RGBA8
        for i in 0..<n {
            let t = r.tissue[i] <= UInt8(TissueCode.maxCode) ? r.tissue[i] : 0
            px[i * 4 + 0] = t
            px[i * 4 + 1] = r.mask[i] != 0 ? 1 : 0
            px[i * 4 + 2] = r.origMask[i] != 0 ? 1 : 0
            px[i * 4 + 3] = 255
        }
        guard let png = Self.pngFromRGBA(px, width: r.mw, height: r.mh) else { return nil }

        var meta: [String: Any] = [
            "rx0": r.rx0, "ry0": r.ry0, "mw": r.mw, "mh": r.mh, "m_scale": r.mScale,
            "tissue_edited_px": r.tissueEditedPx, "mask_px": r.maskPx,
            // 畫布尺寸一起存：續編時若影像尺寸不同（例如換了後端縮圖策略），柵格座標就對不上
            // ——寧可放棄續編也不要用錯的座標畫在病人的傷口上。
            "canvas_w": r.canvasW, "canvas_h": r.canvasH
        ]
        meta["cm2_per_px"] = r.cm2PerPx ?? NSNull()
        if let g = r.wbGains { meta["wb_gains"] = g }

        guard let mjson = try? JSONSerialization.data(withJSONObject: meta),
              let mstr = String(data: mjson, encoding: .utf8) else { return nil }
        return (png, mstr)
        #else
        return nil
        #endif
    }

    /// - Parameters:
    ///   - canvasW: 目前畫布尺寸。與存檔時不同就回 nil（呼叫端退回由多邊形重建）。
    static func decode(png: Data?, meta: String?, canvasW: Int, canvasH: Int) -> EditRaster? {
        #if canImport(UIKit)
        guard let png = png, let meta = meta, !meta.isEmpty,
              let mdata = meta.data(using: .utf8), let j = JSONAny(data: mdata) else { return nil }
        let mw = j["mw"].int(0), mh = j["mh"].int(0)
        guard mw > 0, mh > 0 else { return nil }

        // ⚠ 尺寸不符時**不要**縮放遷就。柵格座標是相對於當時那張畫布算的，
        //   縮放後每一個像素都對應到不同的位置，醫師的判斷會被搬到錯的地方。
        let cw = j["canvas_w"].int(0), ch = j["canvas_h"].int(0)
        if cw > 0, ch > 0, cw != canvasW || ch != canvasH { return nil }

        guard let px = Self.rgbaFromPNG(png, expectW: mw, expectH: mh) else { return nil }
        let n = mw * mh
        var tissue = [UInt8](repeating: 0, count: n)
        var mask = [UInt8](repeating: 0, count: n)
        var orig = [UInt8](repeating: 0, count: n)
        for i in 0..<n {
            tissue[i] = px[i * 4 + 0]
            mask[i]   = px[i * 4 + 1]
            orig[i]   = px[i * 4 + 2]
        }
        var wb: [Double]?
        let g = j["wb_gains"].array.compactMap { $0.double }
        if g.count == 3 { wb = g }

        return EditRaster(
            mask: mask, tissue: tissue, origMask: orig,
            rx0: j["rx0"].double(0), ry0: j["ry0"].double(0),
            mw: mw, mh: mh, mScale: j["m_scale"].double(1),
            cm2PerPx: j["cm2_per_px"].double,
            tissueEditedPx: j["tissue_edited_px"].int(0),
            maskPx: j["mask_px"].int(0),
            canvasW: cw, canvasH: ch, wbGains: wb
        )
        #else
        return nil
        #endif
    }

    // MARK: - 像素 ↔ PNG

    #if canImport(UIKit)
    /// ⚠ 一定要用 `.noneSkipLast`（不預乘）。預乘 alpha 會在 alpha<255 時改寫顏色分量，
    ///   而我們的「顏色」是類別碼——被乘過就再也還原不回來。
    static func pngFromRGBA(_ rgba: [UInt8], width: Int, height: Int) -> Data? {
        guard width > 0, height > 0, rgba.count >= width * height * 4 else { return nil }
        var buf = rgba
        let cs = CGColorSpaceCreateDeviceRGB()
        guard let ctx = CGContext(data: &buf, width: width, height: height,
                                  bitsPerComponent: 8, bytesPerRow: width * 4, space: cs,
                                  bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue),
              let cg = ctx.makeImage() else { return nil }
        return UIImage(cgImage: cg).pngData()
    }

    static func rgbaFromPNG(_ data: Data, expectW: Int, expectH: Int) -> [UInt8]? {
        guard let img = UIImage(data: data), let cg = img.cgImage else { return nil }
        guard cg.width == expectW, cg.height == expectH else { return nil }
        var buf = [UInt8](repeating: 0, count: expectW * expectH * 4)
        let cs = CGColorSpaceCreateDeviceRGB()
        guard let ctx = CGContext(data: &buf, width: expectW, height: expectH,
                                  bitsPerComponent: 8, bytesPerRow: expectW * 4, space: cs,
                                  bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else { return nil }
        ctx.draw(cg, in: CGRect(x: 0, y: 0, width: expectW, height: expectH))
        return buf
    }
    #endif
}

// MARK: - 上傳用組織遮罩

/**
 把修邊柵格的組織分類編成 PNG，供上傳成訓練用的組織分割 GT。

 ## 為什麼送柵格而不是影像尺寸的遮罩

 柵格**就是醫師實際畫的解析度**。放大成原圖尺寸再送，等於在資料裡加入我們自己插值出來的
 假精細度——訓練時模型會去學那些其實不存在的邊界細節。送小圖再附仿射參數
 （`tissue_raster`）反而是無損的：它保留了「這是在什麼解析度上判斷的」這個事實。

 ## 編碼

 值直接放在 R 通道（0 ＝ 遮罩外，1…5 ＝ **修邊畫面碼**，見 `TissueCode`），G/B 補 0，A=255。

 ⚠ **必須無損。** JPEG 會把類別邊界糊掉，而「3.7 類」這種值沒有意義——解碼端只能四捨五入，
 於是邊界像素被隨機指派到相鄰類別。PNG 是唯一選項。
 */
enum TissueMaskCodec {

    /// - Returns: base64 PNG，失敗回 nil。
    ///   失敗時呼叫端應照常送出其餘欄位——遮罩缺席只是少一個訓練樣本，
    ///   讓整筆標註失敗才是真的損失。
    static func encodeBase64(tissue: [UInt8], mask: [UInt8], mw: Int, mh: Int) -> String? {
        #if canImport(UIKit)
        guard mw >= 2, mh >= 2, tissue.count >= mw * mh, mask.count >= mw * mh else { return nil }
        let n = mw * mh
        var px = [UInt8](repeating: 0, count: n * 4)
        for i in 0..<n {
            // 遮罩外一律 0。tissue 陣列在遮罩外可能留有舊值（擦除只清 mask 不清 tissue），
            // 直接編出去會讓訓練資料多出一圈傷口外的假標註。
            var v: UInt8 = 0
            if mask[i] != 0 {
                let c = tissue[i]
                v = (c >= 1 && c <= UInt8(TissueCode.maxCode)) ? c : 0
            }
            px[i * 4 + 0] = v
            px[i * 4 + 3] = 255
        }
        guard let png = EditRasterCodec.pngFromRGBA(px, width: mw, height: mh) else { return nil }
        return png.base64EncodedString()
        #else
        return nil
        #endif
    }
}
