#!/usr/bin/env python3
"""產生兩個 App 的 1024×1024 圖示（App Store 規格：無 alpha、無圓角、sRGB）。

## 為什麼用程式產生而不是切圖

圖示要能重生：改一次色票就重跑，不必回頭找設計檔。這支腳本是圖示的 SSOT，
輸出直接覆蓋 `Assets.xcassets/AppIcon.appiconset/icon-1024.png`。

## 兩張圖為什麼必須明顯不同

同一個開發者帳號下兩個外觀相近的 App，審查可能以 Guideline 4.3（Spam／重複）
質疑。而且真正的使用者風險是：醫護人員手機上兩個都裝，點錯進民眾版，
以為那個數字可以寫病歷。**外觀差異是安全設計，不是美術偏好。**

  · 醫療版 WoundAI：深臨床藍＋白色量測角框（取景/校正的語彙）
  · 民眾版 WoundLite：青綠＋掃描弧線（親和、非臨床）

## 小尺寸可讀性

iOS 最小顯示到 40×40。所以：單一主體、粗線條、高對比、不放文字。
設計完請縮到 60px 看一眼——那才是使用者真正看到的樣子。
"""
import math
import os
import sys

from PIL import Image, ImageDraw, ImageFilter

S = 1024


def lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def vertical_gradient(top, bottom):
    """垂直漸層底。**不留 alpha**——App Store 拒收含透明通道的圖示。"""
    img = Image.new("RGB", (S, S), top)
    d = ImageDraw.Draw(img)
    for y in range(S):
        d.line([(0, y), (S, y)], fill=lerp(top, bottom, y / (S - 1)))
    return img


def blob(cx, cy, r, wobble, phase, n=360):
    """有機的傷口輪廓（擾動圓）。用兩個諧波疊加，避免看起來像齒輪或花。"""
    pts = []
    for i in range(n):
        a = 2 * math.pi * i / n
        rr = r * (1 + wobble * (0.6 * math.sin(3 * a + phase)
                                + 0.4 * math.sin(5 * a + phase * 1.7)))
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    return pts


def soft_shadow(size, draw_fn, blur=18, offset=(0, 10), alpha=90):
    """主體下方的柔和陰影：小尺寸時把主體與背景分離，避免糊成一團。"""
    lay = Image.new("L", (size, size), 0)
    draw_fn(ImageDraw.Draw(lay))
    lay = lay.filter(ImageFilter.GaussianBlur(blur))
    shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    shadow.putalpha(lay.point(lambda v: min(alpha, v)))
    return shadow, offset


def medical_icon():
    """醫療版：深臨床藍＋白色量測角框＋中央傷口。角框＝取景與校正的語彙。"""
    img = vertical_gradient((11, 52, 102), (24, 92, 158))
    ov = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)

    # 傷口本體（暖紅），先畫陰影再畫實體
    pts = blob(S / 2, S / 2 + 8, 214, 0.11, 0.7)
    sh, off = soft_shadow(S, lambda dd: dd.polygon(pts, fill=255), blur=26, alpha=110)
    img.paste(Image.new("RGB", (S, S), (0, 0, 0)),
              (off[0], off[1]), sh)
    d.polygon(pts, fill=(214, 88, 78, 255))
    # 內圈肉芽色（深淺兩層讓小尺寸也有立體感）
    d.polygon(blob(S / 2, S / 2 + 8, 150, 0.10, 2.1), fill=(178, 58, 52, 255))

    # 白色量測角框：四角 L 形。**用矩形不用線**——線有端點外溢，
    # 會在外側留下 half-width 的小凸角（1024 看不出來、放大檢視就很醜）。
    m, L, w = 176, 150, 34
    for (x, y, sx, sy) in ((m, m, 1, 1), (S - m, m, -1, 1),
                           (m, S - m, 1, -1), (S - m, S - m, -1, -1)):
        for (ex, ey) in ((L, w), (w, L)):          # 橫臂、直臂
            x0, x1 = sorted((x, x + sx * ex))
            y0, y1 = sorted((y, y + sy * ey))
            d.rectangle([x0, y0, x1, y1], fill=(255, 255, 255, 255))

    img.paste(ov, (0, 0), ov)
    return img


def lite_icon():
    """民眾版：青綠＋白色輪廓＋掃描弧線。刻意不用角框，與醫療版一眼可分。"""
    img = vertical_gradient((10, 140, 110), (46, 196, 150))
    ov = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)

    cx, cy, r, ring = S / 2, S / 2 + 26, 236, 30
    pts = blob(cx, cy, r, 0.13, 1.9)
    inner = blob(cx, cy, r - ring, 0.13, 1.9)
    sh, off = soft_shadow(S, lambda dd: dd.polygon(pts, fill=255), blur=28, alpha=100)
    img.paste(Image.new("RGB", (S, S), (0, 0, 0)), (off[0], off[1]), sh)

    # 主體＝**白色圈選環**（民眾版的核心動作就是「把傷口圈起來」），
    # 環內留半透明白讓底色透出來，與醫療版的實心傷口一眼可分。
    #
    # ⚠ 環用「外圈填滿 − 內圈挖空」的遮罩做，**不要用 d.line(pts, joint="curve")**：
    #   360 個點的折線描邊會在每個轉折生出尖刺（2026-08-21 第一版就是這樣）。
    d.polygon(pts, fill=(255, 255, 255, 70))
    mask = Image.new("L", (S, S), 0)
    md = ImageDraw.Draw(mask)
    md.polygon(pts, fill=255)
    md.polygon(inner, fill=0)
    ring_layer = Image.new("RGBA", (S, S), (255, 255, 255, 255))
    ov.paste(ring_layer, (0, 0), mask)

    # 掃描弧線（LiDAR 語彙）：三道由粗到細的弧，暗示深度掃描
    for i, (rad, wid, a) in enumerate(((312, 26, 150), (368, 20, 120), (424, 14, 96))):
        box = [S / 2 - rad, S / 2 + 26 - rad, S / 2 + rad, S / 2 + 26 + rad]
        d.arc(box, start=205, end=205 + a, fill=(255, 255, 255, 235 - i * 55), width=wid)

    img.paste(ov, (0, 0), ov)
    return img


def write(img, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # convert("RGB") 是關鍵：留著 alpha 會在上傳時被 App Store Connect 退件。
    img.convert("RGB").save(path, "PNG", optimize=True)
    print("wrote %s (%dx%d, mode=%s)" % (path, img.size[0], img.size[1], "RGB"))


CONTENTS = """{
  "images" : [
    {
      "filename" : "icon-1024.png",
      "idiom" : "universal",
      "platform" : "ios",
      "size" : "1024x1024"
    }
  ],
  "info" : {
    "author" : "xcode",
    "version" : 1
  }
}
"""


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    targets = [
        (medical_icon(), os.path.join(root, "iOS", "WoundMeasurementApp",
                                      "Assets.xcassets", "AppIcon.appiconset")),
        (lite_icon(), os.path.join(root, "iOS", "WoundLite",
                                   "Assets.xcassets", "AppIcon.appiconset")),
    ]
    for img, d in targets:
        write(img, os.path.join(d, "icon-1024.png"))
        with open(os.path.join(d, "Contents.json"), "w") as f:
            f.write(CONTENTS)
        # 縮圖自查：40px 是 iOS 最小顯示尺寸，看得清楚才算過關
        img.convert("RGB").resize((60, 60), Image.LANCZOS).save(
            os.path.join("/tmp", os.path.basename(os.path.dirname(d)) + "-60.png"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
