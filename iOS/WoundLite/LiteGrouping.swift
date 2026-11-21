import Foundation

struct LiteWoundGroup: Identifiable, Equatable {
    let id: String
    let name: String
    var location: LiteWoundLocation? = nil
    var account: String? = nil
    var ended = false
    var displayName: String { location.map { "\(name)・\($0.label)" } ?? "\(name)・待補位置" }
    var trackingLabel: String {
        guard account != nil, let wire = try? LiteWoundRoster.wireID(id) else { return displayName }
        return displayName + "（" + wire.suffix(6).uppercased() + "）"
    }
}

extension LiteRecord {
    /// Preserve offsets and subsecond capture times across history, charts and exports.
    static func captureDate(_ iso: String) -> Date? {
        (try? Date.ISO8601FormatStyle(includingFractionalSeconds: true).parse(iso))
            ?? (try? Date.ISO8601FormatStyle().parse(iso))
    }
}

enum LiteGrouping {
    /// Historical rows can still be opened individually when their group binding is inconsistent.
    static func canCombine(_ rows: [LiteRecord], account: String?) -> Bool {
        rows.allSatisfy { $0.woundAccount == account } && Set(rows.map(\.id)).count == rows.count
    }

    /// Use the latest recorded measurement, not the order in which groups were created.
    static func mostRecentWoundID(_ records: [LiteRecord]) -> String? {
        records.compactMap { record -> (String, Date)? in
            guard let id = record.woundID, !id.isEmpty,
                  let date = LiteRecord.captureDate(record.dateISO) else { return nil }
            return (id, date)
        }.max { $0.1 < $1.1 }?.0
    }

    static func groups(_ records: [LiteRecord]) -> [LiteWoundGroup] {
        var seen = Set<String>()
        return records.sorted { first, second in
            first.dateISO == second.dateISO ? first.id < second.id : first.dateISO < second.dateISO
        }.compactMap { record in
            guard let id = record.woundID, !id.isEmpty, seen.insert(id).inserted else { return nil }
            let locations = records.filter { $0.woundID == id }.compactMap(\.woundLocation)
            let valid = locations.first.flatMap { first in
                first.isComplete && locations.allSatisfy { $0 == first } ? first : nil
            }
            let members = records.filter { $0.woundID == id }
            return LiteWoundGroup(id: id, name: record.woundName ?? "傷口 \(id.prefix(6))", location: valid,
                account: members.compactMap(\.woundAccount).first, ended: members.contains { $0.woundEnded == true })
        }
    }

    static func records(for woundID: String, in records: [LiteRecord]) -> [LiteRecord] {
        guard !woundID.isEmpty else { return [] }
        return records.filter { $0.woundID == woundID }.map { ($0, LiteRecord.captureDate($0.dateISO)) }
            .sorted { lhs, rhs in
                let left = lhs.1 ?? .distantPast, right = rhs.1 ?? .distantPast
                return left == right ? lhs.0.id < rhs.0.id : left > right
            }.map { $0.0 }
    }

    /// 僅比較已明確歸組的同一傷口；相同時間或無效時間不推測先後。
    static func changePercent(_ record: LiteRecord, in records: [LiteRecord]) -> Double? {
        guard let id = record.woundID, !id.isEmpty,
              let series = record.areaSeries,
              let date = LiteRecord.captureDate(record.dateISO),
              record.surfaceCm2.isFinite, record.surfaceCm2 >= 0 else { return nil }
        let rows = records.filter { $0.woundID == id }
        guard canCombine(rows, account: record.woundAccount) else { return nil }
        let earlier = rows.compactMap { candidate -> (LiteRecord, Date)? in
            guard let candidateDate = LiteRecord.captureDate(candidate.dateISO),
                  candidate.areaSeries == series,
                  candidateDate < date else { return nil }
            return (candidate, candidateDate)
        }.max { $0.1 < $1.1 }?.0
        guard let earlier, let basis = record.contourOrigin, basis.supportsComparison,
              earlier.contourOrigin == basis else { return nil }
        let previous = earlier.surfaceCm2
        guard previous.isFinite, previous > 0 else { return nil }
        return (record.surfaceCm2 - previous) / previous * 100
    }
}

