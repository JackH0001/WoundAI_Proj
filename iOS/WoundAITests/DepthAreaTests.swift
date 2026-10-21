import XCTest
#if WOUND_INSTITUTION_TESTS
@testable import WoundAIInstitution
#else
@testable import WoundMeasurementApp
#endif

/**
 深度面積估算的解析解金標（對齊後端 `measure3d.py` 的合成驗證思路）。

 合成深度圖 → 已知幾何的解析面積比對。這裡驗的是**免貼紙版能不能承諾 5%** 的
 數學核心：反投影、三角化、斜面不低估、體積基準面。
 */
final class DepthAreaTests: XCTestCase {

    /// 產生合成 DepthCapture：dw×dh、內參置中、指定每像素深度。
    private func synth(dw: Int, dh: Int, fx: Double, z: (Int, Int) -> Float) -> DepthCapture {
        var map = [Float](repeating: 0, count: dw * dh)
        for y in 0..<dh { for x in 0..<dw { map[y * dw + x] = z(x, y) } }
        return DepthCapture(map: map, width: dw, height: dh,
                            fx: fx, fy: fx,
                            cx: Double(dw) / 2, cy: Double(dh) / 2,
                            refWidth: Double(dw), refHeight: Double(dh),
                            accuracy: "absolute", filtered: false,
                            rgbWidth: dw * 8, rgbHeight: dh * 8)
    }

    /// 正對平面：投影面積解析解 = (px 數)·(Z/fx)²。表面積應等於投影面積。
    func testFlatPlaneMatchesAnalytic() throws {
        let dw = 128, dh = 128
        let fx = 100.0, Z = 0.30
        let d = synth(dw: dw, dh: dh, fx: fx) { _, _ in Float(Z) }
        // 遮罩：RGB 空間（dw*8）中央 400×400 → 深度空間 50×50 格
        let rgbW = dw * 8, rgbH = dh * 8
        let m = 400
        let poly: [[Int]] = [[rgbW/2 - m/2, rgbH/2 - m/2], [rgbW/2 + m/2, rgbH/2 - m/2],
                             [rgbW/2 + m/2, rgbH/2 + m/2], [rgbW/2 - m/2, rgbH/2 + m/2]]
        let r = try XCTUnwrap(DepthAreaEstimator.estimate(polygons: [poly], depth: d,
                                                          imageW: rgbW, imageH: rgbH, smoothMm: 0))
        // 解析：50×50 深度格，每格 (Z/fx)² m² → 2500 · (0.003)² = 0.0225 m² = 225 cm²
        let expect = 2500.0 * (Z / fx) * (Z / fx) * 1e4
        XCTAssertEqual(r.projectedAreaCm2, expect, accuracy: expect * 0.03,
                       "投影面積應在解析解 3% 內（邊界格點取分數權重）")
        XCTAssertEqual(r.surfaceAreaCm2, r.projectedAreaCm2,
                       accuracy: expect * 0.01, "正對平面時表面積＝投影面積")
        XCTAssertEqual(r.coverage, 1.0, accuracy: 0.001)
        XCTAssertEqual(r.medianDistanceM, Z, accuracy: 0.001)
    }

    /**
     斜面：深度沿 x 線性變化（平面繞 y 軸傾斜）。**表面積必須大於投影面積約 1/cosθ**
     ——這正是 ArUco 平面假設會低估、深度法能救回來的那一塊。
     */
    func testTiltedPlaneNotUnderestimated() throws {
        let dw = 128, dh = 128
        let fx = 200.0, Z0 = 0.30
        // 每格深度增量 dz/dx：讓表面相對影像平面傾斜。
        // 世界座標中 dX ≈ Z/fx per px；取 dz = 0.4·Z/fx per px → tanθ = 0.4，1/cosθ ≈ 1.077
        let dzdx = 0.4 * Z0 / fx
        let d = synth(dw: dw, dh: dh, fx: fx) { x, _ in Float(Z0 + Double(x - dw/2) * dzdx) }
        let rgbW = dw * 8, rgbH = dh * 8
        let m = 320
        let poly: [[Int]] = [[rgbW/2 - m/2, rgbH/2 - m/2], [rgbW/2 + m/2, rgbH/2 - m/2],
                             [rgbW/2 + m/2, rgbH/2 + m/2], [rgbW/2 - m/2, rgbH/2 + m/2]]
        let r = try XCTUnwrap(DepthAreaEstimator.estimate(polygons: [poly], depth: d,
                                                          imageW: rgbW, imageH: rgbH, smoothMm: 0))
        let ratio = r.surfaceAreaCm2 / r.projectedAreaCm2
        // 期望 ≈ √(1+0.4²) = 1.077（透視微擾容忍 ±3%）
        XCTAssertEqual(ratio, 1.077, accuracy: 0.033,
                       "斜面表面積/投影比應 ≈ 1/cosθ，實得 \(ratio)")
    }

