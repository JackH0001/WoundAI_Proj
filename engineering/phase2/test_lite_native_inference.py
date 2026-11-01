"""Candidate model parity against isolated legacy functions, no model download.

Synthetic sessions test tensor/threshold/control-flow and failures. The separate
CLI probe runs pinned real ONNX artifacts on synthetic RGB, never patient data.
"""
import ast
import hashlib
import json
import logging
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import cv2
import numpy as np
import lite_native_inference as native
from lite_attest_state import StateUnavailable
from lite_raster_tools import _polygons_from_mask
from model_preprocessing import student_logits_probability, STUDENT_OUTPUT_CONTRACT
from stage_lite_candidate import MODEL_SHA256 as STAGED_MODELS

ROOT=Path(__file__).resolve().parents[2]


def legacy_adapter(sessions, config, source=None):
    """Evaluate only the named legacy functions, no application module startup."""
    wanted={'_apply_ssot_preproc','segment_wound_ai','_au_infer','student_threshold','escalate_mask','segment_for_lite'}
    tree=ast.parse(source if source is not None else (ROOT/'Backend/Flask/app.py').read_text())
    nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in wanted]
    assert len(nodes)==len(wanted)
    scope=dict(np=np,cv2=cv2,ONNX_AVAILABLE=True,ort=SimpleNamespace(InferenceSession=type(sessions['student_fp16.onnx'])),
        wound_segmentation_model=sessions['student_fp16.onnx'],_active_model_key=lambda:'student',
        _load_ssot=lambda:config,_load_cloud_au=lambda:(sessions['a_unet.onnx'],sessions['unetpp.onnx']),
        os=SimpleNamespace(environ={}),logger=logging.getLogger('legacy-parity'),
        student_logits_probability=student_logits_probability,STUDENT_OUTPUT_CONTRACT=STUDENT_OUTPUT_CONTRACT,
        segment_wound_traditional=lambda _:(_ for _ in ()).throw(AssertionError('fallback forbidden in parity')))
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'legacy-functions','exec'),scope)
    return scope['segment_for_lite']


class Session:
    def __init__(self,name,output):
        self.name,self.output,self.calls=name,output,[]
        self.shape=['N',3,256,256] if name=='student_fp16.onnx' else ['N',256,256,3]
        self.kind='tensor(float)'
    def get_inputs(self):return [SimpleNamespace(name='input',shape=self.shape,type=self.kind)]
    def run(self,_,tensors):
        self.calls.append(tensors['input'].copy())
        if isinstance(self.output,Exception):raise self.output
        return [self.output.copy()]


class ModelTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.base=Path(t.name)
        (self.base/'vendor').mkdir();(self.base/'models').mkdir()
        self.config=ROOT/'engineering/phase0/preprocessing.json'
        shutil.copyfile(self.config,self.base/'vendor/preprocessing.json')
        self.hash=hashlib.sha256(self.config.read_bytes()).hexdigest()
        self.hashes={};self.sessions={}
        for name in native.MODEL_SHA256:
            raw=name.encode();(self.base/'models'/name).write_bytes(raw)
            self.hashes[name]=hashlib.sha256(raw).hexdigest()
            shape=(1,1,256,256) if name.startswith('student') else (1,256,256,1)
            self.sessions[name]=Session(name,np.full(shape,-10.0 if name.startswith('student') else 0.0,np.float32))
        p=patch.object(native,'MODEL_SHA256',self.hashes);p.start();self.addCleanup(p.stop)
        self.factory_calls=[]
        def factory(raw):
            self.factory_calls.append(raw);return self.sessions[raw.decode()]
        self.factory=factory
        self.rgb=np.random.default_rng(17).integers(0,256,(319,521,3),dtype=np.uint8)
    def make(self):return native.LiteNativeInference(package=self.base,preprocessing_sha256=self.hash,session_factory=self.factory)
    def compare(self):
        model=self.make();old=legacy_adapter(self.sessions,json.loads(self.config.read_bytes()))
        first,fi=old(self.rgb);before={k:[a.copy() for a in s.calls] for k,s in self.sessions.items()}
        second,si=model(self.rgb)
        np.testing.assert_array_equal(first,second);self.assertEqual(fi,si)
        self.assertEqual(_polygons_from_mask(first),_polygons_from_mask(second))
        for name,inputs in before.items():
            self.assertEqual(len(self.sessions[name].calls),2*len(inputs))
            for a,b in zip(inputs,self.sessions[name].calls[len(inputs):]):np.testing.assert_array_equal(a,b)
        return model
    def test_artifact_pins_match_existing_build_profile(self):
        self.assertEqual(STAGED_MODELS,ModelTests.original_pins)
    original_pins=dict(native.MODEL_SHA256)
    def test_student_normal_mask_identical_without_ensemble_execution(self):
        self.sessions['student_fp16.onnx'].output[:,:,20:160,30:180]=0.9
        self.compare();self.assertEqual(self.sessions['a_unet.onnx'].calls,[])
    def test_empty_student_ensemble_rescue_identical(self):
        self.sessions['a_unet.onnx'].output[:,20:150,50:210,:]=0.95
        self.sessions['unetpp.onnx'].output[:,20:150,50:210,:]=0.95
        self.compare()
    def test_tiny_student_and_ensemble_logits_identical(self):
        self.sessions['student_fp16.onnx'].output[:,:,100:104,100:105]=0.95
        for name in ('a_unet.onnx','unetpp.onnx'):
            self.sessions[name].output.fill(-8);self.sessions[name].output[:,20:150,50:210,:]=8
        self.compare()
    def test_legitimate_empty_output_identical(self):self.compare()
    def test_point_four_logit_is_not_mistaken_for_point_four_probability(self):
        self.sessions['student_fp16.onnx'].output.fill(0.4)
        model=self.compare();self.assertTrue(model(self.rgb)[0].all())
    def test_missing_model_refused_before_any_session_created(self):
        (self.base/'models/unetpp.onnx').unlink()
        with self.assertRaises(ValueError):self.make()
        self.assertEqual(self.factory_calls,[])
    def test_wrong_model_digest_refused_before_any_session_created(self):
        (self.base/'models/unetpp.onnx').write_bytes(b'wrong')
        with self.assertRaisesRegex(ValueError,'digest'):self.make()
        self.assertEqual(self.factory_calls,[])
    def test_external_symlink_refused(self):
        p=self.base/'models/a_unet.onnx';p.unlink();p.symlink_to(self.config)
        with self.assertRaises(ValueError):self.make()
    def test_mismatched_config_hash_refused(self):
        self.hash='0'*64
        with self.assertRaisesRegex(ValueError,'digest'):self.make()
    def test_modified_threshold_not_accepted_even_with_new_hash(self):
        p=self.base/'vendor/preprocessing.json';c=json.loads(p.read_bytes());c['models']['student']['threshold']=0.5
        p.write_text(json.dumps(c));self.hash=hashlib.sha256(p.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError,'preprocessing required'):self.make()
    def test_wrong_input_shape_or_dtype_refused(self):
        s=self.sessions['student_fp16.onnx'];s.shape=['N',256,256,3]
        with self.assertRaises(ValueError):self.make()
        s.shape=[1,3,256,256];s.kind='tensor(float16)'
        with self.assertRaises(ValueError):self.make()
    def test_fixed_and_symbolic_batch_one_supported(self):
        self.make();self.sessions['student_fp16.onnx'].shape[0]=1;self.make()
        self.sessions['student_fp16.onnx'].shape[0]=2
        with self.assertRaises(ValueError):self.make()
    def test_output_nan_and_shape_fail_instead_of_hsv_or_empty(self):
        model=self.make();s=self.sessions['student_fp16.onnx']
        for value in (np.full((1,1,256,256),np.nan),np.zeros((256,256))):
            s.output=value
            with self.assertRaisesRegex(StateUnavailable,'inference unavailable'):model(self.rgb)
    def test_real_artifact_logit_semantics_apply_sigmoid_once(self):
        s=self.sessions['student_fp16.onnx'];s.output.fill(-2)
        s.output[:,:,60:180,60:180]=0.2;s.output[:,:,90:150,90:150]=2.0
        self.compare()
    def test_session_error_does_not_settle_as_successful_empty(self):
        model=self.make();self.sessions['student_fp16.onnx'].output=RuntimeError('private/path/token')
        with self.assertRaisesRegex(StateUnavailable,'^native inference unavailable$'):model(self.rgb)
    def test_ensemble_error_does_not_fall_back_to_empty_student(self):
        model=self.make();self.sessions['unetpp.onnx'].output=RuntimeError('private/path')
        with self.assertRaises(StateUnavailable):model(self.rgb)
    def test_invalid_image_rejected_before_model(self):
        model=self.make()
        for value in (None,np.zeros((10,10)),np.zeros((10,10,3),np.float32),np.zeros((0,10,3),np.uint8)):
            with self.assertRaises(ValueError):model(value)
        self.assertEqual(self.sessions['student_fp16.onnx'].calls,[])
    def test_sigmoid_matches_independent_scalar_oracle(self):
        raw=np.array([[-88.,-10.,-1.,-0.4054651081081643,-0.1,0.,0.2,0.4,1.,10.,88.]],np.float32)
        expected=np.array([[1/(1+math.exp(-float(x))) for x in raw[0]]],np.float64)
        np.testing.assert_allclose(student_logits_probability(raw),expected,rtol=2e-7,atol=6e-8)
    def test_all_logits_inside_zero_one_still_get_converted(self):
        raw=np.array([[0.1,0.2,0.3]],np.float32)
        self.assertTrue((student_logits_probability(raw)>0.4).all())
    def test_probability_conversion_precedes_resize(self):
        raw=np.array([[-8.,2.],[2.,-8.]],np.float32)
        correct=cv2.resize(student_logits_probability(raw),(5,5))
        wrong=student_logits_probability(cv2.resize(raw,(5,5)))
        self.assertGreater(float(abs(correct-wrong).max()),0.1)
    def test_version_covers_preprocessing_and_all_models(self):
        first=self.make().version
        p=self.base/'vendor/preprocessing.json';p.write_text(p.read_text()+'\n')
        self.hash=hashlib.sha256(p.read_bytes()).hexdigest()
        self.assertNotEqual(self.make().version,first)
    def test_shared_helpers_do_not_import_flask_or_research(self):
        code='import sys;import lite_raster_tools;assert not ({"api_lite","api_flywheel","store","flask"}&set(sys.modules))'
        subprocess.run([sys.executable,'-c',code],check=True)
    def test_native_raster_infer_does_not_import_research_api(self):
        code='''import sys,numpy as np
from lite_service_operations import RasterInference
r=RasterInference(lambda rgb:(np.ones(rgb.shape[:2],np.uint8),{}),lambda _:False)
r.infer(np.zeros((20,20,3),np.uint8))
assert not ({"api_lite","api_flywheel","app","store"}&set(sys.modules))
'''
        subprocess.run([sys.executable,'-c',code],check=True)

