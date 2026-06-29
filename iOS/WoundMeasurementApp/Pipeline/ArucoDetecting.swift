import Foundation
import CoreGraphics

/**
 校正貼紙 ArUco 偵測（iOS）。
 注意：Apple Vision 無原生 ArUco；iOS 需以 **OpenCV(opencv2.framework)** 之 cv::aruco 實作，
 透過 Objective-C++ 橋接暴露給 Swift。本檔定義 Swift 介面與整合點；OpenCV 橋接由原生實作補上。

 介面：detect(image, wantId) → marker 四角 [x0,y0,x1,y1,x2,y2,x3,y3](TL,TR,BR,BL) 或 nil。
 字典/ID 取自 SSOT(DICT_4X4_50, id 7);面積以 WoundPipeline.areaCm2ByRatio(... markerMm: Preproc.markerMmActive)。
 偵測演算法即 cv::aruco.detectMarkers，與後端 aruco_calibrate.detect_marker 同。
 */
public protocol ArucoDetecting {
    func detect(_ image: CGImage, wantId: Int) -> [CGFloat]?
}

/// 預設佔位：未接 OpenCV 橋接時回 nil（面積=未校正，graceful）。
/// 實作範例（Obj-C++ 橋接）：
///   OpenCVAruco.detectMarkers(grayMat, dict: DICT_4X4_50) → 取 id==wantId 之 4 角。
public struct NoopArucoDetector: ArucoDetecting {
    public init() {}
    public func detect(_ image: CGImage, wantId: Int = 7) -> [CGFloat]? { nil }
}
