# -*- coding: utf-8 -*-
"""合成深度圖：**已知幾何 + 可獨立計算的正確答案**。

    python depth_synth.py --out D:/depth_synth        # 產生一組樣本供人眼檢查

## 這支存在的理由

深度資料鏈有一段是硬體（ARCore Depth / ARKit sceneDepth），沒有裝置就跑不了。
但那一段之外的**每一節都可能出錯，而且錯了不會有任何徵兆**：

  · 單位：公尺當成公釐 → 面積差 10⁶ 倍（但程式跑得完，數字看起來只是「很小」）
  · 內參：fx/fy 對調、cx/cy 用了縮圖前的值 → 面積系統性偏差
  · 位元深度：存成 8-bit PNG → 深度被壓到 0–255 mm，30 cm 的攝距整片飽和
  · 座標軸：u/v 對調 → 非正方形影像才會露餡，方形測試圖完全看不出來
  · depth_scale：套了兩次或沒套 → 又是 1000 倍

這些全都可以在**沒有感測器**的情況下驗證完，而且應該現在驗——
等硬體到位再一次驗，整條鏈都是新的，出錯時無從二分查找。

## 正確答案怎麼來的

不是用三角化算一次再拿來比對自己。平面的表面積有**閉式解**：

對深度圖 Z(u,v)，曲面元素為 |∂P/∂u × ∂P/∂v| du dv。
把平面 Z = Z0 / (1 - a·s - b·t)（其中 s=(u-cx)/fx, t=(v-cy)/fy）代進去，
化簡後得每像素面積：

    dA = Z³ · sqrt(a² + b² + 1) / (Z0 · fx · fy)

正對平面（a=b=0, Z=Z0）退化成 Z0²/(fx·fy)，與直覺一致。

這條式子與 `measure3d.surface_area_cm2` 的三角化**完全獨立**——
一個是解析積分，一個是離散求和，兩者相符才代表兩邊都對。
"""
import argparse
import json
import os
import sys

import numpy as np


def intrinsics(w, h, fov_deg=60.0):
    """典型手機後鏡頭的內參。fx≠fy 是刻意的——兩者相等的話 u/v 對調的錯誤看不出來。"""
    fx = (w / 2.0) / np.tan(np.radians(fov_deg) / 2.0)
    fy = fx * 1.02                      # 刻意讓 fx≠fy
    return {"fx": float(fx), "fy": float(fy), "cx": (w - 1) / 2.0, "cy": (h - 1) / 2.0}


def _st(w, h, K):
    u, v = np.meshgrid(np.arange(w, dtype=np.float64), np.arange(h, dtype=np.float64))
    return (u - K["cx"]) / K["fx"], (v - K["cy"]) / K["fy"]


def plane(w, h, K, z0_mm=300.0, tilt_deg=0.0, tilt_axis="x"):
    """傾斜平面。回 (depth_mm, exact_area_fn)。

    tilt_deg 是平面法線與光軸的夾角。正對＝0°。
    """
    s, t = _st(w, h, K)
    g = np.tan(np.radians(tilt_deg))
    a, b = (g, 0.0) if tilt_axis == "x" else (0.0, g)
    denom = 1.0 - a * s - b * t
    Z = z0_mm / denom
    norm = np.sqrt(a * a + b * b + 1.0)

    def exact_area_mm2(mask):
        # 每像素解析面積（見檔頭推導）。與三角化完全獨立。
        dA = Z ** 3 * norm / (z0_mm * K["fx"] * K["fy"])
        return float(dA[np.asarray(mask, bool)].sum())

    return Z, exact_area_mm2


def projected_area_mm2(Z, mask, K):
    """投影面積（現行 2D 比例法的等價物）：逐像素 Z²/(fx·fy) 的和。

    ⚠ **不可以**用「遮罩內平均深度的平方 × 像素數」。斜面上深度會變化，
    而 E[Z²] > E[Z]²（Jensen），用平均值會系統性低估投影面積，
    於是「表面積／投影面積」的比值被虛報得偏高。
    第一版就是這樣算的，30° 斜面得到 1.19（正確值 1.16），
    而那個數字看起來完全合理——這正是它危險的地方。
    """
    m = np.asarray(mask, bool)
    return float((np.asarray(Z)[m] ** 2).sum()) / (K["fx"] * K["fy"])


