import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import medical_image_build as m

COMMIT='a'*40
MANIFEST='b'*64
ID='12345678-1234-1234-1234-123456789abc'
DIGEST='sha256:'+'c'*64
ROOT=Path(__file__).resolve().parents[1]


def good():
    b=m.configuration(COMMIT,MANIFEST)
    b.update(id=ID,projectId=m.PROJECT,status='SUCCESS',
        source={'storageSource':{'bucket':m.BUCKET,'object':'source/test.tgz','generation':'123'}},
        results={'images':[{'name':b['images'][0],'digest':DIGEST}]})
    b['sourceProvenance']={'resolvedStorageSource':copy.deepcopy(b['source']['storageSource'])}
    b['options'].update(pool={},workerRelease='legacy')
    for s in b['steps']:s.update(status='SUCCESS',timing={},pullTiming={})
    return b


class ImageBuildTests(unittest.TestCase):
    def test_success_uses_immutable_digest_and_artifact_readback(self):
        calls=[]
        def run(args):
            calls.append(args)
            if args[0]=='builds':return good()
            return {'image_summary':{'digest':DIGEST,'fully_qualified_digest':m.IMAGE_ROOT+'@'+DIGEST}}
        self.assertEqual(m.resolve(ID,COMMIT,MANIFEST,m.PROJECT,m.REGION,run),m.IMAGE_ROOT+'@'+DIGEST)
        self.assertEqual(len(calls),2)
        self.assertTrue(all(a[1] in ('describe','docker') for a in calls))

    def test_bad_inputs_refused_before_cloud(self):
        for args in [(ID,COMMIT,MANIFEST,'foreign',m.REGION),(ID,COMMIT,MANIFEST,m.PROJECT,'global'),
                     ('latest',COMMIT,MANIFEST,m.PROJECT,m.REGION),(ID,'HEAD',MANIFEST,m.PROJECT,m.REGION),
                     (ID,COMMIT,'wrong',m.PROJECT,m.REGION)]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                m.resolve(*args,run=lambda a:self.fail('cloud requested for invalid inputs'))

    def reject(self, mutate):
        b=good();mutate(b)
        with self.assertRaises(ValueError):m.validate_build(b,ID,COMMIT,MANIFEST)

    def test_non_success(self):
        for state in ('WORKING','FAILURE','CANCELLED','UNKNOWN',None):
            with self.subTest(state=state):self.reject(lambda b:b.update(status=state))
    def test_wrong_builder(self):self.reject(lambda b:b.update(serviceAccount='projects/x/serviceAccounts/default'))
    def test_wrong_project(self):self.reject(lambda b:b.update(projectId='foreign'))
    def test_wrong_build_id(self):self.reject(lambda b:b.update(id='different'))
    def test_changed_recipe(self):self.reject(lambda b:b['steps'][1]['args'].append('--build-arg=BAD=1'))
    def test_extra_step(self):self.reject(lambda b:b['steps'].append({'name':'unexpected'}))
    def test_changed_commit_or_manifest(self):
        for c,h in [('d'*40,MANIFEST),(COMMIT,'e'*64)]:
            with self.assertRaises(ValueError):m.validate_build(good(),ID,c,h)
    def test_runtime_bucket_refused(self):self.reject(lambda b:b['source']['storageSource'].update(bucket='woundai-flywheel-jackh001'))
    def test_unversioned_source_refused(self):self.reject(lambda b:b['source']['storageSource'].pop('generation'))
    def test_source_generation_mismatch(self):self.reject(lambda b:b['sourceProvenance']['resolvedStorageSource'].update(generation='456'))
    def test_secrets_refused(self):self.reject(lambda b:b.update(availableSecrets={'unexpected':'input'}))
    def test_options_cannot_inject_env(self):self.reject(lambda b:b['options'].update(env=['PYTHONOPTIMIZE=1']))
    def test_step_cannot_inject_env(self):self.reject(lambda b:b['steps'][0].update(env=['PYTHONOPTIMIZE=1']))
    def test_timeout_changed(self):self.reject(lambda b:b.update(timeout='3600s'))
    def test_extra_image_refused(self):self.reject(lambda b:b['results']['images'].append(copy.deepcopy(b['results']['images'][0])))
    def test_image_name_mismatch(self):self.reject(lambda b:b['results']['images'][0].update(name='other/image'))
    def test_mutable_digest_refused(self):self.reject(lambda b:b['results']['images'][0].update(digest='latest'))
    def test_artifact_mismatch_refused(self):
        with self.assertRaises(ValueError):
            m.resolve(ID,COMMIT,MANIFEST,m.PROJECT,m.REGION,lambda a:good() if a[0]=='builds' else {'image_summary':{'digest':DIGEST,'fully_qualified_digest':'wrong'}})
    def test_cloud_failure_propagates(self):
        with patch.object(m.subprocess,'run',return_value=subprocess.CompletedProcess([],1,'','denied')):
            with self.assertRaises(ValueError):m.cloud(['builds','describe',ID])
    def test_prepare_dirty_source_never_stages(self):
        with tempfile.TemporaryDirectory() as td,patch.object(m,'git',side_effect=[COMMIT,' M app.py']),patch.object(m,'stage') as stage:
            with self.assertRaises(ValueError):m.prepare(td,td,Path(td)/'out',COMMIT)
            stage.assert_not_called()
    def test_cloud_manifest_check_detects_changed_or_extra_bytes(self):
        import hashlib
        with tempfile.TemporaryDirectory() as td:
            ctx=Path(td)/'context';ctx.mkdir();(ctx/'app.py').write_bytes(b'original')
            d={'git_head':COMMIT,'working_tree_dirty':False,'files':{'app.py':{'bytes':8,'sha256':hashlib.sha256(b'original').hexdigest()}}}
            path=ctx/'build-context-manifest.json';path.write_text(json.dumps(d))
            manifest=hashlib.sha256(path.read_bytes()).hexdigest()
            script=m.configuration(COMMIT,manifest)['steps'][0]['args'][1]
            import sys
            def verify():return subprocess.run([sys.executable,'-c',script],cwd=td,capture_output=True).returncode
            self.assertEqual(verify(),0)
            (ctx/'app.py').write_bytes(b'modified');self.assertNotEqual(verify(),0)
            (ctx/'app.py').write_bytes(b'original');(ctx/'patient.jpg').write_bytes(b'not allowed')
            self.assertNotEqual(verify(),0)
            (ctx/'patient.jpg').unlink();d['working_tree_dirty']=True;path.write_text(json.dumps(d))
            self.assertNotEqual(verify(),0)

    def test_no_implicit_source_deploy(self):
        for name in ('deploy_cloudrun.ps1','deploy_demo_candidate.ps1'):
            s=(ROOT/'Backend/Flask'/name).read_text(encoding='utf-8-sig')
            lines='\n'.join(l for l in s.splitlines() if not l.lstrip().startswith('#'))
            self.assertNotIn('--source ',lines)
            self.assertIn('--image $VerifiedImage',lines)
            self.assertLess(lines.index('$VerifiedImage = Get-VerifiedMedicalImage'),lines.index('Invoke-GCloud run deploy'))
            self.assertIn('../../tools/resolve_medical_image.ps1',lines)
    def test_powershell_resolver_gate(self):
        shell=shutil.which('pwsh')
        if not shell:self.skipTest('PowerShell unavailable')
        with tempfile.TemporaryDirectory() as td:
            helper=ROOT/'tools/resolve_medical_image.ps1'
            script=Path(td)/'check.ps1'
            script.write_text(". '"+str(helper).replace("'","''")+"'\n"+r'''
function python { if ($args[1] -notlike "*/tools/medical_image_build.py" -and $args[1] -notlike "*\tools\medical_image_build.py") { throw "wrong resolver path" }; $global:LASTEXITCODE = $script:rc; Write-Output $script:answer }
$script:rc=0
$script:answer='''+"'"+m.IMAGE_ROOT+'@'+DIGEST+"'"+r'''
$arg=@{ProjectId='woundai-jackh001';Region='asia-east1';GitCommit=('a'*40);BuildId='12345678-1234-1234-1234-123456789abc';BuildManifestSha256=('b'*64)}
if ((Get-VerifiedMedicalImage @arg) -cne $script:answer) {throw 'good result rejected'}
$script:rc=1
try {Get-VerifiedMedicalImage @arg;throw 'unsafe accepted'} catch {if ($_.Exception.Message -eq 'unsafe accepted') {throw}}
$script:rc=0;$script:answer='wrong/image:latest'
try {Get-VerifiedMedicalImage @arg;throw 'unsafe accepted'} catch {if ($_.Exception.Message -eq 'unsafe accepted') {throw}}
$arg.BuildId=''
try {Get-VerifiedMedicalImage @arg;throw 'unsafe accepted'} catch {if ($_.Exception.Message -eq 'unsafe accepted') {throw}}
Write-Output 'GATES_OK'
''')
            env=dict(os.environ,PSModuleAnalysisCachePath=str(Path(td)/'module-cache'))
            r=subprocess.run([shell,'-NoProfile','-File',str(script)],capture_output=True,text=True,env=env)
            self.assertEqual(r.returncode,0,r.stdout+r.stderr);self.assertIn('GATES_OK',r.stdout)

if __name__=='__main__':unittest.main(verbosity=2)