    /// 體積：平面皮膚中央挖一個等深方槽 → 容積 = 槽底面積 × 深。
    func testVolumeOfRecessedBox() throws {
        let dw = 128, dh = 128
        let fx = 100.0, Z = 0.30
        let depthM = 0.005                       // 5 mm 深
        let half = 20                            // 槽半寬（深度格）
        let d = synth(dw: dw, dh: dh, fx: fx) { x, y in
            let inBox = abs(x - dw/2) < half && abs(y - dh/2) < half
            return Float(inBox ? Z + depthM : Z)
        }
        let rgbW = dw * 8, rgbH = dh * 8
        // 遮罩取槽的範圍（含一點邊）
        let m = (half * 2) * 8
        let poly: [[Int]] = [[rgbW/2 - m/2, rgbH/2 - m/2], [rgbW/2 + m/2, rgbH/2 - m/2],
                             [rgbW/2 + m/2, rgbH/2 + m/2], [rgbW/2 - m/2, rgbH/2 + m/2]]
        let r = try XCTUnwrap(DepthAreaEstimator.estimate(polygons: [poly], depth: d,
                                                          imageW: rgbW, imageH: rgbH, smoothMm: 0))
        let vol = try XCTUnwrap(r.volumeMl)
        // 解析：(2·half)² 格 × (Z/fx)² × depthM → mL；槽底 Z+5mm 的腳印略大，容忍 5%
        let footprint = (Z / fx) * (Z / fx)
        let expect = Double(4 * half * half) * footprint * depthM * 1e6
        XCTAssertEqual(vol, expect, accuracy: expect * 0.08,
                       "方槽容積應在解析解 8% 內，實得 \(vol) vs \(expect)")
        let maxD = try XCTUnwrap(r.maxDepthMm)
        XCTAssertEqual(maxD, 5.0, accuracy: 0.8)
    }

    /// png16_mm 編碼往返：值=mm、0=無效、big-endian。
    func testPng16Encoding() throws {
        let d = synth(dw: 16, dh: 16, fx: 50) { x, _ in x == 0 ? 0 : Float(0.3) }
        let enc = try XCTUnwrap(DepthAreaEstimator.encodePng16mm(d))
        XCTAssertGreaterThan(enc.depthPng.count, 8)
        // PNG magic
        XCTAssertEqual([UInt8](enc.depthPng.prefix(4)), [0x89, 0x50, 0x4E, 0x47])
        // IHDR: bit depth 16、colour type 0（灰階）——後端就是驗這兩個位元組
        XCTAssertEqual(enc.depthPng[24], 16)
        XCTAssertEqual(enc.depthPng[25], 0)
    }
    /// Analytic sloped-wall cavities, independent of the physical silicone phantom.
    /// The surface follows a cone in world coordinates; its volume is A*h/3.
    func testSlopedCavitiesKeepVolumeIndependentOfSurfaceSmoothing() throws {
        for (areaCm2, volumeMl) in [(10.04, 3.9), (3.0, 0.5)] {
            let w = 128, fx = 400.0, z0 = 0.30
            let radius = sqrt(areaCm2 / 1e4 / .pi)
            let height = volumeMl / 1e6 * 3 / (areaCm2 / 1e4)
            let radiusPx = radius * fx / z0
            let d = synth(dw: w, dh: w, fx: fx) { x, y in
                let pixelRadius = hypot(Double(x - w/2), Double(y - w/2))
                let z = pixelRadius < radiusPx ? (z0 + height) / (1 + height * pixelRadius / (fx * radius)) : z0
                return Float(z)
            }
            let polygon = (0..<120).map { i -> [Int] in
                let angle = Double(i) * 2 * .pi / 120
                return [Int(((Double(w/2) + radiusPx * cos(angle)) * 8).rounded()),
                        Int(((Double(w/2) + radiusPx * sin(angle)) * 8).rounded())]
            }
            let original = d.map
            let reference = try XCTUnwrap(DepthAreaEstimator.estimate(polygons: [polygon], depth: d,
                imageW: w*8, imageH: w*8, smoothMm: 0))
            let normal = try XCTUnwrap(DepthAreaEstimator.estimate(polygons: [polygon], depth: d,
                imageW: w*8, imageH: w*8))
            XCTAssertEqual(try XCTUnwrap(reference.volumeMl), volumeMl, accuracy: volumeMl * 0.08)
            XCTAssertEqual(try XCTUnwrap(normal.volumeMl), try XCTUnwrap(reference.volumeMl), accuracy: 1e-10,
                "Surface smoothing must not flatten the volume or contaminate the reference ring")
            XCTAssertEqual(try XCTUnwrap(normal.maxDepthMm), height * 1000, accuracy: 0.05)
            XCTAssertEqual(d.map, original, "Research source depth must remain unchanged")
        }
    }

