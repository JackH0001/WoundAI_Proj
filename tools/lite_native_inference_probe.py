"""Compare pinned ONNX artifacts against isolated legacy inference on synthetic RGB.

No credentials, human images, storage service, cloud request or application import.
This measures migration parity, not model accuracy, clinical quality or throughput.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import time
import subprocess
import cv2
import numpy as np
from lite_native_inference import LiteNativeInference, MODEL_SHA256
from lite_raster_tools import _polygons_from_mask
from test_lite_native_inference import legacy_adapter


def run(models, root):
    records=[]
    baseline='1cf4ab6b0fbabf115ca71f2a1fa725d19e04a470'
    previous=subprocess.check_output(['git','show',baseline+':Backend/Flask/app.py'],cwd=root,text=True)
    with tempfile.TemporaryDirectory(prefix='lite-model-parity-') as t:
        base=Path(t);(base/'models').mkdir();(base/'vendor').mkdir()
        original={name:hashlib.sha256((models/name).read_bytes()).hexdigest() for name in MODEL_SHA256}
        if original!=MODEL_SHA256:raise ValueError('unreviewed model artifacts')
        for name in MODEL_SHA256:shutil.copyfile(models/name,base/'models'/name)
        config=root/'engineering/phase0/preprocessing.json'
        raw=config.read_bytes();(base/'vendor/preprocessing.json').write_bytes(raw)
        model=LiteNativeInference(package=base,preprocessing_sha256=hashlib.sha256(raw).hexdigest())
        aligned=legacy_adapter(model.sessions,json.loads(raw))
        old=legacy_adapter(model.sessions,json.loads(raw),previous)
        rng=np.random.default_rng(314159)
        cases={
            'blank-white':np.full((320,480,3),255,np.uint8),
            'blank-black':np.zeros((240,320,3),np.uint8),
            'random-square':rng.integers(0,256,(256,256,3),dtype=np.uint8),
            'random-portrait':rng.integers(0,256,(521,319,3),dtype=np.uint8),
            'red-rectangle':np.full((640,480,3),230,np.uint8),
            'gradient':np.repeat(np.arange(256,dtype=np.uint8)[None,:,None],256,axis=0).repeat(3,axis=2),
        }
        cases['red-rectangle'][180:420,150:320]=(180,50,70)
        for name,rgb in cases.items():
            before=hashlib.sha256(rgb.tobytes()).hexdigest();start=time.monotonic()
            x=cv2.resize(rgb,(256,256),interpolation=cv2.INTER_CUBIC).astype(np.float32)
            x=np.ascontiguousarray((x/255.0-model.mean)/model.std)
            session=model.sessions['student_fp16.onnx']
            raw_scores=session.run(None,{session.get_inputs()[0].name:np.transpose(x,(2,0,1))[None]})[0]
            before_mask,before_info=old(rgb)
            a,ai=aligned(rgb);b,bi=model(rgb)
            ap,bp=_polygons_from_mask(a),_polygons_from_mask(b)
            same=bool(np.array_equal(a,b) and ai==bi and ap==bp)
            if not same:raise AssertionError('model migration parity: '+name)
            if before!=hashlib.sha256(rgb.tobytes()).hexdigest():raise AssertionError('RGB mutated')
            records.append(dict(case=name,width=rgb.shape[1],height=rgb.shape[0],
                differing_pixels=int(np.count_nonzero(a!=b)),foreground_pixels=int(b.sum()),
                before_foreground_pixels=int(before_mask.sum()),before_route=before_info['route'],
                raw_student_min=float(raw_scores.min()),raw_student_max=float(raw_scores.max()),
                correction_changed_pixels=int(np.count_nonzero(before_mask!=b)),
                route=bi['route'],polygons=len(bp),mask_sha256=hashlib.sha256(b.tobytes()).hexdigest(),
                route_metadata_equal=ai==bi,polygons_equal=ap==bp,source_unchanged=True,
                pair_seconds=round(time.monotonic()-start,3)))
        if original!={name:hashlib.sha256((models/name).read_bytes()).hexdigest() for name in MODEL_SHA256}:
            raise AssertionError('source model changed')
        return dict(schema='lite-native-inference-parity/1',passed=True,cases=records,
            model_version=model.version,artifact_hashes=original,preprocessing_sha256=hashlib.sha256(raw).hexdigest(),
            runtime=dict(opencv=cv2.__version__,numpy=np.__version__,onnxruntime=__import__('onnxruntime').__version__),
            baseline=baseline,
            scope='local real ONNX runtime, six synthetic images; corrected native vs corrected legacy parity and before/after; not clinical accuracy or image deployment',
            original_models_unchanged=True,cloud=False)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--models',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();v=run(a.models,Path(__file__).resolve().parents[1])
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(v,indent=2)+'\n')
    print(json.dumps(dict(passed=v['passed'],cases=len(v['cases']),max_differing_pixels=max(r['differing_pixels'] for r in v['cases']))))
if __name__=='__main__':main()
