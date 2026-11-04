import SwiftUI
import UIKit

enum LiteTimelineError: Error { case invalidSelection }

/// Freeze the selected wound and calendar interval when export is requested.
struct LiteTimelineSelection {
  let group: LiteWoundGroup
  let rows: [LiteRecord]
  let period: String
  let missingDates: Int
  private let calendar: Calendar
  private let datedRows: [String: Date]
  func date(of record: LiteRecord) -> Date? { datedRows[record.id] }

  func displayDate(_ iso: String) -> String {
    guard let date = LiteRecord.captureDate(iso) else {
      return "日期待確認"
    }
    let formatter = DateFormatter()
    formatter.calendar = calendar
    formatter.locale = Locale(identifier: "zh_Hant_TW")
    formatter.timeZone = calendar.timeZone
    formatter.dateFormat = "yyyy/MM/dd HH:mm"
    return formatter.string(from: date)
  }

  init(
    group: LiteWoundGroup, records: [LiteRecord], first: Date? = nil, last: Date? = nil,
    calendar: Calendar = .current
  ) throws {
    guard !group.id.isEmpty, (first == nil) == (last == nil) else {
      throw LiteTimelineError.invalidSelection
    }
    let dates = DateFormatter()
    dates.locale = Locale(identifier: "zh_Hant_TW")
    dates.calendar = calendar
    dates.timeZone = calendar.timeZone
    dates.dateFormat = "yyyy/MM/dd"
    var lower: Date?
    var upper: Date?
    if let first, let last {
      lower = calendar.startOfDay(for: first)
      upper = calendar.date(byAdding: .day, value: 1, to: calendar.startOfDay(for: last))
      guard let lower, let upper, lower < upper else { throw LiteTimelineError.invalidSelection }
      period = dates.string(from: first) + " 至 " + dates.string(from: last)
    } else {
      period = "全部日期"
    }
    self.group = group
    self.calendar = calendar
    let rowsInGroup = records.filter { $0.woundID == group.id }
    guard LiteGrouping.canCombine(rowsInGroup, account: group.account) else {
      throw LiteTimelineError.invalidSelection
    }
    let series = rowsInGroup.map {
      ($0, LiteRecord.captureDate($0.dateISO))
    }
    datedRows = Dictionary(series.compactMap { record, date in date.map { (record.id, $0) } },
                           uniquingKeysWith: { first, _ in first })
    missingDates = series.filter { $0.1 == nil }.count
    rows = series.filter { _, date in
      guard let lower, let upper else { return true }
      return date.map { $0 >= lower && $0 < upper } ?? false
    }.sorted {
      let left = $0.1 ?? Date.distantFuture
      let right = $1.1 ?? Date.distantFuture
      return left == right ? $0.0.id < $1.0.id : left < right
    }.map { $0.0 }
  }
}

