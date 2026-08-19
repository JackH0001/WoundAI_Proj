import Foundation
import CoreGraphics
import ImageIO
import UniformTypeIdentifiers

/**
 免貼紙面積／體積估算：LiDAR 深度圖＋相機內參 → 反投影三角化。

 演算法對齊後端 `engineering/phase2/measure3d.py`（2026-08-06 以合成幾何與解析積分
 交叉驗證過 34 項）：

 - **表面積**：每個深度格點反投影成 3D 點（X=(u−cx)Z/fx），相鄰四點拆兩個三角形，
   面積取 3D 三角形面積和。**斜面／曲面因此不會被低估**——這是比 ArUco 平面假設
   更真的地方，也是免貼紙版的立足點。
 - **投影面積**：Σ (Z/fx)(Z/fy)，供與 ArUco 值對照（ArUco 量的本質上是投影面積）。
 - **體積**：傷口環外健康皮膚擬合基準面，Σ max(0, Z_表面 − Z_基準)×格點腳印。
   參考值等級——潛行與隧道單視角量不到，這裡不假裝量得到。

 ## 誤差預算（為什麼敢說「約 5%」要靠實測）

 面積 ∝ Z²：LiDAR 近距 ~1% 深度誤差 → ~2% 面積；分割邊界 1.5–3%；
 內參 <0.5%。合計 3–5% 是**平面～中曲面、25–40cm、傷口 ≥2cm** 的預期，
 小傷口與濕亮面會更差。行銷數字等 phantom 誤差表，不先承諾。
 */
struct DepthAreaResult {
    /// 三角化 3D 表面積（cm²）。
    var surfaceAreaCm2: Double
    /// 投影面積（cm²），與 ArUco 同語意，供對照。
    var projectedAreaCm2: Double
    /// 相對基準面的傷口容積（mL）。基準面擬合失敗為 nil。
    var volumeMl: Double?
    /// 相對基準面的最大深度（mm）。
    var maxDepthMm: Double?
    /// 遮罩內有效深度比例（補洞前）。低於 ~0.7 的結果不可信，要說出來。
    var coverage: Double
    /// 中位攝距（m）。<0.2m 超出 LiDAR 可靠範圍。
    var medianDistanceM: Double
    /// 表面相對光軸的傾角（度，來自環帶平面擬合）。>25° 建議改近正拍。
    var tiltDeg: Double?
}

enum DepthAreaEstimator {

