import SwiftUI
import UIKit

/**
 民眾版量測流程：拍照（LiDAR）→ 輪廓（雲端辨識或手動圈選）→ 深度幾何 → 結果。

 主數字＝**表面積**（傾角不變、曲面真實）；品質不足直接不給數字。
 所有重計算（影像正規化、深度估算）都在背景緒——醫療版 2026-08-18 的
 「畫面凍住 10 秒」教訓，這裡從第一天就不犯。
 */
@MainActor
final class LiteMeasureVM: ObservableObject {
    @Published var image: UIImage?
    @Published var polys: [[[Int]]] = []
    @Published var imageW = 0
    @Published var imageH = 0
    @Published var depth: DepthCapture?
    @Published var estimate: DepthAreaResult?
    @Published var busy = false
    @Published var busyHint = ""
    @Published var note: String?
    @Published var savedId: String?
    /// 輪廓來源（存進紀錄）："manual" / "cloud"
    @Published var source = "manual"
    private var gen = 0

    /// 長邊 ≤2048＋方向烘進像素（與醫療版 `normalizeForBackend` 同規則；
    /// 座標空間正確性的前提，詳見該處註解）。
    nonisolated static func normalize(_ img: UIImage, maxDim: CGFloat = 2048) -> UIImage {
        let pw = CGFloat(img.cgImage?.width ?? Int(img.size.width))
        let ph = CGFloat(img.cgImage?.height ?? Int(img.size.height))
        let needRedraw = img.imageOrientation != .up || max(pw, ph) > maxDim
        if !needRedraw { return img }
        let dw = img.size.width, dh = img.size.height
        let sc = min(1, maxDim / max(dw, dh))
        let target = CGSize(width: (dw * sc).rounded(), height: (dh * sc).rounded())
        let fmt = UIGraphicsImageRendererFormat()
        fmt.scale = 1
        return UIGraphicsImageRenderer(size: target, format: fmt).image { _ in
            img.draw(in: CGRect(origin: .zero, size: target))
        }
    }

    /// 拍照回來：正規化（背景）→ 依同意分流輪廓來源。
    /// - Returns: true＝已有輪廓；false＝需要開手動圈選。
    func ingest(_ raw: UIImage, depth d: DepthCapture?) async -> Bool {
        gen += 1
        busy = true
        busyHint = "影像處理中…"
        defer { busy = false }
        savedId = nil
        note = nil
        estimate = nil
        polys = []
        let work = await Task.detached(priority: .userInitiated) {
            Self.normalize(raw)
        }.value
        image = work
        imageW = work.cgImage?.width ?? Int(work.size.width)
        imageH = work.cgImage?.height ?? Int(work.size.height)
        var dd = d
        dd?.rgbWidth = imageW
        dd?.rgbHeight = imageH
        depth = dd
        if d == nil {
            note = "⚠ 這張照片沒有取得 LiDAR 深度，無法量測。請重拍（勿遮擋鏡頭旁的 LiDAR 感測器）。"
            return true   // 沒深度就不必進圈選——圈了也算不出來
        }

        // 輪廓來源優先序（2026-08-18 規劃）：
        //   ① 地端模型（成熟上線後把 .mlmodel 丟進 WoundLite/Models/ 即自動啟用，離線可用）
        //   ② 雲端辨識（研究同意＋連線設定）
        //   ③ 手動圈選（永遠可用的保底，也是①②結果的微調入口）
        if LiteLocalSeg.available {
            busyHint = "本地辨識中…"
            if let local = await LiteLocalSeg.segment(work) {
                let picked = Self.centerWound(local, w: imageW, h: imageH)
                if !picked.isEmpty {
                    polys = picked
                    source = "local"
                    note = "已由裝置端模型自動圈選（未連網）。可用「重新圈選」微調。"
                    await runEstimate()
                    return true
                }
            }
        }
        if LitePrefs.researchConsent == true {
            busyHint = "雲端辨識中…（約數秒）"
            switch await liteCloudSegment(work, depth: dd) {
            case .ok(let cloud, let stored):
                let picked = Self.centerWound(cloud, w: imageW, h: imageH)
                if !picked.isEmpty {
                    polys = picked
                    source = "cloud"
                    var lines: [String] = []
                    if cloud.count > 1 {
                        lines.append("已自動取畫面中央的傷口（偵測到 \(cloud.count) 處，其餘忽略）。可用「重新圈選」微調。")
                    }
                    // 據實告知有沒有上傳——同意分流講給人聽才有意義（後端 stored 欄位）。
                    if stored { lines.append("去識別影像與深度資料已上傳供研究（可於設定撤回未來上傳）。") }
                    note = lines.isEmpty ? nil : lines.joined(separator: "\n")
                    await runEstimate()
                    return true
                }
                // 空輪廓時後端照樣落地（同意者）——難例正是研究最需要的樣本，
                // 而「有沒有上傳」必須據實告知，不因辨識失敗而略過。
                note = stored
                    ? "雲端未辨識到傷口，請手動圈選。去識別資料已上傳供研究（正是改進辨識所需的難例）。"
                    : "雲端未辨識到傷口，請手動圈選。"
                return false
            case .softFail(let message):
                // 429 配額／人臉退件：不是錯誤，是流程分流。訊息照後端的說法給。
                note = message
                return false
            case .hardFail:
                note = "雲端辨識未成功（連線或服務問題），請手動圈選傷口。"
                return false
            }
        }
        return false   // 未同意研究 → 一律手動，資料不離機
    }

