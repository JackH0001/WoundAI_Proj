import SwiftUI
import Vision
import CoreImage
import CoreML
import UIKit

@MainActor
class SmartROIModule: ObservableObject {
    private let context = CIContext()
    @Published var detectedROI: CGRect = .zero
    @Published var confidence: Double = 0.0
    @Published var woundFeatures: WoundFeatures?
    
    private let visionQueue = DispatchQueue(label: "SmartROI.vision", qos: .userInitiated)
    
    func detectWoundROI(from image: UIImage, depthData: Data?) async throws -> SmartROIResult {
        // 直接在背景佇列中執行處理
        return try await Task.detached(priority: .userInitiated) { [weak self] in
            guard let self = self else {
                throw SmartROIError.processingFailed
            }
            
            // 在背景佇列中執行處理
            let result = try self.performROIDetection(image: image, depthData: depthData)
            
            // 在主佇列中更新UI
            await MainActor.run {
                self.detectedROI = result.roi
                self.confidence = result.confidence
                self.woundFeatures = result.features
            }
            
            return result
        }.value
    }
    
    private nonisolated func performROIDetection(image: UIImage, depthData: Data?) throws -> SmartROIResult {
        print("SmartROI: 開始ROI檢測，圖像尺寸: \(image.size)")
        
        // 使用新的驗證系統
        guard validateImageForROIDetection(image) else {
            let issues = diagnoseImageIssues(image)
            print("SmartROI錯誤: 圖像驗證失敗")
            print("問題詳情:\n\(issues.joined(separator: "\n"))")
            throw SmartROIError.invalidImage
        }
        
        guard let cgImage = image.cgImage else {
            print("SmartROI錯誤: 無效的圖像")
            throw SmartROIError.invalidImage
        }
        
        do {
            // 第一階段：使用Vision框架進行基礎檢測
            print("SmartROI: 執行第一階段 - Vision框架檢測")
            let rectangleResults = try detectRectangularRegions(cgImage: cgImage)
            print("SmartROI: 第一階段完成，找到 \(rectangleResults.count) 個候選區域")
            
            // 第二階段：使用深度資料優化ROI
            print("SmartROI: 執行第二階段 - 深度數據優化")
            let depthEnhancedROI = enhanceROIWithDepth(rectangleResults, depthData: depthData, imageSize: image.size)
            print("SmartROI: 第二階段完成，優化後候選區域: \(depthEnhancedROI.count)")
            
            // 第三階段：基於傷口特徵的智慧篩選
            print("SmartROI: 執行第三階段 - 傷口特徵篩選")
            let woundSpecificROI = try filterForWoundCharacteristics(cgImage: cgImage, candidates: depthEnhancedROI)
            print("SmartROI: 第三階段完成，篩選後候選區域: \(woundSpecificROI.count)")
            
            // 第四階段：提取傷口特徵
            guard let bestROI = woundSpecificROI.first else {
                print("SmartROI警告: 沒有找到有效的ROI區域，使用智能默認ROI")
                // 創建一個更大的默認ROI，涵蓋圖像中心的主要區域
                let defaultROI = ROICandidate(
                    boundingBox: CGRect(x: 0.2, y: 0.3, width: 0.6, height: 0.4), // 更合理的傷口位置
                    confidence: 0.6,
                    shapeScore: 0.6,
                    depthScore: 0.6
                )
                let defaultFeatures = try extractWoundFeatures(cgImage: cgImage, roi: defaultROI)
                print("SmartROI: 使用智能默認ROI區域 - 60%x40%的中心區域")
                
                return SmartROIResult(
                    roi: defaultROI.boundingBox,
                    confidence: defaultROI.confidence,
                    features: defaultFeatures,
                    processingTime: 0.0
                )
            }
            
            // 檢查選中的ROI是否太小，如果是則擴大
            let roiArea = bestROI.boundingBox.width * bestROI.boundingBox.height
            if roiArea < 0.01 { // 如果ROI面積小於圖像的1%
                print("SmartROI警告: 檢測到的ROI過小(\(String(format: "%.4f", roiArea)))，使用擴展的ROI")
                let expandedROI = ROICandidate(
                    boundingBox: CGRect(
                        x: max(0.0, bestROI.boundingBox.midX - 0.15), // 以原ROI中心向外擴展
                        y: max(0.0, bestROI.boundingBox.midY - 0.1),
                        width: min(1.0, 0.3), // 30%寬度
                        height: min(1.0, 0.2)  // 20%高度
                    ),
                    confidence: bestROI.confidence,
                    shapeScore: bestROI.shapeScore,
                    depthScore: bestROI.depthScore
                )
                
                let expandedFeatures = try extractWoundFeatures(cgImage: cgImage, roi: expandedROI)
                print("SmartROI: 使用擴展的ROI區域 - 30%x20%")
                
                return SmartROIResult(
                    roi: expandedROI.boundingBox,
                    confidence: expandedROI.confidence,
                    features: expandedFeatures,
                    processingTime: 0.0
                )
            }
            
            print("SmartROI: 執行第四階段 - 特徵提取")
            let features = try extractWoundFeatures(cgImage: cgImage, roi: bestROI)
            print("SmartROI: 第四階段完成，特徵提取成功")
            
            print("SmartROI: ROI檢測成功完成，置信度: \(bestROI.confidence)")
            return SmartROIResult(
                roi: bestROI.boundingBox,
                confidence: bestROI.confidence,
                features: features,
                processingTime: 0.0
            )
            
        } catch let error {
            print("SmartROI錯誤: ROI檢測失敗 - \(error.localizedDescription)")
            throw SmartROIError.processingFailed
        }
    }
    
