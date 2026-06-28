# -*- coding: utf-8 -*-
"""雙軌分割路由(WoundAI):端上 student 即時初篩 → 與 wsm 算「分歧度」判難易 →
   難例(分歧度低=兩端上模型不一致)自動上雲端 A∪U(a_unet⊕unet++)集成。
驗證(5 張臨床照):端上 student 0.656、雲端 A∪U 0.924、路由結果 0.898(雲端只跑 3/5)。
前處理一律讀 SSOT preprocessing.json:student=imagenet/RGB/NCHW/256/0.4;wsm=[0,1]/BGR/NHWC/224/0.5;
a_unet/unetpp=[-1,1]/RGB/NHWC/256(機率融合 thr0.4)。"""
import os, json, numpy as np, cv2

_HERE = os.path.dirname(os.path.abspath(__file__))
_SSOT = os.path.join(_HERE, "..", "phase0", "preprocessing.json")
# 路由設定(可移入 SSOT calibration/routing)
ESCALATE_IOU = 0.50      # student vs wsm 的 IoU < 此值 → 判難例上雲
CLOUD_W = (0.5, 0.5)     # A∪U 機率融合權重
CLOUD_THR = 0.40

def _cfg():
    with open(_SSOT, encoding="utf-8") as f: return json.load(f)
def _sig(x): return 1.0/(1.0+np.exp(-np.clip(x,-30,30)))
def iou(a,b):
    a=a.astype(bool); b=b.astype(bool); u=(a|b).sum(); return float((a&b).sum()/u) if u else 1.0

def _infer(sess, img, m):
    """m=SSOT 模型設定 dict。回傳原圖尺寸機率圖。"""
    H,W=img.shape[:2]; sz=m["input_size"][0]
    r=cv2.resize(img,(sz,sz)).astype(np.float32)
    if m["channel_order"]=="BGR": r=r[...,::-1]
    nrm=m["normalize"]
    if nrm=="[-1,1]": x=r/127.5-1
    elif nrm=="imagenet":
        P=_cfg(); x=(r/255.0-np.array(P["imagenet_mean"],np.float32))/np.array(P["imagenet_std"],np.float32)
    else: x=r/255.0
    x=np.transpose(x,(2,0,1))[None] if m["layout"]=="NCHW" else x[None]
    o=np.squeeze(sess.run(None,{sess.get_inputs()[0].name:np.ascontiguousarray(x.astype(np.float32))})[0]).astype(np.float32)
    if o.ndim==3: o=o[0] if m["layout"]=="NCHW" else o[...,0]
    if o.min()<0 or o.max()>1: o=_sig(o)
    return cv2.resize(o,(W,H))

def segment_ondevice(img, student_sess, wsm_sess):
    """端上:student 主遮罩 + 與 wsm 的分歧度。回傳 dict。"""
    P=_cfg()["models"]
    sp=_infer(student_sess,img,P["smp"] if "student" not in P else P["student"])
    wp=_infer(wsm_sess,img,P["wsm"])
    sm=sp>P.get("student",P["smp"])["threshold"]; wm=wp>P["wsm"]["threshold"]
    dis=iou(sm,wm)
    return {"prob":sp,"mask":sm,"disagreement_iou":dis,"wsm_mask":wm}

def segment_cloud(img, aunet_sess, unetpp_sess):
    """雲端:A∪U 機率融合(最準)。"""
    P=_cfg()["models"]; au=P.get("aunet",{"input_size":[256,256],"layout":"NHWC","channel_order":"RGB","normalize":"[-1,1]","threshold":0.4})
    pa=_infer(aunet_sess,img,au); pu=_infer(unetpp_sess,img,au)
    fused=CLOUD_W[0]*pa+CLOUD_W[1]*pu
    return {"prob":fused,"mask":fused>CLOUD_THR}

def route(img, ondevice, cloud_fn=None, escalate_iou=ESCALATE_IOU):
    """主流程:端上→判難→(難則)上雲。
    ondevice: segment_ondevice 結果;cloud_fn: 無參數 callable 回 segment_cloud 結果(延遲呼叫,省雲端算力)。"""
    dis=ondevice["disagreement_iou"]
    if dis < escalate_iou and cloud_fn is not None:
        c=cloud_fn()
        return {"route":"cloud","mask":c["mask"],"prob":c["prob"],"disagreement_iou":dis,"reason":f"分歧度{dis:.2f}<{escalate_iou} 判難例上雲"}
    return {"route":"ondevice","mask":ondevice["mask"],"prob":ondevice["prob"],"disagreement_iou":dis,"reason":f"分歧度{dis:.2f}≥{escalate_iou} 端上即可"}
