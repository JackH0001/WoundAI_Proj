import SwiftUI
import UIKit

/**
 醫師修邊畫布（對等 Android `WoundEditScreen.kt`，元件級對齊）。

 邊界筆刷（＝GT）＋組織筆刷（互斥塗蓋）＋亮青邊界線。柵格 ROI 高解析（≈原圖像素）；
 **筆刷靠近框緣自動擴張視窗**（同解析度、內容像素級搬移，cm²/px 係數不變）。
 遮罩跨回合持久化（`EditRaster`）零損耗；面積＝像素數 × 鎖定係數，冪等。
 輔助、非診斷、需醫師確認。

 ## 手勢為什麼走 UIKit 原始觸控

 Android 把「單指依工具塗抹／雙指縮放平移」放在**同一個手勢迴圈**裡依觸點數分流——
 兩個獨立手勢疊起來的話，先註冊的會把事件吃掉（雙指縮放永遠不觸發，或反過來畫不了圖）。
 SwiftUI 的 `DragGesture`＋`MagnificationGesture` 組合正是那種「兩個獨立手勢」，
 而且第一指落下到第二指被辨識之間會漏一筆塗抹進 GT。所以觸控層用
 `UIViewRepresentable` 收原始 touches，分流邏輯與 Android 逐行同構
 （含「進入雙指時還原第一指誤畫的那一筆」）。
 */

// MARK: - 工具與調色盤

private enum EditTool { case bPaint, bErase, pan, tissue }

/**
 組織圖層配色（RGBA，straight alpha）。索引＝修邊畫面碼。

 α 一律 115（45%）：低到看得見底下的組織紋理（醫師要靠紋理判斷），高到強光下仍分得出區塊。
 α 不一致的話 **α 高的區域看起來「比較多」**——醫師比較相鄰兩塊組織範圍時會被透明度誤導，
 而他正在做的判斷會直接變成訓練用的 GT。肉芽用綠（紅的互補色）：半透明紅疊在紅色肉芽上
 是資訊量為零的疊圖。與 Android `T_COLORS` 同值。
 */
enum EditPalette {
    static let alpha: UInt8 = 115
    /// (r, g, b)；索引 0 不用。
    static let rgb: [(UInt8, UInt8, UInt8)] = [
        (0, 0, 0),
        (29, 158, 117),    // 肉芽：綠
        (239, 159, 39),    // 腐肉：琥珀
        (60, 52, 137),     // 壞死：深紫（純黑會與陰影混淆）
        (237, 147, 177),   // 上皮：粉
        (180, 178, 169)    // 其他／未分類：灰
    ]
    static let edge: (UInt8, UInt8, UInt8) = (0, 229, 255)   // 亮青，α=255

    static func color(_ code: Int, opaque: Bool = false) -> Color {
        let c = rgb[max(0, min(TissueCode.maxCode, code))]
        return Color(red: Double(c.0) / 255, green: Double(c.1) / 255, blue: Double(c.2) / 255)
            .opacity(opaque ? 1.0 : Double(alpha) / 255)
    }
}

private let maxMaskDim = 2200   // 擴張上限（記憶體防護）

// MARK: - 柵格狀態（非 Compose/SwiftUI 狀態；變更後由呼叫端 bump() 觸發重繪）

@MainActor
private final class RasterState {
    var rx0: Double
    var ry0: Double
    var mw: Int
    var mh: Int
    let mScale: Double
    let bw: Int
    let bh: Int

    var mask: [UInt8]
    var tissue: [UInt8]
    var orig: [UInt8]
    /**
     分類器對每個像素的建議（修邊畫面碼；0＝尚未算過）。這不是遮罩，是「AI 覺得這裡是什麼」
     的底稿：① 進修邊時把 tissue 初始化成逐像素分區 ② 「邊界＋」畫進新像素時自動帶分類。

     ⚠ Android 舊版把整片填成比例最高的那一類，於是送出的組織 GT 是「整個傷口都是肉芽」——
     把後端算對的 54/38/6 覆蓋成 100/0/0。畫面看起來完全正常，資料是錯的。
     */
    var auto: [UInt8]
    /// RGBA（straight alpha）覆蓋圖緩衝。只在髒矩形內重算，CGImage 由外層 memoize。
    var overlay: [UInt8]
    var maskCount = 0
    var tCounts = [Int](repeating: 0, count: TissueCode.maxCode + 1)
    /// 醫師實際改過的組織像素數（tissue ≠ auto）。增量維護，restore／擴張時全量重算。
    var editedCount = 0
    var cm2PerPx: Double?
    /**
     組織填色是否顯示。**邊界不受此影響**——關圖層的用意是看清底下的組織紋理，
     不是放棄邊界回饋；「畫看不見的東西」那一筆會直接進 GT。
     */
    var showTissue = true

    init(rx0: Double, ry0: Double, mw: Int, mh: Int, mScale: Double, bw: Int, bh: Int) {
        self.rx0 = rx0; self.ry0 = ry0; self.mw = mw; self.mh = mh
        self.mScale = mScale; self.bw = bw; self.bh = bh
        mask = [UInt8](repeating: 0, count: mw * mh)
        tissue = [UInt8](repeating: 0, count: mw * mh)
        orig = [UInt8](repeating: 0, count: mw * mh)
        auto = [UInt8](repeating: 0, count: mw * mh)
        overlay = [UInt8](repeating: 0, count: mw * mh * 4)
    }

    private func writeColor(at i: Int, x: Int, y: Int) {
        let o = i * 4
        if mask[i] == 0 {
            overlay[o] = 0; overlay[o + 1] = 0; overlay[o + 2] = 0; overlay[o + 3] = 0
            return
        }
        let edge = x == 0 || y == 0 || x == mw - 1 || y == mh - 1 ||
            mask[i - 1] == 0 || mask[i + 1] == 0 ||
            mask[i - mw] == 0 || mask[i + mw] == 0
        if edge {
            overlay[o] = EditPalette.edge.0; overlay[o + 1] = EditPalette.edge.1
            overlay[o + 2] = EditPalette.edge.2; overlay[o + 3] = 255
            return
        }
        if showTissue {
            let c = EditPalette.rgb[max(1, min(TissueCode.maxCode, Int(tissue[i])))]
            overlay[o] = c.0; overlay[o + 1] = c.1; overlay[o + 2] = c.2
            overlay[o + 3] = EditPalette.alpha
        } else {
            // 隱藏組織時內部透明，邊界仍在——「看得見自己在畫什麼」的最低要求。
            overlay[o] = 0; overlay[o + 1] = 0; overlay[o + 2] = 0; overlay[o + 3] = 0
        }
    }

    func syncAll() { refresh(0, 0, mw - 1, mh - 1) }

    func refresh(_ rx: Int, _ ry: Int, _ rx1: Int, _ ry1: Int) {
        let a0 = max(0, rx), b0 = max(0, ry)
        let a1 = min(mw - 1, rx1), b1 = min(mh - 1, ry1)
        guard a0 <= a1, b0 <= b1 else { return }
        for y in b0...b1 { for x in a0...a1 { writeColor(at: y * mw + x, x: x, y: y) } }
    }

