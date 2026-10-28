"""Candidate packaging excludes runtime/secret files and pins model artifacts."""
import importlib.util
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import sys
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
        for name in stager.FLASK_FILES:self.write(self.repo/'Backend/Flask'/name,b'# reviewed source\n')
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
        actual={p.relative_to(self.output).as_posix() for p in self.output.rglob('*') if p.is_file()}
        self.assertEqual(actual,set(manifest['files'])|{'build-context-manifest.json'})
        self.assertEqual(len(manifest['files']),len(stager.FLASK_FILES)+len(stager.VENDOR_FILES)+3)
        self.assertFalse(manifest['release_approved']);self.assertTrue(manifest['working_tree_dirty'])
        for name,item in manifest['files'].items():self.assertEqual(hashlib.sha256((self.output/name).read_bytes()).hexdigest(),item['sha256'])
    def test_real_runtime_imports_are_closed_over_the_allowlist(self):
        stager.validate_runtime_imports(ROOT)
        for missing in ('annotation_masks.py', 'tissue_mask_contract.py', 'medical_depth_geometry.py', 'review_service_profile.py', 'lite_reconsent.py'):
            with self.subTest(missing=missing), patch.object(stager, 'FLASK_FILES', tuple(n for n in stager.FLASK_FILES if n != missing)):
                with self.assertRaisesRegex(ValueError, 'missing from build allowlist'):
                    stager.validate_runtime_imports(ROOT)
    def test_unlisted_nested_runtime_import_stops_before_output(self):
        self.write(self.repo/'Backend/Flask/app.py', b'def handler():\n    from unreviewed import value\n')
        self.write(self.repo/'Backend/Flask/unreviewed.py', b'value = 1\n')
        with self.assertRaisesRegex(ValueError, 'missing from build allowlist'):
            self.run_stage()
        self.assertFalse(self.output.exists())
    def test_optional_commerce_guard_does_not_hide_other_imports(self):
        valid = ('def install_lite_attest(app, *, commerce=None):\n'
                 '    if commerce is not None:\n'
                 '        from lite_service_operations import ServiceOperations\n')
        module = self.repo/'Backend/Flask/lite_attest_http.py'
        self.write(self.repo/'Backend/Flask/lite_service_operations.py', b'# optional\n')
        module.write_text(valid)
        stager.validate_runtime_imports(self.repo)
        variants = [valid.replace('        from', '    from'),
                    valid.replace('is not None', 'is None'),
                    valid.replace('commerce=None', 'commerce=True'),
                    valid.replace('install_lite_attest', 'some_handler'),
                    valid + '    else:\n        from lite_service_operations import ServiceOperations\n']
        # The unguarded variant must remain syntactically valid.
        variants[0] = valid.replace('    if commerce is not None:\n', '').replace('        from', '    from')
        for source in variants:
            with self.subTest(source=source):
                module.write_text(source)
                with self.assertRaisesRegex(ValueError, 'missing from build allowlist'):
                    stager.validate_runtime_imports(self.repo)
        self.write(self.repo/'Backend/Flask/unreviewed.py', b'value = 1\n')
        module.write_text(valid + '        from unreviewed import value\n')
        with self.assertRaisesRegex(ValueError, 'missing from build allowlist'):
            stager.validate_runtime_imports(self.repo)

    def test_optional_purchase_guard_does_not_hide_unguarded_or_other_imports(self):
        module = self.repo/'Backend/Flask/lite_attest_http.py'
        valid = ('def install_lite_attest(app, *, purchases=None):\n'
                 '    if purchases is not None:\n'
                 '        from lite_native_purchase import NativePurchaseBridge\n')
        self.write(self.repo/'Backend/Flask/lite_native_purchase.py', b'# optional\n')
        module.write_text(valid);stager.validate_runtime_imports(self.repo)
        for source in [valid.replace('purchases=None','purchases=True'),valid.replace('is not None','is None'),
                       valid.replace('    if purchases is not None:\n','').replace('        from','    from')]:
            module.write_text(source)
            with self.assertRaisesRegex(ValueError,'missing from build allowlist'):stager.validate_runtime_imports(self.repo)

    def test_optional_identity_guard_does_not_hide_unguarded_or_other_imports(self):
        module = self.repo/'Backend/Flask/lite_attest_http.py'
        valid = ('def install_lite_attest(app, *, identity=None):\n'
                 '    if identity is not None:\n'
                 '        from lite_account_identity import AccountIdentityBridge\n')
        self.write(self.repo/'Backend/Flask/lite_account_identity.py', b'# optional\n')
        module.write_text(valid);stager.validate_runtime_imports(self.repo)
        for source in [valid.replace('identity=None','identity=True'),valid.replace('is not None','is None'),
                       valid.replace('    if identity is not None:\n','').replace('        from','    from')]:
            module.write_text(source)
            with self.assertRaisesRegex(ValueError,'missing from build allowlist'):stager.validate_runtime_imports(self.repo)

    def test_packaged_gate_starts_without_any_commerce_modules(self):
        staged = self.base/'isolated-runtime';staged.mkdir()
        for name in stager.FLASK_FILES:
            target = staged/name;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(ROOT/'Backend/Flask'/name,target)
        script = '''
import importlib.util, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from flask import Flask
from lite_attest_http import install_lite_attest
optional = ('lite_service_operations', 'lite_service_http', 'lite_usage_ledger',
            'lite_subscription_authority', 'lite_service_subscription_links', 'lite_native_purchase',
            'lite_subscription_runtime', 'lite_subscription_executor', 'lite_account_identity',
            'lite_apple_identity_provider', 'lite_apple_account_events')
assert all(importlib.util.find_spec(name) is None for name in optional)
app = Flask('staged-legacy')
install_lite_attest(app)
assert 'lite_attest_http' in app.extensions
assert not any(rule.rule.startswith('/api/v1/lite/service/') for rule in app.url_map.iter_rules())
assert app.test_client().post('/api/v1/lite/attest/challenge', json={}).status_code == 503
assert not any(name in sys.modules for name in optional)
# An incomplete opt-in image must fail startup, never silently fall back.
broken = Flask('invalid-opt-in')
try:
    install_lite_attest(broken, commerce=object())
except ModuleNotFoundError:
    assert 'lite_attest_http' not in broken.extensions
else:
    raise AssertionError('missing commerce dependencies accepted')
print('staged legacy gate started; optional modules absent; opt-in fails closed')
'''
        run = subprocess.run([sys.executable, '-I', '-c', script, str(staged)],
                             cwd=staged,capture_output=True,text=True,timeout=30)
        self.assertEqual(run.returncode,0,run.stdout+run.stderr)
        self.assertIn('staged legacy gate started',run.stdout)
    def test_invalid_python_source_stops_before_output(self):
        self.write(self.repo/'Backend/Flask/app.py', b'def invalid syntax!\n')
        with self.assertRaisesRegex(ValueError, 'invalid Python build source'):
            self.run_stage()
        self.assertFalse(self.output.exists())
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
