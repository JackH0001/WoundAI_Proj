import Foundation

struct LiteWoundGroup: Identifiable, Equatable {
    let id: String
    let name: String
    var location: LiteWoundLocation? = nil
    var displayName: String { location.map { "\(name)・\($0.label)" } ?? "\(name)・待補位置" }
}

enum LiteGrouping {
    /// Use the latest recorded measurement, not the order in which groups were created.
    static func mostRecentWoundID(_ records: [LiteRecord]) -> String? {
        records.compactMap { record -> (String, Date)? in
            guard let id = record.woundID, !id.isEmpty,
                  let date = ISO8601DateFormatter().date(from: record.dateISO) else { return nil }
            return (id, date)
        }.max { $0.1 < $1.1 }?.0
    }

    static func groups(_ records: [LiteRecord]) -> [LiteWoundGroup] {
        var seen = Set<String>()
        return records.sorted { $0.dateISO < $1.dateISO }.compactMap { record in
            guard let id = record.woundID, !id.isEmpty, seen.insert(id).inserted else { return nil }
            let locations = records.filter { $0.woundID == id }.compactMap(\.woundLocation)
            let valid = locations.first.flatMap { first in
                first.isComplete && locations.allSatisfy { $0 == first } ? first : nil
            }
            return LiteWoundGroup(id: id, name: record.woundName ?? "傷口 \(id.prefix(6))", location: valid)
        }
    }

    static func records(for woundID: String, in records: [LiteRecord]) -> [LiteRecord] {
        guard !woundID.isEmpty else { return [] }
        return records.filter { $0.woundID == woundID }.sorted { $0.dateISO > $1.dateISO }
    }

    /// 僅比較已明確歸組的同一傷口；相同時間或無效時間不推測先後。
    static func changePercent(_ record: LiteRecord, in records: [LiteRecord]) -> Double? {
        guard let id = record.woundID, !id.isEmpty,
              let date = ISO8601DateFormatter().date(from: record.dateISO),
              record.surfaceCm2.isFinite, record.surfaceCm2 >= 0 else { return nil }
        let earlier = Self.records(for: id, in: records).compactMap { candidate -> (LiteRecord, Date)? in
            guard let candidateDate = ISO8601DateFormatter().date(from: candidate.dateISO),
                  candidateDate < date else { return nil }
            return (candidate, candidateDate)
        }.max { $0.1 < $1.1 }?.0
        guard let previous = earlier?.surfaceCm2, previous.isFinite, previous > 0 else { return nil }
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
    }

    static func points(woundID: String, records: [LiteRecord]) -> [Point] {
        guard !woundID.isEmpty else { return [] }
        let formatter = ISO8601DateFormatter()
        return records.compactMap { record -> Point? in
            guard record.woundID == woundID, record.surfaceCm2.isFinite,
                  record.surfaceCm2 >= 0,
                  let date = formatter.date(from: record.dateISO) else { return nil }
            return Point(id: record.id, date: date, area: record.surfaceCm2)
        }.sorted { $0.date == $1.date ? $0.id < $1.id : $0.date < $1.date }
    }
}