enum LiteTimelinePDF {
  static func export(
    _ selection: LiteTimelineSelection, photos: Bool,
    image: (LiteRecord) -> UIImage?, directory: URL = LitePDFShare.directory
  ) throws -> LitePDFShare {
    guard !selection.rows.isEmpty else { throw LiteTimelineError.invalidSelection }
    let page = CGRect(x: 0, y: 0, width: 595.28, height: 841.89)
    let format = UIGraphicsPDFRendererFormat()
    format.documentInfo = [
      kCGPDFContextTitle as String: "WoundAILite 傷口時間軸觀察報告",
      kCGPDFContextCreator as String: "WoundAILite",
    ]
    let renderer = UIGraphicsPDFRenderer(bounds: page, format: format)
    // Keep only eligibility for the first-page chart. Full resolution photos are loaded one at a time.
    var eligible: [String: LiteAreaSeries] = [:]
    for record in selection.rows where record.areaSeries != nil {
      autoreleasepool {
        eligible[record.id] = LitePDFReport.areaSeries(record, polygons:
          LitePDFReport.polygons(record, image: image(record)))
      }
    }
    let firstCount = min(photos ? 1 : 2, selection.rows.count)
    let perPage = photos ? 1 : 3
    let photoIndex = photos && selection.rows.count > 1
    // Keep the printed photo frame identical across dates. The first photo
    // must not look smaller than later photos simply because it shares a chart.
    let ranges = (photoIndex ? [0..<0] : []) + [0..<firstCount] + stride(from: firstCount, to: selection.rows.count, by: perPage).map {
      $0..<min($0 + perPage, selection.rows.count)
    }
    let ink = UIColor(red: 0.08, green: 0.18, blue: 0.21, alpha: 1)
    func text(
      _ value: String, _ rect: CGRect, size: CGFloat, bold: Bool = false, color: UIColor? = nil
    ) {
      let style = NSMutableParagraphStyle()
      style.lineBreakMode = .byWordWrapping
      (value as NSString).draw(
        in: rect,
        withAttributes: [
          .font: bold ? UIFont.boldSystemFont(ofSize: size) : UIFont.systemFont(ofSize: size),
          .foregroundColor: color ?? ink, .paragraphStyle: style,
        ])
    }
    return try LitePDFShare(directory: directory) { output in
      var sourceChanged = false
      try renderer.writePDF(to: output) { context in
        for (number, range) in ranges.enumerated() {
          context.beginPage()
          UIColor.white.setFill()
          context.cgContext.fill(page)
          text("WoundAILite   /   個人觀察紀錄", CGRect(x: 36, y: photos ? 24 : 32, width: 510, height: 20), size: 12)
          text("傷口時間軸", CGRect(x: 36, y: photos ? 46 : 58, width: 520, height: 40), size: 28, bold: true)
          text(
            selection.group.displayName, CGRect(x: 36, y: photos ? 85 : 107, width: 525, height: 27), size: 17,
            bold: true)
          text(
            "\(selection.period)     ·     共 \(selection.rows.count) 筆",
            CGRect(x: 36, y: photos ? 111 : 140, width: 525, height: 22), size: 13)
          let stamp =
            selection.missingDates > 0
            ? "有 \(selection.missingDates) 筆日期待確認；自訂期間只列日期可核對的紀錄。" : "依拍攝時間排列，面積為個人紀錄參考值。"
          text(stamp, CGRect(x: 36, y: photos ? 136 : 168, width: 525, height: 24), size: 11)
          if number == 0 {
            context.cgContext.saveGState()
            if photos { context.cgContext.translateBy(x: 0, y: -36) }
            drawTrend(selection, eligible: eligible, ctx: context.cgContext)
            context.cgContext.restoreGState()
            if photoIndex {
              text("觀察紀錄索引", CGRect(x:36,y:356,width:523,height:26),size:17,bold:true)
              text("拍攝時間", CGRect(x:36,y:396,width:192,height:18),size:12)
              text("LiDAR 表面積估算", CGRect(x:255,y:396,width:184,height:18),size:12)
              text("照片頁", CGRect(x:496,y:396,width:62,height:18),size:12)
              let entries = selection.rows.prefix(8)
              for (index, record) in entries.enumerated() {
                let y = CGFloat(430 + index * 31)
                let area = eligible[record.id].map { basis in
                  (basis == .legacy ? "舊版 " : "") + String(format:"%.2f cm²",record.surfaceCm2)
                } ?? "未提供面積"
                text(selection.displayDate(record.dateISO),CGRect(x:36,y:y,width:198,height:20),size:13)
                text(area,CGRect(x:255,y:y,width:215,height:20),size:13)
                text("第 \(index+2) 頁",CGRect(x:496,y:y,width:70,height:20),size:12)
              }
              let note = selection.rows.count > entries.count
                ? "另有 \(selection.rows.count-entries.count) 筆；全部照片皆依日期逐頁列出。"
                : "照片依比例呈現；拍攝距離可能不同，觀察面積變化請參照趨勢與各筆數值。"
              text(note, CGRect(x:36,y:CGFloat(445 + entries.count * 31),width:523,height:36),size:11)
            }
          }
          for (local, index) in range.enumerated() {
            autoreleasepool {
              let record = selection.rows[index]
              let img = image(record)
              let polygons = LitePDFReport.polygons(record, image: img)
              if LitePDFReport.areaSeries(record, polygons: polygons) != eligible[record.id] { sourceChanged = true }
              let top = photos ? CGFloat(number == 0 ? 344 : 164)
                               : CGFloat((number == 0 ? 380 : 202) + local * 180)
              UIColor(white: 0.82, alpha: 1).setStroke()
              context.cgContext.move(to: CGPoint(x: 36, y: top))
              context.cgContext.addLine(to: CGPoint(x: 559, y: top))
              context.cgContext.strokePath()
              text(
                "\(index+1)    \(selection.displayDate(record.dateISO))",
                CGRect(x: 36, y: top + 10, width: 525, height: 23), size: 13, bold: true)
              if photos {
                // Use the page width and keep the entire exposure visible, including portrait photos.
                let frame = CGRect(x: 36, y: top + 38, width: 523, height: 626 - (top + 38))
                if let img, img.size.width > 0, img.size.height > 0 {
                  let factor = min(frame.width / img.size.width, frame.height / img.size.height)
                  let size = CGSize(
                    width: img.size.width * factor, height: img.size.height * factor)
                  let rect = CGRect(
                    x: frame.midX - size.width / 2, y: frame.midY - size.height / 2,
                    width: size.width, height: size.height)
                  LitePDFReport.reportPhoto(img).draw(in: rect)
                  if let polygons, let cg = img.cgImage {
                    LitePDFReport.drawMask(polygons, pixels: cg, in: rect, context: context.cgContext)
                  }
                } else {
                  text("本機照片無法讀取", frame, size: 12)
                }
              }
              let areaTop: CGFloat = photos ? 634 : top + 40
              let area = LitePDFReport.areaLines(record, polygons: polygons)
              text(
                area[0], CGRect(x: 36, y: areaTop, width: 523, height: photos ? 32 : 47), size: 14, bold: true
              )
              if area.count > 1 {
                text(area[1], CGRect(x: 36, y: photos ? 662 : top + 86, width: 523, height: 28), size: 12)
              }
              let mask = !photos ? "照片與圈選依選項省略" : polygons == nil
                ? "無可核對的圈選，未疊圖" : "青色細線標示已存傷口輪廓"
              text(mask + " · " + (record.contourOrigin?.label ?? "圈選版本未確認"),
                   CGRect(x: 36, y: photos ? 693 : top + 112, width: 523, height: 24), size: 11)
              let quality: String
              switch record.savedArea {
              case .unavailable: quality = "本筆面積未通過檢查。"
              case .legacy: quality = "舊版紀錄，完整拍攝品質未保存。"
              case .current(let verdict):
                quality = (record.measurementContext?.warnings.first ?? verdict.warnings.first)
                  ?? (record.quality == "ok" ? "未記錄警示" : record.quality)
              }
              text(
                "拍攝品質  " + quality,
                CGRect(x: 36, y: photos ? 718 : top + 143, width: 523, height: 28), size: 11
              )
            }
          }
          text(
            "面積來源為免貼紙 LiDAR 深度估算；投影與表面積分開列示。", CGRect(x: 36, y: 748, width: 522, height: 20),
            size: 10)
          text(LitePDFReport.disclaimer, CGRect(x: 36, y: 770, width: 522, height: 30), size: 10)
          text(
            "WoundAILite                                  第 \(number+1) / \(ranges.count) 頁",
            CGRect(x: 36, y: 803, width: 525, height: 18), size: 10)
        }
      }
      if sourceChanged { throw LiteTimelineError.invalidSelection }
    }
  }
}

