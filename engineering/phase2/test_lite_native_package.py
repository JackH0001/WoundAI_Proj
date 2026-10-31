"""Exact native context, no legacy app, no model binaries in the repository."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import stage_lite_native_candidate as stage

ROOT=Path(__file__).resolve().parents[2]


class NativePackageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.root=self.base/'repo';self.models=self.base/'models';self.out=self.base/'output'
        files=['Backend/Flask/'+n+'.py' for n in stage.MODULES]
        files+=['Backend/Flask/'+n for n in stage.ASSETS]
        files+=['Backend/LiteNative/'+n for n in stage.PROFILE]+['engineering/phase0/preprocessing.json']
        for name in files:
            p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,p)
        self.models.mkdir();self.hashes={}
        for name in stage.MODEL_SHA256:
            raw=('synthetic model '+name).encode();(self.models/name).write_bytes(raw)
            self.hashes[name]=hashlib.sha256(raw).hexdigest()
        subprocess.run(['git','init','-q',str(self.root)],check=True)
        subprocess.run(['git','-C',str(self.root),'-c','user.name=Synthetic','-c','user.email=test@example.invalid',
            'commit','--allow-empty','-qm','fixture'],check=True)
        p=patch.object(stage,'MODEL_SHA256',self.hashes);p.start();self.addCleanup(p.stop)
    def build(self):return stage.stage(self.root,self.models,self.out)
    def verify(self):
        env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1')
        # Keep installed dependency overlays, never fallback to repo modules.
        env['PYTHONPATH']=os.pathsep.join(p for p in env.get('PYTHONPATH','').split(os.pathsep)
            if Path(p).is_absolute() and 'python-deps' in p)
        return subprocess.run([sys.executable,str(self.out/'verify_native_package.py')],cwd=self.out,
            env=env,capture_output=True,text=True,timeout=30)
    def test_actual_source_dependencies_closed(self):stage.validate_imports(ROOT)
    def test_exact_package_and_clean_import_without_legacy_modules(self):
        for name in ('.env','uploads/original.jpg','store.py','api_lite.py','unknown.py'):
            p=self.root/'Backend/Flask'/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('must not ship')
        m=self.build();self.assertFalse(m['release_approved'])
        self.assertNotIn('lite_key_provisioning.py',m['files'])
        self.assertEqual(len(m['files']),len(stage.MODULES)+len(stage.ASSETS)+len(stage.PROFILE)+4)
        result=self.verify();self.assertEqual(result.returncode,0,result.stderr)
        data=json.loads(result.stdout);self.assertTrue(data['passed']);self.assertFalse(data['cloud_access'])
    def test_missing_or_new_nested_dependency_refused_before_output(self):
        folder=self.root/'Backend/Flask';(folder/'unreviewed.py').write_text('value=1')
        with (folder/'lite_native_startup.py').open('a') as f:f.write('\ndef never_called():\n from unreviewed import value\n')
        with self.assertRaisesRegex(ValueError,'outside allowlist'):self.build()
        self.assertFalse(self.out.exists())
    def test_missing_allowlisted_module_stops(self):
        (self.root/'Backend/Flask/store_errors.py').unlink()
        with self.assertRaises(ValueError):self.build()
        self.assertFalse(self.out.exists())
    def test_unreviewed_model_stops_without_copy(self):
        (self.models/'student_fp16.onnx').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'model digest'):self.build()
        self.assertFalse(self.out.exists())
    def test_symlink_refused(self):
        p=self.root/'Backend/Flask/store_errors.py';p.unlink();p.symlink_to(ROOT/'Backend/Flask/store_errors.py')
        with self.assertRaisesRegex(ValueError,'symlink'):self.build()
    def test_existing_output_never_overwritten(self):
        self.build();before=(self.out/'build-context-manifest.json').read_bytes()
        with self.assertRaises(ValueError):self.build()
        self.assertEqual(before,(self.out/'build-context-manifest.json').read_bytes())
    def test_package_tamper_and_extra_file_block_import(self):
        self.build();p=self.out/'lite_native_startup.py';old=p.read_bytes();p.write_bytes(old+b'\n')
        self.assertNotEqual(self.verify().returncode,0);p.write_bytes(old)
        (self.out/'unexpected.txt').write_text('unexpected');self.assertNotEqual(self.verify().returncode,0)
    def test_profile_uses_factory_pins_and_no_legacy_entry(self):
        text=(ROOT/'Backend/LiteNative/Dockerfile').read_text()
        self.assertIn('lite_native_startup:application()',text)
        self.assertIn('--require-hashes',text);self.assertIn('pip check',text)
        self.assertIn('--workers 1 --threads 2',text);self.assertNotIn('app:app',text)
        self.assertNotIn('LITE_NATIVE_CONFIG=',text)


if __name__=='__main__':unittest.main()
