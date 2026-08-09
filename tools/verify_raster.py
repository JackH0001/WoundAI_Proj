#!/usr/bin/env python3
"""
修邊柵格運算的跨語言驗證：把 `RasterOps.swift` 機械式轉寫成 Python，驗證不變量。

這些運算沒有現成金標可比對（不像 PUSH 有 push_golden.json），所以改用**不變量**驗證：
面積守恆、往返有損程度、筆刷可逆性、組織碼不被污染。每一條都對應一個「畫面看起來
正常但資料是錯的」的失敗形狀。
"""
import json
import math
import sys

fails, checks = [], 0


def ck(cond, msg):
    global checks
    checks += 1
    if not cond:
        fails.append(msg)


# ── TissueSeg.rasterizePolygon 的轉寫 ──────────────────────────────────────
def rasterize_polygon(polygon, x0, y0, bw, bh, gw, gh):
    inside = [0] * (gw * gh)
    if bw <= 0 or bh <= 0 or gw <= 0 or gh <= 0 or len(polygon) < 3:
        return inside
    sx, sy = gw / bw, gh / bh
    poly = [((p[0] - x0) * sx, (p[1] - y0) * sy) for p in polygon]
    for y in range(gh):
        yc = y + 0.5
        xs = []
        j = len(poly) - 1
        for i in range(len(poly)):
            a, b = poly[i], poly[j]
            if (a[1] > yc) != (b[1] > yc):
                xs.append(a[0] + (yc - a[1]) / (b[1] - a[1]) * (b[0] - a[0]))
            j = i
        xs.sort()
        k = 0
        while k + 1 < len(xs):
            xa = max(0, round(xs[k])); xb = min(gw - 1, round(xs[k + 1]))
            for x in range(xa, xb + 1):
                inside[y * gw + x] = 1
            k += 2
    return inside


# ── RasterOps.traceBoundary 的轉寫（最大連通元件 + Moore 追蹤）─────────────
def trace_boundary(mask, mw, mh):
    if mw < 2 or mh < 2:
        return []
    label = [0] * (mw * mh)
    lbl = 0; best_lbl = 0; best_cnt = 0
    for s in range(mw * mh):
        if mask[s] == 0 or label[s] != 0:
            continue
        lbl += 1; label[s] = lbl
        stack = [s]; cnt = 0
        while stack:
            p = stack.pop(); cnt += 1
            px, py = p % mw, p // mw
            for np_, ok in ((p - 1, px > 0), (p + 1, px < mw - 1),
                            (p - mw, py > 0), (p + mw, py < mh - 1)):
                if ok and mask[np_] != 0 and label[np_] == 0:
                    label[np_] = lbl; stack.append(np_)
        if cnt > best_cnt:
            best_cnt, best_lbl = cnt, lbl
    if best_lbl == 0:
        return []

    def on(x, y):
        return 0 <= x < mw and 0 <= y < mh and label[y * mw + x] == best_lbl

    sx = sy = -1
    for y in range(mh):
        for x in range(mw):
            if on(x, y):
                sx, sy = x, y; break
        if sx >= 0:
            break
    dirs = [(0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1)]
    pts = []
    cx, cy, d = sx, sy, 6
    cap = 4 * (mw + mh) * 4
    steps = 0
    while True:
        pts.append((float(cx), float(cy)))
        found = False
        for i in range(8):
            nd = (d + i) % 8
            nx, ny = cx + dirs[nd][0], cy + dirs[nd][1]
            if on(nx, ny):
                cx, cy, d = nx, ny, (nd + 6) % 8
                found = True; break
        if not found:
            break
        steps += 1
        if (cx == sx and cy == sy) or steps >= cap:
            break
    return pts


def rdp(pts, eps):
    if len(pts) < 8:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        ax, ay = pts[a]
        dx, dy = pts[b][0] - ax, pts[b][1] - ay
        ln = max(1e-6, math.hypot(dx, dy))
        maxd, idx = 0.0, -1
        for i in range(a + 1, b):
            dist = abs((pts[i][0] - ax) * dy - (pts[i][1] - ay) * dx) / ln
            if dist > maxd:
                maxd, idx = dist, i
        if maxd > eps and idx > 0:
            keep[idx] = True
            stack.append((a, idx)); stack.append((idx, b))
    return [p for i, p in enumerate(pts) if keep[i]]