class ProvenanceTests(unittest.TestCase):
    def test_signed_legacy_upload_persists_contract_without_rewriting_older_metadata(self):
        import test_lite_raw_depth_http as raw_fixture
        import api_lite
        f=raw_fixture.RawDepthHTTPTests();f.setUp();self.addCleanup(f.doCleanups)
        store=f.h.f.store
        older={'image_id':'older','route':'student','ai_polygons':[]}
        store.put_blob('lite/older/original.json',json.dumps(older).encode())
        def model(rgb):
            return np.ones(rgb.shape[:2],np.uint8),dict(route='student',student_output_contract=STUDENT_OUTPUT_CONTRACT)
        with patch.object(api_lite,'_SEGMENT',side_effect=model):response=f.upload()
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(response.json['student_output_contract'],STUDENT_OUTPUT_CONTRACT)
        keys=list(store.list_keys('lite/'+f.h.owner+'/'))
        meta=[json.loads(store.get_blob(k)) for k in keys if k.endswith('.json')]
        self.assertEqual(len(meta),1)
        self.assertEqual(meta[0]['student_output_contract'],STUDENT_OUTPUT_CONTRACT)
        self.assertEqual(json.loads(store.get_blob('lite/older/original.json')),older)

if __name__=='__main__':unittest.main()