extension LiteTimelinePDF {
  /// All timestamps come from the selected rows. No interpolation across missing values or source changes.
  private static func drawTrend(_ selection: LiteTimelineSelection, eligible: [String: LiteAreaSeries],
                                ctx: CGContext) {
    let teal = UIColor(red: 0.03, green: 0.39, blue: 0.46, alpha: 1)
    let ochre = UIColor(red: 0.56, green: 0.32, blue: 0.10, alpha: 1)
    let muted = UIColor(white: 0.34, alpha: 1)
    let panel = CGRect(x: 36, y: 198, width: 523, height: 170)
    let plot = CGRect(x: 86, y: 244, width: 449, height: 66)
    func label(_ value: String, x: CGFloat, y: CGFloat, width: CGFloat,
               size: CGFloat = 10, bold: Bool = false, color: UIColor = .black) {
      (value as NSString).draw(in: CGRect(x: x, y: y, width: width, height: 22), withAttributes: [
        .font: bold ? UIFont.boldSystemFont(ofSize: size) : UIFont.systemFont(ofSize: size),
        .foregroundColor: color])
    }
    ctx.saveGState()
    defer { ctx.restoreGState() }
    ctx.setFillColor(UIColor(white: 0.965, alpha: 1).cgColor)
    ctx.fill(panel)
    label("LiDAR 表面積趨勢（cm²）", x: 48, y: 208, width: 241, size: 15, bold: true)
    let series = LiteAreaSeries.allCases.map { basis in
      LiteWoundTrend.points(woundID: selection.group.id, records: selection.rows, series: basis,
                            include: { eligible[$0.id] == basis })
    }
    let points = series.flatMap { $0 }
    let plotted = Set(points.map(\.id))
    label("可繪 \(plotted.count) / \(selection.rows.count) 筆", x: 443, y: 209, width: 111, color: muted)
    let dated = selection.rows.compactMap { row in selection.date(of: row).map { (row, $0) } }
    guard let first = dated.first, let last = dated.last else {
      label("沒有可核對的拍攝日期。", x: 49, y: 266, width: 475, size: 12)
      return
    }
    func x(_ date: Date) -> CGFloat {
      let span = last.1.timeIntervalSince(first.1)
      return span == 0 ? plot.midX : plot.minX + plot.width * date.timeIntervalSince(first.1) / span
    }
    let upper = max(1, ceil(points.map(\.area).max() ?? 0))
    if !points.isEmpty {
      for fraction in [0.0, 0.5, 1.0] {
        let y = plot.maxY - plot.height * fraction
        ctx.setStrokeColor(UIColor(white: 0.80, alpha: 1).cgColor)
        ctx.setLineWidth(0.6)
        ctx.move(to: CGPoint(x: plot.minX, y: y)); ctx.addLine(to: CGPoint(x: plot.maxX, y: y)); ctx.strokePath()
        label(String(format: "%.3g", upper * fraction), x: 48, y: y-6, width: 38, color: muted)
      }
      for (basis, group) in zip(LiteAreaSeries.allCases, series) {
        let color = basis == .current ? teal : ochre
        var previous: (LiteWoundTrend.Point, CGPoint)?
        for point in group {
          let pos = CGPoint(x: x(point.date), y: plot.maxY - CGFloat(point.area / upper) * plot.height)
          ctx.setStrokeColor(color.cgColor)
          ctx.setLineWidth(1.5)
          if let (prior, origin) = previous, let key = prior.lineGroup, key == point.lineGroup, prior.date < point.date {
            ctx.move(to: origin); ctx.addLine(to: pos); ctx.strokePath()
          }
          let dot = CGRect(x: pos.x-3, y: pos.y-3, width: 6, height: 6)
          ctx.setFillColor((basis == .current ? color : .white).cgColor)
          ctx.fillEllipse(in: dot); ctx.strokeEllipse(in: dot)
          previous = (point, pos)
        }
      }
    } else {
      label("此期間沒有可繪製的面積。", x: 49, y: 263, width: 475, size: 12)
    }
    // Ticks beneath the plot mean an observation without a plottable area, never a measured zero.
    ctx.setFillColor(muted.cgColor)
    for (row, date) in dated where !plotted.contains(row.id) {
      ctx.fill(CGRect(x: x(date)-0.6, y: plot.maxY+4, width: 1.2, height: 5))
    }
    label(selection.displayDate(first.0.dateISO), x: 48, y: 322, width: 170, color: muted)
    if first.1 != last.1 {
      label(selection.displayDate(last.0.dateISO), x: 447, y: 322, width: 106, color: muted)
    }
    label("● 本版    ○ 舊版留存值    | 無可繪面積    同一圈選方式及版本才連線。", x: 48, y: 345,
          width: 500, color: muted)
  }
}

