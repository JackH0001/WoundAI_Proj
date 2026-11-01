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

/// The initial contour persists even if the user subsequently edits its boundary.
struct LiteContourOrigin: Codable, Equatable, Sendable {
    let method: String
    var route: String? = nil
    var outputContract: String? = nil

    static let manual = Self(method: "manual")
    static let local = Self(method: "local")
    static let studentV1 = "student-onnx-logits-sigmoid-before-resize/v1"

    static func cloud(route: String?, contract: String?) -> Self {
        Self(method: "cloud", route: route.flatMap { $0.utf8.count <= 64 ? $0 : nil },
             outputContract: contract.flatMap { $0.utf8.count <= 128 ? $0 : nil })
    }

    var label: String {
        if self == .manual { return "手動圈選" }
        if method == "cloud", outputContract == Self.studentV1 {
            if route == "student" { return "雲端圈選（Student 第 1 版）" }
            if route == "cloud_escalated(AU)" { return "雲端集成圈選（Student 第 1 版）" }
        }
        return "圈選版本未確認"
    }

    var supportsComparison: Bool {
        self == .manual || (method == "cloud" && outputContract == Self.studentV1
            && (route == "student" || route == "cloud_escalated(AU)"))
    }
}

enum LiteAreaSeries: String, CaseIterable, Identifiable {
    case current = "本版 LiDAR 表面積"
    case legacy = "舊版 LiDAR 表面積"
    var id: String { rawValue }
}

/// What the saved measurement supports displaying. Legacy numbers belong to
/// their original history series only. A PDF also requires matching photo pixels.
enum LiteSavedArea {
    case current(LiteVerdict)
    case legacy
    case unavailable(String)

    var series: LiteAreaSeries? {
        switch self {
        case .current: .current
        case .legacy: .legacy
        case .unavailable: nil
        }
    }
}

extension LiteRecord {
    var savedArea: LiteSavedArea {
        guard let context = measurementContext else {
            guard surfaceCm2.isFinite, projectedCm2.isFinite, surfaceCm2 >= 0, projectedCm2 >= 0
            else { return .unavailable("舊紀錄的面積數值無法核對。") }
            return .legacy
        }
        guard context.isKnownSurface else { return .unavailable("這筆紀錄的面積來源無法核對。") }
        guard context.orientation == "normalized",
              (8...4096).contains(context.depthWidth), (8...4096).contains(context.depthHeight),
              (1...16384).contains(context.imageWidth), (1...16384).contains(context.imageHeight)
        else { return .unavailable("缺少可核對的影像方向或尺寸。") }
        let depthAspect = Double(context.depthWidth) / Double(context.depthHeight)
        let imageAspect = Double(context.imageWidth) / Double(context.imageHeight)
        guard abs(depthAspect / imageAspect - 1) <= 0.01 else {
            return .unavailable("保存的影像與深度圖比例不符。")
        }
        let verdict = liteVerdict(DepthAreaResult(surfaceAreaCm2: surfaceCm2,
            projectedAreaCm2: projectedCm2, coverage: context.coverage,
            medianDistanceM: context.distanceM, tiltDeg: tiltDeg))
        if verdict.usable { return .current(verdict) }
        return .unavailable(verdict.blocker ?? "深度品質資訊不足。")
    }

    var areaSeries: LiteAreaSeries? { savedArea.series }
}
