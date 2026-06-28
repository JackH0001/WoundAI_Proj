# -*- coding: utf-8 -*-
"""優化校正貼紙 SVG 生成(依 sticker_bundle 慣例:mm 單位、置中 viewBox)。
關鍵修正:舊 bundle SVG 的 ArUco 是『示意假棋盤』且被十字/色點覆蓋→偵測失敗。
本版用『真實可解碼 DICT_4X4_50 id=7』bit grid + 乾淨不覆蓋。含 RGBY/灰 WB 色點 + 四角 LiDAR 凸點。"""
import cv2, numpy as np
def aruco_cells(aruco_id=7):
    """回傳 6x6 bool(True=黑);DICT_4X4_50 = 4x4 資料 + 1格黑邊框。"""
    d=cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    gen=cv2.aruco.generateImageMarker if hasattr(cv2.aruco,"generateImageMarker") else cv2.aruco.drawMarker
    img=gen(d,aruco_id,6)            # 6x6 px,每格1px
    return (img<128)
def _aruco_rects(cx, cy, marker_mm, aruco_id):
    cells=aruco_cells(aruco_id); n=cells.shape[0]; s=marker_mm/n
    x0=cx-marker_mm/2; y0=cy-marker_mm/2; out=[]
    # 白底(quiet 由外層 footprint 提供;此處 marker 區白底確保對比)
    out.append(f'<rect x="{x0:.3f}" y="{y0:.3f}" width="{marker_mm:.3f}" height="{marker_mm:.3f}" fill="white"/>')
    for r in range(n):
        for c in range(n):
            if cells[r,c]:
                out.append(f'<rect x="{x0+c*s:.3f}" y="{y0+r*s:.3f}" width="{s:.3f}" height="{s:.3f}" fill="black"/>')
    return "\n".join(out)
def _ticks(half):
    L=[]
    for t in range(-int(half),int(half)+1,5):
        L.append(f'<line x1="{t}" y1="{-half-0.5:.2f}" x2="{t}" y2="{-half+0.0:.2f}" stroke="black" stroke-width="0.1"/>')
        L.append(f'<text x="{t}" y="{half+2.6:.2f}" font-size="2" text-anchor="middle" fill="black">{t}</text>')
    return "\n".join(L)
def square_svg_20mm(aruco_id=7, marker_mm=12.0):
    H=10.0
    color=[("0","-8","#FF0000"),("0","8","#00C800"),("-8","0","#0000FF"),("8","0","#FFD200")]
    dots="\n".join(f'<circle cx="{x}" cy="{y}" r="1.5" fill="{c}"/>' for x,y,c in color)
    corners="\n".join(f'<circle cx="{x}" cy="{y}" r="1.0" fill="#BEBEBE" stroke="#555" stroke-width="0.1"/>' for x in("-9","9") for y in("-9","9"))
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="24mm" height="24mm" viewBox="-12 -12 24 24">
  <!-- WoundAI 方形校正貼紙 20x20mm:真實 DICT_4X4_50 id={aruco_id}(乾淨,不被覆蓋) -->
  <rect x="-10" y="-10" width="20" height="20" fill="white" stroke="#999" stroke-width="0.15"/>
{_aruco_rects(0,0,marker_mm,aruco_id)}
  <!-- WB 四色點 R上/G下/B左/Y右 (3mm) -->
{dots}
  <!-- 四角 LiDAR 立體凸點 gray18 (2mm,實體凸起~0.5mm,深度/平面基準) -->
{corners}
  <!-- 邊緣尺標 -->
{_ticks(H)}
</svg>'''
def circle_svg_30mm(aruco_id=7, marker_mm=15.0):
    ring=11.5
    color=[("0",f"{-ring}","#FF0000"),(f"{ring}","0","#FFD200"),("0",f"{ring}","#00C800"),(f"{-ring}","0","#0000FF")]
    dots="\n".join(f'<circle cx="{x}" cy="{y}" r="2.0" fill="{c}"/>' for x,y,c in color)
    import math
    cor=[]
    for a in (45,135,225,315):
        x=ring*math.cos(math.radians(a)); y=ring*math.sin(math.radians(a))
        cor.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="1.5" fill="#BEBEBE" stroke="#555" stroke-width="0.1"/>')
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="34mm" height="34mm" viewBox="-17 -17 34 34">
  <!-- WoundAI 圓形校正貼紙 Ø30mm:真實 DICT_4X4_50 id={aruco_id}(乾淨) -->
  <circle cx="0" cy="0" r="15" fill="white" stroke="#999" stroke-width="0.2"/>
{_aruco_rects(0,0,marker_mm,aruco_id)}
{dots}
{chr(10).join(cor)}
</svg>'''
if __name__=="__main__":
    import cairosvg
    OUT="/sessions/nifty-sweet-edison/mnt/dev/WoundAI_work/out/sticker_bundle_v2"
    import os; os.makedirs(OUT,exist_ok=True)
    for name,svg in [("WoundAI_Square_20mm",square_svg_20mm()),("WoundAI_Circle_30mm",circle_svg_30mm())]:
        open(f"{OUT}/{name}.svg","w").write(svg)
        cairosvg.svg2png(bytestring=svg.encode(),write_to=f"{OUT}/{name}.png",output_width=600,output_height=600)
        # 驗證:渲染後標準偵測必須解出 id
        png=cv2.imread(f"{OUT}/{name}.png"); g=cv2.cvtColor(png,cv2.COLOR_BGR2GRAY)
        d=cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
        pr=cv2.aruco.DetectorParameters() if hasattr(cv2.aruco,"DetectorParameters") else cv2.aruco.DetectorParameters_create()
        c,ids,_=(cv2.aruco.ArucoDetector(d,pr).detectMarkers(g) if hasattr(cv2.aruco,"ArucoDetector") else cv2.aruco.detectMarkers(g,d,parameters=pr))
        print(f"{name}: SVG→PNG 偵測 id={None if ids is None else ids.flatten().tolist()}")
