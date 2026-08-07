import Foundation

/**
 組織分型 v2（白平衡 + HSV 飽和度感知,遮罩內互斥）— 對等 Android TissueClassifierV2。
 與後端 wound_classifier.tissue_classmap_v2 一致；HSV 採 OpenCV 8-bit 公式(已驗證與 cv2 0 差異)。
 組織碼：1 壞死 / 2 腐肉 / 3 肉芽 / 4 上皮 / 5 其他。輔助、非診斷、需醫師確認。
 金標 engineering/generated/tissue_golden.json。
 */
enum TissueClassifierV2 {
    struct HSV { let h: Int; let s: Int; let v: Int }

    static func rgb2hsv(_ r: Int, _ g: Int, _ b: Int) -> HSV {
        let R = Double(r), G = Double(g), B = Double(b)
        let v = max(R, max(G, B)), mn = min(R, min(G, B)), d = v - mn
        let s = v == 0 ? 0 : d / v * 255.0
        var h: Double
        if d == 0 { h = 0 }
        else if v == R { h = 60 * (G - B) / d }
        else if v == G { h = 120 + 60 * (B - R) / d }
        else { h = 240 + 60 * (R - G) / d }
        if h < 0 { h += 360 }
        h /= 2.0
        return HSV(h: Int(h.rounded()), s: Int(s.rounded()), v: Int(v.rounded()))
    }

    /// 單像素互斥分類(輸入應為已白平衡 RGB)。
    static func classifyPixel(_ r: Int, _ g: Int, _ b: Int) -> Int {
        let hsv = rgb2hsv(r, g, b)
        if hsv.v < 75 && hsv.s < 90 { return 1 }                       // 壞死
        if (18...45).contains(hsv.h) && hsv.s >= 60 && hsv.v >= 60 { return 2 }  // 腐肉
        if hsv.v >= 170 && hsv.s < 70 && r > 150 { return 4 }          // 上皮
        if (hsv.h < 15 || hsv.h > 160) && hsv.s >= 60 { return 3 }     // 肉芽
        return 5                                                       // 其他
    }

    /// 灰世界白平衡增益 gain_c = meanAll / mean_c。
    static func wbGains(_ meanR: Double, _ meanG: Double, _ meanB: Double) -> [Double] {
        let mu = (meanR + meanG + meanB) / 3.0
        return [mu / (meanR + 1e-6), mu / (meanG + 1e-6), mu / (meanB + 1e-6)]
    }
    static func applyGain(_ v: Int, _ gain: Double) -> Int { min(255, max(0, Int((Double(v) * gain).rounded()))) }

    /// 遮罩內組織比例。pixels=遮罩內已白平衡 RGB。
    static func proxy(_ pixels: [[Int]]) -> [String: Double] {
        let key = ["necrosis", "slough", "granulation", "epithelial", "other"]
        var cnt = [Int](repeating: 0, count: 6)
        for p in pixels { cnt[classifyPixel(p[0], p[1], p[2])] += 1 }
        let tot = max(pixels.count, 1)
        var out = [String: Double]()
        for i in 1...5 { out[key[i - 1]] = Double(cnt[i]) / Double(tot) }
        return out
    }
}
