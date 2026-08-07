import Foundation
import UIKit
import CoreImage
import Vision
import os.log

/// 真實數據比對器 - 使用實際的雲端訓練數據進行比對分析
class RealDataComparator: ObservableObject {
    
    // MARK: - Properties
    
    @Published var analysisProgress: Double = 0.0
    @Published var comparisonResults: [ImageComparisonResult] = []
    @Published var overallAnalysis: OverallComparisonAnalysis?
    @Published var isProcessing: Bool = false
    
    private let cloudDataPath: String
    private let mobileSimulator: MobileComputeSimulator
    private let logger = os.Logger(subsystem: "WoundMeasurementApp", category: "RealDataComparator")
    
    // Ground Truth 數據路徑
    private let fusegTrainPath: String
    private let fusegTestPath: String
    private let fusegLabelsPath: String
    
    init() {
        self.cloudDataPath = "/Users/Jack.Hou/Library/Mobile Documents/com~apple~CloudDocs/Xcode/WoundAI/雲端 AI 模型訓練及分析服務"
        self.fusegTrainPath = "\(cloudDataPath)/wound-segmentation-master/data/Foot Ulcer Segmentation Challenge/train/images"
        self.fusegTestPath = "\(cloudDataPath)/wound-segmentation-master/data/Foot Ulcer Segmentation Challenge/test/images" 
        self.fusegLabelsPath = "\(cloudDataPath)/wound-segmentation-master/data/Foot Ulcer Segmentation Challenge/train/labels"
        self.mobileSimulator = MobileComputeSimulator()
        
        logger.info("RealDataComparator initialized with cloud path: \(self.cloudDataPath)")
    }
    
    // MARK: - 主要比對分析方法
    
    /// 執行真實數據比對分析
    func performRealDataComparison(sampleCount: Int = 10) async throws -> RealDataAnalysisResult {
        logger.info("開始真實數據比對分析，樣本數量: \(sampleCount)")
        
        await MainActor.run {
            isProcessing = true
            analysisProgress = 0.0
            comparisonResults.removeAll()
        }
        
        do {
            // 步驟1: 載入真實測試圖像和Ground Truth
            await updateProgress(0.1)
            let testImagePairs = try await loadRealTestData(count: sampleCount)
            logger.info("成功載入 \(testImagePairs.count) 對測試數據")
            
            // 步驟2: 對每張圖像執行行動端模擬處理
            await updateProgress(0.2)
            var comparisonResults: [ImageComparisonResult] = []
            
            for (index, testPair) in testImagePairs.enumerated() {
                logger.info("處理圖像 \(index + 1)/\(testImagePairs.count): \(testPair.imageName)")
                
                // 執行行動端模擬
                let mobileResult = try await simulateMobileProcessingForImage(testPair.image)
                
                // 與Ground Truth比對
                let comparison = try await compareWithGroundTruth(
                    mobileResult: mobileResult,
                    groundTruth: testPair.groundTruth,
                    imageName: testPair.imageName
                )
                
                comparisonResults.append(comparison)
                
                // 更新進度
                let progress = 0.2 + (0.7 * Double(index + 1) / Double(testImagePairs.count))
                await updateProgress(progress)
                
                await MainActor.run {
                    self.comparisonResults.append(comparison)
                }
            }
            
            // 步驟3: 綜合分析
            await updateProgress(0.95)
            let overallAnalysis = try await performOverallAnalysis(comparisonResults: comparisonResults)
            
            await updateProgress(1.0)
            await MainActor.run {
                self.overallAnalysis = overallAnalysis
                self.isProcessing = false
            }
            
            return RealDataAnalysisResult(
                comparisonResults: comparisonResults,
                overallAnalysis: overallAnalysis,
                analysisTimestamp: Date(),
                totalSamples: testImagePairs.count
            )
            
        } catch {
            await MainActor.run {
                self.isProcessing = false
            }
            logger.error("真實數據比對分析失敗: \(error.localizedDescription)")
            throw error
        }
    }
    
    // MARK: - 數據載入方法
    
