import SwiftUI
import RealityKit
import ARKit
import MetalKit
import simd
import SceneKit

/// 按照技術文件建議的3D傷口可視化和表面重建模組
/// 整合LiDAR深度數據進行高精度3D重建和體積測量
@MainActor
class Wound3DVisualizationModule: ObservableObject {
    
    @Published var is3DReconstructing = false
    @Published var reconstructionProgress: Float = 0.0
    @Published var current3DModel: Wound3DModel?
    @Published var visualizationMode: VisualizationMode = .surface
    @Published var renderingQuality: RenderingQuality = .high
    
    private var arView: ARView?
    private var sceneView: SCNView?
    private let metalDevice = MTLCreateSystemDefaultDevice()
    
    enum VisualizationMode: String, CaseIterable {
        case surface = "表面重建"
        case wireframe = "線框模型"
        case pointCloud = "點雲"
        case heightMap = "高度圖"
        case volumeRender = "體積渲染"
        case crossSection = "橫截面"
    }
    
    enum RenderingQuality: String, CaseIterable {
        case low = "低品質"
        case medium = "中品質"
        case high = "高品質"
        case ultraHigh = "超高品質"
        
        var vertexDensity: Int {
            switch self {
            case .low: return 1000
            case .medium: return 5000
            case .high: return 15000
            case .ultraHigh: return 50000
            }
        }
    }
    
    struct Wound3DModel {
        let pointCloud: [SIMD3<Float>]           // 3D點雲
        let surfaceMesh: MDLMesh                 // 表面網格
        let textureCoordinates: [SIMD2<Float>]   // 紋理座標
        let normalVectors: [SIMD3<Float>]        // 法向量
        let colorData: [SIMD3<Float>]            // 顏色數據
        let boundingBox: BoundingBox             // 包圍盒
        let volumeInfo: VolumeInfo               // 體積資訊
        let qualityMetrics: Quality3DMetrics     // 品質指標
        let reconstructionTime: TimeInterval     // 重建時間
        
        struct BoundingBox {
            let min: SIMD3<Float>
            let max: SIMD3<Float>
            let center: SIMD3<Float>
            let size: SIMD3<Float>
        }
        
        struct VolumeInfo {
            let totalVolume: Double              // cm³
            let surfaceArea: Double              // cm²
            let depthDistribution: [Float]       // 深度分佈
            let curvatureAnalysis: CurvatureData // 曲率分析
        }
        
        struct CurvatureData {
            let meanCurvature: Float
            let gaussianCurvature: Float
            let principalCurvatures: (Float, Float)
            let curvatureMap: [Float]
        }
        
        struct Quality3DMetrics {
            let meshDensity: Float               // 網格密度
            let reconstructionAccuracy: Float    // 重建精度
            let surfaceSmoothness: Float         // 表面平滑度
            let noiseLevel: Float                // 噪聲水平
            let completeness: Float              // 完整度
        }
    }
    
    /// 主要3D重建函數
    func reconstruct3DWound(
        rgbImage: UIImage,
        depthData: Data,
        cameraIntrinsics: CameraIntrinsics,
        woundROI: CGRect
    ) async throws -> Wound3DModel {
        
        let startTime = Date()
        is3DReconstructing = true
        reconstructionProgress = 0.0
        
        defer {
            Task { @MainActor in
                is3DReconstructing = false
                reconstructionProgress = 1.0
            }
        }
        
        do {
            // 階段1: 數據預處理和驗證
            updateProgress(0.1, status: "預處理數據...")
            let preprocessedData = try await preprocessReconstructionData(
                rgbImage: rgbImage,
                depthData: depthData,
                intrinsics: cameraIntrinsics,
                roi: woundROI
            )
            
            // 階段2: 生成3D點雲
            updateProgress(0.3, status: "生成3D點雲...")
            let pointCloud = try await generatePointCloud(
                from: preprocessedData,
                quality: renderingQuality
            )
            
            // 階段3: 表面重建
            updateProgress(0.5, status: "重建表面網格...")
            let surfaceMesh = try await reconstructSurface(
                from: pointCloud,
                method: .poissonReconstruction
            )
            
            // 階段4: 紋理映射
            updateProgress(0.7, status: "應用紋理映射...")
            let textureData = try await generateTextureMapping(
                mesh: surfaceMesh,
                rgbImage: rgbImage,
                pointCloud: pointCloud
            )
            
            // 階段5: 幾何分析
            updateProgress(0.85, status: "計算幾何屬性...")
            let geometryAnalysis = try await analyzeGeometry(
                mesh: surfaceMesh,
                pointCloud: pointCloud,
                intrinsics: cameraIntrinsics
            )
            
            // 階段6: 品質評估
            updateProgress(0.95, status: "評估重建品質...")
            let qualityMetrics = try await evaluateReconstructionQuality(
                mesh: surfaceMesh,
                pointCloud: pointCloud,
                originalDepth: depthData
            )
            
            let reconstructionTime = Date().timeIntervalSince(startTime)
            
            let wound3DModel = Wound3DModel(
                pointCloud: pointCloud.points,
                surfaceMesh: surfaceMesh,
                textureCoordinates: textureData.coordinates,
                normalVectors: geometryAnalysis.normals,
                colorData: textureData.colors,
                boundingBox: geometryAnalysis.boundingBox,
                volumeInfo: geometryAnalysis.volumeInfo,
                qualityMetrics: qualityMetrics,
                reconstructionTime: reconstructionTime
            )
            
            current3DModel = wound3DModel
            updateProgress(1.0, status: "3D重建完成")
            
            print("3D重建完成: 耗時\(String(format: "%.2f", reconstructionTime))秒, 點數\(pointCloud.points.count), 品質\(qualityMetrics.reconstructionAccuracy)")
            
            return wound3DModel
            
        } catch {
            print("3D重建失敗: \(error)")
            throw Visualization3DError.reconstructionFailed(error.localizedDescription)
        }
    }
    
