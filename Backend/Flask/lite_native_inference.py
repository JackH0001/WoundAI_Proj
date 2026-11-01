"""Explicit, pinned Lite ONNX adapter without importing the research application.

Student logits are converted to probability, matching Android and the CoreML
export contract. The existing on-weak ensemble selection remains unchanged.
Missing/invalid models and inference errors stop the operation: there is no HSV
fallback, silent empty result, source lookup outside the supplied package or
environment-selected threshold. Hashes identify artifacts, not clinical quality.
"""
import hashlib
from pathlib import Path
import cv2
import numpy as np
from lite_attest_state import StateUnavailable
from lite_service_operations import RasterInference
from lite_raster_tools import _has_face
from lite_usage_ledger import digest, _hex
from model_preprocessing import load_preprocessing, student_logits_probability, STUDENT_OUTPUT_CONTRACT

MODEL_SHA256 = {
    'student_fp16.onnx': '6fa9c340c458d2b1f4ca022c6516aaffbe686adcc84d3292c4bb5f43850ebacb',
    'a_unet.onnx': 'b05b3d17c5f8b9716cf36bac1027d8ab6728b3490fc3ce331673a57296286ae4',
    'unetpp.onnx': 'aa6c9619c68c7d267c93c3ca0838dabcb8fe8572fd313036fca311377bb8f2fc',
}
ALGORITHM = 'lite-student-sigmoid-on-weak/2'


