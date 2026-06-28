# -*- coding: utf-8 -*-
"""學生 → CoreML(.mlpackage) for iOS(本機 mac 執行)。
學生前處理=ImageNet RGB；CoreML ImageType 的 scale 為純量、無法做 per-channel std,
故把正規化「包進模型」:ImageType 先 /255→[0,1],包裝層再 (x-mean)/std。
iOS 端直接餵原圖(RGB),不需自己做正規化(避免跨端不一致)。
pip install coremltools torch segmentation_models_pytorch
用法: STUDENT_ENCODER=mobilenet_v2 python convert_student_coreml.py
"""
import os, torch, torch.nn as nn, numpy as np, segmentation_models_pytorch as smp
import coremltools as ct
ENC=os.environ.get("STUDENT_ENCODER","mobilenet_v2"); SIZE=256
mean=[0.485,0.456,0.406]; std=[0.229,0.224,0.225]
class Wrapped(nn.Module):
    def __init__(s,net):
        super().__init__(); s.net=net
        s.register_buffer("m",torch.tensor(mean).view(1,3,1,1))
        s.register_buffer("s",torch.tensor(std).view(1,3,1,1))
    def forward(s,x):                 # x: [0,1] RGB (ImageType scale=1/255)
        x=(x-s.m)/s.s
        return torch.sigmoid(s.net(x))   # 直接輸出機率(0-1)
net=smp.Unet(encoder_name=ENC,encoder_weights=None,in_channels=3,classes=1,activation=None)
net.load_state_dict(torch.load("student_best.pt",map_location="cpu")); net.eval()
model=Wrapped(net).eval()
ex=torch.rand(1,3,SIZE,SIZE)
ts=torch.jit.trace(model,ex)
inp=[ct.ImageType(name="image",shape=(1,3,SIZE,SIZE),scale=1/255.0,bias=[0,0,0],color_layout=ct.colorlayout.RGB)]
try:
    # 優先 mlprogram(.mlpackage,需 macOS/Linux 的 libmilstoragepython)
    mlmodel=ct.convert(ts,inputs=inp,outputs=[ct.TensorType(name="mask")],
                       minimum_deployment_target=ct.target.iOS16,compute_precision=ct.precision.FLOAT16)
    mlmodel.save("StudentSeg.mlpackage")
    print("CoreML 完成 → StudentSeg.mlpackage (mlprogram, FP16)")
except Exception as e:
    print("mlprogram 失敗(常見於 Windows:BlobWriter):",repr(e)[:100])
    print("改用 neuralnetwork(.mlmodel,Windows 可寫)...")
    mlmodel=ct.convert(ts,inputs=inp,convert_to="neuralnetwork",minimum_deployment_target=ct.target.iOS14)
    try:
        from coremltools.models.neural_network.quantization_utils import quantize_weights
        mlmodel=quantize_weights(mlmodel,nbits=16)  # FP16 權重
    except Exception as qe:
        print("FP16 量化略過:",repr(qe)[:80])
    mlmodel.save("StudentSeg.mlmodel")
    print("CoreML 完成 → StudentSeg.mlmodel (neuralnetwork)")
print("iOS 用法: 餵原圖(RGB,自動 resize 256),輸出 mask 機率,門檻 0.4。內含 imagenet 正規化。")