def sphere_cap(w, h, K, z0_mm=300.0, radius_mm=80.0):
    """朝向鏡頭的球冠（模擬隆起的肉芽或身體曲面）。

    正確答案用**數值微分同一張真實深度圖**求得——仍然獨立於三角化：
    一個是逐像素求 |∂P/∂u × ∂P/∂v|，一個是把四個角連成兩個三角形。
    兩者在解析度夠時應收斂到同一個值。
    """
    s, t = _st(w, h, K)
    # 球心在 (0, 0, z0 + R)，表面 Z = z_c - sqrt(R² - X² - Y²)，X=sZ, Y=tZ
    # → 解 Z：(1+s²+t²)Z² - 2 z_c Z + (z_c² - R²) = 0
    zc = z0_mm + radius_mm
    A = 1.0 + s * s + t * t
    B = -2.0 * zc
    C = zc * zc - radius_mm * radius_mm
    disc = B * B - 4 * A * C
    Z = np.where(disc > 0, (-B - np.sqrt(np.maximum(disc, 0))) / (2 * A), 0.0)

    def exact_area_mm2(mask):
        return _numeric_area_mm2(Z, mask, K)

    return Z, exact_area_mm2


def _numeric_area_mm2(Z, mask, K):
    """逐像素 |∂P/∂u × ∂P/∂v|，用中央差分。三角化以外的第二種算法。"""
    s, t = _st(Z.shape[1], Z.shape[0], K)
    P = np.stack([s * Z, t * Z, Z], -1)
    du = np.gradient(P, axis=1)
    dv = np.gradient(P, axis=0)
    n = np.cross(du, dv)
    dA = np.linalg.norm(n, axis=-1)
    m = np.asarray(mask, bool)
    # 邊界像素的中央差分會跨出遮罩，誤差大 → 只取內部
    inner = m.copy()
    inner[0, :] = inner[-1, :] = inner[:, 0] = inner[:, -1] = False
    return float(dA[inner].sum())


def disc_mask(w, h, frac=0.35):
    """圓形「傷口」遮罩。用圓而非矩形：矩形的邊界剛好對齊像素格，
    會讓離散化誤差消失，而那不是真實情況。"""
    u, v = np.meshgrid(np.arange(w), np.arange(h))
    r = min(w, h) * frac
    return ((u - (w - 1) / 2.0) ** 2 + (v - (h - 1) / 2.0) ** 2) < r * r


def disc_mask_mm(w, h, K, z_mm, diameter_mm):
    """指定**實體直徑**的圓形遮罩。

    用實體尺寸而非像素比例，是因為球冠案例的遮罩必須落在球的可見範圍內——
    用比例的話換個 FOV 或球半徑，遮罩就跑到球外面，那裡深度是 0，
    而 0 會被當成有效值算進面積（第一版就是這樣，球冠投影面積算出 6.6 cm²）。
    """
    u, v = np.meshgrid(np.arange(w), np.arange(h))
    rx = (diameter_mm / 2.0) * K["fx"] / z_mm
    ry = (diameter_mm / 2.0) * K["fy"] / z_mm
    return (((u - K["cx"]) / rx) ** 2 + ((v - K["cy"]) / ry) ** 2) < 1.0


def encode_png16_mm(depth_mm):
    """契約格式：16-bit 灰階 PNG，值＝公釐，0 保留給無效值。

    ⚠ 回傳的是**位元組**，不是檔案。編碼與落盤要分開，
    才驗得出「App 產生的位元組」與「後端存下來的位元組」是不是同一份。
    """
    from PIL import Image
    import io
    d = np.asarray(depth_mm, dtype=np.float64)
    q = np.clip(np.rint(d), 0, 65535).astype(np.uint16)
    q[d <= 0] = 0
    buf = io.BytesIO()
    Image.fromarray(q, mode="I;16").save(buf, "PNG")
    return buf.getvalue()


def decode_png16_mm(raw):
    from PIL import Image
    import io
    im = Image.open(io.BytesIO(raw))
    if im.mode not in ("I;16", "I;16B", "I;16L", "I"):
        raise ValueError("不是 16-bit 灰階 PNG（實際 %s）——"
                         "8-bit 會把深度壓到 0–255 mm，30 cm 攝距整片飽和" % im.mode)
    return np.asarray(im).astype(np.float64)


def encode_conf_png(conf01):
    """置信度圖：8-bit，0–255。低置信度區在量測時要排除。"""
    from PIL import Image
    import io
    q = np.clip(np.rint(np.asarray(conf01) * 255.0), 0, 255).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(q, mode="L").save(buf, "PNG")
    return buf.getvalue()