    func recount() {
        for i in tCounts.indices { tCounts[i] = 0 }
        var c = 0, ed = 0
        for i in 0..<(mw * mh) where mask[i] != 0 {
            c += 1
            tCounts[max(1, min(TissueCode.maxCode, Int(tissue[i])))] += 1
            if tissue[i] != auto[i] { ed += 1 }
        }
        maskCount = c
        editedCount = ed
    }

    /// 視需要向外擴張（維持 mScale；內容整格搬移，像素級無損）。回傳是否擴張。
    func expandIfNeeded(cxM: Double, cyM: Double, rM: Double) -> Bool {
        let margin = rM + 6
        var gL = 0, gR = 0, gT = 0, gB = 0
        let grow = max(64, max(mw, mh) / 2)
        if cxM - margin < 0 { gL = grow }
        if cxM + margin > Double(mw) { gR = grow }
        if cyM - margin < 0 { gT = grow }
        if cyM + margin > Double(mh) { gB = grow }
        if gL + gR + gT + gB == 0 { return false }
        // 邊界夾擠：不可超出影像、不可超過總尺寸上限。
        gL = min(gL, max(0, Int(rx0 * mScale)))
        gT = min(gT, max(0, Int(ry0 * mScale)))
        let rightRoom = max(0, Int((Double(bw) - (rx0 + Double(mw) / mScale)) * mScale))
        let bottomRoom = max(0, Int((Double(bh) - (ry0 + Double(mh) / mScale)) * mScale))
        gR = min(gR, rightRoom); gB = min(gB, bottomRoom)
        if mw + gL + gR > maxMaskDim { gR = max(0, gR - (mw + gL + gR - maxMaskDim)) }
        if mh + gT + gB > maxMaskDim { gB = max(0, gB - (mh + gT + gB - maxMaskDim)) }
        if gL + gR + gT + gB == 0 { return false }
        let nw = mw + gL + gR, nh = mh + gT + gB
        func move(_ src: [UInt8]) -> [UInt8] {
            var d = [UInt8](repeating: 0, count: nw * nh)
            for y in 0..<mh {
                let s0 = y * mw, d0 = (y + gT) * nw + gL
                d.replaceSubrange(d0..<(d0 + mw), with: src[s0..<(s0 + mw)])
            }
            return d
        }
        mask = move(mask); tissue = move(tissue); orig = move(orig); auto = move(auto)
        rx0 -= Double(gL) / mScale; ry0 -= Double(gT) / mScale
        mw = nw; mh = nh
        // 舊 overlay 直接換新配置。（Android 這裡要手動 recycle Bitmap 防 39MB 尖峰；
        // Swift 的 Array 舊儲存在無別名時隨即釋放，語意相同。）
        overlay = [UInt8](repeating: 0, count: mw * mh * 4)
        recount()
        syncAll()
        return true
    }

    /// 遮罩外框（影像座標 [x0, y0, x1, y1]），無遮罩回 nil。
    func maskBBoxImg() -> [Double]? {
        var x0 = Int.max, y0 = Int.max, x1 = -1, y1 = -1
        for y in 0..<mh {
            for x in 0..<mw where mask[y * mw + x] != 0 {
                if x < x0 { x0 = x }; if x > x1 { x1 = x }
                if y < y0 { y0 = y }; if y > y1 { y1 = y }
            }
        }
        if x1 < 0 { return nil }
        return [rx0 + Double(x0) / mScale, ry0 + Double(y0) / mScale,
                rx0 + Double(x1) / mScale, ry0 + Double(y1) / mScale]
    }
}

/**
 用 `TissueSeg.classify` 重算整個柵格的分類底稿（`auto`）——**純計算，背景緒可跑**。

 在**取樣網格**上分類再最近鄰放大，不在柵格解析度上逐像素跑：2200² 要 484 萬次 HSV 換算，
 而且結果是椒鹽狀雜點；512² 上算完再放大既快又平滑，代價只是分區邊界精細度——
 那本來就要靠醫師修。

 ⚠ 2026-08-18 改版：原本這件事在 `EditCanvasModel.init` 內同步做（MainActor），
 Debug 組建（-Onone）要 2–10 秒——「進入修邊畫面白凍」的根因。現在改為
 開畫面後由 `runSeed()` 丟到背景，這裡只吃值、不碰 `RasterState`，算完回主緒套用。
 */
private func computeAutoGrid(rx0: Double, ry0: Double, mw: Int, mh: Int, mScale: Double,
                             mask: [UInt8], src: CGImage, wbGains: [Double]?) -> [UInt8]? {
    let x0 = max(0, min(src.width - 2, Int(rx0.rounded())))
    let y0 = max(0, min(src.height - 2, Int(ry0.rounded())))
    let x1 = max(x0 + 2, min(src.width, Int((rx0 + Double(mw) / mScale).rounded())))
    let y1 = max(y0 + 2, min(src.height, Int((ry0 + Double(mh) / mScale).rounded())))
    let (gw, gh) = TissueSeg.grid(x1 - x0, y1 - y0)
    // 只在遮罩內分類。遮罩外是皮膚與背景，分類它們既浪費又會把白平衡增益拉偏。
    var inside = [UInt8](repeating: 0, count: gw * gh)
    for gy in 0..<gh {
        for gx in 0..<gw {
            let mx = max(0, min(mw - 1, gx * mw / gw))
            let my = max(0, min(mh - 1, gy * mh / gh))
            if mask[my * mw + mx] != 0 { inside[gy * gw + gx] = 1 }
        }
    }
    // 遮罩太小（例如 AI 沒抓到）時網格上可能一格都不落——那就整片算，之後由筆刷決定範圍。
    if !inside.contains(where: { $0 != 0 }) {
        for i in inside.indices { inside[i] = 1 }
    }
    guard let g = TissueSeg.classify(src, x0: x0, y0: y0, x1: x1, y1: y1,
                                     gw: gw, gh: gh, inside: inside, wbGains: wbGains) else { return nil }
    var auto = [UInt8](repeating: 0, count: mw * mh)
    for y in 0..<mh {
        let gy = max(0, min(gh - 1, y * gh / mh))
        for x in 0..<mw {
            let gx = max(0, min(gw - 1, x * gw / mw))
            auto[y * mw + x] = g[gy * gw + gx]
        }
    }
    return auto
}

/// 同步版（MainActor）：**畫布擴張的當下**要立即補算新視窗的底稿——筆畫接著就要讀
/// `st.auto` 給新像素帶分類，async 會讓這些像素落到預設類。擴張視窗遠小於初始整圖，
/// 單次耗時可接受；初始的大計算已移到 `runSeed()` 背景。
@MainActor
private func seedAutoSync(_ st: RasterState, src: CGImage, wbGains: [Double]?) {
    if let auto = computeAutoGrid(rx0: st.rx0, ry0: st.ry0, mw: st.mw, mh: st.mh,
                                  mScale: st.mScale, mask: st.mask, src: src, wbGains: wbGains),
       auto.count == st.mw * st.mh {
        st.auto = auto
    }
}

