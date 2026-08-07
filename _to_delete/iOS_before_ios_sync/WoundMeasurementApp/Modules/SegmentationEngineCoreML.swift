import Foundation
import UIKit
import CoreML
import CoreImage
import VideoToolbox

/// CoreML 版傷口分割引擎（StudentSeg 蒸餾學生；imagenet 正規化+sigmoid 已內建於模型）
/// - 輸入: 任意尺寸 UIImage（自動縮放至模型輸入尺寸）
/// - 前處理: 使用 CoreML 的 ImageType 正規化 (x/127.5 - 1.0)
/// - 輸出: 概率圖 (Float) 與二值遮罩 CGImage（使用閾值 threshold）
final class SegmentationEngineCoreML {
    struct OutputMask {
        let mask: CGImage
        let probability: MLMultiArray?
        let width: Int
        let height: Int
    }

    private let model: MLModel
    private let inputWidth: Int
    private let inputHeight: Int
    private let ciContext = CIContext()

    init?(bundle: Bundle = .main) {
        // 尋找已編譯的 .mlmodelc
        guard let url = bundle.url(forResource: "StudentSeg", withExtension: "mlmodelc") else {
            return nil
        }
        do {
            self.model = try MLModel(contentsOf: url)
        } catch {
            return nil
        }
        // 從 model description 取得輸入解析度（若不可得，預設 256)
        if let inDesc = model.modelDescription.inputDescriptionsByName.values.first,
           let imgConstraint = inDesc.imageConstraint {
            self.inputWidth = Int(imgConstraint.pixelsWide)
            self.inputHeight = Int(imgConstraint.pixelsHigh)
        } else {
            self.inputWidth = 256
            self.inputHeight = 256
        }
    }

    // MARK: - Sprint S1 constants
    // SSOT student 門檻 0.40(蒸餾學生;模型輸出已 sigmoid 機率)
    static let optimizedThreshold: Float = 0.40   // SSOT student thr0.4

    /// 主推論：4-fold TTA + 優化閾值（Sprint S1）
    /// - threshold: 二值化閾值，預設 0.30（優化後，原為 0.5）
    /// - tta: 是否啟用 4-fold TTA（orig + h-flip + v-flip + 90°CW）
    func predictMask(from image: UIImage,
                     threshold: Float = SegmentationEngineCoreML.optimizedThreshold,
                     tta: Bool = true) -> OutputMask? {
        guard tta else { return predictMaskSingle(from: image, threshold: threshold) }

        // 4-fold TTA：收集四個方向的概率圖
        let augmented: [UIImage] = [
            image,
            image.flipped(horizontally: true),
            image.flipped(horizontally: false),
            image.rotated90CW()
        ]
        var probArrays: [MLMultiArray] = []
        var size: (Int, Int) = (256, 256)
        for (i, aug) in augmented.enumerated() {
            guard let result = predictMaskSingle(from: aug, threshold: threshold),
                  let prob = result.probability else { continue }
            var probToUse = prob
            // 反轉增強（h-flip / v-flip / 旋轉逆轉）
            if i == 1 { probToUse = prob.flipped(axis: .width)  ?? prob }
            if i == 2 { probToUse = prob.flipped(axis: .height) ?? prob }
            if i == 3 { probToUse = prob.rotated90CCW()         ?? prob }
            probArrays.append(probToUse)
            size = (result.width, result.height)
        }
        guard !probArrays.isEmpty else { return predictMaskSingle(from: image, threshold: threshold) }

        // 平均概率圖
        guard let averaged = MLMultiArray.average(probArrays) else {
            return predictMaskSingle(from: image, threshold: threshold)
        }
        return binarize(prob: averaged, originalSize: size, threshold: threshold)
    }

