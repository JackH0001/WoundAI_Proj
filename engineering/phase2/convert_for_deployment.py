# -*- coding: utf-8 -*-
"""M2 模型落地打包(turnkey,於本機/mac 執行)。
重點:Android(OnnxSegmentationModule)、Windows(OnnxAIModule)、Backend 皆用 onnxruntime →
**分割 ONNX 可直接部署該三端,無需轉檔**;只有 iOS 需 CoreML。

部署矩陣:
  Backend/Windows/Android : 直接放 smp_trained.onnx / fusegnet.onnx(+a_unet/unetpp 供集成)。前處理讀 SSOT 產生常數。
  iOS                     : 轉 CoreML(.mlpackage)。smp 來源為 PyTorch、A-UNet/UNet++ 來源為 Keras .h5 → 直接從原框架轉最穩(ONNX→CoreML 於新版 coremltools 已不支援)。

iOS CoreML 轉檔(擇一來源,本機 mac):
  # 從 Keras .h5 (A-UNet/UNet++)
  pip install coremltools tensorflow tf-keras
  python -c "import coremltools as ct,tensorflow as tf; m=tf.keras.models.load_model('att_unet_best.h5',compile=False); ct.convert(m, source='tensorflow', inputs=[ct.ImageType(shape=(1,256,256,3),scale=1/127.5,bias=[-1,-1,-1])]).save('AUNet.mlpackage')"
  # 從 PyTorch (smp) → 先 torchscript 再轉(略;見 coremltools torch 範例)

注意:CoreML ImageType 的 scale/bias 必須對應 SSOT 前處理(A-UNet/UNet++=[-1,1] → scale=1/127.5,bias=-1);wsm=[0,1] BGR → scale=1/255 且需 BGR(ct 用 color_layout=BGR)。**勿讓 CoreML 與 SSOT 前處理不一致(這正是先前 bug 來源)。**
"""
import onnxruntime as ort, hashlib, os, json, sys
def verify_onnx(path, expect_hw=None, expect_layout=None):
    s=ort.InferenceSession(path,providers=["CPUExecutionProvider"]); i=s.get_inputs()[0]
    sha=hashlib.sha256(open(path,"rb").read()).hexdigest()[:16]
    return {"input":i.name,"shape":[str(x) for x in i.shape],"sha256":sha,"mb":round(os.path.getsize(path)/1e6,1)}
if __name__=="__main__":
    A=os.environ.get("WOUNDAI_ARCHIVE","C:/dev/WoundAI_weights_archive")
    targets={"smp":A+"/雲端 AI 模型訓練及分析服務/wound-segmentation-master/smp_trained.onnx",
             "fusegnet":A+"/雲端 AI 模型訓練及分析服務/FUSegNet-main/fusegnet.onnx"}
    for k,p in targets.items():
        if os.path.exists(p): print(k, json.dumps(verify_onnx(p),ensure_ascii=False))
        else: print(k,"缺檔",p)
