import SwiftUI
import UIKit

/**
 時間軸的兩張圖與縮圖（對等 Android `WoundTimelineScreen` 的 AreaTrendChart／
 TissueTrendChart／TimelineRow 縮圖）。

 面積縮小不等於在癒合：壞死傷口清創後面積會**變大**（好轉）；面積不變但肉芽轉腐肉
 是惡化。只看面積曲線，這兩種情況都會被讀反——組織比例是判讀癒合方向的另一半資訊，
 所以兩張圖都要有。
 */

/// 面積趨勢折線（舊→新）。Y 軸 0／半／滿刻度＋水平參考線＋首末日期軌——
/// 只有線條看得出形狀，看不出量級與時間間隔。
struct AreaTrendChart: View {
    let areas: [Double]
    let labels: [String]

    var body: some View {
        let maxA = max(0.001, areas.max() ?? 1)
        VStack(spacing: 2) {
            HStack(spacing: 4) {
                VStack {
                    Text(String(format: "%.1f", maxA)).font(.system(size: 9))
                    Spacer()
                    Text(String(format: "%.1f", maxA / 2)).font(.system(size: 9))
                    Spacer()
                    Text("0").font(.system(size: 9))
                }
                .frame(width: 34)
                Canvas { ctx, size in
                    let n = areas.count
                    guard n >= 2 else { return }
                    let padL: CGFloat = 6, padT: CGFloat = 6, padB: CGFloat = 6
                    let w = size.width - padL * 2
                    let h = size.height - padT - padB
                    func px(_ i: Int) -> CGFloat { return padL + w * CGFloat(i) / CGFloat(n - 1) }
                    func py(_ v: Double) -> CGFloat { return padT + h * CGFloat(1 - v / maxA) }
                    // 水平參考線（0／半／滿），量級才看得出來
                    for v in [0.0, maxA / 2, maxA] {
                        var g = Path()
                        g.move(to: CGPoint(x: padL, y: py(v)))
                        g.addLine(to: CGPoint(x: padL + w, y: py(v)))
                        ctx.stroke(g, with: .color(.primary.opacity(0.13)), lineWidth: 1)
                    }
                    var line = Path()
                    line.move(to: CGPoint(x: px(0), y: py(areas[0])))
                    for i in 1..<n { line.addLine(to: CGPoint(x: px(i), y: py(areas[i]))) }
                    ctx.stroke(line, with: .color(Color(red: 0.23, green: 0.35, blue: 0.55)), lineWidth: 3)
                    for i in 0..<n {
                        let r: CGFloat = 4
                        ctx.fill(Path(ellipseIn: CGRect(x: px(i) - r, y: py(areas[i]) - r,
                                                        width: r * 2, height: r * 2)),
                                 with: .color(Color(red: 0.75, green: 0.27, blue: 0.23)))
                    }
                }
            }
            // 日期軌：只標首末（中間點多了會疊在一起，反而看不清）
            if labels.count >= 2 {
                HStack {
                    Text(labels.first ?? "").font(.system(size: 9))
                    Spacer()
                    if labels.count > 2 {
                        Text("… \(labels.count) 點 …").font(.system(size: 9))
                        Spacer()
                    }
                    Text(labels.last ?? "").font(.system(size: 9))
                }
                .padding(.leading, 38)
                .foregroundStyle(.secondary)
            }
        }
    }
}

/// 組織比例趨勢（100% 堆疊柱）。
///
/// 順序刻意由「好」到「壞」：上皮 → 肉芽 → 其他 → 腐肉 → 壞死——堆疊圖的閱讀方式
/// 是看色帶消長，順序隨意排就看不出方向。
/// ⚠ **無資料 ≠ 0%**：v5 之前的紀錄沒有組織欄位，畫成 0% 的柱子會被讀成
/// 「當時完全沒有肉芽」——一個看起來完全合理的假數據。無資料畫灰底＋標示。
struct TissueTrendChart: View {
    let rows: [Measurement]
    let labels: [String]

    private static let order: [(Int, String)] = [(4, "上皮"), (1, "肉芽"), (5, "其他"), (2, "腐肉"), (3, "壞死")]

