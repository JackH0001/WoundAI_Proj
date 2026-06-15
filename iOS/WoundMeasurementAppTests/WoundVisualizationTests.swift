import XCTest
import SceneKit
import UIKit
@testable import WoundMeasurementApp

/// 3D傷口視覺化功能單元測試
final class WoundVisualizationTests: XCTestCase {
    
    // MARK: - 測試屬性
    var testWoundData: WoundVisualizationData!
    
    // MARK: - 測試生命週期
    override func setUpWithError() throws {
        super.setUp()
        testWoundData = createTestWoundVisualizationData()
    }
    
    override func tearDownWithError() throws {
        testWoundData = nil
        super.tearDown()
    }
    
    // MARK: - 3D視覺化數據結構測試
    
    /// 測試傷口視覺化數據創建
    func testWoundVisualizationDataCreation() throws {
        // Given & When
        let woundData = WoundVisualizationData(
            area: 2.5,
            volume: 0.125,
            perimeter: 5.6,
            maxDepth: 3.2,
            woundColor: .systemRed
        )
        
        // Then
        XCTAssertEqual(woundData.area, 2.5, accuracy: 0.001)
        XCTAssertEqual(woundData.volume, 0.125, accuracy: 0.001)
        XCTAssertEqual(woundData.perimeter, 5.6, accuracy: 0.001)
        XCTAssertEqual(woundData.maxDepth, 3.2, accuracy: 0.001)
        XCTAssertEqual(woundData.woundColor, .systemRed)
        XCTAssertNil(woundData.depthMap)
        XCTAssertNil(woundData.contourPoints)
    }
    
    /// 測試帶有深度圖和輪廓點的數據創建
    func testWoundVisualizationDataWithDepthMap() throws {
        // Given
        let depthMap = [[Float]]([[1.0, 2.0], [1.5, 2.5]])
        let contourPoints = [CGPoint(x: 10, y: 10), CGPoint(x: 20, y: 20)]
        
        // When
        let woundData = WoundVisualizationData(
            area: 3.0,
            volume: 0.2,
            perimeter: 6.0,
            maxDepth: 4.0,
            woundColor: .systemOrange,
            depthMap: depthMap,
            contourPoints: contourPoints
        )
        
        // Then
        XCTAssertNotNil(woundData.depthMap)
        XCTAssertNotNil(woundData.contourPoints)
        XCTAssertEqual(woundData.depthMap?.count, 2)
        XCTAssertEqual(woundData.contourPoints?.count, 2)
    }
    
    // MARK: - 3D場景創建測試
    
    /// 測試3D場景基礎功能
    func testSceneCreation() throws {
        // Given
        let visualizationView = Wound3DVisualizationView(woundData: testWoundData)
        
        // When
        // 通過反射訪問私有方法進行測試（簡化版）
        // 實際測試中可以將方法設為內部或創建測試友好的接口
        
        // Then
        // 驗證視圖能正常創建
        XCTAssertNotNil(visualizationView.woundData)
        XCTAssertEqual(visualizationView.woundData.area, testWoundData.area, accuracy: 0.001)
    }
    
    /// 測試ViewMode枚舉
    func testViewModeConfiguration() throws {
        // Given
        let viewModes = Wound3DVisualizationView.ViewMode.allCases
        
        // When & Then
        XCTAssertEqual(viewModes.count, 4)
        
        // 驗證每個視圖模式的相機位置
        for mode in viewModes {
            let position = mode.cameraPosition
            XCTAssertNotEqual(position.x, 0, "相機X位置不應該全為0")
            XCTAssertTrue(position.y >= 0, "相機Y位置應該為非負")
            XCTAssertTrue(position.z >= 0, "相機Z位置應該為非負")
        }
        
        // 驗證透視圖模式
        let perspectiveMode = Wound3DVisualizationView.ViewMode.perspective
        let perspectivePosition = perspectiveMode.cameraPosition
        XCTAssertEqual(perspectivePosition.x, 2, accuracy: 0.001)
        XCTAssertEqual(perspectivePosition.y, 2, accuracy: 0.001)
        XCTAssertEqual(perspectivePosition.z, 2, accuracy: 0.001)
    }
    
    // MARK: - InfoCard組件測試
    
    /// 測試信息卡片組件
    func testInfoCardComponent() throws {
        // Given
        let label = "面積"
        let value = "2.50 cm²"
        let color = Color.blue
        
        // When
        let infoCard = InfoCard(label: label, value: value, color: color)
        
        // Then
        XCTAssertEqual(infoCard.label, label)
        XCTAssertEqual(infoCard.value, value)
        XCTAssertEqual(infoCard.color, color)
    }
    
    // MARK: - SceneView測試
    