    /// 單次推論（TTA 關閉時，或 TTA 內部使用）
    func predictMaskSingle(from image: UIImage, threshold: Float = SegmentationEngineCoreML.optimizedThreshold) -> OutputMask? {
        guard let cg = image.cgImage else { return nil }
        guard let pb = Self.createPixelBuffer(from: cg, width: inputWidth, height: inputHeight) else { return nil }

        // 構建特徵輸入（名稱使用模型第一個輸入名）
        guard let inputName = model.modelDescription.inputDescriptionsByName.keys.first else { return nil }
        let input = try? MLDictionaryFeatureProvider(dictionary: [inputName: MLFeatureValue(pixelBuffer: pb)])
        guard let inputFP = input else { return nil }

        // 推論
        guard let out = try? model.prediction(from: inputFP) else { return nil }
        guard let (_, feat) = out.featureNames.first.flatMap({ ($0, out.featureValue(for: $0)) }) else { return nil }

        // 取得概率 0..1 的單通道圖
        let probImage: CIImage?
        var probArray: MLMultiArray? = nil
        if feat.type == .multiArray, let arr = feat.multiArrayValue {
            probArray = arr
            probImage = Self.multiArrayToCIImage(arr)
        } else if feat.type == .image, let pbOut = feat.imageBufferValue {
            probImage = CIImage(cvPixelBuffer: pbOut)
        } else {
            // 無法識別的輸出格式
            return nil
        }
        guard let probCI = probImage else { return nil }

        // 將概率圖縮放到原始圖尺寸
        let targetW = cg.width
        let targetH = cg.height
        let scaledProb = probCI.transformed(by: CGAffineTransform(scaleX: CGFloat(targetW) / probCI.extent.width,
                                                                  y: CGFloat(targetH) / probCI.extent.height))

        // 閾值二值化
        // 將概率圖視為灰階，閾值 threshold
        let clamp = CIFilter(name: "CIColorClamp", parameters: ["inputImage": scaledProb,
                                                                 "inputMinComponents": CIVector(x: 0, y: 0, z: 0, w: 0),
                                                                 "inputMaxComponents": CIVector(x: 1, y: 1, z: 1, w: 1)])!.outputImage!
        let thresh = CIFilter(name: "CIColorThreshold", parameters: ["inputImage": clamp,
                                                                      "inputThreshold": threshold])?.outputImage
            ?? Self.simpleThreshold(image: clamp, threshold: threshold)

        guard let bin = thresh, let outCG = ciContext.createCGImage(bin, from: CGRect(x: 0, y: 0, width: targetW, height: targetH)) else {
            return nil
        }

        // 後處理：保留最大連通區塊，避免背景被整片判白
        if let largest = Self.keepLargestComponent(from: outCG, context: ciContext) {
            return OutputMask(mask: largest, probability: probArray, width: targetW, height: targetH)
        } else {
            return OutputMask(mask: outCG, probability: probArray, width: targetW, height: targetH)
        }
    }

    // MARK: - Helpers

    private static func createPixelBuffer(from cgImage: CGImage, width: Int, height: Int) -> CVPixelBuffer? {
        var pb: CVPixelBuffer?
        let attrs: [CFString: Any] = [kCVPixelBufferCGImageCompatibilityKey: true,
                                      kCVPixelBufferCGBitmapContextCompatibilityKey: true]
        let status = CVPixelBufferCreate(kCFAllocatorDefault, width, height, kCVPixelFormatType_32BGRA, attrs as CFDictionary, &pb)
        guard status == kCVReturnSuccess, let pixelBuffer = pb else { return nil }
        CVPixelBufferLockBaseAddress(pixelBuffer, [])
        defer { CVPixelBufferUnlockBaseAddress(pixelBuffer, []) }

        guard let base = CVPixelBufferGetBaseAddress(pixelBuffer) else { return nil }
        let bytesPerRow = CVPixelBufferGetBytesPerRow(pixelBuffer)
        guard let ctx = CGContext(data: base, width: width, height: height, bitsPerComponent: 8, bytesPerRow: bytesPerRow, space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedFirst.rawValue | CGBitmapInfo.byteOrder32Little.rawValue) else {
            return nil
        }
        ctx.interpolationQuality = .high
        ctx.draw(cgImage, in: CGRect(x: 0, y: 0, width: width, height: height))
        return pixelBuffer
    }

    private static func multiArrayToCIImage(_ arr: MLMultiArray) -> CIImage? {
        // 嘗試將形狀解析為 (H,W) 或 (1,H,W,1) 或 (H,W,1)
        let shape = arr.shape.map { Int(truncating: $0) }
        let count = arr.count
        // 推定 H,W
        var h = 256
        var w = 256
        if shape.count >= 2 {
            // 從末兩軸推估
            h = shape[shape.count - 3 >= 0 ? shape.count - 3 : 0]
            w = shape[shape.count - 2 >= 0 ? shape.count - 2 : 1]
            if h * w != count { h = 256; w = count / max(1, h) }
        }
        // 建立灰階位圖
        let ptr = arr.dataPointer.bindMemory(to: Float.self, capacity: count)
        // 正規化到 0..1
        let buf = UnsafeBufferPointer(start: ptr, count: count)
        let data = buf.map { min(max($0, 0.0), 1.0) }
        // 轉成 8-bit 灰階
        let u8 = data.map { UInt8(clamping: Int($0 * 255.0)) }
        return CIImage(bitmapData: Data(u8), bytesPerRow: w, size: CGSize(width: w, height: h), format: .L8, colorSpace: CGColorSpaceCreateDeviceGray())
    }

