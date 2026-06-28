"""ArUco 校正貼紙：穩健四角＋ID → homography(image→metric) 透視校正面積。
較棋盤角點穩健（detectMarkers 回傳一致角序 TL,TR,BR,BL）。marker_mm=標記實際邊長(mm)。"""
import numpy as np
import cv2
_DICT = cv2.aruco.DICT_4X4_50
def _detector():
    d = cv2.aruco.getPredefinedDictionary(_DICT)
    try:
        return ("new", cv2.aruco.ArucoDetector(d, cv2.aruco.DetectorParameters()))
    except AttributeError:
        return ("old", d)
def detect_marker(image_rgb):
    """回傳 (corners 4x2 影像座標 TL,TR,BR,BL, id) 或 None。"""
    gray = cv2.cvtColor(np.asarray(image_rgb), cv2.COLOR_RGB2GRAY)
    mode, det = _detector()
    if mode == "new":
        corners, ids, _ = det.detectMarkers(gray)
    else:
        corners, ids, _ = cv2.aruco.detectMarkers(gray, det, parameters=cv2.aruco.DetectorParameters_create())
    if ids is None or len(ids) == 0: return None
    return corners[0].reshape(4, 2).astype(np.float32), int(ids[0])
def homography_image_to_metric(corners, marker_mm=18.0, out_ppmm=10.0):
    s = marker_mm * out_ppmm
    dst = np.array([[0, 0], [s, 0], [s, s], [0, s]], np.float32)   # 對應 TL,TR,BR,BL
    return cv2.getPerspectiveTransform(corners.astype(np.float32), dst), out_ppmm