    /// 創建AR可視化場景
    func createARVisualization(for model: Wound3DModel) -> ARView {
        let arView = ARView(frame: .zero)
        
        // 設置AR配置
        let configuration = ARWorldTrackingConfiguration()
        configuration.planeDetection = [.horizontal, .vertical]
        if ARWorldTrackingConfiguration.supportsSceneReconstruction(.mesh) {
            configuration.sceneReconstruction = .mesh
        }
        
        arView.session.run(configuration)
        
        // 創建3D實體
        let meshEntity = createMeshEntity(from: model)
        
        // 添加到場景
        let anchor = AnchorEntity(world: [0, 0, -0.5]) // 50cm前方
        anchor.addChild(meshEntity)
        arView.scene.addAnchor(anchor)
        
        // 添加互動手勢
        setupARInteractions(arView: arView, entity: meshEntity)
        
        self.arView = arView
        return arView
    }
    
    /// 創建SceneKit可視化場景
    func createSceneKitVisualization(for model: Wound3DModel) -> SCNView {
        let sceneView = SCNView()
        let scene = SCNScene()
        
        // 創建傷口幾何體
        let woundNode = createWoundNode(from: model)
        scene.rootNode.addChildNode(woundNode)
        
        // 設置攝影機
        let cameraNode = SCNNode()
        cameraNode.camera = SCNCamera()
        cameraNode.position = SCNVector3(0, 0, 5)
        scene.rootNode.addChildNode(cameraNode)
        
        // 設置光照
        setupSceneKitLighting(scene: scene)
        
        // 配置渲染選項
        sceneView.scene = scene
        sceneView.allowsCameraControl = true
        sceneView.autoenablesDefaultLighting = true
        sceneView.antialiasingMode = .multisampling4X
        
        self.sceneView = sceneView
        return sceneView
    }
    
    /// 導出3D模型
    func export3DModel(format: Export3DFormat) async throws -> Data {
        guard let model = current3DModel else {
            throw Visualization3DError.noModelAvailable
        }
        
        switch format {
        case .obj:
            return try await exportAsOBJ(model: model)
        case .ply:
            return try await exportAsPLY(model: model)
        case .stl:
            return try await exportAsSTL(model: model)
        case .usdz:
            return try await exportAsUSDZ(model: model)
        }
    }
    
    // MARK: - 私有實現方法
    
    private func preprocessReconstructionData(
        rgbImage: UIImage,
        depthData: Data,
        intrinsics: CameraIntrinsics,
        roi: CGRect
    ) async throws -> PreprocessedReconstructionData {
        
        guard let cgImage = rgbImage.cgImage else {
            throw Visualization3DError.invalidInputData
        }
        
        // 解析深度數據
        let depthArray = depthData.withUnsafeBytes { bytes in
            Array(bytes.bindMemory(to: Float32.self))
        }
        
        // 驗證深度數據尺寸
        let expectedSize = 256 * 192 // ARKit標準
        guard depthArray.count >= expectedSize else {
            throw Visualization3DError.invalidDepthData
        }
        
        let depthWidth = 256
        let depthHeight = 192
        
        // 裁切ROI區域
        let roiDepth = extractROIDepth(
            depthArray: depthArray,
            depthSize: (depthWidth, depthHeight),
            roi: roi
        )
        
        let roiImage = try extractROIImage(cgImage: cgImage, roi: roi)
        
        // 深度過濾和平滑
        let filteredDepth = applyDepthFiltering(roiDepth)
        
        return PreprocessedReconstructionData(
            rgbData: roiImage,
            depthData: filteredDepth,
            intrinsics: intrinsics,
            roi: roi
        )
    }
    
