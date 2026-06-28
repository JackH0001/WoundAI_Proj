# -*- coding: utf-8 -*-
"""把微調 .h5 (A-UNet / UNet++) 轉成 ONNX，供跨端部署、擺脫 TF/Keras 版本相依。
本機執行：pip install tf2onnx onnx，設 WOUNDAI_ARCHIVE 後 python export_h5_to_onnx.py。
輸入規格(與 .h5 相同)：NHWC [N,256,256,3]、RGB、normalize [-1,1] (x/127.5-1)。
輸出：WOUNDAI_ARCHIVE/onnx_export/{a_unet,unetpp}.onnx (純 ASCII 路徑，避 HDF5 中文問題)。"""
import os, numpy as np
import eval_tf_models as E

ARCHIVE = E.ARCHIVE
OUTDIR = os.environ.get("ONNX_OUT", os.path.join(ARCHIVE, "onnx_export")); os.makedirs(OUTDIR, exist_ok=True)
JOBS = {"a_unet": E.MODELS["A-UNet"][0], "unetpp": E.MODELS["UNet++"][0]}  # [0]=相對路徑(MODELS 值為 tuple)
OPSET = int(os.environ.get("OPSET", "13"))

def export_one(keras, tf, tf2onnx, tag, rel):
    mp = os.path.join(ARCHIVE, rel); lp = E._ascii_stage(mp, "x_"+tag)
    model = E._load(keras, lp); print("已載入", tag, "input", model.input_shape, "output", model.output_shape)
    out = os.path.join(OUTDIR, tag + ".onnx")
    spec = (tf.TensorSpec((None, 256, 256, 3), tf.float32, name="input"),)
    try:
        tf2onnx.convert.from_keras(model, input_signature=spec, opset=OPSET, output_path=out)
    except Exception as e:
        print(" from_keras 失敗(改走 SavedModel):", repr(e)[:160])
        import tempfile
        sd = os.path.join(tempfile.gettempdir(), "woundai_sm_" + tag); model.save(sd)
        tf2onnx.convert.from_function  # noqa
        os.system('python -m tf2onnx.convert --saved-model "%s" --output "%s" --opset %d' % (sd, out, OPSET))
    print(" 輸出 ->", out, "(%.1f MB)" % (os.path.getsize(out)/1e6) if os.path.exists(out) else "(未產生)")
    return out

def main():
    import tensorflow as tf, tf2onnx
    keras = E._get_keras()
    for tag, rel in JOBS.items():
        export_one(keras, tf, tf2onnx, tag, rel)
    print("\n完成。ONNX 於", OUTDIR, "（私有權重，勿入協作 repo）")

if __name__ == "__main__": main()