def paint(mask, tissue, mw, mh, cx, cy, radius, code, erase, auto):
    """回傳 (maskPx 變化, tissueEditedPx 變化)。"""
    x0 = max(0, math.floor(cx - radius)); x1 = min(mw - 1, math.ceil(cx + radius))
    y0 = max(0, math.floor(cy - radius)); y1 = min(mh - 1, math.ceil(cy + radius))
    dmask = dedit = 0
    r2 = radius * radius
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            if (x - cx) ** 2 + (y - cy) ** 2 > r2:
                continue
            i = y * mw + x
            if erase:
                if mask[i] != 0:
                    mask[i] = 0; dmask -= 1
                continue
            if mask[i] == 0:
                mask[i] = 1; dmask += 1
            if code is not None:
                if tissue[i] != code:
                    sug = auto[i] if auto and i < len(auto) else None
                    was = (tissue[i] != sug) if sug is not None else False
                    will = (code != sug) if sug is not None else True
                    if not was and will:  dedit += 1
                    if was and not will:  dedit -= 1
                    tissue[i] = code
            elif tissue[i] == 0 and auto and i < len(auto):
                tissue[i] = auto[i]
    return dmask, dedit


# ══ 驗證 ══════════════════════════════════════════════════════════════════

# 1. 掃描線填充：40×40 正方形在 100×100 網格上應得約 1600 px
sq = [[30, 30], [70, 30], [70, 70], [30, 70]]
m = rasterize_polygon(sq, 0, 0, 100, 100, 100, 100)
n = sum(m)
ck(abs(n - 1600) <= 90, f"正方形柵格化像素數 {n}，期望 ~1600（±90）")

# 2. 面積換算：mm_per_px=0.2、mScale=1 → 每 px 0.0004 cm²
cm2_per_px = (0.2 / 1.0) ** 2 / 100.0
area = n * cm2_per_px
ck(abs(area - 0.64) < 0.05, f"面積 {area:.4f} cm²，期望 ~0.64")

# 3. 邊界追蹤：矩形的外緣點數應約等於周長
pts = trace_boundary(m, 100, 100)
ck(len(pts) > 100, f"邊界點數 {len(pts)} 過少，追蹤可能提早中斷")
xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
ck(min(xs) >= 29 and max(xs) <= 71 and min(ys) >= 29 and max(ys) <= 71,
   f"邊界超出原多邊形範圍 x[{min(xs)},{max(xs)}] y[{min(ys)},{max(ys)}]")

# 4. RDP：矩形應被簡化到極少的點（四個角 + 起點）
simp = rdp(pts, 1.5)
ck(len(simp) <= 12, f"RDP 後 {len(simp)} 點，矩形應簡化到 ≤12 點")
ck(len(simp) >= 4, f"RDP 後只剩 {len(simp)} 點，過度簡化")

# 5. **往返有損**：這正是「續編必須原樣載回柵格」的理由
m2 = rasterize_polygon([[int(round(p[0])), int(round(p[1]))] for p in simp],
                       0, 0, 100, 100, 100, 100)
n2 = sum(m2)
drift = abs(n2 - n) / n * 100
ck(n2 > 0, "往返後遮罩為空")
print(f"  往返漂移：{n} → {n2} px（{drift:.2f}%）"
      f" ← 這就是為什麼 resume 必須載回柵格而不是由多邊形重建")
ck(drift < 15, f"往返漂移 {drift:.1f}% 過大，追蹤或 RDP 可能有誤")

# 6. 筆刷可逆：畫上再擦掉，maskPx 回到原值
mask = [0] * (50 * 50); tissue = [0] * (50 * 50); auto = [1] * (50 * 50)
d1, _ = paint(mask, tissue, 50, 50, 25, 25, 8, None, False, auto)
d2, _ = paint(mask, tissue, 50, 50, 25, 25, 8, None, True, auto)
ck(d1 > 0, "筆刷未畫進任何像素")
ck(d1 + d2 == 0, f"畫上 {d1} 擦掉 {d2}，應互相抵消")
ck(sum(mask) == 0, "擦除後遮罩應為空")