/// Snapshot shown in the save confirmation. Re-resolve before writing so a changed
/// or deleted group cannot silently receive a measurement under a different location.
struct LiteWoundSaveTarget: Equatable {
    let woundID: String
    let name: String
    let location: LiteWoundLocation

    static func resolve(addingNew: Bool, selectedID: String,
                        entered: LiteWoundLocation, groups: [LiteWoundGroup]) -> Self? {
        if addingNew {
            guard entered.isComplete else { return nil }
            return Self(woundID: "", name: "新增傷口", location: entered)
        }
        guard !selectedID.isEmpty, let group = groups.first(where: { $0.id == selectedID }),
              let location = group.location ?? (entered.isComplete ? entered : nil),
              location.isComplete else { return nil }
        return Self(woundID: group.id, name: group.name, location: location)
    }

    var confirmationMessage: String {
        "傷口：\(name)\n位置：\(location.label)\n\n請確認本次拍攝的是這一處傷口，再存入紀錄。"
    }
}

/// A chart always belongs to one explicit wound. Invalid dates and measurements
/// cannot become plotted points or be silently assigned a replacement timestamp.
enum LiteWoundTrend {
    struct Point: Identifiable {
        let id: String
        let date: Date
        let area: Double
        let lineGroup: String?
    }

    static func points(woundID: String, records: [LiteRecord], series: LiteAreaSeries? = nil,
                       include: (LiteRecord) -> Bool = { _ in true }) -> [Point] {
        guard !woundID.isEmpty else { return [] }
        let rows = records.filter { $0.woundID == woundID }
        guard LiteGrouping.canCombine(rows, account: rows.first?.woundAccount) else { return [] }
        let ordered = rows.compactMap { record -> (LiteRecord, Date)? in
            guard let date = LiteRecord.captureDate(record.dateISO) else { return nil }
            return (record, date)
        }.sorted {
            $0.1 == $1.1 ? $0.0.id < $1.0.id : $0.1 < $1.1
        }
        guard let chosen = series ?? ordered.last(where: { $0.0.areaSeries != nil && include($0.0) })?.0.areaSeries
        else { return [] }
        var group: String?
        var previous: LiteContourOrigin?
        return ordered.compactMap { record, date in
            guard record.areaSeries == chosen, include(record) else {
                group = nil; previous = nil
                return nil
            }
            let basis = record.contourOrigin
            if basis?.supportsComparison != true {
                group = nil
            } else if basis != previous || group == nil {
                group = record.id
            }
            previous = basis
            return Point(id: record.id, date: date, area: record.surfaceCm2, lineGroup: group)
        }
    }
}

/// Pick the two most recent dated observations for this wound, even when one
/// of their photos is unavailable. Never substitute an older photo for it.
enum LitePhotoComparison {
    case pair(previous: LiteRecord, current: LiteRecord, missingDates: Int)
    case unavailable(String)

    init(group: LiteWoundGroup, records: [LiteRecord]) {
        guard let timeline = try? LiteTimelineSelection(group: group, records: records) else {
            self = .unavailable("這組紀錄的傷口或帳戶無法核對。請從個別紀錄查看照片。")
            return
        }
        let dated = timeline.rows.filter { timeline.date(of: $0) != nil }
        guard dated.count >= 2 else {
            self = .unavailable("此傷口需有兩筆拍攝時間可核對的紀錄，才能比較前後照片。")
            return
        }
        let current = dated[dated.count-1]
        let previous = dated[dated.count-2]
        guard timeline.date(of: current) != timeline.date(of: previous),
              dated.count == 2 || timeline.date(of: dated[dated.count-3]) != timeline.date(of: previous) else {
            self = .unavailable("有多筆紀錄的拍攝時間相同，無法確定前後順序。請從個別紀錄查看照片。")
            return
        }
        self = .pair(previous: previous, current: current, missingDates: timeline.missingDates)
    }
}
