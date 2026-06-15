import SwiftUI
import SceneKit
import UIKit

/// 3D傷口視覺化視圖 - 使用SceneKit展示傷口的3D模型
struct Wound3DVisualizationView: View {
    let woundData: WoundVisualizationData
    @State private var rotationAngle: Float = 0
    @State private var showingControls = true
    @State private var selectedView: ViewMode = .perspective
    @State private var showingMeasurements = true
    @State private var animationSpeed: Double = 1.0
    
    enum ViewMode: String, CaseIterable {
        case perspective = "透視圖"
        case front = "正視圖"
        case side = "側視圖"
        case top = "俯視圖"
        
        var cameraPosition: SCNVector3 {
            switch self {
            case .perspective:
                return SCNVector3(2, 2, 2)
            case .front:
                return SCNVector3(0, 0, 3)
            case .side:
                return SCNVector3(3, 0, 0)
            case .top:
                return SCNVector3(0, 3, 0)
            }
        }
    }
    
    var body: some View {
        NavigationView {
            VStack(spacing: 0) {
                // 3D場景視圖
                sceneView
                
                // 控制面板
                if showingControls {
                    controlPanel
                        .background(Color.gray.opacity(0.1))
                        .transition(.move(edge: .bottom))
                }
            }
            .navigationTitle("3D傷口視覺化")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button(showingControls ? "隱藏控制" : "顯示控制") {
                        withAnimation(.easeInOut(duration: 0.3)) {
                            showingControls.toggle()
                        }
                    }
                }
            }
        }
    }
    
    // MARK: - 3D場景視圖
    private var sceneView: some View {
        SceneView(
            scene: create3DScene(),
            options: [.allowsCameraControl, .autoenablesDefaultLighting]
        )
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .onAppear {
            startRotationAnimation()
        }
    }
    
    // MARK: - 控制面板
    private var controlPanel: some View {
        VStack(spacing: 16) {
            // 視圖模式選擇
            viewModeSelector
            
            // 測量顯示控制
            measurementControls
            
            // 旋轉和動畫控制
            animationControls
            
            // 傷口數據信息
            woundDataInfo
        }
        .padding()
    }
    
    // MARK: - 視圖模式選擇器
    private var viewModeSelector: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("視圖模式")
                .font(.headline)
            
            Picker("視圖模式", selection: $selectedView) {
                ForEach(ViewMode.allCases, id: \.self) { mode in
                    Text(mode.rawValue).tag(mode)
                }
            }
            .pickerStyle(SegmentedPickerStyle())
        }
    }
    
    // MARK: - 測量控制
    private var measurementControls: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("顯示選項")
                .font(.headline)
            
            HStack {
                Toggle("顯示測量數據", isOn: $showingMeasurements)
                
                Spacer()
                
                if showingMeasurements {
                    Text("✓ 已啟用")
                        .font(.caption)
                        .foregroundColor(.green)
                }
            }
        }
    }
    
    // MARK: - 動畫控制
    private var animationControls: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("動畫控制")
                .font(.headline)
            
            HStack {
                Text("旋轉速度")
                    .font(.subheadline)
                
                Spacer()
                
                Slider(value: $animationSpeed, in: 0...3, step: 0.1) {
                    Text("速度")
                } minimumValueLabel: {
                    Text("慢")
                        .font(.caption)
                } maximumValueLabel: {
                    Text("快")
                        .font(.caption)
                }
                .frame(width: 120)
            }
        }
    }
    
    // MARK: - 傷口數據信息
    private var woundDataInfo: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("傷口數據")
                .font(.headline)
            
            LazyVGrid(columns: Array(repeating: GridItem(.flexible()), count: 2), spacing: 8) {
                InfoCard(label: "面積", value: "\(woundData.area, specifier: "%.2f") cm²", color: .blue)
                InfoCard(label: "體積", value: "\(woundData.volume, specifier: "%.3f") cm³", color: .green)
                InfoCard(label: "深度", value: "\(woundData.maxDepth, specifier: "%.1f") mm", color: .orange)
                InfoCard(label: "周長", value: "\(woundData.perimeter, specifier: "%.2f") cm", color: .purple)
            }
        }
    }
    
    // MARK: - 3D場景創建
    private func create3DScene() -> SCNScene {
        let scene = SCNScene()
        
        // 創建傷口3D模型
        let woundNode = createWoundGeometry()
        scene.rootNode.addChildNode(woundNode)
        
        // 添加測量標註（如果啟用）
        if showingMeasurements {
            addMeasurementAnnotations(to: scene)
        }
        
        // 設置相機
        setupCamera(in: scene)
        
        // 設置光照
        setupLighting(in: scene)
        
        return scene
    }
    
    private func createWoundGeometry() -> SCNNode {
        let node = SCNNode()
        
        // 基礎傷口形狀（使用圓柱體模擬）
        let cylinderGeometry = SCNCylinder(radius: CGFloat(woundData.area / .pi).squareRoot(), height: CGFloat(woundData.maxDepth / 10))
        cylinderGeometry.radialSegmentCount = 32
        cylinderGeometry.heightSegmentCount = 8
        
        // 材質設置
        let material = SCNMaterial()
        material.diffuse.contents = woundData.woundColor
        material.specular.contents = UIColor.white
        material.shininess = 0.3
        material.transparency = 0.9
        
        cylinderGeometry.materials = [material]
        
        let woundNode = SCNNode(geometry: cylinderGeometry)
        woundNode.position = SCNVector3(0, -Float(woundData.maxDepth / 20), 0)
        
        // 添加旋轉動畫
        if animationSpeed > 0 {
            let rotationAction = SCNAction.rotateBy(x: 0, y: CGFloat(2 * Float.pi), z: 0, duration: 4.0 / animationSpeed)
            let repeatAction = SCNAction.repeatForever(rotationAction)
            woundNode.runAction(repeatAction)
        }
        
        node.addChildNode(woundNode)
        
        // 添加輪廓線
        addWoundContour(to: node)
        
        return node
    }
    
    private func addWoundContour(to parentNode: SCNNode) {
        // 創建傷口輪廓（使用線條）
        let contourPoints = generateContourPoints()
        
        for i in 0..<contourPoints.count {
            let nextIndex = (i + 1) % contourPoints.count
            let start = contourPoints[i]
            let end = contourPoints[nextIndex]
            
            let line = createLine(from: start, to: end)
            parentNode.addChildNode(line)
        }
    }
    
    private func generateContourPoints() -> [SCNVector3] {
        var points: [SCNVector3] = []
        let radius = Float(sqrt(woundData.area / .pi))
        let segments = 32
        
        for i in 0..<segments {
            let angle = Float(i) * 2 * .pi / Float(segments)
            let x = radius * cos(angle)
            let z = radius * sin(angle)
            points.append(SCNVector3(x, 0, z))
        }
        
        return points
    }
    
    private func createLine(from start: SCNVector3, to end: SCNVector3) -> SCNNode {
        let vector = SCNVector3(end.x - start.x, end.y - start.y, end.z - start.z)
        let distance = sqrt(vector.x * vector.x + vector.y * vector.y + vector.z * vector.z)
        
        let cylinder = SCNCylinder(radius: 0.01, height: CGFloat(distance))
        cylinder.radialSegmentCount = 8
        
        let material = SCNMaterial()
        material.diffuse.contents = UIColor.red
        cylinder.materials = [material]
        
        let lineNode = SCNNode(geometry: cylinder)
        lineNode.position = SCNVector3(
            (start.x + end.x) / 2,
            (start.y + end.y) / 2,
            (start.z + end.z) / 2
        )
        
        // 設置方向
        lineNode.look(at: end, up: SCNVector3(0, 1, 0), localFront: SCNVector3(0, 1, 0))
        
        return lineNode
    }
    
    private func addMeasurementAnnotations(to scene: SCNScene) {
        // 添加測量標註（文字標籤）
        if showingMeasurements {
            // 面積標註
            let areaText = create3DText("面積: \(String(format: "%.2f", woundData.area)) cm²")
            areaText.position = SCNVector3(0, 1, 0)
            scene.rootNode.addChildNode(areaText)
            
            // 體積標註
            let volumeText = create3DText("體積: \(String(format: "%.3f", woundData.volume)) cm³")
            volumeText.position = SCNVector3(0, 0.5, 0)
            scene.rootNode.addChildNode(volumeText)
        }
    }
    
    private func create3DText(_ text: String) -> SCNNode {
        let textGeometry = SCNText(string: text, extrusionDepth: 0.02)
        textGeometry.font = UIFont.systemFont(ofSize: 0.1)
        textGeometry.firstMaterial?.diffuse.contents = UIColor.blue
        
        let textNode = SCNNode(geometry: textGeometry)
        textNode.scale = SCNVector3(0.5, 0.5, 0.5)
        
        return textNode
    }
    
    private func setupCamera(in scene: SCNScene) {
        let cameraNode = SCNNode()
        cameraNode.camera = SCNCamera()
        cameraNode.position = selectedView.cameraPosition
        cameraNode.look(at: SCNVector3Zero)
        scene.rootNode.addChildNode(cameraNode)
    }
    
    private func setupLighting(in scene: SCNScene) {
        // 環境光
        let ambientLight = SCNNode()
        ambientLight.light = SCNLight()
        ambientLight.light?.type = .ambient
        ambientLight.light?.color = UIColor(white: 0.4, alpha: 1.0)
        scene.rootNode.addChildNode(ambientLight)
        
        // 方向光
        let directionalLight = SCNNode()
        directionalLight.light = SCNLight()
        directionalLight.light?.type = .directional
        directionalLight.light?.color = UIColor.white
        directionalLight.position = SCNVector3(2, 2, 2)
        directionalLight.look(at: SCNVector3Zero)
        scene.rootNode.addChildNode(directionalLight)
    }
    
    private func startRotationAnimation() {
        // 已在createWoundGeometry中實現
    }
}