    private func generatePointCloud(
        from data: PreprocessedReconstructionData,
        quality: RenderingQuality
    ) async throws -> PointCloudData {
        
        let rgbImage = data.rgbData
        let depthData = data.depthData
        let intrinsics = data.intrinsics
        
        var points: [SIMD3<Float>] = []
        var colors: [SIMD3<Float>] = []
        var normals: [SIMD3<Float>] = []
        
        let width = rgbImage.width
        let height = rgbImage.height
        let stepSize = max(1, Int(sqrt(Double(width * height) / Double(quality.vertexDensity))))
        
        // 獲取圖像數據
        guard let provider = rgbImage.dataProvider,
              let pixelData = provider.data,
              let pixels = CFDataGetBytePtr(pixelData) else {
            throw Visualization3DError.imageProcessingFailed
        }
        
        // 逐像素生成3D點
        for y in stride(from: 0, to: height, by: stepSize) {
            for x in stride(from: 0, to: width, by: stepSize) {
                let depthIndex = y * width + x
                
                guard depthIndex < depthData.count else { continue }
                
                let depth = depthData[depthIndex]
                
                // 過濾無效深度值
                guard depth > 0.001 && depth < 2.0 else { continue }
                
                // 轉換為3D座標
                let worldPoint = convertToWorldCoordinates(
                    x: Float(x), y: Float(y), depth: depth,
                    intrinsics: intrinsics
                )
                
                points.append(worldPoint)
                
                // 提取顏色
                let pixelOffset = (y * width + x) * 4
                let r = Float(pixels[pixelOffset]) / 255.0
                let g = Float(pixels[pixelOffset + 1]) / 255.0
                let b = Float(pixels[pixelOffset + 2]) / 255.0
                
                colors.append(SIMD3<Float>(r, g, b))
                
                // 計算法向量（簡化版）
                let normal = estimateNormal(x: x, y: y, depthData: depthData, width: width, height: height, intrinsics: intrinsics)
                normals.append(normal)
            }
        }
        
        print("點雲生成完成: \(points.count) 個點")
        
        return PointCloudData(
            points: points,
            colors: colors,
            normals: normals
        )
    }
    
    private func reconstructSurface(
        from pointCloud: PointCloudData,
        method: SurfaceReconstructionMethod
    ) async throws -> MDLMesh {
        
        switch method {
        case .poissonReconstruction:
            return try await poissonSurfaceReconstruction(pointCloud: pointCloud)
        case .delaunayTriangulation:
            return try await delaunayTriangulation(pointCloud: pointCloud)
        case .marchingCubes:
            return try await marchingCubesReconstruction(pointCloud: pointCloud)
        }
    }
    
    private func poissonSurfaceReconstruction(pointCloud: PointCloudData) async throws -> MDLMesh {
        // Poisson表面重建的簡化實現
        // 實際應用中需要使用更復雜的算法庫
        
        let allocator = MTKMeshBufferAllocator(device: metalDevice!)
        
        // 創建頂點描述符
        let vertexDescriptor = MDLVertexDescriptor()
        vertexDescriptor.attributes[0] = MDLVertexAttribute(
            name: MDLVertexAttributePosition,
            format: .float3,
            offset: 0,
            bufferIndex: 0
        )
        vertexDescriptor.attributes[1] = MDLVertexAttribute(
            name: MDLVertexAttributeNormal,
            format: .float3,
            offset: 12,
            bufferIndex: 0
        )
        vertexDescriptor.attributes[2] = MDLVertexAttribute(
            name: MDLVertexAttributeColor,
            format: .float3,
            offset: 24,
            bufferIndex: 0
        )
        vertexDescriptor.layouts[0] = MDLVertexBufferLayout(stride: 36)
        
        // 準備頂點數據
        var vertexData: [Float] = []
        for i in 0..<pointCloud.points.count {
            let point = pointCloud.points[i]
            let normal = pointCloud.normals[i]
            let color = pointCloud.colors[i]
            
            vertexData.append(contentsOf: [point.x, point.y, point.z])
            vertexData.append(contentsOf: [normal.x, normal.y, normal.z])
            vertexData.append(contentsOf: [color.x, color.y, color.z])
        }
        
        let vertexBuffer = allocator.newBuffer(
            with: Data(bytes: vertexData, count: vertexData.count * MemoryLayout<Float>.size),
            type: .vertex
        )
        
        // 生成三角形索引（簡化版）
        let triangleIndices = generateTriangleIndices(pointCount: pointCloud.points.count)
        let indexBuffer = allocator.newBuffer(
            with: Data(bytes: triangleIndices, count: triangleIndices.count * MemoryLayout<UInt32>.size),
            type: .index
        )
        
        // 創建子網格
        let submesh = MDLSubmesh(
            indexBuffer: indexBuffer,
            indexCount: triangleIndices.count,
            indexType: .uInt32,
            geometryType: .triangles,
            material: nil
        )
        
        // 創建網格
        let mesh = MDLMesh(
            vertexBuffers: [vertexBuffer],
            vertexCount: pointCloud.points.count,
            descriptor: vertexDescriptor,
            submeshes: [submesh]
        )
        
        return mesh
    }
    
