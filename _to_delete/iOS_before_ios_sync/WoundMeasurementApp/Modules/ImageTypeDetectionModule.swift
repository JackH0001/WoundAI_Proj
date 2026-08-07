import UIKit
import ImageIO

enum ImageTypeDetectionModule {
    // MARK: - 介面型別
    enum Strategy: String, CaseIterable {
        case arDepthImage = "AR深度影像"
        case flatImageWithSticker = "平面影像（含校正貼紙）"
        case flatImageEstimated = "平面影像（估計尺度）"
        case unknown = "未知類型"
    }

    struct Result {
        let strategy: Strategy
        let confidence: Double
        let method: String
        let details: [String: Any]
    }

    struct Config {
        // AR 深度檢測參數
        static let arMinResolution: Double = 1440 * 1080
        static let arAspectRatioTolerance: Double = 0.15
        static let arTargetAspectRatio: Double = 4.0 / 3.0

        // 貼紙檢測參數
        static let stickerEdgeThreshold: Double = 60.0

        // 平面影像檢測參數
        static let commonAspectRatios: [Double] = [16.0/9.0, 4.0/3.0, 3.0/2.0, 1.0/1.0, 9.0/16.0, 3.0/4.0]
        static let aspectRatioTolerance: Double = 0.15
        static let maxFlatImagePixels: Double = 2560 * 1440
    }

    // MARK: - 對外 API
    static func analyzeImageType(_ image: UIImage) -> Strategy {
        let t0 = CFAbsoluteTimeGetCurrent()
        let detectionResults = performMultiAlgorithmDetection(image)
        if let best = detectionResults.max(by: { $0.confidence < $1.confidence }) {
            let dt = CFAbsoluteTimeGetCurrent() - t0
            print("✅ 最佳檢測結果: \(best.strategy.rawValue)")
            print("   🎯 信心度: \(String(format: "%.3f", best.confidence))")
            print("   ⏱️ 處理時間: \(String(format: "%.3f", dt))秒")
            print("   🔬 算法: \(best.method)")
            return best.strategy
        }
        print("⚠️ 所有算法檢測信心度過低，使用備用處理策略")
        return .unknown
    }

    static func performMultiAlgorithmDetection(_ image: UIImage) -> [Result] {
        var results: [Result] = []
        // AR 深度影像檢測
        results.append(detectARDepthByMetadata(image))
        results.append(detectARDepthByResolution(image))
        results.append(detectARDepthByColorSpace(image))
        // 校正貼紙檢測
        results.append(detectStickerByEdges(image))
        results.append(detectStickerByCircularPattern(image))
        // 平面影像檢測
        results.append(detectFlatImageByAspectRatio(image))
        results.append(detectFlatImageByResolution(image))
        // 過濾過低信心
        return results.filter { $0.confidence >= 0.3 }
    }

    // MARK: - 檢測算法
    private static func detectARDepthByMetadata(_ image: UIImage) -> Result {
        var confidence: Double = 0.0
        var details: [String: Any] = [:]

        if let imageData = image.pngData(),
           let src = CGImageSourceCreateWithData(imageData as CFData, nil),
           let props = CGImageSourceCopyPropertiesAtIndex(src, 0, nil) as? [String: Any] {
            details["hasProperties"] = true
            if let exif = props[kCGImagePropertyExifDictionary as String] as? [String: Any] {
                details["hasEXIF"] = true
                if let userComment = exif["UserComment"] as? String, userComment.contains("DEPTH") {
                    confidence += 0.5
                }
                if let software = exif["Software"] as? String, software.contains("ARKit") {
                    confidence += 0.3
                }
            }
        }
        let final = max(0.0, min(1.0, confidence * 0.9))
        return Result(strategy: .arDepthImage, confidence: final, method: "EXIF元數據檢測", details: details)
    }

    private static func detectARDepthByResolution(_ image: UIImage) -> Result {
        let size = image.size
        let totalPixels = size.width * size.height
        let aspect = size.width / size.height
        var confidence: Double = 0.0

        if totalPixels >= Config.arMinResolution { confidence += 0.4 }
        let aspectDiff = abs(aspect - Config.arTargetAspectRatio)
        if aspectDiff <= Config.arAspectRatioTolerance { confidence += 0.3 }

        let final = max(0.0, min(1.0, confidence * 0.9))
        return Result(strategy: .arDepthImage, confidence: final, method: "解析度/比例檢測", details: [
            "width": size.width,
            "height": size.height,
            "totalPixels": totalPixels,
            "aspectRatio": aspect
        ])
    }

    private static func detectARDepthByColorSpace(_ image: UIImage) -> Result {
        guard let cg = image.cgImage else {
            return Result(strategy: .arDepthImage, confidence: 0.0, method: "色彩空間檢測", details: ["error": "無法獲取CGImage"]) 
        }
        var confidence: Double = 0.0
        if cg.bitsPerComponent >= 8 { confidence += 0.3 }
        if cg.bitsPerPixel >= 24 { confidence += 0.2 }
        if let cs = cg.colorSpace, cs.model == .rgb { confidence += 0.4 }
        let final = max(0.0, min(1.0, confidence * 0.8))
        return Result(strategy: .arDepthImage, confidence: final, method: "色彩空間檢測", details: [:])
    }

