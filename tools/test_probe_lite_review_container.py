"""Reproduce the external-script import failure without Docker or network."""
from pathlib import Path
import shutil,subprocess,sys,tempfile,unittest

ROOT=Path(__file__).resolve().parents[1]
class ExternalProbeTests(unittest.TestCase):
 def setUp(self):
  self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
  self.root=Path(self.temporary.name);self.app=self.root/'app';self.external=self.root/'verification'
  self.external.mkdir();shutil.copytree(ROOT/'Backend/LiteReview',self.app/'Backend/LiteReview',ignore=shutil.ignore_patterns('__pycache__'))
  shutil.copyfile(ROOT/'tools/probe_lite_review_container.py',self.external/'probe.py')
 def test_old_external_import_fails_even_when_workdir_is_app(self):
  old=self.external/'old.py';old.write_text('from Backend.LiteReview.runtime import portal,reader\n')
  result=subprocess.run([sys.executable,'-I','-B',str(old)],cwd=self.app,capture_output=True,text=True)
  self.assertNotEqual(result.returncode,0)
  self.assertIn("No module named 'Backend'",result.stderr)
 def test_fixed_probe_executes_factories_from_unrelated_workdir(self):
  result=subprocess.run([sys.executable,'-I','-B',str(self.external/'probe.py'),'--application-root',str(self.app)],cwd=self.root,capture_output=True,text=True,timeout=30)
  self.assertEqual(result.returncode,0,result.stderr)
  self.assertIn('real portal/reader factories',result.stdout)
 def test_wrong_application_root_is_not_silently_accepted(self):
  result=subprocess.run([sys.executable,'-I','-B',str(self.external/'probe.py'),'--application-root',str(self.root)],cwd=self.app,capture_output=True,text=True)
  self.assertNotEqual(result.returncode,0);self.assertIn('isolated application root missing',result.stderr)

if __name__=='__main__':unittest.main(verbosity=2)
