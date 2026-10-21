import Foundation

/// Native-grid, camera-ray diagnostic integration shared verbatim with WoundAI3D.
/// A small fit residual does NOT establish a healthy reference surface, correct
/// registration, or sensor accuracy. Results never certify measurement/training truth.
public enum DepthReferenceVolume {
    public enum ReferenceSource: String, Codable, Sendable {
        case automaticRing, rgbSelectedNormal
    }
    public enum Failure: Error { case invalidInput, insufficientReference, degenerateReference, invalidPlane }
    public struct Intrinsics: Sendable {
        public let fx: Double, fy: Double, cx: Double, cy: Double
        public init(fx: Double, fy: Double, cx: Double, cy: Double) {
            self.fx = fx; self.fy = fy; self.cx = cx; self.cy = cy
        }
    }
    public struct Result: Codable, Sendable {
        public let schema: String
        public let referenceSource: ReferenceSource
        /// Plane convention: a*X + b*Y + c*Z = 1 in camera metres.
        public let inverseDepthPlane: [Double]
        /// Optional for decoding historical results. Native grid Chebyshev clearance;
        /// a separation guard is not proof of normal skin or physical accuracy.
        public let referencePolicy: String?
        public let referenceClearancePixels: Int?
        public let excludedBoundaryReferenceSamples: Int?
        public let referenceSamples: Int
        public let referenceCoverage: Double
        public let baselineRMSMm: Double
        public let roiCoverage: Double
        /// Partial sums retained for debugging only; never filled/extrapolated.
        public let observedDeficitMl: Double, observedExcessMl: Double
        /// Complete diagnostic volumes, withheld if any ROI cell is missing.
        public let deficitMl: Double?, excessMl: Double?
        public let maxDeficitMm: Double?, maxExcessMm: Double?
        public let status: String
        public let metricGroundTruthAllowed: Bool
    }

    /// Fixed engineering guard, resolution-normalized; never tuned to measured height.
    public static func recommendedReferenceClearance(width: Int, height: Int) -> Int {
        max(2, Int(ceil(Double(min(width, height)) * 24 / 576)))
    }

    /// O(width*height) eight-neighbour distance to ANY wound component. Pixel 0
    /// belongs to the ROI. The resulting square distance is explicitly declared,
    /// not mislabelled as Euclidean millimetres or a healthy-skin classifier.
    private static func boundaryDistances(width: Int, height: Int, roiWeights: [Double]) throws -> [Int] {
        guard width > 0, height > 0, width <= 4096, height <= 4096,
              roiWeights.count == width * height,
              roiWeights.allSatisfy({ $0.isFinite && (0...1).contains($0) }),
              roiWeights.contains(where: { $0 > 0 }) else { throw Failure.invalidInput }
        let infinity = width + height + 1
        var distance = roiWeights.map { $0 > 0 ? 0 : infinity }
        for y in 0..<height { for x in 0..<width {
            let i = y*width+x
            if x > 0 { distance[i] = min(distance[i],distance[i-1]+1) }
            if y > 0 {
                distance[i] = min(distance[i],distance[i-width]+1)
                if x > 0 { distance[i] = min(distance[i],distance[i-width-1]+1) }
                if x+1 < width { distance[i] = min(distance[i],distance[i-width+1]+1) }
            }
        } }
        for y in stride(from:height-1,through:0,by:-1) { for x in stride(from:width-1,through:0,by:-1) {
            let i = y*width+x
            if x+1 < width { distance[i] = min(distance[i],distance[i+1]+1) }
            if y+1 < height {
                distance[i] = min(distance[i],distance[i+width]+1)
                if x > 0 { distance[i] = min(distance[i],distance[i+width-1]+1) }
                if x+1 < width { distance[i] = min(distance[i],distance[i+width+1]+1) }
            }
        } }
        return distance
    }

    /// Explicit selections are only reduced, never expanded. Automatic selection
    /// is a distant candidate annulus [clearance, 2*clearance]; no close-ring fallback.
    public static func separatedReferenceMask(width: Int, height: Int, roiWeights: [Double],
                                             requested: [Bool]? = nil) throws -> [Bool] {
        let d = try boundaryDistances(width: width,height: height,roiWeights: roiWeights)
        if let requested, requested.count != d.count { throw Failure.invalidInput }
        let gap = recommendedReferenceClearance(width: width,height: height)
        return d.indices.map { d[$0] >= gap && (requested?[$0] ?? (d[$0] <= gap*2)) }
    }

    /// Exact little-endian Float32 decoding; no millimetre quantisation or hole fill.
    public static func decodeMetres(_ data: Data, count: Int) throws -> [Float] {
        guard count > 0, count <= 4096 * 4096, data.count == count * 4 else { throw Failure.invalidInput }
        return data.withUnsafeBytes { bytes in
            (0..<count).map { Float(bitPattern: UInt32(littleEndian: bytes.loadUnaligned(fromByteOffset: $0 * 4, as: UInt32.self))) }
        }
    }