    /// 載入真實的測試數據和Ground Truth
    private func loadRealTestData(count: Int) async throws -> [TestImagePair] {
        logger.info("載入真實測試數據...")
        
        // 獲取訓練圖像列表 (有對應的labels)
        let trainImageFiles = try await getImageFiles(from: fusegTrainPath)
        let selectedFiles = Array(trainImageFiles.shuffled().prefix(count))
        
        logger.info("選擇了 \(selectedFiles.count) 個訓練圖像文件")
        
        var testPairs: [TestImagePair] = []
        
        for imageFile in selectedFiles {
            let imageName = URL(fileURLWithPath: imageFile).deletingPathExtension().lastPathComponent
            
            // 載入原始圖像
            guard let image = UIImage(contentsOfFile: imageFile) else {
                logger.warning("無法載入圖像: \(imageFile)")
                continue
            }
            
            // 載入對應的Ground Truth標註
            let labelFile = "\(fusegLabelsPath)/\(imageName).png"
            guard let groundTruthMask = UIImage(contentsOfFile: labelFile) else {
                logger.warning("無法載入標註: \(labelFile)")
                continue
            }
            
            // 解析Ground Truth
            let groundTruth = try await parseGroundTruth(mask: groundTruthMask, imageName: imageName)
            
            let testPair = TestImagePair(
                imageName: imageName,
                image: image,
                groundTruth: groundTruth
            )
            
            testPairs.append(testPair)
            logger.info("成功載入測試對: \(imageName)")
        }
        
        return testPairs
    }
    
    /// 獲取圖像文件列表
    private func getImageFiles(from directory: String) async throws -> [String] {
        let fileManager = FileManager.default
        
        guard fileManager.fileExists(atPath: directory) else {
            throw RealDataError.directoryNotFound(directory)
        }
        
        let contents = try fileManager.contentsOfDirectory(atPath: directory)
        let imageFiles = contents
            .filter { $0.hasSuffix(".png") || $0.hasSuffix(".jpg") || $0.hasSuffix(".jpeg") }
            .map { "\(directory)/\($0)" }
        
        return imageFiles
    }
    
    /// 解析Ground Truth標註
    private func parseGroundTruth(mask: UIImage, imageName: String) async throws -> WoundGroundTruth {
        logger.info("解析Ground Truth標註: \(imageName)")
        
        guard let cgImage = mask.cgImage else {
            throw RealDataError.invalidGroundTruthMask
        }
        
        // 分析二值化遮罩
        let maskAnalysis = try await analyzeBinaryMask(cgImage: cgImage)
        
        // 計算傷口輪廓
        let contours = try await extractContoursFromMask(cgImage: cgImage)
        
        // 計算面積和周長 (像素單位)
        let areaPixels = maskAnalysis.whitePixelCount
        let perimeterPixels = calculatePerimeter(contours: contours)
        
        // 轉換為實際尺寸 (假設標準像素密度)
        let pixelsPerMM = 10.0 // FUSeg數據集的典型像素密度
        let areaCm2 = Double(areaPixels) / (pixelsPerMM * pixelsPerMM) / 100.0
        let perimeterCm = Double(perimeterPixels) / pixelsPerMM / 10.0
        
        return WoundGroundTruth(
            imageName: imageName,
            segmentationMask: mask,
            contours: contours,
            areaPixels: areaPixels,
            areaCm2: areaCm2,
            perimeterPixels: perimeterPixels,
            perimeterCm: perimeterCm,
            boundingBox: maskAnalysis.boundingBox,
            confidence: 1.0, // Ground Truth 完全可信
            source: "FUSeg Challenge Dataset"
        )
    }
    
    // MARK: - 行動端模擬方法
    
    /// 對指定圖像執行行動端模擬處理
    private func simulateMobileProcessingForImage(_ image: UIImage) async throws -> SimulationResult {
        logger.info("執行行動端模擬處理，圖像尺寸: \(image.size)")
        
        // 使用已建立的MobileComputeSimulator
        let simulationResult = try await mobileSimulator.simulateMobileProcessing(
            image,
            withDepthData: nil // FUSeg數據集沒有深度數據
        )
        
        return simulationResult
    }
    