    private func frac(_ m: Measurement, _ code: Int) -> Double? {
        switch code {
        case 1: return m.tissueGranulation
        case 2: return m.tissueSlough
        case 3: return m.tissueNecrosis
        case 4: return m.tissueEpithelial
        case 5: return m.tissueOther
        default: return nil
        }
    }

    var body: some View {
        let hasAny = rows.contains { m in Self.order.contains { frac(m, $0.0) != nil } }
        VStack(alignment: .leading, spacing: 4) {
            Text("組織比例").font(.subheadline)
            if !hasAny {
                Text("這個個案還沒有組織比例資料（新版量測才會記錄）。")
                    .font(.caption).foregroundStyle(.secondary)
            } else {
                HStack(alignment: .bottom, spacing: 4) {
                    ForEach(Array(rows.enumerated()), id: \.element.id) { i, m in
                        VStack(spacing: 1) {
                            let vals = Self.order.map { frac(m, $0.0) }
                            let sum = vals.compactMap { $0 }.reduce(0, +)
                            if sum <= 0 {
                                Rectangle().fill(Color.secondary.opacity(0.2))
                                    .frame(maxHeight: .infinity)
                                Text("無資料").font(.system(size: 8)).lineLimit(1)
                                    .foregroundStyle(.secondary)
                            } else {
                                GeometryReader { geo in
                                    VStack(spacing: 0) {
                                        ForEach(Array(Self.order.enumerated()), id: \.offset) { k, item in
                                            let v = (vals[k] ?? 0) / sum
                                            if v > 0 {
                                                Rectangle()
                                                    .fill(EditPalette.color(item.0, opaque: true))
                                                    .frame(height: geo.size.height * CGFloat(v))
                                            }
                                        }
                                    }
                                }
                                Text(labels.indices.contains(i) ? labels[i] : "")
                                    .font(.system(size: 8)).lineLimit(1)
                            }
                        }
                        .frame(maxWidth: .infinity)
                    }
                }
                .frame(height: 110)
                HStack(spacing: 8) {
                    ForEach(Self.order, id: \.0) { code, name in
                        HStack(spacing: 2) {
                            Rectangle().fill(EditPalette.color(code, opaque: true))
                                .frame(width: 8, height: 8)
                            Text(name).font(.system(size: 10))
                        }
                    }
                }
            }
        }
    }
}

/// 單筆縮圖：IO 背景解密＋降採樣。**分辨「還在解」與「解不開」**——
/// 兩者都畫「…」的話，App 重裝使金鑰換新後整條時間軸像永遠載入中，
/// 而實際是影像已不可解密（與「已依保存期限清除」是兩回事）。
struct TimelineThumb: View {
    let m: Measurement
    let store: LocalImageStore

    private enum ThumbState { case loading, ok(UIImage), undecryptable, purged, none }
    @State private var state: ThumbState = .loading

    var body: some View {
        ZStack {
            Rectangle().fill(Color.secondary.opacity(0.15))
            switch state {
            case .loading:
                Text("…").font(.caption2).foregroundStyle(.secondary)
            case .ok(let img):
                Image(uiImage: img).resizable().scaledToFill()
            case .undecryptable:
                Text("無法解密").font(.system(size: 9)).foregroundStyle(.secondary)
            case .purged:
                Text("已清除").font(.system(size: 9)).foregroundStyle(.secondary)
            case .none:
                Text("無影像").font(.system(size: 9)).foregroundStyle(.secondary)
            }
        }
        .frame(width: 64, height: 64)
        .clipped()
        .cornerRadius(6)
        .task(id: m.id) {
            guard !m.imagePath.isEmpty else { state = .none; return }
            let path = m.imagePath
            let s = store
            // 解密＋降採樣走背景；不快取全圖（清單捲幾列就 OOM）。
            let result: (Bool, UIImage?) = await Task.detached {
                let exists = s.exists(path)
                return (exists, exists ? s.loadThumbnail(path, maxPixel: 200) : nil)
            }.value
            if let img = result.1 { state = .ok(img) }
            else if result.0 { state = .undecryptable }   // 檔案在但解不開＝金鑰換新
            else { state = .purged }                      // 檔案不在＝保存期限已清除
        }
    }
}