    /// 手動圈選完成。
    func applyManual(_ traced: [[[Int]]]) async {
        let picked = Self.centerWound(traced, w: imageW, h: imageH)
        polys = picked
        source = "manual"
        if traced.count > 1, picked.count == 1 {
            note = "圈選含多個分離區塊，已取最靠近畫面中央的一塊。"
        }
        await runEstimate()
    }

    /// 單一中心傷口：取質心最靠近畫面中央的輪廓（民眾版產品決策 2026-08-18）。
    nonisolated static func centerWound(_ all: [[[Int]]], w: Int, h: Int) -> [[[Int]]] {
        let valid = all.filter { $0.count >= 3 }
        guard valid.count > 1 else { return valid }
        let cx = Double(w) / 2, cy = Double(h) / 2
        let best = valid.min { a, b in
            func d2(_ p: [[Int]]) -> Double {
                let mx = Double(p.reduce(0) { $0 + $1[0] }) / Double(p.count)
                let my = Double(p.reduce(0) { $0 + $1[1] }) / Double(p.count)
                return (mx - cx) * (mx - cx) + (my - cy) * (my - cy)
            }
            return d2(a) < d2(b)
        }
        return best.map { [$0] } ?? []
    }

    private func runEstimate() async {
        guard let d = depth, !polys.isEmpty, imageW > 0 else { estimate = nil; return }
        gen += 1
        let g = gen
        busy = true
        busyHint = "深度幾何計算中…"
        defer { busy = false }
        let p = polys, iw = imageW, ih = imageH
        let est = await Task.detached(priority: .userInitiated) {
            DepthAreaEstimator.estimate(polygons: p, depth: d, imageW: iw, imageH: ih)
        }.value
        if gen == g { estimate = est }
    }

    enum LiteCloudOutcome {
        case ok([[[Int]]], stored: Bool)
        case softFail(String)     // 429 配額／人臉退件：後端給的可讀訊息
        case hardFail
    }

    /**
     匿名辨識（`/api/v1/lite/segment`，2026-08-19 切換；免帳號免登入）。

     同意研究時一併附上深度研究資料：png16_mm 深度圖＋置信度＋**深度圖像素空間**
     內參（與醫療版 annotation 同一 wire format、後端同一驗證器）。
     編碼是 MB 級工作，照例移出主執行緒。
     */
    private func liteCloudSegment(_ work: UIImage, depth d: DepthCapture?) async -> LiteCloudOutcome {
        let payload: (jpeg: Data, dep: String?, conf: String?, k: [String: Double]?)? =
            await Task.detached(priority: .userInitiated) {
                guard let jpeg = work.jpegData(compressionQuality: 0.92) else { return nil }
                guard let d, let enc = DepthAreaEstimator.encodePng16mm(d) else {
                    return (jpeg, nil, nil, nil)
                }
                return (jpeg,
                        enc.depthPng.base64EncodedString(),
                        enc.confPng.base64EncodedString(),
                        DepthAreaEstimator.intrinsicsForUpload(d))
            }.value
        guard let payload else { return .hardFail }
        let c = BackendClient(baseUrl: AppSettings.backendURL())
        do {
            let r = try await c.liteSegment(jpeg: payload.jpeg,
                                            anonId: LitePrefs.anonId,
                                            depthMapPngBase64: payload.dep,
                                            depthConfPngBase64: payload.conf,
                                            cameraIntrinsics: payload.k)
            if let m = r.userMessage { return .softFail(m) }
            // 以後端回覆的座標空間為準（它處理的那張圖才是輪廓所在的空間）。
            if r.imageW > 0 { imageW = r.imageW }
            if r.imageH > 0 { imageH = r.imageH }
            depth?.rgbWidth = imageW
            depth?.rgbHeight = imageH
            return r.polygons.isEmpty ? .ok([], stored: r.stored)
                                      : .ok(r.polygons, stored: r.stored)
        } catch {
            return .hardFail
        }
    }
}

// MARK: - 品質判定（民眾版比醫療版更嚴：不合格就不給數字）