    /// 若裝置未支援 CIColorThreshold，自行用 LUT 閾值（近似）
    private static func simpleThreshold(image: CIImage, threshold: Float) -> CIImage? {
        // 使用 CIColorMatrix 將 >th 的像素推到白色，其餘黑色（近似）
        // y = step(x - th)
        let t = CGFloat(threshold)
        let params: [String: Any] = [
            kCIInputImageKey: image,
            "inputRVector": CIVector(x: 1, y: 0, z: 0, w: 0),
            "inputGVector": CIVector(x: 0, y: 1, z: 0, w: 0),
            "inputBVector": CIVector(x: 0, y: 0, z: 1, w: 0),
            "inputBiasVector": CIVector(x: -t, y: -t, z: -t, w: 0)
        ]
        guard let shifted = CIFilter(name: "CIColorMatrix", parameters: params)?.outputImage else { return nil }
        // 再用曝光強化對比
        return CIFilter(name: "CIColorControls", parameters: [kCIInputImageKey: shifted, kCIInputContrastKey: 4.0])?.outputImage
    }

    // MARK: - Largest connected component (近似實作，避免依賴外部庫)
    private static func keepLargestComponent(from mask: CGImage, context: CIContext) -> CGImage? {
        let width = mask.width
        let height = mask.height
        let bytesPerPixel = 1
        let bytesPerRow = width * bytesPerPixel
        guard let dataProvider = mask.dataProvider, let data = dataProvider.data else { return nil }
        let src = CFDataGetBytePtr(data)!
        var labels = Array(repeating: Array(repeating: 0, count: width), count: height)
        var currentLabel = 0
        var sizes: [Int: Int] = [:]
        for y in 0..<height {
            for x in 0..<width {
                let off = y * mask.bytesPerRow + x * max(1, mask.bitsPerPixel/8)
                let isOn = src[off] > 127
                if isOn && labels[y][x] == 0 {
                    currentLabel += 1
                    var stack: [(Int,Int)] = [(x,y)]
                    labels[y][x] = currentLabel
                    var count = 0
                    while let (cx, cy) = stack.popLast() {
                        count += 1
                        for dy in -1...1 {
                            for dx in -1...1 {
                                if dx == 0 && dy == 0 { continue }
                                let nx = cx + dx, ny = cy + dy
                                if nx >= 0 && nx < width && ny >= 0 && ny < height && labels[ny][nx] == 0 {
                                    let off2 = ny * mask.bytesPerRow + nx * max(1, mask.bitsPerPixel/8)
                                    if src[off2] > 127 {
                                        labels[ny][nx] = currentLabel
                                        stack.append((nx, ny))
                                    }
                                }
                            }
                        }
                    }
                    sizes[currentLabel] = count
                }
            }
        }
        guard let (bestLabel, _) = sizes.max(by: { $0.value < $1.value }) else { return mask }
        // 重建最大成分二值圖
        var outBytes = [UInt8](repeating: 0, count: width*height)
        for y in 0..<height {
            for x in 0..<width {
                outBytes[y*width + x] = labels[y][x] == bestLabel ? 255 : 0
            }
        }
        let cs = CGColorSpaceCreateDeviceGray()
        guard let outCtx = CGContext(data: &outBytes, width: width, height: height, bitsPerComponent: 8, bytesPerRow: width, space: cs, bitmapInfo: 0), let outCG = outCtx.makeImage() else { return nil }
        return outCG
    }

    // MARK: - Binarize (shared by TTA and single path)
    private func binarize(prob: MLMultiArray, originalSize: (Int, Int), threshold: Float) -> OutputMask? {
        guard let probCI = Self.multiArrayToCIImage(prob) else { return nil }
        let (targetW, targetH) = originalSize
        let scaled = probCI.transformed(by: CGAffineTransform(
            scaleX: CGFloat(targetW) / probCI.extent.width,
            y: CGFloat(targetH) / probCI.extent.height))
        let thresh = CIFilter(name: "CIColorThreshold",
                              parameters: ["inputImage": scaled, "inputThreshold": threshold])?.outputImage
                     ?? Self.simpleThreshold(image: scaled, threshold: threshold)
        guard let bin = thresh,
              let outCG = ciContext.createCGImage(bin, from: CGRect(x: 0, y: 0, width: targetW, height: targetH))
        else { return nil }
        if let largest = Self.keepLargestComponent(from: outCG, context: ciContext) {
            return OutputMask(mask: largest, probability: prob, width: targetW, height: targetH)
        }
        return OutputMask(mask: outCG, probability: prob, width: targetW, height: targetH)
    }
}