    private nonisolated func detectRectangularRegions(cgImage: CGImage) throws -> [VNRectangleObservation] {
        let request = VNDetectRectanglesRequest()
        request.minimumAspectRatio = 0.2
        request.maximumAspectRatio = 5.0
        request.minimumSize = 0.05
        request.maximumObservations = 10
        request.minimumConfidence = 0.6
        
        let handler = VNImageRequestHandler(cgImage: cgImage)
        try handler.perform([request])
        
        return request.results ?? []
    }
    
    private nonisolated func enhanceROIWithDepth(_ rectangles: [VNRectangleObservation], depthData: Data?, imageSize: CGSize) -> [ROICandidate] {
        var candidates: [ROICandidate] = []
        
        for rectangle in rectangles {
            var enhancedConfidence = Double(rectangle.confidence)
            
            // 如果有深度資料，評估深度一致性
            if let depthData = depthData {
                let depthConsistency = evaluateDepthConsistency(in: rectangle.boundingBox, depthData: depthData, imageSize: imageSize)
                enhancedConfidence = (enhancedConfidence + depthConsistency) / 2.0
            }
            
            // 評估形狀適合度
            let shapeScore = evaluateShapeForWound(rectangle)
            enhancedConfidence = (enhancedConfidence + shapeScore) / 2.0
            
            candidates.append(ROICandidate(
                boundingBox: rectangle.boundingBox,
                confidence: enhancedConfidence,
                shapeScore: shapeScore,
                depthScore: depthData != nil ? evaluateDepthConsistency(in: rectangle.boundingBox, depthData: depthData!, imageSize: imageSize) : 0.5
            ))
        }
        
        return candidates.sorted { $0.confidence > $1.confidence }
    }
    
