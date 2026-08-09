import Foundation

/**
 修邊柵格的核心運算。**全部是純函式**（不碰 CoreGraphics、不碰 UI），這樣才能離線
 跨語言比對——`tools/verify_raster.py` 用同一組輸入驗證 Swift 與 Python 的輸出一致。

 逐條對齊 Android `WoundEditScreen.kt` 的對應實作。兩端要在同一張圖上得到同一個遮罩，
 否則同一位醫師在兩個平台上修同一張圖會得到不同的面積，而時間軸會把那個差異畫成癒合。
 */
enum RasterOps {

    /// 柵格長邊上限。與 `TissueMaskCodec` 的說明一致：柵格就是醫師實際畫的解析度，
    /// 放大送出等於在資料裡加入我們自己插值出來的假精細度。
    static let maxRasterDim = 1024

    /// 擴張上限（記憶體防護）。對應 Android 的 `MAX_MASK_DIM`。
    static let maxExpandDim = 2200

    // MARK: - 建立初始柵格

    /**
     由 AI 回傳的多邊形建立可編輯柵格。

     ROI 取多邊形外接矩形再**外擴 50%**：醫師最常做的修正是「AI 少抓了一塊」，
     而如果柵格只有外接矩形那麼大，那塊就在畫布之外，怎麼畫都畫不進去。

     - Parameters:
       - polygon: 影像座標的傷口輪廓（`imageW × imageH` 空間）
       - mmPerPx: 後端回傳的尺度。`nil` 時面積無法計算，但仍可修邊。
     */
    /// 多輪廓版：ROI 涵蓋**所有**傷口，遮罩是各輪廓的聯集。
    ///
    /// ⚠ 只用最大的那一個輪廓，第二個傷口在修邊畫面就沒有初始輪廓——醫師得自己補畫，
    ///   沒注意到的話那個傷口會以背景的身分進訓練集。
    static func buildRaster(polygons: [[[Int]]], imageW: Int, imageH: Int,
                            mmPerPx: Double?, canvasW: Int, canvasH: Int,
                            wbGains: [Double]? = nil) -> EditRaster? {
        let valid = polygons.filter { $0.count >= 3 }
        guard !valid.isEmpty else {
            return buildRaster(polygon: [], imageW: imageW, imageH: imageH,
                               mmPerPx: mmPerPx, canvasW: canvasW, canvasH: canvasH,
                               wbGains: wbGains)
        }
        // ROI 取所有輪廓的聯集外接矩形，再交給單輪廓版算尺度；
        // 之後把每個輪廓各自柵格化並疊上去。
        let merged = valid.flatMap { $0 }
        guard var r = buildRaster(polygon: merged, imageW: imageW, imageH: imageH,
                                  mmPerPx: mmPerPx, canvasW: canvasW, canvasH: canvasH,
                                  wbGains: wbGains) else { return nil }
        var mask = [UInt8](repeating: 0, count: r.mw * r.mh)
        let roi = r.imageRect
        for poly in valid {
            let m = TissueSeg.rasterizePolygon(poly, x0: Int(roi.minX), y0: Int(roi.minY),
                                               bw: Int(roi.width), bh: Int(roi.height),
                                               gw: r.mw, gh: r.mh)
            for i in 0..<mask.count where m[i] != 0 { mask[i] = 1 }
        }
        r.mask = mask
        r.origMask = mask
        r.maskPx = mask.reduce(0) { $0 + ($1 != 0 ? 1 : 0) }
        return r
    }

