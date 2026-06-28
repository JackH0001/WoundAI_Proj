# -*- coding: utf-8 -*-
"""方案1(PUSH)+方案3(WB+飽和度組織)回歸測試。"""
import os,sys,numpy as np,cv2
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0,os.path.join(os.path.dirname(os.path.abspath(__file__)),"..","phase1"))
from wound_classifier import tissue_proxy, tissue_proxy_v2
import clinical_rules as cr

def _red_blob():
    img=np.full((200,200,3),(190,160,140),np.uint8)   # 膚色背景
    cv2.circle(img,(100,100),60,(105,22,30),-1)        # 暗紅(墨水/低光,v1會誤判壞死)
    m_=np.zeros((200,200),np.uint8); cv2.circle(m_,(100,100),60,1,-1)
    return img, m_.astype(bool)

def test_push_area_bands():
    assert [cr.push_area_subscore(x) for x in [0,0.2,0.8,2.0,10,30]]==[0,1,3,4,8,10]

def test_push_tissue_worst():
    assert cr.push_tissue_subscore({"granulation":0.9,"necrosis":0.1})==4  # 有壞死取最差
    assert cr.push_tissue_subscore({"granulation":0.9})==2

def test_push_exudate_honest():
    p=cr.push_score(2.5,{"granulation":0.9})
    assert p["total_partial_img"]==7 and p["total_full"] is None  # 2.5cm2->面積分5+組織2=7;滲液缺->不偽造完整分
    assert cr.push_score(2.5,{"granulation":0.9},exudate_level=2)["total_full"]==9

def test_v2_dark_red_is_granulation_not_necrosis():
    """方案3核心:暗紅(墨水/低光)不可誤判壞死。"""
    img,m=_red_blob()
    t1=tissue_proxy(img,m); t2=tissue_proxy_v2(img,m,wb=False)  # 隔離HSV規則(WB於真實照片另驗)
    assert t1["necrosis"]>0.5     # v1 確實誤判(回歸見證)
    assert t2["necrosis"]<0.1     # v2 修正
    assert t2["granulation"]>0.6  # v2 正確判肉芽
    assert cr.push_tissue_subscore(t2)==2

if __name__=="__main__":
    for k,fn in list(globals().items()):
        if k.startswith("test_") and callable(fn): fn(); print("PASS",k)
