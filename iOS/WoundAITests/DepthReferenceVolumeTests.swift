import XCTest
#if canImport(WoundAI3DCore)
@testable import WoundAI3DCore
#elseif WOUND_INSTITUTION_TESTS
@testable import WoundAIInstitution
#else
@testable import WoundMeasurementApp
#endif

final class DepthReferenceVolumeTests: XCTestCase {
    let w = 64
    let k = DepthReferenceVolume.Intrinsics(fx: 200, fy: 200, cx: 32, cy: 32)
    var roi: [Double] { (0..<4096).map { (22..<42).contains($0 % 64) && (22..<42).contains($0 / 64) ? 1 : 0 } }
    var reference: [Bool] { (0..<4096).map { let x = $0 % 64, y = $0 / 64; return x < 15 || x > 49 || y < 15 || y > 49 } }
    func estimate(_ z: [Float], ref: [Bool]? = nil, weights: [Double]? = nil) throws -> DepthReferenceVolume.Result {
        try DepthReferenceVolume.estimate(metres: z, width: w, height: w, intrinsics: k,
            roiWeights: weights ?? roi, referenceMask: ref ?? reference, source: .rgbSelectedNormal)
    }
    func expected(_ z: Float, _ base: Float, cells: Double = 400) -> Double {
        cells * (pow(Double(z),3)-pow(Double(base),3)) / (3 * 200 * 200) * 1e6
    }
    func testAnalyticCavityAndRawUnchanged() throws {
        let weights = roi, z: [Float] = weights.map { $0 > 0 ? 0.304 : 0.3 }, before = z.map(\.bitPattern)
        let r = try estimate(z)
        XCTAssertEqual(try XCTUnwrap(r.deficitMl), expected(0.304,0.3), accuracy: 1e-8)
        XCTAssertEqual(try XCTUnwrap(r.maxDeficitMm),4,accuracy:0.0001)
        XCTAssertEqual(r.excessMl!,0,accuracy:1e-8)
        XCTAssertEqual(z.map(\.bitPattern),before)
        XCTAssertFalse(r.metricGroundTruthAllowed)
        XCTAssertEqual(r.status,"unverified_reference_and_registration")
    }
    func testRaisedAndMixedNeverCancel() throws {
        let weights = roi
        let raised = try estimate(weights.map { $0 > 0 ? 0.296 : 0.3 })
        XCTAssertEqual(raised.deficitMl!,0,accuracy:1e-8)
        XCTAssertEqual(raised.excessMl!,-expected(0.296,0.3),accuracy:1e-8)
        let mixed: [Float] = (0..<4096).map { weights[$0] == 0 ? 0.3 : ($0 % 64 < 32 ? 0.304 : 0.296) }
        let r = try estimate(mixed)
        XCTAssertEqual(r.deficitMl!,expected(0.304,0.3,cells:200),accuracy:1e-8)
        XCTAssertEqual(r.excessMl!,-expected(0.296,0.3,cells:200),accuracy:1e-8)
    }
    func testFlatAndTiltedTruePlaneHaveZeroVolume() throws {
        let flat = try estimate([Float](repeating:0.3,count:4096))
        XCTAssertEqual(flat.deficitMl!,0,accuracy:1e-8)
        // A camera plane has LINEAR INVERSE depth, not linear axial Z.
        let tilted: [Float] = (0..<4096).map {
            let u = Double($0 % 64 - 32)/200, v = Double($0 / 64 - 32)/200
            return Float(1 / (0.7*u-0.4*v+1/0.3))
        }
        let r = try estimate(tilted)
        XCTAssertEqual(r.deficitMl!,0,accuracy:0.0001)
        XCTAssertEqual(r.excessMl!,0,accuracy:0.0001)
        XCTAssertEqual(r.inverseDepthPlane[0],0.7,accuracy:0.00001)
        XCTAssertEqual(r.inverseDepthPlane[1],-0.4,accuracy:0.00001)
    }
    func testContaminatedRingVersusIndependentNormal() throws {
        let weights = roi, normal = reference
        let ring = (0..<4096).map { weights[$0] == 0 && !normal[$0] }
        let z: [Float] = (0..<4096).map { normal[$0] ? 0.3 : 0.304 }
        let bad = try estimate(z,ref:ring), good = try estimate(z)
        XCTAssertEqual(bad.deficitMl!,0,accuracy:1e-8)
        XCTAssertEqual(good.deficitMl!,expected(0.304,0.3),accuracy:1e-8)
        XCTAssertLessThan(bad.baselineRMSMm,0.0001)
        XCTAssertFalse(bad.metricGroundTruthAllowed, "Zero fit RMS cannot certify a wrong plane")
    }
    func testMissingROIWithholdsFullMetricsWithoutFilling() throws {
        var z: [Float] = roi.map { $0 > 0 ? 0.304 : 0.3 }
        for missing: Float in [0,.nan,.infinity,10] {
            z[32*64+32] = missing
            let r = try estimate(z)
            XCTAssertNil(r.deficitMl); XCTAssertNil(r.excessMl); XCTAssertNil(r.maxDeficitMm)
            XCTAssertEqual(r.roiCoverage,399/400.0,accuracy:1e-10)
            XCTAssertEqual(r.observedDeficitMl,expected(0.304,0.3,cells:399),accuracy:1e-8)
            XCTAssertEqual(r.status,"incomplete_depth")
        }
    }
    func testFractionalWeights() throws {
        let z: [Float] = roi.map { $0 > 0 ? 0.304 : 0.3 }
        let r = try estimate(z,weights:roi.map{$0/4})
        XCTAssertEqual(r.deficitMl!,expected(0.304,0.3,cells:100),accuracy:1e-8)
    }
    func testOverlapRejected() {
        XCTAssertThrowsError(try estimate([Float](repeating:0.3,count:4096),ref:[Bool](repeating:true,count:4096)))
    }
    func testCollinearOrMissingReferenceRejected() {
        let line = (0..<4096).map { $0 / 64 == 5 }
        XCTAssertThrowsError(try estimate([Float](repeating:0.3,count:4096),ref:line))
        XCTAssertThrowsError(try estimate([Float](repeating:0,count:4096)))
    }
    func testBadDimensionsWeightsAndIntrinsicsRejected() {
        let z = [Float](repeating:0.3,count:4096)
        XCTAssertThrowsError(try estimate(Array(z.dropLast())))
        XCTAssertThrowsError(try estimate(z,weights:roi.map{ $0 > 0 ? .nan : 0 }))
        XCTAssertThrowsError(try estimate(z,weights:roi.map{ $0 * 2 }))
        XCTAssertThrowsError(try DepthReferenceVolume.estimate(metres:z,width:64,height:64,
            intrinsics:.init(fx:0,fy:200,cx:32,cy:32),roiWeights:roi,referenceMask:reference,source:.automaticRing))
    }
    func testFloat32BitsPreservedAndByteCountChecked() throws {
        let bits: [UInt32] = [Float(0.3001).bitPattern,0x7fc01234,Float(0.3043).bitPattern,0]
        var bytes = Data()
        for b in bits { var le = b.littleEndian; withUnsafeBytes(of:&le) { bytes.append(contentsOf:$0) } }
        XCTAssertEqual(try DepthReferenceVolume.decodeMetres(bytes,count:4).map(\.bitPattern),bits)
        XCTAssertThrowsError(try DepthReferenceVolume.decodeMetres(bytes,count:3))
    }
    func testReferenceMissingReportedAndNotCertified() throws {
        var z: [Float] = roi.map { $0 > 0 ? 0.304 : 0.3 }; z[0] = .nan
        let r = try estimate(z)
        XCTAssertLessThan(r.referenceCoverage,1); XCTAssertFalse(r.metricGroundTruthAllowed)
    }
    func testOriginTranslationWithMatchingKInvariant() throws {
        let z: [Float] = roi.map { $0 > 0 ? 0.304 : 0.3 }
        let r = try DepthReferenceVolume.estimate(metres:z,width:64,height:64,
            intrinsics:.init(fx:200,fy:200,cx:32000,cy:-32000),roiWeights:roi,referenceMask:reference,source:.rgbSelectedNormal)
        XCTAssertEqual(r.deficitMl!,expected(0.304,0.3),accuracy:1e-7)
    }
    func testSeparatedCandidateMaskExcludesNearBoundaryForAllComponents() throws {
        var weights = roi; weights[8*64+8] = 1
        let m = try DepthReferenceVolume.separatedReferenceMask(width:64,height:64,roiWeights:weights)
        XCTAssertFalse(m[21*64+32]); XCTAssertFalse(m[20*64+32]); XCTAssertTrue(m[19*64+32])
        XCTAssertFalse(m[8*64+9]); XCTAssertTrue(m[8*64+11])
        XCTAssertFalse(m[0]); XCTAssertFalse(m[32*64+32])
    }
    func testExplicitReferenceSelectionOnlyRemovesNeverInventsPoints() throws {
        let requested = (0..<4096).map { reference[$0] || ($0/64 == 21 && $0%64 == 32) }
        let selected = try DepthReferenceVolume.separatedReferenceMask(width:64,height:64,roiWeights:roi,requested:requested)
        XCTAssertFalse(selected[21*64+32]); XCTAssertTrue(selected[0])
        XCTAssertFalse(selected.indices.contains { selected[$0] && !requested[$0] })
    }
    func testGuardRejectsNearOnlyReferenceRatherThanFallingBack() throws {
        let near = (0..<4096).map { $0/64 == 21 || $0%64 == 21 }
        XCTAssertThrowsError(try DepthReferenceVolume.estimate(metres:[Float](repeating:0.3,count:4096),width:64,height:64,
            intrinsics:k,roiWeights:roi,referenceMask:near.map { $0 },source:.rgbSelectedNormal,minimumReferenceDistance:64))
    }
    func testBoundaryContaminationRemovedAndPolicyRecorded() throws {
        let weights = roi
        let near = (0..<4096).map { weights[$0] == 0 && (20..<44).contains($0%64) && (20..<44).contains($0/64) }
        let requested = (0..<4096).map { reference[$0] || near[$0] }
        let z: [Float] = (0..<4096).map { weights[$0] > 0 ? 0.304 : (near[$0] ? 0.304 : 0.3) }
        let before=z.map(\.bitPattern)
        let r=try DepthReferenceVolume.estimate(metres:z,width:64,height:64,intrinsics:k,roiWeights:weights,
            referenceMask:requested,source:.rgbSelectedNormal,minimumReferenceDistance:3)
        XCTAssertEqual(r.deficitMl!,expected(0.304,0.3),accuracy:1e-8)
        XCTAssertEqual(r.excludedBoundaryReferenceSamples,near.filter{$0}.count)
        XCTAssertEqual(r.referencePolicy,"separated_chebyshev_native_v1")
        XCTAssertEqual(r.referenceClearancePixels,3); XCTAssertFalse(r.metricGroundTruthAllowed)
        XCTAssertEqual(z.map(\.bitPattern),before)
    }
    func testReferencePolicyLegacyDecodeRemainsSupported() throws {
        let r=try estimate(roi.map{$0 > 0 ? 0.304:0.3})
        let encoded=try JSONEncoder().encode(r)
        var object=try XCTUnwrap(JSONSerialization.jsonObject(with:encoded) as? [String:Any])
        for key in ["referencePolicy","referenceClearancePixels","excludedBoundaryReferenceSamples"] { object.removeValue(forKey:key) }
        let old=try JSONDecoder().decode(DepthReferenceVolume.Result.self,from:JSONSerialization.data(withJSONObject:object))
        XCTAssertNil(old.referencePolicy);XCTAssertEqual(old.deficitMl,r.deficitMl)
    }
    func testNativeResolutionNormalizedClearance() {
        XCTAssertEqual(DepthReferenceVolume.recommendedReferenceClearance(width:256,height:192),8)
        XCTAssertEqual(DepthReferenceVolume.recommendedReferenceClearance(width:320,height:240),10)
        XCTAssertEqual(DepthReferenceVolume.recommendedReferenceClearance(width:768,height:576),24)
    }
    func testBadReferenceSelectionCannotCreateMask() {
        XCTAssertThrowsError(try DepthReferenceVolume.separatedReferenceMask(width:64,height:64,roiWeights:[Double](repeating:0,count:4096)))
        XCTAssertThrowsError(try DepthReferenceVolume.separatedReferenceMask(width:64,height:64,roiWeights:roi,requested:[true]))
    }

}