    static func buildRaster(polygon: [[Int]], imageW: Int, imageH: Int,
                            mmPerPx: Double?, canvasW: Int, canvasH: Int,
                            wbGains: [Double]? = nil) -> EditRaster? {
        guard imageW > 0, imageH > 0 else { return nil }

        // 外接矩形。多邊形為空（AI 沒抓到）時退回影像中央 1/3，讓醫師從零畫起。
        var x0 = imageW, y0 = imageH, x1 = 0, y1 = 0
        for p in polygon where p.count >= 2 {
            x0 = min(x0, p[0]); y0 = min(y0, p[1])
            x1 = max(x1, p[0]); y1 = max(y1, p[1])
        }
        if polygon.count < 3 || x1 <= x0 || y1 <= y0 {
            x0 = imageW / 3; y0 = imageH / 3
            x1 = imageW * 2 / 3; y1 = imageH * 2 / 3
        }

        let padX = Double(x1 - x0) * 0.5, padY = Double(y1 - y0) * 0.5
        let rx0 = max(0.0, Double(x0) - padX / 2)
        let ry0 = max(0.0, Double(y0) - padY / 2)
        let rx1 = min(Double(imageW), Double(x1) + padX / 2)
        let ry1 = min(Double(imageH), Double(y1) + padY / 2)
        let roiW = rx1 - rx0, roiH = ry1 - ry0
        guard roiW >= 2, roiH >= 2 else { return nil }

        // 柵格解析度：長邊不超過 maxRasterDim，且**不放大**（mScale ≤ 1）。
        // 放大只會製造插值出來的假細節。
        let mScale = min(1.0, Double(maxRasterDim) / max(roiW, roiH))
        let mw = max(2, Int((roiW * mScale).rounded()))
        let mh = max(2, Int((roiH * mScale).rounded()))

        let mask = TissueSeg.rasterizePolygon(polygon, x0: Int(rx0), y0: Int(ry0),
                                              bw: Int(roiW), bh: Int(roiH), gw: mw, gh: mh)
        var maskPx = 0
        for v in mask where v != 0 { maskPx += 1 }

        return EditRaster(
            mask: mask,
            tissue: [UInt8](repeating: 0, count: mw * mh),   // 由 seedAuto 填
            origMask: mask,                                   // AI 原始遮罩，算 correction_iou 用
            rx0: rx0, ry0: ry0, mw: mw, mh: mh, mScale: mScale,
            cm2PerPx: WoundPipeline.cm2PerPixel(mmPerPx: mmPerPx, rasterScale: mScale),
            tissueEditedPx: 0, maskPx: maskPx,
            canvasW: canvasW, canvasH: canvasH, wbGains: wbGains
        )
    }

    // MARK: - 組織自動分區

    /**
     對**已縮到取樣網格**的 RGBA 緩衝做逐像素組織分類，回傳 `gw × gh` 的**修邊畫面碼**
     （0 ＝ 區域外）。

     - Parameter inside: `gw*gh` 的遮罩，非 0 才分類。
     - Parameter wbGains: 後端由校正貼紙中性色塊算出的白平衡增益 `[R,G,B]`。

     ⚠ **給了 `wbGains` 就用它，沒給才退回灰世界。** 灰世界假設「場景平均為灰」，
     而以傷口為主體的近拍照嚴重違反這個假設——後端實測紅色增益被壓到正確值的 ×0.78，
     被壓掉的肉芽像素掉出飽和度條件、落進「其他」。

     更要緊的是**一致性**：後端 classify 已改用色卡增益，這裡若還用灰世界，
     結果欄會顯示肉芽 73% 而修邊畫面只把 21% 標成肉芽——醫師的修正會從一個錯的起點開始，
     而那份 GT 會進訓練集。
     */
    static func classifyGrid(rgba: [UInt8], gw: Int, gh: Int,
                             inside: [UInt8], wbGains: [Double]?) -> [UInt8]? {
        guard gw >= 2, gh >= 2, rgba.count >= gw * gh * 4, inside.count >= gw * gh else { return nil }

        var sr = 0.0, sg = 0.0, sb = 0.0, n = 0
        for i in 0..<(gw * gh) where inside[i] != 0 {
            let o = i * 4
            sr += Double(rgba[o]); sg += Double(rgba[o + 1]); sb += Double(rgba[o + 2]); n += 1
        }
        guard n > 0 else { return nil }

        // 退回灰世界時仍只用**遮罩內**的像素算——用整張圖會被背景膚色拉偏，
        // 而膚色恰好落在肉芽的色相附近。
        let g = wbGains ?? TissueClassifierV2.wbGains(sr / Double(n), sg / Double(n), sb / Double(n))

        var raw = [UInt8](repeating: 0, count: gw * gh)
        for i in 0..<(gw * gh) where inside[i] != 0 {
            let o = i * 4
            let r = TissueClassifierV2.applyGain(Int(rgba[o]), g[0])
            let gg = TissueClassifierV2.applyGain(Int(rgba[o + 1]), g[1])
            let b = TissueClassifierV2.applyGain(Int(rgba[o + 2]), g[2])
            raw[i] = TissueSeg.clsToEdit[TissueClassifierV2.classifyPixel(r, gg, b)]
        }

        // 3×3 多數決。少了這一步畫面是一片椒鹽，醫師無法從中判斷任何東西，
        // 而「看不懂的分區」比「沒有分區」更糟——他會關掉圖層，等於這個功能不存在。
        var out = raw
        var cnt = [Int](repeating: 0, count: TissueCode.maxCode + 1)
        for y in 1..<(gh - 1) {
            for x in 1..<(gw - 1) {
                let i = y * gw + x
                if inside[i] == 0 { continue }
                for k in 0...TissueCode.maxCode { cnt[k] = 0 }
                for dy in -1...1 {
                    for dx in -1...1 {
                        let c = Int(raw[(y + dy) * gw + (x + dx)])
                        if c >= 1 && c <= TissueCode.maxCode { cnt[c] += 1 }
                    }
                }
                var best = Int(raw[i]), bn = -1
                for c in 1...TissueCode.maxCode where cnt[c] > bn { bn = cnt[c]; best = c }
                out[i] = UInt8(best)
            }
        }
        return out
    }

