"""Exact worker source context and independent imports, without cloud or models."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import stage_lite_key_candidate as stage

ROOT=Path(__file__).resolve().parents[2]


class PackageTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.base=Path(tmp.name);self.root=self.base/'repo';self.out=self.base/'context'
        for n in ['Backend/Flask/'+m+'.py' for m in stage.MODULES]+['Backend/LiteKeyWorker/'+p for p in stage.PROFILE]:
            dest=self.root/n;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/n,dest)
        subprocess.run(['git','init','-q',str(self.root)],check=True)
        subprocess.run(['git','-C',str(self.root),'-c','user.name=Synthetic','-c','user.email=test@example.invalid',
            'commit','--allow-empty','-qm','fixture'],check=True)
    def build(self):return stage.stage(self.root,self.out)
    def verify(self):
        env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1')
        env['PYTHONPATH']=os.pathsep.join(p for p in env.get('PYTHONPATH','').split(os.pathsep)
            if Path(p).is_absolute() and 'python-deps' in p)
        return subprocess.run([sys.executable,str(self.out/'verify_key_package.py')],cwd=self.out,env=env,
            capture_output=True,text=True,timeout=30)
    def test_actual_import_closure(self):stage.validate_imports(ROOT)
    def test_independent_package_no_model_secret_or_web_routes(self):
        for n in ('models/weight.onnx','upload.jpg','.env','app.py','lite_native_startup.py','secret.p8'):
            p=self.root/'Backend/Flask'/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('not for worker')
        m=self.build();self.assertEqual(len(m['files']),37);self.assertFalse(m['release_approved'])
        r=self.verify();self.assertEqual(r.returncode,0,r.stderr)
        self.assertFalse(json.loads(r.stdout)['application_started']);self.assertFalse(json.loads(r.stdout)['cloud_access'])
    def test_new_nested_dependency_blocks_staging(self):
        p=self.root/'Backend/Flask';(p/'unknown.py').write_text('x=1')
        with (p/'lite_key_startup.py').open('a') as f:f.write('\ndef unused():\n import unknown\n')
        with self.assertRaisesRegex(ValueError,'outside allowlist'):self.build()
        self.assertFalse(self.out.exists())
    def test_missing_module_blocks_staging(self):
        (self.root/'Backend/Flask/lite_store_locations.py').unlink()
        with self.assertRaises(ValueError):self.build()
        self.assertFalse(self.out.exists())
    def test_symlink_and_existing_context_refused(self):
        p=self.root/'Backend/Flask/lite_key_startup.py';p.unlink();p.symlink_to(ROOT/'Backend/Flask/lite_key_startup.py')
        with self.assertRaises(ValueError):self.build()
        p.unlink();shutil.copyfile(ROOT/'Backend/Flask/lite_key_startup.py',p)
        self.build();before=(self.out/'build-context-manifest.json').read_bytes()
        with self.assertRaises(ValueError):self.build()
        self.assertEqual((self.out/'build-context-manifest.json').read_bytes(),before)
    def test_tampered_or_extra_file_fails_before_import(self):
        self.build();p=self.out/'lite_key_startup.py';old=p.read_bytes();p.write_bytes(old+b'\n')
        self.assertNotEqual(self.verify().returncode,0);p.write_bytes(old)
        (self.out/'unexpected.txt').write_text('unexpected');self.assertNotEqual(self.verify().returncode,0)
    def test_profile_is_nonroot_pinned_private_factory(self):
        s=(ROOT/'Backend/LiteKeyWorker/Dockerfile').read_text()
        for v in ('@sha256:', '--require-hashes', 'pip check', 'USER woundai', 'lite_key_startup:application()'):self.assertIn(v,s)
        self.assertNotIn('app:app',s);self.assertNotIn('LITE_KEY_CONFIG=',s)
        self.assertNotIn('onnxruntime==',(ROOT/'Backend/LiteKeyWorker/requirements.lock').read_text())


if __name__=='__main__':unittest.main()
