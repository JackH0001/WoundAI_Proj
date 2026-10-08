"""Real loopback HTTP + Lite routes + temporary LocalStore; synthetic model only.

Not the deployed Cloud Run image, GCS transport, App Attest or model accuracy.
"""
import base64
import io
import json
import threading
import unittest
import uuid
from werkzeug.serving import make_server
from PIL import Image
from test_backend_http import LocalTestClient
import test_lite_quota as fixture

class LiteHTTPLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.LiteQuotaTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        app=self.f.client.application
        run_id=uuid.uuid4().hex
        @app.route('/api/health')
        def health(): return {'status':'ok'}
        @app.after_request
        def marker(response):
            response.headers['X-WoundAI-Local-Test-Run']=run_id
            return response
        self.server=make_server('127.0.0.1',0,app)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.client=LocalTestClient('http://127.0.0.1:'+str(self.server.server_port),run_id)
        self.addCleanup(self.client.session.close)
        self.client.verify_server()
        self.aid='http-'+run_id

    def stop(self):
        self.server.shutdown();self.thread.join(timeout=5);self.server.server_close()
        self.assertFalse(self.thread.is_alive(),'HTTP server did not stop')

    def segment(self,depth=None):
        data=dict(anon_id=self.aid,research_consent='true',depth_format='png16_mm',depth_scale='0.001')
        if depth is None:
            out=io.BytesIO();Image.new('I;16',(8,6),300).save(out,'PNG');depth=out.getvalue()
        out=io.BytesIO();Image.new('L',(8,6),255).save(out,'PNG')
        data.update(depth_map_png=base64.b64encode(depth).decode(),depth_conf_png=base64.b64encode(out.getvalue()).decode())
        return self.client.request('POST','/api/v1/lite/segment',data=data,files={'image':('synthetic.jpg',self.f.jpeg,'image/jpeg')})

    def body(self,image,area=1):
        return dict(anon_id=self.aid,image_id=image,revision=1,research_consent=True,payload_json=json.dumps(dict(
            polygons=[[[10,10],[39,10],[39,39]]],image_w=64,image_h=64,surface_cm2=area,projected_cm2=area,
            source='manual',consent_version='2026-10-03.1',wound_id=str(uuid.UUID(int=1)),wound_side='right',wound_site='foot_dorsum')))

    def revision(self,body): return self.client.request('POST','/api/v1/lite/annotation/revision',json=body)

    def test_upload_revision_retry_withdraw_and_stale_upload(self):
        r=self.segment();self.assertEqual(r.status_code,200)
        value=r.json();image=value['image_id'];receipt=value['storage_receipt']
        self.assertEqual(receipt['depth'],'stored');self.assertEqual(receipt['validity_mask'],'stored')
        self.assertEqual(receipt['rgbd_validation'],'not_performed')
        body=self.body(image);first=self.revision(body);self.assertEqual(first.status_code,200)
        repeat=self.revision(body);self.assertEqual(repeat.status_code,200);self.assertEqual(first.json(),repeat.json())
        rows=fixture.lite._lite_label_rows_fresh();self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['surface_cm2'],1)
        r=self.client.request('DELETE','/api/v1/lite/data/'+self.aid);self.assertEqual(r.status_code,200)
        for suffix in ('.jpg','.json','.depth.png','.conf.png'):
            self.assertFalse(self.f.store.exists('lite/'+self.aid+'/'+image+suffix))
        self.assertEqual(self.revision(body).status_code,410)
        self.assertEqual(self.segment().status_code,410)

    def test_revision_conflict_does_not_overwrite(self):
        image=self.segment().json()['image_id']
        self.assertEqual(self.revision(self.body(image,1)).status_code,200)
        self.assertEqual(self.revision(self.body(image,2)).status_code,409)
        rows=fixture.lite._lite_label_rows_fresh();self.assertEqual(len(rows),1);self.assertEqual(rows[0]['surface_cm2'],1)

    def test_invalid_depth_never_claims_complete_storage(self):
        r=self.segment(b'invalid png');self.assertEqual(r.status_code,200)
        self.assertEqual(r.json()['storage_receipt']['depth'],'rejected')
        image=r.json()['image_id'];self.assertFalse(self.f.store.exists('lite/'+self.aid+'/'+image+'.depth.png'))

if __name__=='__main__':unittest.main()
