import Foundation
import CoreGraphics

/**
 量測畫面 ViewModel(SwiftUI ObservableObject)：拍攝後 → ArUco 偵測 → WoundAnalyzer
 (分割→雙軌→面積→組織v2→PUSH) → 發布 UI 狀態。輔助、非診斷、需醫師確認。
 */
@MainActor
public final class MeasureViewModel: ObservableObject {
    @Published public private(set) var loading = false
    @Published public private(set) var result: MeasureResult?
    @Published public private(set) var error: String?

    private let analyzer: WoundAnalyzer
    private let aruco: ArucoDetecting

    public init(analyzer: WoundAnalyzer, aruco: ArucoDetecting = NoopArucoDetector()) {
        self.analyzer = analyzer; self.aruco = aruco
    }

    /// - Parameters:
    ///   - image: 拍攝原圖(含校正貼紙)
    ///   - exudate: 滲液(醫師輸入 0–3)或 nil
    ///   - cloudEscalate: 難例上雲(呼叫 /api/v1/segment/escalate);nil 則純端上
    public func analyze(image: CGImage, exudate: Int?,
                        cloudEscalate: ((CGImage) async -> [Bool])? = nil) async {
        loading = true; error = nil
        let corners = aruco.detect(image, wantId: 7)   // nil → 面積未校正(graceful)
        let r = await analyzer.run(image: image, markerCorners: corners,
                                   exudate: exudate, cloudEscalate: cloudEscalate)
        result = r; loading = false
    }
}