// MARK: - 模型（狀態＋手勢分流＋undo）

@MainActor
private final class EditCanvasModel: ObservableObject {
    /// 只用來觸發重繪。柵格內容都在 `st` 裡，逐像素塗抹不該經過 SwiftUI diff。
    @Published var version = 0
    @Published var tool: EditTool = .bPaint
    @Published var curTissue = 2
    /// 筆刷半徑（**點**，不是像素）。Android 的 36 是 px（≈420dpi 下約 12pt）；
    /// iOS 直接抄 36 會是三倍粗——實機回報「預設過粗」的根因。14pt ≈ Android 視覺同寬。
    @Published var brushScreen: CGFloat = 14
    @Published var cursor: CGPoint?
    @Published var peeking = false
    @Published var viewScale: CGFloat = 1
    @Published var viewOffset = CGPoint.zero
    @Published var boxSize = CGSize.zero
    @Published var undoCount = 0
    @Published var redoCount = 0
    /// 組織底稿（auto）背景計算中。期間畫布觸控與「完成修邊」鎖定——
    /// 底稿沒好之前塗抹會蓋在待覆寫的資料上，鎖住比事後解釋一致性簡單。
    @Published var seeding = false

    let st: RasterState
    let cg: CGImage?
    let bw: Int
    let bh: Int
    let defaultClass: Int
    let wbGains: [Double]?
    let originalArea: Double?
    /// true＝全新編修（底稿算完要拿 auto 種 tissue）；false＝續編（tissue 已有人修過，auto 只供自動筆用）。
    private let freshSeed: Bool
    /// 民眾版借用：只圈邊界，不算組織底稿（TissueSeg 對 Lite 無意義，白耗 2–5 秒）。
    let boundaryOnly: Bool
    private var viewInit = false

    // 覆蓋圖 CGImage：每個 bump 失效一次，同一幀內多筆塗抹只重建一張。
    private var overlayCache: CGImage?
    private var overlayCacheVersion = -1

    struct Snap {
        let m: [UInt8]; let t: [UInt8]
        let mw: Int; let mh: Int
        let rx0: Double; let ry0: Double
    }
    private var undoStack: [Snap] = []
    private var redoStack: [Snap] = []

    // 手勢分流狀態（對齊 Android awaitEachGesture 的迴圈區域變數）。
    private var strokeSnapshot: Snap?
    private var lastImg: CGPoint?
    private var multi = false
    private var prevPair: (CGPoint, CGPoint)?
    private var prevSingle: CGPoint?

    init(image: UIImage, initialPolygons: [[[Int]]], originalArea: Double?,
         tissueFrac: [String: Double], mmPerPx: Double?, resume: EditRaster?,
         wbGains: [Double]?, boundaryOnly: Bool = false) {
        self.boundaryOnly = boundaryOnly
        let cgImg = image.cgImage
        self.cg = cgImg
        self.bw = cgImg?.width ?? 1
        self.bh = cgImg?.height ?? 1
        self.wbGains = wbGains
        self.originalArea = originalArea
        // 比例最高且 >0 的那一類；全 0 退回肉芽。只有 auto 算不出來時才用它——降級，不是預設。
        let cand: [(String, Int)] = [("granulation", 1), ("slough", 2), ("necrosis", 3), ("epithelial", 4)]
        self.defaultClass = cand.max { (tissueFrac[$0.0] ?? 0) < (tissueFrac[$1.0] ?? 0) }
            .flatMap { (tissueFrac[$0.0] ?? 0) > 0 ? $0.1 : nil } ?? 1

        let initPolys = initialPolygons.filter { $0.count >= 3 }

        if let r = resume, r.canvasW == bw, r.canvasH == bh {
            // 同影像續編：原樣載回上次遮罩（零損耗）。
            let s = RasterState(rx0: r.rx0, ry0: r.ry0, mw: r.mw, mh: r.mh,
                                mScale: r.mScale, bw: bw, bh: bh)
            s.mask = r.mask; s.tissue = r.tissue; s.orig = r.origMask
            // 續編時 auto 不隨 EditRaster 保存，開畫面後由 runSeed() 背景重算
            //（原本在這裡同步算＝進畫面白凍的根因）。
            s.cm2PerPx = mmPerPx.map { ($0 * $0 / 100.0) / (r.mScale * r.mScale) } ?? r.cm2PerPx
            s.recount(); s.syncAll()
            self.freshSeed = false
            self.st = s
        } else {
            // 初始 ROI＝AI 遮罩外框＋60% 邊距（AI 低估時仍可自動擴張，不受限）。
            // 外框要涵蓋**所有**輪廓，否則第二個傷口一開始就在框外。
            let xs = initPolys.flatMap { $0 }.map { $0.count > 0 ? $0[0] : 0 }
            let ys = initPolys.flatMap { $0 }.map { $0.count > 1 ? $0[1] : 0 }
            let hasPoly = !initPolys.isEmpty && !xs.isEmpty
            let w = hasPoly ? max(16, xs.max()! - xs.min()!) : bw
            let h = hasPoly ? max(16, ys.max()! - ys.min()!) : bh
            let mgx = max(48, Int((Double(w) * 0.6).rounded()))
            let mgy = max(48, Int((Double(h) * 0.6).rounded()))
            let x0 = hasPoly ? max(0, xs.min()! - mgx) : 0
            let y0 = hasPoly ? max(0, ys.min()! - mgy) : 0
            let x1 = hasPoly ? min(bw - 1, xs.max()! + mgx) : bw - 1
            let y1 = hasPoly ? min(bh - 1, ys.max()! + mgy) : bh - 1
            let rw = x1 - x0 + 1, rh = y1 - y0 + 1
            let sc = min(1.0, 1024.0 / Double(max(rw, rh)))
            let s = RasterState(rx0: Double(x0), ry0: Double(y0),
                                mw: max(8, Int((Double(rw) * sc).rounded())),
                                mh: max(8, Int((Double(rh) * sc).rounded())),
                                mScale: sc, bw: bw, bh: bh)
            // 每個輪廓各填一次。只填最大的那一個，第二個傷口就沒有初始遮罩。
            for p in initPolys {
                MaskTrace.scanlineFill(p, s: sc, mw: s.mw, mh: s.mh,
                                       into: &s.mask, ox: s.rx0, oy: s.ry0)
            }
            // 底稿（auto）改由 runSeed() 背景計算；這裡先以預設類種 tissue，
            // 算完後在 runSeed 內用 auto 覆寫——期間畫布鎖定，語意與舊版同步計算一致。
            var c = 0
            for i in 0..<(s.mw * s.mh) where s.mask[i] != 0 {
                c += 1
                s.tissue[i] = UInt8(defaultClass)
            }
            s.maskCount = c
            s.orig = s.mask
            // 係數優先序：ArUco 尺度直傳（精確）＞ AI 面積/像素數（後備）。首次鎖定，之後不重算。
            s.cm2PerPx = mmPerPx.map { ($0 * $0 / 100.0) / (sc * sc) }
                ?? (originalArea != nil && c > 0 ? originalArea! / Double(c) : nil)
            s.recount(); s.syncAll()
            self.freshSeed = true
            self.st = s
        }
        // 有影像才需要底稿；設了這個旗標，畫布觸控與「完成修邊」會鎖到 runSeed 完成。
        // 民眾版（boundaryOnly）不算底稿：組織分類對它無意義，開畫面即可用。
        self.seeding = (cgImg != nil) && !boundaryOnly
    }

