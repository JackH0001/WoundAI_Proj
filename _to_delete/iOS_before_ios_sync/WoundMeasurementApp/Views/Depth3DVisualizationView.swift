import SwiftUI
import SceneKit
import UIKit

struct Depth3DVisualizationView: View {
    let depthData: Data
    let woundArea: Double
    
    @State private var isGenerating3D = false
    @State private var sceneView: SCNView?
    @State private var currentRotationX: Float = 0
    @State private var currentRotationY: Float = 0
    @State private var zoomScale: Float = 1.0
    
    var body: some View {
        VStack(spacing: 0) {
            // 3D視圖標題和控制
            VStack {
                HStack {
                    Text("3D深度視覺化")
                        .font(.headline)
                    
                    Spacer()
                    
                    Button(action: resetView) {
                        Image(systemName: "arrow.clockwise")
                            .foregroundColor(.blue)
                    }
                }
                
                Text("滑動旋轉 • 捏合縮放")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
            .padding()
            .background(Color.gray.opacity(0.1))
            
            // 3D場景視圖
            if isGenerating3D {
                VStack {
                    ProgressView()
                        .scaleEffect(1.5)
                    Text("生成3D模型中...")
                        .padding(.top)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(Color.black.opacity(0.05))
            } else {
                SceneKitView(
                    depthData: depthData,
                    onSceneCreated: { sceneView in
                        self.sceneView = sceneView
                    }
                )
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .clipped()
            }
            
            // 深度統計信息
            DepthStatisticsView(depthData: depthData, woundArea: woundArea)
                .padding()
                .background(Color.gray.opacity(0.1))
        }
        .onAppear {
            generate3DVisualization()
        }
    }
    
    private func generate3DVisualization() {
        isGenerating3D = true
        
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) {
            self.isGenerating3D = false
        }
    }
    
    private func resetView() {
        guard let sceneView = sceneView else { return }
        
        // 重置相機位置和角度
        if let cameraNode = sceneView.scene?.rootNode.childNode(withName: "camera", recursively: true) {
            SCNTransaction.begin()
            SCNTransaction.animationDuration = 0.5
            
            cameraNode.position = SCNVector3(0, 0, 10)
            cameraNode.eulerAngles = SCNVector3(0, 0, 0)
            
            SCNTransaction.commit()
        }
        
        // 在下一個運行循環中更新狀態以避免在視圖更新期間修改
        DispatchQueue.main.async {
            self.currentRotationX = 0
            self.currentRotationY = 0
            self.zoomScale = 1.0
        }
    }
}

struct SceneKitView: UIViewRepresentable {
    let depthData: Data
    let onSceneCreated: (SCNView) -> Void
    
    func makeUIView(context: Context) -> SCNView {
        let sceneView = SCNView()
        sceneView.backgroundColor = UIColor.black
        sceneView.allowsCameraControl = true
        sceneView.autoenablesDefaultLighting = true
        sceneView.antialiasingMode = .multisampling4X
        
        // 創建場景
        let scene = SCNScene()
        sceneView.scene = scene
        
        // 設置相機
        setupCamera(in: scene)
        
        // 生成3D深度網格
        generate3DMesh(from: depthData, in: scene)
        
        // 添加環境光
        setupLighting(in: scene)
        
        onSceneCreated(sceneView)
        
        return sceneView
    }
    
    func updateUIView(_ uiView: SCNView, context: Context) {}
    
    private func setupCamera(in scene: SCNScene) {
        let cameraNode = SCNNode()
        cameraNode.name = "camera"
        cameraNode.camera = SCNCamera()
        cameraNode.position = SCNVector3(0, 0, 10)
        cameraNode.camera?.automaticallyAdjustsZRange = true
        scene.rootNode.addChildNode(cameraNode)
    }
    
    private func setupLighting(in scene: SCNScene) {
        // 環境光
        let ambientLight = SCNNode()
        ambientLight.light = SCNLight()
        ambientLight.light?.type = .ambient
        ambientLight.light?.color = UIColor.white.withAlphaComponent(0.3)
        scene.rootNode.addChildNode(ambientLight)
        
        // 主光源
        let mainLight = SCNNode()
        mainLight.light = SCNLight()
        mainLight.light?.type = .directional
        mainLight.light?.color = UIColor.white
        mainLight.position = SCNVector3(5, 5, 5)
        mainLight.eulerAngles = SCNVector3(-Float.pi/4, Float.pi/4, 0)
        scene.rootNode.addChildNode(mainLight)
        
        // 輔助光源
        let fillLight = SCNNode()
        fillLight.light = SCNLight()
        fillLight.light?.type = .directional
        fillLight.light?.color = UIColor.white.withAlphaComponent(0.5)
        fillLight.position = SCNVector3(-5, 2, 5)
        fillLight.eulerAngles = SCNVector3(-Float.pi/6, -Float.pi/4, 0)
        scene.rootNode.addChildNode(fillLight)
    }
    
