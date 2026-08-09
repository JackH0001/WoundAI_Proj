import Foundation

/**
 遮罩 ↔ 輪廓的純演算法（對等 Android `WoundEditScreen.kt` 檔尾四支函式）。

 抽成獨立檔案的理由：這四支是**資料正確性**的關鍵路徑（畫布只是它們的操作介面），
 必須可以在沒有 UI 的情況下單元測試——合成一個已知形狀的遮罩，驗證
 填充 → 追蹤 → 簡化 → 再填充的往返行為。
 */
enum MaskTrace {

    /// 多邊形 scanline 填充（even-odd）→ mask（ROI 局部座標，工作解析度）。
    /// - Parameters:
    ///   - s: 影像座標 → 柵格座標的縮放。
    ///   - ox, oy: ROI 在影像座標的左上角。
    static func scanlineFill(_ poly: [[Int]], s: Double, mw: Int, mh: Int,
                             into out: inout [UInt8], ox: Double = 0, oy: Double = 0) {
        guard poly.count >= 3, mw > 0, mh > 0, out.count >= mw * mh else { return }
        let xs = poly.map { (Double($0.count > 0 ? $0[0] : 0) - ox) * s }
        let ys = poly.map { (Double($0.count > 1 ? $0[1] : 0) - oy) * s }
        var cuts: [Double] = []
        cuts.reserveCapacity(16)
        for y in 0..<mh {
            let yc = Double(y) + 0.5
            cuts.removeAll(keepingCapacity: true)
            var j = poly.count - 1
            for i in poly.indices {
                let yi = ys[i], yj = ys[j]
                if (yi > yc) != (yj > yc) {
                    cuts.append(xs[i] + (yc - yi) * (xs[j] - xs[i]) / (yj - yi))
                }
                j = i
            }
            cuts.sort()
            var t = 0
            while t + 1 < cuts.count {
                let x0 = max(0, Int(cuts[t].rounded()))
                let x1 = min(mw - 1, Int(cuts[t + 1].rounded()))
                if x0 <= x1 { for x in x0...x1 { out[y * mw + x] = 1 } }
                t += 2
            }
        }
    }

    /**
     **所有**連通元件的外邊界（Moore-neighbor），由大到小排序。

     ## 為什麼不能只取最大的那一個

     舊版 Android 是 `traceLargestBoundary`——函式名就寫著它會丟掉其他元件。
     臨床上同一肢體多處傷口是常態，而醫師在修邊畫面明明把兩個都標了。
     2026-08-07 實測的後果：參照圖只畫得出一個傷口；**送進訓練集的 GT 是錯的**——
     第二個傷口被標成背景，等於在教模型「那不是傷口」。

     ## 雜點過濾

     小於 `minPx` 的元件不回傳。筆刷邊緣與擴張時的殘留會產生幾像素的孤立點，
     把它們當成獨立傷口送出去只會製造垃圾標註。
     */
    static func traceAllBoundaries(_ mask: [UInt8], mw: Int, mh: Int,
                                   minPx: Int = 64) -> [[[Double]]] {
        guard mw > 0, mh > 0, mask.count >= mw * mh else { return [] }
        var label = [Int32](repeating: 0, count: mw * mh)
        var counts: [Int] = []                       // index = lbl-1
        var lbl: Int32 = 0
        var stack = [Int32](repeating: 0, count: mw * mh)
        for start in 0..<(mw * mh) where mask[start] != 0 && label[start] == 0 {
            lbl += 1
            var top = 0
            stack[top] = Int32(start); top += 1
            label[start] = lbl
            var cnt = 0
            while top > 0 {
                top -= 1
                let p = Int(stack[top]); cnt += 1
                let px = p % mw, py = p / mw
                if px > 0, mask[p - 1] != 0, label[p - 1] == 0 { label[p - 1] = lbl; stack[top] = Int32(p - 1); top += 1 }
                if px < mw - 1, mask[p + 1] != 0, label[p + 1] == 0 { label[p + 1] = lbl; stack[top] = Int32(p + 1); top += 1 }
                if py > 0, mask[p - mw] != 0, label[p - mw] == 0 { label[p - mw] = lbl; stack[top] = Int32(p - mw); top += 1 }
                if py < mh - 1, mask[p + mw] != 0, label[p + mw] == 0 { label[p + mw] = lbl; stack[top] = Int32(p + mw); top += 1 }
            }
            counts.append(cnt)
        }
        if lbl == 0 { return [] }
        // 由大到小：下游若只能取一個（舊格式相容），拿到的仍是主要傷口。
        let order = (1...Int(lbl)).filter { counts[$0 - 1] >= minPx }
            .sorted { counts[$0 - 1] > counts[$1 - 1] }
        return order.compactMap { target -> [[Double]]? in
            let b = traceOne(label, mw: mw, mh: mh, target: Int32(target))
            return b.count >= 3 ? b : nil
        }
    }

    /// 單一標籤的外邊界（Moore-neighbor 順時針走訪）。
    private static func traceOne(_ label: [Int32], mw: Int, mh: Int, target: Int32) -> [[Double]] {
        func on(_ x: Int, _ y: Int) -> Bool {
            return x >= 0 && x < mw && y >= 0 && y < mh && label[y * mw + x] == target
        }
        var sx = -1, sy = -1
        outer: for y in 0..<mh {
            for x in 0..<mw where on(x, y) { sx = x; sy = y; break outer }
        }
        if sx < 0 { return [] }
        let dirs: [(Int, Int)] = [(0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1)]
        var pts: [[Double]] = []
        var cx = sx, cy = sy, d = 6
        let cap = 4 * (mw + mh) * 4
        var steps = 0
        repeat {
            pts.append([Double(cx), Double(cy)])
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
    static func rdp(_ pts: [[Double]], eps: Double) -> [[Double]] {
        if pts.count < 8 { return pts }
        var keep = [Bool](repeating: false, count: pts.count)
        keep[0] = true; keep[pts.count - 1] = true
        var stack: [(Int, Int)] = [(0, pts.count - 1)]
        while let seg = stack.popLast() {
            let (a, b) = seg
            var maxD = 0.0, idx = -1
            let ax = pts[a][0], ay = pts[a][1], bx = pts[b][0], by = pts[b][1]
            let dx = bx - ax, dy = by - ay
            let len = max(1e-6, (dx * dx + dy * dy).squareRoot())
            for i in (a + 1)..<b {
                let dist = abs((pts[i][0] - ax) * dy - (pts[i][1] - ay) * dx) / len
                if dist > maxD { maxD = dist; idx = i }
            }
            if maxD > eps, idx > 0 {
                keep[idx] = true
                stack.append((a, idx)); stack.append((idx, b))
            }
        }
        return pts.enumerated().filter { keep[$0.offset] }.map { $0.element }
    }
}