    /**
     進畫面後補算組織底稿。`computeAutoGrid` 丟背景緒（純值進出、不碰 `RasterState`），
     算完回主緒套用：全新編修同時以 auto 種 tissue（與舊版 init 內的種子邏輯逐位一致）；
     續編只更新 auto 供「自動筆」使用。期間 `seeding=true` 鎖畫布。
     */
    func runSeed() async {
        guard seeding else { return }
        guard let src = cg else { seeding = false; return }
        let s = st
        let (rx0, ry0, mw, mh, mScale) = (s.rx0, s.ry0, s.mw, s.mh, s.mScale)
        let mask = s.mask
        let gains = wbGains
        let auto = await Task.detached(priority: .userInitiated) {
            computeAutoGrid(rx0: rx0, ry0: ry0, mw: mw, mh: mh, mScale: mScale,
                            mask: mask, src: src, wbGains: gains)
        }.value
        defer { seeding = false; bump() }
        guard let auto, auto.count == mw * mh else { return }
        s.auto = auto
        if freshSeed {
            for i in 0..<(mw * mh) where s.mask[i] != 0 {
                let a = Int(auto[i])
                s.tissue[i] = UInt8((a >= 1 && a <= TissueCode.maxCode) ? a : defaultClass)
            }
            s.recount(); s.syncAll()
        }
    }

    func bump() {
        overlayCache = nil
        version += 1
    }

