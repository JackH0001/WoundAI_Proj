"""Candidate packaging excludes runtime/secret files and pins model artifacts."""
import importlib.util
from pathlib import Path
import hashlib
import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('stager',ROOT/'tools/stage_lite_candidate.py')
stager=importlib.util.module_from_spec(spec);spec.loader.exec_module(stager)

class ContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.repo=self.base/'repo';self.models=self.base/'models';self.output=self.base/'output'
        for name in stager.FLASK_FILES:self.write(self.repo/'Backend/Flask'/name,b'reviewed source')
        for name in stager.VENDOR_FILES:self.write(self.repo/'engineering'/name,b'vendor source')
        self.hashes={}
        for name in stager.MODEL_SHA256:
            raw=('synthetic '+name).encode();self.write(self.models/name,raw);self.hashes[name]=hashlib.sha256(raw).hexdigest()
        subprocess.run(['git','init','-q',str(self.repo)],check=True)
        subprocess.run(['git','-C',str(self.repo),'-c','user.name=Test','-c','user.email=test@example.invalid','commit','--allow-empty','-qm','fixture'],check=True)
        self.patch=patch.object(stager,'MODEL_SHA256',self.hashes);self.patch.start();self.addCleanup(self.patch.stop)
    def write(self,path,value):path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(value)
    def run_stage(self):return stager.stage(self.repo,self.models,self.output)
    def test_exact_file_set_and_no_runtime_or_secrets(self):
        for name in ['.env','flywheel/case.jpg','uploads/patient.jpg','clinical.db','private-key.pem','unknown.py']:
            self.write(self.repo/'Backend/Flask'/name,b'must never copy')
        self.write(self.models/'training.jpg',b'must never copy')
        manifest=self.run_stage()
        actual={str(p.relative_to(self.output)) for p in self.output.rglob('*') if p.is_file()}
        self.assertEqual(actual,set(manifest['files'])|{'build-context-manifest.json'})
        self.assertEqual(len(manifest['files']),len(stager.FLASK_FILES)+len(stager.VENDOR_FILES)+3)
        self.assertFalse(manifest['release_approved']);self.assertTrue(manifest['working_tree_dirty'])
        for name,item in manifest['files'].items():self.assertEqual(hashlib.sha256((self.output/name).read_bytes()).hexdigest(),item['sha256'])
    def test_missing_source_and_bad_model_never_publish_partial_context(self):
        source=self.repo/'Backend/Flask/app.py';source.unlink()
        with self.assertRaises(ValueError):self.run_stage()
        self.assertFalse(self.output.exists());source.write_bytes(b'reviewed source')
        (self.models/'student_fp16.onnx').write_bytes(b'corrupt')
        with self.assertRaises(ValueError):self.run_stage()
        self.assertFalse(self.output.exists())
    def test_symlink_file_and_parent_rejected(self):
        path=self.repo/'Backend/Flask/app.py';path.unlink();path.symlink_to(self.models/'student_fp16.onnx')
        with self.assertRaises(ValueError):self.run_stage()
        path.unlink();path.write_bytes(b'source')
        folder=self.repo/'Backend/Flask/certificates';folder.rename(self.base/'certs');folder.symlink_to(self.base/'certs',target_is_directory=True)
        with self.assertRaises(ValueError):self.run_stage()
        self.assertFalse(self.output.exists())
    def test_existing_output_and_repo_nested_output_refused(self):
        self.output.mkdir();(self.output/'keep').write_text('keep')
        with self.assertRaises(ValueError):self.run_stage()
        self.assertEqual((self.output/'keep').read_text(),'keep')
        with self.assertRaises(ValueError):stager.stage(self.repo,self.models,self.repo/'build')
    def test_change_during_copy_is_detected(self):
        original=stager.shutil.copyfile
        def changing(source,target):
            result=original(source,target)
            if source.name=='app.py':source.write_bytes(b'changed source')
            return result
        with patch.object(stager.shutil,'copyfile',side_effect=changing),self.assertRaises(ValueError):self.run_stage()
        self.assertFalse(self.output.exists())

if __name__=='__main__':unittest.main()
