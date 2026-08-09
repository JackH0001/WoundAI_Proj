import SwiftUI
import CoreGraphics
import UIKit

/**
 醫師確認・修邊（對等 Android `WoundEditScreen`）。

 這個畫面是飛輪的**前提**：沒有經過人的判斷，`doctor_verified` 就不能是 true，
 而後端靠那個欄位決定一筆標註能不能進訓練集。

 ## 為什麼是筆刷而不是拖曳頂點

 早期版本讓醫師拖多邊形頂點。問題有兩個：頂點自交會讓鞋帶公式算出負面積或錯誤面積，
 而簡化過的多邊形本來就少掉轉角。改成**柵格筆刷**之後，面積直接由像素數乘上
 `cm2PerPx` 得出——沒有多邊形，就沒有自交，也沒有簡化誤差。

 多邊形只在送出時由柵格產生一次（`RasterOps.rasterToPolygon`），純粹是後端契約要的格式。
 */
struct WoundEditView: View {

    let image: UIImage
    let initialPolygon: [[Int]]
    /// 全部輪廓（多處傷口）。空的話退回 `initialPolygon`。
    let initialPolygons: [[[Int]]]
    let imageW: Int
    let imageH: Int
    let mmPerPx: Double?
    let wbGains: [Double]?
    /// 從時間軸回頭修邊時原樣載回的柵格。有它就不重建——重建會丟掉組織分區、面積也會漂移。
    let resume: EditRaster?

    let onCancel: () -> Void
    let onDone: (EditRaster) -> Void

    @State private var raster: EditRaster?
    /// 分類器的建議底稿。用來判斷哪些像素是醫師「真的改過」的。
    @State private var auto: [UInt8] = []
    @State private var overlay: UIImage?
    @State private var tool: Tool = .boundaryAdd
    @State private var brushRadius: Double = 18
    @State private var showTissue = true
    @State private var busy = true

    enum Tool: Hashable {
        case boundaryAdd, boundaryErase
        case tissue(UInt8)

        var label: String {
            switch self {
            case .boundaryAdd:   return "邊界＋"
            case .boundaryErase: return "邊界－"
            case .tissue(let c): return TissueCode.editNames[Int(c)]
            }
        }
    }

    /**
     組織疊色。

     ⚠ **五個顏色的 alpha 必須一致。** 舊版 Android 用了 70/110/130/110 四種沒有理由的
     透明度，後果不只是美觀：**α 高的區域看起來「比較多」**，醫師在比較相鄰兩塊組織的
     範圍時會被透明度誤導，而他正在做的判斷會直接變成訓練用的 GT。

     選 0.45：低到看得見底下的組織紋理（醫師要靠紋理判斷），高到在強光下的手機螢幕上
     仍分得出區塊。
     */
    static let tissueAlpha: Double = 0.45
    static let tissueColors: [UIColor] = [
        .clear,
        UIColor(red:  29/255, green: 158/255, blue: 117/255, alpha: tissueAlpha),  // 1 肉芽：綠（紅的互補色）
        UIColor(red: 239/255, green: 159/255, blue:  39/255, alpha: tissueAlpha),  // 2 腐肉：琥珀
        UIColor(red:  60/255, green:  52/255, blue: 137/255, alpha: tissueAlpha),  // 3 壞死：深紫（純黑會與陰影混淆）
        UIColor(red: 237/255, green: 147/255, blue: 177/255, alpha: tissueAlpha),  // 4 上皮：粉
        UIColor(red: 180/255, green: 178/255, blue: 169/255, alpha: tissueAlpha)   // 5 其他
    ]

    var body: some View {
        VStack(spacing: 10) {
            header
            canvas
            controls
        }
        .padding(12)
        .task { await prepare() }
    }

    // MARK: - 標頭：即時面積與組織比例

