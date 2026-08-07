import SwiftUI
import UIKit

struct WoundVisualizationView: View {
    let result: WoundMeasurementResult
    let image: UIImage
    let depthData: Data
    
    @StateObject private var visualizationModule = VisualizationModule()
    @State private var selectedVisualizationMode: VisualizationMode = .combined
    @State private var isGeneratingVisualization = false
    @State private var visualizationResult: VisualizationResult?
    
    enum VisualizationMode: CaseIterable {
        case original
        case areaMask
        case depthGradient
        case combined
        case measurement
        
        var title: String {
            switch self {
            case .original: return "原始圖像"
            case .areaMask: return "面積遮罩"
            case .depthGradient: return "深度漸層"
            case .combined: return "綜合視圖"
            case .measurement: return "測量參數"
            }
        }
    }
    
    var body: some View {
        NavigationView {
            VStack(spacing: 0) {
                // 視覺化模式選擇器
                VisualizationModeSelector(
                    selectedMode: $selectedVisualizationMode,
                    modes: VisualizationMode.allCases
                )
                
                // 主要視覺化顯示區域
                MainVisualizationView(
                    selectedMode: selectedVisualizationMode,
                    visualizationResult: visualizationResult,
                    isGenerating: isGeneratingVisualization
                )
                
                // 分析參數顯示
                AnalysisParametersView(result: result)
                
                Spacer()
            }
            .navigationTitle("傷口視覺化分析")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    ShareButton(
                        visualizationResult: visualizationResult,
                        result: result
                    )
                }
            }
            .onAppear {
                generateVisualization()
            }
        }
    }
    
    private func generateVisualization() {
        guard !isGeneratingVisualization else { return }
        
        isGeneratingVisualization = true
        
        Task {
            // 創建模擬測量數據（在實際應用中會來自ImageJ分析）
            let mockMeasurement = WoundMeasurement(
                area: result.area ?? 0.0,
                perimeter: 0.0,
                volume: result.volume ?? 0.0,
                maxDepth: 0.0,
                avgDepth: 0.0,
                length: 0.0,
                width: 0.0,
                tissueComposition: TissueComposition(
                    healthyPercentage: 0.0,
                    granulationPercentage: 0.6,
                    necroticPercentage: 0.2,
                    epithelialPercentage: 0.2,
                    fibrinPercentage: 0.0,
                    sloughPercentage: 0.0
                ),
                qualityMetrics: QualityMetrics.defaultMetrics(),
                depthQuality: DepthQualityInfo(
                    validPixelRatio: 0.8,
                    averageConfidence: 0.7,
                    depthConsistency: 0.8,
                    noiseLevel: 0.1,
                    coverageInROI: 0.85
                ),
                cameraDistance: 30.0,
                pixelScale: 0.1,
                timestamp: Date()
            )
            
            // 創建模擬分割結果
            let mockSegmented = SegmentedImage(
                originalImage: image,
                contours: [WoundContour(
                    points: generateMockContourPoints(),
                    area: result.area ?? 0.0,
                    perimeter: 0.0
                )]
            )
            
            // 生成各種視覺化
            let areaMask = visualizationModule.generateAreaMask(mockSegmented, size: image.size)
            let depthGradient = visualizationModule.generateDepthGradient(depthData, size: image.size)
            let measurementOverlay = visualizationModule.generateMeasurementOverlay(mockMeasurement, size: image.size)
            let combinedImage = visualizationModule.combineVisualizations(
                original: image,
                areaMask: areaMask,
                depthGradient: depthGradient,
                overlay: measurementOverlay
            )
            
            let visualization = VisualizationResult(
                originalImage: image,
                areaMask: areaMask,
                depthGradient: depthGradient,
                measurementOverlay: measurementOverlay,
                combinedImage: combinedImage,
                measurement: mockMeasurement
            )
            
            await MainActor.run {
                self.visualizationResult = visualization
                self.isGeneratingVisualization = false
            }
        }
    }
    
    private func generateMockContourPoints() -> [CGPoint] {
        // 生成模擬輪廓點（橢圓形）
        var points: [CGPoint] = []
        let centerX: CGFloat = 0.5
        let centerY: CGFloat = 0.5
        let radiusX: CGFloat = 0.1
        let radiusY: CGFloat = 0.08
        
        for i in 0..<36 {
            let angle = Double(i) * 2.0 * Double.pi / 36.0
            let x = centerX + radiusX * CGFloat(cos(angle))
            let y = centerY + radiusY * CGFloat(sin(angle))
            points.append(CGPoint(x: x, y: y))
        }
        
        return points
    }
}