    /**
     - Parameters:
       - polygons: 傷口輪廓（RGB 影像座標，`imageW × imageH` 空間）。
       - depth: 擷取的深度（map 為深度圖解析度；內參相對 refW×refH）。
       - imageW/imageH: 輪廓座標空間（＝上傳後端的 work 影像尺寸）。
     */
    static func estimate(polygons: [[[Int]]], depth: DepthCapture,
                         imageW: Int, imageH: Int,
                         smoothMm: Double = 8) -> DepthAreaResult? {
        let dw = depth.width, dh = depth.height
        guard dw >= 8, dh >= 8, imageW > 0, imageH > 0,
              depth.fx > 0, depth.fy > 0, depth.refWidth > 0,
              !polygons.isEmpty else { return nil }

        // 內參換算到**深度圖像素空間**（原值相對 refW×refH）。
        let sx = Double(dw) / depth.refWidth
        let sy = Double(dh) / depth.refHeight
        let fx = depth.fx * sx, fy = depth.fy * sy
        let cx = depth.cx * sx, cy = depth.cy * sy

        // 遮罩以 2× 深度解析度柵格化再降階成覆蓋比例（邊界格點吃部分面積，
        // 對齊 measure3d 的 edge="fraction"——邊界正是誤差最大的地方，0/1 硬切會抖）。
        let up = 2
        let uw = dw * up, uh = dh * up
        var hi = [UInt8](repeating: 0, count: uw * uh)
        let sU = Double(uw) / Double(imageW)   // 影像座標 → 高解析深度格
        let sV = Double(uh) / Double(imageH)
        for p in polygons where p.count >= 3 {
            // 座標軸向縮放不等比時 scanlineFill 的單一 s 不夠——先把點換算過去。
            let scaled = p.map { [Int((Double($0[0]) * sU).rounded()),
                                 Int((Double($0[1]) * sV).rounded())] }
            MaskTrace.scanlineFill(scaled, s: 1.0, mw: uw, mh: uh, into: &hi)
        }
        var frac = [Double](repeating: 0, count: dw * dh)
        var maskAny = false
        for y in 0..<dh {
            for x in 0..<dw {
                var n = 0
                for dy in 0..<up { for dx in 0..<up {
                    if hi[(y * up + dy) * uw + (x * up + dx)] != 0 { n += 1 }
                } }
                frac[y * dw + x] = Double(n) / Double(up * up)
                if n > 0 { maskAny = true }
            }
        }
        guard maskAny else { return nil }

        // 有效深度與補洞（濕亮面 LiDAR 會給洞；用鄰域均值迭代補，並誠實記 coverage）。
        var z = depth.map.map { Double($0) }
        var valid = z.map { $0.isFinite && $0 > 0.05 && $0 < 5.0 }
        var inMaskValid = 0, inMaskTotal = 0
        var distances: [Double] = []
        for i in 0..<(dw * dh) where frac[i] > 0 {
            inMaskTotal += 1
            if valid[i] { inMaskValid += 1; distances.append(z[i]) }
        }
        guard inMaskTotal > 0 else { return nil }
        let coverage = Double(inMaskValid) / Double(inMaskTotal)
        guard coverage > 0.2 else { return nil }   // 幾乎全是洞，估了也是編故事
        for _ in 0..<6 {
            var changed = false
            for y in 0..<dh { for x in 0..<dw {
                let i = y * dw + x
                if valid[i] { continue }
                var s = 0.0, n = 0
                if x > 0, valid[i-1] { s += z[i-1]; n += 1 }
                if x < dw-1, valid[i+1] { s += z[i+1]; n += 1 }
                if y > 0, valid[i-dw] { s += z[i-dw]; n += 1 }
                if y < dh-1, valid[i+dw] { s += z[i+dw]; n += 1 }
                if n >= 2 { z[i] = s / Double(n); valid[i] = true; changed = true }
            } }
            if !changed { break }
        }
        distances.sort()
        let medianZ = distances.isEmpty ? 0 : distances[distances.count / 2]

        // ⚠ 三角化前**必須**依實體尺度平滑（對齊 measure3d.smooth_depth）。
        //   表面積對擾動是凸函數：逐像素雜訊只會讓三角形變大、永不抵銷。
        //   後端實測：量化+1mm 雜訊 → 表面積 +104%~+171%。2026-08-10 實機同款症狀
        //   （平面印刷測出 1.6–1.9× 假表面積）就是漏了這一步。
        if smoothMm > 0, medianZ > 0 {
            let k = max(1, min(6, Int((smoothMm / 1000 * fx / medianZ).rounded())))
            for _ in 0..<2 {           // 兩趟 box ≈ 高斯
                var out = z
                for y in 0..<dh {
                    for x in 0..<dw {
                        let i = y * dw + x
                        guard valid[i] else { continue }
                        var s = 0.0; var n = 0
                        for dy in -k...k {
                            let ny = y + dy
                            guard ny >= 0, ny < dh else { continue }
                            for dx in -k...k {
                                let nx = x + dx
                                guard nx >= 0, nx < dw else { continue }
                                let j = ny * dw + nx
                                if valid[j] { s += z[j]; n += 1 }
                            }
                        }
                        if n > 0 { out[i] = s / Double(n) }
                    }
                }
                z = out
            }
        }

        func pt(_ x: Int, _ y: Int) -> (Double, Double, Double) {
            let Z = z[y * dw + x]
            return ((Double(x) - cx) * Z / fx, (Double(y) - cy) * Z / fy, Z)
        }
        func triArea(_ a: (Double, Double, Double), _ b: (Double, Double, Double),
                     _ c: (Double, Double, Double)) -> Double {
            let ux = b.0 - a.0, uy = b.1 - a.1, uz = b.2 - a.2
            let vx = c.0 - a.0, vy = c.1 - a.1, vz = c.2 - a.2
            let x = uy * vz - uz * vy, y = uz * vx - ux * vz, w = ux * vy - uy * vx
            return 0.5 * (x * x + y * y + w * w).squareRoot()
        }

        var surface = 0.0, projected = 0.0
        for y in 0..<(dh - 1) {
            for x in 0..<(dw - 1) {
                let i = y * dw + x
                // 格子權重＝四角覆蓋比例均值（edge=fraction）。
                let w = (frac[i] + frac[i+1] + frac[i+dw] + frac[i+dw+1]) / 4
                if w <= 0 { continue }
                guard valid[i], valid[i+1], valid[i+dw], valid[i+dw+1] else { continue }
                let a = pt(x, y), b = pt(x+1, y), c = pt(x, y+1), d = pt(x+1, y+1)
                surface += w * (triArea(a, b, c) + triArea(b, d, c))
                let Zc = (a.2 + b.2 + c.2 + d.2) / 4
                projected += w * (Zc / fx) * (Zc / fy)
            }
        }

        // 體積：環帶（遮罩外 2–6 格）擬合基準面 Z=au+bv+c，正深度積分。
        var volume: Double? = nil
        var maxDepthMm: Double? = nil
        var tiltOut: Double? = nil
        var su = 0.0, sv = 0.0, sz = 0.0, suu = 0.0, svv = 0.0, suv = 0.0, suz = 0.0, svz = 0.0
        var rn = 0
        for y in 0..<dh { for x in 0..<dw {
            let i = y * dw + x
            guard frac[i] == 0, valid[i] else { continue }
            // 距遮罩 2–6 格的環帶
            var near = false, far = true
            for dy in -6...6 { for dx in -6...6 {
                let nx = x + dx, ny = y + dy
                guard nx >= 0, nx < dw, ny >= 0, ny < dh else { continue }
                if frac[ny * dw + nx] > 0 {
                    let d2 = dx * dx + dy * dy
                    if d2 <= 36 { near = true }
                    if d2 < 4 { far = false }
                }
            } }
            guard near, far else { continue }
            let u = Double(x), v = Double(y), Z = z[i]
            su += u; sv += v; sz += Z
            suu += u*u; svv += v*v; suv += u*v; suz += u*Z; svz += v*Z
            rn += 1
        } }
        if rn >= 12 {
            // 3×3 正規方程求 (a,b,c)
            let n = Double(rn)
            let d = suu*(svv*n - sv*sv) - suv*(suv*n - sv*su) + su*(suv*sv - svv*su)
            if abs(d) > 1e-9 {
                let a = (suz*(svv*n - sv*sv) - suv*(svz*n - sv*sz) + su*(svz*sv - svv*sz)) / d
                let b = (suu*(svz*n - sv*sz) - suz*(suv*n - sv*su) + su*(suv*sz - svz*su)) / d
                let c = (suu*(svv*sz - svz*sv) - suv*(suv*sz - svz*su) + suz*(suv*sv - svv*su)) / d
                var vol = 0.0, dmax = 0.0
                for y in 0..<dh { for x in 0..<dw {
                    let i = y * dw + x
                    guard frac[i] > 0, valid[i] else { continue }
                    let planeZ = a * Double(x) + b * Double(y) + c
                    let dep = z[i] - planeZ               // 傷口面比皮膚遠＝凹陷
                    if dep > 0 {
                        let Z = z[i]
                        vol += frac[i] * dep * (Z / fx) * (Z / fy)
                        if dep > dmax { dmax = dep }
                    }
                } }
                volume = vol * 1e6      // m³ → mL
                maxDepthMm = dmax * 1e3
                let tu = a * fx / max(1e-9, medianZ)   // dZ/du ÷ (dX/du=Z/fx)
                let tv = b * fy / max(1e-9, medianZ)
                tiltOut = atan((tu * tu + tv * tv).squareRoot()) * 180 / .pi
            }
        }

        return DepthAreaResult(surfaceAreaCm2: surface * 1e4,
                               projectedAreaCm2: projected * 1e4,
                               volumeMl: volume,
                               maxDepthMm: maxDepthMm,
                               coverage: coverage,
                               medianDistanceM: medianZ,
                               tiltDeg: tiltOut)
    }

