"""Sealed retirement-only package, checked in a clean Python process."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from stage_lite_retirement_candidate import stage, mapping, validate_imports
from lite_subscription_executor import canonical
from lite_usage_ledger import digest
from test_lite_shared_retirement_runner import RunnerFixture
import test_lite_native_purchase as native

ROOT=Path(__file__).resolve().parents[2]


class PackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):native.legacy.LinksTests.setUpClass()
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.context=self.root/'context'
        self.manifest=stage(ROOT,self.context)
        self.env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1')
        # Keep third-party dependency directory only; never resolve app modules
        # from the source checkout when checking the allowlisted package.
        entries=[p for p in sys.path if p and 'python-deps' in p]
        self.env['PYTHONPATH']=os.pathsep.join(entries)
    def command(self,*args):
        return subprocess.run([sys.executable,'-B',*args],cwd=self.context,env=self.env,
            capture_output=True,text=True,timeout=30)
    def test_isolated_imports_and_exact_file_hashes(self):
        p=self.command('verify_retirement_package.py');self.assertEqual(p.returncode,0,p.stderr)
        result=json.loads(p.stdout);self.assertTrue(result['passed']);self.assertFalse(result['credential_acquisition'])
    def test_missing_cli_pins_never_start_providers(self):
        p=self.command('lite_shared_retirement_runner.py');self.assertEqual(p.returncode,2)
        self.assertIn('required',p.stderr);self.assertNotIn('Traceback',p.stderr)
    def test_packaged_validate_only_accepts_real_synthetic_public_config(self):
        f=RunnerFixture(self);f.c['provider_budget'].update(not_before=int(time.time())-1,expires_at=int(time.time())+300)
        r=f.request('prepare');cp=self.root/'config.json';rp=self.root/'request.json'
        cp.write_bytes(canonical(f.c));rp.write_bytes(canonical(r))
        p=self.command('lite_shared_retirement_runner.py','--config',str(cp),'--config-sha256',digest(f.c),
            '--request',str(rp),'--request-sha256',digest(r),'--source','a'*40,'--image','b'*64,'--validate-only')
        self.assertEqual(p.returncode,0,p.stdout+p.stderr);result=json.loads(p.stdout)
        self.assertTrue(result['passed']);self.assertFalse(result['provider_access']);self.assertFalse(result['execution_authorized'])
    def test_tampered_package_fails(self):
        (self.context/'lite_shared_retirement_runner.py').write_text('raise Exception("changed")')
        p=self.command('verify_retirement_package.py');self.assertNotEqual(p.returncode,0)
    def test_extra_file_fails(self):
        (self.context/'unexpected.key').write_text('synthetic only')
        self.assertNotEqual(self.command('verify_retirement_package.py').returncode,0)
    def test_no_web_or_models_in_allowlist(self):
        names=set(mapping());self.assertIn('lite_shared_retirement_runner.py',names)
        for n in ('app.py','api_lite.py','lite_native_startup.py','lite_key_startup.py'):self.assertNotIn(n,names)
        validate_imports(ROOT)
    def test_existing_output_never_overwritten(self):
        with self.assertRaises(ValueError):stage(ROOT,self.context)
    def test_container_defaults_are_private_nonroot_and_hash_locked(self):
        text=(self.context/'Dockerfile').read_text()
        for s in ('@sha256:','--require-hashes','USER woundai','RUN --network=none','lite_shared_retirement_runner.py'):
            self.assertIn(s,text)
        self.assertNotIn('EXPOSE',text)


if __name__=='__main__':unittest.main()