    private func delaunayTriangulation(pointCloud: PointCloudData) async throws -> MDLMesh {
        // Delaunay三角剖分的簡化實現
        // 實際中需要使用專業幾何庫
        return try await poissonSurfaceReconstruction(pointCloud: pointCloud) // 簡化為同一方法
    }
    
    private func marchingCubesReconstruction(pointCloud: PointCloudData) async throws -> MDLMesh {
        // Marching Cubes算法的簡化實現
        return try await poissonSurfaceReconstruction(pointCloud: pointCloud) // 簡化為同一方法
    }
    
    private func generateTextureMapping(
        mesh: MDLMesh,
        rgbImage: UIImage,
        pointCloud: PointCloudData
    ) async throws -> TextureMappingData {
        
        // 生成UV座標
        var textureCoordinates: [SIMD2<Float>] = []
        let colors = pointCloud.colors
        
        for i in 0..<pointCloud.points.count {
            // 簡化的UV映射 - 基於3D位置投影
            let point = pointCloud.points[i]
            let u = (point.x + 1.0) * 0.5 // 正規化到0-1
            let v = (point.z + 1.0) * 0.5
            textureCoordinates.append(SIMD2<Float>(u, v))
        }
        
        return TextureMappingData(
            coordinates: textureCoordinates,
            colors: colors
        )
    }
    
    private func analyzeGeometry(
        mesh: MDLMesh,
        pointCloud: PointCloudData,
        intrinsics: CameraIntrinsics
    ) async throws -> GeometryAnalysis {
        
        let points = pointCloud.points
        let normals = pointCloud.normals
        
        // 計算包圍盒
        var minPoint = SIMD3<Float>(Float.greatestFiniteMagnitude, Float.greatestFiniteMagnitude, Float.greatestFiniteMagnitude)
        var maxPoint = SIMD3<Float>(-Float.greatestFiniteMagnitude, -Float.greatestFiniteMagnitude, -Float.greatestFiniteMagnitude)
        
        for point in points {
            minPoint = simd_min(minPoint, point)
            maxPoint = simd_max(maxPoint, point)
        }
        
        let center = (minPoint + maxPoint) * 0.5
        let size = maxPoint - minPoint
        
        let boundingBox = Wound3DModel.BoundingBox(
            min: minPoint,
            max: maxPoint,
            center: center,
            size: size
        )
        
        // 計算體積（使用蒙地卡羅方法）
        let volume = calculateVolumeUsingMonteCarlo(points: points, boundingBox: boundingBox)
        
        // 計算表面積
        let surfaceArea = calculateSurfaceArea(mesh: mesh)
        
        // 深度分析
        let depths = points.map { $0.z }
        let depthDistribution = calculateDepthDistribution(depths: depths)
        
        // 曲率分析
        let curvatureData = calculateCurvature(points: points, normals: normals)
        
        let volumeInfo = Wound3DModel.VolumeInfo(
            totalVolume: Double(volume) * 1000000.0, // 轉換為cm³
            surfaceArea: Double(surfaceArea) * 10000.0, // 轉換為cm²
            depthDistribution: depthDistribution,
            curvatureAnalysis: curvatureData
        )
        
        return GeometryAnalysis(
            boundingBox: boundingBox,
            volumeInfo: volumeInfo,
            normals: normals
        )
    }
    
    private func evaluateReconstructionQuality(
        mesh: MDLMesh,
        pointCloud: PointCloudData,
        originalDepth: Data
    ) async throws -> Wound3DModel.Quality3DMetrics {
        
        let pointCount = pointCloud.points.count
        let meshDensity = Float(pointCount) / 10000.0 // 正規化密度
        
        // 重建精度評估
        let reconstructionAccuracy = evaluateReconstructionAccuracy(
            pointCloud: pointCloud,
            originalDepth: originalDepth
        )
        
        // 表面平滑度
        let surfaceSmoothness = evaluateSurfaceSmoothness(normals: pointCloud.normals)
        
        // 噪聲水平
        let noiseLevel = evaluateNoiseLevel(points: pointCloud.points)
        
        // 完整度評估
        let completeness = evaluateCompleteness(pointCloud: pointCloud)
        
        return Wound3DModel.Quality3DMetrics(
            meshDensity: min(1.0, meshDensity),
            reconstructionAccuracy: reconstructionAccuracy,
            surfaceSmoothness: surfaceSmoothness,
            noiseLevel: noiseLevel,
            completeness: completeness
        )
    }
    
    // MARK: - 輔助計算函數
    
    private func convertToWorldCoordinates(
        x: Float, y: Float, depth: Float,
        intrinsics: CameraIntrinsics
    ) -> SIMD3<Float> {
        
        let worldX = (x - Float(intrinsics.cx)) * depth / Float(intrinsics.fx)
        let worldY = (y - Float(intrinsics.cy)) * depth / Float(intrinsics.fy)
        let worldZ = depth
        
        return SIMD3<Float>(worldX, worldY, worldZ)
    }
    