    private var header: some View {
        VStack(alignment: .leading, spacing: 4) {
            if let r = raster {
                Text(r.areaCm2.map { String(format: "面積 %.2f cm²", $0) } ?? "面積：未校正（無貼紙）")
                    .font(.headline)
                let f = r.tissueFrac()
                Text("肉芽 \(pct(f["granulation"]))・腐肉 \(pct(f["slough"]))・壞死 \(pct(f["necrosis"]))"
                     + "・上皮 \(pct(f["epithelial"]))・其他 \(pct(f["other"]))")
                    .font(.caption)
                Text(r.tissueEdited
                     ? "已修正 \(r.tissueEditedPx) 個組織像素（\(pct(r.tissueEditRatio))）"
                     : "尚未修正組織分區——未修正的分區是演算法原樣輸出，不會進訓練集")
                    .font(.caption2)
                    .foregroundStyle(r.tissueEdited ? .secondary : .orange)
            } else if busy {
                Text("載入中…").font(.headline)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func pct(_ v: Double?) -> String {
        guard let v else { return "—" }
        return "\(Int((v * 100).rounded()))%"
    }

    // MARK: - 畫布

    private var canvas: some View {
        GeometryReader { geo in
            let fit = fitRect(in: geo.size)
            // 全部用 topLeading + offset 定位。混用 .position（中心座標）與 .offset
            // （左上角位移）會讓疊圖偏移半張圖，而那看起來就像 ROI 算錯了。
            ZStack(alignment: .topLeading) {
                Image(uiImage: image)
                    .resizable()
                    .frame(width: fit.width, height: fit.height)
                    .offset(x: fit.minX, y: fit.minY)

                if let ov = overlay, let r = raster {
                    // 疊圖只覆蓋 ROI 那一塊，依 tissue_raster 的仿射參數定位。
                    // 拉滿整張影像是後端預覽踩過的坑——色塊大幅溢出輪廓，
                    // 而複核者會拿一張錯位的圖去判斷該不該排除這筆紀錄。
                    let rect = r.imageRect
                    let s = fit.width / Double(imageW)
                    Image(uiImage: ov)
                        .resizable()
                        .interpolation(.none)     // 類別色塊不可插值，會糊出不存在的中間色
                        .frame(width: rect.width * s, height: rect.height * s)
                        .offset(x: fit.minX + rect.minX * s, y: fit.minY + rect.minY * s)
                }
            }
            .frame(width: geo.size.width, height: geo.size.height, alignment: .topLeading)
            .contentShape(Rectangle())
            .gesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { v in stroke(at: v.location, fit: fit) }
                    .onEnded { _ in redraw() }
            )
        }
        .frame(minHeight: 320)
        .background(Color.black.opacity(0.06))
        .clipped()
    }

    /// 影像以 `.fit` 置中後的實際矩形。兩軸各自縮放會讓筆刷位置與畫面對不上。
    private func fitRect(in size: CGSize) -> CGRect {
        let iw = Double(max(imageW, 1)), ih = Double(max(imageH, 1))
        let s = min(size.width / iw, size.height / ih)
        let w = iw * s, h = ih * s
        return CGRect(x: (size.width - w) / 2, y: (size.height - h) / 2, width: w, height: h)
    }

    // MARK: - 工具列

    private var controls: some View {
        VStack(spacing: 8) {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    toolChip(.boundaryAdd)
                    toolChip(.boundaryErase)
                    ForEach(1...UInt8(TissueCode.maxCode), id: \.self) { toolChip(.tissue($0)) }
                }
            }

            HStack {
                Text("筆刷").font(.caption)
                Slider(value: $brushRadius, in: 4...60)
                Text("\(Int(brushRadius))").font(.caption).monospacedDigit()
                Toggle("組織圖層", isOn: $showTissue)
                    .toggleStyle(.button).font(.caption)
                    .onChange(of: showTissue) { _, _ in redraw() }
            }

            HStack {
                Button("取消", role: .cancel) { onCancel() }
                Spacer()
                Button("完成修邊") { if let r = raster { onDone(r) } }
                    .buttonStyle(.borderedProminent)
                    .disabled(raster == nil || (raster?.maskPx ?? 0) == 0)
            }

            Text("完成修邊代表你已檢視並確認這個輪廓與組織分區。這份判斷會作為訓練資料的真值。")
                .font(.caption2).foregroundStyle(.secondary)
        }
    }