// MARK: - UIImage geometry helpers (for TTA)
private extension UIImage {
    func flipped(horizontally: Bool) -> UIImage {
        UIGraphicsBeginImageContextWithOptions(size, false, scale)
        defer { UIGraphicsEndImageContext() }
        let ctx = UIGraphicsGetCurrentContext()!
        ctx.translateBy(x: horizontally ? size.width : 0,
                        y: horizontally ? 0          : size.height)
        ctx.scaleBy(x: horizontally ? -1 : 1,
                    y: horizontally ?  1 : -1)
        draw(in: CGRect(origin: .zero, size: size))
        return UIGraphicsGetImageFromCurrentImageContext() ?? self
    }
    func rotated90CW() -> UIImage {
        let newSize = CGSize(width: size.height, height: size.width)
        UIGraphicsBeginImageContextWithOptions(newSize, false, scale)
        defer { UIGraphicsEndImageContext() }
        let ctx = UIGraphicsGetCurrentContext()!
        ctx.translateBy(x: newSize.width, y: 0)
        ctx.rotate(by: .pi / 2)
        draw(in: CGRect(origin: .zero, size: size))
        return UIGraphicsGetImageFromCurrentImageContext() ?? self
    }
}

// MARK: - MLMultiArray TTA helpers
private extension MLMultiArray {
    enum FlipAxis { case width, height }
    /// Element-wise average of same-shape arrays
    static func average(_ arrays: [MLMultiArray]) -> MLMultiArray? {
        guard let first = arrays.first else { return nil }
        let count = first.count
        guard let result = try? MLMultiArray(shape: first.shape, dataType: .float32) else { return nil }
        let rPtr = result.dataPointer.bindMemory(to: Float.self, capacity: count)
        for arr in arrays {
            let aPtr = arr.dataPointer.bindMemory(to: Float.self, capacity: count)
            for i in 0..<count { rPtr[i] += aPtr[i] }
        }
        let n = Float(arrays.count)
        for i in 0..<count { rPtr[i] /= n }
        return result
    }
    /// Flip along width or height axis (assumes shape [1,H,W,1] or [H,W])
    func flipped(axis: FlipAxis) -> MLMultiArray? {
        let shape = self.shape.map { Int(truncating: $0) }
        guard shape.count >= 2 else { return nil }
        let h = shape[shape.count - (axis == .width  ? 2 : 3 > shape.count-1 ? 2 : 3)]
        let w = shape[shape.count - (axis == .height ? 2 : 1)]
        guard let result = try? MLMultiArray(shape: self.shape, dataType: .float32) else { return nil }
        let src = dataPointer.bindMemory(to: Float.self, capacity: count)
        let dst = result.dataPointer.bindMemory(to: Float.self, capacity: count)
        for row in 0..<h {
            for col in 0..<w {
                let srcIdx = row * w + col
                let dstRow = axis == .height ? (h - 1 - row) : row
                let dstCol = axis == .width  ? (w - 1 - col) : col
                dst[dstRow * w + dstCol] = src[srcIdx]
            }
        }
        return result
    }
    /// Rotate 90° counter-clockwise (inverse of CW)
    func rotated90CCW() -> MLMultiArray? {
        let shape = self.shape.map { Int(truncating: $0) }
        guard shape.count >= 2 else { return nil }
        let h = shape[shape.count - 2]
        let w = shape[shape.count - 1]
        var newShape = shape; newShape[shape.count-2] = w; newShape[shape.count-1] = h
        guard let result = try? MLMultiArray(shape: newShape.map { NSNumber(value: $0) }, dataType: .float32) else { return nil }
        let src = dataPointer.bindMemory(to: Float.self, capacity: count)
        let dst = result.dataPointer.bindMemory(to: Float.self, capacity: count)
        for row in 0..<h {
            for col in 0..<w {
                dst[col * h + (h - 1 - row)] = src[row * w + col]
            }
        }
        return result
    }
}