struct LiteVerdict {
    var usable: Bool
    var blocker: String?      // usable=false 的原因（請重拍文案）
    var warnings: [String]    // usable=true 時的注意事項
}

func liteVerdict(_ e: DepthAreaResult) -> LiteVerdict {
    let ratio = e.projectedAreaCm2 > 0 ? e.surfaceAreaCm2 / e.projectedAreaCm2 : 99
    if ratio > 1.25, (e.tiltDeg ?? 0) < 25 {
        return LiteVerdict(usable: false,
                           blocker: "深度品質不足（表面雜訊偏高）。請補光、避免反光與濕亮面，保持 25–40 公分重拍。",
                           warnings: [])
    }
    if e.medianDistanceM < 0.22 {
        return LiteVerdict(usable: false,
                           blocker: "拍攝距離太近（LiDAR 在 22 公分內不可靠）。請退後一些重拍。",
                           warnings: [])
    }
    if e.coverage < 0.5 {
        return LiteVerdict(usable: false,
                           blocker: "傷口區域的深度資料太少（可能反光或濕潤）。請調整光線與角度重拍。",
                           warnings: [])
    }
    var warns: [String] = []
    if let t = e.tiltDeg, t > 25 {
        warns.append("拍攝角度偏斜（約 \(Int(t))°），面積誤差會變大。建議正對傷口重拍。")
    } else if let t = e.tiltDeg, t > 10 {
        warns.append("拍攝略斜（約 \(Int(t))°）。表面積受角度影響小，但正對拍攝最準。")
    }
    if e.medianDistanceM > 0.5 {
        warns.append("距離偏遠（約 \(Int(e.medianDistanceM * 100)) 公分），建議 25–40 公分。")
    }
    if e.coverage < 0.7 {
        warns.append("部分區域缺深度資料（已補洞估算），數值可能略偏。")
    }
    if e.surfaceAreaCm2 < 1.0 {
        warns.append("傷口小於 1 cm²，相對誤差較大，數字僅供趨勢參考。")
    }
    return LiteVerdict(usable: true, blocker: nil, warnings: warns)
}

// MARK: - 量測頁

struct LiteMeasureView: View {
    @ObservedObject var store: LiteStore
    @StateObject private var vm = LiteMeasureVM()
    @State private var showCamera = false
    @State private var showTrace = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    Button {
                        showCamera = true
                    } label: {
                        Label("拍攝傷口", systemImage: "camera.fill")
                            .frame(maxWidth: .infinity).padding(.vertical, 10)
                    }
                    .buttonStyle(.borderedProminent)

                    Text("正對傷口、距離 25–40 公分，等中央對焦框變綠再拍。")
                        .font(.footnote).foregroundStyle(.secondary)

                    if vm.busy {
                        HStack(spacing: 8) { ProgressView(); Text(vm.busyHint).font(.footnote) }
                    }
                    if let n = vm.note {
                        Text(n).font(.footnote)
                            .padding(10).frame(maxWidth: .infinity, alignment: .leading)
                            .background(Color.orange.opacity(0.15)).cornerRadius(8)
                    }

                    if let img = vm.image {
                        LitePreview(image: img, polys: vm.polys,
                                    imageW: vm.imageW, imageH: vm.imageH)
                        if vm.depth != nil {
                            Button(vm.polys.isEmpty ? "圈選傷口" : "重新圈選") { showTrace = true }
                                .buttonStyle(.bordered)
                        }
                    }

