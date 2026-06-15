import Foundation
import Vision
import UIKit

final class StickerDetector {
    // 已知實際邊長 2.0 cm 的方形貼紙
    private let stickerSideCM: Double = 2.0
    
    /// 從影像偵測近似正方形的矩形框，回傳 cm/pixel（以最短邊像素作為邊長像素）
    func detectScaleCmPerPixel(in image: UIImage, completion: @escaping (Double?) -> Void) {
        guard let cg = image.cgImage else { completion(nil); return }
        let request = VNDetectRectanglesRequest()
        request.maximumObservations = 4
        request.minimumConfidence = 0.5
        request.minimumAspectRatio = 0.8
        request.maximumAspectRatio = 1.25
        request.quadratureTolerance = 20
        
        let handler = VNImageRequestHandler(cgImage: cg, options: [:])
        DispatchQueue.global(qos: .userInitiated).async {
            do {
                try handler.perform([request])
                guard let rect = request.results?.first as? VNRectangleObservation else {
                    DispatchQueue.main.async { completion(nil) }
                    return
                }
                // 取最短邊像素長度
                let w = hypot(rect.topRight.x - rect.topLeft.x, rect.topRight.y - rect.topLeft.y)
                let h = hypot(rect.topLeft.x - rect.bottomLeft.x, rect.topLeft.y - rect.bottomLeft.y)
                // 視為相對座標，換算成像素
                let pxW = Double(w) * Double(cg.width)
                let pxH = Double(h) * Double(cg.height)
                let pxSide = min(pxW, pxH)
                guard pxSide > 0 else { DispatchQueue.main.async { completion(nil) }; return }
                let cmPerPixel = stickerSideCM / pxSide
                DispatchQueue.main.async { completion(cmPerPixel) }
            } catch {
                DispatchQueue.main.async { completion(nil) }
            }
        }
    }
}
