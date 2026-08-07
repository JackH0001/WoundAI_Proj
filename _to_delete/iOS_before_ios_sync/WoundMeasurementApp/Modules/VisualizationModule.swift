import SwiftUI
import CoreImage
import UIKit

class VisualizationModule: ObservableObject {
    @Published var isGenerating = false
    
    // MARK: - Deepskin風格的遮罩可視化
    
    /// 生成Deepskin風格的語義分割遮罩可視化
    func generateDeepskinStyleMask(_ originalImage: UIImage, segmentedImage: SegmentedImage) -> UIImage? {
        guard let largestContour = segmentedImage.contours.max(by: { $0.area < $1.area }) else {
            return nil
        }
        
        let size = originalImage.size
        let renderer = UIGraphicsImageRenderer(size: size)
        
        return renderer.image { context in
            // 繪製原圖作為背景（透明度75%）
            originalImage.draw(at: .zero, blendMode: .normal, alpha: 0.75)
            
            // 繪製傷口輪廓 - 綠色邊框（lime色，參考Deepskin）
            let path = createBezierPath(from: largestContour.points, size: size)
            
            // 傷口區域填充 - 半透明紅色
            UIColor.systemRed.withAlphaComponent(0.2).setFill()
            path.fill()
            
            // 傷口邊界 - 綠色輪廓線
            UIColor.systemGreen.setStroke()
            path.lineWidth = 3.0
            path.stroke()
            
            // 添加面積標註（像素面積顯示為 px²，避免誤以為是 cm²）
            let areaPixels = largestContour.area
            if let areaText = createAreaAnnotation(areaPixels: areaPixels, contour: largestContour, size: size) {
                areaText.draw(at: CGPoint(x: 10, y: size.height - 80))
            }
        }
    }
    
    /// 生成多層遮罩可視化（類似Deepskin的多類別分割）
    func generateMultiClassMask(_ originalImage: UIImage, segmentedImage: SegmentedImage) -> UIImage? {
        let size = originalImage.size
        let renderer = UIGraphicsImageRenderer(size: size)
        
        return renderer.image { context in
            // 繪製原圖
            originalImage.draw(at: .zero)
            
            // 為每個輪廓分配不同顏色（模擬多類別）
            let colors: [UIColor] = [
                .systemGreen.withAlphaComponent(0.3),  // 主要傷口 - 綠色
                .systemBlue.withAlphaComponent(0.3),   // 周圍組織 - 藍色  
                .systemYellow.withAlphaComponent(0.3)  // 其他區域 - 黃色
            ]
            
            for (index, contour) in segmentedImage.contours.enumerated() {
                let path = createBezierPath(from: contour.points, size: size)
                let colorIndex = min(index, colors.count - 1)
                
                // 填充
                colors[colorIndex].setFill()
                path.fill()
                
                // 邊框
                colors[colorIndex].withAlphaComponent(0.8).setStroke()
                path.lineWidth = 2.0
                path.stroke()
            }
        }
    }
    
    /// 生成周圍組織(peri-wound)遮罩可視化
    func generatePeriWoundMask(_ originalImage: UIImage, segmentedImage: SegmentedImage, kernelSize: CGFloat = 20) -> UIImage? {
        guard let largestContour = segmentedImage.contours.max(by: { $0.area < $1.area }) else {
            return nil
        }
        
        let size = originalImage.size
        let renderer = UIGraphicsImageRenderer(size: size)
        
        return renderer.image { context in
            // 繪製原圖
            originalImage.draw(at: .zero, blendMode: .normal, alpha: 0.75)
            
            // 創建內部路徑（侵蝕）
            let innerPath = createErodedPath(from: largestContour.points, size: size, erosion: kernelSize)
            
            // 創建外部路徑（膨脹）
            let outerPath = createDilatedPath(from: largestContour.points, size: size, dilation: kernelSize)
            
            // 繪製peri-wound區域（外部 - 內部）
            context.cgContext.saveGState()
            
            // 設置剪裁區域為外部路徑
            outerPath.addClip()
            
            // 繪製peri-wound填充
            UIColor.systemOrange.withAlphaComponent(0.3).setFill()
            outerPath.fill()
            
            // 移除內部區域
            context.cgContext.setBlendMode(.clear)
            innerPath.fill()
            
            context.cgContext.restoreGState()
            
            // 繪製邊界
            UIColor.systemOrange.setStroke()
            outerPath.lineWidth = 2.0
            outerPath.stroke()
            
            UIColor.systemGreen.setStroke()
            innerPath.lineWidth = 2.0  
            innerPath.stroke()
        }
    }
    
