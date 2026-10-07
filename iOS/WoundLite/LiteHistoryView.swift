import SwiftUI
import Charts

/**
 本地紀錄＋同一傷口的面積變化；量測變化不能單獨判定癒合。
 */
struct LiteHistoryView: View {
    @ObservedObject var store: LiteStore
    @State private var selected: LiteRecord?
    @State private var woundFilter = ""
    @State private var choseInitialWound = false
    private let ungrouped = "__ungrouped__"

    private var visibleRecords: [LiteRecord] {
        if woundFilter.isEmpty { return store.records }
        if woundFilter == ungrouped { return store.records.filter { $0.woundID?.isEmpty != false } }
        return LiteGrouping.records(for: woundFilter, in: store.records)
    }
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
                        Text(store.needsReload ? "紀錄暫時無法讀取" : "還沒有量測紀錄").font(.headline)
                        if let error = store.storageError { Text(error).font(.footnote).foregroundStyle(.orange) }
                        if store.needsReload {
                            Button("重新讀取紀錄") { store.reload() }
                        }
                        Text(store.needsReload ? "原資料未被覆寫。" : "到「量測」拍下第一筆，同一傷口的面積紀錄會分開追蹤。")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                } else {
                    List {
                        if let error = store.storageError { Text(error).foregroundStyle(.orange) }
                        if store.needsReload { Button("重新讀取紀錄") { store.reload() } }
                        Section {
                            Picker("傷口", selection: $woundFilter) {
                                Text("全部紀錄").tag("")
                                Text("未分組").tag(ungrouped)
                                ForEach(store.wounds) { wound in
                                    Text(wound.displayName).tag(wound.id)
                                }
                            }
                        } header: {
                            Text("選擇傷口")
                        }
                        Section("傷口表面積變化趨勢（cm²）") {
                            if !woundFilter.isEmpty, woundFilter != ungrouped {
                                LiteTrendChart(woundID: woundFilter, records: store.records)
                                Text("只比較同一處傷口。拍攝角度、距離與圈選差異都會影響數值；變化不等於癒合判定。")
                                    .font(.caption).foregroundStyle(.secondary)
                            } else {
                                Text("請在上方選擇一處傷口查看圖表；全部紀錄與未分組資料不合併計算趨勢。")
                                    .font(.footnote).foregroundStyle(.secondary)
                            }
                        }
                        Section("量測紀錄（點選查看・左滑刪除）") {
                            ForEach(visibleRecords) { r in
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
            .onAppear {
                if !choseInitialWound, !store.records.isEmpty {
                    woundFilter = LiteGrouping.mostRecentWoundID(store.records) ?? ""
                    choseInitialWound = true
                }
            }
            .onChange(of: store.wounds) { _, wounds in
                if !woundFilter.isEmpty, woundFilter != ungrouped,
                   !wounds.contains(where: { $0.id == woundFilter }) { woundFilter = "" }
            }
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
                Text("刪除此裝置的紀錄及對應影像、深度資料。已上傳的研究資料不會因此撤回。")
            }
        }
    }

    private func row(_ r: LiteRecord) -> some View {
        HStack(spacing: 10) {
            LiteThumb(name: r.imageName, store: store)
            VStack(alignment: .leading, spacing: 2) {
                Text(String(format: "%.2f cm²", r.surfaceCm2)).font(.headline)
                Text([r.woundName ?? "未分組", r.woundLocation?.label ?? "待補位置"].joined(separator: "・")).font(.caption).foregroundStyle(.secondary)
                Text(Self.pretty(r.dateISO) + (r.source == "cloud" ? "・雲端辨識" : "・手動圈選"))
                    .font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            if let pct = deltaPct(r) {
                Text(String(format: "%+.0f%%", pct))
                    .font(.caption).bold()
                    .foregroundStyle(.secondary)
            }
            if r.quality != "ok" {
                Text("⚠").font(.caption)
            }
        }
    }

    /// 與同一傷口更早一筆相比；不以紅綠色推論癒合。
    private func deltaPct(_ r: LiteRecord) -> Double? {
        LiteGrouping.changePercent(r, in: store.records)
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
 · `ImageLoadQueue` → 跨列序列化解密，取消的列不再啟動解碼
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
            img = nil
            guard !name.isEmpty else { return }
            let result = await ImageLoadQueue.shared.thumbnail(store: store.images, name: name, maxPixel: 160)
            guard !Task.isCancelled else { return }
            img = result.image
        }
    }
}

/**
 單筆詳情：原影像＋輪廓重繪、完整量測數值；有深度側檔的紀錄可
 「修正圈選・重新量測」（同醫療版複核頁的角色，畫布同一套 boundaryOnly）。

 以 `recordId` 對 store 即時查值而不是快照傳入——重新量測更新後，畫面跟著變。
 */