struct VisualizationModeSelector: View {
    @Binding var selectedMode: WoundVisualizationView.VisualizationMode
    let modes: [WoundVisualizationView.VisualizationMode]
    
    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 12) {
                ForEach(modes, id: \.self) { mode in
                    Button(action: {
                        selectedMode = mode
                    }) {
                        Text(mode.title)
                            .font(.caption)
                            .padding(.horizontal, 12)
                            .padding(.vertical, 6)
                            .background(
                                selectedMode == mode ? 
                                Color.blue : Color.gray.opacity(0.2)
                            )
                            .foregroundColor(
                                selectedMode == mode ? .white : .primary
                            )
                            .cornerRadius(16)
                    }
                }
            }
            .padding(.horizontal)
        }
        .padding(.vertical, 8)
        .background(Color.gray.opacity(0.1))
    }
}

struct MainVisualizationView: View {
    let selectedMode: WoundVisualizationView.VisualizationMode
    let visualizationResult: VisualizationResult?
    let isGenerating: Bool
    
    var body: some View {
        ZStack {
            if isGenerating {
                VStack {
                    ProgressView()
                        .scaleEffect(1.5)
                    Text("生成視覺化中...")
                        .padding(.top)
                }
            } else if let result = visualizationResult {
                displayVisualization(for: selectedMode, result: result)
            } else {
                Text("視覺化生成失敗")
                    .foregroundColor(.secondary)
            }
        }
        .frame(maxHeight: 400)
        .background(Color.black.opacity(0.05))
    }
    
    @ViewBuilder
    private func displayVisualization(
        for mode: WoundVisualizationView.VisualizationMode,
        result: VisualizationResult
    ) -> some View {
        switch mode {
        case .original:
            Image(uiImage: result.originalImage)
                .resizable()
                .aspectRatio(contentMode: .fit)
            
        case .areaMask:
            if let maskImage = result.areaMask {
                ZStack {
                    Image(uiImage: result.originalImage)
                        .resizable()
                        .aspectRatio(contentMode: .fit)
                        .opacity(0.7)
                    
                    Image(uiImage: maskImage)
                        .resizable()
                        .aspectRatio(contentMode: .fit)
                }
            } else {
                Text("面積遮罩生成失敗")
            }
            
        case .depthGradient:
            if let depthImage = result.depthGradient {
                ZStack {
                    Image(uiImage: result.originalImage)
                        .resizable()
                        .aspectRatio(contentMode: .fit)
                        .opacity(0.5)
                    
                    Image(uiImage: depthImage)
                        .resizable()
                        .aspectRatio(contentMode: .fit)
                }
            } else {
                Text("深度視覺化生成失敗")
            }
            
        case .combined:
            if let combinedImage = result.combinedImage {
                Image(uiImage: combinedImage)
                    .resizable()
                    .aspectRatio(contentMode: .fit)
            } else {
                Text("綜合視圖生成失敗")
            }
            
        case .measurement:
            MeasurementOverlayView(
                originalImage: result.originalImage,
                measurement: result.measurement
            )
        }
    }
}

struct MeasurementOverlayView: View {
    let originalImage: UIImage
    let measurement: WoundMeasurement
    