    /// 把 `gw × gh` 的分類結果最近鄰放大到柵格解析度並寫入 `tissue`。
    ///
    /// 不在柵格解析度上逐像素跑分類：1024² 要一百萬次 HSV 換算，結果還是椒鹽狀雜點；
    /// 在 512² 上算完再放大既快又平滑，代價只是分區邊界的精細度——那本來就要靠醫師修。
    static func upscaleTissue(_ grid: [UInt8], gw: Int, gh: Int,
                              into raster: inout EditRaster) {
        guard gw > 0, gh > 0, grid.count >= gw * gh else { return }
        for y in 0..<raster.mh {
            let gy = min(gh - 1, max(0, y * gh / raster.mh))
            for x in 0..<raster.mw {
                let gx = min(gw - 1, max(0, x * gw / raster.mw))
                raster.tissue[y * raster.mw + x] = grid[gy * gw + gx]
            }
        }
    }

    // MARK: - 遮罩 → 多邊形

    /**
     取**最大連通元件**的外緣（Moore 鄰域追蹤）。

     取最大而非全部：傷口是單一連通區域，零星的雜點是分割雜訊。把它們一起送出去，
     後端的鞋帶公式會算出一個包含雜點的多邊形，面積因此偏大。
     */
    static func traceBoundary(mask: [UInt8], mw: Int, mh: Int) -> [(Double, Double)] {
        guard mw > 1, mh > 1, mask.count >= mw * mh else { return [] }

        // 連通元件標記（4-連通，迭代式泛洪；遞迴在大遮罩上會爆堆疊）
        var label = [Int32](repeating: 0, count: mw * mh)
        var lbl: Int32 = 0
        var bestLbl: Int32 = 0, bestCnt = 0
        var stack = [Int](repeating: 0, count: mw * mh)

        for s in 0..<(mw * mh) where mask[s] != 0 && label[s] == 0 {
            lbl += 1
            label[s] = lbl
            var top = 0
            stack[top] = s; top += 1
            var cnt = 0
            while top > 0 {
                top -= 1
                let p = stack[top]
                cnt += 1
                let px = p % mw, py = p / mw
                if px > 0,      mask[p - 1] != 0,  label[p - 1] == 0  { label[p - 1] = lbl;  stack[top] = p - 1;  top += 1 }
                if px < mw - 1, mask[p + 1] != 0,  label[p + 1] == 0  { label[p + 1] = lbl;  stack[top] = p + 1;  top += 1 }
                if py > 0,      mask[p - mw] != 0, label[p - mw] == 0 { label[p - mw] = lbl; stack[top] = p - mw; top += 1 }
                if py < mh - 1, mask[p + mw] != 0, label[p + mw] == 0 { label[p + mw] = lbl; stack[top] = p + mw; top += 1 }
            }
            if cnt > bestCnt { bestCnt = cnt; bestLbl = lbl }
        }
        guard bestLbl != 0 else { return [] }

        func on(_ x: Int, _ y: Int) -> Bool {
            return x >= 0 && x < mw && y >= 0 && y < mh && label[y * mw + x] == bestLbl
        }

        var sx = -1, sy = -1
        outer: for y in 0..<mh {
            for x in 0..<mw where on(x, y) { sx = x; sy = y; break outer }
        }
        guard sx >= 0 else { return [] }

        let dirs = [(0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1)]
        var pts: [(Double, Double)] = []
        var cx = sx, cy = sy, d = 6
        let cap = 4 * (mw + mh) * 4
        var steps = 0
        repeat {
            pts.append((Double(cx), Double(cy)))
            var found = false
            for i in 0..<8 {
                let nd = (d + i) % 8
                let nx = cx + dirs[nd].0, ny = cy + dirs[nd].1
                if on(nx, ny) { cx = nx; cy = ny; d = (nd + 6) % 8; found = true; break }
            }
            if !found { break }
            steps += 1
        } while (cx != sx || cy != sy) && steps < cap
        return pts
    }