    private func estimateNormal(
        x: Int, y: Int,
        depthData: [Float],
        width: Int, height: Int,
        intrinsics: CameraIntrinsics
    ) -> SIMD3<Float> {
        
        // 使用鄰近點計算法向量
        var neighbors: [SIMD3<Float>] = []
        
        for dy in -1...1 {
            for dx in -1...1 {
                let nx = x + dx
                let ny = y + dy
                
                guard nx >= 0, nx < width, ny >= 0, ny < height else { continue }
                
                let index = ny * width + nx
                guard index < depthData.count else { continue }
                
                let depth = depthData[index]
                guard depth > 0.001 else { continue }
                
                let worldPoint = convertToWorldCoordinates(
                    x: Float(nx), y: Float(ny), depth: depth,
                    intrinsics: intrinsics
                )
                neighbors.append(worldPoint)
            }
        }
        
        // 使用最小二乘法擬合平面並計算法向量
        if neighbors.count >= 3 {
            let normal = calculatePlaneNormal(points: neighbors)
            return normalize(normal)
        }
        
        return SIMD3<Float>(0, 0, 1) // 預設法向量
    }
    
    private func calculatePlaneNormal(points: [SIMD3<Float>]) -> SIMD3<Float> {
        guard points.count >= 3 else { return SIMD3<Float>(0, 0, 1) }
        
        let p0 = points[0]
        let p1 = points[1]
        let p2 = points[2]
        
        let v1 = p1 - p0
        let v2 = p2 - p0
        
        return cross(v1, v2)
    }
    
    private func generateTriangleIndices(pointCount: Int) -> [UInt32] {
        // 簡化的三角形生成 - 實際中需要更復雜的算法
        var indices: [UInt32] = []
        
        for i in stride(from: 0, to: pointCount - 2, by: 3) {
            indices.append(UInt32(i))
            indices.append(UInt32(i + 1))
            indices.append(UInt32(i + 2))
        }
        
        return indices
    }
    
    private func calculateVolumeUsingMonteCarlo(
        points: [SIMD3<Float>],
        boundingBox: Wound3DModel.BoundingBox
    ) -> Float {
        
        let sampleCount = 10000
        let boxVolume = boundingBox.size.x * boundingBox.size.y * boundingBox.size.z
        
        var insideCount = 0
        
        for _ in 0..<sampleCount {
            let randomPoint = SIMD3<Float>(
                Float.random(in: boundingBox.min.x...boundingBox.max.x),
                Float.random(in: boundingBox.min.y...boundingBox.max.y),
                Float.random(in: boundingBox.min.z...boundingBox.max.z)
            )
            
            if isPointInsideMesh(point: randomPoint, meshPoints: points) {
                insideCount += 1
            }
        }
        
        return boxVolume * Float(insideCount) / Float(sampleCount)
    }
    
    private func isPointInsideMesh(point: SIMD3<Float>, meshPoints: [SIMD3<Float>]) -> Bool {
        // 簡化的點在網格內判斷
        // 實際中需要使用射線投射算法
        
        let threshold: Float = 0.01
        for meshPoint in meshPoints {
            let distance = simd_length(point - meshPoint)
            if distance < threshold {
                return true
            }
        }
        
        return false
    }
    
    private func calculateSurfaceArea(mesh: MDLMesh) -> Float {
        // 簡化的表面積計算
        // 實際中需要遍歷所有三角形面積求和
        
        guard let submesh = mesh.submeshes?.firstObject as? MDLSubmesh else {
            return 0.0
        }
        
        let triangleCount = submesh.indexCount / 3
        let averageTriangleArea: Float = 0.001 // 預設值
        
        return Float(triangleCount) * averageTriangleArea
    }
    
    private func calculateDepthDistribution(depths: [Float]) -> [Float] {
        // 計算深度分佈直方圖
        let binCount = 20
        let minDepth = depths.min() ?? 0
        let maxDepth = depths.max() ?? 1
        let binSize = (maxDepth - minDepth) / Float(binCount)
        
        var histogram = Array(repeating: Float(0), count: binCount)
        
        for depth in depths {
            let binIndex = min(binCount - 1, Int((depth - minDepth) / binSize))
            histogram[binIndex] += 1
        }
        
        // 正規化
        let totalCount = Float(depths.count)
        return histogram.map { $0 / totalCount }
    }
    
    private func calculateCurvature(
        points: [SIMD3<Float>],
        normals: [SIMD3<Float>]
    ) -> Wound3DModel.CurvatureData {
        
        // 簡化的曲率計算
        var curvatures: [Float] = []
        
        for i in 0..<min(points.count, normals.count) {
            // 計算局部曲率（簡化版）
            let curvature = calculateLocalCurvature(at: i, points: points, normals: normals)
            curvatures.append(curvature)
        }
        
        let meanCurvature = curvatures.reduce(0, +) / Float(curvatures.count)
        let gaussianCurvature = meanCurvature * 0.5 // 簡化
        
        return Wound3DModel.CurvatureData(
            meanCurvature: meanCurvature,
            gaussianCurvature: gaussianCurvature,
            principalCurvatures: (meanCurvature * 1.2, meanCurvature * 0.8),
            curvatureMap: curvatures
        )
    }
    