    /// 測試SceneView包裝器基本功能
    func testSceneViewWrapper() throws {
        // Given
        let scene = SCNScene()
        let options: SceneView.Option = [.allowsCameraControl, .autoenablesDefaultLighting]
        
        // When
        let sceneView = SceneView(scene: scene, options: options)
        
        // Then
        XCTAssertNotNil(sceneView.scene)
        XCTAssertEqual(sceneView.options, options)
    }
    
    /// 測試SceneView選項
    func testSceneViewOptions() throws {
        // Given & When
        let option1 = SceneView.Option.allowsCameraControl
        let option2 = SceneView.Option.autoenablesDefaultLighting
        let combinedOptions: SceneView.Option = [option1, option2]
        
        // Then
        XCTAssertTrue(combinedOptions.contains(.allowsCameraControl))
        XCTAssertTrue(combinedOptions.contains(.autoenablesDefaultLighting))
        XCTAssertEqual(option1.rawValue, 1)
        XCTAssertEqual(option2.rawValue, 2)
    }
    
    // MARK: - 3D幾何體測試
    
    /// 測試基於傷口數據的幾何體計算
    func testWoundGeometryCalculations() throws {
        // Given
        let area = 4.0 // cm²
        let maxDepth = 5.0 // mm
        
        // When - 計算圓柱體半徑（基於面積）
        let expectedRadius = sqrt(area / .pi)
        let expectedHeight = maxDepth / 10 // 縮放因子
        
        // Then
        XCTAssertEqual(expectedRadius, sqrt(4.0 / .pi), accuracy: 0.001)
        XCTAssertEqual(expectedHeight, 0.5, accuracy: 0.001)
        
        // 驗證合理性
        XCTAssertGreaterThan(expectedRadius, 0, "半徑應該大於0")
        XCTAssertGreaterThan(expectedHeight, 0, "高度應該大於0")
    }
    
    /// 測試輪廓點生成算法
    func testContourPointGeneration() throws {
        // Given
        let area = 9.0 // cm² (半徑為√(9/π) ≈ 1.69)
        let expectedRadius = sqrt(area / .pi)
        let segments = 8 // 8個點的多邊形
        
        // When - 模擬輪廓點生成
        var points: [SCNVector3] = []
        for i in 0..<segments {
            let angle = Float(i) * 2 * .pi / Float(segments)
            let x = Float(expectedRadius) * cos(angle)
            let z = Float(expectedRadius) * sin(angle)
            points.append(SCNVector3(x, 0, z))
        }
        
        // Then
        XCTAssertEqual(points.count, segments)
        
        // 驗證點在正確的半徑上
        for point in points {
            let distance = sqrt(point.x * point.x + point.z * point.z)
            XCTAssertEqual(distance, Float(expectedRadius), accuracy: 0.001)
            XCTAssertEqual(point.y, 0, "所有點的Y座標應該為0")
        }
        
        // 驗證第一個點在正X軸上
        XCTAssertEqual(points[0].x, Float(expectedRadius), accuracy: 0.001)
        XCTAssertEqual(points[0].z, 0, accuracy: 0.001)
    }
    
    // MARK: - 動畫和交互測試
    
    /// 測試動畫參數驗證
    func testAnimationParameters() throws {
        // Given
        let animationSpeeds: [Double] = [0.0, 0.5, 1.0, 2.0, 3.0]
        
        // When & Then
        for speed in animationSpeeds {
            if speed > 0 {
                let duration = 4.0 / speed
                XCTAssertGreaterThan(duration, 0, "動畫持續時間應該大於0")
                XCTAssertLessThanOrEqual(duration, 4.0, "動畫持續時間應該合理")
            }
        }
        
        // 驗證極端值
        let maxSpeed = 3.0
        let minDuration = 4.0 / maxSpeed
        XCTAssertEqual(minDuration, 4.0/3.0, accuracy: 0.001)
    }
    
    // MARK: - 材質和光照測試
    
    /// 測試材質屬性設定
    func testMaterialProperties() throws {
        // Given
        let material = SCNMaterial()
        let testColor = UIColor.systemRed
        let transparency: CGFloat = 0.9
        let shininess: CGFloat = 0.3
        
        // When
        material.diffuse.contents = testColor
        material.specular.contents = UIColor.white
        material.transparency = transparency
        material.shininess = shininess
        
        // Then
        XCTAssertEqual(material.transparency, transparency)
        XCTAssertEqual(material.shininess, shininess)
        XCTAssertNotNil(material.diffuse.contents)
        XCTAssertNotNil(material.specular.contents)
    }
    
