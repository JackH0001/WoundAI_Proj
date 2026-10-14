import Foundation
import Combine

/// Read authoritative SQLite state on entry and again at the send boundary.
@MainActor
final class ClinicalReviewRecord: ObservableObject {
    @Published var measurement: Measurement?
    func refresh(id: Int64, repo: CaseRepository) async {
        measurement = await repo.measurement(id: id)
    }
    func prepareSubmission(id: Int64, repo: CaseRepository) async -> Measurement? {
        await refresh(id: id, repo: repo)
        guard let current = measurement, !current.annotationSubmitted else { return nil }
        return current
    }
}

/// Saving unchanged content must not revoke a confirmed annotation receipt.
enum ClinicalAnnotationRevision {
    static func matches(_ a: Measurement, _ b: Measurement,
                        rasterA: Data?, rasterB: Data?) -> Bool {
        guard let iid = a.imageId, !iid.isEmpty, iid == b.imageId,
              a.wdCode == b.wdCode, a.caseId == b.caseId, a.source == b.source,
              a.gtPolygon == b.gtPolygon, a.imageW == b.imageW, a.imageH == b.imageH,
              a.exudate == b.exudate, a.estimatedArea == b.estimatedArea,
              a.mmPerPx == b.mmPerPx, a.route == b.route,
              a.correctionIou == b.correctionIou, a.doctorVerified == b.doctorVerified,
              a.tissueGranulation == b.tissueGranulation, a.tissueSlough == b.tissueSlough,
              a.tissueNecrosis == b.tissueNecrosis, a.tissueEpithelial == b.tissueEpithelial,
              a.tissueOther == b.tissueOther, rasterA == rasterB else { return false }
        if a.rasterPath != nil || b.rasterPath != nil {
            guard rasterA != nil, rasterB != nil,
                  let am = a.rasterMeta, let bm = b.rasterMeta,
                  let aj = try? JSONSerialization.jsonObject(with: Data(am.utf8)) as? NSDictionary,
                  let bj = try? JSONSerialization.jsonObject(with: Data(bm.utf8)) as? NSDictionary
            else { return false }
            return aj.isEqual(bj)
        }
        return a.rasterMeta == nil && b.rasterMeta == nil
    }
    static func sameDepth(_ a: (raw: Data, meta: [String: Any]),
                          _ b: (raw: Data, meta: [String: Any])) -> Bool {
        var am = a.meta, bm = b.meta
        // Legacy sidecar creation time changes on save; it is not sensor capture time.
        am.removeValue(forKey: "captured_at"); bm.removeValue(forKey: "captured_at")
        return a.raw == b.raw && NSDictionary(dictionary: am).isEqual(NSDictionary(dictionary: bm))
    }
}