    // MARK: - 比對分析方法
    
    /// 與Ground Truth進行詳細比對
    private func compareWithGroundTruth(mobileResult: SimulationResult, 
                                      groundTruth: WoundGroundTruth, 
                                      imageName: String) async throws -> ImageComparisonResult {
        logger.info("與Ground Truth比對: \(imageName)")
        
        // 1. 分割準確度比對
        let segmentationComparison = try await compareSegmentation(
            mobileSegmentation: mobileResult.mobileAnalysis.segmentation,
            groundTruthMask: groundTruth.segmentationMask,
            imageName: imageName
        )
        
        // 2. 測量準確度比對
        let measurementComparison = try await compareMeasurement(
            mobileMeasurement: mobileResult.mobileAnalysis.measurement,
            groundTruthArea: groundTruth.areaCm2,
            groundTruthPerimeter: groundTruth.perimeterCm
        )
        
        // 3. 特徵提取比對
        let featureComparison = try await compareFeatures(
            mobileFeatures: extractMobileFeatures(mobileResult),
            groundTruthFeatures: extractGroundTruthFeatures(groundTruth)
        )
        
        // 4. 計算綜合差異分析
        let differenceAnalysis = analyzeDifferences(
            segmentationComparison: segmentationComparison,
            measurementComparison: measurementComparison,
            featureComparison: featureComparison
        )
        
        return ImageComparisonResult(
            imageName: imageName,
            segmentationComparison: segmentationComparison,
            measurementComparison: measurementComparison,
            featureComparison: featureComparison,
            differenceAnalysis: differenceAnalysis,
            overallAccuracy: calculateOverallAccuracy([
                segmentationComparison.diceScore,
                measurementComparison.areaAccuracy,
                featureComparison.overallSimilarity
            ]),
            processingTime: mobileResult.performanceMetrics.totalProcessingTime
        )
    }
    
    /// 分割結果比對
    private func compareSegmentation(mobileSegmentation: SegmentationOutput,
                                   groundTruthMask: UIImage,
                                   imageName: String) async throws -> SegmentationComparison {
        logger.info("比對分割結果: \(imageName)")
        
        // 將行動端分割結果轉換為二值化遮罩
        guard let mobileMask = generateBinaryMask(from: mobileSegmentation) else {
            throw RealDataError.segmentationProcessingFailed
        }
        
        guard let mobileGrayImage = mobileMask.cgImage,
              let groundTruthGrayImage = groundTruthMask.cgImage else {
            throw RealDataError.maskConversionFailed
        }
        
        // 確保兩個遮罩尺寸一致
        let resizedMobileMask = try await resizeImage(mobileGrayImage, to: groundTruthGrayImage.width, height: groundTruthGrayImage.height)
        
        // 計算經典分割指標
        let metrics = try await calculateSegmentationMetrics(
            predicted: resizedMobileMask,
            groundTruth: groundTruthGrayImage
        )
        
        return SegmentationComparison(
            diceScore: metrics.diceScore,
            iouScore: metrics.iouScore,
            precision: metrics.precision,
            recall: metrics.recall,
            f1Score: metrics.f1Score,
            hausdorffDistance: metrics.hausdorffDistance,
            meanSurfaceDistance: metrics.meanSurfaceDistance,
            volumeOverlapError: metrics.volumeOverlapError
        )
    }
    