    /// K, ROI weights, and reference mask MUST already use the same native depth
    /// grid and pixel origin. This method does not infer RGB registration or units.
    /// Valid range is an explicit diagnostic input filter, not an accuracy claim.
    public static func estimate(metres: [Float], width: Int, height: Int,
                                intrinsics k: Intrinsics, roiWeights: [Double],
                                referenceMask: [Bool], source: ReferenceSource,
                                minimumReferenceDistance: Int = 0) throws -> Result {
        guard width > 0, height > 0, width <= 4096, height <= 4096,
              metres.count == width * height, roiWeights.count == metres.count,
              referenceMask.count == metres.count, minimumReferenceDistance >= 0, minimumReferenceDistance <= max(width,height),
              [k.fx, k.fy, k.cx, k.cy].allSatisfy({ $0.isFinite }), k.fx > 0, k.fy > 0,
              roiWeights.allSatisfy({ $0.isFinite && (0...1).contains($0) }),
              !roiWeights.indices.contains(where: { roiWeights[$0] > 0 && referenceMask[$0] })
        else { throw Failure.invalidInput }
        func valid(_ i: Int) -> Bool { metres[i].isFinite && metres[i] > 0.05 && metres[i] < 5 }
        func ray(_ i: Int) -> (Double, Double) {
            ((Double(i % width) - k.cx) / k.fx, (Double(i / width) - k.cy) / k.fy)
        }
        let roi = roiWeights.indices.filter { roiWeights[$0] > 0 }
        let requested = referenceMask.indices.filter { referenceMask[$0] }
        let distances = minimumReferenceDistance > 0 ? try boundaryDistances(width: width,height: height,roiWeights: roiWeights) : nil
        let separated = requested.filter { distances == nil || distances![$0] >= minimumReferenceDistance }
        let selected = separated.filter { valid($0) }
        guard !roi.isEmpty else { throw Failure.invalidInput }
        guard selected.count >= 12 else { throw Failure.insufficientReference }
        // Centre and scale coordinates before solving to avoid pixel-origin
        // dependent conditioning. Fit inverse axial depth: a true 3D plane.
        let n = Double(selected.count)
        let mu = selected.reduce(0.0) { $0 + ray($1).0 } / n
        let mv = selected.reduce(0.0) { $0 + ray($1).1 } / n
        let mt = selected.reduce(0.0) { $0 + 1 / Double(metres[$1]) } / n
        var uu = 0.0, vv = 0.0, uv = 0.0, ut = 0.0, vt = 0.0
        for i in selected {
            let (u, v) = ray(i); let du = u - mu, dv = v - mv
            let dt = 1 / Double(metres[i]) - mt
            uu += du * du; vv += dv * dv; uv += du * dv; ut += du * dt; vt += dv * dt
        }
        let sx = sqrt(uu / n), sy = sqrt(vv / n)
        guard sx > 1e-12, sy > 1e-12 else { throw Failure.degenerateReference }
        let correlation = uv / (n * sx * sy)
        guard 1 - correlation * correlation > 1e-8 else { throw Failure.degenerateReference }
        let du = ut / (n * sx), dv = vt / (n * sy)
        let a = (du - correlation * dv) / (1 - correlation * correlation) / sx
        let b = (dv - correlation * du) / (1 - correlation * correlation) / sy
        let c = mt - a * mu - b * mv
        let norm = sqrt(a * a + b * b + c * c)
        guard norm.isFinite, norm > 0 else { throw Failure.invalidPlane }
        func inverseZ(_ i: Int) -> Double { let (u, v) = ray(i); return a * u + b * v + c }
        // Reject a plane crossing/behind the camera anywhere in the requested ROI.
        guard (roi + selected).allSatisfy({ inverseZ($0).isFinite && inverseZ($0) > 0 }) else { throw Failure.invalidPlane }
        let rms = sqrt(selected.reduce(0.0) {
            let d = (Double(metres[$1]) * inverseZ($1) - 1) / norm * 1000
            return $0 + d * d
        } / n)
        var deficit = 0.0, excess = 0.0, deepest = 0.0, highest = 0.0
        var observedWeight = 0.0
        let totalWeight = roi.reduce(0.0) { $0 + roiWeights[$1] }
        var complete = true
        for i in roi {
            guard valid(i) else { complete = false; continue }
            let z = Double(metres[i]), ref = 1 / inverseZ(i), w = roiWeights[i]
            // Integrate ray Jacobian Z²/(fx*fy) between plane and observed surface.
            // Factorised cubic difference avoids cancellation for shallow relief.
            let signedMl = w * (z - ref) * (z * z + z * ref + ref * ref) / (3 * k.fx * k.fy) * 1e6
            let normalMm = (z * inverseZ(i) - 1) / norm * 1000
            deficit += max(0, signedMl); excess += max(0, -signedMl)
            deepest = max(deepest, normalMm); highest = max(highest, -normalMm)
            observedWeight += w
        }
        guard [deficit, excess, deepest, highest, rms].allSatisfy({ $0.isFinite }) else { throw Failure.invalidPlane }
        return Result(schema: "native-reference-volume/1", referenceSource: source,
                      inverseDepthPlane: [a, b, c],
                      referencePolicy: minimumReferenceDistance > 0 ? "separated_chebyshev_native_v1" : "explicit_input_legacy",
                      referenceClearancePixels: minimumReferenceDistance,
                      excludedBoundaryReferenceSamples: requested.count - separated.count,
                      referenceSamples: selected.count,
                      referenceCoverage: n / Double(requested.count), baselineRMSMm: rms,
                      roiCoverage: observedWeight / totalWeight,
                      observedDeficitMl: deficit, observedExcessMl: excess,
                      deficitMl: complete ? deficit : nil, excessMl: complete ? excess : nil,
                      maxDeficitMm: complete ? deepest : nil, maxExcessMm: complete ? highest : nil,
                      status: complete ? "unverified_reference_and_registration" : "incomplete_depth",
                      metricGroundTruthAllowed: false)
    }
}
