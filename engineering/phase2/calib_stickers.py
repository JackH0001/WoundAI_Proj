# -*- coding: utf-8 -*-
"""優化校正貼紙(乾淨 ArUco 不被覆蓋;含 RGBY/灰 WB 色塊 + LiDAR 立體凸點)。
兩款:方形 20x20mm、圓形 Ø30mm。皆 DICT_4X4_50 id=7,角點供面積比例法。
凸點(raised_dots):物理上凸起(預設高 0.5mm)的灰點,提供 LiDAR 平面擬合+深度尺度基準;
2D 列印呈灰圓,座標(mm,相對貼紙中心)寫入 metadata 供 3D 對位。"""
import cv2, numpy as np
_DICT = cv2.aruco.DICT_4X4_50
RGBY = {"R":(255,0,0),"G":(0,160,0),"B":(0,0,255),"Y":(255,210,0)}
GRAY18 = (189,189,189)
def _marker(aruco_id, side_px):
    d=cv2.aruco.getPredefinedDictionary(_DICT)
    gen=cv2.aruco.generateImageMarker if hasattr(cv2.aruco,"generateImageMarker") else cv2.aruco.drawMarker
    return cv2.cvtColor(gen(d,aruco_id,side_px),cv2.COLOR_GRAY2RGB)
def _disc(img,cx,cy,r,color):
    cv2.circle(img,(int(round(cx)),int(round(cy))),int(round(r)),color,-1,lineType=cv2.LINE_AA)

def gen_square_sticker_20mm(aruco_id=7, ppmm=20.0, marker_mm=12.0, quiet_mm=1.4,
                            color_dot_mm=2.0, raised_dot_mm=2.6, raised_height_mm=0.5):
    """方形 20x20mm:中央乾淨 marker(12mm)+1.4mm quiet;四角灰凸點(LiDAR+灰基準);
    四邊中點 RGBY 色點(WB)。回傳 (img_rgb, meta)。"""
    F=20.0; S=int(round(F*ppmm)); img=np.full((S,S,3),255,np.uint8)
    mpx=int(round(marker_mm*ppmm)); off=int(round((F-marker_mm)/2*ppmm))
    img[off:off+mpx, off:off+mpx]=_marker(aruco_id,mpx)
    c=S/2.0; rdot=raised_dot_mm/2*ppmm; cdot=color_dot_mm/2*ppmm
    corner=(F/2-1.6)*ppmm   # 角凸點離中心
    raised=[]
    for sx in (-1,1):
        for sy in (-1,1):
            x=c+sx*corner; y=c+sy*corner; _disc(img,x,y,rdot,GRAY18); raised.append((round((x-c)/ppmm,2),round((y-c)/ppmm,2)))
    edge=(F/2-1.1)*ppmm
    _disc(img,c,c-edge,cdot,RGBY["R"]); _disc(img,c,c+edge,cdot,RGBY["G"])
    _disc(img,c-edge,c,cdot,RGBY["B"]); _disc(img,c+edge,c,cdot,RGBY["Y"])
    meta={"shape":"square","footprint_mm":F,"aruco_dict":"DICT_4X4_50","aruco_id":aruco_id,
          "marker_mm":marker_mm,"quiet_mm":quiet_mm,"color_dots_mm":color_dot_mm,
          "raised_dots_mm":raised_dot_mm,"raised_height_mm":raised_height_mm,
          "raised_dot_centers_mm":raised,"wb_dots":"R上/G下/B左/Y右,角=gray18凸點"}
    return img, meta

def gen_circle_sticker_30mm(aruco_id=7, ppmm=20.0, marker_mm=15.0, quiet_mm=2.0,
                            color_dot_mm=3.0, raised_dot_mm=3.0, raised_height_mm=0.5):
    """圓形 Ø30mm:中央乾淨 marker(15mm)+2mm quiet;環上 N/E/S/W=RGBY 色點,
    四對角=灰凸點(LiDAR)。回傳 (img_rgb, meta)。"""
    F=30.0; S=int(round(F*ppmm)); img=np.full((S,S,3),255,np.uint8)
    c=S/2.0
    mpx=int(round(marker_mm*ppmm)); o=int(round((S-mpx)/2))
    img[o:o+mpx,o:o+mpx]=_marker(aruco_id,mpx)
    ring=(F/2-2.5)*ppmm; cdot=color_dot_mm/2*ppmm; rdot=raised_dot_mm/2*ppmm
    # RGBY 在 N/E/S/W
    _disc(img,c,c-ring,cdot,RGBY["R"]); _disc(img,c+ring,c,cdot,RGBY["Y"])
    _disc(img,c,c+ring,cdot,RGBY["G"]); _disc(img,c-ring,c,cdot,RGBY["B"])
    raised=[]
    for ang in (45,135,225,315):
        a=np.deg2rad(ang); x=c+ring*np.cos(a); y=c+ring*np.sin(a)
        _disc(img,x,y,rdot,GRAY18); raised.append((round((x-c)/ppmm,2),round((y-c)/ppmm,2)))
    # 圓形外緣裁白(畫圓邊界)
    mask=np.zeros((S,S),np.uint8); cv2.circle(mask,(int(c),int(c)),int(c)-1,255,-1,cv2.LINE_AA)
    img[mask==0]=255
    cv2.circle(img,(int(c),int(c)),int(c)-1,(120,120,120),max(1,int(0.3*ppmm)),cv2.LINE_AA)
    meta={"shape":"circle","footprint_mm":F,"aruco_dict":"DICT_4X4_50","aruco_id":aruco_id,
          "marker_mm":marker_mm,"quiet_mm":quiet_mm,"color_dots_mm":color_dot_mm,
          "raised_dots_mm":raised_dot_mm,"raised_height_mm":raised_height_mm,
          "raised_dot_centers_mm":raised,"wb_dots":"R北/Y東/G南/B西,對角=gray18凸點"}
    return img, meta

if __name__=="__main__":
    from PIL import Image; import json
    OUT="/sessions/nifty-sweet-edison/mnt/dev/WoundAI_work/out"
    for name,fn in [("方形20mm",gen_square_sticker_20mm),("圓形30mm",gen_circle_sticker_30mm)]:
        img,meta=fn(ppmm=24.0)
        Image.fromarray(img).save(f"{OUT}/校正貼紙_{name}_列印版.png")
        # 自檢:乾淨 ArUco 必須仍能解出 id
        g=cv2.cvtColor(img,cv2.COLOR_RGB2GRAY); d=cv2.aruco.getPredefinedDictionary(_DICT)
        pr=cv2.aruco.DetectorParameters() if hasattr(cv2.aruco,"DetectorParameters") else cv2.aruco.DetectorParameters_create()
        c,ids,_=(cv2.aruco.ArucoDetector(d,pr).detectMarkers(g) if hasattr(cv2.aruco,"ArucoDetector") else cv2.aruco.detectMarkers(g,d,parameters=pr))
        print(f"{name}: {img.shape[1]}x{img.shape[0]}px 自檢 id={None if ids is None else ids.flatten().tolist()} 凸點{meta['raised_dot_centers_mm']}")
        json.dump(meta,open(f"{OUT}/校正貼紙_{name}_規格.json","w"),ensure_ascii=False,indent=2,default=float)