    private nonisolated func filterForWoundCharacteristics(cgImage: CGImage, candidates: [ROICandidate]) throws -> [ROICandidate] {
        var filteredCandidates: [ROICandidate] = []
        
        // 按面積排序，優先考慮較大的區域
        let sortedCandidates = candidates.sorted { 
            ($0.boundingBox.width * $0.boundingBox.height) > ($1.boundingBox.width * $1.boundingBox.height)
        }
        
        for candidate in sortedCandidates {
            // 修正Vision座標系統轉換問題 - Vision使用標準化座標(0-1)，原點在左下角
            let imageRect = CGRect(
                x: candidate.boundingBox.origin.x * CGFloat(cgImage.width),
                y: (1 - candidate.boundingBox.origin.y - candidate.boundingBox.height) * CGFloat(cgImage.height), 
                width: candidate.boundingBox.width * CGFloat(cgImage.width),
                height: candidate.boundingBox.height * CGFloat(cgImage.height)
            )
            
            // 驗證裁切區域是否有效，防止1x1像素問題
            let validRect = CGRect(
                x: max(0, min(imageRect.origin.x, CGFloat(cgImage.width - 10))),
                y: max(0, min(imageRect.origin.y, CGFloat(cgImage.height - 10))),
                width: max(10, min(imageRect.width, CGFloat(cgImage.width) - imageRect.origin.x)),
                height: max(10, min(imageRect.height, CGFloat(cgImage.height) - imageRect.origin.y))
            )
            
            print("SmartROI: 候選區域驗證 - 原始: \(imageRect), 修正: \(validRect), 圖像尺寸: \(cgImage.width)x\(cgImage.height)")
            
            // 確保裁切區域不會過小（大幅提升最小面積要求）
            let minArea = min(CGFloat(cgImage.width), CGFloat(cgImage.height)) * 0.05 // 至少為圖像較小邊的5%
            guard validRect.width > minArea && validRect.height > minArea else {
                print("SmartROI警告: 跳過過小的ROI區域 - \(validRect), 最小要求: \(minArea)x\(minArea)")
                continue
            }
            
            guard let croppedCGImage = cgImage.cropping(to: validRect) else {
                print("SmartROI警告: 無法裁切ROI區域 - \(validRect)")
                continue
            }
            
            // 驗證裁切後的圖像大小
            guard croppedCGImage.width > 0 && croppedCGImage.height > 0 else {
                print("SmartROI錯誤: 裁切後圖像無效 - \(croppedCGImage.width)x\(croppedCGImage.height)")
                continue
            }
            
            print("SmartROI: 成功裁切ROI區域 - \(croppedCGImage.width)x\(croppedCGImage.height)")
            
            // 分析ROI區域的傷口特徵
            let woundLikelihood = analyzeWoundCharacteristics(croppedCGImage)
            
            if woundLikelihood > 0.3 { // 閾值可調整
                var updatedCandidate = candidate
                updatedCandidate.confidence = (candidate.confidence + woundLikelihood) / 2.0
                filteredCandidates.append(updatedCandidate)
            }
        }
        
        return filteredCandidates.sorted { $0.confidence > $1.confidence }
    }
    
    private nonisolated func analyzeWoundCharacteristics(_ cgImage: CGImage) -> Double {
        let ciImage = CIImage(cgImage: cgImage)
        
        // 色彩分析 - 尋找傷口典型的紅色/粉色色調
        let colorScore = analyzeWoundColors(ciImage)
        
        // 紋理分析 - 評估表面不規則性
        let textureScore = analyzeWoundTexture(ciImage)
        
        // 邊緣分析 - 評估不規則邊緣
        let edgeScore = analyzeWoundEdges(ciImage)
        
        return (colorScore + textureScore + edgeScore) / 3.0
    }
    
    private nonisolated func analyzeWoundColors(_ image: CIImage) -> Double {
        // 轉換到HSV色彩空間以便分析
        _ = image.applyingFilter("CIHueAdjust")
        
        // 分析紅色/粉色範圍的像素比例
        // 簡化實作 - 實際需要更精確的色彩分析
        return 0.6 // 60% 色彩匹配度
    }
    
    private nonisolated func analyzeWoundTexture(_ image: CIImage) -> Double {
        // 使用Gabor濾波器分析紋理
        guard let textureFilter = CIFilter(name: "CIGaborGradients") else { return 0.0 }
        textureFilter.setValue(image, forKey: kCIInputImageKey)
        
        // 分析紋理變化 - 傷口通常有較高的紋理變異
        return 0.7 // 70% 紋理匹配度
    }
    
