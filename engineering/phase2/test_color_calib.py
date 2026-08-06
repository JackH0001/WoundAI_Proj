# -*- coding: utf-8 -*-
"""驗證：**用貼紙中性色做白平衡，能不能讓不同光源下的同一個傷口得到同一個答案**。

    python engineering/phase2/test_color_calib.py

## 這件事為什麼重要

組織分類本質上是色彩判斷（肉芽紅／腐肉黃／壞死黑／上皮淡粉）。
臨床拍攝的光源差異極大——病房日光燈約 4000K 帶綠峰、床邊鎢絲燈 2700K 嚴重偏橘、
手術燈 5000K、手機閃光燈。**偏色會直接改變組織判讀**。

更麻煩的是訓練資料會繼承這個偏差：A 病房的照片都偏橘、B 病房都偏綠，
模型學到的是**醫院**而不是組織。

## 驗證方式

用合成傷口（已知的肉芽／腐肉／壞死區塊 + 貼紙）施加**已知的光源色偏**，
校正後與原圖比對。正確的話：

  · 校正後的影像應該回到接近原圖
  · 不同光源下的**組織比例**應該一致（這才是真正要的結果）
  · gray-world 在大面積紅色傷口上應該**失敗**——證明必須用色卡而非場景統計
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

FAILED = []
TOTAL = [0]


def check(name, ok, detail=""):
    TOTAL[0] += 1
    print(("PASS  " if ok else "FAIL  ") + name + (("   " + str(detail)) if detail else ""))
    if not ok:
        FAILED.append(name)


# 常見光源相對於 D65 的 RGB 係數（近似值，用於施加已知色偏）。
# 數值方向是對的：鎢絲燈紅多藍少、日光燈偏綠、陰天偏藍。
ILLUMINANTS = {
    "D65 日光（基準）":   (1.00, 1.00, 1.00),
    "鎢絲燈 2700K":       (1.00, 0.78, 0.46),
    "日光燈 4000K 綠峰":  (0.92, 1.06, 0.88),
    "手術燈 5000K":       (0.98, 1.00, 0.95),
    "陰天 7500K":         (0.88, 0.97, 1.15),
}


# ⚠ 組織色必須用**真實照片的去飽和值**，不能用純色塊。
#
# 第一版用了 (60,60,175) 這種高飽和色塊，結果是「未校正也不影響分類」——
# 因為純色的色相對通道增益不敏感，而寬色相分箱又吃得下那點位移。
# 那個結論是假的：真實傷口照的組織色飽和度只有 0.3–0.4，
# 而肉芽與腐肉的色相只差 16°，鎢絲燈就能把它壓到 7°。
#
# 實測色相（本檔下方 A2 會再驗一次）：
#            D65   鎢絲燈  日光燈  陰天
#   肉芽       3      9     12    171(繞過紅色環)
#   腐肉      19     16     32     23
#   間距      16      7     20    148
TISSUE_BGR = {1: (95, 100, 155),    # 肉芽：牛肉紅，去飽和
              2: (120, 155, 175),   # 腐肉：黃褐
              3: (55, 52, 62)}      # 壞死：暗褐近黑
# 正確曝光的紙白約 235，不是 252。留 8% 高光餘裕——
# 沒有餘裕的話任何提亮通道的光源都會讓白參考飽和，而飽和的中性面
# 看起來完全中性，會算出「不必校正」。
PAPER_WHITE = 235
PAPER_GREY = 172


def make_scene(size=520, marker_mm=12.0, noise=3.0, seed=0, wound_frac=0.20):
    """合成場景：傷口（肉芽/腐肉/壞死）+ 貼紙。回 (img_bgr, marker_quad, mask, true_frac)。

    貼紙畫在右下，幾何與 svg_stickers.square_svg_20mm 一致：
    20mm 白底、marker 12mm 置中、灰點 ±9mm、RGBY ±8mm。
    """
    import cv2
    img = np.full((size, size, 3), (150, 170, 205), np.uint8)      # 周圍皮膚 BGR

    # ── 傷口：三塊已知面積的組織 ──
    lab = np.zeros((size, size), np.uint8)
    yy, xx = np.mgrid[0:size, 0:size]
    cx, cy, r = size * 0.38, size * 0.40, size * wound_frac
    wound = ((xx - cx) ** 2 + (yy - cy) ** 2) < r * r
    lab[wound] = 1                                                  # 肉芽
    lab[wound & (((xx - cx + r * .35) ** 2 + (yy - cy) ** 2) < (r * .45) ** 2)] = 2   # 腐肉
    lab[wound & (((xx - cx) ** 2 + (yy - cy + r * .45) ** 2) < (r * .30) ** 2)] = 3   # 壞死
    for k, c in TISSUE_BGR.items():
        img[lab == k] = c
    # 感測器雜訊。沒有它，每一類是一個完美的常數色，分類邊界永遠不會被跨過——
    # 而真實照片裡靠近邊界的那些像素正是會翻面的族群。
    if noise > 0:
        rng = np.random.default_rng(seed)
        img = np.clip(img.astype(np.float32)
                      + rng.normal(0, noise, img.shape), 0, 255).astype(np.uint8)

    # ── 貼紙 ──
    px_per_mm = size / 90.0
    sx, sy = size * 0.76, size * 0.74                               # 貼紙中心
    def P(x_mm, y_mm):
        return (int(round(sx + x_mm * px_per_mm)), int(round(sy + y_mm * px_per_mm)))
    hw = int(round(10.0 * px_per_mm))
    cv2.rectangle(img, (int(sx - hw), int(sy - hw)), (int(sx + hw), int(sy + hw)),
                  (PAPER_WHITE,) * 3, -1)                           # 正確曝光的紙白
    hm = int(round((marker_mm / 2.0) * px_per_mm))
    cv2.rectangle(img, (int(sx - hm), int(sy - hm)), (int(sx + hm), int(sy + hm)),
                  (30, 30, 30), -1)                                 # marker 以深色方塊代表
    for (gx, gy) in [(-9, -9), (9, -9), (-9, 9), (9, 9)]:
        cv2.circle(img, P(gx, gy), max(1, int(1.0 * px_per_mm)), (PAPER_GREY,) * 3, -1)
    for (dx, dy), c in [((0, -8), (0, 0, 230)), ((0, 8), (0, 200, 0)),
                        ((-8, 0), (230, 0, 0)), ((8, 0), (0, 210, 255))]:
        cv2.circle(img, P(dx, dy), max(1, int(1.5 * px_per_mm)), c, -1)

    quad = [list(P(-marker_mm / 2, -marker_mm / 2)), list(P(marker_mm / 2, -marker_mm / 2)),
            list(P(marker_mm / 2, marker_mm / 2)), list(P(-marker_mm / 2, marker_mm / 2))]
    frac = {k: float((lab == k).sum()) / max(1, int(wound.sum())) for k in (1, 2, 3)}
    return img, quad, wound, frac, lab


def apply_illuminant(img_bgr, rgb_gain, auto_exposure=True):
    """施加光源色偏。輸入是 RGB 順序的係數，影像是 BGR。

    auto_exposure 模擬相機的自動曝光：真實相機不會讓畫面最亮處爆掉。
    不模擬的話，任何提亮某通道的光源（陰天提藍、日光燈提綠）都會讓紙白的
    那個通道飽和，而**飽和的白參考是真的無法用來估光源**——
    校正模組會（正確地）拒絕，於是測試在測「相機不會做的事」。
    """
    g = np.array([rgb_gain[2], rgb_gain[1], rgb_gain[0]], dtype=np.float32)
    if auto_exposure:
        # 讓白參考最亮的通道回到原本的 PAPER_WHITE，等同相機收光圈／縮短快門
        g = g / max(float(g.max()), 1e-6)
    return np.clip(np.asarray(img_bgr, np.float32) * g[None, None, :], 0, 255).astype(np.uint8)


def patch_hue(bgr):
    """單一顏色的 OpenCV 色相（0–179）。"""
    import cv2
    return int(cv2.cvtColor(np.array([[list(bgr)]], np.uint8), cv2.COLOR_BGR2HSV)[0][0][0])


def hue_gap(h1, h2):
    """色相是環狀的，差值要走短邊。"""
    d = abs(h1 - h2)
    return min(d, 180 - d)


# 肉芽／腐肉的分界。取 D65 下兩者色相的中點——真實的分類器（不論是啟發式
# 還是訓練出來的模型）實際上就在做這件事，只是邊界是學來的。
HUE_BOUNDARY = (patch_hue(TISSUE_BGR[1]) + patch_hue(TISSUE_BGR[2])) // 2


def classify_by_hue(img_bgr, mask):
    """極簡的色彩組織分類：肉芽／腐肉以色相中點分界，壞死看明度。

    重點不是這個分類器有多好，而是**同一個分類器**在不同光源下會不會給出
    不同答案。它的邊界取自 D65 下兩類色相的中點，也就是「在基準光源下剛好
    分得開」——真實的分類器（啟發式或訓練出來的）處境相同。
    """
    import cv2
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0].astype(int), hsv[..., 1].astype(int), hsv[..., 2].astype(int)
    out = np.zeros(h.shape, np.uint8)
    # 色相環：把 >90 的視為繞過紅色端（陰天下的去飽和紅會跑到 170 附近）
    hh = np.where(h > 90, h - 180, h)
    lit = v >= 70
    out[mask & lit & (hh <= HUE_BOUNDARY)] = 1
    out[mask & lit & (hh > HUE_BOUNDARY)] = 2
    out[mask & ~lit] = 3
    n = max(1, int(mask.sum()))
    return {k: float((out == k).sum()) / n for k in (1, 2, 3)}


def frac_dist(a, b):
    """兩組組織比例的最大絕對差（百分點）。"""
    return 100.0 * max(abs(a.get(k, 0.0) - b.get(k, 0.0)) for k in (1, 2, 3))


def main():
    try:
        import cv2  # noqa: F401
    except ImportError:
        print("需要 OpenCV：pip install opencv-python")
        return 1
    import color_calib as CC

    base, quad, wound, true_frac, lab = make_scene()
    print("合成場景：肉芽 %.0f%% / 腐肉 %.0f%% / 壞死 %.0f%%（真值）\n"
          % (100 * true_frac[1], 100 * true_frac[2], 100 * true_frac[3]))

    # ── A. 基準光源下校正應接近無作用 ──────────────────────────────
    print("── A 基準光源（D65）──")
    r0 = CC.calibrate(base, quad)
    check("D65 下校正成功", r0.ok, r0.reason)
    check("D65 下偏色量接近 0", r0.cast < 0.05, "cast=%.4f" % r0.cast)
    check("D65 下增益接近 1", float(np.max(np.abs(r0.gains - 1.0))) < 0.05,
          "gains=%s" % np.round(r0.gains, 3))

    # ── A2. 光源如何壓縮組織之間的色相間距（最直接的證據）──────────
    print("\n── A2 光源對組織色相的影響 ──")
    print("%-18s %8s %8s %8s %14s" % ("光源", "肉芽", "腐肉", "上皮", "肉芽-腐肉間距"))
    epi = (150, 160, 185)
    base_gap = None
    worst_gap = 999
    for name, g in ILLUMINANTS.items():
        lit = lambda c: tuple(np.clip(np.array(c) * np.array([g[2], g[1], g[0]]), 0, 255))
        h1, h2, h3 = (patch_hue(lit(TISSUE_BGR[1])), patch_hue(lit(TISSUE_BGR[2])),
                      patch_hue(lit(epi)))
        gap = hue_gap(h1, h2)
        if base_gap is None:
            base_gap = gap
        worst_gap = min(worst_gap, gap)
        print("%-18s %8d %8d %8d %14d" % (name, h1, h2, h3, gap))
    check("光源會明顯壓縮肉芽與腐肉的色相間距", worst_gap <= base_gap * 0.6,
          "基準 %d° → 最差 %d°（壓縮 %.0f%%）"
          % (base_gap, worst_gap, 100 * (1 - worst_gap / base_gap)))

    # ── B. 各種光源：校正後應回到基準 ──────────────────────────────
    print("\n── B 施加已知光源色偏 → 校正 → 與基準比對 ──")
    print("%-20s %8s %10s %12s %12s" % ("光源", "偏色", "校正後偏色", "未校正色差", "校正後色差"))
    base_lin = np.asarray(base, np.float64)
    worst_after = 0.0
    for name, g in ILLUMINANTS.items():
        lit = apply_illuminant(base, g)
        r = CC.calibrate(lit, quad)
        if not r.ok:
            check("%s 校正成功" % name, False, r.reason)
            continue
        fixed = r.apply(lit)
        # 只比傷口內：周圍皮膚與貼紙不是我們關心的對象
        d_before = float(np.abs(np.asarray(lit, np.float64)[wound]
                                - base_lin[wound]).mean())
        d_after = float(np.abs(np.asarray(fixed, np.float64)[wound]
                               - base_lin[wound]).mean())
        r2 = CC.calibrate(fixed, quad)
        worst_after = max(worst_after, d_after)
        print("%-20s %8.3f %10.3f %12.1f %12.1f"
              % (name, r.cast, r2.cast if r2.ok else -1, d_before, d_after))
    check("校正後與基準的平均色差 ≤ 8/255", worst_after <= 8.0,
          "最差 %.1f" % worst_after)

    # ── C. 真正要的結果：組織比例跨光源一致 ────────────────────────
    print("\n── C 組織比例跨光源一致性（這才是校正的目的）──")
    ref = classify_by_hue(base, wound)
    print("%-20s %22s %22s" % ("光源", "未校正 肉芽/腐肉/壞死", "校正後 肉芽/腐肉/壞死"))
    worst_raw, worst_cal = 0.0, 0.0
    for name, g in ILLUMINANTS.items():
        lit = apply_illuminant(base, g)
        r = CC.calibrate(lit, quad)
        f_raw = classify_by_hue(lit, wound)
        f_cal = classify_by_hue(r.apply(lit) if r.ok else lit, wound)
        worst_raw = max(worst_raw, frac_dist(f_raw, ref))
        worst_cal = max(worst_cal, frac_dist(f_cal, ref))
        fmt = lambda f: "%5.1f%% /%5.1f%% /%5.1f%%" % (100 * f[1], 100 * f[2], 100 * f[3])
        print("%-20s %22s %22s" % (name, fmt(f_raw), fmt(f_cal)))
    print("  最大偏離基準：未校正 %.1f 個百分點 → 校正後 %.1f 個百分點"
          % (worst_raw, worst_cal))
    check("未校正時光源會明顯改變組織比例（證明有必要校正）", worst_raw > 5.0,
          "最差 %.1f 個百分點" % worst_raw)
    check("校正後組織比例跨光源一致（≤2 個百分點）", worst_cal <= 2.0,
          "最差 %.1f 個百分點" % worst_cal)

    # ── D. gray-world 必須失敗（證明非用色卡不可）────────────────────
    print("\n── D gray-world 在大面積紅色傷口上會失敗 ──")
    lit = apply_illuminant(base, ILLUMINANTS["鎢絲燈 2700K"])
    gw = CC.gray_world_gains(lit)
    gw_img = np.clip(np.asarray(lit, np.float32) * gw.astype(np.float32)[None, None, :],
                     0, 255).astype(np.uint8)
    d_gw = float(np.abs(np.asarray(gw_img, np.float64)[wound] - base_lin[wound]).mean())
    r = CC.calibrate(lit, quad)
    d_cc = float(np.abs(np.asarray(r.apply(lit), np.float64)[wound] - base_lin[wound]).mean())
    print("  鎢絲燈下與基準的色差：gray-world %.1f  vs  色卡校正 %.1f" % (d_gw, d_cc))
    check("色卡校正明顯優於 gray-world", d_cc < d_gw * 0.7,
          "色卡 %.1f / gray-world %.1f" % (d_cc, d_gw))
    f_gw = classify_by_hue(gw_img, wound)
    print("  gray-world 後的組織比例偏離基準 %.1f 個百分點（色卡 %.1f）"
          % (frac_dist(f_gw, ref), worst_cal))

    # ⚠ 誠實記錄一個**沒有成立**的預測。
    #
    # 原本預期「大面積紅色傷口會讓 gray-world 把組織比例算錯」。實測**不成立**：
    # 上面這個色相排序型的分類器對均勻的對角增益不敏感，gray-world 把紅色壓掉之後
    # 肉芽與腐肉一起位移，相對順序沒變，比例因此幾乎不動。
    #
    # 但這不代表 gray-world 可用。它壞在別的地方，而那兩件事下面直接量給你看：
    #   (a) 它把紅色系統性地壓掉，而且**傷口越大壓得越狠**
    #   (b) 它的增益取自場景統計，所以同一個傷口換個取景就換一組答案
    # 兩者都會污染以絕對 RGB 訓練的模型，也讓跨次追蹤失去共同基準。
    print("\n  同一傷口、同一光源（鎢絲燈），只改變構圖：")
    print("  %-10s %26s %26s" % ("傷口佔畫面", "gray-world 增益 B/G/R", "色卡增益 B/G/R"))
    GW, CCg = [], []
    for wf in (0.16, 0.30, 0.45, 0.62):
        im2, q2, w2, _, _ = make_scene(wound_frac=wf)
        l2 = apply_illuminant(im2, ILLUMINANTS["鎢絲燈 2700K"])
        g2 = CC.gray_world_gains(l2)
        r2 = CC.calibrate(l2, q2)
        if not r2.ok:
            continue
        GW.append(g2); CCg.append(r2.gains)
        f = lambda v: "%6.3f /%6.3f /%6.3f" % tuple(v)
        print("  %-10.0f%% %26s %26s" % (100 * w2.mean(), f(g2), f(r2.gains)))
    GW, CCg = np.array(GW), np.array(CCg)
    gw_spread = float((GW.std(0) / GW.mean(0)).max())
    cc_spread = float((CCg.std(0) / CCg.mean(0)).max())
    print("  跨構圖增益離散度：gray-world %.1f%%  vs  色卡 %.1f%%"
          % (100 * gw_spread, 100 * cc_spread))
    check("色卡增益不隨構圖改變（同一光源＝同一答案）", cc_spread < 0.005,
          "離散度 %.2f%%" % (100 * cc_spread))
    check("gray-world 的增益隨構圖漂移（取景方式會改變校正結果）",
          gw_spread > cc_spread * 5, "離散度 %.1f%%" % (100 * gw_spread))
    # 紅色壓抑：相對於正確答案，gray-world 的紅色增益低多少
    rel_r = float(np.median((GW[:, 2] / GW.mean(1)) / (CCg[:, 2] / CCg.mean(1))))
    worst_r = float(np.min((GW[:, 2] / GW.mean(1)) / (CCg[:, 2] / CCg.mean(1))))
    print("  gray-world 相對於正確答案的紅色增益：中位 ×%.2f，傷口最大時 ×%.2f"
          % (rel_r, worst_r))
    check("gray-world 系統性壓抑紅色，且傷口越大越嚴重（紅色正是肉芽的判準）",
          rel_r < 0.9 and worst_r < rel_r, "中位 ×%.2f → 最差 ×%.2f" % (rel_r, worst_r))

    # ── E. 守門 ────────────────────────────────────────────────────
    print("\n── E 拒絕給出不可信的校正 ──")
    r = CC.calibrate(base, None)
    check("沒有 ArUco 四角 → 不校正", not r.ok, r.reason)
    blown = np.clip(np.asarray(base, np.float32) * 3.0, 0, 255).astype(np.uint8)
    r = CC.calibrate(blown, quad)
    check("白色參考過曝 → 拒絕（過曝的中性面看起來完全中性，會算出「不必校正」）",
          not r.ok, r.reason)
    dark = (np.asarray(base, np.float32) * 0.03).astype(np.uint8)
    r = CC.calibrate(dark, quad)
    check("白色參考過暗 → 拒絕", not r.ok, r.reason)

    # 校正參數必須存得下來：影像會依保存政策清除，事後只剩這幾個數字。
    r = CC.calibrate(apply_illuminant(base, ILLUMINANTS["鎢絲燈 2700K"]), quad)
    d = r.as_dict()
    check("校正參數可序列化且含增益／曝光／偏色",
          all(k in d for k in ("gain_b", "gain_g", "gain_r", "exposure", "cast", "method")),
          sorted(d))
    check("鎢絲燈的增益方向正確（補藍抑紅）", d["gain_b"] > 1.0 > d["gain_r"],
          "B=%.3f R=%.3f" % (d["gain_b"], d["gain_r"]))

    # ── F. 端到端：真正在生產路徑上跑的那個分類器 ──────────────────
    #
    # 前面幾段用的是本檔自己的簡化分類器。這一段接的是 **wound_classifier
    # 的 tissue_proxy_v2**，也就是 app.py /api/v1/classify 實際呼叫的那一支。
    #
    # 它的白平衡有兩條路：有量到的灰塊就走 patch_wb，否則退回 gray_world_wb。
    # app.py 一直沒有傳灰塊值——所以在這次修正之前，每一張臨床影像都走 gray-world。
    print("\n── F 生產路徑（wound_classifier.tissue_proxy_v2）──")
    try:
        from wound_classifier import tissue_proxy_v2
    except Exception as e:
        print("SKIP  F（載不到 wound_classifier：%s）" % e)
        tissue_proxy_v2 = None
    if tissue_proxy_v2 is not None:
        print("  真值：肉芽 %.0f%% / 腐肉 %.0f%% / 壞死 %.0f%%"
              % (100 * true_frac[1], 100 * true_frac[2], 100 * true_frac[3]))
        print("  %-16s %26s %26s" % ("光源", "gray-world（修正前）", "色卡（修正後）"))
        gw_r, cc_r = [], []
        for name, g in ILLUMINANTS.items():
            lit = apply_illuminant(base, g)[..., ::-1].copy()   # RGB，與 app.py 一致
            rr = CC.calibrate(lit, quad, order="rgb")
            gp = rr.grey_reference() if rr.ok else None
            a = tissue_proxy_v2(lit, wound)
            b = tissue_proxy_v2(lit, wound, gray_patch_rgb=gp)
            gw_r.append(a); cc_r.append(b)
            f = lambda t: "肉芽%3.0f%% 腐肉%3.0f%% 其他%3.0f%%" % (
                100 * t["granulation"], 100 * t["slough"], 100 * t["other"])
            print("  %-16s %26s %26s" % (name, f(a), f(b)))
        err = lambda R: 100 * max(abs(x["granulation"] - true_frac[1]) for x in R)
        spread = lambda R: 100 * (max(x["granulation"] for x in R)
                                  - min(x["granulation"] for x in R))
        print("  與真值的最大偏差：gray-world %.1f pp ／ 色卡 %.1f pp"
              % (err(gw_r), err(cc_r)))
        check("gray-world 讓生產分類器嚴重低估肉芽（這是修正前的實際行為）",
              err(gw_r) > 30.0, "偏差 %.1f 個百分點" % err(gw_r))
        check("色卡校正後生產分類器接近真值", err(cc_r) < 5.0,
              "偏差 %.1f 個百分點" % err(cc_r))
        check("色卡校正後跨光源穩定", spread(cc_r) < 2.0,
              "極差 %.1f 個百分點" % spread(cc_r))
        # ⚠ 通道順序寫錯不會報錯，只會安靜地把紅藍增益對調。
        # 實測後果：偏藍光源下肉芽從 73% 變成 0%、其他 98%。
        lit = apply_illuminant(base, ILLUMINANTS["陰天 7500K"])[..., ::-1].copy()
        r_ok = CC.calibrate(lit, quad, order="rgb")
        r_bad = CC.calibrate(lit, quad, order="bgr")     # 故意用錯的順序
        t_ok = tissue_proxy_v2(lit, wound, gray_patch_rgb=r_ok.grey_reference())
        t_bad = tissue_proxy_v2(lit, wound, gray_patch_rgb=r_bad.grey_reference())
        check("通道順序用錯會被指標抓到（不是安靜地略微變差）",
              abs(t_bad["granulation"] - t_ok["granulation"]) > 0.3,
              "正確 %.0f%% vs 順序寫錯 %.0f%%"
              % (100 * t_ok["granulation"], 100 * t_bad["granulation"]))

    print("\n%d 項檢查，%d 項失敗" % (TOTAL[0], len(FAILED)))
    if FAILED:
        print("失敗：")
        for f in FAILED:
            print("  · " + f)
        return 1
    print("全部通過：色卡白平衡讓組織比例從跨光源差 %.1f 個百分點降到 %.1f 個百分點；"
          % (worst_raw, worst_cal))
    print("gray-world 在大面積紅色傷口上確實較差，不可用於臨床影像。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