    func overlayImage() -> CGImage? {
        if overlayCacheVersion == version, let c = overlayCache { return c }
        let data = Data(st.overlay)
        guard let provider = CGDataProvider(data: data as CFData) else { return nil }
        let img = CGImage(width: st.mw, height: st.mh, bitsPerComponent: 8, bitsPerPixel: 32,
                          bytesPerRow: st.mw * 4, space: CGColorSpaceCreateDeviceRGB(),
                          bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.last.rawValue),
                          provider: provider, decode: nil, shouldInterpolate: false,
                          intent: .defaultIntent)
        overlayCache = img
        overlayCacheVersion = version
        return img
    }

    // MARK: 視圖座標

    func base() -> CGFloat {
        guard boxSize != .zero else { return 1 }
        return min(boxSize.width / CGFloat(bw), boxSize.height / CGFloat(bh))
    }
    func k() -> CGFloat { return base() * viewScale }

    func fitFull() {
        viewScale = 1
        let kk = k()
        viewOffset = CGPoint(x: (CGFloat(bw) - boxSize.width / kk) / 2,
                             y: (CGFloat(bh) - boxSize.height / kk) / 2)
    }
    func fitRoi() {
        guard let bb = st.maskBBoxImg(), boxSize != .zero else { return }
        let w = max(bb[2] - bb[0], 8), h = max(bb[3] - bb[1], 8)
        let kT = 0.5 * min(boxSize.width / CGFloat(w), boxSize.height / CGFloat(h))
        viewScale = min(24, max(0.5, kT / base()))
        let kk = k()
        viewOffset = CGPoint(x: CGFloat(bb[0] + bb[2]) / 2 - boxSize.width / (2 * kk),
                             y: CGFloat(bb[1] + bb[3]) / 2 - boxSize.height / (2 * kk))
    }
    func zoomBy(_ f: CGFloat) {
        guard boxSize != .zero else { return }
        let c = CGPoint(x: boxSize.width / 2, y: boxSize.height / 2)
        let kOld = k()
        let ci = CGPoint(x: viewOffset.x + c.x / kOld, y: viewOffset.y + c.y / kOld)
        viewScale = min(24, max(0.5, viewScale * f))
        let kNew = k()
        viewOffset = CGPoint(x: ci.x - c.x / kNew, y: ci.y - c.y / kNew)
    }
    func ensureInitialFit() {
        if !viewInit, boxSize != .zero {
            fitRoi()
            if st.maskBBoxImg() == nil { fitFull() }
            viewInit = true
        }
    }

    // MARK: undo / redo（位元組預算）

    /**
     undo 深度由**位元組預算**決定，不固定 8 筆。每筆快照＝mask＋tissue 兩份 `mw*mh`；
     2200² 時單筆 9.7 MB，固定 8 筆就是 77 MB——大面積傷口正是最需要修邊的情境，
     會當在最不該當的時候。固定 24 MB 預算＝小傷口 undo 很深、大傷口較淺但絕不 OOM；
     下限 2（只剩 1 步可復原太難用）、上限 8（小遮罩不無限成長）。
     */
    private let undoBudgetBytes = 24 * 1024 * 1024
    private func maxUndoDepth() -> Int {
        let perSnap = st.mw * st.mh * 2
        if perSnap <= 0 { return 8 }
        return min(8, max(2, undoBudgetBytes / perSnap))
    }
    private func push(_ stack: inout [Snap], _ s: Snap) {
        stack.append(s)
        let cap = maxUndoDepth()
        while stack.count > cap { stack.removeFirst() }
    }
    /// 新的一筆編輯：進 undo，並讓 redo 失效（分岔後的未來已不成立）。
    private func pushUndo(_ s: Snap) {
        push(&undoStack, s)
        redoStack.removeAll()
        undoCount = undoStack.count; redoCount = 0
    }
    func snap() -> Snap {
        return Snap(m: st.mask, t: st.tissue, mw: st.mw, mh: st.mh, rx0: st.rx0, ry0: st.ry0)
    }
    @discardableResult
    private func restore(_ s: Snap) -> Bool {
        guard s.mw == st.mw, s.mh == st.mh else { return false }   // 擴張後尺寸不同 → 無法還原
        st.mask = s.m; st.tissue = s.t
        st.recount(); st.syncAll()
        return true
    }
    func undo() {
        guard let s = undoStack.popLast() else { return }
        let cur = snap()
        if restore(s) { push(&redoStack, cur) } else { undoStack.append(s) }
        undoCount = undoStack.count; redoCount = redoStack.count
        bump()
    }
    func redo() {
        guard let s = redoStack.popLast() else { return }
        let cur = snap()
        // 不能走 pushUndo：它會清掉 redo，等於按一次「重做」就再也重做不了下一步。
        if restore(s) { push(&undoStack, cur) } else { redoStack.append(s) }
        undoCount = undoStack.count; redoCount = redoStack.count
        bump()
    }

    // MARK: 塗抹

    private func stamp(_ imgPt: CGPoint) {
        let rM = max(1, Double(brushScreen / k()) * st.mScale)
        var cx = (Double(imgPt.x) - st.rx0) * st.mScale
        var cy = (Double(imgPt.y) - st.ry0) * st.mScale
        if tool == .bPaint || tool == .bErase || tool == .tissue {
            if st.expandIfNeeded(cxM: cx, cyM: cy, rM: rM) {
                // 視窗擴張（內容無損）；undo 尺寸失效 → 清空。
                undoStack.removeAll(); redoStack.removeAll()
                undoCount = 0; redoCount = 0
                strokeSnapshot = nil
                // seedAuto **只有 B_PAINT 需要**（新遮罩像素要帶分類）。它在 2200² 上是
                // 近千萬次寫入，跑在筆畫中的主執行緒——B_ERASE／組織🖌 不跑，
                // 才不會「擴張時卡一下、手指帶過頭、邊界突然過標」。
                if tool == .bPaint, !boundaryOnly, let cg {
                    seedAutoSync(st, src: cg, wbGains: wbGains)
                }
                cx = (Double(imgPt.x) - st.rx0) * st.mScale
                cy = (Double(imgPt.y) - st.ry0) * st.mScale
            }
        }
        let r2 = rM * rM
        let x0 = max(0, Int(cx - rM)), x1 = min(st.mw - 1, Int(cx + rM))
        let y0 = max(0, Int(cy - rM)), y1 = min(st.mh - 1, Int(cy + rM))
        guard x0 <= x1, y0 <= y1 else { return }
        for y in y0...y1 {
            for x in x0...x1 {
                let dx = Double(x) - cx, dy = Double(y) - cy
                if dx * dx + dy * dy > r2 { continue }
                let i = y * st.mw + x
                switch tool {
                case .bPaint:
                    if st.mask[i] == 0 {
                        st.mask[i] = 1; st.maskCount += 1
                        // 新像素帶分類器建議，不是預設類別——否則醫師每往外補一筆，
                        // GT 裡就多一塊「其實沒人判斷過」的組織。
                        let a = Int(st.auto[i])
                        let nc = (a >= 1 && a <= TissueCode.maxCode) ? a : defaultClass
                        st.tissue[i] = UInt8(nc); st.tCounts[nc] += 1
                        if st.tissue[i] != st.auto[i] { st.editedCount += 1 }
                    }
                case .bErase:
                    if st.mask[i] != 0 {
                        st.mask[i] = 0; st.maskCount -= 1
                        let tc = Int(st.tissue[i])
                        if tc >= 1 && tc <= TissueCode.maxCode { st.tCounts[tc] -= 1 }
                        if st.tissue[i] != st.auto[i] { st.editedCount -= 1 }
                        st.tissue[i] = 0
                    }
                case .tissue:
                    if st.mask[i] != 0, Int(st.tissue[i]) != curTissue {
                        let tc = Int(st.tissue[i])
                        if tc >= 1 && tc <= TissueCode.maxCode { st.tCounts[tc] -= 1 }
                        let wasEdited = st.tissue[i] != st.auto[i]
                        st.tissue[i] = UInt8(curTissue); st.tCounts[curTissue] += 1
                        let isEdited = st.tissue[i] != st.auto[i]
                        if isEdited && !wasEdited { st.editedCount += 1 }
                        if !isEdited && wasEdited { st.editedCount -= 1 }
                    }
                case .pan:
                    break
                }
            }
        }
        st.refresh(x0 - 1, y0 - 1, x1 + 1, y1 + 1)
        bump()
    }

    /**
     兩點之間補間塗抹。⚠ **段落過長時只點終點，不補間**：一段遠超過筆刷直徑的位移
     幾乎不可能是使用者真的想畫的那一筆——它代表中間掉了影格（最常見：遮罩擴張時的重算）。
     補成一條線就是「邊界突然過標，還要再修回來」。標註工具裡**少畫可以補，多畫要靠 undo**。
     */
    private func stampLine(_ a: CGPoint, _ b: CGPoint) {
        let dx = b.x - a.x, dy = b.y - a.y
        let len = (dx * dx + dy * dy).squareRoot()
        let brushImg = brushScreen / k()
        if len > brushImg * 6 { stamp(b); return }
        let stepPx = max(1, brushImg * 0.5)
        let n = max(1, Int(len / stepPx))
        for i in 0...n {
            let t = CGFloat(i) / CGFloat(n)
            stamp(CGPoint(x: a.x + dx * t, y: a.y + dy * t))
        }
    }

    // MARK: 觸控分流（對齊 Android 的單迴圈依觸點數分流）

    private var canPaint: Bool { return tool != .pan && !peeking }

    private func toImage(_ p: CGPoint) -> CGPoint {
        let kk = k()
        return CGPoint(x: p.x / kk + viewOffset.x, y: p.y / kk + viewOffset.y)
    }

    func touchesChanged(_ phase: UITouch.Phase, _ pts: [CGPoint]) {
        switch phase {
        case .began:
            if pts.count == 1 {
                multi = false
                prevPair = nil
                prevSingle = pts[0]
                cursor = pts[0]
                if canPaint {
                    strokeSnapshot = snap()
                    let p0 = toImage(pts[0])
                    stamp(p0); lastImg = p0
                } else {
                    strokeSnapshot = nil; lastImg = nil
                }
            } else if pts.count >= 2 {
                enterMulti(pts)
            }
        case .moved:
            if pts.count >= 2 {
                if !multi { enterMulti(pts) } else { pinch(pts) }
            } else if pts.count == 1, !multi {
                let p = pts[0]
                cursor = p
                if !canPaint {
                    // 平移只動 @Published 的 viewOffset，自然觸發重繪；
                    // **不可** bump()——那會讓 4–19MB 的覆蓋圖 CGImage 每一影格重建一次。
                    if let prev = prevSingle {
                        let kk = k()
                        viewOffset = CGPoint(x: viewOffset.x - (p.x - prev.x) / kk,
                                             y: viewOffset.y - (p.y - prev.y) / kk)
                    }
                } else {
                    let cur = toImage(p)
                    if let last = lastImg { stampLine(last, cur) } else { stamp(cur) }
                    lastImg = cur
                }
                prevSingle = p
            }
        case .ended, .cancelled:
            if pts.isEmpty {
                if let s = strokeSnapshot, s.mw == st.mw, s.mh == st.mh { pushUndo(s) }
                strokeSnapshot = nil; lastImg = nil
                multi = false; prevPair = nil; prevSingle = nil
                cursor = nil          // @Published，自帶重繪；覆蓋圖內容沒變，不 bump
            } else if pts.count == 1 {
                // 雙指抬起一指：本輪已進 multi，不回頭作畫（與 Android 相同）。
                prevPair = nil
                prevSingle = pts[0]
            } else {
                prevPair = (pts[0], pts[1])
            }
        default:
            break
        }
    }

    private func enterMulti(_ pts: [CGPoint]) {
        multi = true
        // 第一指落下時已經畫了一筆。使用者的意圖是縮放不是畫圖——用既有快照把那一筆還原，
        // 否則每次縮放都會在傷口上留一個點，而那個點會直接進 GT。
        if let s = strokeSnapshot, s.mw == st.mw, s.mh == st.mh, restore(s) {
            bump()   // 只有真的還原了內容才失效覆蓋圖快取
        }
        strokeSnapshot = nil; lastImg = nil; cursor = nil
        prevPair = pts.count >= 2 ? (pts[0], pts[1]) : nil
    }

    private func pinch(_ pts: [CGPoint]) {
        guard pts.count >= 2 else { return }
        let cur = (pts[0], pts[1])
        guard let prev = prevPair else { prevPair = cur; return }
        func mid(_ a: CGPoint, _ b: CGPoint) -> CGPoint { return CGPoint(x: (a.x + b.x) / 2, y: (a.y + b.y) / 2) }
        func dist(_ a: CGPoint, _ b: CGPoint) -> CGFloat {
            let dx = a.x - b.x, dy = a.y - b.y
            return max(1, (dx * dx + dy * dy).squareRoot())
        }
        let c = mid(cur.0, cur.1)
        let pc = mid(prev.0, prev.1)
        let zoom = dist(cur.0, cur.1) / dist(prev.0, prev.1)
        let pan = CGPoint(x: c.x - pc.x, y: c.y - pc.y)
        // 以雙指中心為錨點縮放：螢幕點 p 對應影像點 p/k + off，
        // 要讓錨點下的影像位置不動 → off' = ci - c/k' - pan/k'。
        let kOld = k()
        let ci = CGPoint(x: c.x / kOld + viewOffset.x, y: c.y / kOld + viewOffset.y)
        if zoom != 1 { viewScale = min(24, max(0.5, viewScale * zoom)) }
        let kNew = k()
        viewOffset = CGPoint(x: ci.x - c.x / kNew - pan.x / kNew,
                             y: ci.y - c.y / kNew - pan.y / kNew)
        prevPair = cur   // viewScale/viewOffset 是 @Published，自帶重繪，不 bump
    }

    // MARK: 即時數值

    func liveFrac() -> [String: Double] {
        let tot = Double(max(1, st.maskCount))
        return [
            "granulation": Double(st.tCounts[1]) / tot,
            "slough":      Double(st.tCounts[2]) / tot,
            "necrosis":    Double(st.tCounts[3]) / tot,
            "epithelial":  Double(st.tCounts[4]) / tot,
            // ⚠ 不可寫死 0。分類器本來就會產生「其他」，抹掉它等於訓練資料永遠學不到
            // 「這塊我也不知道是什麼」——而那正是最該讓醫師覆核的部分。
            "other":       Double(st.tCounts[5]) / tot
        ]
    }
    var liveArea: Double? {
        return st.cm2PerPx.map { $0 * Double(st.maskCount) } ?? originalArea
    }

    // MARK: 完成

    func finish() -> (poly: [[Int]], all: [[[Int]]], iou: Double, area: Double?,
                      frac: [String: Double], raster: EditRaster)? {
        // 追**所有**連通元件。同一肢體多處傷口是臨床常態，
        // 只取最大的那一個等於把第二個傷口標成背景。
        let boundaries = MaskTrace.traceAllBoundaries(st.mask, mw: st.mw, mh: st.mh)
        guard !boundaries.isEmpty else { return nil }
        let polys: [[[Int]]] = boundaries.map { b in
            MaskTrace.rdp(b, eps: 1.5).map {
                [Int(($0[0] / st.mScale + st.rx0).rounded()),
                 Int(($0[1] / st.mScale + st.ry0).rounded())]
            }
        }.filter { $0.count >= 3 }
        guard let poly = polys.first else { return nil }
        var inter = 0, uni = 0
        for i in 0..<(st.mw * st.mh) {
            let a = st.orig[i] != 0, b = st.mask[i] != 0
            if a || b { uni += 1 }
            if a && b { inter += 1 }
        }
        let iou = uni == 0 ? 1.0 : Double(inter) / Double(uni)
        let raster = EditRaster(mask: st.mask, tissue: st.tissue, origMask: st.orig,
                                rx0: st.rx0, ry0: st.ry0, mw: st.mw, mh: st.mh,
                                mScale: st.mScale, cm2PerPx: st.cm2PerPx,
                                tissueEditedPx: st.editedCount, maskPx: st.maskCount,
                                canvasW: bw, canvasH: bh,
                                // 白平衡增益跟著存：下次從時間軸回頭修邊沒有後端回應，
                                // 底稿要靠它才能重建成與這次相同的分區。
                                wbGains: wbGains)
        // ⚠ 沒動過邊界就**不要動那個數字**。由多邊形重建柵格是有損的（RDP＋重新填充＋
        // ROI 改變），每進出一次面積漂移約 0.5%。醫師什麼都沒改卻看到面積變了，
        // 那個數字就失去意義——會自己緩慢變動的臨床數值比明顯的錯誤更難察覺。
        let areaOut = (iou >= 0.9999 && originalArea != nil) ? originalArea : liveArea
        return (poly, polys, iou, areaOut, liveFrac(), raster)
    }
}