    // MARK: - 上傳編碼（契約 docs/depth_capture_contract.md：png16_mm，0＝無效）

    /// f32 公尺 → 16-bit 灰階 PNG（值＝mm，big-endian）。回 (png, 有效遮罩 8-bit PNG)。
    static func encodePng16mm(_ depth: DepthCapture) -> (depthPng: Data, confPng: Data)? {
        let w = depth.width, h = depth.height
        guard w > 0, h > 0, depth.map.count >= w * h else { return nil }
        var d16 = [UInt8](repeating: 0, count: w * h * 2)
        var conf = [UInt8](repeating: 0, count: w * h)
        for i in 0..<(w * h) {
            let m = depth.map[i]
            if m.isFinite, m > 0.05, m < 60.0 {
                let mm = UInt16(min(65535, max(1, Int((m * 1000).rounded()))))
                d16[i * 2] = UInt8(mm >> 8)          // PNG 走 big-endian
                d16[i * 2 + 1] = UInt8(mm & 0xFF)
                conf[i] = 255
            }   // 無效 → 0（契約保留值）
        }
        guard let dPng = grayPng(d16, w: w, h: h, bits: 16),
              let cPng = grayPng(conf, w: w, h: h, bits: 8) else { return nil }
        return (dPng, cPng)
    }

    private static func grayPng(_ bytes: [UInt8], w: Int, h: Int, bits: Int) -> Data? {
        let bpp = bits / 8
        guard let provider = CGDataProvider(data: Data(bytes) as CFData),
              let img = CGImage(width: w, height: h, bitsPerComponent: bits,
                                bitsPerPixel: bits, bytesPerRow: w * bpp,
                                space: CGColorSpaceCreateDeviceGray(),
                                bitmapInfo: bits == 16
                                    ? CGBitmapInfo(rawValue: CGImageAlphaInfo.none.rawValue | CGBitmapInfo.byteOrder16Big.rawValue)
                                    : CGBitmapInfo(rawValue: CGImageAlphaInfo.none.rawValue),
                                provider: provider, decode: nil,
                                shouldInterpolate: false, intent: .defaultIntent)
        else { return nil }
        let out = NSMutableData()
        guard let dest = CGImageDestinationCreateWithData(out, UTType.png.identifier as CFString, 1, nil)
        else { return nil }
        CGImageDestinationAddImage(dest, img, nil)
        guard CGImageDestinationFinalize(dest) else { return nil }
        return out as Data
    }

    /// 上傳用內參（**深度圖像素空間**——契約明言少了它整批資料報廢）。
    static func intrinsicsForUpload(_ d: DepthCapture) -> [String: Double] {
        let sx = Double(d.width) / max(1, d.refWidth)
        let sy = Double(d.height) / max(1, d.refHeight)
        return ["fx": d.fx * sx, "fy": d.fy * sy, "cx": d.cx * sx, "cy": d.cy * sy]
    }
}
