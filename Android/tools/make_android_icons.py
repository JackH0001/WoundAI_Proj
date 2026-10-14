#!/usr/bin/env python3
"""Export shared medical artwork to legacy, adaptive and Play icons.
Source hashes and earlier artwork are retained in iOS/IconSources.
Adaptive export places the artwork on a transparent foreground and uses
an edge-sampled background. Launcher masks still vary by device.
"""
import argparse
import hashlib
import statistics
import sys
from pathlib import Path

from PIL import Image, ImageDraw

# provenance.json 記錄的核定圖稿雜湊。對不上就停——這是為了確保匯出的是
# 經核定的那一張，而不是某次實驗留下來的檔案。
APPROVED_SHA256 = "e37a8b3fa705c17329f8965d27427aec69084e206858c793ce7a135b49bce988"

# 密度 → 48dp 基準的像素邊長（Android 啟動器圖示的傳統尺寸）
LEGACY = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}
# adaptive icon 的畫布固定 108dp
ADAPTIVE = {"mdpi": 108, "hdpi": 162, "xhdpi": 216, "xxhdpi": 324, "xxxhdpi": 432}
# Fit artwork inside the 66dp content region of the 108dp canvas.
SAFE_FRACTION = 66 / 108
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