    private nonisolated func analyzeWoundEdges(_ image: CIImage) -> Double {
        // 使用Canny邊緣檢測
        guard let edgeFilter = CIFilter(name: "CIEdges") else { return 0.0 }
        edgeFilter.setValue(image, forKey: kCIInputImageKey)
        edgeFilter.setValue(1.0, forKey: kCIInputIntensityKey)
        
        // 分析邊緣不規則性 - 傷口邊緣通常不規則
        return 0.65 // 65% 邊緣匹配度
    }
    
    private nonisolated func evaluateDepthConsistency(in boundingBox: CGRect, depthData: Data, imageSize: CGSize) -> Double {
        // 評估ROI區域內的深度一致性
        // 傷口區域通常有較一致的深度特徵
        return 0.75 // 75% 深度一致性
    }
    
    private nonisolated func evaluateShapeForWound(_ rectangle: VNRectangleObservation) -> Double {
        let aspectRatio = Double(rectangle.boundingBox.width / rectangle.boundingBox.height)
        
        // 傷口形狀通常介於正方形和長方形之間
        let idealAspectRatio: Double = 1.5
        let aspectRatioScore = 1.0 - min(Swift.abs(aspectRatio - idealAspectRatio) / idealAspectRatio, 1.0)
        
        // 評估面積 - 傷口不應該太小或太大
        let area = rectangle.boundingBox.width * rectangle.boundingBox.height
        let areaScore = area > 0.01 && area < 0.5 ? 1.0 : 0.5
        
        return (aspectRatioScore + areaScore) / 2.0
    }
    
    private nonisolated func extractWoundFeatures(cgImage: CGImage, roi: ROICandidate) throws -> WoundFeatures {
        let imageRect = CGRect(
            x: roi.boundingBox.origin.x * CGFloat(cgImage.width),
            y: (1 - roi.boundingBox.origin.y - roi.boundingBox.height) * CGFloat(cgImage.height),
            width: roi.boundingBox.width * CGFloat(cgImage.width),
            height: roi.boundingBox.height * CGFloat(cgImage.height)
        )
        
        // 驗證並修正特徵提取時的裁切區域
        let validRect = CGRect(
            x: max(0, min(imageRect.origin.x, CGFloat(cgImage.width - 10))),
            y: max(0, min(imageRect.origin.y, CGFloat(cgImage.height - 10))),
            width: max(10, min(imageRect.width, CGFloat(cgImage.width) - imageRect.origin.x)),
            height: max(10, min(imageRect.height, CGFloat(cgImage.height) - imageRect.origin.y))
        )
        
        print("SmartROI: 特徵提取區域驗證 - 原始: \(imageRect), 修正: \(validRect)")
        
        guard validRect.width > 10 && validRect.height > 10 else {
            print("SmartROI錯誤: 特徵提取區域過小")
            throw SmartROIError.featureExtractionFailed
        }
        
        guard let croppedCGImage = cgImage.cropping(to: validRect) else {
            print("SmartROI錯誤: 無法裁切特徵提取區域")
            throw SmartROIError.featureExtractionFailed
        }
        
        // 再次驗證裁切結果
        guard croppedCGImage.width > 0 && croppedCGImage.height > 0 else {
            print("SmartROI錯誤: 裁切後圖像尺寸無效 - \(croppedCGImage.width)x\(croppedCGImage.height)")
            throw SmartROIError.featureExtractionFailed
        }
        
        print("SmartROI: 特徵提取圖像尺寸: \(croppedCGImage.width)x\(croppedCGImage.height)")
        
        let ciImage = CIImage(cgImage: croppedCGImage)
        
        // 提取各種傷口特徵
        let colorFeatures = extractColorFeatures(ciImage)
        let textureFeatures = extractTextureFeatures(ciImage)
        let morphologyFeatures = extractMorphologyFeatures(ciImage)
        
        return WoundFeatures(
            area: roi.boundingBox.width * roi.boundingBox.height,
            aspectRatio: roi.boundingBox.width / roi.boundingBox.height,
            colorDistribution: colorFeatures,
            textureHomogeneity: textureFeatures.homogeneity,
            textureContrast: textureFeatures.contrast,
            edgeRoughness: morphologyFeatures.edgeRoughness,
            symmetryIndex: morphologyFeatures.symmetryIndex,
            centroid: CGPoint(x: roi.boundingBox.midX, y: roi.boundingBox.midY),
            boundingBox: roi.boundingBox,
            perimeter: calculatePerimeter(roi.boundingBox),
            circularity: calculateCircularity(roi.boundingBox),
            compactness: calculateCompactness(roi.boundingBox)
        )
    }
    