    /// Ramer–Douglas–Peucker 精簡。
    static func rdp(_ pts: [(Double, Double)], eps: Double) -> [(Double, Double)] {
        guard pts.count >= 8 else { return pts }
        var keep = [Bool](repeating: false, count: pts.count)
        keep[0] = true; keep[pts.count - 1] = true
        var stack: [(Int, Int)] = [(0, pts.count - 1)]
        while let seg = stack.popLast() {
            let (a, b) = seg
            guard b > a + 1 else { continue }
            let ax = pts[a].0, ay = pts[a].1
            let dx = pts[b].0 - ax, dy = pts[b].1 - ay
            let len = max(1e-6, (dx * dx + dy * dy).squareRoot())
            var maxD = 0.0, idx = -1
            for i in (a + 1)..<b {
                let dist = abs((pts[i].0 - ax) * dy - (pts[i].1 - ay) * dx) / len
                if dist > maxD { maxD = dist; idx = i }
            }
            if maxD > eps, idx > 0 {
                keep[idx] = true
                stack.append((a, idx)); stack.append((idx, b))
            }
        }
        return pts.enumerated().filter { keep[$0.offset] }.map { $0.element }
    }

    /**
     柵格遮罩 → **影像座標**的 GT 多邊形。

     ⚠ 這條路是**有損**的：`遮罩 →(邊界追蹤)→ 輪廓 →(RDP)→ 多邊形`。
     RDP 會把轉角切掉，所以**不要**拿這個多邊形再柵格化回去當下一輪的起點——
     Android 端實測那樣往返一次面積就掉 0.62%，而醫師什麼都沒改。
     續編一律用存下來的柵格原樣載回（見 `EditRasterCodec`）。

     這個多邊形只有一個用途：送給後端當 `gt_polygon`。
     */
    static func rasterToPolygon(_ r: EditRaster, epsilon: Double = 1.5) -> [[Int]] {
        let raw = traceBoundary(mask: r.mask, mw: r.mw, mh: r.mh)
        guard !raw.isEmpty else { return [] }
        let simp = rdp(raw, eps: epsilon)
        guard r.mScale > 0 else { return [] }
        return simp.map { p in
            [Int((r.rx0 + p.0 / r.mScale).rounded()),
             Int((r.ry0 + p.1 / r.mScale).rounded())]
        }
    }

