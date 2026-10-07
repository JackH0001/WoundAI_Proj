#!/usr/bin/env python3
"""把使用者核定的圖稿匯出成 Android 啟動器圖示，不重畫構圖。

SSOT：iOS/IconSources/medical.png（與 iOS 同一張；來源與核定紀錄見
iOS/IconSources/provenance.json）。本腳本只做縮放與遮罩，不改顏色、不去背、
不重新排版——那張圖是 2026-10-01 經使用者核定的，構圖不是我們可以順手調的東西。

## 為什麼需要這支腳本（修掉的原始缺陷）

原本 res/mipmap-hdpi/ic_launcher.xml 是：

    <bitmap android:src="@drawable/ic_launcher_foreground" />

而 ic_launcher_foreground 是一個 <vector>。<bitmap> 要的是可解碼的點陣圖，
不是向量 XML，所以執行期 BitmapDrawable 取不到圖，logcat 每一兩秒噴一次：

    PackageManager: Failure retrieving resources for com.woundai.app:
    Drawable com.woundai.app:mipmap/ic_launcher with resource ID #0x7f0d0000

資源條目是存在的（ID 解得出來），壞的是取用那一步。症狀是桌面上沒有可按的
圖示——2026-10-05 在 iPlay60_mini_Pro 上實測到，當時只能用 adb am start 啟動。
另外那個 vector 是兩個填 @color/white 的圓角矩形、沒有背景層，白底桌面上
就算 inflate 成功也是看不見的。兩個獨立缺陷疊在一起。

## adaptive icon 的前景層為什麼是「裁切到主體」而不是整張

Android 8+ 的啟動器會用自己的遮罩（圓形／方圓形／水滴）裁 108dp 的畫布，
只有內圈 66dp 保證看得見。實測這張圖的主體（十字／鏡頭／傷口示意）佔畫面
82.8%，整張滿版放進前景層的話，圓形遮罩會削掉十字的臂端。

把整張縮到 73.8% 可以塞進安全區，但桌面上會明顯比旁邊的 App 小一圈。
所以改成：
  * 背景層 = 圖稿自己的背景色（邊框像素中位數，不是猜的）
  * 前景層 = 裁切到主體 bbox 的那一塊，縮到安全區、置中於透明畫布
兩層顏色相同，主體周圍那圈背景色會無縫融進背景層 → 視覺上是滿版圖示，
主體維持正常大小，而且**沒有任何一個像素被遮罩裁掉**。
不需要去背（會產生邊緣光暈），也不需要重組構圖。

## 相容 API 24–25

minSdk 24，而 adaptive icon 是 API 26 起。所以 mipmap-anydpi-v26 放
<adaptive-icon>，而 mipmap-hdpi 的那兩個 XML 改成合法的 <bitmap> 包住真正的
PNG——這同時修掉了上面那個原始缺陷，不需要刪檔（沙箱掛載不允許 unlink）。

用法：python3 Android/tools/make_android_icons.py [--source <png>]
"""
import argparse
import hashlib
import statistics
import sys
from pathlib import Path

from PIL import Image, ImageDraw

# provenance.json 記錄的核定圖稿雜湊。對不上就停——這是為了確保匯出的是
# 經核定的那一張，而不是某次實驗留下來的檔案。
APPROVED_SHA256 = "1034f119c5c9c59926458adcd8292a49285088fe835fe9c9a95ee73947805a91"

# 密度 → 48dp 基準的像素邊長（Android 啟動器圖示的傳統尺寸）
LEGACY = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}
# adaptive icon 的畫布固定 108dp
ADAPTIVE = {"mdpi": 108, "hdpi": 162, "xhdpi": 216, "xxhdpi": 324, "xxxhdpi": 432}
# 前景層裡圖稿佔畫布的比例。
#
# **不是 66/108（Google 文件說的「保證可見內圈」）。** 實測用 66/108 匯出後，
# 桌面上明顯比旁邊的 App 小一圈——因為這張圖稿本身就已經留了邊：青色磚只佔
# 原圖的 82.8%，再縮到 61.1% 等於在留好邊的設計上又加一圈，變成「圖示裡面
# 還有一個圖示」。
#
# 76/108 = 70.37% 是**幾何上限**：正方形置中、邊長佔畫布 f 時，四角到中心的
# 距離是 (f/2)·√2；圓形遮罩半徑 0.5，所以不被裁的條件是 f ≤ 1/√2 = 70.71%。
# 70.37% 剛好卡在裡面，而青磚本身還有圓角，餘裕更多。
# 換圖稿時這個值要重新判斷——它取決於圖稿自己留了多少邊。
SAFE_FRACTION = 76 / 108
PLAY_STORE_PX = 512        # Play 商店資訊要的圖示尺寸


