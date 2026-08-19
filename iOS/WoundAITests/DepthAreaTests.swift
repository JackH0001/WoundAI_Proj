import XCTest
@testable import WoundMeasurementApp

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
}
