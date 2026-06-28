# -*- coding: utf-8 -*-
"""驗證蒸餾學生 vs 老師(A∪U) vs 現有(smp/wsm) — Dice/大小,可在沙箱(onnxruntime)跑。
用法: STUDENT_ONNX=path/to/student_distilled.onnx WOUNDAI_ARCHIVE=... python distill_eval.py
學生規格須: 256 NCHW, ImageNet RGB(與 distill_train 一致)。"""
import os, glob, numpy as np, cv2, onnxruntime as ort
from PIL import Image
A=os.environ.get("WOUNDAI_ARCHIVE","/sessions/nifty-sweet-edison/mnt/dev/WoundAI_weights_archive")
IM=A+"/批次驗證工具/retrain_merged/image"; LB=A+"/批次驗證工具/retrain_merged/labels"
OD=A+"/onnx_export"
SMP=A+"/雲端 AI 模型訓練及分析服務/wound-segmentation-master/smp_trained.onnx"
WSM=A+"/雲端 AI 模型訓練及分析服務/wound-segmentation-master/wsm.onnx"
STU=os.environ.get("STUDENT_ONNX","")
mean=np.array([0.485,0.456,0.406],np.float32); std=np.array([0.229,0.224,0.225],np.float32)
def sig(x): return 1/(1+np.exp(-np.clip(x,-30,30)))
def sess(p): return ort.InferenceSession(p,providers=["CPUExecutionProvider"])
def run(s,img,size,layout,norm,bgr=False):
    H,W=img.shape[:2]; r=cv2.resize(img,(size,size)).astype(np.float32)
    if bgr: r=r[...,::-1]
    x=(r/127.5-1) if norm=="-11" else ((r/255-mean)/std if norm=="imagenet" else r/255.0)
    x=np.transpose(x,(2,0,1))[None] if layout=="NCHW" else x[None]
    o=np.squeeze(s.run(None,{s.get_inputs()[0].name:np.ascontiguousarray(x.astype(np.float32))})[0]).astype(np.float32)
    if o.ndim==3: o=o[0] if layout=="NCHW" else o[...,0]
    if o.min()<0 or o.max()>1: o=sig(o)
    return cv2.resize(o,(W,H))
def dice(p,g):p=p.astype(bool);g=g.astype(bool);s=p.sum()+g.sum();return 2*(p&g).sum()/s if s else 1.0
names=[n for n in sorted(os.listdir(IM))]
sm=sess(SMP); wm=sess(WSM); sa=sess(OD+"/a_unet.onnx"); suu=sess(OD+"/unetpp.onnx")
st=sess(STU) if STU and os.path.exists(STU) else None
res={k:[] for k in ["smp","wsm","A∪U老師","student"]}
for n in names:
    img=np.asarray(Image.open(os.path.join(IM,n)).convert("RGB"))
    g=np.asarray(Image.open(os.path.join(LB,n)).convert("L"))>127
    if g.sum()==0: continue
    res["smp"].append(dice(run(sm,img,256,"NCHW","imagenet")>0.3,g))
    res["wsm"].append(dice(run(wm,img,224,"NHWC","01",bgr=True)>0.5,g))
    au=0.5*run(sa,img,256,"NHWC","-11")+0.5*run(suu,img,256,"NHWC","-11"); res["A∪U老師"].append(dice(au>0.4,g))
    if st is not None: res["student"].append(dice(run(st,img,256,"NCHW","imagenet")>0.4,g))
print("模型            Dice(非空GT,均)  中位   n  大小MB")
import os.path as op
sizes={"smp":op.getsize(SMP)/1e6,"wsm":op.getsize(WSM)/1e6,"A∪U老師":(op.getsize(OD+'/a_unet.onnx')+op.getsize(OD+'/unetpp.onnx'))/1e6,"student":(op.getsize(STU)/1e6 if st is not None else 0)}
for k in ["smp","wsm","A∪U老師","student"]:
    d=res[k]
    if d: print(f"  {k:12} {np.mean(d):.3f}          {np.median(d):.3f}  {len(d)}  {sizes[k]:.1f}")
    else: print(f"  {k:12} (無 student onnx — 設 STUDENT_ONNX 後再跑)")
