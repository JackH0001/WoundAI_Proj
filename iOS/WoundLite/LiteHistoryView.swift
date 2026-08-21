import SwiftUI

/**
 本地紀錄＋癒合趨勢。「有沒有變好」是居家照護的核心問題——
 單點數字沒有意義，趨勢才有。
 */
struct LiteHistoryView: View {
    @ObservedObject var store: LiteStore
    @State private var selected: LiteRecord?
    /// 待確認刪除的那一筆。左滑的「刪除」只設定它、彈確認框——
    /// 單靠一個滑動手勢就永久毀掉影像＋深度資料，誤觸成本太高。
    @State private var pendingDelete: LiteRecord?

    var body: some View {
        NavigationStack {
            Group {
                if store.records.isEmpty {
                    VStack(spacing: 10) {
                        Image(systemName: "chart.line.uptrend.xyaxis")
                            .font(.system(size: 40)).foregroundStyle(.secondary)
                        Text("還沒有量測紀錄").font(.headline)
                        Text("到「量測」拍下第一筆，之後這裡會畫出癒合趨勢。")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                } else {
                    List {
                        Section {
                            LiteTrendChart(records: store.records)
                                .frame(height: 140)
                                .listRowInsets(EdgeInsets(top: 8, leading: 8, bottom: 8, trailing: 8))
                        } header: {
                            Text("表面積趨勢（cm²）")
                        }
                        Section("量測紀錄（點選查看・左滑刪除）") {
                            ForEach(store.records) { r in
                                Button { selected = r } label: { row(r) }
                                    .buttonStyle(.plain)
                                    .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                                        Button("刪除", role: .destructive) { pendingDelete = r }
                                    }
                            }
                        }
                    }
                }
            }
            .navigationTitle("紀錄")
            .sheet(item: $selected) { r in
                LiteRecordDetailView(recordId: r.id, store: store)
            }
            .confirmationDialog(
                "確定刪除這筆紀錄？",
                isPresented: Binding(get: { pendingDelete != nil },
                                     set: { if !$0 { pendingDelete = nil } }),
                titleVisibility: .visible
            ) {
                Button("刪除（無法復原）", role: .destructive) {
                    if let r = pendingDelete { store.delete(r) }
                    pendingDelete = nil
                }
                Button("取消", role: .cancel) { pendingDelete = nil }
            } message: {
                Text("影像與深度資料會一併刪除。")
            }
        }
    }

    private func row(_ r: LiteRecord) -> some View {
        HStack(spacing: 10) {
            LiteThumb(name: r.imageName, store: store)
            VStack(alignment: .leading, spacing: 2) {
                Text(String(format: "%.2f cm²", r.surfaceCm2)).font(.headline)
                Text(Self.pretty(r.dateISO) + (r.source == "cloud" ? "・雲端辨識" : "・手動圈選"))
                    .font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            if let pct = deltaPct(r) {
                Text(String(format: "%+.0f%%", pct))
                    .font(.caption).bold()
                    .foregroundStyle(pct <= 0 ? Color.green : Color.red)
            }
            if r.quality != "ok" {
                Text("⚠").font(.caption)
            }
        }
    }

    /// 與**前一筆**（時間上更早的下一列）相比的面積變化。負＝縮小＝變好。
    private func deltaPct(_ r: LiteRecord) -> Double? {
        guard let i = store.records.firstIndex(where: { $0.id == r.id }),
              i + 1 < store.records.count else { return nil }
        let prev = store.records[i + 1].surfaceCm2
        guard prev > 0 else { return nil }
        return (r.surfaceCm2 - prev) / prev * 100
    }

    private static func pretty(_ iso: String) -> String {
        guard let d = ISO8601DateFormatter().date(from: iso) else { return iso }
        let f = DateFormatter()
        f.dateFormat = "M/d HH:mm"
        return f.string(from: d)
    }
}

/**
 列表縮圖。**這不是最佳化，是修 bug。**

 2026-08-19 實機出現 `Terminated due to memory issue (code 9)`（jetsam）。
 主嫌就是這裡的前一版：直接在 SwiftUI 的 `body` 裡同步呼叫 `loadThumbnail`
 ——而 body 會被反覆求值（捲動、@Published 更新、sheet 開關各一次），
 於是每個可見列每次重繪都做一輪「AES-GCM 解密整張 JPEG → 建 CGImageSource
 → 解碼縮圖」。CPU 與**暫態記憶體**同時爆，捲幾列就被系統收掉。

 醫療版 `TimelineCharts` 早就是對的做法（`.task(id:)`＋背景緒＋@State），
 該處註解甚至寫著「清單捲幾列就 OOM」。這裡把 Lite 拉回同一套：

 · `.task(id:)` → 每列只在出現／換資料時載一次，不隨重繪重跑
 · `Task.detached` → 解密與解碼不佔主執行緒
 · `maxPixel: 160` → 46pt @3x ≈ 138px，載 256px 是白白多 4 倍像素
 */
private struct LiteThumb: View {
    let name: String
    let store: LiteStore
    @State private var img: UIImage?

