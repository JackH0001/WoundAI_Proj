"""Exercise the offline plan using the owner App's shared-text format and enrollment runtime."""
import io
import json
import os
import plistlib
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import patch

import lite_owner_enrollment_command as command
import test_lite_owner_enrollment as enrollment_tests
from lite_attest_state import GCSStateStore
from lite_operational_materials import canonical, read_private, sha
from test_lite_attest_state import FakeBucket


class EnrollmentCommandTests(unittest.TestCase):
    def setUp(self):
        self.device = enrollment_tests.OwnerEnrollmentTests()
        self.device.setUp()
        self.addCleanup(self.device.doCleanups)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.ipa = self.root/'owner.ipa'
        self.key = self.device.e.key_id.hex().encode('ascii')
        self.native = canonical(self.device.config)
        self.info = dict(CFBundleIdentifier='com.woundai.lite', CFBundleVersion='33',
                         DTPlatformName='iphoneos', TFInternalTestingOnly=True, LiteOwnerReviewEnabled='YES',
                         LiteNativeSandboxOrigin='https://woundai-lite-native-sandbox-123456789.asia-east1.run.app')
        self.template = dict(schema='lite-owner-enrollment-template/1', source='3'*40, image='4'*64,
                             app_id='ABCDEFGHIJ.com.woundai.lite', key_id_sha256=sha(self.device.e.key_id),
                             **{k:self.device.plan[k] for k in ('service_account','starts','until','native_sha256')})
        self.write_ipa(self.info)

    def write_ipa(self, info):
        with zipfile.ZipFile(self.ipa,'w') as z:
            z.writestr(command.INFO_PATH, plistlib.dumps(info))
        self.template['ipa_sha256'] = sha(self.ipa.read_bytes())

    def prepare(self, *, key=None, native=None, now=None, pinned=None):
        data = canonical(self.template)
        return command.prepare(self.native if native is None else native, data, pinned or sha(data),
                               self.key if key is None else key, self.ipa,
                               self.device.now if now is None else now)

    def test_native_runtime_accepts_created_plan_and_replays_using_the_reviewed_objects(self):
        raw, scope = self.prepare()
        self.assertEqual(json.loads(raw), self.device.plan)
        self.assertEqual(scope['plan_sha256'], sha(raw))
        bucket = FakeBucket()
        def stores(dep, location):
            self.assertEqual((dep['service_account'], location[:2]), (scope['service_account'],
                             (scope['bucket'],scope['metageneration'])))
            return tuple(GCSStateStore(bucket,prefix=p) for p in location[2:])
        self.device.client = self.device.build(plan=json.loads(raw),stores=stores).test_client()
        proof = self.device.proof(self.device.start())
        first = self.device.client.post(command.REGISTER_PATH,json=proof)
        self.assertEqual(first.status_code,200)
        self.assertEqual(set(scope['objects']),set(bucket.live))
        self.assertEqual({c[1] for c in bucket.calls},set(scope['objects']))
        original = bucket.live[scope['objects'][0]]
        self.device.client = self.device.build(plan=json.loads(raw),stores=stores).test_client()
        again = self.device.client.post(command.REGISTER_PATH,json=proof)
        self.assertEqual((again.status_code,again.json),(200,first.json))
        self.assertEqual(original,bucket.live[scope['objects'][0]])
        self.assertEqual(set(scope['routes']),{command.CHALLENGE_PATH,command.REGISTER_PATH})

    def test_text_share_accepts_lf_crlf_and_no_ending(self):
        expected = self.prepare()
        for ending in [b'\n',b'\r\n',b'']:
            with self.subTest(ending=ending):
                self.assertEqual(self.prepare(key=self.key+ending),expected)

    def test_rejects_other_device_and_ambiguous_text(self):
        for key in [b'f'*64, self.key+b'\n\n', self.key.upper(), b' '+self.key, self.key+b'.json']:
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.prepare(key=key)

    def test_rejects_unpinned_inputs(self):
        with self.assertRaises(ValueError):
            self.prepare(pinned='0'*64)
        with self.assertRaises(ValueError):
            self.prepare(native=self.native+b' ')
        self.ipa.write_bytes(self.ipa.read_bytes()+b' ')
        with self.assertRaises(ValueError):
            self.prepare()

    def test_external_build_and_other_origins_cannot_prepare_owner_enrollment(self):
        variants = dict(TFInternalTestingOnly=False, LiteOwnerReviewEnabled='NO', DTPlatformName='iphonesimulator',
                        CFBundleIdentifier='com.woundai.app', CFBundleVersion='51',
                        LiteNativeSandboxOrigin='https://example.invalid')
        for name,bad in variants.items():
            with self.subTest(name=name):
                self.write_ipa(dict(self.info,**{name:bad}))
                with self.assertRaises(ValueError):
                    self.prepare()

    def test_pin_team_source_and_image_from_operator_template(self):
        original=self.template.copy()
        for k,v in dict(app_id='ZZZZZZZZZZ.com.woundai.lite',source='5'*40,image='6'*64).items():
            with self.subTest(k=k):
                self.template=dict(original,**{k:v})
                with self.assertRaises(ValueError):
                    self.prepare()

    def test_no_plan_extension_after_expiry(self):
        self.assertTrue(self.prepare(now=self.template['starts']-60)[0])
        with self.assertRaises(ValueError):
            self.prepare(now=self.template['until'])
        self.template['until']=self.template['starts']+901
        with self.assertRaises(ValueError):
            self.prepare()

    @unittest.skipUnless(os.name=='posix','operator private-file workflow requires POSIX')
    def test_cli_creates_private_outputs_and_checks_exact_saved_plan(self):
        for name,data in [('template.json',canonical(self.template)),('native.json',self.native),('device.txt',self.key)]:
            p=self.root/name
            p.write_bytes(data)
            p.chmod(0o600)
        plan=self.root/'plan.json'
        scope=self.root/'scope.json'
        common=['--native-config',str(self.root/'native.json'),'--template',str(self.root/'template.json'),
                '--template-sha256',sha(canonical(self.template)),'--ipa',str(self.ipa),
                '--reference',str(self.root/'device.txt')]
        with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()), patch.object(command.time,'time',return_value=self.device.now):
            make=['prepare',*common,'--output',str(plan)]
            report=['scope',*common,'--output',str(scope),'--plan',str(plan)]
            self.assertEqual(command.main(make),0)
            original=read_private(plan)
            self.assertEqual(plan.stat().st_mode & 0o777,0o600)
            self.assertEqual(command.main(make),1)
            self.assertEqual(read_private(plan),original)
            plan.write_bytes(original+b'\n')
            self.assertEqual(command.main(report),1)
            self.assertFalse(scope.exists())
            plan.write_bytes(original)
            self.assertEqual(command.main(report),0)
            self.assertEqual(json.loads(read_private(scope))['plan_sha256'],sha(original))
            self.assertEqual(json.loads(read_private(scope))['window_state'],'open')
            with patch.object(command.time,'time',return_value=self.template['until']):
                historical=self.root/'historical.json'
                self.assertEqual(command.main(['scope',*common,'--output',str(historical),'--plan',str(plan)]),0)
                reviewed=json.loads(read_private(historical))
                self.assertEqual((reviewed['window_state'],reviewed['until']),('expired',self.template['until']))
                late=self.root/'late.json'
                self.assertEqual(command.main(['prepare',*common,'--output',str(late)]),1)
                self.assertFalse(late.exists())
            self.assertNotIn(self.key.decode(),out.getvalue())
