"""Exercise the shipped private CLI, independent of the repository import path."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import stage_lite_bootstrap_candidate as stage

ROOT = Path(__file__).resolve().parents[2]


class BootstrapPackageTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name); self.root = self.base/'repo'; self.out = self.base/'context'
        for name in stage.mapping().values():
            dest = self.root/name; dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT/name, dest)
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        subprocess.run(['git', '-C', str(self.root), '-c', 'user.name=Synthetic',
            '-c', 'user.email=test@example.invalid', 'commit', '--allow-empty', '-qm', 'fixture'], check=True)

    def build(self): return stage.stage(self.root, self.out)

    def process(self, script, *args):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
        env['PYTHONPATH'] = os.pathsep.join(p for p in env.get('PYTHONPATH', '').split(os.pathsep)
            if Path(p).is_absolute() and 'python-deps' in p)
        return subprocess.run([sys.executable, '-B', str(self.out/script), *args],
            cwd=self.out, env=env, capture_output=True, text=True, timeout=30)

    def test_actual_dependency_closure(self): stage.validate_imports(ROOT)

    def test_package_excludes_http_models_credentials_and_user_files(self):
        for n in ('models/weight.onnx', 'photo.jpg', '.env', 'app.py', 'secret.p8'):
            p = self.root/'Backend/Flask'/n; p.parent.mkdir(parents=True, exist_ok=True); p.write_text('excluded')
        m = self.build()
        self.assertEqual(len(m['files']), 45)
        self.assertFalse(m['release_approved'])
        self.assertEqual(m['origins']['requirements.lock'], 'Backend/LiteDisposition/requirements.lock')
        r = self.process('verify_bootstrap_package.py'); self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), dict(passed=True, files=45, application_started=False,
            cloud_access=False, models_packaged=False, credential_acquisition=False))

    def test_shipped_help_without_configuration_or_credentials(self):
        self.build(); r = self.process('lite_fresh_bootstrap_runner.py', '--help')
        self.assertEqual(r.returncode, 0, r.stderr); self.assertIn('--request-sha256', r.stdout)

    def test_shipped_missing_pins_refused_before_any_provider(self):
        self.build(); r = self.process('lite_fresh_bootstrap_runner.py')
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn('the following arguments are required', r.stderr)
        self.assertNotIn('ModuleNotFoundError', r.stderr)

    def test_shipped_malformed_input_returns_redacted_failure(self):
        self.build(); p = self.base/'public.json'; p.write_text('DO-NOT-ECHO')
        args = []
        for n in ('config', 'native', 'request'):
            args += ['--'+n, str(p), '--'+n+'-sha256', 'a'*64]
        r = self.process('lite_fresh_bootstrap_runner.py', *args, '--source', 'b'*40, '--image', 'c'*64)
        self.assertEqual(r.returncode, 1, r.stderr)
        result = json.loads(r.stdout); self.assertEqual(result['error_type'], 'ValueError')
        self.assertEqual(result['status'], 'stopped'); self.assertNotIn('DO-NOT-ECHO', r.stdout+r.stderr)
        self.assertNotIn('Traceback', r.stdout+r.stderr)

    def test_nested_dependency_not_silently_added(self):
        (self.root/'tools/unreviewed.py').write_text('x=1')
        p = self.root/'tools/lite_fresh_bootstrap_runner.py'
        p.write_text(p.read_text()+'\ndef future():\n import unreviewed\n')
        with self.assertRaisesRegex(ValueError, 'outside allowlist'): self.build()
        self.assertFalse(self.out.exists())

    def test_missing_module_blocks_staging(self):
        (self.root/'Backend/Flask/lite_native_schema.py').unlink()
        with self.assertRaises(ValueError): self.build()
        self.assertFalse(self.out.exists())

    def test_source_symlink_refused(self):
        p = self.root/'tools/lite_fresh_bootstrap_runner.py'; p.unlink()
        p.symlink_to(ROOT/'tools/lite_fresh_bootstrap_runner.py')
        with self.assertRaisesRegex(ValueError, 'symlink'): self.build()

    def test_existing_output_unchanged_on_restage(self):
        self.build(); before = (self.out/'build-context-manifest.json').read_bytes()
        with self.assertRaises(ValueError): self.build()
        self.assertEqual(before, (self.out/'build-context-manifest.json').read_bytes())

    def test_tampered_module_rejected_before_import(self):
        self.build(); p = self.out/'lite_fresh_bootstrap_runner.py'
        p.write_text('raise RuntimeError("MUST-NOT-IMPORT")\n')
        r = self.process('verify_bootstrap_package.py')
        self.assertNotEqual(r.returncode, 0); self.assertIn('package changed', r.stderr)
        self.assertNotIn('MUST-NOT-IMPORT', r.stderr)

    def test_extra_file_or_symlink_rejected_before_import(self):
        self.build(); p = self.out/'unlisted'; p.write_text('extra')
        self.assertNotEqual(self.process('verify_bootstrap_package.py').returncode, 0)
        p.unlink(); p.symlink_to(self.base/'nonexistent')
        r = self.process('verify_bootstrap_package.py')
        self.assertNotEqual(r.returncode, 0); self.assertIn('unsafe packaged link', r.stderr)

    def test_profile_is_one_shot_nonroot_without_server_or_default_pins(self):
        s = (ROOT/'Backend/LiteBootstrap/Dockerfile').read_text()
        for v in ('@sha256:', '--require-hashes', 'USER woundai', '--network=none',
            'ENTRYPOINT ["python", "-B", "lite_fresh_bootstrap_runner.py"]'): self.assertIn(v, s)
        for v in ('EXPOSE', 'gunicorn', 'LITE_NATIVE_CONFIG=', '--config '): self.assertNotIn(v, s)


if __name__ == '__main__': unittest.main()