def measure_area_cm2(mask, corners, marker_mm=18.0, out_ppmm=10.0):
    """透視校正面積：傷口輪廓經 H 轉到 metric 平面 → 多邊形面積。"""
    H, p = homography_image_to_metric(corners, marker_mm, out_ppmm)
    cnts, _ = cv2.findContours(np.asarray(mask, np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts: return 0.0
    big = max(cnts, key=cv2.contourArea).reshape(1, -1, 2).astype(np.float32)
    metric = cv2.perspectiveTransform(big, H)[0]
    return float(abs(cv2.contourArea(metric.astype(np.float32)))) / (out_ppmm ** 2) / 100.0

def measure_area_cm2_ratio(mask, corners, marker_mm=20.0):
    """穩健面積（建議預設）：wound_px / marker_px面積 × marker_mm²。
    傷口與 marker 共面、同程度透視縮放時，比例自動抵銷傾斜（一階精確），
    遠優於單一小 marker 的 homography 外推（後者數值病態、遠距傷口會爆掉）。
    實測（印刷模擬傷口 n=30）：方形20mm 全角度 ~3.3%（30° 僅 1.3%）。"""
    m = np.asarray(mask, np.uint8)
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts: return 0.0
    wound_px = float(max(cv2.contourArea(c) for c in cnts))
    marker_px = abs(cv2.contourArea(corners.astype(np.float32)))
    if marker_px <= 0: return 0.0
    return wound_px * (marker_mm ** 2) / marker_px / 100.0

# ===== 色點貼紙偵測(WoundAI 自家貼紙:RGBY 點疊在 ArUco 上→標準解碼會失敗,改用色點幾何) =====
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
def _gray_world(img):
    im=np.asarray(img).astype(np.float32); mu=im.reshape(-1,3).mean(0)+1e-6
    return np.clip(im*(mu.mean()/mu),0,255).astype(np.uint8)
_DOT_RANGES={"R":[(0,90,60,12,255,255),(165,90,60,180,255,255)],"G":[(36,45,35,92,255,255)],
             "B":[(90,55,35,135,255,255)],"Y":[(16,70,90,36,255,255)]}
def _dot_cands(hsv,specs,W,H):
    m=np.zeros(hsv.shape[:2],np.uint8)
    for s in specs: m|=cv2.inRange(hsv,np.array(s[:3]),np.array(s[3:]))
    m=cv2.morphologyEx(m,cv2.MORPH_OPEN,np.ones((3,3),np.uint8))
    m=cv2.morphologyEx(m,cv2.MORPH_CLOSE,np.ones((3,3),np.uint8))
    cnts,_=cv2.findContours(m,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    out=[];amin=max(4,(W*H)//400000);amax=(W*H)//1200
    for c in cnts:
        a=cv2.contourArea(c);per=cv2.arcLength(c,True)
        if a<amin or a>amax or per<=0: continue
        circ=4*np.pi*a/(per*per)
        if circ<0.45: continue
        M=cv2.moments(c)
        if M["m00"]>0: out.append((M["m10"]/M["m00"],M["m01"]/M["m00"],a,circ))
    return sorted(out,key=lambda o:-o[3])
def detect_color_sticker(image_rgb, wb=True):
    """偵測 RGBY 四色點貼紙。回傳 dict{R,G,B,Y:(x,y), center, dots_px(對角平均像素長)} 或 None。
    用幾何約束(四點約等距、R在G上/B在Y左、兩對角長相近)排除傷口雜色。"""
    img=_gray_world(image_rgb) if wb else np.asarray(image_rgb)
    H,W=img.shape[:2]; hsv=cv2.cvtColor(img,cv2.COLOR_RGB2HSV)
    C={k:_dot_cands(hsv,v,W,H) for k,v in _DOT_RANGES.items()}
    if min(len(C[k]) for k in "RGBY")==0: return None
    best=None
    for r in C["R"][:12]:
     for g in C["G"][:12]:
      for b in C["B"][:12]:
       for y in C["Y"][:12]:
        P={"R":np.array(r[:2]),"G":np.array(g[:2]),"B":np.array(b[:2]),"Y":np.array(y[:2])}
        ctr=sum(P.values())/4; ds=[np.linalg.norm(P[k]-ctr) for k in "RGBY"]
        if max(ds)<2 or max(ds)/max(min(ds),1e-6)>2.1: continue
        if not (P["R"][1]<P["G"][1] and P["B"][0]<P["Y"][0]): continue
        rg=np.linalg.norm(P["R"]-P["G"]); by=np.linalg.norm(P["B"]-P["Y"])
        if max(rg,by)/max(min(rg,by),1e-6)>1.6: continue
        score=np.mean([r[3],g[3],b[3],y[3]])-0.003*abs(rg-by)
        if best is None or score>best[0]:
            best=(score,{k:(float(P[k][0]),float(P[k][1])) for k in "RGBY"},
                  (float(ctr[0]),float(ctr[1])),(rg+by)/2.0)
    if best is None: return None
    return {"R":best[1]["R"],"G":best[1]["G"],"B":best[1]["B"],"Y":best[1]["Y"],
            "center":best[2],"dots_px":best[3],"score":best[0]}
def measure_area_cm2_dots(mask, sticker, dot_span_mm=13.0):
    """以四色點對角像素長換算 px/mm 量面積。dot_span_mm=對角(R-G,B-Y)實際 mm(預設13≈marker邊;須對實體貼紙確認)。"""
    ppm = sticker["dots_px"] / float(dot_span_mm)
    if ppm<=0: return None
    return float(np.asarray(mask,bool).sum()) / (ppm*ppm) / 100.0

# ===== 乾淨 ArUco 校正貼紙生成(新方案:marker 不被覆蓋;色條獨立供白平衡) =====
def gen_clean_aruco_sticker(aruco_id=7, marker_mm=15.0, quiet_mm=3.0, ppmm=10.0,
                            color_strip=True, patch_mm=4.0):
    d=cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    mpx=int(round(marker_mm*ppmm)); qpx=int(round(quiet_mm*ppmm))
    gen=cv2.aruco.generateImageMarker if hasattr(cv2.aruco,"generateImageMarker") else cv2.aruco.drawMarker
    mk=cv2.cvtColor(gen(d,aruco_id,mpx),cv2.COLOR_GRAY2RGB)
    side=mpx+2*qpx; tile=np.full((side,side,3),255,np.uint8); tile[qpx:qpx+mpx,qpx:qpx+mpx]=mk
    if not color_strip: return tile
    pp=int(round(patch_mm*ppmm)); cols=[(255,0,0),(0,160,0),(0,0,255),(255,210,0),(189,189,189)]
    n=len(cols); gap=(side-n*pp)//(n+1); strip=np.full((pp+2*qpx,side,3),255,np.uint8)
    for i,c in enumerate(cols):
        x=gap+i*(pp+gap); strip[qpx:qpx+pp,x:x+pp]=c
    out=np.full((side+strip.shape[0]-qpx,side,3),255,np.uint8)
    out[:side]=tile; out[side-qpx:side-qpx+strip.shape[0]]=strip
    return out