// MARK: - 原始觸控轉接

private final class TouchProxyUIView: UIView {
    var onTouches: ((UITouch.Phase, [CGPoint]) -> Void)?

    override init(frame: CGRect) {
        super.init(frame: frame)
        isMultipleTouchEnabled = true
        backgroundColor = .clear
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) 不支援") }

    private func active(_ event: UIEvent?) -> [CGPoint] {
        guard let all = event?.allTouches else { return [] }
        return all.filter { $0.phase != .ended && $0.phase != .cancelled }
            .map { $0.location(in: self) }
    }
    override func touchesBegan(_ touches: Set<UITouch>, with event: UIEvent?) {
        onTouches?(.began, active(event))
    }
    override func touchesMoved(_ touches: Set<UITouch>, with event: UIEvent?) {
        onTouches?(.moved, active(event))
    }
    override func touchesEnded(_ touches: Set<UITouch>, with event: UIEvent?) {
        onTouches?(.ended, active(event))
    }
    override func touchesCancelled(_ touches: Set<UITouch>, with event: UIEvent?) {
        onTouches?(.cancelled, active(event))
    }
}

private struct TouchProxy: UIViewRepresentable {
    let handler: (UITouch.Phase, [CGPoint]) -> Void
    func makeUIView(context: Context) -> TouchProxyUIView {
        let v = TouchProxyUIView()
        v.onTouches = handler
        return v
    }
    func updateUIView(_ uiView: TouchProxyUIView, context: Context) {
        uiView.onTouches = handler
    }
}

