"""Execute the actual app preprocessing functions in a container-shaped tree.

No app startup, credentials, models or cloud clients; synthetic RGB inputs only.
"""
import ast
import json
import logging
from pathlib import Path
import sys
import tempfile
import unittest
import numpy as np

ROOT=Path(__file__).resolve().parents[2]
FLASK=ROOT/'Backend/Flask'
sys.path.insert(0,str(FLASK))

class PackagedPreprocessingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name)/'service'/'app';self.base.mkdir(parents=True)
        self.repo=self.base/'../../engineering/phase0/preprocessing.json'
        self.vendor=self.base/'vendor/preprocessing.json'
        self.config=json.loads((ROOT/'engineering/phase0/preprocessing.json').read_text())
        tree=ast.parse((FLASK/'app.py').read_text())
        selected=[node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name in ('_load_ssot','_apply_ssot_preproc')]
        assert len(selected)==2
        import os
        self.ns=dict(__file__=str(self.base/'app.py'),os=os,_json=json,np=np,_SSOT_CACHE=None,logger=logging.getLogger('packaged-test'))
        try:
            from model_preprocessing import load_preprocessing
            self.ns['load_preprocessing']=load_preprocessing
        except ModuleNotFoundError:pass
        exec(compile(ast.Module(body=selected,type_ignores=[]),str(FLASK/'app.py'),'exec'),self.ns)
    def write(self,path,config):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(config))
    def test_vendor_student_has_imagenet_normalization(self):
        self.write(self.vendor,self.config)
        result=self.ns['_apply_ssot_preproc'](np.zeros((2,2,3),np.uint8),'student')
        np.testing.assert_allclose(result[0,0],[-0.485/0.229,-0.456/0.224,-0.406/0.225],rtol=1e-6)
    def test_vendor_wsm_preserves_bgr_contract(self):
        self.write(self.vendor,self.config)
        result=self.ns['_apply_ssot_preproc'](np.array([[[255,0,0]]],np.uint8),'wsm')
        np.testing.assert_array_equal(result,[[[0,0,1]]])
    def test_repo_authority_precedes_stale_vendor(self):
        self.write(self.repo,self.config);self.write(self.vendor,{'wrong':'stale'})
        self.assertEqual(self.ns['_load_ssot'](),self.config)
    def test_missing_configuration_never_defaults_to_wrong_normalization(self):
        with self.assertRaises((OSError,ValueError)):self.ns['_load_ssot']()
    def test_corrupt_repo_cannot_fall_through_to_old_vendor(self):
        self.write(self.vendor,self.config);self.repo.parent.mkdir(parents=True,exist_ok=True);self.repo.write_text('{broken')
        with self.assertRaises(ValueError):self.ns['_load_ssot']()
    def test_invalid_vendor_structure_is_rejected(self):
        for value in ([],{}, {'models':{}}, {'models':self.config['models'],'imagenet_mean':[0,0,0],'imagenet_std':[0,0,0]}):
            with self.subTest(value=type(value).__name__):
                self.ns['_SSOT_CACHE']=None;self.write(self.vendor,value)
                with self.assertRaises(ValueError):self.ns['_load_ssot']()
    def test_negative_one_normalization_and_float32_contiguous_output(self):
        self.config["models"]["synthetic"] = dict(self.config["models"]["student"], normalize="[-1,1]")
        self.write(self.vendor,self.config)
        result=self.ns['_apply_ssot_preproc'](np.array([[[255,0,127]]],np.uint8),'synthetic')
        np.testing.assert_allclose(result[0,0],[1,-1,127/127.5-1],atol=1e-7)
        self.assertEqual(result.dtype,np.float32);self.assertTrue(result.flags.c_contiguous)

    def load_model(self, shape, model_name='student_fp16.onnx'):
        from types import SimpleNamespace
        tree=ast.parse((FLASK/'app.py').read_text())
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='WoundAnalysisService')
        method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='load_models')
        session=SimpleNamespace(get_inputs=lambda:[SimpleNamespace(shape=shape)],get_providers=lambda:['synthetic'])
        ort=SimpleNamespace(SessionOptions=lambda:SimpleNamespace(),GraphOptimizationLevel=SimpleNamespace(ORT_ENABLE_ALL=1),InferenceSession=lambda *a,**k:session)
        self.ns.update(ONNX_AVAILABLE=True,TENSORFLOW_AVAILABLE=False,ort=ort,wound_segmentation_model=None,tissue_classification_model=None)
        exec(compile(ast.Module(body=[method],type_ignores=[]),str(FLASK/'app.py'),'exec'),self.ns)
        service=SimpleNamespace(_resolve_onnx_model_path=lambda:str(self.base/'models'/model_name))
        self.ns['load_models'](service)
        return service,session
    def test_valid_model_is_published_only_after_packaged_contract_check(self):
        self.write(self.vendor,self.config)
        service,session=self.load_model(['N',3,256,256])
        self.assertIs(self.ns['wound_segmentation_model'],session)
        self.assertEqual(service._model_backend,'onnxruntime')
    def test_wrong_layout_or_shape_never_publishes_model(self):
        self.write(self.vendor,self.config)
        for shape in (['N',256,256,3],['N',3,224,224],['N',3,'H','W']):
            with self.subTest(shape=shape):
                service,_=self.load_model(shape)
                self.assertIsNone(self.ns['wound_segmentation_model'])
                self.assertNotEqual(service._model_backend,'onnxruntime')
    def test_missing_or_unknown_model_contract_never_publishes_model(self):
        self.load_model(['N',3,256,256]);self.assertIsNone(self.ns['wound_segmentation_model'])
        self.write(self.vendor,self.config)
        self.load_model(['N',3,256,256],'unknown.onnx');self.assertIsNone(self.ns['wound_segmentation_model'])

if __name__=='__main__':unittest.main()