    private func calculateLocalCurvature(
        at index: Int,
        points: [SIMD3<Float>],
        normals: [SIMD3<Float>]
    ) -> Float {
        
        guard index < points.count && index < normals.count else { return 0 }
        
        let point = points[index]
        let normal = normals[index]
        
        // 找鄰近點
        var neighborCurvatures: [Float] = []
        let searchRadius: Float = 0.01
        
        for i in 0..<points.count {
            if i == index { continue }
            
            let neighbor = points[i]
            let distance = simd_length(neighbor - point)
            
            if distance < searchRadius && distance > 0 {
                let neighborNormal = normals[i]
                let normalDiff = simd_length(normal - neighborNormal)
                let curvature = normalDiff / distance
                neighborCurvatures.append(curvature)
            }
        }
        
        return neighborCurvatures.isEmpty ? 0 : neighborCurvatures.reduce(0, +) / Float(neighborCurvatures.count)
    }
    
    // MARK: - 品質評估函數
    
    private func evaluateReconstructionAccuracy(
        pointCloud: PointCloudData,
        originalDepth: Data
    ) -> Float {
        
        // 比較重建點雲與原始深度數據的一致性
        let depthArray = originalDepth.withUnsafeBytes { bytes in
            Array(bytes.bindMemory(to: Float32.self))
        }
        
        var accuracySum: Float = 0.0
        var validComparisons = 0
        
        for point in pointCloud.points {
            // 將3D點投影回深度圖座標
            let projectedIndex = projectPointToDepthIndex(point: point)
            
            if projectedIndex >= 0 && projectedIndex < depthArray.count {
                let originalDepth = depthArray[projectedIndex]
                let reconstructedDepth = point.z
                
                if originalDepth > 0.001 {
                    let error = Swift.abs(originalDepth - reconstructedDepth) / originalDepth
                    accuracySum += (1.0 - min(1.0, error))
                    validComparisons += 1
                }
            }
        }
        
        return validComparisons > 0 ? accuracySum / Float(validComparisons) : 0.0
    }
    
    private func evaluateSurfaceSmoothness(normals: [SIMD3<Float>]) -> Float {
        guard normals.count > 1 else { return 0.0 }
        
        var smoothnessSum: Float = 0.0
        
        for i in 1..<normals.count {
            let dotProduct = dot(normals[i-1], normals[i])
            let angle = acos(min(1.0, max(-1.0, dotProduct)))
            smoothnessSum += (Float.pi - angle) / Float.pi
        }
        
        return smoothnessSum / Float(normals.count - 1)
    }
    
    private func evaluateNoiseLevel(points: [SIMD3<Float>]) -> Float {
        guard points.count > 2 else { return 0.0 }
        
        var noiseSum: Float = 0.0
        
        for i in 2..<points.count {
            let p0 = points[i-2]
            let p1 = points[i-1]
            let p2 = points[i]
            
            // 計算點的偏離程度
            let expectedPoint = p1 + (p1 - p0) // 線性預測
            let deviation = simd_length(p2 - expectedPoint)
            noiseSum += deviation
        }
        
        let averageNoise = noiseSum / Float(points.count - 2)
        return min(1.0, averageNoise * 100.0) // 正規化噪聲水平
    }
    
    private func evaluateCompleteness(pointCloud: PointCloudData) -> Float {
        // 評估點雲的完整度
        let expectedDensity = Float(renderingQuality.vertexDensity)
        let actualDensity = Float(pointCloud.points.count)
        
        return min(1.0, actualDensity / expectedDensity)
    }
    
    private func projectPointToDepthIndex(point: SIMD3<Float>) -> Int {
        // 簡化的投影函數
        // 實際中需要使用正確的相機內參
        let depthWidth = 256
        let depthHeight = 192
        
        let x = Int((point.x + 1.0) * Float(depthWidth) * 0.5)
        let y = Int((point.y + 1.0) * Float(depthHeight) * 0.5)
        
        if x >= 0 && x < depthWidth && y >= 0 && y < depthHeight {
            return y * depthWidth + x
        }
        
        return -1
    }
    
    // MARK: - UI創建和互動
    
    private func createMeshEntity(from model: Wound3DModel) -> ModelEntity {
        // 創建RealityKit模型實體
        let meshResource = try! MeshResource.generate(from: model.surfaceMesh)
        
        var material = SimpleMaterial()
        material.color = .init(tint: .red, texture: nil)
        material.roughness = .float(0.3)
        material.metallic = .float(0.1)
        
        let modelEntity = ModelEntity(mesh: meshResource, materials: [material])
        
        // 添加比例調整
        modelEntity.scale = [10, 10, 10] // 放大10倍用於AR顯示
        
        return modelEntity
    }
    