    private static func detectStickerByEdges(_ image: UIImage) -> Result {
        guard let cg = image.cgImage else {
            return Result(strategy: .flatImageWithSticker, confidence: 0.0, method: "邊緣檢測", details: [:])
        }
        let edge = analyzeEdgePatterns(cg)
        var confidence: Double = 0.0
        if edge > Config.stickerEdgeThreshold {
            confidence = min(1.0, edge / 200.0)
        } else if edge > 40.0 {
            confidence = edge / 300.0
        }
        let final = max(0.0, min(1.0, confidence * 0.7))
        return Result(strategy: .flatImageWithSticker, confidence: final, method: "邊緣檢測", details: ["edgeStrength": edge])
    }

    private static func detectStickerByCircularPattern(_ image: UIImage) -> Result {
        guard let cg = image.cgImage else {
            return Result(strategy: .flatImageWithSticker, confidence: 0.0, method: "圓形圖案檢測", details: [:])
        }
        let circ = analyzeCircularPatterns(cg)
        var confidence: Double = 0.0
        if circ > 120.0 { confidence = min(1.0, circ / 200.0) }
        else if circ > 80.0 { confidence = circ / 300.0 }
        let final = max(0.0, min(1.0, confidence * 0.6))
        return Result(strategy: .flatImageWithSticker, confidence: final, method: "圓形圖案檢測", details: ["circularStrength": circ])
    }

    private static func detectFlatImageByAspectRatio(_ image: UIImage) -> Result {
        let aspect = image.size.width / image.size.height
        var confidence: Double = 0.0
        var best: (ratio: Double, diff: Double)?
        for r in Config.commonAspectRatios {
            let diff = abs(aspect - r)
            if diff <= Config.aspectRatioTolerance {
                if best == nil || diff < best!.diff { best = (r, diff) }
            }
        }
        if let m = best { confidence = max(0.3, 1.0 - (m.diff / Config.aspectRatioTolerance)) }
        let final = max(0.0, min(1.0, confidence * 0.7))
        return Result(strategy: .flatImageEstimated, confidence: final, method: "長寬比檢測", details: ["aspectRatio": aspect])
    }

    private static func detectFlatImageByResolution(_ image: UIImage) -> Result {
        let total = image.size.width * image.size.height
        var confidence: Double = 0.0
        if total <= Config.maxFlatImagePixels {
            let ideal = 1920.0 * 1080.0
            let diff = abs(total - ideal)
            confidence = max(0.2, 1.0 - (diff / ideal))
        }
        let final = max(0.0, min(1.0, confidence * 0.7))
        return Result(strategy: .flatImageEstimated, confidence: final, method: "解析度檢測", details: ["totalPixels": total])
    }

    // MARK: - 低階像素分析
    private static func analyzeEdgePatterns(_ cgImage: CGImage) -> Double {
        let width = cgImage.width
        let height = cgImage.height
        guard let provider = cgImage.dataProvider,
              let data = provider.data,
              let buf = CFDataGetBytePtr(data) else { return 0.0 }
        var strength: Double = 0.0
        let cx = width / 2
        let cy = height / 2
        let radius = min(width, height) / 8
        for i in 0..<32 {
            let angle = Double(i) * 2.0 * Double.pi / 32.0
            let x = cx + Int(cos(angle) * Double(radius))
            let y = cy + Int(sin(angle) * Double(radius))
            if x >= 0 && x < width && y >= 0 && y < height {
                let idx = (y * width + x) * 4
                if idx < width * height * 4 { strength += Double(buf[idx]) }
            }
        }
        return strength / 32.0
    }

    private static func analyzeCircularPatterns(_ cgImage: CGImage) -> Double {
        let width = cgImage.width
        let height = cgImage.height
        guard let provider = cgImage.dataProvider,
              let data = provider.data,
              let buf = CFDataGetBytePtr(data) else { return 0.0 }
        var total: Double = 0.0
        let cx = width / 2
        let cy = height / 2
        let radii = [min(width, height) / 8, min(width, height) / 6]
        for r in radii {
            var s: Double = 0.0
            for i in 0..<24 {
                let angle = Double(i) * 2.0 * Double.pi / 24.0
                let x = cx + Int(cos(angle) * Double(r))
                let y = cy + Int(sin(angle) * Double(r))
                if x >= 0 && x < width && y >= 0 && y < height {
                    let idx = (y * width + x) * 4
                    if idx < width * height * 4 { s += Double(buf[idx]) }
                }
            }
            total += s / 24.0
        }
        return total / Double(radii.count)
    }
}


