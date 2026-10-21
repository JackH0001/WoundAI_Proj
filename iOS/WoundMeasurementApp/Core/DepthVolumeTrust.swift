import Foundation

/// Diagnostic-only gate shared with WoundAI3D. Integrity/confidence never certify
/// metrological accuracy. No validated physical profile is registered in v1.
/// This gate neither changes source bytes nor grants consent/training permission.
public enum DepthVolumeTrust {
    public struct Evidence: Codable {
        public let inputManifestSHA256: String
        public let candidateVolumeMl: Double?
        public let validFraction: Double
        public let baselineRMSMm: Double
        public let signedDepthP95Mm: Double
        public let registrationEvidenceID: String?
        public let scaleEvidenceID: String?
        public let physicalValidationProfileID: String?
        public let referenceVolumeMl: Double?
        public let referenceRelativeTolerance: Double?
    }
    public struct Decision: Codable {
        public let schema: String
        public let inputManifestSHA256: String
        public let status: String
        public let reasons: [String]
        public let diagnosticVolumeMl: Double?
        public let measurementVolumeMl: Double?
        public let metricGroundTruthAllowed: Bool
        public let retainRawData: Bool
        public let policy: String
    }
    public static func assess(_ e: Evidence, expectedManifestSHA256: String) -> Decision {
        var reasons: [String] = []
        let hashOK = e.inputManifestSHA256.range(of: "^[a-f0-9]{64}$", options: .regularExpression) != nil
            && e.inputManifestSHA256 == expectedManifestSHA256
        if !hashOK { reasons.append("manifest_binding_failed") }
        let candidateOK = e.candidateVolumeMl.map { $0.isFinite && $0 >= 0 } ?? false
        let numbersOK = e.validFraction.isFinite && (0...1).contains(e.validFraction)
            && e.baselineRMSMm.isFinite && e.baselineRMSMm >= 0 && e.signedDepthP95Mm.isFinite
        if !candidateOK || !numbersOK { reasons.append("invalid_or_missing_metric") }
        if numbersOK && e.validFraction < 1 { reasons.append("incomplete_depth") }
        if e.registrationEvidenceID?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty != false {
            reasons.append("registration_not_verified")
        }
        if e.scaleEvidenceID?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty != false {
            reasons.append("scale_not_verified")
        }
        // A diagnostic screen, not a clinical detection limit or proof of zero depth.
        if numbersOK && e.signedDepthP95Mm <= 3 * e.baselineRMSMm {
            reasons.append("relief_not_resolved_above_baseline")
        }
        if let ref = e.referenceVolumeMl {
            if !ref.isFinite || ref <= 0 || e.referenceRelativeTolerance == nil
                || !(e.referenceRelativeTolerance!.isFinite)
                || !(0...1).contains(e.referenceRelativeTolerance!) {
                reasons.append("invalid_reference")
            } else if candidateOK, let candidate = e.candidateVolumeMl,
                      abs(candidate / ref - 1) > e.referenceRelativeTolerance! {
                reasons.append("physical_reference_mismatch")
            }
        } else if e.referenceRelativeTolerance != nil { reasons.append("invalid_reference") }
        // Fail closed: IDs/booleans supplied by an upstream app are not validation.
        reasons.append("physical_validation_profile_not_registered")
        let rejected = reasons.contains { ["manifest_binding_failed", "invalid_or_missing_metric",
            "incomplete_depth", "invalid_reference", "physical_reference_mismatch",
            "relief_not_resolved_above_baseline"].contains($0) }
        return Decision(schema: "wound-depth-volume-quality/1", inputManifestSHA256: e.inputManifestSHA256,
            status: rejected ? "not_reliable" : "unverified", reasons: reasons,
            diagnosticVolumeMl: hashOK && candidateOK ? e.candidateVolumeMl : nil,
            measurementVolumeMl: nil, metricGroundTruthAllowed: false, retainRawData: true,
            policy: "diagnostic-v1-no-validated-physical-profile")
    }
}
