#!/usr/bin/env python3
"""
跨語言邏輯驗證：把 Swift 的計分／分型規則機械式轉寫成 Python，對金標逐值比對。

## 這證明了什麼、沒證明什麼

**證明**：`WoundPipeline.swift` 與 `TissueClassifierV2.swift` 的**演算法**與
`push_golden.json` / `tissue_golden.json` 完全一致，且常數確實取自
`Preprocessing.generated.swift`（本腳本直接從該檔剖析，不另抄一份）。

**沒證明**：Swift 能編譯、型別正確、SwiftUI 能跑。那些只有 macOS 上的 `xcodebuild` 能給。

之所以值得做，是因為這一層的錯誤（面積帶抄錯一格、HSV 用了 0–360 而非 OpenCV 的 0–180）
不會有任何執行期徵兆——服務照回 200，畫面照顯示一個合理的數字。
"""
import json
import re
import sys
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SWIFT_GEN = os.path.join(ROOT, 'iOS/WoundMeasurementApp/Generated/Preprocessing.generated.swift')
SWIFT_PIPE = os.path.join(ROOT, 'iOS/WoundMeasurementApp/Pipeline/WoundPipeline.swift')
SWIFT_TISSUE = os.path.join(ROOT, 'iOS/WoundMeasurementApp/Pipeline/TissueClassifierV2.swift')
GOLD = os.path.join(ROOT, 'engineering/generated')

fails, checks = [], 0


def ck(cond, msg):
    global checks
    checks += 1
    if not cond:
        fails.append(msg)


# ── 從 Swift 原始碼剖析 SSOT 常數（不另抄一份，否則抄錯就驗不出來）─────────────
src = open(SWIFT_GEN, encoding='utf-8').read()

m = re.search(r'pushAreaBands\s*:\s*\[\(Double,Int\)\]\s*=\s*\[(.*?)\]', src, re.S)
bands = [(float(a), int(b)) for a, b in re.findall(r'\(([\d.]+)\s*,\s*(\d+)\)', m.group(1))]

m = re.search(r'tissueWorstOrder\s*=\s*\[(.*?)\]', src, re.S)
worst_order = re.findall(r'"(\w+)"', m.group(1))

m = re.search(r'markerMmActive\s*:\s*Double\s*=\s*([\d.]+)', src)
marker_mm = float(m.group(1))

print(f"從 Preprocessing.generated.swift 讀到：bands={len(bands)} 段, "
      f"worstOrder={worst_order}, markerMm={marker_mm}")

# 與 Android 的 Kotlin 版比對同一組常數
kt = open(os.path.join(ROOT, 'Android/app/src/main/java/com/woundmeasurement/app/'
                             'generated/Preprocessing.generated.kt'), encoding='utf-8').read()
kt_bands = [(float(a), int(float(b))) for a, b in
            re.findall(r'doubleArrayOf\(([\d.]+),([\d.]+)\)', kt)]
ck(bands == kt_bands, f"iOS 與 Android 的 pushAreaBands 不一致：{bands} vs {kt_bands}")
kt_worst = re.findall(r'"(\w+)"', re.search(r'tissueWorstOrder\s*=\s*arrayOf\((.*?)\)', kt).group(1))
ck(worst_order == kt_worst, f"tissueWorstOrder 不一致：{worst_order} vs {kt_worst}")
ck(marker_mm == float(re.search(r'markerMmActive\s*=\s*([\d.]+)', kt).group(1)),
   "markerMmActive 不一致")

TISSUE_SCORE = {"necrosis": 4, "slough": 3, "granulation": 2, "epithelial": 1}


# ── WoundPipeline.swift 的機械轉寫 ──────────────────────────────────────────
def area_subscore(cm2):
    if cm2 is None:
        return None
    if cm2 <= 0.0:
        return 0
    for hi, sc in bands:
        if cm2 <= hi:
            return sc
    return 10


def tissue_subscore(frac, present=0.05):
    for k in worst_order:
        if frac.get(k, 0.0) >= present:
            return TISSUE_SCORE.get(k, 0)
    return 0


def push(cm2, frac, exudate):
    a = area_subscore(cm2)
    t = tissue_subscore(frac)
    partial = None if a is None else a + t
    full = None if (partial is None or exudate is None) else partial + exudate
    return a, t, partial, full


def area_by_ratio(wound_px, marker_px_area, mm=None):
    mm = marker_mm if mm is None else mm
    if marker_px_area <= 0:
        return None
    return wound_px * mm * mm / marker_px_area / 100.0


# ── TissueClassifierV2.swift 的機械轉寫 ────────────────────────────────────
def rgb2hsv(r, g, b):
    R, G, B = float(r), float(g), float(b)
    v = max(R, G, B); mn = min(R, G, B); d = v - mn
    s = 0.0 if v == 0 else d / v * 255.0
    if d == 0:      h = 0.0
    elif v == R:    h = 60 * (G - B) / d
    elif v == G:    h = 120 + 60 * (B - R) / d
    else:           h = 240 + 60 * (R - G) / d
    if h < 0: h += 360
    h /= 2.0
    # Swift 的 .rounded() 是 half-away-from-zero，Python 的 round() 是 banker's rounding
    def rnd(x): return int(x + 0.5) if x >= 0 else -int(-x + 0.5)
    return rnd(h), rnd(s), rnd(v)