    private func toolChip(_ t: Tool) -> some View {
        Button {
            tool = t
        } label: {
            Text(t.label).font(.caption)
                .padding(.horizontal, 10).padding(.vertical, 6)
        }
        .background(tool == t ? Color.accentColor.opacity(0.25) : Color.secondary.opacity(0.12))
        .cornerRadius(8)
    }

    // MARK: - 筆畫

    private func stroke(at p: CGPoint, fit: CGRect) {
        guard var r = raster, r.mScale > 0 else { return }
        let s = fit.width / Double(imageW)
        guard s > 0 else { return }
        // 畫面座標 → 影像座標 → 柵格座標
        let ix = (p.x - fit.minX) / s
        let iy = (p.y - fit.minY) / s
        let mx = (ix - r.rx0) * r.mScale
        let my = (iy - r.ry0) * r.mScale

        let code: UInt8?
        let erase: Bool
        switch tool {
        case .boundaryAdd:   code = nil; erase = false
        case .boundaryErase: code = nil; erase = true
        case .tissue(let c): code = c;   erase = false
        }
        RasterOps.paint(&r, cx: mx, cy: my, radius: brushRadius, tissueCode: code,
                        erase: erase, auto: auto)
        raster = r
        redraw()
    }

    // MARK: - 準備與繪製

    private func prepare() async {
        defer { busy = false }

        if let resumed = resume {
            // 原樣載回：像素數不變 → 面積不變。**不要**由多邊形重建。
            raster = resumed
            auto = resumed.tissue          // 續編時把現況當底稿，避免把舊修正誤判成新修正
            redraw()
            return
        }

        let polys = initialPolygons.isEmpty ? [initialPolygon] : initialPolygons
        guard var r = RasterOps.buildRaster(polygons: polys, imageW: imageW, imageH: imageH,
                                            mmPerPx: mmPerPx, canvasW: imageW, canvasH: imageH,
                                            wbGains: wbGains),
              let cg = image.cgImage else { return }

        // 取樣網格上分類，再最近鄰放大到柵格。
        let (gw, gh) = TissueSeg.grid(Int(r.imageRect.width), Int(r.imageRect.height))
        if let rgba = Self.scaledRGBA(cg, rect: r.imageRect, gw: gw, gh: gh) {
            var inside = [UInt8](repeating: 0, count: gw * gh)
            for gy in 0..<gh {
                for gx in 0..<gw {
                    let mx = min(r.mw - 1, max(0, gx * r.mw / gw))
                    let my = min(r.mh - 1, max(0, gy * r.mh / gh))
                    if r.mask[my * r.mw + mx] != 0 { inside[gy * gw + gx] = 1 }
                }
            }
            // 遮罩太小（AI 沒抓到）時網格上可能一格都不落——整片算，之後由筆刷決定範圍。
            if !inside.contains(where: { $0 != 0 }) {
                for i in 0..<inside.count { inside[i] = 1 }
            }
            if let grid = RasterOps.classifyGrid(rgba: rgba, gw: gw, gh: gh,
                                                 inside: inside, wbGains: r.wbGains) {
                RasterOps.upscaleTissue(grid, gw: gw, gh: gh, into: &r)
                auto = r.tissue
            }
        }
        raster = r
        redraw()
    }