// MARK: - 畫面

struct WoundEditView: View {
    let image: UIImage
    let initialPolygons: [[[Int]]]
    let originalArea: Double?
    let tissueFrac: [String: Double]
    let exudate: Int?
    let mmPerPx: Double?
    let resume: EditRaster?
    let wbGains: [Double]?
    /// true＝民眾版（WoundLite）借用模式：**只圈邊界**。隱藏組織筆刷／PUSH／GT 等
    /// 醫療概念與文案，保留雙指縮放平移、筆刷、undo、亮青邊界線——兩個 App 的
    /// 畫布操作因此完全一致（2026-08-18 使用者要求）。
    let boundaryOnly: Bool
    let onCancel: () -> Void
    let onDone: (_ edited: [[Int]], _ all: [[[Int]]], _ correctionIou: Double?,
                 _ newArea: Double?, _ tissue: [String: Double], _ raster: EditRaster) -> Void

    @StateObject private var m: EditCanvasModel

    init(image: UIImage, initialPolygons: [[[Int]]], originalArea: Double?,
         tissueFrac: [String: Double], exudate: Int?, mmPerPx: Double?,
         resume: EditRaster?, wbGains: [Double]?,
         boundaryOnly: Bool = false,
         onCancel: @escaping () -> Void,
         onDone: @escaping (_ edited: [[Int]], _ all: [[[Int]]], _ correctionIou: Double?,
                            _ newArea: Double?, _ tissue: [String: Double],
                            _ raster: EditRaster) -> Void) {
        self.image = image
        self.initialPolygons = initialPolygons
        self.originalArea = originalArea
        self.tissueFrac = tissueFrac
        self.exudate = exudate
        self.mmPerPx = mmPerPx
        self.resume = resume
        self.wbGains = wbGains
        self.boundaryOnly = boundaryOnly
        self.onCancel = onCancel
        self.onDone = onDone
        _m = StateObject(wrappedValue: EditCanvasModel(
            image: image, initialPolygons: initialPolygons, originalArea: originalArea,
            tissueFrac: tissueFrac, mmPerPx: mmPerPx, resume: resume, wbGains: wbGains,
            boundaryOnly: boundaryOnly))
    }

    var body: some View {
        let _ = m.version   // 柵格內容變更靠 version 驅動重繪
        let lf = m.liveFrac()
        let liveArea = m.liveArea
        let livePush = WoundPipeline.push(cm2: liveArea, frac: lf, exudate: exudate).partial

        VStack(alignment: .leading, spacing: 6) {
            if boundaryOnly {
                liteHeader
            } else {
                header(lf: lf, liveArea: liveArea, livePush: livePush)
            }

            if m.seeding {
                HStack(spacing: 6) {
                    ProgressView()
                    Text("組織底稿計算中…（約數秒，完成前畫布暫時鎖定）")
                        .font(.caption).foregroundStyle(.orange)
                }
            }

            GeometryReader { geo in
                ZStack {
                    canvasLayer
                    // 底稿沒好前吞掉觸控：塗抹會被 runSeed 的 tissue 覆寫掉，鎖住最誠實。
                    TouchProxy { phase, pts in
                        if !m.seeding { m.touchesChanged(phase, pts) }
                    }
                }
                .onAppear { m.boxSize = geo.size; m.ensureInitialFit() }
                .onChange(of: geo.size) { _, s in m.boxSize = s; m.ensureInitialFit() }
            }
            .clipped()
            .background(Color.black.opacity(0.05))
            .frame(maxWidth: .infinity, maxHeight: .infinity)

            toolRow
            if !boundaryOnly { tissueRow }
            brushRow
            zoomRow
            bottomRow
        }
        .padding(10)
        .task { await m.runSeed() }
    }

    // MARK: 區塊

    /// 民眾版標頭：白話操作說明，不出現面積/PUSH/GT/ArUco 等醫療與技術詞彙。
    @ViewBuilder
    private var liteHeader: some View {
        Text("圈選傷口範圍").font(.subheadline).bold()
        Text("單指塗抹・雙指縮放與移動・畫錯用「擦除」或 ↺ 復原。亮青色線＝目前圈選的邊界。")
            .font(.caption).foregroundStyle(.secondary)
        if m.st.maskCount == 0 {
            Text("請把整個傷口塗滿（不必沿邊描線，整片塗滿即可）")
                .font(.caption).foregroundStyle(.red)
        }
    }

    @ViewBuilder
    private func header(lf: [String: Double], liveArea: Double?, livePush: Int?) -> some View {
        // 字串先在 String 層組好，Text 只吃一個引數——混插值的多段 `+` 會讓
        // 型別檢查器組合爆炸（同 ResultCard 的教訓，錯誤還指不出是哪一行）。
        let pctI = { (v: Double?) -> Int in Int(((v ?? 0) * 100)) }
        let titleLine: String = {
            var s = "修邊(=GT)  面積 \(liveArea.map { String(format: "%.2f", $0) } ?? "-") cm²"
            s += " · PUSH \(livePush.map(String.init) ?? "-")"
            s += "  [尺度:\(mmPerPx != nil ? "ArUco✓" : "AI後備⚠")]"
            return s
        }()
        let tissueLine: String = {
            var s = "組織  肉芽\(pctI(lf["granulation"]))%"
            s += " · 腐肉\(pctI(lf["slough"]))%"
            s += " · 壞死\(pctI(lf["necrosis"]))%"
            s += " · 上皮\(pctI(lf["epithelial"]))%"
            s += " · 其他\(pctI(lf["other"]))%  (框會隨筆刷自動擴張)"
            return s
        }()
        Text(titleLine)
            .font(.subheadline).foregroundStyle(.blue)
        Text(tissueLine)
            .font(.caption).foregroundStyle(.secondary)
        // 「其他」偏高時要說出來：它代表**分類器不知道那是什麼**（肌腱／異物／血水／反光），
        // 正是最需要醫師看一眼的部分。硬歸到四類之一，訓練資料就會學到錯的東西。
        if (lf["other"] ?? 0) > 0.10 {
            let s = "ℹ 有 \(pctI(lf["other"]))% 判不出類別（肌腱／異物／血水／遮蔽物／反光）。"
                  + "請用「組織🖌 → 其他」確認範圍，或改標成正確的組織——這一塊會照原樣進訓練集。"
            Text(s).font(.caption).foregroundStyle(.orange)
        }
        // 不動筆刷不是錯，只是這一筆不會成為組織訓練樣本——醫師有權說「AI 分得對」，
        // 但要讓他知道這個選擇的意義（未修正的遮罩是 AI 自己的輸出，拿去訓練是自我確認）。
        if m.st.maskCount > 0, m.st.editedCount == 0 {
            Text("ℹ 尚未修正任何組織分區。面積與邊界照常送出；但組織遮罩會標記為「未經醫師修正」，**不會進入組織分割訓練集**。")
                .font(.caption).foregroundStyle(.secondary)
        }
        if m.st.maskCount == 0 {
            Text("⚠ AI 未偵測到傷口：請用「邊界＋」從零塗抹；ArUco 尺度仍有效，面積照常精確計算")
                .font(.caption).foregroundStyle(.red)
        }
    }

