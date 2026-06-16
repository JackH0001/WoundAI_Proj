"""統一量測管線 + phantom 精度基準測試。"""
import sys, numpy as np, cv2
from measure import measure_wound
import phantom_validation as pv
r=[]
def ck(n,c): r.append(bool(c)); print(("PASS " if c else "FAIL "),n)
# 貼紙 100px 方形(20mm)；傷口 50x50 → 真實 1.0 cm²
quad=[[100,100],[200,100],[200,200],[100,200]]
mask=np.zeros((400,400),bool); mask[250:300,250:300]=True
img=np.full((400,400,3),200,np.uint8)
m=measure_wound(img,mask,sticker_quad=quad)
ck("assisted quad 量測≈1.0 cm²", abs(m["area_cm2"]-1.0)<0.05 and m["perspective_corrected"])
mb=measure_wound(img,mask,assist_bbox=(100,100,200,200))
ck("assisted bbox 量測≈1.0 cm²", abs(mb["area_cm2"]-1.0)<0.05)
ck("無校正 -> found False、不偽造面積", measure_wound(img,mask)["area_cm2"] is None)
ck("分類仍附帶(組織/嚴重度)", "classification" in m and "severity" in m["classification"])
# phantom 精度基準：平面 + 傾斜
Hd=cv2.getPerspectiveTransform(np.array([[0,0],[400,0],[400,400],[0,400]],np.float32),
    np.array([[20,40],[360,5],[395,380],[30,360]],np.float32))
qd=cv2.perspectiveTransform(np.array(quad,np.float32).reshape(1,-1,2),Hd)[0].tolist()
mw=cv2.warpPerspective(mask.astype(np.uint8),Hd,(400,400),flags=cv2.INTER_NEAREST)>0
res=pv.run([{"name":"flat","image":img,"mask":mask,"true_cm2":1.0,"sticker_quad":quad},
            {"name":"tilted","image":img,"mask":mw,"true_cm2":1.0,"sticker_quad":qd}])
ck("phantom 兩筆皆量到", res["summary"]["n_measured"]==2)
ck("phantom 平均面積誤差 < 8%", res["summary"]["mean_area_err_pct"]<8.0)
ck("phantom area_error_pct 公式", abs(pv.area_error_pct(1.1,1.0)-10.0)<1e-9)
ck("決定性", measure_wound(img,mask,sticker_quad=quad)["area_cm2"]==measure_wound(img,mask,sticker_quad=quad)["area_cm2"])
# manifest 讀檔 → area_err 報告
import os as _os, csv as _csv, tempfile as _tf
import phantom_validation as pv
_d=_tf.mkdtemp()
from PIL import Image as _Img
_Img.fromarray(np.full((400,400,3),200,np.uint8)).save(_os.path.join(_d,"i0.png"))
_mm=np.zeros((400,400),np.uint8); _mm[250:300,250:300]=255; _Img.fromarray(_mm).save(_os.path.join(_d,"m0.png"))
_mani=_os.path.join(_d,"manifest.csv")
with open(_mani,"w",newline="",encoding="utf-8") as _f:
    _w=_csv.writer(_f); _w.writerow(["name","image_path","mask_path","true_cm2","sticker_x0","sticker_y0","sticker_x1","sticker_y1","sticker_mm","operator"])
    _w.writerow(["ph0","i0.png","m0.png",1.0,100,100,200,200,20,"A"])
_res=pv.run_from_manifest(_mani, out_csv=_os.path.join(_d,"rep.csv"))
ck("manifest: n_measured=1", _res["summary"]["n_measured"]==1)
ck("manifest: area_err≈0%", _res["summary"]["mean_area_err_pct"]<3.0)
ck("manifest: 報告CSV產出 + 帶meta(operator)", _os.path.exists(_res["out_csv"]) and _res["rows"][0]["operator"]=="A")

# tissue_report smoke（自足合成：紅肉芽 wound + GT）
import json as _json
_sd=_tf.mkdtemp()
_im=np.full((120,120,3),200,np.uint8); _im[40:80,40:80]=[190,55,55]   # 紅=肉芽
_Img.fromarray(_im).save(_os.path.join(_sd,"w_image.png"))
_wm=np.zeros((120,120),np.uint8); _wm[40:80,40:80]=255; _Img.fromarray(_wm).save(_os.path.join(_sd,"w_woundmask.png"))
_json.dump({"name":"w","true_cm2":1.0,"tissue_fraction_gt":{"necrosis":0.0,"slough":0.0,"granulation":1.0,"epithelial":0.0}},
           open(_os.path.join(_sd,"w_meta.json"),"w"))
_tr=pv.tissue_report(_sd, out_csv=_os.path.join(_sd,"tr.csv"))
ck("tissue_report: dominant 命中(肉芽)", _tr["rows"][0]["dom_match"] is True)
ck("tissue_report: necrosis 誤差≈0", _tr["summary"]["mean_abserr"]["necrosis"]<0.05)
ck("tissue_report: 報告CSV產出", _os.path.exists(_os.path.join(_sd,"tr.csv")))

ok=sum(r); print(f"\n{ok}/{len(r)} PASS"); sys.exit(0 if ok==len(r) else 1)
