# iOS 舊架構隔離清單

`iOS/_quarantine/` 底下的檔案不參與 build。這份文件說明為什麼，以及要救回時該做什麼。

## 為什麼隔離

舊 `.xcodeproj` 的 App target 收了 89 個 `.swift`。靜態稽核在**這些檔案之內**
找出 **58 個頂層符號重複宣告**。在同一個 Swift module 裡，這些是硬性的
`invalid redeclaration of 'X'` 錯誤——換句話說，**那份專案從來沒有編譯成功過**。

另有兩個 build 輸入引用了 repo 裡不存在的檔案：`opencv2.xcframework`、`UNet256.mlmodel`；
以及一個 XCTest 檔（`Tests/MeasurementEngineTests.swift`）被誤編進 App target。

Android 端做過同一個決定：16 個壞掉的檔案改名 `.kt.disabled` 隔離，核心才編得起來。
這裡是同一招，只是規模較大。

## 要救回某個功能時

1. 從下表找出該檔涉及的所有衝突符號
2. 逐一改名或合併（保留一份定義）
3. 把檔案路徑加進 `iOS/project.yml` 的 `sources`
4. `xcodegen generate` 後重編

不要整批加回去——衝突同時處理時錯誤訊息會互相掩蓋。

## 重複宣告全表（58 個符號）