    // MARK: - 輔助方法
    
    private func createBezierPath(from points: [CGPoint], size: CGSize) -> UIBezierPath {
        let path = UIBezierPath()
        
        for (index, point) in points.enumerated() {
            let x = point.x * size.width
            let y = point.y * size.height
            
            if index == 0 {
                path.move(to: CGPoint(x: x, y: y))
            } else {
                path.addLine(to: CGPoint(x: x, y: y))
            }
        }
        path.close()
        return path
    }
    
    private func createErodedPath(from points: [CGPoint], size: CGSize, erosion: CGFloat) -> UIBezierPath {
        // 簡化的侵蝕操作 - 向內收縮
        let path = UIBezierPath()
        let erosionFactor = erosion / min(size.width, size.height)
        
        for (index, point) in points.enumerated() {
            let x = (point.x + erosionFactor) * size.width
            let y = (point.y + erosionFactor) * size.height
            
            if index == 0 {
                path.move(to: CGPoint(x: x, y: y))
            } else {
                path.addLine(to: CGPoint(x: x, y: y))
            }
        }
        path.close()
        return path
    }
    
    private func createDilatedPath(from points: [CGPoint], size: CGSize, dilation: CGFloat) -> UIBezierPath {
        // 簡化的膨脹操作 - 向外擴張
        let path = UIBezierPath()
        let dilationFactor = dilation / min(size.width, size.height)
        
        for (index, point) in points.enumerated() {
            let x = max(0, (point.x - dilationFactor)) * size.width
            let y = max(0, (point.y - dilationFactor)) * size.height
            
            if index == 0 {
                path.move(to: CGPoint(x: x, y: y))
            } else {
                path.addLine(to: CGPoint(x: x, y: y))
            }
        }
        path.close()
        return path
    }
    
    private func createAreaAnnotation(areaPixels: Double, contour: WoundContour, size: CGSize) -> UIImage? {
        let text = String(format: "面積: %.0f px²", areaPixels)
        let font = UIFont.systemFont(ofSize: 16, weight: .semibold)
        let attributes: [NSAttributedString.Key: Any] = [
            .font: font,
            .foregroundColor: UIColor.white,
            .backgroundColor: UIColor.black.withAlphaComponent(0.7)
        ]
        
        let attributedString = NSAttributedString(string: text, attributes: attributes)
        let textSize = attributedString.size()
        
        let renderer = UIGraphicsImageRenderer(size: CGSize(width: textSize.width + 16, height: textSize.height + 8))
        
        return renderer.image { context in
            UIColor.black.withAlphaComponent(0.7).setFill()
            context.fill(CGRect(origin: .zero, size: renderer.format.bounds.size))
            
            attributedString.draw(at: CGPoint(x: 8, y: 4))
        }
    }
    
    // MARK: - 原有方法保持兼容性
    
    func generateAreaMask(_ segmentedImage: SegmentedImage, size: CGSize) -> UIImage? {
        guard let largestContour = segmentedImage.contours.max(by: { $0.area < $1.area }) else {
            return nil
        }
        
        let renderer = UIGraphicsImageRenderer(size: size)
        
        return renderer.image { context in
            // 設置背景為透明
            UIColor.clear.setFill()
            context.fill(CGRect(origin: .zero, size: size))
            
            // 繪製面積遮罩
            let path = UIBezierPath()
            
            for (index, point) in largestContour.points.enumerated() {
                let x = point.x * size.width
                let y = point.y * size.height
                
                if index == 0 {
                    path.move(to: CGPoint(x: x, y: y))
                } else {
                    path.addLine(to: CGPoint(x: x, y: y))
                }
            }
            path.close()
            
            // 設置遮罩顏色（半透明紅色）
            UIColor.red.withAlphaComponent(0.3).setFill()
            path.fill()
            
            // 設置邊框顏色
            UIColor.red.setStroke()
            path.lineWidth = 2.0
            path.stroke()
        }
    }
    
    func generateDepthGradient(_ depthData: Data, size: CGSize) -> UIImage? {
        let width = 256
        let height = 192
        
        let floats = depthData.withUnsafeBytes { buffer in
            return buffer.bindMemory(to: Float32.self)
        }
        
        let renderer = UIGraphicsImageRenderer(size: size)
        
        return renderer.image { context in
            // 設置背景為透明
            UIColor.clear.setFill()
            context.fill(CGRect(origin: .zero, size: size))
            
            let cellWidth = size.width / CGFloat(width)
            let cellHeight = size.height / CGFloat(height)
            
            for y in 0..<height {
                for x in 0..<width {
                    let index = y * width + x
                    if index < floats.count {
                        let depth = Double(floats[index])
                        
                        // 將深度值映射到顏色（藍色到紅色）
                        let normalizedDepth = min(depth / 0.5, 1.0) // 假設最大深度為 0.5m
                        let color = depthToColor(normalizedDepth)
                        
                        let rect = CGRect(
                            x: CGFloat(x) * cellWidth,
                            y: CGFloat(y) * cellHeight,
                            width: cellWidth,
                            height: cellHeight
                        )
                        
                        color.setFill()
                        context.fill(rect)
                    }
                }
            }
        }
    }
    