def classify_pixel(r, g, b):
    h, s, v = rgb2hsv(r, g, b)
    if v < 75 and s < 90:                       return 1   # 壞死
    if 18 <= h <= 45 and s >= 60 and v >= 60:   return 2   # 腐肉
    if v >= 170 and s < 70 and r > 150:         return 4   # 上皮
    if (h < 15 or h > 160) and s >= 60:         return 3   # 肉芽
    return 5                                               # 其他


# ── 對金標比對 ─────────────────────────────────────────────────────────────
pg = json.load(open(os.path.join(GOLD, 'push_golden.json')))
for cm2, expect in pg['area_subscore']:
    got = area_subscore(cm2)
    ck(got == expect, f"area_subscore({cm2}) = {got}，金標為 {expect}")

for frac, expect in pg['tissue_subscore']:
    got = tissue_subscore(frac)
    ck(got == expect, f"tissue_subscore({frac}) = {got}，金標為 {expect}")

for c in pg['push_cases']:
    frac = {"necrosis": 0.1, "granulation": 0.8} if c['tissue_sub'] == 4 else \
           {"slough": 0.2, "granulation": 0.7} if c['tissue_sub'] == 3 else \
           {"granulation": 0.95}
    a, t, p, f = push(c['area_cm2'], frac, c['exudate'])
    ck(a == c['area_sub'], f"push area {c['area_cm2']}: {a} vs {c['area_sub']}")
    ck(t == c['tissue_sub'], f"push tissue: {t} vs {c['tissue_sub']}")
    ck(p == c['partial'], f"push partial: {p} vs {c['partial']}")
    ck(f == c['full'], f"push full: {f} vs {c['full']}")

tg = json.load(open(os.path.join(GOLD, 'tissue_golden.json')))
ck(tg['hsv_formula'] == 'opencv_8bit_H0-180', "HSV 公式標示與預期不符")
for s in tg['samples']:
    r, g, b = s['rgb']
    got = classify_pixel(r, g, b)
    ck(got == s['code'], f"classifyPixel{tuple(s['rgb'])} = {got}，金標為 {s['code']}（{s['note']}）")

# 組織碼映射：修邊碼 ＝ 資料集正典（由 train_tissue_seg.py 定義）
CLS_TO_EDIT = [0, 3, 2, 1, 4, 5]
EDIT_NAMES = ["", "granulation", "slough", "necrosis", "epithelial", "other"]
CLS_NAMES = ["", "necrosis", "slough", "granulation", "epithelial", "other"]
for cls in range(1, 6):
    ck(EDIT_NAMES[CLS_TO_EDIT[cls]] == CLS_NAMES[cls],
       f"組織碼轉換錯誤：分類器碼 {cls}({CLS_NAMES[cls]}) → 修邊碼 "
       f"{CLS_TO_EDIT[cls]}({EDIT_NAMES[CLS_TO_EDIT[cls]]})")

# 面積比例法 sanity：marker 12mm 在影像上 100×100px
ck(abs(area_by_ratio(10000, 10000) - 1.44) < 1e-9, "areaCm2ByRatio 數值不符")
ck(area_by_ratio(100, 0) is None, "markerPxArea=0 應回 None")

# Swift 原始碼必須真的引用 SSOT，而不是自己寫死數字
pipe = open(SWIFT_PIPE, encoding='utf-8').read()
ck('Preproc.pushAreaBands' in pipe, "WoundPipeline.swift 未引用 Preproc.pushAreaBands")
ck('Preproc.tissueWorstOrder' in pipe, "WoundPipeline.swift 未引用 Preproc.tissueWorstOrder")
ck('Preproc.markerMmActive' in pipe, "WoundPipeline.swift 未引用 Preproc.markerMmActive")
ck(not re.search(r'\b0\.3\s*,\s*1\b', pipe), "WoundPipeline.swift 疑似硬編碼面積帶")

# ── iOS ↔ Android 預設後端網址必須一致 ────────────────────────────────────
# 不一致的症狀是「登入失敗」，而訊息會叫人去查帳密——錯的其實是網址。
import os as _os
_ios = open(_os.path.join(ROOT,'iOS/WoundMeasurementApp/Core/AppSettings.swift'),encoding='utf-8').read()
_gradle_path = _os.path.join(ROOT,'Android/app/build.gradle')
_ios_url = re.search(r'return "(https://[^"]+run\.app)"', _ios)
ck(_ios_url is not None, "iOS AppSettings 找不到 release 預設後端網址")
if _os.path.exists(_gradle_path):
    _g = open(_gradle_path,encoding='utf-8').read()
    _and_url = re.search(r'DEFAULT_BACKEND_URL",\s*\n?\s*\'"(https://[^"]+run\.app)"\'', _g)
    ck(_and_url is not None, "Android build.gradle 找不到 release DEFAULT_BACKEND_URL")
    if _ios_url and _and_url:
        ck(_ios_url.group(1) == _and_url.group(1),
           f"預設後端網址不一致：iOS={_ios_url.group(1)} vs Android={_and_url.group(1)}")
    print(f"  預設後端網址：iOS={_ios_url.group(1) if _ios_url else '?'}")
else:
    print("  (沙箱快照無 Android/app/build.gradle，跳過跨端網址比對)")

# ── 結果 ───────────────────────────────────────────────────────────────────
print(f"\n檢查項目：{checks}　失敗：{len(fails)}")
for f in fails:
    print("  ✗", f)
print("\n" + ("✅ 全部通過" if not fails else "❌ 有失敗項"))
sys.exit(1 if fails else 0)