// MARK: - 傷口視覺化數據結構
struct WoundVisualizationData {
    let area: Double           // cm²
    let volume: Double         // cm³
    let perimeter: Double      // cm
    let maxDepth: Double       // mm
    let woundColor: UIColor
    let depthMap: [[Float]]?   // 可選的深度圖數據
    let contourPoints: [CGPoint]?
    
    init(area: Double, volume: Double, perimeter: Double, maxDepth: Double, woundColor: UIColor = .red, depthMap: [[Float]]? = nil, contourPoints: [CGPoint]? = nil) {
        self.area = area
        self.volume = volume
        self.perimeter = perimeter
        self.maxDepth = maxDepth
        self.woundColor = woundColor
        self.depthMap = depthMap
        self.contourPoints = contourPoints
    }
}

// MARK: - 信息卡片組件
struct InfoCard: View {
    let label: String
    let value: String
    let color: Color
    
    var body: some View {
        VStack(spacing: 4) {
            Text(value)
                .font(.headline)
                .fontWeight(.bold)
                .foregroundColor(color)
            
            Text(label)
                .font(.caption)
                .foregroundColor(.secondary)
        }
        .padding()
        .background(color.opacity(0.1))
        .cornerRadius(8)
    }
}

// MARK: - SceneView包裝器
struct SceneView: UIViewRepresentable {
    let scene: SCNScene
    let options: SCNView.Option
    
    func makeUIView(context: Context) -> SCNView {
        let scnView = SCNView()
        scnView.scene = scene
        scnView.allowsCameraControl = options.contains(.allowsCameraControl)
        scnView.autoenablesDefaultLighting = options.contains(.autoenablesDefaultLighting)
        scnView.backgroundColor = UIColor.systemBackground
        return scnView
    }
    
    func updateUIView(_ uiView: SCNView, context: Context) {
        uiView.scene = scene
    }
}

extension SCNView {
    struct Option: OptionSet {
        let rawValue: Int
        
        static let allowsCameraControl = Option(rawValue: 1 << 0)
        static let autoenablesDefaultLighting = Option(rawValue: 1 << 1)
    }
}

// MARK: - 預覽
#Preview {
    Wound3DVisualizationView(
        woundData: WoundVisualizationData(
            area: 2.5,
            volume: 0.125,
            perimeter: 5.6,
            maxDepth: 3.2,
            woundColor: .systemRed
        )
    )
}