    var body: some View {
        Group {
            if let img {
                Image(uiImage: img).resizable().scaledToFill()
            } else {
                RoundedRectangle(cornerRadius: 6).fill(Color.secondary.opacity(0.15))
            }
        }
        .frame(width: 46, height: 46)
        .clipped()
        .cornerRadius(6)
        .task(id: name) {
            guard !name.isEmpty, img == nil else { return }
            let s = store.images
            let n = name
            img = await Task.detached(priority: .utility) {
                s.loadThumbnail(n, maxPixel: 160)
            }.value
        }
    }
}

/**
 單筆詳情：原影像＋輪廓重繪、完整量測數值；有深度側檔的紀錄可
 「重新圈選・重新量測」（同醫療版複核頁的角色，畫布同一套 boundaryOnly）。

 以 `recordId` 對 store 即時查值而不是快照傳入——重新量測更新後，畫面跟著變。
 */
struct LiteRecordDetailView: View {
    let recordId: String
    @ObservedObject var store: LiteStore
    @Environment(\.dismiss) private var dismiss
    @State private var image: UIImage?
    @State private var polys: [[[Int]]] = []
    @State private var editing = false
    @State private var busy = false
    @State private var note: String?

    private var record: LiteRecord? { store.records.first { $0.id == recordId } }