struct LiteRecordDetailView: View {
    @ObservedObject private var withdrawal = LiteWithdrawalModel.shared
    let recordId: String
    @ObservedObject var store: LiteStore
    @Environment(\.dismiss) private var dismiss
    @State private var image: UIImage?
    @State private var polys: [[[Int]]] = []
    @State private var editing = false
    @State private var busy = false
    @State private var note: String?
    @State private var assigningWound: LiteWoundGroup?
    @State private var assignmentSide = ""
    @State private var assignmentSite = ""

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
                        if let binding = r.cloudSync?.binding,
                           let status = LiteWithdrawalJournal.standard.status(for: binding) {
                            Text(status).font(.footnote).foregroundStyle(.secondary)
                        } else {
                            if let binding = r.cloudSync?.binding,
                               let status = LiteStorageReceipt.message(imageStored: true, receipt: binding.storageReceipt) {
                                Text(status).font(.footnote).foregroundStyle(.secondary)
                            }
                            Text(r.cloudSyncDescription).font(.footnote).foregroundStyle(.secondary)
                        }
                        if r.cloudSync != nil, (r.manuallyConfirmed == true || (r.manuallyConfirmed == false && r.source == "cloud")) {
                            Button(store.syncingRecordIDs.contains(recordId) ? "同步確認中…" : "檢查並同步修改") {
                                Task { await store.syncRevision(recordID: recordId) }
                            }
                            .disabled(busy || store.syncingRecordIDs.contains(recordId) || withdrawal.blocksUploads || LitePrefs.researchConsent != true)
                            if LitePrefs.researchConsent != true {
                                Text("研究上傳已關閉，修改只存於此裝置。").font(.caption)
                            }
                        }
                        if busy {
                            HStack(spacing: 8) { ProgressView(); Text("重新計算中…").font(.footnote) }
                        }
                        if let n = note {
                            Text(n).font(.footnote)
                                .padding(10).frame(maxWidth: .infinity, alignment: .leading)
                                .background(Color.orange.opacity(0.15)).cornerRadius(8)
                        }
                        if r.depthName != nil, image != nil {
                            Button("修正圈選・重新量測") { editing = true }
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
        .sheet(item: $assigningWound) { wound in
            NavigationStack {
                Form {
                    Text(wound.name)
                    LiteLocationFields(side: $assignmentSide, site: $assignmentSite)
                    Text("此位置會用於同一傷口的紀錄。不同傷口即使位置相同，仍應分開建檔。")
                        .font(.caption).foregroundStyle(.secondary)
                    if let error = store.storageError { Text(error).foregroundStyle(.orange) }
                    Button("儲存位置與分組") {
                        guard let r = record else { return }
                        var completed = wound
                        completed.location = LiteWoundLocation(side: assignmentSide, site: assignmentSite)
                        if store.assign(r, to: completed) {
                            assigningWound = nil
                            Task { await store.syncRevision(recordID: recordId) }
                        }
                    }
                    .disabled(!LiteWoundLocation(side: assignmentSide, site: assignmentSite).isComplete)
                }
                .navigationTitle("傷口位置")
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button("取消") { assigningWound = nil } } }
            }
        }
        .fullScreenCover(isPresented: $editing) {
            if let img = image {
                WoundEditView(image: img, initialPolygons: polys, originalArea: nil,
                              tissueFrac: [:], exudate: nil, mmPerPx: nil, resume: nil,
                              wbGains: nil, boundaryOnly: true,
                              onCancel: { editing = false },
                              onDone: { _, all, iou, _, _, _ in
                                  editing = false
                                  applyEdited(all, correctionIoU: iou)
                              })
            }
        }
    }

    private func assign(_ record: LiteRecord, to wound: LiteWoundGroup) {
        if wound.location?.isComplete == true {
            if store.assign(record, to: wound) {
                Task { await store.syncRevision(recordID: recordId) }
            }
        } else {
            assignmentSide = ""
            assignmentSite = ""
            assigningWound = wound
        }
    }

    private func detailCard(_ r: LiteRecord) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            if let error = store.storageError { Text(error).font(.footnote).foregroundStyle(.orange) }
            Menu {
                ForEach(store.wounds) { wound in
                    Button(wound.displayName) { assign(r, to: wound) }
                }
                Button("新增一處傷口") { assign(r, to: store.newWound()) }
            } label: {
                Label(r.woundName ?? "未分組・指定傷口", systemImage: "folder")
            }
            .disabled(busy)
            Text(r.woundLocation?.label ?? "位置尚未填寫，可由上方分組選單補齊。")
                .font(.footnote).foregroundStyle(.secondary)
            Text(String(format: "傷口面積：%.2f cm²", r.surfaceCm2)).font(.title3).bold()
            Text("（LiDAR 輔助表面積估算）").font(.caption).foregroundStyle(.secondary)
            Text(String(format: "投影面積 %.2f cm²（平面對照）", r.projectedCm2)).font(.footnote)
            if let v = r.volumeMl, let md = r.maxDepthMm {
                Text(String(format: "估算參考：容積約 %.2f mL・最深約 %.1f mm（相對周邊皮膚擬合面）", v, md)).font(.footnote)
            }
            NavigationLink("精確度與限制") { LiteMeasurementInfoView() }
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
        let loaded = await ImageLoadQueue.shared.fullImage(store: s, name: n)
        guard !Task.isCancelled else { return }
        image = loaded
    }

    /// 修正圈選完成：讀回深度側檔 → 背景重算 → 品質過閘才覆寫紀錄。
    private func applyEdited(_ all: [[[Int]]], correctionIoU: Double?) {
        guard correctionIoU != 1 || polys.isEmpty else {
            note = "圈選範圍未變更，保留原量測與來源。"
            return
        }
        guard let original = record, let name = original.depthName, let img = image else { return }
        guard let d = store.loadDepth(name, matchingImage: original.imageName) else {
            note = "照片或深度資料不完整或不相符，未更新紀錄。請保留原紀錄並重新拍攝。"
            return
        }
        let iw = img.cgImage?.width ?? 1
        let ih = img.cgImage?.height ?? 1
        guard d.matchesImageAspect(width: iw, height: ih) else {
            note = "這筆舊紀錄的照片與深度方向不相符，無法可靠重新計算。原紀錄保留；請重新拍攝。"
            return
        }
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
                note = "修正圈選後品質不足，未更新紀錄。\(v.blocker ?? "")"
                return
            }
            guard var r = record, r.imageName == original.imageName, r.depthName == original.depthName else {
                note = "原紀錄已變更或移除，未覆寫。"
                return
            }
            r.manuallyConfirmed = true
            r.source = "manual"
            r.surfaceCm2 = est.surfaceAreaCm2
            r.projectedCm2 = est.projectedAreaCm2
            r.tiltDeg = est.tiltDeg
            r.volumeMl = est.volumeMl
            r.maxDepthMm = est.maxDepthMm
            r.quality = v.warnings.isEmpty ? "ok" : "注意"
            r.polysJson = (try? JSONEncoder().encode(picked))
                .flatMap { String(data: $0, encoding: .utf8) }
            guard store.update(r) else { note = store.storageError; return }
            polys = picked
            note = "✓ 已依新輪廓更新此筆本機紀錄。"
            await store.syncRevision(recordID: recordId)
        }
    }

    private static func pretty(_ iso: String) -> String {
        guard let d = ISO8601DateFormatter().date(from: iso) else { return iso }
        let f = DateFormatter()
        f.dateFormat = "M/d HH:mm"
        return f.string(from: d)
    }
}

