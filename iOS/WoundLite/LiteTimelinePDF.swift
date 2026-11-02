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

  func displayDate(_ iso: String) -> String {
    let precise = ISO8601DateFormatter()
    precise.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    guard let date = ISO8601DateFormatter().date(from: iso) ?? precise.date(from: iso) else {
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
    let iso = ISO8601DateFormatter()
    let precise = ISO8601DateFormatter()
    precise.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    let series = records.filter { $0.woundID == group.id }.map {
      ($0, iso.date(from: $0.dateISO) ?? precise.date(from: $0.dateISO))
    }
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
    let perPage = 3
    let pages = (selection.rows.count + perPage - 1) / perPage
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
      try renderer.writePDF(to: output) { context in
        for number in 0..<pages {
          context.beginPage()
          UIColor.white.setFill()
          context.cgContext.fill(page)
          text("WoundAILite   /   個人觀察紀錄", CGRect(x: 36, y: 32, width: 510, height: 20), size: 12)
          text("傷口時間軸", CGRect(x: 36, y: 58, width: 520, height: 40), size: 28, bold: true)
          text(
            selection.group.displayName, CGRect(x: 36, y: 107, width: 525, height: 27), size: 17,
            bold: true)
          text(
            "\(selection.period)     ·     共 \(selection.rows.count) 筆",
            CGRect(x: 36, y: 140, width: 525, height: 22), size: 13)
          let stamp =
            selection.missingDates > 0
            ? "有 \(selection.missingDates) 筆日期待確認；自訂期間只列日期可核對的紀錄。" : "依拍攝時間排列，面積為個人紀錄參考值。"
          text(stamp, CGRect(x: 36, y: 168, width: 525, height: 24), size: 11)
          for local in 0..<perPage {
            let index = number * perPage + local
            guard index < selection.rows.count else { break }
            autoreleasepool {
              let record = selection.rows[index]
              let img = image(record)
              let top = CGFloat(202 + local * 180)
              UIColor(white: 0.82, alpha: 1).setStroke()
              context.cgContext.move(to: CGPoint(x: 36, y: top))
              context.cgContext.addLine(to: CGPoint(x: 559, y: top))
              context.cgContext.strokePath()
              text(
                "\(index+1)    \(selection.displayDate(record.dateISO))",
                CGRect(x: 36, y: top + 10, width: 525, height: 23), size: 13, bold: true)
              let x: CGFloat = photos ? 215 : 36
              if photos {
                let frame = CGRect(x: 36, y: top + 38, width: 164, height: 123)
                if let img, img.size.width > 0, img.size.height > 0 {
                  let factor = min(frame.width / img.size.width, frame.height / img.size.height)
                  let size = CGSize(
                    width: img.size.width * factor, height: img.size.height * factor)
                  let rect = CGRect(
                    x: frame.midX - size.width / 2, y: frame.midY - size.height / 2,
                    width: size.width, height: size.height)
                  let opts = UIGraphicsImageRendererFormat()
                  opts.scale = 2
                  opts.opaque = true
                  let thumb = UIGraphicsImageRenderer(size: size, format: opts).image { _ in
                    img.draw(in: CGRect(origin: .zero, size: size))
                  }
                  thumb.draw(in: rect)
                  if let polygons = LitePDFReport.polygons(record, image: img), let cg = img.cgImage
                  {
                    let ctx = context.cgContext
                    ctx.saveGState()
                    ctx.clip(to: rect)
                    ctx.setLineWidth(1.2)
                    ctx.setStrokeColor(UIColor.systemTeal.cgColor)
                    for poly in polygons {
                      let points = poly.map {
                        CGPoint(
                          x: rect.minX + CGFloat($0[0]) / CGFloat(cg.width) * rect.width,
                          y: rect.minY + CGFloat($0[1]) / CGFloat(cg.height) * rect.height)
                      }
                      ctx.addLines(between: points)
                      ctx.closePath()
                      ctx.strokePath()
                    }
                    ctx.restoreGState()
                  }
                } else {
                  text("本機照片無法讀取", frame, size: 12)
                }
              }
              let area = LitePDFReport.areaLines(record, image: img)
              text(
                area[0], CGRect(x: x, y: top + 40, width: 559 - x, height: 47), size: 14, bold: true
              )
              if area.count > 1 {
                text(area[1], CGRect(x: x, y: top + 86, width: 559 - x, height: 24), size: 12)
              }
              text(
                "圈選來源  " + (record.contourOrigin?.label ?? "來源未確認"),
                CGRect(x: x, y: top + 112, width: 559 - x, height: 28), size: 11)
              let quality = record.quality == "ok" ? "未記錄警示" : record.quality
              text(
                "拍攝品質  " + quality, CGRect(x: x, y: top + 143, width: 559 - x, height: 24), size: 11
              )
            }
          }
          text(
            "面積來源為免貼紙 LiDAR 深度估算；投影與表面積分開列示。", CGRect(x: 36, y: 748, width: 522, height: 20),
            size: 10)
          text(LitePDFReport.disclaimer, CGRect(x: 36, y: 770, width: 522, height: 30), size: 10)
          text(
            "WoundAILite                                  第 \(number+1) / \(pages) 頁",
            CGRect(x: 36, y: 803, width: 525, height: 18), size: 10)
        }
      }
    }
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
          Text("依時間由舊到新，共 \(selection?.rows.count ?? 0) 筆。").font(.footnote)
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