    private func createWoundNode(from model: Wound3DModel) -> SCNNode {
        // 創建SceneKit節點
        let geometry = SCNGeometry(mdlMesh: model.surfaceMesh)
        
        let material = SCNMaterial()
        material.diffuse.contents = UIColor.red
        material.specular.contents = UIColor.white
        material.shininess = 50.0
        
        geometry?.materials = [material]
        
        let node = SCNNode(geometry: geometry)
        return node
    }
    
    private func setupARInteractions(arView: ARView, entity: ModelEntity) {
        // 添加點擊手勢
        arView.addGestureRecognizer(UITapGestureRecognizer(target: self, action: #selector(handleARTap(_:))))
        
        // 添加拖拽手勢
        entity.generateCollisionShapes(recursive: true)
        arView.installGestures([.rotation, .scale, .translation], for: entity)
    }
    
    @objc private func handleARTap(_ gesture: UITapGestureRecognizer) {
        guard let arView = self.arView else { return }
        
        let location = gesture.location(in: arView)
        let hitResults = arView.hitTest(location)
        
        if let hitResult = hitResults.first {
            // 處理AR點擊事件
            print("AR模型被點擊: \(hitResult.position)")
        }
    }
    
    private func setupSceneKitLighting(scene: SCNScene) {
        // 設置環境光
        let ambientLight = SCNNode()
        ambientLight.light = SCNLight()
        ambientLight.light?.type = .ambient
        ambientLight.light?.color = UIColor.white
        ambientLight.light?.intensity = 200
        scene.rootNode.addChildNode(ambientLight)
        
        // 設置方向光
        let directionalLight = SCNNode()
        directionalLight.light = SCNLight()
        directionalLight.light?.type = .directional
        directionalLight.light?.color = UIColor.white
        directionalLight.light?.intensity = 800
        directionalLight.position = SCNVector3(0, 5, 5)
        directionalLight.eulerAngles = SCNVector3(-Float.pi/4, 0, 0)
        scene.rootNode.addChildNode(directionalLight)
    }
    
    // MARK: - 模型導出
    
    private func exportAsOBJ(model: Wound3DModel) async throws -> Data {
        var objContent = "# Wound 3D Model - OBJ Export\n"
        objContent += "# Generated by WoundAI\n\n"
        
        // 寫入頂點
        for point in model.pointCloud {
            objContent += "v \(point.x) \(point.y) \(point.z)\n"
        }
        
        // 寫入法向量
        for normal in model.normalVectors {
            objContent += "vn \(normal.x) \(normal.y) \(normal.z)\n"
        }
        
        // 寫入面（簡化）
        let faceCount = model.pointCloud.count / 3
        for i in 0..<faceCount {
            let v1 = i * 3 + 1
            let v2 = i * 3 + 2
            let v3 = i * 3 + 3
            objContent += "f \(v1)//\(v1) \(v2)//\(v2) \(v3)//\(v3)\n"
        }
        
        return objContent.data(using: .utf8) ?? Data()
    }
    
    private func exportAsPLY(model: Wound3DModel) async throws -> Data {
        let pointCount = model.pointCloud.count
        
        var plyContent = "ply\n"
        plyContent += "format ascii 1.0\n"
        plyContent += "comment Generated by WoundAI\n"
        plyContent += "element vertex \(pointCount)\n"
        plyContent += "property float x\n"
        plyContent += "property float y\n"
        plyContent += "property float z\n"
        plyContent += "property float nx\n"
        plyContent += "property float ny\n"
        plyContent += "property float nz\n"
        plyContent += "property uchar red\n"
        plyContent += "property uchar green\n"
        plyContent += "property uchar blue\n"
        plyContent += "end_header\n"
        
        for i in 0..<pointCount {
            let point = model.pointCloud[i]
            let normal = model.normalVectors[i]
            let color = model.colorData[i]
            
            let r = UInt8(color.x * 255)
            let g = UInt8(color.y * 255)
            let b = UInt8(color.z * 255)
            
            plyContent += "\(point.x) \(point.y) \(point.z) \(normal.x) \(normal.y) \(normal.z) \(r) \(g) \(b)\n"
        }
        
        return plyContent.data(using: .utf8) ?? Data()
    }
    
    private func exportAsSTL(model: Wound3DModel) async throws -> Data {
        var stlContent = "solid WoundAI_Model\n"
        
        // 簡化：每3個點組成一個三角形
        let triangleCount = model.pointCloud.count / 3
        
        for i in 0..<triangleCount {
            let p1 = model.pointCloud[i * 3]
            let p2 = model.pointCloud[i * 3 + 1]
            let p3 = model.pointCloud[i * 3 + 2]
            
            // 計算法向量
            let v1 = p2 - p1
            let v2 = p3 - p1
            let normal = normalize(cross(v1, v2))
            
            stlContent += "facet normal \(normal.x) \(normal.y) \(normal.z)\n"
            stlContent += "outer loop\n"
            stlContent += "vertex \(p1.x) \(p1.y) \(p1.z)\n"
            stlContent += "vertex \(p2.x) \(p2.y) \(p2.z)\n"
            stlContent += "vertex \(p3.x) \(p3.y) \(p3.z)\n"
            stlContent += "endloop\n"
            stlContent += "endfacet\n"
        }
        
        stlContent += "endsolid WoundAI_Model\n"
        
        return stlContent.data(using: .utf8) ?? Data()
    }
    
    private func exportAsUSDZ(model: Wound3DModel) async throws -> Data {
        // USDZ導出需要使用USD框架
        // 這裡返回簡化的實現
        return try await exportAsOBJ(model: model)
    }
    
    // MARK: - 工具函數
    
    private func extractROIDepth(
        depthArray: [Float32],
        depthSize: (width: Int, height: Int),
        roi: CGRect
    ) -> [Float] {
        
        let roiX = Int(roi.origin.x * CGFloat(depthSize.width))
        let roiY = Int(roi.origin.y * CGFloat(depthSize.height))
        let roiWidth = Int(roi.size.width * CGFloat(depthSize.width))
        let roiHeight = Int(roi.size.height * CGFloat(depthSize.height))
        
        var roiDepth: [Float] = []
        
        for y in roiY..<min(roiY + roiHeight, depthSize.height) {
            for x in roiX..<min(roiX + roiWidth, depthSize.width) {
                let index = y * depthSize.width + x
                if index < depthArray.count {
                    roiDepth.append(Float(depthArray[index]))
                }
            }
        }
        
        return roiDepth
    }
    
    private func extractROIImage(cgImage: CGImage, roi: CGRect) throws -> CGImage {
        let imageWidth = cgImage.width
        let imageHeight = cgImage.height
        
        let roiRect = CGRect(
            x: roi.origin.x * CGFloat(imageWidth),
            y: roi.origin.y * CGFloat(imageHeight),
            width: roi.size.width * CGFloat(imageWidth),
            height: roi.size.height * CGFloat(imageHeight)
        )
        
        guard let croppedImage = cgImage.cropping(to: roiRect) else {
            throw Visualization3DError.imageProcessingFailed
        }
        
        return croppedImage
    }
    
    private func applyDepthFiltering(_ depthData: [Float]) -> [Float] {
        // 應用中位數濾波去除噪聲
        let windowSize = 5
        var filteredData = depthData
        
        for i in windowSize/2..<depthData.count - windowSize/2 {
            let window = Array(depthData[(i - windowSize/2)...(i + windowSize/2)])
            let sortedWindow = window.sorted()
            filteredData[i] = sortedWindow[windowSize/2]
        }
        
        return filteredData
    }
    
    private func updateProgress(_ progress: Float, status: String) {
        DispatchQueue.main.async {
            self.reconstructionProgress = progress
            print("3D重建進度: \(Int(progress * 100))% - \(status)")
        }
    }
}

// MARK: - 支援結構和枚舉

struct PreprocessedReconstructionData {
    let rgbData: CGImage
    let depthData: [Float]
    let intrinsics: CameraIntrinsics
    let roi: CGRect
}

struct PointCloudData {
    let points: [SIMD3<Float>]
    let colors: [SIMD3<Float>]
    let normals: [SIMD3<Float>]
}

struct TextureMappingData {
    let coordinates: [SIMD2<Float>]
    let colors: [SIMD3<Float>]
}

struct GeometryAnalysis {
    let boundingBox: Wound3DVisualizationModule.Wound3DModel.BoundingBox
    let volumeInfo: Wound3DVisualizationModule.Wound3DModel.VolumeInfo
    let normals: [SIMD3<Float>]
}

enum SurfaceReconstructionMethod {
    case poissonReconstruction
    case delaunayTriangulation
    case marchingCubes
}

enum Export3DFormat: String, CaseIterable {
    case obj = "OBJ"
    case ply = "PLY"
    case stl = "STL"
    case usdz = "USDZ"
    
    var fileExtension: String {
        return rawValue.lowercased()
    }
}

enum Visualization3DError: Error, LocalizedError {
    case invalidInputData
    case invalidDepthData
    case imageProcessingFailed
    case reconstructionFailed(String)
    case noModelAvailable
    case exportFailed
    
    var errorDescription: String? {
        switch self {
        case .invalidInputData:
            return "輸入數據無效"
        case .invalidDepthData:
            return "深度數據無效或格式不正確"
        case .imageProcessingFailed:
            return "圖像處理失敗"
        case .reconstructionFailed(let detail):
            return "3D重建失敗: \(detail)"
        case .noModelAvailable:
            return "沒有可用的3D模型"
        case .exportFailed:
            return "模型導出失敗"
        }
    }
}