import Foundation

/// Local measurement provenance. Does not change existing cloud keys or claim
/// verified optical registration. Contour source remains in LiteRecord.source.
struct LiteMeasurementContext: Codable, Equatable {
    static let algorithm = "lidar_surface_box8mm_v1"
    var schema: String = "lite.measurement/1"
    var areaSource: String = "lidar_triangulated_surface"
    var projectionSource: String = "lidar_camera_projection"
    var areaUnit: String = "cm2"
    var algorithm: String = Self.algorithm
    var registration: String = "not_verified"
    var orientation: String
    var filtered: Bool
    var depthWidth: Int
    var depthHeight: Int
    var imageWidth: Int
    var imageHeight: Int
    var coverage: Double
    var distanceM: Double
    var warnings: [String]
    var volumeStatus: String = "research_only_not_physically_validated"

    init(depth: DepthCapture, result: DepthAreaResult, warnings: [String]) {
        orientation = depth.sourceExifOrientation.map { (1...8).contains($0) } == true
            ? "normalized" : "unknown"
        filtered = depth.filtered
        depthWidth = depth.width; depthHeight = depth.height
        imageWidth = depth.rgbWidth; imageHeight = depth.rgbHeight
        coverage = result.coverage; distanceM = result.medianDistanceM
        self.warnings = warnings
    }

    var isKnownSurface: Bool {
        schema == "lite.measurement/1" && areaSource == "lidar_triangulated_surface"
            && projectionSource == "lidar_camera_projection" && areaUnit == "cm2"
            && algorithm == Self.algorithm && registration == "not_verified"
    }
}

enum LiteAreaSeries: String, CaseIterable, Identifiable {
    case current = "本版 LiDAR 表面積"
    case legacy = "舊版 LiDAR 表面積"
    var id: String { rawValue }
}

extension LiteRecord {
    var areaSeries: LiteAreaSeries? {
        guard let measurementContext else { return .legacy }
        return measurementContext.isKnownSurface ? .current : nil
    }
}
