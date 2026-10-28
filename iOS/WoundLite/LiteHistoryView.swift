import SwiftUI
import Charts

/**
 本地紀錄＋同一傷口的面積變化；量測變化不能單獨判定癒合。
 */
struct LiteHistoryView: View {
    @ObservedObject var store: LiteStore
    @Binding var recordToOpen: String?
    @State private var selected: LiteRecord?
    @State private var woundFilter = ""
    @State private var choseInitialWound = false
    private let ungrouped = "__ungrouped__"

    init(store: LiteStore, recordToOpen: Binding<String?> = .constant(nil)) {
        self.store = store
        self._recordToOpen = recordToOpen
    }

    private func openSavedRecord() {
        guard let id = recordToOpen,
              let record = store.records.first(where: { $0.id == id }) else { return }
        woundFilter = record.woundID ?? ""
        choseInitialWound = true
        selected = record
        recordToOpen = nil
    }

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
                        Section("LiDAR 表面積估算趨勢（cm²）") {
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
                openSavedRecord()
                if !choseInitialWound, !store.records.isEmpty {
                    woundFilter = LiteGrouping.mostRecentWoundID(store.records) ?? ""
                    choseInitialWound = true
                }
            }
            .onChange(of: recordToOpen) { _, _ in openSavedRecord() }
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
                Text(String(format: r.areaSeries == nil ? "面積紀錄 %.2f cm²" : "LiDAR %.2f cm²", r.surfaceCm2)).font(.headline)
                Text(r.areaSeries?.rawValue ?? "量測來源待核對").font(.caption2).foregroundStyle(.secondary)
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
/// Each presentation owns its baseline and accepts completion once. A dismissed editor cannot
/// overwrite the next session or replace a successful save with an old "unchanged" notice.
struct LiteContourEditFlow {
    struct Session: Identifiable {
        let id = UUID()
        let record: LiteRecord
        let polygons: [[[Int]]]
    }
    private(set) var session: Session?