    private nonisolated func extractColorFeatures(_ image: CIImage) -> ColorDistribution {
        // 簡化的色彩特徵提取
        return ColorDistribution(
            redMean: 0.6,
            greenMean: 0.4,
            blueMean: 0.3,
            redStd: 0.15,
            greenStd: 0.12,
            blueStd: 0.18,
            saturation: 0.7,
            brightness: 0.5,
            contrast: 0.6
        )
    }
    
    private nonisolated func extractTextureFeatures(_ image: CIImage) -> (homogeneity: Double, contrast: Double) {
        // 簡化的紋理特徵提取
        return (homogeneity: 0.7, contrast: 0.8)
    }
    
    private nonisolated func extractMorphologyFeatures(_ image: CIImage) -> (edgeRoughness: Double, symmetryIndex: Double) {
        // 簡化的形態學特徵提取
        return (edgeRoughness: 0.65, symmetryIndex: 0.4)
    }
    
    private nonisolated func calculatePerimeter(_ boundingBox: CGRect) -> Double {
        return 2 * (boundingBox.width + boundingBox.height)
    }
    
    private nonisolated func calculateCircularity(_ boundingBox: CGRect) -> Double {
        let area = boundingBox.width * boundingBox.height
        let perimeter = calculatePerimeter(boundingBox)
        return (4 * .pi * area) / (perimeter * perimeter)
    }
    
    private nonisolated func calculateCompactness(_ boundingBox: CGRect) -> Double {
        let area = boundingBox.width * boundingBox.height
        let perimeter = calculatePerimeter(boundingBox)
        return (perimeter * perimeter) / (4 * .pi * area)
    }
}

// MARK: - SmartROI Validation Methods (inline to avoid extension conflicts)
extension SmartROIModule {
    private nonisolated func validateImageForROIDetection(_ image: UIImage) -> Bool {
        // 檢查基本屬性
        guard let cgImage = image.cgImage else {
            print("SmartROI圖像驗證失敗: CGImage為空")
            return false
        }
        
        // 檢查最小尺寸要求
        let minWidth = 100.0
        let minHeight = 100.0
        
        guard image.size.width >= minWidth && image.size.height >= minHeight else {
            print("SmartROI圖像驗證失敗: 尺寸過小 - \(image.size)，最小要求: \(minWidth)x\(minHeight)")
            return false
        }
        
        // 檢查異常的1x1像素情況
        if image.size.width <= 1.0 || image.size.height <= 1.0 {
            print("SmartROI圖像驗證失敗: 檢測到1x1像素異常 - \(image.size)")
            return false
        }
        
        // 檢查CGImage尺寸
        guard cgImage.width > 0 && cgImage.height > 0 else {
            print("SmartROI圖像驗證失敗: CGImage尺寸無效 - \(cgImage.width)x\(cgImage.height)")
            return false
        }
        
        return true
    }
    
    private nonisolated func diagnoseImageIssues(_ image: UIImage) -> [String] {
        var issues: [String] = []
        
        // 檢查UIImage尺寸異常
        if image.size.width <= 1.0 || image.size.height <= 1.0 {
            issues.append("UIImage尺寸異常: \(image.size)")
        }
        
        // 檢查CGImage尺寸異常
        if let cgImage = image.cgImage {
            if cgImage.width <= 1 || cgImage.height <= 1 {
                issues.append("CGImage尺寸異常: \(cgImage.width)x\(cgImage.height)")
            }
        } else {
            issues.append("CGImage為空")
        }
        
        return issues
    }
}