def file_hash(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def regular_file(path, base):
    if not path.is_file() or not path.resolve().is_relative_to(base.resolve()):
        raise ValueError('missing or outside-package model input')
    for item in (path, *path.parents):
        if item == base: break
        if item.is_symlink(): raise ValueError('linked model input refused')
    return path


class LiteNativeInference:
    def __init__(self, *, package, preprocessing_sha256, session_factory=None):
        base = Path(package).resolve()
        _hex(preprocessing_sha256)
        # A staged package always supplies its own SSOT; checkout-relative
        # lookup is intentionally unavailable to this independent adapter.
        config_path = regular_file(base/'vendor/preprocessing.json', base)
        if (base/'../../engineering/phase0/preprocessing.json').exists():
            raise ValueError('standalone package required; no checkout SSOT fallback')
        if file_hash(config_path) != preprocessing_sha256:
            raise ValueError('preprocessing digest mismatch')
        config = load_preprocessing(base)
        if file_hash(config_path) != preprocessing_sha256:
            raise ValueError('preprocessing changed during load')
        c = config['models'].get('student', {})
        if (c.get('input_size') != [256, 256] or c.get('layout') != 'NCHW'
                or c.get('channel_order') != 'RGB' or c.get('normalize') != 'imagenet'
                or type(c.get('threshold')) not in (float, int) or c['threshold'] != 0.4
                or c.get('output') != 'logits_mask'):
            raise ValueError('reviewed student preprocessing required')
        paths = {name:regular_file(base/'models'/name, base) for name in MODEL_SHA256}
        if any(file_hash(p) != MODEL_SHA256[n] for n,p in paths.items()):
            raise ValueError('model digest mismatch')
        if session_factory is None:
            import onnxruntime as ort
            options = ort.SessionOptions()
            options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            options.intra_op_num_threads = 2
            def session_factory(raw):
                return ort.InferenceSession(raw, options, providers=['CPUExecutionProvider'])
        self.sessions = {}
        for name, path in paths.items():
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != MODEL_SHA256[name]:
                raise ValueError('model changed during load')
            session = session_factory(raw)
            expected = [3, 256, 256] if name == 'student_fp16.onnx' else [256, 256, 3]
            inputs = session.get_inputs()
            shape = list(inputs[0].shape) if len(inputs) == 1 else []
            if (len(shape) != 4 or shape[1:] != expected
                    or not (shape[0] is None or shape[0] == 1 or type(shape[0]) is str and bool(shape[0]))
                    or inputs[0].type != 'tensor(float)'):
                raise ValueError('model input shape/type differs from reviewed contract')
            self.sessions[name] = session
        self.mean = np.asarray(config['imagenet_mean'], np.float32)
        self.std = np.asarray(config['imagenet_std'], np.float32)
        self.version = digest(dict(algorithm=ALGORITHM, artifacts=MODEL_SHA256,
            preprocessing=preprocessing_sha256, student_output=STUDENT_OUTPUT_CONTRACT,
            weak_fraction=0.01, ensemble_threshold=0.4))
        self.raster = RasterInference(self, _has_face)

    @staticmethod
    def run(session, tensor, shape):
        output = session.run(None, {session.get_inputs()[0].name: tensor})
        if not output:
            raise StateUnavailable('model output absent')
        a = np.asarray(output[0])
        if tuple(a.shape) not in shape or not np.isfinite(a).all():
            raise StateUnavailable('model output shape/values invalid')
        return a

    def _student(self, rgb):
        h,w = rgb.shape[:2]
        x = cv2.resize(rgb, (256,256), interpolation=cv2.INTER_CUBIC).astype(np.float32)
        x = np.ascontiguousarray((x/255.0-self.mean)/self.std)
        x = np.transpose(x, (2,0,1))[None]
        p = self.run(self.sessions['student_fp16.onnx'], x, ((1,1,256,256),(1,256,256,1)))
        p = np.squeeze(p).astype(np.float32)
        p = student_logits_probability(p)
        return cv2.resize(p, (w,h), interpolation=cv2.INTER_LINEAR) > 0.4

    def _ensemble(self, rgb):
        x = cv2.resize(rgb, (256,256)).astype(np.float32)/127.5-1.0
        outputs = []
        for name in ('a_unet.onnx','unetpp.onnx'):
            p = np.squeeze(self.run(self.sessions[name], x[None], ((1,256,256,1),(1,256,256))))
            p = p.astype(np.float32)
            if p.min() < 0 or p.max() > 1:
                p = 1.0/(1.0+np.exp(-np.clip(p,-30,30)))
            outputs.append(p)
        return cv2.resize(0.5*outputs[0]+0.5*outputs[1], (rgb.shape[1],rgb.shape[0])) > 0.4

    def __call__(self, rgb):
        if (not isinstance(rgb,np.ndarray) or rgb.dtype != np.uint8 or rgb.ndim != 3
                or rgb.shape[2] != 3 or min(rgb.shape[:2]) < 1 or max(rgb.shape[:2]) > 8192
                or rgb.shape[0]*rgb.shape[1] > 16000000):
            raise ValueError('bounded uint8 RGB image required')
        try:
            mask = self._student(rgb)
            fraction = float(mask.sum())/mask.size
            info = dict(route='student', escalated=False, student_output_contract=STUDENT_OUTPUT_CONTRACT)
            if fraction >= 0.01: return mask,info
            info['weak_reason'] = 'empty' if fraction == 0 else 'tiny_mask %.4f' % fraction
            au = self._ensemble(rgb)
            def big(m):
                cs,_ = cv2.findContours(m.astype(np.uint8),cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
                return max((cv2.contourArea(c) for c in cs),default=0.0)
            sp,ap = big(mask),big(au)
            inter,union = float(np.logical_and(mask,au).sum()),float(np.logical_or(mask,au).sum())
            info['iou_student_au'] = round(inter/union,3) if union else 1.0
            info['au_area_ratio'] = round(ap/sp,2) if sp else (999.0 if ap else 0.0)
            if ap > 0 and (info['au_area_ratio'] > 1.5 or info['iou_student_au'] < 0.5):
                info.update(route='cloud_escalated(AU)',escalated=True,seg_model='ensemble.AU')
                return au,info
            return mask,info
        except Exception:
            # HTTP reports unavailability; it must not settle a model failure
            # as a successful empty inference or reveal provider paths.
            raise StateUnavailable('native inference unavailable') from None
