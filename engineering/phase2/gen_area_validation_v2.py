# -*- coding: utf-8 -*-
"""面積驗證單 v2 產生器(幾何由程式保證,取代舊 2025-08 貼紙那批不可信驗證單)。
每張 A4(300dpi,1:1 列印):不規則深紅模擬傷口(真值=光柵像素計數,印表機級精確)+
v2 方形貼紙(footprint20mm/marker12mm DICT_4X4_50 id=7,標準白 quiet zone)緊鄰傷口右側
(降低透視尺度差)+ 50mm 比例尺(印後核對 100%)。
輸出 PNG×5 + 合併 PDF;自我驗證:同一套 detect_marker+比例法量回,|誤差|應 <1%。"""
import os, sys, numpy as np, cv2
from PIL import Image, ImageDraw, ImageFont

DPI = 300; MM = DPI / 25.4
A4 = (int(210 * MM), int(297 * MM))
MAROON = (139, 30, 42)          # 深紅(HSV H≈176,S高 → 診斷腳本可分割)
TARGETS = [1.0, 3.0, 5.0, 10.0, 16.0]

def font(sz, bold=False):
    for p in ["/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
              "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc",
              "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc"]:
        if os.path.exists(p):
            try: return ImageFont.truetype(p, sz)
            except Exception: pass
    return ImageFont.load_default()

def make_sticker():
    """v2 方形貼紙:24mm 文件、20mm footprint、12mm marker(id7)、色點/角點。回 PIL(RGB)。"""
    doc = int(round(24 * MM)); fp = int(round(20 * MM)); mk = int(round(12 * MM))
    img = Image.new("RGB", (doc, doc), "white")
    d = ImageDraw.Draw(img)
    o = (doc - fp) // 2
    d.rectangle([o, o, o + fp - 1, o + fp - 1], outline=(150, 150, 150), width=2)
    aru = cv2.aruco.generateImageMarker(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50), 7, mk)
    img.paste(Image.fromarray(cv2.cvtColor(aru, cv2.COLOR_GRAY2RGB)), ((doc - mk) // 2, (doc - mk) // 2))
    c = doc / 2; r3 = 1.5 * MM; r2 = 1.0 * MM; e = 8.5 * MM; g = 9.0 * MM
    for (dx, dy), col in [((0, -e), (255, 0, 0)), ((0, e), (0, 170, 0)), ((-e, 0), (0, 0, 255)), ((e, 0), (230, 200, 0))]:
        d.ellipse([c + dx - r3, c + dy - r3, c + dx + r3, c + dy + r3], fill=col)
    for sx in (-1, 1):
        for sy in (-1, 1):
            d.ellipse([c + sx * g - r2, c + sy * g - r2, c + sx * g + r2, c + sy * g + r2], fill=(46, 46, 46))
    return img

def blob_poly(seed, n=40):
    rng = np.random.default_rng(seed)
    ang = np.linspace(0, 2 * np.pi, n, endpoint=False)
    r = 1 + 0.30 * np.sin(ang * rng.integers(2, 4) + rng.uniform(0, 6)) \
          + 0.16 * np.sin(ang * rng.integers(5, 8) + rng.uniform(0, 6)) + rng.normal(0, 0.03, n)
    r = np.clip(r, 0.35, None)
    return np.stack([r * np.cos(ang), r * np.sin(ang)], 1)

def shoelace(p):
    x, y = p[:, 0], p[:, 1]
    return 0.5 * abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1)))