def background_colour(im):
    """以邊框像素的中位數當背景色。取單一角落會被壓縮雜訊影響。"""
    px, (w, h) = im.load(), im.size
    samples = []
    for x in range(0, w, 7):
        samples += [px[x, 2], px[x, h - 3]]
    for y in range(0, h, 7):
        samples += [px[2, y], px[w - 3, y]]
    return tuple(int(statistics.median(c[i] for c in samples)) for i in range(3))


def subject_bbox(im, bg, tol=18):
    """主體（與背景色差異超過 tol 的像素）的外接矩形。"""
    px, (w, h) = im.load(), im.size
    x0, y0, x1, y1 = w, h, -1, -1
    for y in range(h):
        for x in range(w):
            p = px[x, y]
            if max(abs(p[i] - bg[i]) for i in range(3)) > tol:
                x0, y0 = min(x0, x), min(y0, y)
                x1, y1 = max(x1, x), max(y1, y)
    if x1 < 0:
        raise SystemExit("圖稿裡找不到主體——整張都是背景色？")
    return x0, y0, x1, y1


def circle_mask(size):
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).ellipse((0, 0, size - 1, size - 1), fill=255)
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=None,
                    help="圖稿路徑；預設為 iOS/IconSources/medical.png")
    args = ap.parse_args()

    root = Path(__file__).resolve().parents[2]
    src = Path(args.source) if args.source else root / "iOS" / "IconSources" / "medical.png"
    if not src.is_file():
        raise SystemExit(
            f"找不到圖稿：{src}\n"
            "它是 iOS 與 Android 共用的 SSOT。若本分支尚未帶入，"
            "可用 --source 指向別處，或等 iOS 的圖示分支合併。")

    digest = hashlib.sha256(src.read_bytes()).hexdigest()
    if digest != APPROVED_SHA256:
        raise SystemExit(
            f"圖稿雜湊不符，拒絕匯出。\n  預期 {APPROVED_SHA256}\n  實際 {digest}\n"
            "換圖稿要同時更新 iOS/IconSources/provenance.json 與本腳本的常數，"
            "並重新取得使用者核定。")

    im = Image.open(src).convert("RGB")
    bg = background_colour(im)
    x0, y0, x1, y1 = subject_bbox(im, bg)
    subject = im.crop((x0, y0, x1 + 1, y1 + 1))

    res = root / "Android" / "app" / "src" / "main" / "res"
    written = []

    for density, px_size in LEGACY.items():
        d = res / f"mipmap-{density}"
        d.mkdir(parents=True, exist_ok=True)
        # 傳統方形圖示：原構圖等比縮放，不裁切。
        square = im.resize((px_size, px_size), Image.LANCZOS)
        square.save(d / "ic_launcher_sq.png", optimize=True)
        written.append(d / "ic_launcher_sq.png")
        # 傳統圓形圖示：同一張、加圓形遮罩，圓外透明。
        rnd = square.convert("RGBA")
        rnd.putalpha(circle_mask(px_size))
        rnd.save(d / "ic_launcher_round_sq.png", optimize=True)
        written.append(d / "ic_launcher_round_sq.png")

    for density, canvas in ADAPTIVE.items():
        d = res / f"mipmap-{density}"
        d.mkdir(parents=True, exist_ok=True)
        safe = int(round(canvas * SAFE_FRACTION))
        fg = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
        s = subject.resize((safe, safe), Image.LANCZOS).convert("RGBA")
        off = (canvas - safe) // 2
        fg.paste(s, (off, off), s)
        fg.save(d / "ic_launcher_fg.png", optimize=True)
        written.append(d / "ic_launcher_fg.png")

    # Play 商店資訊用的 512×512（Console 手動上傳，不進建置）
    store = root / "Android" / "store"
    store.mkdir(parents=True, exist_ok=True)
    im.resize((PLAY_STORE_PX, PLAY_STORE_PX), Image.LANCZOS).save(
        store / "play-icon-512.png", optimize=True)
    written.append(store / "play-icon-512.png")

    print(f"圖稿 {src.name}  {im.size[0]}x{im.size[1]}  sha256 ✓")
    print(f"  背景色      #%02X%02X%02X" % bg)
    print(f"  主體 bbox   ({x0},{y0})-({x1},{y1})  佔畫面 "
          f"{max(x1-x0+1, y1-y0+1)/im.size[0]*100:.1f}%")
    print(f"  前景層      主體縮至畫布的 {SAFE_FRACTION*100:.1f}%"
          f"（圓形遮罩不裁切的幾何上限為 {1/2**0.5*100:.2f}%）")
    print(f"  寫出 {len(written)} 個檔案")
    print("\n⚠ 背景色要與 res/values/colors.xml 的 ic_launcher_background 一致：")
    print("    <color name=\"ic_launcher_background\">#%02X%02X%02X</color>" % bg)


if __name__ == "__main__":
    sys.exit(main())