    /**
     **所有**連通元件的輪廓 → 影像座標多邊形（由大到小）。

     送標註時 `gt_polygon` 只帶最大的那一個（相容舊後端），`gt_polygons` 帶全部。
     少了這個，多處傷口只有一處會進訓練集，其餘被標成背景。
     */
    static func rasterToPolygons(_ r: EditRaster, epsilon: Double = 1.5) -> [[[Int]]] {
        guard r.mScale > 0, r.mw > 1, r.mh > 1 else { return [] }
        var remaining = r.mask
        var out: [[[Int]]] = []
        // 逐次取最大連通元件、轉輪廓、再把該元件從剩餘遮罩挖掉。
        // 上限 12：真實傷口不會更多，而分割雜訊可能製造大量小元件。
        for _ in 0..<12 {
            let pts = traceBoundary(mask: remaining, mw: r.mw, mh: r.mh)
            guard pts.count >= 3 else { break }
            let simp = rdp(pts, eps: epsilon)
            guard simp.count >= 3 else { break }
            out.append(simp.map { p in
                [Int((r.rx0 + p.0 / r.mScale).rounded()),
                 Int((r.ry0 + p.1 / r.mScale).rounded())]
            })
            // 把剛取出的元件填掉（掃描線填充該輪廓在柵格座標的範圍）
            let fill = TissueSeg.rasterizePolygon(
                simp.map { [Int($0.0.rounded()), Int($0.1.rounded())] },
                x0: 0, y0: 0, bw: r.mw, bh: r.mh, gw: r.mw, gh: r.mh)
            var left = 0
            for i in 0..<remaining.count {
                if fill[i] != 0 { remaining[i] = 0 }
                if remaining[i] != 0 { left += 1 }
            }
            if left == 0 { break }
        }
        return out
    }

    // MARK: - 筆刷

    /**
     圓形筆刷。`tissueCode == nil` 代表只改遮罩（邊界筆刷），否則同時把遮罩內的組織
     改成指定類別。

     - Parameter erase: true ＝ 從遮罩移除。
     - Returns: 這一筆實際改動的組織像素數（累加進 `tissueEditedPx`）。

     ⚠ 擦除**只清 `mask` 不清 `tissue`**，與 Android 一致。上傳時 `TissueMaskCodec`
     會把遮罩外一律寫成 0，所以殘留值不會外流；而保留它讓「擦掉又補回來」不必重新分類。
     */
    @discardableResult
    static func paint(_ r: inout EditRaster, cx: Double, cy: Double, radius: Double,
                      tissueCode: UInt8?, erase: Bool, auto: [UInt8]?) -> Int {
        let x0 = max(0, Int((cx - radius).rounded(.down)))
        let x1 = min(r.mw - 1, Int((cx + radius).rounded(.up)))
        let y0 = max(0, Int((cy - radius).rounded(.down)))
        let y1 = min(r.mh - 1, Int((cy + radius).rounded(.up)))
        guard x0 <= x1, y0 <= y1 else { return 0 }

        let r2 = radius * radius
        var editedDelta = 0
        for y in y0...y1 {
            for x in x0...x1 {
                let dx = Double(x) - cx, dy = Double(y) - cy
                if dx * dx + dy * dy > r2 { continue }
                let i = y * r.mw + x

                if erase {
                    if r.mask[i] != 0 { r.mask[i] = 0; r.maskPx -= 1 }
                    continue
                }

                if r.mask[i] == 0 { r.mask[i] = 1; r.maskPx += 1 }

                if let code = tissueCode {
                    if r.tissue[i] != code {
                        // 只有「與分類器建議不同」才算醫師的修正。把單純重塗算進去的話，
                        // tissue_edited 會在醫師只是描邊時就變成 true，
                        // 而後端靠它決定這張遮罩能不能進訓練集。
                        let suggested = auto.flatMap { i < $0.count ? $0[i] : nil }
                        let wasEdited = suggested.map { r.tissue[i] != $0 } ?? false
                        let willBeEdited = suggested.map { code != $0 } ?? true
                        if !wasEdited && willBeEdited { editedDelta += 1 }
                        if wasEdited && !willBeEdited { editedDelta -= 1 }
                        r.tissue[i] = code
                    }
                } else if r.tissue[i] == 0, let a = auto, i < a.count {
                    // 邊界筆刷畫進來的新像素自動帶上分類器建議，而不是繼承某個預設類別。
                    // Android 舊版把整片填成「比例最高的那一類」，於是醫師送出的組織 GT
                    // 是「整個傷口都是肉芽」——把後端算對的 54/38/6 覆蓋成 100/0/0，
                    // 而畫面看起來完全正常。
                    r.tissue[i] = a[i]
                }
            }
        }
        r.tissueEditedPx = max(0, r.tissueEditedPx + editedDelta)
        return editedDelta
    }
}
