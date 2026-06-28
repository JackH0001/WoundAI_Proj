import os,glob,json,numpy as np,cv2,onnxruntime as ort
from PIL import Image
A=os.environ.get("WOUNDAI_ARCHIVE","C:/dev/WoundAI_weights_archive")
IM=A+"/批次驗證工具/retrain_merged/image"; LB=A+"/批次驗證工具/retrain_merged/labels"
OD=A+"/onnx_export"
sa=ort.InferenceSession(OD+"/a_unet.onnx",providers=["CPUExecutionProvider"]); ia=sa.get_inputs()[0].name
su=ort.InferenceSession(OD+"/unetpp.onnx",providers=["CPUExecutionProvider"]); iu=su.get_inputs()[0].name
def prob(s,i,img):
    r=cv2.resize(img,(256,256)).astype(np.float32)/127.5-1
    o=np.squeeze(s.run(None,{i:r[None]})[0]).astype(np.float32)
    if o.ndim==3:o=o[...,0]
    return 1/(1+np.exp(-np.clip(o,-30,30))) if (o.min()<0 or o.max()>1) else o
names=sorted(os.listdir(IM))
out=os.path.join(A,"批次驗證工具","distill_teacher_AU"); os.makedirs(out,exist_ok=True)
def dice(p,g):p=p.astype(bool);g=g.astype(bool);s=p.sum()+g.sum();return 2*(p&g).sum()/s if s else 1.0
ds=[]; saved=0
for n in names:
    img=np.asarray(Image.open(os.path.join(IM,n)).convert("RGB"))
    soft=(0.5*prob(sa,ia,img)+0.5*prob(su,iu,img))  # 256 soft prob (老師軟標籤)
    if os.path.exists(os.path.join(out,n.replace(".png","")+".npy")): continue
    np.save(os.path.join(out,n.replace(".png","")+".npy"), soft.astype(np.float16))  # 蒸餾目標(256 soft)
    saved+=1
    g=np.asarray(Image.open(os.path.join(LB,n)).convert("L"))>127
    if g.sum()>0:
        pred=cv2.resize(soft,(g.shape[1],g.shape[0]))>0.4
        ds.append(dice(pred,g))
print(f"老師軟標籤已存 {saved} 張 → distill_teacher_AU/ (256 soft float16)")
print(f"老師(A∪U)在非空GT {len(ds)} 張 Dice@0.4 = {np.mean(ds):.3f} (中位 {np.median(ds):.3f})")