    var body: some View {
        NavigationStack {
            ScrollView {
                if let r = record {
                    VStack(alignment: .leading, spacing: 12) {
                        if let img = image {
                            LitePreview(image: img, polys: polys,
                                        imageW: img.cgImage?.width ?? 1,
                                        imageH: img.cgImage?.height ?? 1)
                        }
                        detailCard(r)
                        if busy {
                            HStack(spacing: 8) { ProgressView(); Text("重新計算中…").font(.footnote) }
                        }
                        if let n = note {
                            Text(n).font(.footnote)
                                .padding(10).frame(maxWidth: .infinity, alignment: .leading)
                                .background(Color.orange.opacity(0.15)).cornerRadius(8)
                        }
                        if r.depthName != nil, image != nil {
                            Button("重新圈選・重新量測") { editing = true }
                                .buttonStyle(.bordered)
                                .disabled(busy)
                        } else {
                            Text("此筆沒有深度側檔（舊版儲存），僅供檢視，無法重新量測。")
                                .font(.caption2).foregroundStyle(.secondary)
                        }
                    }
                    .padding()
                } else {
                    Text("紀錄不存在").padding()
                }
            }
            .navigationTitle("量測紀錄")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("關閉") { dismiss() } }
            }
        }
        .task { await load() }
        .fullScreenCover(isPresented: $editing) {
            if let img = image {
                WoundEditView(image: img, initialPolygons: polys, originalArea: nil,
                              tissueFrac: [:], exudate: nil, mmPerPx: nil, resume: nil,
                              wbGains: nil, boundaryOnly: true,
                              onCancel: { editing = false },
                              onDone: { _, all, _, _, _, _ in
                                  editing = false
                                  applyEdited(all)
                              })
            }
        }
    }

    private func detailCard(_ r: LiteRecord) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(String(format: "傷口面積：%.2f cm²", r.surfaceCm2)).font(.title3).bold()
            Text("（皮膚表面實際面積）").font(.caption).foregroundStyle(.secondary)
            Text(String(format: "投影面積 %.2f cm²（平面對照）", r.projectedCm2)).font(.footnote)
            if let v = r.volumeMl, let md = r.maxDepthMm {
                Text(String(format: "深度參考：容積約 %.2f mL・最深 %.1f mm", v, md)).font(.footnote)
            }
            if let t = r.tiltDeg {
                Text(String(format: "拍攝傾角約 %.0f°", t)).font(.footnote)
            }
            Text("拍攝時間 \(Self.pretty(r.dateISO))・輪廓 "
                 + (r.source == "cloud" ? "雲端辨識" : r.source == "local" ? "地端模型" : "手動圈選")
                 + (r.quality == "ok" ? "" : "・⚠ 拍攝品質備註"))
                .font(.caption).foregroundStyle(.secondary)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.blue.opacity(0.08))
        .cornerRadius(10)
    }

    /// 詳情頁載圖。**必須非同步**：`loadFull` 是「解密整張 JPEG ＋ 全解析度解碼」，
    /// 在 `onAppear` 同步做會卡住開場動畫，且與縮圖同屬 jetsam 的記憶體來源。
    private func load() async {
        guard let r = record else { return }
        if let j = r.polysJson, let d = j.data(using: .utf8),
           let ps = try? JSONDecoder().decode([[[Int]]].self, from: d) {
            polys = ps
        }
        let s = store.images
        let n = r.imageName
        image = await Task.detached(priority: .userInitiated) { s.loadFull(n) }.value
    }

    /// 重新圈選完成：讀回深度側檔 → 背景重算 → 品質過閘才覆寫紀錄。
    private func applyEdited(_ all: [[[Int]]]) {
        guard var r = record, let name = r.depthName,
              let d = store.loadDepth(name), let img = image else { return }
        let iw = img.cgImage?.width ?? 1
        let ih = img.cgImage?.height ?? 1
        let picked = LiteMeasureVM.centerWound(all, w: iw, h: ih)
        guard !picked.isEmpty else { return }
        busy = true
        note = nil
        Task {
            let est = await Task.detached(priority: .userInitiated) {
                DepthAreaEstimator.estimate(polygons: picked, depth: d,
                                            imageW: iw, imageH: ih)
            }.value
            busy = false
            guard let est else { note = "重新計算失敗（深度資料不足）。"; return }
            let v = liteVerdict(est)
            guard v.usable else {
                note = "重新圈選後品質不足，未更新紀錄。\(v.blocker ?? "")"
                return
            }
            r.surfaceCm2 = est.surfaceAreaCm2
            r.projectedCm2 = est.projectedAreaCm2
            r.tiltDeg = est.tiltDeg
            r.volumeMl = est.volumeMl
            r.maxDepthMm = est.maxDepthMm
            r.quality = v.warnings.isEmpty ? "ok" : "注意"
            r.polysJson = (try? JSONEncoder().encode(picked))
                .flatMap { String(data: $0, encoding: .utf8) }
            store.update(r)
            polys = picked
            note = "✓ 已依新輪廓更新此筆紀錄。"
        }
    }

    private static func pretty(_ iso: String) -> String {
        guard let d = ISO8601DateFormatter().date(from: iso) else { return iso }
        let f = DateFormatter()
        f.dateFormat = "M/d HH:mm"
        return f.string(from: d)
    }
}

/// 極簡趨勢圖：時間升冪的表面積折線。列表百筆等級，SwiftUI Path 直畫即可。
struct LiteTrendChart: View {
    let records: [LiteRecord]

    var body: some View {
        // records 是新到舊；畫圖要舊到新。
        let pts = records.reversed().map { $0.surfaceCm2 }
        GeometryReader { geo in
            let w = geo.size.width, h = geo.size.height
            let maxV = max(pts.max() ?? 1, 0.01)
            let minV = min(pts.min() ?? 0, maxV * 0.9)
            let span = max(maxV - minV, 0.01)
            ZStack(alignment: .topLeading) {
                if pts.count >= 2 {
                    Path { p in
                        for (i, v) in pts.enumerated() {
                            let x = w * CGFloat(i) / CGFloat(pts.count - 1)
                            let y = h - h * CGFloat((v - minV) / span) * 0.9 - h * 0.05
                            if i == 0 { p.move(to: CGPoint(x: x, y: y)) }
                            else { p.addLine(to: CGPoint(x: x, y: y)) }
                        }
                    }
                    .stroke(Color.blue, lineWidth: 2)
                }
                ForEach(Array(pts.enumerated()), id: \.offset) { i, v in
                    let x = pts.count > 1 ? w * CGFloat(i) / CGFloat(pts.count - 1) : w / 2
                    let y = h - h * CGFloat((v - minV) / span) * 0.9 - h * 0.05
                    Circle().fill(Color.blue).frame(width: 5, height: 5)
                        .position(x: x, y: y)
                }
                Text(String(format: "%.1f", maxV))
                    .font(.caption2).foregroundStyle(.secondary)
            }
        }
    }
}