    /// AVFoundation commonly uses 6 RGB pixels per depth pixel, ARKit 7.5.
    /// The same physical cavity must not change when intrinsics/RGB reference grids change.
    func testVolumeInvariantAcrossRGBAndIntrinsicsReferenceScales() throws {
        let base = synth(dw:128,dh:128,fx:200) { x,y in
            (44..<84).contains(x) && (44..<84).contains(y) ? 0.3043 : 0.3
        }
        var volumes:[Double]=[]
        for scale in [1.0,4.0,6.0,7.5,8.0] {
            let size=Int(128*scale)
            let d=DepthCapture(map:base.map,width:128,height:128,fx:200*scale,fy:200*scale,
                cx:64*scale,cy:64*scale,refWidth:Double(size),refHeight:Double(size),
                accuracy:"absolute",filtered:false,rgbWidth:size,rgbHeight:size)
            let poly=[[44,44],[84,44],[84,84],[44,84]].map { $0.map { Int(Double($0)*scale) } }
            let result=try XCTUnwrap(DepthAreaEstimator.estimate(polygons:[poly],depth:d,imageW:size,imageH:size,smoothMm:0))
            volumes.append(try XCTUnwrap(result.volumeMl))
            XCTAssertEqual(try XCTUnwrap(result.maxDepthMm),4.3,accuracy:0.001)
            XCTAssertEqual(d.map,base.map)
        }
        for volume in volumes { XCTAssertEqual(volume,volumes[0],accuracy:1e-8) }
    }

    func testMissingCavityDepthDoesNotBecomeInventedVolume() throws {
        let d = synth(dw:64,dh:64,fx:100) { x,y in
            if x == 32 && y == 32 { return 0 }
            return (20..<44).contains(x) && (20..<44).contains(y) ? 0.305 : 0.3
        }
        let polygon = [[160,160],[352,160],[352,352],[160,352]]
        let r = try XCTUnwrap(DepthAreaEstimator.estimate(polygons:[polygon],depth:d,imageW:512,imageH:512))
        XCTAssertLessThan(r.coverage,1)
        XCTAssertNil(r.volumeMl, "Filled pixels are not measured cavity depth")
        XCTAssertNil(r.maxDepthMm)
    }

    func testExplicitNormalReferenceBypassesContaminatedRing() throws {
        let d = synth(dw:64,dh:64,fx:200) { x,y in
            (14..<50).contains(x) && (14..<50).contains(y) ? 0.304 : 0.3
        }
        let poly = [[176,176],[336,176],[336,336],[176,336]]
        let independent = (0..<4096).map { i in
            let x = i % 64, y = i / 64
            return x < 10 || x > 54 || y < 10 || y > 54
        }
        let automatic = try XCTUnwrap(DepthAreaEstimator.estimate(polygons:[poly],depth:d,imageW:512,imageH:512))
        let selected = try XCTUnwrap(DepthAreaEstimator.estimate(polygons:[poly],depth:d,imageW:512,imageH:512,
            normalReferenceMask:independent))
        XCTAssertEqual(automatic.volumeMl!,0,accuracy:1e-7)
        XCTAssertGreaterThan(selected.volumeMl!,3)
        XCTAssertEqual(selected.maxDepthMm!,4,accuracy:0.001)
        XCTAssertEqual(selected.referenceVolume?.referenceSource,.rgbSelectedNormal)
        XCTAssertEqual(automatic.referenceVolume?.referenceSource,.automaticRing)
        XCTAssertEqual(selected.surfaceAreaCm2,automatic.surfaceAreaCm2)
        XCTAssertEqual(selected.referenceVolume?.metricGroundTruthAllowed,false)
    }

    func testInvalidExplicitReferenceCannotFallBackSilently() throws {
        let d = synth(dw:64,dh:64,fx:200) { _,_ in 0.3 }
        let poly = [[176,176],[336,176],[336,336],[176,336]]
        for ref in [[Bool](repeating:true,count:4096),[false]] {
            let r = try XCTUnwrap(DepthAreaEstimator.estimate(polygons:[poly],depth:d,imageW:512,imageH:512,
                normalReferenceMask:ref))
            XCTAssertNil(r.volumeMl)
            XCTAssertNil(r.referenceVolume)
            XCTAssertGreaterThan(r.projectedAreaCm2,0)
        }
    }

}