CASES = {
    "plane_frontal": dict(kind="plane", z0=300.0, tilt=0.0, wound_mm=50.0,
                          why="正對平面：表面積＝投影面積。單位／內參錯誤在這裡最明顯"),
    "plane_30deg": dict(kind="plane", z0=300.0, tilt=30.0, wound_mm=50.0,
                        why="30° 斜面：2D 投影法會低估，正交近似下為 1/cos30°=1.155"),
    "plane_45deg": dict(kind="plane", z0=300.0, tilt=45.0, wound_mm=50.0,
                        why="45° 斜面：正交近似 1/cos45°=1.414。斜拍時 2D 法的誤差量級"),
    "sphere_cap": dict(kind="sphere", z0=300.0, radius=80.0, wound_mm=50.0,
                       why="球冠（R=80mm，如肢體）：曲面，2D 必低估且沒有單一修正係數"),
}

# ⚠ 「表面積／投影面積」不會剛好等於 1/cos(θ)。
#
# 1/cos(θ) 是**正交投影**下的關係；真實相機是透視投影，前縮率在視野內逐點不同，
# 所以比值會略高於 1/cos(θ)，且 FOV 越大差越多（實測 FOV 5° 時 45° 斜面為 1.4146、
# FOV 60° 時為 1.4905）。這不是誤差，是幾何本來就如此。
# 把 1/cos(θ) 當成期望值去比對，會在 FOV 大時判定「演算法錯了」而其實是對的。


def build(name, w=320, h=240, fov=60.0):
    """回 (depth_mm, mask, K, exact_mm2, meta)。"""
    c = CASES[name]
    K = intrinsics(w, h, fov)
    if c["kind"] == "plane":
        Z, exact = plane(w, h, K, c["z0"], c["tilt"])
    else:
        Z, exact = sphere_cap(w, h, K, c["z0"], c["radius"])
    m = disc_mask_mm(w, h, K, c["z0"], c["wound_mm"])
    # 遮罩內若有無效深度，後面每一個數字都會被污染而不會有任何提示。
    if not np.all(np.asarray(Z)[m] > 0):
        raise ValueError("%s：遮罩內有無效深度（Z<=0）。傷口直徑 %.0f mm 可能超出"
                         "幾何的可見範圍——球冠的話請加大 radius 或縮小 wound_mm。"
                         % (name, c["wound_mm"]))
    return Z, m, K, exact(m), dict(c, name=name, w=w, h=h, fov=fov)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--size", type=int, default=320)
    a = ap.parse_args()

    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        print("需要 Pillow：pip install pillow")
        return 1

    os.makedirs(a.out, exist_ok=True)
    idx = []
    print("%-16s %10s %10s %10s   %s" % ("案例", "投影 cm²", "表面 cm²", "比值", "說明"))
    for name in CASES:
        Z, m, K, exact_mm2, meta = build(name, a.size, int(a.size * 0.75))
        raw = encode_png16_mm(Z)
        open(os.path.join(a.out, name + "_depth.png"), "wb").write(raw)
        open(os.path.join(a.out, name + "_conf.png"), "wb").write(
            encode_conf_png(np.where(m, 0.95, 0.4)))
        from PIL import Image
        Image.fromarray((m * 255).astype(np.uint8)).save(os.path.join(a.out, name + "_mask.png"))
        proj = projected_area_mm2(Z, m, K) / 100.0
        surf = exact_mm2 / 100.0
        print("%-16s %10.3f %10.3f %10.4f   %s"
              % (name, proj, surf, surf / proj if proj else 0, meta["why"]))
        idx.append(dict(meta, intrinsics=K, exact_surface_cm2=surf, projected_cm2=proj,
                        depth="%s_depth.png" % name, mask="%s_mask.png" % name,
                        conf="%s_conf.png" % name, depth_format="png16_mm", depth_scale=0.001))
    json.dump({"cases": idx, "note": "合成深度，用於驗證資料鏈；不是真實傷口"},
              open(os.path.join(a.out, "index.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("\n→ %s" % a.out)
    print("「比值」是表面積／投影面積，也就是 3D 相對於現行 2D 量測的**全部價值**——")
    print("斜拍與曲面時 2D 會低估這麼多。正對平面必須是 1.000（不是的話單位或內參有錯）。")
    print("斜面的比值會略高於 1/cos(θ)：那是透視投影，1/cos(θ) 只在正交近似下成立。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