                    if let e = vm.estimate {
                        LiteResultCard(e: e, verdict: liteVerdict(e))
                        saveRow(e: e)
                    }
                }
                .padding()
            }
            .navigationTitle("量測")
            .fullScreenCover(isPresented: $showCamera) {
                CameraCaptureView(
                    // 「只拍傷口」不只是構圖建議，是去識別化的一環：協定層去得掉 EXIF 與
                    // 檔名身分，去不掉畫面裡的臉、證件與環境——那只能靠拍攝當下不要入鏡。
                    hintText: "請正對傷口、距離 25–40 公分，框變綠再拍。畫面只拍傷口部位，"
                        + "避免臉部或可辨識個人的物品入鏡。",
                    onCapture: { img, dep in
                        showCamera = false
                        Task {
                            let done = await vm.ingest(img, depth: dep)
                            if !done { showTrace = true }
                        }
                    },
                    onCancel: { showCamera = false })
            }
            .fullScreenCover(isPresented: $showTrace) {
                if let img = vm.image {
                    // 借用醫療版修邊畫布的 boundaryOnly 模式（雙指縮放平移、筆刷、undo、
                    // 亮青邊界線；隱藏組織/PUSH 等醫療概念）——兩個 App 畫布操作一致。
                    // initialPolygons 預載目前輪廓：對雲端/上次結果做微調，而不是從零重畫。
                    WoundEditView(image: img,
                                  initialPolygons: vm.polys,
                                  originalArea: nil, tissueFrac: [:], exudate: nil,
                                  mmPerPx: nil, resume: nil, wbGains: nil,
                                  boundaryOnly: true,
                                  onCancel: { showTrace = false },
                                  onDone: { _, all, _, _, _, _ in
                                      showTrace = false
                                      Task { await vm.applyManual(all) }
                                  })
                }
            }
        }
    }

    @ViewBuilder
    private func saveRow(e: DepthAreaResult) -> some View {
        let v = liteVerdict(e)
        if v.usable {
            Button(vm.savedId == nil ? "存入紀錄" : "✓ 已存入紀錄") {
                guard vm.savedId == nil, let img = vm.image,
                      let jpeg = img.jpegData(compressionQuality: 0.85),
                      let name = store.images.save(jpeg: jpeg) else { return }
                // 輪廓與深度側檔一併落地：詳情頁「重新圈選・重新量測」靠它們
                // 把當時的幾何完整還原（只有影像沒有深度，就只能看不能重算）。
                let polysJson = (try? JSONEncoder().encode(vm.polys))
                    .flatMap { String(data: $0, encoding: .utf8) }
                let depthName = vm.depth.flatMap { store.saveDepth($0) }
                let rec = LiteRecord(
                    id: UUID().uuidString,
                    dateISO: ISO8601DateFormatter().string(from: Date()),
                    surfaceCm2: e.surfaceAreaCm2,
                    projectedCm2: e.projectedAreaCm2,
                    tiltDeg: e.tiltDeg,
                    volumeMl: e.volumeMl,
                    maxDepthMm: e.maxDepthMm,
                    quality: v.warnings.isEmpty ? "ok" : "注意",
                    imageName: name,
                    source: vm.source,
                    polysJson: polysJson,
                    depthName: depthName)
                store.add(rec)
                vm.savedId = rec.id
            }
            .buttonStyle(.bordered)
            .disabled(vm.savedId != nil)
        }
    }
}

// MARK: - 影像＋輪廓預覽

struct LitePreview: View {
    let image: UIImage
    let polys: [[[Int]]]
    let imageW: Int
    let imageH: Int

    var body: some View {
        GeometryReader { geo in
            let iw = CGFloat(max(imageW, 1)), ih = CGFloat(max(imageH, 1))
            let s = min(geo.size.width / iw, geo.size.height / ih)
            let ox = (geo.size.width - iw * s) / 2
            let oy = (geo.size.height - ih * s) / 2
            ZStack(alignment: .topLeading) {
                Image(uiImage: image)
                    .resizable().scaledToFit()
                    .frame(width: geo.size.width, height: geo.size.height)
                Path { p in
                    for poly in polys where poly.count >= 3 {
                        let pts = poly.map {
                            CGPoint(x: ox + CGFloat($0[0]) * s, y: oy + CGFloat($0[1]) * s)
                        }
                        p.move(to: pts[0]); p.addLines(pts); p.closeSubpath()
                    }
                }
                .stroke(Color.cyan, lineWidth: 2)
            }
        }
        .frame(height: 280)
        .background(Color.black.opacity(0.05))
        .cornerRadius(8)
    }
}

// MARK: - 結果卡（表面積為主）

struct LiteResultCard: View {
    let e: DepthAreaResult
    let verdict: LiteVerdict

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if verdict.usable {
                Text(String(format: "傷口面積：%.2f cm²", e.surfaceAreaCm2))
                    .font(.title3).bold()
                Text("（皮膚表面實際面積，拍攝角度改變數值不變）")
                    .font(.caption).foregroundStyle(.secondary)
                if let v = e.volumeMl, let md = e.maxDepthMm {
                    Text(String(format: "深度參考：容積約 %.2f mL・最深 %.1f mm", v, md))
                        .font(.footnote)
                }
                Text(String(format: "投影面積 %.2f cm²（平面對照用）・攝距 %.0f cm・深度覆蓋 %d%%",
                            e.projectedAreaCm2, e.medianDistanceM * 100,
                            Int(e.coverage * 100)))
                    .font(.caption2).foregroundStyle(.secondary)
                ForEach(verdict.warnings, id: \.self) { w in
                    Text("⚠ " + w).font(.footnote).foregroundStyle(.orange)
                }
            } else {
                Text("這次拍攝品質不足，未計算面積").font(.headline)
                if let b = verdict.blocker {
                    Text(b).font(.footnote).foregroundStyle(.orange)
                }
            }
            Text("健康參考工具，非醫療診斷。傷口惡化、發燒或大量滲液請就醫。")
                .font(.caption2).foregroundStyle(.secondary)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background((verdict.usable ? Color.blue : Color.orange).opacity(0.08))
        .cornerRadius(10)
    }
}
