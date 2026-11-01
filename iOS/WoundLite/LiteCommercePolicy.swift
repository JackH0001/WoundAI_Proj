#if DEBUG && targetEnvironment(simulator)
import Foundation
import CryptoKit

// Executable synthetic-only contract. Never used by the live transport, quota or consent journal.
enum LiteLabPlan: String, Codable, CaseIterable {
    case free, plus
    var dailyLimit: Int { self == .free ? 2 : 10 }
    var label: String { self == .free ? "免費：每日 2 次＋廣告" : "Plus：每日共 10 次、無廣告" }
}

enum LiteLabSampling: String, Codable, CaseIterable {
    case disabled, allConsenting, fixedHalf
    var label: String {
        switch self {
        case .disabled: return "研究收案停用"
        case .allConsenting: return "已同意者全部收案（對照）"
        case .fixedHalf: return "固定 50% 收案（後續改版選項）"
        }
    }
}

struct LiteLabAssignment: Codable, Equatable {
    let subject: String
    let study: String
    let policy: LiteLabSampling
    let policyVersion: String
    let selected: Bool
}

struct LiteLabCapture: Codable, Equatable {
    let id: String
    let wound: String
    let epoch: Int
    let rawReference: String
    let depthReference: String
    var masks: [String]
}

enum LiteLabError: Error { case invalidInput, conflict, exhausted, withdrawn }

/// Synthetic identity is scoped to a study, not a photo or wound. Re-consent does not re-roll it.
struct LiteLabCohort: Codable, Equatable {
    private(set) var assignments: [String: LiteLabAssignment] = [:]
    private(set) var epochs: [String: Int] = [:]
    private(set) var consent: [String: Bool] = [:]
    private(set) var local: [String: [LiteLabCapture]] = [:]
    private(set) var research: [String: [LiteLabCapture]] = [:]
    private(set) var audit: [String] = []
    private var used: [String: Int] = [:]
    private var requests: [String: String] = [:]

    private func key(_ subject: String) -> String { "synthetic-v1/" + subject }

    mutating func enroll(subject: String, policy: LiteLabSampling, agreed: Bool) throws -> LiteLabAssignment? {
        guard subject.hasPrefix("synthetic-"), subject.count <= 80 else { throw LiteLabError.invalidInput }
        let k = key(subject)
        consent[k] = agreed
        if epochs[k] == nil { epochs[k] = 1 }
        // No research decision before eligibility / consent; service remains independent.
        guard agreed, policy != .disabled else { return assignments[k] }
        if let existing = assignments[k] { return existing }
        let digest = HMAC<SHA256>.authenticationCode(for: Data(k.utf8),
            using: SymmetricKey(data: Data("PUBLIC-SYNTHETIC-TEST-SEED-NOT-FOR-PRODUCTION".utf8)))
        let bytes = Array(digest)
        let bucket = Int(bytes[0]) * 256 + Int(bytes[1])
        let value = LiteLabAssignment(subject: subject, study: "synthetic-v1", policy: policy,
            policyVersion: "lab-v1", selected: policy == .allConsenting || bucket < 32768)
        assignments[k] = value
        audit.append("assignment:\(subject):\(value.policy.rawValue):\(value.selected)")
        return value
    }

    func remaining(subject: String, day: String, plan: LiteLabPlan) -> Int {
        max(0, plan.dailyLimit - (used[key(subject) + "/" + day] ?? 0))
    }

    mutating func capture(subject: String, day: String, plan: LiteLabPlan, id: String,
                          wound: String, mask: String, researchEnabled: Bool) throws -> Bool {
        guard subject.hasPrefix("synthetic-"), !id.isEmpty, !wound.isEmpty, !mask.isEmpty else {
            throw LiteLabError.invalidInput
        }
        let k = key(subject), request = k + "/" + id
        let fingerprint = "\(wound)|\(mask)"
        if let prior = requests[request] {
            guard prior == fingerprint else { throw LiteLabError.conflict }
            return research[k, default: []].contains { $0.id == id }
        }
        guard remaining(subject: subject, day: day, plan: plan) > 0 else { throw LiteLabError.exhausted }
        // The synthetic service creates a local result regardless of the sampling decision.
        let record = LiteLabCapture(id: id, wound: wound, epoch: epochs[k] ?? 1,
            rawReference: "synthetic-rgb-" + id, depthReference: "synthetic-depth-" + id, masks: [mask])
        local[k, default: []].append(record)
        used[k + "/" + day, default: 0] += 1
        requests[request] = fingerprint
        let retain = researchEnabled && consent[k] == true && assignments[k]?.selected == true
        if retain { research[k, default: []].append(record) }
        audit.append("capture:\(subject):\(id):epoch\(record.epoch):research=\(retain)")
        return retain
    }

    mutating func revise(subject: String, id: String, expectedRevision: Int, mask: String,
                         researchEnabled: Bool) throws {
        let k = key(subject)
        guard !mask.isEmpty, let i = local[k]?.firstIndex(where: { $0.id == id }) else {
            throw LiteLabError.invalidInput
        }
        let prior = local[k]![i]
        if prior.masks.last == mask { return } // Same content is not a new revision or inference.
        guard expectedRevision == prior.masks.count else { throw LiteLabError.conflict }
        local[k]![i].masks.append(mask)
        // Never resurrect a withdrawn epoch or retrospectively recruit old service-only images.
        if researchEnabled, consent[k] == true, prior.epoch == epochs[k],
           let r = research[k]?.firstIndex(where: { $0.id == id }) {
            research[k]![r].masks.append(mask)
        }
        audit.append("revision:\(subject):\(id):\(prior.masks.count + 1)")
    }

    mutating func withdraw(subject: String) {
        let k = key(subject)
        guard consent[k] == true else { return }
        consent[k] = false
        research[k] = [] // Synthetic in-memory cleanup only; retains local timeline + audit.
        audit.append("withdraw:\(subject):epoch\(epochs[k] ?? 1)")
        epochs[k] = (epochs[k] ?? 1) + 1
    }

    func localTimeline(subject: String) -> [LiteLabCapture] { local[key(subject), default: []] }
    func researchTimeline(subject: String) -> [LiteLabCapture] { research[key(subject), default: []] }
}

enum LiteLabAdPlacement { case plans, measurement, history, consent }
struct LiteLabAdvertisement {
    static func visible(plan: LiteLabPlan, placement: LiteLabAdPlacement) -> Bool {
        plan == .free && placement == .plans
    }
    // No wound, consent, installation, cohort, image or depth attributes are accepted.
    let creativeID = "synthetic-sponsor-001"
    let title = "照護用品品牌合作版位"
}
#endif