struct LiteTimelineExportView: View {
  let group: LiteWoundGroup
  @ObservedObject var store: LiteStore
  @Environment(\.dismiss) private var dismiss
  @State private var entire = true
  @State private var photos = true
  @State private var start = Calendar.current.date(byAdding: .month, value: -1, to: .now) ?? .now
  @State private var end = Date()
  @State private var busy = false
  @State private var problem: String?
  @State private var share: LitePDFShare?
  @State private var file: LitePDFShare?

  private var selection: LiteTimelineSelection? {
    try? LiteTimelineSelection(
      group: group, records: store.records, first: entire ? nil : start, last: entire ? nil : end)
  }
  var body: some View {
    NavigationStack {
      Form {
        Section("傷口") { Text(group.displayName) }
        Section("報告範圍") {
          Toggle("全部日期", isOn: $entire)
          if !entire {
            DatePicker("起日", selection: $start, displayedComponents: .date)
            DatePicker("迄日", selection: $end, displayedComponents: .date)
          }
          Toggle("附上照片與圈選", isOn: $photos)
          if let selection {
            Text("依時間由舊到新，共 \(selection.rows.count) 筆。").font(.footnote)
          } else {
            Text("請核對日期範圍與傷口分組。無法核對時仍可查看個別紀錄。").font(.footnote)
          }
        }
        Button(busy ? "正在產生 PDF…" : "產生並分享 PDF") { export() }
          .disabled(busy || selection?.rows.isEmpty != false || store.needsReload)
        Text("在本機產生，只包含此傷口。分享前請確認接收對象。").font(.footnote)
        if let problem { Text(problem).foregroundStyle(.orange) }
      }
      .navigationTitle("匯出時間軸")
      .toolbar { Button("關閉") { dismiss() }.disabled(busy) }
      .interactiveDismissDisabled(busy)
      .sheet(
        item: $share,
        onDismiss: {
          file?.remove()
          file = nil
        }
      ) { item in
        LitePDFShareSheet(item: item) { share = nil }
      }
    }
  }
  private func export() {
    guard let selection, !selection.rows.isEmpty, !busy else { return }
    busy = true
    problem = nil
    let include = photos
    Task { @MainActor in
      defer { busy = false }
      do {
        let item = try await Task.detached(priority: .userInitiated) {
          let images = LocalImageStore()
          return try LiteTimelinePDF.export(
            selection, photos: include,
            image: { record in
              images.rawBytes(record.imageName).flatMap { UIImage(data: $0) }
            })
        }.value
        file = item
        share = item
      } catch { problem = "時間軸 PDF 暫時無法產生。原紀錄已保留，請稍後重試。" }
    }
  }
}