    private func generate3DMesh(from depthData: Data, in scene: SCNScene) {
        let width = 256
        let height = 192
        
        let floats = depthData.withUnsafeBytes { buffer in
            return buffer.bindMemory(to: Float32.self)
        }
        
        guard floats.count >= width * height else {
            print("深度數據不足以生成3D網格")
            return
        }
        
        var vertices: [SCNVector3] = []
        var indices: [Int32] = []
        var texCoords: [CGPoint] = []
        
        let scaleX: Float = 6.0 / Float(width)   // 調整X軸範圍
        let scaleY: Float = 4.5 / Float(height) // 調整Y軸範圍
        let scaleZ: Float = 2.0                 // 深度縮放係數
        
        // 生成頂點
        for y in 0..<height {
            for x in 0..<width {
                let index = y * width + x
                let depth = floats[index]
                
                let vertex = SCNVector3(
                    Float(x) * scaleX - 3.0,          // 置中X軸
                    (Float(height - y) * scaleY) - 2.25, // 置中Y軸，翻轉Y
                    -depth * scaleZ                    // Z軸深度（負值因為相機朝向）
                )
                vertices.append(vertex)
                
                // 紋理座標
                texCoords.append(CGPoint(
                    x: Double(x) / Double(width - 1),
                    y: Double(y) / Double(height - 1)
                ))
            }
        }
        
        // 生成三角形索引
        for y in 0..<(height - 1) {
            for x in 0..<(width - 1) {
                let topLeft = Int32(y * width + x)
                let topRight = Int32(y * width + (x + 1))
                let bottomLeft = Int32((y + 1) * width + x)
                let bottomRight = Int32((y + 1) * width + (x + 1))
                
                // 第一個三角形
                indices.append(topLeft)
                indices.append(bottomLeft)
                indices.append(topRight)
                
                // 第二個三角形
                indices.append(topRight)
                indices.append(bottomLeft)
                indices.append(bottomRight)
            }
        }
        
        // 創建幾何體
        let geometry = SCNGeometry(
            sources: [
                SCNGeometrySource(vertices: vertices),
                SCNGeometrySource(textureCoordinates: texCoords)
            ],
            elements: [
                SCNGeometryElement(indices: indices, primitiveType: .triangles)
            ]
        )
        
        // 設置材質
        let material = SCNMaterial()
        material.diffuse.contents = createDepthTexture(from: depthData, width: width, height: height)
        material.lightingModel = .lambert
        material.isDoubleSided = true
        geometry.materials = [material]
        
        // 創建節點並添加到場景
        let meshNode = SCNNode(geometry: geometry)
        scene.rootNode.addChildNode(meshNode)
        
        // 添加網格線框（可選）
        if let wireframeMaterial = createWireframeMaterial() {
            let wireframeGeometry = geometry.copy() as! SCNGeometry
            wireframeGeometry.materials = [wireframeMaterial]
            let wireframeNode = SCNNode(geometry: wireframeGeometry)
            wireframeNode.position = SCNVector3(0, 0, 0.001) // 稍微偏移避免Z-fighting
            scene.rootNode.addChildNode(wireframeNode)
        }
    }
    
    private func createDepthTexture(from depthData: Data, width: Int, height: Int) -> UIImage {
        let floats = depthData.withUnsafeBytes { buffer in
            return buffer.bindMemory(to: Float32.self)
        }
        
        let renderer = UIGraphicsImageRenderer(size: CGSize(width: width, height: height))
        
        return renderer.image { context in
            for y in 0..<height {
                for x in 0..<width {
                    let index = y * width + x
                    if index < floats.count {
                        let depth = Double(floats[index])
                        let normalizedDepth = min(depth / 1.0, 1.0) // 正規化到0-1
                        
                        // 深度到顏色映射：藍色（淺）到紅色（深）
                        let hue = (1.0 - normalizedDepth) * 0.7 // 從藍色到紅色
                        let color = UIColor(hue: hue, saturation: 0.8, brightness: 0.9, alpha: 1.0)
                        
                        let rect = CGRect(x: x, y: y, width: 1, height: 1)
                        color.setFill()
                        context.fill(rect)
                    }
                }
            }
        }
    }
    