    private func depthToColor(_ depth: Double) -> UIColor {
        // 深度到顏色的映射：藍色（淺）到紅色（深）
        let red = CGFloat(depth)
        let green = CGFloat(0.0)
        let blue = CGFloat(1.0 - depth)
        
        return UIColor(red: red, green: green, blue: blue, alpha: 0.7)
    }
    
    func generateMeasurementOverlay(_ measurement: WoundMeasurement, size: CGSize) -> UIImage? {
        let renderer = UIGraphicsImageRenderer(size: size)
        
        return renderer.image { context in
            // 設置背景為半透明黑色
            UIColor.black.withAlphaComponent(0.1).setFill()
            context.fill(CGRect(origin: .zero, size: size))
            
            // 設置文字屬性
            let textAttributes: [NSAttributedString.Key: Any] = [
                .font: UIFont.systemFont(ofSize: 16, weight: .semibold),
                .foregroundColor: UIColor.white
            ]
            
            let shadowAttributes: [NSAttributedString.Key: Any] = [
                .font: UIFont.systemFont(ofSize: 16, weight: .semibold),
                .foregroundColor: UIColor.black
            ]
            
            // 顯示測量結果
            let measurements = [
                "面積: \(String(format: "%.2f", measurement.area)) cm²",
                "周長: \(String(format: "%.2f", measurement.perimeter)) cm",
                "體積: \(String(format: "%.4f", measurement.volume)) cm³",
                "最大深度: \(String(format: "%.2f", measurement.maxDepth)) cm"
            ]
            
            var yOffset: CGFloat = 20
            
            for measurement in measurements {
                // 繪製陰影
                let shadowRect = CGRect(x: 12, y: yOffset + 2, width: size.width - 24, height: 20)
                measurement.draw(in: shadowRect, withAttributes: shadowAttributes)
                
                // 繪製文字
                let textRect = CGRect(x: 10, y: yOffset, width: size.width - 20, height: 20)
                measurement.draw(in: textRect, withAttributes: textAttributes)
                
                yOffset += 25
            }
            
            // 顯示組織成分
            let tissueInfo = [
                "壞死組織: \(String(format: "%.1f", measurement.tissueComposition.necroticPercentage * 100))%",
                "肉芽組織: \(String(format: "%.1f", measurement.tissueComposition.granulationPercentage * 100))%",
                "上皮組織: \(String(format: "%.1f", measurement.tissueComposition.epithelialPercentage * 100))%"
            ]
            
            yOffset += 10
            
            for tissue in tissueInfo {
                let textRect = CGRect(x: 10, y: yOffset, width: size.width - 20, height: 20)
                tissue.draw(in: textRect, withAttributes: textAttributes)
                yOffset += 25
            }
        }
    }
    
    func combineVisualizations(original: UIImage, areaMask: UIImage?, depthGradient: UIImage?, overlay: UIImage?) -> UIImage? {
        let size = original.size
        let renderer = UIGraphicsImageRenderer(size: size)
        
        return renderer.image { context in
            // 繪製原始圖像
            original.draw(in: CGRect(origin: .zero, size: size))
            
            // 繪製深度漸層
            if let depthGradient = depthGradient {
                depthGradient.draw(in: CGRect(origin: .zero, size: size), blendMode: .multiply, alpha: 0.5)
            }
            
            // 繪製面積遮罩
            if let areaMask = areaMask {
                areaMask.draw(in: CGRect(origin: .zero, size: size), blendMode: .normal, alpha: 1.0)
            }
            
            // 繪製測量結果覆蓋層
            if let overlay = overlay {
                overlay.draw(in: CGRect(origin: .zero, size: size), blendMode: .normal, alpha: 1.0)
            }
        }
    }
}

struct VisualizationResult {
    let originalImage: UIImage
    let areaMask: UIImage?
    let depthGradient: UIImage?
    let measurementOverlay: UIImage?
    let combinedImage: UIImage?
    let measurement: WoundMeasurement
}

// 使用WoundTypes.swift中的定義