    mutating func begin(record: LiteRecord, fallback: [[[Int]]]) {
        guard session == nil else { return }
        let polygons = record.polysJson?.data(using: .utf8)
            .flatMap { try? JSONDecoder().decode([[[Int]]].self, from: $0) } ?? fallback
        session = Session(record: record, polygons: polygons)
    }
    mutating func finish(_ id: UUID) -> Session? {
        guard let current = session, current.id == id else { return nil }
        session = nil
        return current
    }
    mutating func cancel() { session = nil }
}

struct LiteContourChangeSummary: Equatable {
    let before: Double
    let after: Double
    var comparable = true
    var text: String {
        let old = String(format: "%.2f", before), new = String(format: "%.2f", after)
        guard comparable else {
            return "圈選已修正 · LiDAR 表面積估算 \(new) cm²。原值來源不同，保留但不作前後比較。"
        }
        return old == new ? "圈選已修正；LiDAR 表面積估算仍顯示 \(new) cm²。"
            : "圈選已修正 · LiDAR 表面積估算 \(old) → \(new) cm²"
    }
}

struct LiteRecordDetailView: View {
    @ObservedObject private var withdrawal = LiteWithdrawalModel.shared
    let recordId: String
    @ObservedObject var store: LiteStore
    @Environment(\.dismiss) private var dismiss
    @State private var image: UIImage?
    @State private var polys: [[[Int]]] = []
    @State private var editFlow = LiteContourEditFlow()
    @State private var changeSummary: LiteContourChangeSummary?
    @State private var busy = false
    @State private var note: String?
    @State private var noteIsInformational = false
    @State private var assigningWound: LiteWoundGroup?
    @State private var assignmentSide = ""
    @State private var assignmentSite = ""
    @State private var pdfShare: LitePDFShare?
    @State private var pdfCopy: LitePDFShare?
    @State private var pdfError: String?
    @State private var pdfBusy = false
    @State private var imageLoadFinished = false

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
                        Button {
                            pdfError = nil; pdfBusy = true
                            // Freeze the saved record; export never edits or syncs it.
                            let snapshot = r, photo = image
                            Task { @MainActor in
                                await Task.yield()
                                defer { pdfBusy = false }
                                do {
                                    let copy = try LitePDFShare(data: LitePDFReport.render(record: snapshot, image: photo))
                                    pdfCopy = copy; pdfShare = copy
                                } catch { pdfError = "PDF 暫時無法產生；原照片、紀錄與同步狀態未變更。" }
                            }
                        } label: {
                            Label(pdfBusy ? "正在產生 PDF…" : "分享此筆 PDF 紀錄", systemImage: "square.and.arrow.up")
                        }
                        .buttonStyle(.bordered)
                        .disabled(busy || pdfBusy || !imageLoadFinished)
                        Text("只在本機製作，不重新辨識或扣額度。PDF 含照片與位置，分享前請確認接收對象。")
                            .font(.caption).foregroundStyle(.secondary)
                        if let pdfError { Text(pdfError).font(.footnote).foregroundStyle(.orange) }
                        DisclosureGroup("影像與深度保存詳情") {
                            if let binding = r.cloudSync?.binding,
                               let status = LiteWithdrawalJournal.standard.status(for: binding) {
                                Text(status).font(.footnote).foregroundStyle(.secondary)
                            } else if let binding = r.cloudSync?.binding,
                                      let status = LiteStorageReceipt.message(imageStored: true, receipt: binding.storageReceipt) {
                                Text(status).font(.footnote).foregroundStyle(.secondary)
                            }
                        }
                        if r.depthName == nil {
                            Text("此筆沒有可供重新量測的影像或深度資料，原紀錄保留。")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    .padding()
                } else {
                    Text("紀錄不存在").padding()
                }
            }
            .safeAreaInset(edge: .bottom, spacing: 0) {
                if let r = record {
                    LiteRevisionActionCard(
                        status: LiteRevisionPresentation.make(record: r,
                            syncing: store.syncingRecordIDs.contains(recordId),
                            consent: LitePrefs.researchConsent == true,
                            withdrawalBlocked: withdrawal.blocksUploads,
                            retiredResearch: r.cloudSync.map { !withdrawal.blocksUploads && !LiteWithdrawalJournal.standard.permits($0.binding) } ?? false),
                        calculating: busy || pdfBusy, change: changeSummary,
                        note: note, noteIsInformational: noteIsInformational,
                        canEdit: r.depthName != nil && image != nil,
                        syncAllowed: r.cloudSync.map { LiteWithdrawalJournal.standard.permits($0.binding) } == true && LitePrefs.researchConsent == true,
                        onEdit: {
                            note = nil; noteIsInformational = false; changeSummary = nil
                            editFlow.begin(record: r, fallback: polys)
                        },
                        onRetry: {
                            note = nil
                            Task { await store.syncRevision(recordID: recordId) }
                        })
                }
            }
            .navigationTitle("量測紀錄")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("關閉") { dismiss() } }
            }
        }
        .task { await load() }
        .sheet(item: $pdfShare, onDismiss: {
            pdfCopy?.remove(); pdfCopy = nil
        }) { item in
            LitePDFShareSheet(item: item) { pdfShare = nil }
        }
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
        .fullScreenCover(item: Binding(get: { editFlow.session }, set: { value in
            if value == nil { editFlow.cancel() }
        })) { session in
            if let img = image {
                WoundEditView(image: img, initialPolygons: session.polygons, originalArea: nil,
                              tissueFrac: [:], exudate: nil, mmPerPx: nil, resume: nil,
                              wbGains: nil, boundaryOnly: true,
                              onCancel: { _ = editFlow.finish(session.id) },
                              onDone: { _, all, iou, _, _, _ in
                                  guard let baseline = editFlow.finish(session.id) else { return }
                                  applyEdited(all, correctionIoU: iou, baseline: baseline)
                              })
                    .id(session.id)
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
            Text(String(format: r.areaSeries == nil ? "面積紀錄：%.2f cm²" : "LiDAR 表面積估算：%.2f cm²", r.surfaceCm2)).font(.title3).bold()
            Text(r.areaSeries?.rawValue ?? "量測來源待核對，不納入趨勢").font(.caption).foregroundStyle(.secondary)
            Text(String(format: r.areaSeries == nil ? "另一面積紀錄 %.2f cm²（來源待核對）" : "相機方向投影估算 %.2f cm²（不同於表面積）", r.projectedCm2)).font(.footnote)
            LiteVolumeReferenceView(volume: r.volumeMl, depth: r.maxDepthMm)
            if let context = r.measurementContext {
                ForEach(context.warnings, id: \.self) { Text("⚠ " + $0).font(.caption).foregroundStyle(.orange) }
                Text(context.isKnownSurface
                     ? "影像與深度通過資料對應檢查，不代表已完成實物精度驗證。"
                     : "這筆量測的來源版本目前無法核對，保留原值供檢視，不納入趨勢。")
                    .font(.caption).foregroundStyle(.secondary)
            } else {
                Text("舊版紀錄保留原數值；當時的完整品質資訊未記錄，不與本版量測連成同一條趨勢。")
                    .font(.caption).foregroundStyle(.secondary)
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
        imageLoadFinished = true
    }

    /// 修正圈選完成：讀回深度側檔 → 背景重算 → 品質過閘才覆寫紀錄。
    private func applyEdited(_ all: [[[Int]]], correctionIoU: Double?, baseline: LiteContourEditFlow.Session) {
        guard correctionIoU != 1 || baseline.polygons.isEmpty else {
            noteIsInformational = true
            note = "本次未新增圈選修改；原紀錄與同步狀態保持不變。"
            return
        }
        noteIsInformational = false
        guard let original = record, let name = original.depthName, let img = image,
              original.polysJson == baseline.record.polysJson,
              original.imageName == baseline.record.imageName,
              original.depthName == baseline.record.depthName else {
            note = "編輯期間原紀錄已變更，未覆寫。請關閉此頁後重新開啟核對。"
            return
        }
        guard let d = store.loadDepth(name, matchingImage: original.imageName) else {
            note = "照片或深度資料不完整或不相符，未更新紀錄。請保留原紀錄並重新拍攝。"
            return
        }
        let iw = img.cgImage?.width ?? 1
        let ih = img.cgImage?.height ?? 1
        guard d.geometryIssue(imageW: iw, imageH: ih, requireOrientation: true) == nil else {
            note = "這筆紀錄的影像、深度或方向資訊無法完整核對，未重新計算。原紀錄保留；新量測請重新拍攝。"
            return
        }
        let picked = LiteMeasureVM.centerWound(all, w: iw, h: ih)
        guard !picked.isEmpty else {
            note = "未取得有效圈選，原紀錄保留。請重新確認圈選範圍。"
            return
        }
        // Raster edits can be outside the selected component or disappear during contour
        // simplification. Do not claim a saved revision when the retained contour is identical.
        guard picked != baseline.polygons else {
            noteIsInformational = true
            note = "保留的圈選輪廓與原紀錄相同，未建立新修訂；原同步狀態保持不變。"
            return
        }
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
            guard var r = record, r.imageName == original.imageName, r.depthName == original.depthName,
                  r.polysJson == original.polysJson else {
                note = "原紀錄已變更或移除，未覆寫。"
                return
            }
            r.manuallyConfirmed = true
            r.measurementContext = LiteMeasurementContext(depth: d, result: est, warnings: v.warnings)
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
            changeSummary = LiteContourChangeSummary(before: original.surfaceCm2, after: r.surfaceCm2,
                comparable: original.areaSeries != nil && original.areaSeries == r.areaSeries)
            note = nil
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

/// Fixed beside the actions: local persistence and cloud acknowledgement are separate facts.
/// Acknowledged records have no upload button. Only unfinished work offers retry.
struct LiteRevisionActionCard: View {
    let status: LiteRevisionPresentation
    var calculating = false
    var change: LiteContourChangeSummary? = nil
    var note: String? = nil
    var noteIsInformational = false
    var canEdit = true
    var syncAllowed = true
    let onEdit: () -> Void
    let onRetry: () -> Void

    private var active: Bool { calculating || status.phase == .syncing }
    private var statusColor: Color {
        switch status.phase {
        case .synced: return .green
        case .failed, .unavailable: return .orange
        default: return .blue
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if calculating {
                HStack { ProgressView(); Text("正在依新圈選計算面積…").font(.headline) }
                Text("完成計算並保存後，會自動同步已同意上傳的修改。")
                    .font(.caption).foregroundStyle(.secondary)
            } else {
                Label("本機紀錄已保存", systemImage: "checkmark.circle.fill")
                    .font(.subheadline).bold()
                if let change {
                    Text(change.text).font(.subheadline).bold()
                        .accessibilityIdentifier("lite.revision.areaChange")
                }
                HStack(alignment: .top, spacing: 8) {
                    if status.phase == .syncing { ProgressView() }
                    else {
                        Image(systemName: status.phase == .synced ? "checkmark.icloud.fill" : "icloud")
                            .foregroundStyle(statusColor)
                    }
                    Text(status.title).font(.headline).foregroundStyle(statusColor)
                        .accessibilityIdentifier("lite.revision.status")
                }
                Text(status.detail).font(.caption).foregroundStyle(.secondary)
            }
            if let note {
                Text(note).font(.caption)
                    .foregroundStyle(noteIsInformational ? Color.secondary : Color.orange)
                    .accessibilityIdentifier("lite.revision.notice")
            }
            if !active, let retry = status.retryTitle {
                Button(action: onRetry) {
                    Label(retry, systemImage: "arrow.clockwise.icloud")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .accessibilityIdentifier("lite.revision.retry")
            }
            if canEdit {
                Button(action: onEdit) {
                    Label("修正圈選並更新紀錄", systemImage: "pencil.tip.crop.circle")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
                .disabled(active)
                .accessibilityIdentifier("lite.revision.edit")
                if !active {
                    Text(syncAllowed ? "完成圈選後自動保存並同步修改。" : "完成圈選後保存於此手機。")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.regularMaterial)
        .overlay(alignment: .top) { Divider() }
        .accessibilityElement(children: .contain)
    }
}

/// 單一已指定傷口的表面積折線；水平軸為紀錄順序，不代表等距日期。
struct LiteTrendChart: View {
    let woundID: String
    let records: [LiteRecord]
    @State private var selectedSeries: LiteAreaSeries?

    var body: some View {
        let available = LiteAreaSeries.allCases.filter { series in records.contains { $0.woundID == woundID && $0.areaSeries == series } }
        let series = selectedSeries.flatMap { available.contains($0) ? $0 : nil } ?? available.first
        let points = LiteWoundTrend.points(woundID: woundID, records: records, series: series)
        VStack(alignment: .leading, spacing: 12) {
            if available.count > 1 {
                Picker("比較的面積紀錄", selection: Binding(get: { series }, set: { selectedSeries = $0 })) {
                    ForEach(available) { Text($0.rawValue).tag(Optional($0)) }
                }
                Text("本版與舊版紀錄分開比較，原有數值保留。").font(.caption).foregroundStyle(.secondary)
            }
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