/// 單一已指定傷口的表面積折線；水平軸為紀錄順序，不代表等距日期。
struct LiteTrendChart: View {
    let woundID: String
    let records: [LiteRecord]

    var body: some View {
        let points = LiteWoundTrend.points(woundID: woundID, records: records)
        VStack(alignment: .leading, spacing: 12) {
            if let latest = points.last {
                Text("最近一次：\(latest.area, specifier: "%.2f") cm²")
                    .font(.headline)
                Chart(points) { point in
                    LineMark(x: .value("拍攝時間", point.date),
                             y: .value("表面積", point.area))
                        .foregroundStyle(Color.blue)
                        .interpolationMethod(.linear)
                    PointMark(x: .value("拍攝時間", point.date),
                              y: .value("表面積", point.area))
                        .foregroundStyle(Color.blue)
                        .accessibilityLabel(point.date.formatted(date: .abbreviated, time: .shortened))
                        .accessibilityValue(String(format: "%.2f 平方公分", point.area))
                }
                .chartYScale(domain: 0...max(1, (points.map(\.area).max() ?? 0) * 1.15))
                .chartXAxis {
                    AxisMarks(values: .automatic(desiredCount: 3)) {
                        AxisGridLine()
                        AxisTick()
                        AxisValueLabel(format: .dateTime.month().day().hour().minute())
                    }
                }
                .chartXAxisLabel("拍攝時間")
                .chartYAxisLabel("cm²")
                .frame(height: 190)
                if points.count == 1 {
                    Text("目前只有一筆紀錄；新增同一傷口的紀錄後即可比較變化。")
                        .font(.caption).foregroundStyle(.secondary)
                }
            } else {
                Text("這處傷口尚無可繪製的有效量測紀錄。")
                    .font(.footnote).foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 8)
    }
}
