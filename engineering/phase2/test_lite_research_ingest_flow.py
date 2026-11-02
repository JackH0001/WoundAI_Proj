"""Native login -> one inference -> independent research receipt, synthetic only."""
import hashlib
import io
import json
import unittest
from unittest.mock import patch
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from lite_attest_http import install_lite_attest
from lite_attest_state import SQLiteStateStore
from lite_fenced_store import FencedPrivacy
from lite_research_issuer import LinkSigner
from lite_research_capture import configured_receiver, PATH
from lite_raw_depth_store import unpack
from werkzeug.test import EnvironBuilder
import test_lite_native_backend as native_fixture
import test_lite_raw_depth_http as raw_fixture
import test_lite_fenced_objects as object_fixture


class ResearchIngestFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): native_fixture.NativeBackendTests.setUpClass()

    def setUp(self):
        self.n=native_fixture.NativeBackendTests(); self.n.setUp(); self.addCleanup(self.n.doCleanups)
        self.bucket=object_fixture.Bucket(); self.keys=[Ed25519PrivateKey.generate() for _ in range(2)]
        self.signers=[]
        for i,role in enumerate(('native','research')):
            self.signers.append(LinkSigner(role=role,scope=self.n.backend.deletion.scope,bucket=self.bucket.name,
                private_key=self.keys[i].private_bytes_raw(), native_key=self.keys[0].public_key().public_bytes_raw(),
                research_key=self.keys[1].public_key().public_bytes_raw(), clock=self.n.h.clock))
        self.n.client=self.n.backend.create_app(link_signer=self.signers[0],research_source_enabled=True).test_client()
        self.r=raw_fixture.RawDepthHTTPTests()
        def install(app,**kw):
            self.privacy=FencedPrivacy(SQLiteStateStore(self.n.h.release.n.f.root/'research-flow.sqlite'),self.bucket)
            kw['budget'].clock=self.n.h.clock
            self.receiver=configured_receiver({'LITE_RESEARCH_CAPTURE_ENABLED':'1'},signer=self.signers[1],
                privacy=self.privacy,request_budget=kw['budget'])
            return install_lite_attest(app,**(kw|dict(privacy=self.privacy, research_capture=self.receiver)))
        with patch.object(raw_fixture.http_fixture,'install_lite_attest',side_effect=install): self.r.setUp()
        self.addCleanup(self.r.doCleanups)
        self.capture='fe000000-0000-4000-8000-000000000001'
        self.fields=dict(research=self.r.h.owner,consent_epoch='legacy',consent_version='2026-10-16.1',
            capture_id=self.capture,image_sha256=self.n.image_hash,operation=f'{1:032x}')

    def upload(self,ticket):
        metadata=dict(self.r.f.m,schema='lite.rgbd/2',capture_time='host_wall_clock',capture_id=self.capture,
            rgb_sha256=self.n.image_hash,captured_at='2023-11-14T22:13:20.000Z',
            capture_time_source='photo_callback_wall_clock')
        builder=EnvironBuilder(method='POST',data=dict(anon_id=self.r.h.owner,research_consent='true',
            consent_version='2026-10-16.1',consent_epoch='legacy',source_ticket=json.dumps(ticket),
            raw_depth_metadata=json.dumps(metadata),image=(io.BytesIO(self.n.f.jpeg),'capture.jpg'),
            raw_depth=(io.BytesIO(self.r.f.raw),'depth.f32')))
        try:
            env=builder.get_environ(); return self.r.h.post_signed(PATH,env['wsgi.input'].read(),env['CONTENT_TYPE'])
        finally: builder.close()

    def test_two_authenticated_origins_keep_one_inference_and_quota_after_research_replay(self):
        self.assertNotEqual(self.n.owner,self.r.h.owner)
        denied=self.n.post('service/research-source',**self.fields)
        self.assertEqual(denied.status_code,401,denied.json)
        self.n.login(); original=self.n.complete(); self.assertEqual(original.status_code,200,original.json)
        quota=self.n.quota(); self.assertEqual(quota['used'],1)
        ticket=self.n.post('service/research-source',**self.fields)
        self.assertEqual(ticket.status_code,200,ticket.json)
        first=self.upload(ticket.json); self.assertEqual(first.status_code,200,first.json)
        before=dict(self.bucket.live)
        again=self.upload(ticket.json); self.assertEqual(first.json,again.json); self.assertEqual(before,self.bucket.live)
        store=self.privacy.media(self.r.h.owner); iid=first.json['image_id']
        packet=unpack(store.get_blob('lite_raw/'+self.r.h.owner+'/'+iid+'.rgbd'))
        self.assertEqual(packet['jpeg'],self.n.f.jpeg); self.assertEqual(packet['raw_depth'],self.r.f.raw)
        self.assertEqual(self.n.json_request('status').json,original.json)
        self.assertEqual(self.n.quota(),quota); self.assertEqual(self.n.h.model_calls,1)
        self.assertEqual(len(store.read_lines_fresh('lite_index.jsonl')),1)
        withdrawal=self.r.delete(); self.assertEqual(withdrawal.status_code,200,withdrawal.json)
        self.assertEqual(self.upload(ticket.json).status_code,410)
        self.assertEqual(self.n.json_request('status').json,original.json)
        self.assertEqual(self.n.quota(),quota); self.assertEqual(self.n.h.model_calls,1)

    def test_default_off_and_wrong_configurations_fail_closed(self):
        self.assertIsNone(configured_receiver({},signer=None,privacy=None,request_budget=None))
        for value,signer in [('yes',self.signers[1]),('1',None),('1',self.signers[0])]:
            with self.subTest(value=value,signer=signer),self.assertRaises(ValueError):
                configured_receiver({'LITE_RESEARCH_CAPTURE_ENABLED':value},signer=signer,
                    privacy=self.privacy,request_budget=self.n.h.budget)
        self.assertEqual(self.n.backend.create_app().test_client().post('/api/v1/lite/service/research-source').status_code,404)
        with self.assertRaises(ValueError): self.n.backend.create_app(research_source_enabled=True)


if __name__=='__main__': unittest.main()
