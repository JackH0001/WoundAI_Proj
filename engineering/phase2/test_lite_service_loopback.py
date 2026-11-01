"""Real localhost HTTP bytes, signed synthetic device, no external service."""
import io,json,threading,unittest
import requests
from werkzeug.serving import make_server
from werkzeug.test import EnvironBuilder
import test_lite_service_http as fixture
from lite_service_http import PREFIX


class ServiceLoopback(unittest.TestCase):
    def test_prepare_run_readback_without_image_and_security_replay(self):
        h=fixture.ServiceHTTPTests();h.setUp();self.addCleanup(h.doCleanups)
        server=make_server('127.0.0.1',0,h.app);thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start();session=requests.Session();session.trust_env=False
        base='http://127.0.0.1:'+str(server.server_port)
        def post(kind,raw,ctype='application/json'):
            path=PREFIX+kind;headers=h.headers(path,raw,ctype);headers['Content-Type']=ctype
            return session.post(base+path,data=raw,headers=headers,timeout=5),headers
        try:
            prepare=json.dumps(dict(anon_id=h.owner,operation='1'*32,image_sha256=h.image_hash)).encode()
            r,_=post('prepare',prepare);self.assertEqual(r.status_code,200,r.text)
            builder=EnvironBuilder(method='POST',data=dict(anon_id=h.owner,operation='1'*32,image=(io.BytesIO(h.f.jpeg),'synthetic.jpg')))
            try:env=builder.get_environ();body=env['wsgi.input'].read();ctype=env['CONTENT_TYPE']
            finally:builder.close()
            saved,_=post('run',body,ctype);self.assertEqual(saved.status_code,200,saved.text)
            status=json.dumps(dict(anon_id=h.owner,operation='1'*32)).encode()
            replay,headers=post('status',status);self.assertEqual(replay.json(),saved.json())
            self.assertLess(len(status),200);self.assertEqual(h.model_calls,1)
            denied=session.post(base+PREFIX+'status',data=status,headers=headers,timeout=5)
            self.assertEqual(denied.status_code,401)
            self.assertEqual(h.quota()['used'],1)
        finally:
            session.close();server.shutdown();thread.join(5);server.server_close()
        self.assertFalse(thread.is_alive())


if __name__=='__main__':unittest.main()