    /// 把影像的 ROI 區域縮到 `gw × gh` 的 RGBA 緩衝。
    private static func scaledRGBA(_ cg: CGImage, rect: CGRect, gw: Int, gh: Int) -> [UInt8]? {
        guard gw > 0, gh > 0, rect.width >= 1, rect.height >= 1,
              let roi = cg.cropping(to: rect) else { return nil }
        var buf = [UInt8](repeating: 0, count: gw * gh * 4)
        let cs = CGColorSpaceCreateDeviceRGB()
        guard let ctx = CGContext(data: &buf, width: gw, height: gh, bitsPerComponent: 8,
                                  bytesPerRow: gw * 4, space: cs,
                                  bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else { return nil }
        ctx.interpolationQuality = .high
        ctx.draw(roi, in: CGRect(x: 0, y: 0, width: gw, height: gh))
        return buf
    }

    /// 重畫疊圖：組織填色 ＋ 遮罩邊界。
    ///
    /// ⚠ 組織圖層的開關**不影響邊界**。舊版把開關做在「整張 overlay 要不要畫」，
    /// 而 overlay 同時承載邊界與組織色——關掉組織的同時邊界也不見了，
    /// 於是醫師在看不到輪廓的情況下描邊。
    private func redraw() {
        guard let r = raster, r.mw > 0, r.mh > 0 else { overlay = nil; return }
        let n = r.mw * r.mh
        var px = [UInt8](repeating: 0, count: n * 4)

        // ⚠ CGBitmapContext 在 8-bit RGBA 只支援**預乘** alpha（`kCGImageAlphaLast`
        //   未預乘的變體不被支援）。直接寫入原始 RGB 再配上 alpha=115，繪製時會被當成
        //   已預乘的值解讀，顏色整批偏亮——而它不會報錯，只是顏色不對。
        //   先把顏色分量乘上 alpha 存成查表，迴圈裡直接取用。
        var premul: [(UInt8, UInt8, UInt8, UInt8)] = []
        for c in 0...TissueCode.maxCode {
            var cr: CGFloat = 0, cg: CGFloat = 0, cb: CGFloat = 0, ca: CGFloat = 0
            Self.tissueColors[c].getRed(&cr, green: &cg, blue: &cb, alpha: &ca)
            // ⚠ 夾住再轉。UInt8(_:) 對超出範圍的浮點值是**直接 trap**，
            //   而 getRed 在色彩空間不相容時可能回 false 讓變數維持未定值。
            func q(_ v: CGFloat) -> UInt8 { UInt8(max(0, min(255, (v * 255).rounded()))) }
            premul.append((q(cr * ca), q(cg * ca), q(cb * ca), q(ca)))
        }

        if showTissue {
            for i in 0..<n {
                guard r.mask[i] != 0 else { continue }
                let c = Int(r.tissue[i])
                guard c >= 1, c <= TissueCode.maxCode else { continue }
                let (pr, pg, pb, pa) = premul[c]
                px[i * 4 + 0] = pr; px[i * 4 + 1] = pg
                px[i * 4 + 2] = pb; px[i * 4 + 3] = pa
            }
        }

        // 邊界：遮罩內但四鄰有空的像素。獨立於組織圖層之外畫。
        for y in 0..<r.mh {
            for x in 0..<r.mw {
                let i = y * r.mw + x
                guard r.mask[i] != 0 else { continue }
                let edge = x == 0 || y == 0 || x == r.mw - 1 || y == r.mh - 1
                    || r.mask[i - 1] == 0 || r.mask[i + 1] == 0
                    || r.mask[i - r.mw] == 0 || r.mask[i + r.mw] == 0
                if edge {
                    px[i * 4 + 0] = 0; px[i * 4 + 1] = 229; px[i * 4 + 2] = 255; px[i * 4 + 3] = 255
                }
            }
        }

        overlay = Self.imageFromRGBA(px, width: r.mw, height: r.mh)
    }

    private static func imageFromRGBA(_ rgba: [UInt8], width: Int, height: Int) -> UIImage? {
        var buf = rgba
        let cs = CGColorSpaceCreateDeviceRGB()
        // premultipliedLast：這裡的 alpha 是真的透明度（不是類別碼），可以預乘。
        guard let ctx = CGContext(data: &buf, width: width, height: height, bitsPerComponent: 8,
                                  bytesPerRow: width * 4, space: cs,
                                  bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue),
              let cg = ctx.makeImage() else { return nil }
        return UIImage(cgImage: cg)
    }
}
