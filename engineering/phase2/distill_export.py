# -*- coding: utf-8 -*-
"""從 student_best.pt 匯出 ONNX(免重訓)。torch 2.12 新匯出器需 onnxscript;本檔先試 legacy(dynamo=False)。
用法: STUDENT_ENCODER=mobilenet_v2 python distill_export.py"""
import os, torch, segmentation_models_pytorch as smp
ENC=os.environ.get("STUDENT_ENCODER","mobilenet_v2"); SIZE=256
m=smp.Unet(encoder_name=ENC,encoder_weights=None,in_channels=3,classes=1,activation=None)
m.load_state_dict(torch.load("student_best.pt",map_location="cpu")); m.eval()
dummy=torch.randn(1,3,SIZE,SIZE)
try:
    torch.onnx.export(m,dummy,"student_distilled.onnx",input_names=["input"],output_names=["mask"],
                      opset_version=13,dynamo=False,dynamic_axes={"input":{0:"N"},"mask":{0:"N"}})
    print("匯出成功(legacy) → student_distilled.onnx")
except TypeError:
    # 舊版 torch 沒有 dynamo 參數
    torch.onnx.export(m,dummy,"student_distilled.onnx",input_names=["input"],output_names=["mask"],
                      opset_version=13,dynamic_axes={"input":{0:"N"},"mask":{0:"N"}})
    print("匯出成功 → student_distilled.onnx")
import os.path as op
print("大小 %.1f MB"%(op.getsize("student_distilled.onnx")/1e6))