# 7. 擦除只清 mask 不清 tissue（與 Android 一致；上傳時遮罩外會被寫成 0）
ck(any(t != 0 for t in tissue), "擦除不應清掉 tissue 陣列")

# 8. 邊界筆刷畫進來的新像素自動帶上分類器建議，而不是某個預設類別
#    （舊 Android 把整片填成「比例最高的那一類」，送出的 GT 變成「整個傷口都是肉芽」）
mask2 = [0] * (50 * 50); tissue2 = [0] * (50 * 50)
auto2 = [3] * (50 * 50)     # 分類器說這一片是壞死（修邊碼 3）
paint(mask2, tissue2, 50, 50, 25, 25, 6, None, False, auto2)
painted = [tissue2[i] for i in range(len(mask2)) if mask2[i] != 0]
ck(painted and all(t == 3 for t in painted),
   "邊界筆刷的新像素未帶上分類器建議")

# 9. tissueEditedPx 只計「與分類器建議不同」的像素
mask3 = [1] * 100; tissue3 = [2] * 100; auto3 = [2] * 100
_, e_same = paint(mask3, tissue3, 10, 10, 5, 5, 3, 2, False, auto3)
ck(e_same == 0, f"塗成與建議相同的類別不應計為修正（得 {e_same}）")
_, e_diff = paint(mask3, tissue3, 10, 10, 5, 5, 3, 4, False, auto3)
ck(e_diff > 0, "塗成與建議不同的類別應計為修正")

# 10. 組織比例只算遮罩內
mask4 = [1, 1, 0, 0]; tissue4 = [1, 3, 5, 5]
cnt = {}
tot = 0
names = ["", "granulation", "slough", "necrosis", "epithelial", "other"]
for i in range(4):
    if mask4[i]:
        cnt[tissue4[i]] = cnt.get(tissue4[i], 0) + 1
        tot += 1
frac = {names[c]: cnt.get(c, 0) / tot for c in range(1, 6)}
ck(abs(frac["granulation"] - 0.5) < 1e-9 and abs(frac["necrosis"] - 0.5) < 1e-9,
   f"遮罩內組織比例錯誤：{frac}")
ck(abs(frac["other"]) < 1e-9, "遮罩外的像素不該計入組織比例")

# 11. correction_iou：完全未改動 = 1.0
a = [1, 1, 1, 0]; b = [1, 1, 1, 0]
inter = sum(1 for i in range(4) if a[i] and b[i])
uni = sum(1 for i in range(4) if a[i] or b[i])
ck(abs(inter / uni - 1.0) < 1e-9, "未改動時 correction_iou 應為 1.0")

# 12. Swift 原始碼守門：關鍵不變量必須寫在程式碼裡而不是只寫在文件
ro = open('/tmp/wa/iOS/WoundMeasurementApp/Core/RasterOps.swift', encoding='utf-8').read()
ck('clsToEdit[TissueClassifierV2.classifyPixel' in ro,
   "RasterOps 未把分類器碼轉成修邊碼（會讓肉芽與壞死互換）")
ck('min(1.0, Double(maxRasterDim)' in ro, "柵格解析度未限制在不放大")
ev = open('/tmp/wa/iOS/WoundMeasurementApp/UI/WoundEditView.swift', encoding='utf-8').read()
ck('premul' in ev, "疊圖未做 alpha 預乘（顏色會整批偏亮）")
ck(ev.count('tissueAlpha') >= 2 and ev.count('alpha: tissueAlpha') == 5,
   "五個組織顏色的 alpha 必須一致，否則面積比較會被透明度誤導")
mf = open('/tmp/wa/iOS/WoundMeasurementApp/UI/MeasureFlowView.swift', encoding='utf-8').read()
ck('doctorVerified = true' in mf and mf.count('doctorVerified = true') == 1,
   "doctorVerified 只能有一個設為 true 的地方（修邊完成）")
ck('activeConsent' in mf, "送出前未重讀同意真值")

print(f"\n檢查項目：{checks}　失敗：{len(fails)}")
for f in fails:
    print("  ✗", f)
print("\n" + ("✅ 全部通過" if not fails else "❌ 有失敗項"))
sys.exit(1 if fails else 0)
