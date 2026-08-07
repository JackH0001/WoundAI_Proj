import Foundation
import AVFoundation
import CoreImage
import UIKit

final class LiveCameraAnalyzer: NSObject {
    struct Result {
        let maskImage: CGImage?
        let pixelArea: Int
        let cm2Area: Double?
    }
    
    private let ciContext = CIContext()
    private let inferenceQueue = DispatchQueue(label: "live.camera.inference", qos: .userInitiated)
    private var lastProcessTime: TimeInterval = 0
    private let minInterval: TimeInterval = 0.25 // 4 FPS 推論上限
    
    private let engine = SegmentationEngineCoreML()
    private var cmPerPixel: Double? // 由貼紙偵測導入（2cm / stickerPixels）
    
    func updateScale(cmPerPixel: Double?) {
        self.cmPerPixel = cmPerPixel
    }
    
    func process(pixelBuffer: CVPixelBuffer, completion: @escaping (Result?) -> Void) {
        let now = CACurrentMediaTime()
        if now - lastProcessTime < minInterval { return }
        lastProcessTime = now
        
        inferenceQueue.async { [weak self] in
            guard let self = self else { return }
            let image = Self.uiImage(from: pixelBuffer)
            guard let uiImage = image else {
                DispatchQueue.main.async { completion(nil) }
                return
            }
            // 以 letterbox 前處理產生模型輸入並推論（內部使用 engine 目前的縮放；若要嚴格 letterbox，先產生 letterboxed CGImage）
            // 此處簡化：直接用現有 engine 的 predict，後續可擴充為 letterbox 封裝
            guard let out = self.engine.predictMask(from: uiImage, threshold: 0.5) else {
                DispatchQueue.main.async { completion(nil) }
                return
            }
            
            // 計算像素面積（白色像素數）
            let pixelArea = Self.countWhitePixels(in: out.mask)
            
            // cm²（若有比例）
            let cm2: Double? = {
                guard let cmpp = self.cmPerPixel else { return nil }
                return Double(pixelArea) * cmpp * cmpp
            }()
            
            let res = Result(maskImage: out.mask, pixelArea: pixelArea, cm2Area: cm2)
            DispatchQueue.main.async { completion(res) }
        }
    }
    
    static func uiImage(from pixelBuffer: CVPixelBuffer) -> UIImage? {
        let ci = CIImage(cvPixelBuffer: pixelBuffer)
        let context = CIContext()
        guard let cg = context.createCGImage(ci, from: ci.extent) else { return nil }
        return UIImage(cgImage: cg)
    }
    
    private static func countWhitePixels(in cgImage: CGImage) -> Int {
        guard let data = cgImage.dataProvider?.data else { return 0 }
        let ptr = CFDataGetBytePtr(data)
        let width = cgImage.width
        let height = cgImage.height
        let bpp = max(1, cgImage.bitsPerPixel / 8)
        let rowBytes = cgImage.bytesPerRow
        var count = 0
        for y in 0..<height {
            let row = ptr! + y * rowBytes
            for x in 0..<width {
                let v = row[x * bpp]
                if v > 127 { count += 1 }
            }
        }
        return count
    }
}