def gen_sheet(target, seed, sn):
    W, H = A4
    img = Image.new("RGB", (W, H), "white"); d = ImageDraw.Draw(img)
    # 傷口:縮放到目標面積,置於中帶偏左
    poly = blob_poly(seed)
    poly = poly * np.sqrt(target * 100 / shoelace(poly))           # mm
    cx, cy = 78, 138                                                # mm
    pts = [(int(round((cx + x) * MM)), int(round((cy + y) * MM))) for x, y in poly]
    d.polygon(pts, fill=MAROON)
    # 真值=光柵計數(所畫即所印)
    arr = np.array(img); red = ((arr[:, :, 0] > 90) & (arr[:, :, 1] < 90) & (arr[:, :, 2] < 90))
    true_cm2 = red.sum() / (MM * MM) / 100.0
    # v2 貼紙:緊鄰傷口右側(邊到邊 ~14mm)
    st = make_sticker()
    max_x = cx + poly[:, 0].max()
    sx_mm = max_x + 14 + 12                                        # 貼紙中心
    img.paste(st, (int(round((sx_mm - 12) * MM)), int(round((cy - 12) * MM))))
    d = ImageDraw.Draw(img)
    # 標題/說明
    f1, f2, f3 = font(64), font(40), font(34)
    d.text((int(14 * MM), int(12 * MM)), f"WoundAI 面積驗證單 v2  目標 {target:g} cm²(真實 {true_cm2:.2f} cm²)", font=f1, fill="black")
    d.text((int(14 * MM), int(22 * MM)), "1:1・100% 實際大小列印(勿縮放);貼紙=方形20mm複合(marker 12mm id7)。", font=f2, fill="black")
    d.text((int(14 * MM), int(28 * MM)), "拍攝:傷口與貼紙同框、避免反光;可 90/60/30° 各拍一張。", font=f2, fill="black")
    d.text((int((sx_mm - 12) * MM), int((cy + 14) * MM)), "v2 方形20mm(marker12) id7", font=f3, fill="black")
    d.text((int(14 * MM), int((cy + 34) * MM)), f"SN:________    真實面積 {true_cm2:.2f} cm²(光柵計數)", font=f2, fill="black")
    # 50mm 比例尺(印後核對)
    bx, by = 20, 250                                                # mm
    x0, y0 = int(bx * MM), int(by * MM)
    d.line([x0, y0, int((bx + 50) * MM), y0], fill="black", width=max(2, int(0.5 * MM)))
    for i in range(6):
        xt = int((bx + i * 10) * MM)
        d.line([xt, y0 - int(3 * MM), xt, y0], fill="black", width=max(2, int(0.35 * MM)))
        d.text((xt - 20, y0 + 10), f"{i*10}", font=f3, fill="black")
    d.text((x0, y0 - int(9 * MM)), "比例尺 50 mm(每格10mm)—印後量此線應=50.0mm,否則非100%列印", font=f3, fill="black")
    return img, true_cm2

def main(outdir):
    os.makedirs(outdir, exist_ok=True)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import aruco_calibrate as ac
    pages = []; report = []
    for i, t in enumerate(TARGETS):
        img, true_cm2 = gen_sheet(t, seed=101 + i, sn=i + 1)
        p = os.path.join(outdir, f"WoundAI_面積驗證v2_{t:g}cm2.png")
        img.save(p, dpi=(DPI, DPI)); pages.append(img)
        # 自我驗證:同一套量測程式
        bgr = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR); rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        det = ac.detect_marker(rgb)
        hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
        h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
        m = (((h < 12) | (h > 168)) & (s > 80) & (v > 40)).astype(np.uint8)
        cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        big = max(cnts, key=cv2.contourArea); keep = np.zeros_like(m); cv2.drawContours(keep, [big], -1, 1, -1)
        meas = float(ac.measure_area_cm2_ratio(keep, det[0], marker_mm=12.0)) if det is not None else float("nan")
        err = (meas - true_cm2) / true_cm2 * 100
        report.append((t, true_cm2, meas, err, det is not None))
        print(f"{t:g}cm²: 真實{true_cm2:.2f} 量回{meas:.2f} 誤差{err:+.2f}%  ArUco={'✓' if det is not None else '✗'}")
    pdf = os.path.join(outdir, "WoundAI_面積驗證單v2_5張.pdf")
    try:
        pages[0].save(pdf, save_all=True, append_images=pages[1:], resolution=DPI)
    except Exception:  # 無 JPEG 編碼器 → 調色盤(flate)無損且更小
        pp = [im.convert("P", palette=Image.ADAPTIVE, colors=64) for im in pages]
        pp[0].save(pdf, save_all=True, append_images=pp[1:], resolution=DPI)
    print("PDF:", pdf)
    return report

if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "."
    main(out)