    /// 測量結果比對
    private func compareMeasurement(mobileMeasurement: MeasurementOutput,
                                  groundTruthArea: Double,
                                  groundTruthPerimeter: Double) async throws -> MeasurementComparison {
        logger.info("比對測量結果")
        
        // 面積準確度
        let areaError = abs(mobileMeasurement.area - groundTruthArea)
        let areaAccuracy = 1.0 - min(areaError / max(groundTruthArea, 0.001), 1.0)
        
        // 周長準確度  
        let perimeterError = abs(mobileMeasurement.perimeter - groundTruthPerimeter)
        let perimeterAccuracy = 1.0 - min(perimeterError / max(groundTruthPerimeter, 0.001), 1.0)
        
        // 相對誤差
        let relativeAreaError = areaError / max(groundTruthArea, 0.001)
        let relativePerimeterError = perimeterError / max(groundTruthPerimeter, 0.001)
        
        return MeasurementComparison(
            mobileArea: mobileMeasurement.area,
            groundTruthArea: groundTruthArea,
            areaError: areaError,
            areaAccuracy: areaAccuracy,
            relativeAreaError: relativeAreaError,
            mobilePerimeter: mobileMeasurement.perimeter,
            groundTruthPerimeter: groundTruthPerimeter,
            perimeterError: perimeterError,
            perimeterAccuracy: perimeterAccuracy,
            relativePerimeterError: relativePerimeterError
        )
    }
    
    // MARK: - 綜合分析方法
    
    /// 執行整體比對分析
    private func performOverallAnalysis(comparisonResults: [ImageComparisonResult]) async throws -> OverallComparisonAnalysis {
        logger.info("執行整體比對分析，樣本數量: \(comparisonResults.count)")
        
        guard !comparisonResults.isEmpty else {
            throw RealDataError.noComparisonResults
        }
        
        // 統計指標計算
        let overallStats = calculateOverallStatistics(comparisonResults)
        
        // 差異原因分析
        let differenceAnalysis = analyzeDifferencePatterns(comparisonResults)
        
        // 優化建議生成
        let optimizationRecommendations = generateOptimizationRecommendations(
            statistics: overallStats,
            patterns: differenceAnalysis
        )
        
        // 性能評估
        let performanceAnalysis = analyzePerformanceCharacteristics(comparisonResults)
        
        return OverallComparisonAnalysis(
            totalSamples: comparisonResults.count,
            overallStatistics: overallStats,
            differencePatterns: differenceAnalysis,
            optimizationRecommendations: optimizationRecommendations,
            performanceAnalysis: performanceAnalysis,
            medicalGradeAssessment: assessMedicalGradeReadiness(overallStats),
            conclusionsAndInsights: generateConclusionsAndInsights(overallStats, differenceAnalysis)
        )
    }
    
    /// 計算整體統計指標
    private func calculateOverallStatistics(_ results: [ImageComparisonResult]) -> OverallStatistics {
        let diceScores = results.map { $0.segmentationComparison.diceScore }
        let iouScores = results.map { $0.segmentationComparison.iouScore }
        let areaAccuracies = results.map { $0.measurementComparison.areaAccuracy }
        let perimeterAccuracies = results.map { $0.measurementComparison.perimeterAccuracy }
        let overallAccuracies = results.map { $0.overallAccuracy }
        let processingTimes = results.map { $0.processingTime }
        
        return OverallStatistics(
            // 分割指標
            avgDiceScore: diceScores.average,
            stdDiceScore: diceScores.standardDeviation,
            minDiceScore: diceScores.min() ?? 0.0,
            maxDiceScore: diceScores.max() ?? 0.0,
            
            avgIouScore: iouScores.average,
            stdIouScore: iouScores.standardDeviation,
            
            // 測量指標  
            avgAreaAccuracy: areaAccuracies.average,
            stdAreaAccuracy: areaAccuracies.standardDeviation,
            
            avgPerimeterAccuracy: perimeterAccuracies.average,
            stdPerimeterAccuracy: perimeterAccuracies.standardDeviation,
            
            // 整體指標
            avgOverallAccuracy: overallAccuracies.average,
            stdOverallAccuracy: overallAccuracies.standardDeviation,
            
            // 性能指標
            avgProcessingTime: processingTimes.average,
            stdProcessingTime: processingTimes.standardDeviation,
            
            // 醫療級評估
            samplesAbove90Percent: results.filter { $0.overallAccuracy > 0.9 }.count,
            samplesAbove95Percent: results.filter { $0.overallAccuracy > 0.95 }.count,
            
            consistencyIndex: 1.0 - (overallAccuracies.standardDeviation / max(overallAccuracies.average, 0.001))
        )
    }
    