    private func createWireframeMaterial() -> SCNMaterial? {
        let material = SCNMaterial()
        material.fillMode = .lines
        material.diffuse.contents = UIColor.white.withAlphaComponent(0.3)
        material.lightingModel = .constant
        return material
    }
}

struct DepthStatisticsView: View {
    let depthData: Data
    let woundArea: Double
    
    @State private var depthStats: DepthStatistics?
    
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("深度統計")
                .font(.headline)
            
            if let stats = depthStats {
                LazyVGrid(columns: [
                    GridItem(.flexible()),
                    GridItem(.flexible()),
                    GridItem(.flexible())
                ], spacing: 8) {
                    DepthStatCard(
                        title: "最大深度",
                        value: String(format: "%.2f mm", stats.maxDepth * 1000),
                        icon: "arrow.down",
                        color: .red
                    )
                    
                    DepthStatCard(
                        title: "平均深度",
                        value: String(format: "%.2f mm", stats.avgDepth * 1000),
                        icon: "minus",
                        color: .blue
                    )
                    
                    DepthStatCard(
                        title: "深度範圍",
                        value: String(format: "%.2f mm", (stats.maxDepth - stats.minDepth) * 1000),
                        icon: "arrow.up.arrow.down",
                        color: .green
                    )
                    
                    DepthStatCard(
                        title: "估計體積",
                        value: String(format: "%.2f cm³", stats.estimatedVolume),
                        icon: "cube",
                        color: .orange
                    )
                    
                    DepthStatCard(
                        title: "深度變化",
                        value: String(format: "%.3f", stats.depthVariance),
                        icon: "waveform",
                        color: .purple
                    )
                    
                    DepthStatCard(
                        title: "數據點",
                        value: "\(stats.validDataPoints)",
                        icon: "number",
                        color: .gray
                    )
                }
            } else {
                HStack {
                    ProgressView()
                        .scaleEffect(0.8)
                    Text("計算深度統計中...")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
            }
        }
        .onAppear {
            calculateDepthStatistics()
        }
    }
    
    private func calculateDepthStatistics() {
        DispatchQueue.global(qos: .userInitiated).async {
            let stats = self.computeDepthStatistics()
            
            DispatchQueue.main.async {
                self.depthStats = stats
            }
        }
    }
    
    private func computeDepthStatistics() -> DepthStatistics {
        let floats = depthData.withUnsafeBytes { buffer in
            return buffer.bindMemory(to: Float32.self)
        }
        
        let validDepths = floats.filter { $0 > 0.01 && $0 < 2.0 } // 過濾無效數據
        
        guard !validDepths.isEmpty else {
            return DepthStatistics(
                minDepth: 0, maxDepth: 0, avgDepth: 0,
                depthVariance: 0, estimatedVolume: 0,
                validDataPoints: 0
            )
        }
        
        let minDepth = validDepths.min() ?? 0
        let maxDepth = validDepths.max() ?? 0
        let avgDepth = validDepths.reduce(0, +) / Float(validDepths.count)
        
        // 計算變異數
        let variance = validDepths.reduce(0) { sum, depth in
            let diff = depth - avgDepth
            return sum + (diff * diff)
        } / Float(validDepths.count)
        
        // 簡化的體積估算（實際應用中會更複雜）
        let estimatedVolume = Double(avgDepth) * woundArea
        
        return DepthStatistics(
            minDepth: Double(minDepth),
            maxDepth: Double(maxDepth),
            avgDepth: Double(avgDepth),
            depthVariance: Double(variance),
            estimatedVolume: estimatedVolume,
            validDataPoints: validDepths.count
        )
    }
}

struct DepthStatCard: View {
    let title: String
    let value: String
    let icon: String
    let color: Color
    
    var body: some View {
        VStack(spacing: 4) {
            Image(systemName: icon)
                .foregroundColor(color)
                .font(.title3)
            
            Text(title)
                .font(.caption2)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
            
            Text(value)
                .font(.caption)
                .fontWeight(.semibold)
                .multilineTextAlignment(.center)
        }
        .padding(8)
        .background(Color.white)
        .cornerRadius(8)
        .shadow(radius: 1)
    }
}

struct DepthStatistics {
    let minDepth: Double
    let maxDepth: Double
    let avgDepth: Double
    let depthVariance: Double
    let estimatedVolume: Double
    let validDataPoints: Int
}

#Preview {
    Depth3DVisualizationView(
        depthData: Data(count: 256 * 192 * 4), // 模擬深度數據
        woundArea: 12.5
    )
}