    var body: some View {
        ZStack {
            Image(uiImage: originalImage)
                .resizable()
                .aspectRatio(contentMode: .fit)
                .opacity(0.8)
            
            VStack(alignment: .leading) {
                HStack {
                    MeasurementCard(
                        title: "面積",
                        value: String(format: "%.2f cm²", measurement.area),
                        color: .blue
                    )
                    
                    MeasurementCard(
                        title: "體積",
                        value: String(format: "%.4f cm³", measurement.volume),
                        color: .green
                    )
                }
                
                HStack {
                    MeasurementCard(
                        title: "最大深度",
                        value: String(format: "%.2f mm", measurement.maxDepth * 10),
                        color: .orange
                    )
                    
                    MeasurementCard(
                        title: "平均深度",
                        value: String(format: "%.2f mm", measurement.avgDepth * 10),
                        color: .purple
                    )
                }
                
                Spacer()
            }
            .padding()
        }
    }
}

struct MeasurementCard: View {
    let title: String
    let value: String
    let color: Color
    
    var body: some View {
        VStack {
            Text(title)
                .font(.caption)
                .foregroundColor(.white)
            
            Text(value)
                .font(.headline)
                .fontWeight(.bold)
                .foregroundColor(.white)
        }
        .padding(8)
        .background(color.opacity(0.8))
        .cornerRadius(8)
    }
}

struct AnalysisParametersView: View {
    let result: WoundMeasurementResult
    
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("分析參數")
                .font(.headline)
                .padding(.horizontal)
            
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 12) {
                    if let area = result.area {
                        ParameterChip(
                            icon: "square.fill",
                            label: "面積",
                            value: "\(String(format: "%.2f", area)) cm²",
                            color: .blue
                        )
                    }
                    
                    if let volume = result.volume {
                        ParameterChip(
                            icon: "cube.fill",
                            label: "體積",
                            value: "\(String(format: "%.4f", volume)) cm³",
                            color: .green
                        )
                    }
                    
                    if let classification = result.classification {
                        ParameterChip(
                            icon: "chart.bar.fill",
                            label: "急性機率",
                            value: "\(String(format: "%.1f", classification.acuteScore * 100))%",
                            color: .orange
                        )
                        
                        ParameterChip(
                            icon: "clock.fill",
                            label: "慢性機率",
                            value: "\(String(format: "%.1f", classification.chronicScore * 100))%",
                            color: .purple
                        )
                        
                        ParameterChip(
                            icon: "checkmark.circle.fill",
                            label: "信心度",
                            value: "\(String(format: "%.1f", classification.confidence * 100))%",
                            color: .green
                        )
                    }
                }
                .padding(.horizontal)
            }
        }
        .padding(.vertical)
        .background(Color.gray.opacity(0.1))
    }
}

struct ParameterChip: View {
    let icon: String
    let label: String
    let value: String
    let color: Color
    
    var body: some View {
        VStack(spacing: 4) {
            Image(systemName: icon)
                .foregroundColor(color)
                .font(.title3)
            
            Text(label)
                .font(.caption)
                .foregroundColor(.secondary)
            
            Text(value)
                .font(.caption)
                .fontWeight(.semibold)
        }
        .padding(8)
        .background(Color.white)
        .cornerRadius(8)
        .shadow(radius: 2)
    }
}

struct ShareButton: View {
    let visualizationResult: VisualizationResult?
    let result: WoundMeasurementResult
    
    var body: some View {
        Button("分享") {
            shareVisualization()
        }
    }
    
    private func shareVisualization() {
        guard let visualization = visualizationResult,
              let combinedImage = visualization.combinedImage else { return }
        
        let activityVC = UIActivityViewController(
            activityItems: [combinedImage],
            applicationActivities: nil
        )
        
        if let windowScene = UIApplication.shared.connectedScenes.first as? UIWindowScene,
           let window = windowScene.windows.first {
            window.rootViewController?.present(activityVC, animated: true)
        }
    }
}

// 使用 WoundTypes.swift 中定義的結構

#Preview {
    WoundVisualizationView(
        result: WoundMeasurementResult(
            area: 12.5,
            volume: 2.3,
            classification: DetailedWoundClassification(
                acuteScore: 0.8,
                chronicScore: 0.2,
                infectedScore: 0.1,
                healingScore: 0.7,
                confidence: 0.85
            ),
            timestamp: Date()
        ),
        image: UIImage(systemName: "photo")!,
        depthData: Data()
    )
}