    private var canvasLayer: some View {
        Canvas { ctx, _ in
            let kk = m.k()
            guard kk > 0 else { return }
            if let cg = m.cg {
                let dst = CGRect(x: -m.viewOffset.x * kk, y: -m.viewOffset.y * kk,
                                 width: CGFloat(m.bw) * kk, height: CGFloat(m.bh) * kk)
                ctx.draw(Image(decorative: cg, scale: 1), in: dst)
            }
            if let ov = m.overlayImage() {
                let ox = (CGFloat(m.st.rx0) - m.viewOffset.x) * kk
                let oy = (CGFloat(m.st.ry0) - m.viewOffset.y) * kk
                let ow = CGFloat(Double(m.st.mw) / m.st.mScale) * kk
                let oh = CGFloat(Double(m.st.mh) / m.st.mScale) * kk
                let rect = CGRect(x: ox, y: oy, width: ow, height: oh)
                ctx.draw(Image(decorative: ov, scale: 1), in: rect)
                ctx.stroke(Path(rect), with: .color(Color.gray.opacity(0.27)), lineWidth: 2)
            }
            if let cur = m.cursor {
                let col: Color = {
                    switch m.tool {
                    case .bErase: return Color(red: 1, green: 0.31, blue: 0.31)
                    case .tissue: return EditPalette.color(m.curTissue, opaque: true)
                    default: return Color(red: 0.21, green: 0.78, blue: 0.35)
                    }
                }()
                let r = m.brushScreen
                ctx.stroke(Path(ellipseIn: CGRect(x: cur.x - r, y: cur.y - r,
                                                  width: r * 2, height: r * 2)),
                           with: .color(col.opacity(0.9)), lineWidth: 3)
            }
        }
    }

    private var toolRow: some View {
        HStack(spacing: 6) {
            chip(boundaryOnly ? "圈選＋" : "邊界＋", selected: m.tool == .bPaint) { m.tool = .bPaint }
            chip(boundaryOnly ? "擦除" : "邊界－", selected: m.tool == .bErase) { m.tool = .bErase }
            chip("移動", selected: m.tool == .pan) { m.tool = .pan }
            if !boundaryOnly {
                chip("組織🖌", selected: m.tool == .tissue) { m.tool = .tissue }
            }
            // 「按住才隱藏，放開就回來」：切換式有三個實測問題（版面跳動、關著仍可塗、
            // 要按兩次）；peek 期間手指壓在鈕上本來就碰不到畫布，放開立刻回到組織圖層。
            Text(m.peeking ? "原圖🚫" : "按住看原圖")
                .font(.footnote)
                .frame(maxWidth: .infinity, minHeight: 34)
                .background(RoundedRectangle(cornerRadius: 8)
                    .fill(m.peeking ? Color.blue.opacity(0.25) : Color.secondary.opacity(0.15)))
                .gesture(
                    DragGesture(minimumDistance: 0)
                        .onChanged { _ in if !m.peeking { setPeek(true) } }
                        .onEnded { _ in setPeek(false) }
                )
        }
    }

    private func setPeek(_ on: Bool) {
        m.peeking = on
        m.st.showTissue = !on
        m.st.syncAll()
        m.bump()
    }

    /// ⚠ 這一列**永遠顯示**，非組織筆刷時只是停用。條件顯示會讓畫布高度隨工具切換
    /// 改變 → base() 變 → 整張影像縮放跳動，醫師對照的位置跟著跑掉。版面高度必須恆定。
    private var tissueRow: some View {
        HStack(spacing: 6) {
            ForEach(1...TissueCode.maxCode, id: \.self) { c in
                let enabled = !m.peeking && m.tool == .tissue
                let sel = m.curTissue == c
                Button { m.curTissue = c } label: {
                    Text(TissueCode.editNames[c])
                        .font(.footnote)
                        .lineLimit(1)
                        .frame(maxWidth: .infinity, minHeight: 34)
                }
                .buttonStyle(.plain)
                // 色塊永遠實心可辨（實機回報：α115 再乘 0.4 淡到像空心按鈕）。
                // 未選＝實色 0.65、停用＝0.35、選中＝0.95＋粗框；顏色本身就是「塗下去的樣子」。
                .background(RoundedRectangle(cornerRadius: 8)
                    .fill(EditPalette.color(c, opaque: true)
                        .opacity(sel ? 0.95 : (enabled ? 0.65 : 0.35))))
                .overlay(RoundedRectangle(cornerRadius: 8)
                    .stroke(sel ? Color.primary : Color.clear, lineWidth: 2))
                .disabled(!enabled)
            }
        }
    }

    private var brushRow: some View {
        HStack(spacing: 6) {
            Text("筆刷").font(.caption)
            Slider(value: $m.brushScreen, in: 6...48)
            Text("\(Int(m.brushScreen))").font(.caption).frame(width: 26)
        }
    }

    private var zoomRow: some View {
        HStack(spacing: 6) {
            smallBtn("－") { m.zoomBy(1 / 1.3) }
            smallBtn("＋") { m.zoomBy(1.3) }
            smallBtn("ROI") { m.fitRoi() }
            smallBtn("全圖") { m.fitFull() }
            smallBtn("↺", enabled: m.undoCount > 0) { m.undo() }
            smallBtn("↩", enabled: m.redoCount > 0) { m.redo() }
        }
    }

    private var bottomRow: some View {
        HStack(spacing: 10) {
            Button("取消") { onCancel() }
                .buttonStyle(.bordered)
                .frame(maxWidth: .infinity)
            Button(boundaryOnly ? "完成圈選" : "完成修邊") {
                if let r = m.finish() {
                    onDone(r.poly, r.all, r.iou, r.area, r.frac, r.raster)
                }
            }
            .buttonStyle(.borderedProminent)
            .frame(maxWidth: .infinity)
            // seeding 中不可完成：此刻 tissue 還是預設類佔位，送出去就是假 GT。
            .disabled(m.st.maskCount == 0 || m.seeding)
        }
    }

    // MARK: 小元件

    private func chip(_ label: String, selected: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(label)
                .font(.footnote)
                .frame(maxWidth: .infinity, minHeight: 34)
        }
        .buttonStyle(.plain)
        .background(RoundedRectangle(cornerRadius: 8)
            .fill(selected ? Color.blue.opacity(0.25) : Color.secondary.opacity(0.15)))
    }

    private func smallBtn(_ label: String, enabled: Bool = true,
                          action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(label).font(.footnote).frame(maxWidth: .infinity, minHeight: 30)
        }
        .buttonStyle(.bordered)
        .disabled(!enabled)
    }
}
