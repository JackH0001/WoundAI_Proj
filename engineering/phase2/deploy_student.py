# -*- coding: utf-8 -*-
"""本機一站式部署:student_best.pt → onnx(fp32) → fp16(無損) → 驗證 → 複製到 Backend/Flask/models/。
需求:本機已裝 torch + segmentation_models_pytorch + onnx + onnxruntime + onnxconverter_common(訓練環境即有)。
用法(在 engineering/phase2/ 下):  python deploy_student.py
選配環境變數:WOUNDAI_ARCHIVE(預設 C:/dev/WoundAI_weights_archive)、STUDENT_ENCODER(預設 mobilenet_v2)。"""
import os, shutil, numpy as np, cv2
import torch, segmentation_models_pytorch as smp, onnx, onnxruntime as ort
from onnxconverter_common import float16
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
A   = os.environ.get("WOUNDAI_ARCHIVE", "C:/dev/WoundAI_weights_archive")
ENC = os.environ.get("STUDENT_ENCODER", "mobilenet_v2")
SIZE = 256
BK  = os.path.normpath(os.path.join(HERE, "..", "..", "Backend", "Flask", "models"))
mean=np.array([0.485,0.456,0.406],np.float32); std=np.array([0.229,0.224,0.225],np.float32)
sig=lambda x:1/(1+np.exp(-np.clip(x,-30,30)))

assert os.path.exists(os.path.join(HERE,"student_best.pt")), "找不到 student_best.pt(請放 engineering/phase2/)"

# 1) 載 .pt -> 匯出 fp32 ONNX(logits 輸出,推論端需 sigmoid)
m=smp.Unet(encoder_name=ENC,encoder_weights=None,in_channels=3,classes=1,activation=None)
m.load_state_dict(torch.load(os.path.join(HERE,"student_best.pt"),map_location="cpu")); m.eval()
try:
    torch.onnx.export(m,torch.randn(1,3,SIZE,SIZE),os.path.join(HERE,"student_distilled.onnx"),
        input_names=["input"],output_names=["mask"],opset_version=13,dynamo=False,
        dynamic_axes={"input":{0:"N"},"mask":{0:"N"}})
except TypeError:
    torch.onnx.export(m,torch.randn(1,3,SIZE,SIZE),os.path.join(HERE,"student_distilled.onnx"),
        input_names=["input"],output_names=["mask"],opset_version=13,
        dynamic_axes={"input":{0:"N"},"mask":{0:"N"}})
fp32=os.path.getsize(os.path.join(HERE,"student_distilled.onnx"))/1e6

# 2) FP16(無損)
mdl=onnx.load(os.path.join(HERE,"student_distilled.onnx"))
onnx.save(float16.convert_float_to_float16(mdl,keep_io_types=True),os.path.join(HERE,"student_fp16.onnx"))
fp16=os.path.getsize(os.path.join(HERE,"student_fp16.onnx"))/1e6

# 3) 驗證 fp32 vs fp16 一致(無損)
def infer(sess,img):
    H,W=img.shape[:2]; r=cv2.resize(img,(SIZE,SIZE)).astype(np.float32)/255.0
    x=np.transpose((r-mean)/std,(2,0,1))[None].astype(np.float32)
    o=np.squeeze(sess.run(None,{sess.get_inputs()[0].name:x})[0]).astype(np.float32)
    if o.min()<0 or o.max()>1: o=sig(o)
    return cv2.resize(o,(W,H))
def dice(p,g):
    p=p>0.4;g=g>0;s=p.sum()+g.sum();return 1.0 if s==0 else 2*(p&g).sum()/s
s32=ort.InferenceSession(os.path.join(HERE,"student_distilled.onnx"),providers=["CPUExecutionProvider"])
s16=ort.InferenceSession(os.path.join(HERE,"student_fp16.onnx"),providers=["CPUExecutionProvider"])
IMG=os.path.join(A,"批次驗證工具","retrain_merged","image"); GT=os.path.join(A,"批次驗證工具","retrain_merged","labels")
d32=[];d16=[];diff=[]
if os.path.isdir(IMG):
    for n in sorted(os.listdir(IMG))[:8]:
        img=np.asarray(Image.open(os.path.join(IMG,n)).convert("RGB"))
        g=(np.asarray(Image.open(os.path.join(GT,n)).convert("L"))>127)
        p32=infer(s32,img); p16=infer(s16,img)
        d32.append(dice(p32,g)); d16.append(dice(p16,g)); diff.append(float(np.abs(p32-p16).max()))
    print("驗證 n=%d: Dice fp32=%.3f fp16=%.3f 最大機率差=%.4f(應約0=無損)"%(len(d32),np.mean(d32),np.mean(d16),max(diff)))
else:
    print("略過數值驗證(找不到 retrain_merged);僅確認可載入推論")
    _=infer(s16,np.zeros((256,256,3),np.uint8))

# 4) 部署到 Backend/Flask/models/
os.makedirs(BK,exist_ok=True)
shutil.copy(os.path.join(HERE,"student_fp16.onnx"),os.path.join(BK,"student_fp16.onnx"))
print("fp32=%.1fMB fp16=%.1fMB -> 已部署 %s"%(fp32,fp16,os.path.join(BK,"student_fp16.onnx")))
print("提醒:Android 放 app/src/main/assets/student_fp16.onnx;Windows 放各自 models/;iOS 用 StudentSeg.mlmodel(另經 CoreML)。")