    // MARK: - 輔助方法
    
    @MainActor
    private func updateProgress(_ progress: Double) {
        analysisProgress = progress
    }
    
    private func calculateOverallAccuracy(_ accuracies: [Double]) -> Double {
        guard !accuracies.isEmpty else { return 0.0 }
        return accuracies.reduce(0, +) / Double(accuracies.count)
    }
}

// MARK: - 支援資料結構

struct TestImagePair {
    let imageName: String
    let image: UIImage
    let groundTruth: WoundGroundTruth
}

struct WoundGroundTruth {
    let imageName: String
    let segmentationMask: UIImage
    let contours: [WoundContour]
    let areaPixels: Int
    let areaCm2: Double
    let perimeterPixels: Int
    let perimeterCm: Double
    let boundingBox: CGRect
    let confidence: Double
    let source: String
}

struct ImageComparisonResult {
    let imageName: String
    let segmentationComparison: SegmentationComparison
    let measurementComparison: MeasurementComparison
    let featureComparison: FeatureComparison
    let differenceAnalysis: DifferenceAnalysis
    let overallAccuracy: Double
    let processingTime: TimeInterval
}

struct SegmentationComparison {
    let diceScore: Double
    let iouScore: Double
    let precision: Double
    let recall: Double
    let f1Score: Double
    let hausdorffDistance: Double
    let meanSurfaceDistance: Double
    let volumeOverlapError: Double
}

struct MeasurementComparison {
    let mobileArea: Double
    let groundTruthArea: Double
    let areaError: Double
    let areaAccuracy: Double
    let relativeAreaError: Double
    
    let mobilePerimeter: Double
    let groundTruthPerimeter: Double
    let perimeterError: Double
    let perimeterAccuracy: Double
    let relativePerimeterError: Double
}

struct FeatureComparison {
    let colorSimilarity: Double
    let textureSimilarity: Double
    let shapeSimilarity: Double
    let overallSimilarity: Double
}

struct DifferenceAnalysis {
    let primaryDifferenceType: DifferenceType
    let differenceScore: Double
    let contributingFactors: [ContributingFactor]
    let suggestedImprovements: [String]
}

enum DifferenceType {
    case segmentationInaccuracy
    case measurementError
    case featureMismatch
    case processingArtifacts
    case algorithmLimitation
}

struct ContributingFactor {
    let factor: String
    let impact: Double
    let description: String
}

struct OverallStatistics {
    let avgDiceScore: Double
    let stdDiceScore: Double
    let minDiceScore: Double
    let maxDiceScore: Double
    
    let avgIouScore: Double
    let stdIouScore: Double
    
    let avgAreaAccuracy: Double
    let stdAreaAccuracy: Double
    
    let avgPerimeterAccuracy: Double
    let stdPerimeterAccuracy: Double
    
    let avgOverallAccuracy: Double
    let stdOverallAccuracy: Double
    
    let avgProcessingTime: Double
    let stdProcessingTime: Double
    
    let samplesAbove90Percent: Int
    let samplesAbove95Percent: Int
    let consistencyIndex: Double
}

struct RealDataAnalysisResult {
    let comparisonResults: [ImageComparisonResult]
    let overallAnalysis: OverallComparisonAnalysis
    let analysisTimestamp: Date
    let totalSamples: Int
}

struct OverallComparisonAnalysis {
    let totalSamples: Int
    let overallStatistics: OverallStatistics
    let differencePatterns: [DifferencePattern]
    let optimizationRecommendations: [OptimizationRecommendation]
    let performanceAnalysis: PerformanceAnalysis
    let medicalGradeAssessment: MedicalGradeAssessment
    let conclusionsAndInsights: [String]
}

enum RealDataError: Error {
    case directoryNotFound(String)
    case invalidGroundTruthMask
    case segmentationProcessingFailed
    case maskConversionFailed
    case noComparisonResults
    case imageProcessingFailed
}