| 符號 | 宣告處 |
|---|---|
| `ARDepthData` | `Modules/Enhanced/ARDepthVolumeCalculator.swift:612` (struct)<br>`Modules/Enhanced/CalibrationStickerDetector.swift:710` (typealias) |
| `AccuracyAssessment` | `Modules/Enhanced/ARDepthVolumeCalculator.swift:686` (struct)<br>`Modules/Enhanced/MedicalGradeValidationSystem.swift:719` (struct) |
| `AdaptiveParameters` | `Modules/Enhanced/AdaptiveSegmentationModule.swift:547` (struct)<br>`Modules/MobileOptimizer.swift:611` (struct) |
| `AnalysisPattern` | `Analysis/ExecuteAnalysisReport.swift:132` (struct)<br>`Utils/SimulatedAnalysisExecutor.swift:582` (struct) |
| `AnalysisType` | `Analysis/ExecuteAnalysisReport.swift:113` (enum)<br>`Modules/RealDataAnalysisController.swift:429` (enum) |
| `AnnotationData` | `Models/ViewSupportTypes.swift:75` (struct)<br>`Modules/CaptureModule.swift:784` (struct) |
| `CalibrationData` | `Modules/Enhanced/AdaptiveSegmentationModule.swift:525` (struct)<br>`Modules/MobileImageProcessor.swift:470` (struct) |
| `CalibrationResult` | `Models/ViewSupportTypes.swift:35` (struct)<br>`Modules/RulerCalibrationModule.swift:738` (struct) |
| `CertificationReadiness` | `Modules/Enhanced/MedicalGradeValidationSystem.swift:703` (enum)<br>`Modules/MedicalGradeValidator.swift:575` (enum) |
| `CloudAPIService` | `Services/CloudAPIService.swift:101` (class)<br>`Views/AnnotationView.swift:28` (class) |
| `CloudAnalysisResult` | `Models/ViewSupportTypes.swift:56` (struct)<br>`Services/WoundAnalysisAPIService.swift:503` (struct) |
| `CloudUploadResponse` | `Services/CloudAPIService.swift:14` (struct)<br>`Views/AnnotationView.swift:7` (struct) |
| `ColorDistribution` | `Models/WoundTypes.swift:257` (struct)<br>`Modules/Enhanced/AdaptiveParametersController.swift:655` (struct) |
| `ComplexityLevel` | `Analysis/ExecuteAnalysisReport.swift:30` (enum)<br>`Modules/MobileOptimizer.swift:581` (enum)<br>`Modules/RealDataAnalysisController.swift:485` (enum) |
| `ComplianceStatus` | `Modules/Enhanced/MedicalGradeValidationSystem.swift:699` (enum)<br>`Modules/MedicalGradeValidator.swift:594` (struct) |
| `ComprehensiveAnalysisResult` | `Analysis/ExecuteAnalysisReport.swift:60` (struct)<br>`Modules/RealDataAnalysisController.swift:410` (struct) |
| `ConflictResolutionMethod` | `Models/WoundTypes.swift:952` (enum)<br>`Modules/AdvancedMLClassificationModule.swift:753` (enum) |
| `DepthMap` | `Modules/Enhanced/ARDepthVolumeCalculator.swift:741` (typealias)<br>`Modules/ImageJCore.swift:410` (struct) |
| `DetectedCircle` | `Models/WoundTypes.swift:830` (struct)<br>`Modules/Enhanced/CalibrationStickerDetector.swift:603` (struct) |
| `DifferenceAnalysisResult` | `Analysis/ExecuteAnalysisReport.swift:102` (struct)<br>`Modules/RealDataAnalysisController.swift:418` (struct) |
| `ExecutionSummary` | `Analysis/ExecuteAnalysisReport.swift:53` (struct)<br>`Modules/RealDataAnalysisController.swift:506` (struct) |
| `HealingStage` | `Models/WoundTypes.swift:662` (enum)<br>`Modules/AdvancedMLClassificationModule.swift:730` (enum)<br>`Modules/CloudResultComparator.swift:437` (enum) |
| `HistoricalMeasurement` | `Models/ViewSupportTypes.swift:7` (struct)<br>`Views/AnalysisHistoryView.swift:707` (struct) |
| `ImageJError` | `Modules/ImageJHeadlessProcessor.swift:823` (enum)<br>`Modules/RealTimeAnalysisModule.swift:400` (enum) |
| `ImagePicker` | `Views/AnnotationView.swift:703` (struct)<br>`Views/CalibrationSelectionView.swift:328` (struct)<br>`Views/CalibrationStickerView.swift:603` (struct)<br>`Views/StandardStickerCalibrationView.swift:357` (struct) |
| `InfoCard` | `Views/BatchReportView.swift:439` (struct)<br>`Views/Wound3DVisualizationView.swift:356` (struct) |
| `MLError` | `Models/WoundTypes.swift:804` (enum)<br>`Modules/AdvancedMLClassificationModule.swift:761` (enum) |
| `MeasurementBanner` | `Views/ARCameraPreviewView.swift:1052` (struct)<br>`Views/EnhancedMeasurementBanner.swift:3` (struct) |
| `MedicalGradeAssessment` | `Analysis/ExecuteAnalysisReport.swift:96` (struct)<br>`Utils/SimulatedAnalysisExecutor.swift:605` (struct) |
| `MedicalGradeLevel` | `Analysis/ExecuteAnalysisReport.swift:17` (enum)<br>`Modules/MedicalGradeValidator.swift:559` (enum) |
| `MedicalValidationResult` | `Modules/Enhanced/MedicalGradeValidationSystem.swift:671` (struct)<br>`Modules/MedicalGradeValidator.swift:610` (struct) |
| `MetricCard` | `Views/LocalCloudIntegrationView.swift:422` (struct)<br>`Views/PerformanceDashboardView.swift:314` (struct) |
| `MultiScaleSegmentationResult` | `Modules/Enhanced/AdaptiveSegmentationModule.swift:593` (struct)<br>`Modules/Enhanced/MultiScaleSegmentationEngine.swift:785` (struct) |
| `OptimizationSuggestion` | `Analysis/ExecuteAnalysisReport.swift:43` (struct)<br>`Modules/RealDataAnalysisController.swift:466` (struct) |
| `OverallStatistics` | `Analysis/ExecuteAnalysisReport.swift:78` (struct)<br>`Modules/RealDataComparator.swift:516` (struct) |
| `PerformanceMetrics` | `Modules/MobileComputeSimulator.swift:424` (struct)<br>`Services/EnhancedPerformanceMonitor.swift:233` (struct) |
| `PoorPerformanceCase` | `Analysis/ExecuteAnalysisReport.swift:125` (struct)<br>`Modules/RealDataAnalysisController.swift:445` (struct) |
| `PriorityLevel` | `Analysis/ExecuteAnalysisReport.swift:4` (enum)<br>`Modules/MobileOptimizer.swift:566` (enum) |
| `ProcessingError` | `Modules/MultiScaleImageProcessor.swift:822` (enum)<br>`Views/ARCameraPreviewView.swift:970` (enum) |
| `RealDataAnalysisResult` | `Analysis/ExecuteAnalysisReport.swift:68` (struct)<br>`Modules/RealDataComparator.swift:542` (struct) |
| `RecommendationCategory` | `Modules/Enhanced/MedicalGradeValidationSystem.swift:841` (enum)<br>`Modules/LocalCloudIntegrationController.swift:570` (enum) |
| `RecommendedAction` | `Models/WoundTypes.swift:925` (enum)<br>`Modules/AdvancedMLClassificationModule.swift:746` (enum) |
| `RiskAssessment` | `Modules/ClassificationModule.swift:686` (struct)<br>`Modules/MedicalGradeValidator.swift:526` (struct) |
| `RiskLevel` | `Models/WoundTypes.swift:712` (enum)<br>`Modules/MedicalGradeValidator.swift:567` (enum)<br>`Modules/MobileOptimizer.swift:585` (enum) |
| `RootCause` | `Analysis/ExecuteAnalysisReport.swift:138` (struct)<br>`Modules/RealDataAnalysisController.swift:460` (struct) |
| `SegmentationError` | `ContentView.swift:942` (struct)<br>`Modules/Enhanced/AdaptiveSegmentationModule.swift:570` (enum) |
| `StickerCalibrationError` | `Modules/CalibrationStickerModule.swift:2116` (enum)<br>`Views/StandardStickerCalibrationView.swift:340` (enum) |
| `TextureAnalysis` | `Models/WoundTypes.swift:894` (struct)<br>`Modules/AdvancedMLClassificationModule.swift:723` (struct) |
| `TextureFeatures` | `Modules/ClassificationModule.swift:671` (struct)<br>`Modules/Enhanced/AdaptiveParametersController.swift:661` (struct) |
| `TissueColor` | `Models/WoundTypes.swift:876` (enum)<br>`Modules/AdvancedMLClassificationModule.swift:707` (enum) |
| `TissueComposition` | `Models/WoundTypes.swift:589` (struct)<br>`Services/CloudAPIService.swift:81` (struct) |
| `TissueType` | `Models/WoundTypes.swift:617` (enum)<br>`Modules/AdvancedMLClassificationModule.swift:674` (enum) |
| `ValidationResult` | `Modules/LocalSyncSimulationEngine.swift:428` (struct)<br>`Modules/MobileComputeSimulator.swift:407` (struct) |
| `VascularityLevel` | `Models/WoundTypes.swift:886` (enum)<br>`Modules/AdvancedMLClassificationModule.swift:716` (enum) |
| `VolumeCalculationResult` | `Modules/Enhanced/ARDepthVolumeCalculator.swift:666` (struct)<br>`Services/WoundAnalysisAPIService.swift:570` (struct) |
| `WoundRiskAssessment` | `Models/WoundTypes.swift:691` (struct)<br>`Modules/AdvancedMLClassificationModule.swift:738` (struct) |
| `WoundSeverity` | `Modules/CloudResultComparator.swift:433` (enum)<br>`Modules/Enhanced/ARDepthVolumeCalculator.swift:760` (enum) |
| `WoundType` | `Models/WoundTypes.swift:20` (enum)<br>`Modules/CloudResultComparator.swift:429` (enum) |

## 隔離的檔案

### `_quarantine/Pipeline/`

- `CameraCaptureView.swift`
- `MeasureView.swift`
- `MeasureViewModel.swift`
- `WoundNavigation.swift`

### `_quarantine/Tests/`

- `BatchProcessingServiceTests.swift`
- `IntegrationTests.swift`
- `MeasurementEngineTests.swift`
- `PDFReportGeneratorTests.swift`
- `PushScorerTests.swift`
- `TestHelpers.swift`
- `TissueClassifierV2Tests.swift`
- `VisionBasedStickerDetectorTests.swift`
- `WoundVisualizationTests.swift`

### 未納入新 target 的舊目錄

`Modules/`、`Views/`、`Services/`、`Models/`、`Analysis/`、`Utils/`、`OpenCV/`、
`ContentView.swift`（5,813 行）、`WoundMeasurementApp.swift`（舊 @main）。

這些仍留在 git 裡，只是不在 build 中。其中含 5 個檔名明示的模擬／假資料模組
（`MockDataService`、`MobileComputeSimulator`、`LocalSyncSimulationEngine`、
`SimulatedAnalysisExecutor`、`FlywheelDemoView`），另有約 21 個內含亂數模擬邏輯——
它們的功能多半已由後端管線取代。