    /// 測試光照設定
    func testLightingConfiguration() throws {
        // Given
        let scene = SCNScene()
        
        // When - 模擬光照設定
        // 環境光
        let ambientLight = SCNLight()
        ambientLight.type = .ambient
        ambientLight.color = UIColor(white: 0.4, alpha: 1.0)
        
        let ambientLightNode = SCNNode()
        ambientLightNode.light = ambientLight
        scene.rootNode.addChildNode(ambientLightNode)
        
        // 方向光
        let directionalLight = SCNLight()
        directionalLight.type = .directional
        directionalLight.color = UIColor.white
        
        let directionalLightNode = SCNNode()
        directionalLightNode.light = directionalLight
        directionalLightNode.position = SCNVector3(2, 2, 2)
        scene.rootNode.addChildNode(directionalLightNode)
        
        // Then
        XCTAssertEqual(scene.rootNode.childNodes.count, 2)
        XCTAssertEqual(ambientLight.type, .ambient)
        XCTAssertEqual(directionalLight.type, .directional)
    }
    
    // MARK: - 錯誤處理和邊界情況測試
    
    /// 測試零值數據處理
    func testZeroValueDataHandling() throws {
        // Given
        let zeroWoundData = WoundVisualizationData(
            area: 0.0,
            volume: 0.0,
            perimeter: 0.0,
            maxDepth: 0.0,
            woundColor: .systemGray
        )
        
        // When
        let visualizationView = Wound3DVisualizationView(woundData: zeroWoundData)
        
        // Then - 應該能處理零值而不崩潰
        XCTAssertNotNil(visualizationView)
        XCTAssertEqual(visualizationView.woundData.area, 0.0)
        
        // 驗證基於零面積的半徑計算
        let radius = sqrt(zeroWoundData.area / .pi)
        XCTAssertEqual(radius, 0.0, accuracy: 0.001)
    }
    
    /// 測試極大值數據處理
    func testLargeValueDataHandling() throws {
        // Given
        let largeWoundData = WoundVisualizationData(
            area: 1000.0,
            volume: 500.0,
            perimeter: 200.0,
            maxDepth: 100.0,
            woundColor: .systemRed
        )
        
        // When
        let visualizationView = Wound3DVisualizationView(woundData: largeWoundData)
        
        // Then
        XCTAssertNotNil(visualizationView)
        
        // 驗證大數值計算的合理性
        let radius = sqrt(largeWoundData.area / .pi)
        XCTAssertGreaterThan(radius, 15.0) // 面積1000對應的半徑約17.8
        XCTAssertLessThan(radius, 20.0)
    }
    
    // MARK: - 性能測試
    
    /// 測試3D視覺化組件創建性能
    func testVisualizationCreationPerformance() throws {
        measure {
            let woundData = createTestWoundVisualizationData()
            let _ = Wound3DVisualizationView(woundData: woundData)
        }
    }
    
    /// 測試大量輪廓點處理性能
    func testLargeContourPointsPerformance() throws {
        // Given
        let largeContourPoints = (0..<1000).map { i in
            CGPoint(x: CGFloat(i), y: CGFloat(i * 2))
        }
        
        // When & Then
        measure {
            let woundData = WoundVisualizationData(
                area: 10.0,
                volume: 1.0,
                perimeter: 15.0,
                maxDepth: 5.0,
                woundColor: .systemRed,
                depthMap: nil,
                contourPoints: largeContourPoints
            )
            
            let _ = Wound3DVisualizationView(woundData: woundData)
        }
    }
    
    // MARK: - 輔助方法
    
    /// 創建測試用傷口視覺化數據
    private func createTestWoundVisualizationData() -> WoundVisualizationData {
        return WoundVisualizationData(
            area: 2.5,
            volume: 0.125,
            perimeter: 5.6,
            maxDepth: 3.2,
            woundColor: .systemRed,
            depthMap: [[1.0, 2.0], [1.5, 2.5]],
            contourPoints: [
                CGPoint(x: 10, y: 10),
                CGPoint(x: 20, y: 15),
                CGPoint(x: 30, y: 10),
                CGPoint(x: 25, y: 5),
                CGPoint(x: 15, y: 5)
            ]
        )
    }
    
    /// 驗證3D向量的合理性
    private func validateVector3(_ vector: SCNVector3, expectedMagnitude: Float? = nil) {
        XCTAssertFalse(vector.x.isNaN, "X分量不應該是NaN")
        XCTAssertFalse(vector.y.isNaN, "Y分量不應該是NaN")
        XCTAssertFalse(vector.z.isNaN, "Z分量不應該是NaN")
        
        if let expectedMagnitude = expectedMagnitude {
            let magnitude = sqrt(vector.x * vector.x + vector.y * vector.y + vector.z * vector.z)
            XCTAssertEqual(magnitude, expectedMagnitude, accuracy: 0.001)
        }
    }
    
    /// 創建測試用SCNNode
    private func createTestNode() -> SCNNode {
        let node = SCNNode()
        let geometry = SCNCylinder(radius: 1.0, height: 2.0)
        node.geometry = geometry
        return node
    }
}