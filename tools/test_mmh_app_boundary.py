"""Exercise the real Flask application boundary in isolated child processes."""
import json
import os
import secrets
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
CHILD=r'''
import os
import secrets
import auth_users
import app as application
from flask_jwt_extended import create_access_token
org='mmhps20261007'
pw=secrets.token_urlsafe(24)
auth_users.upsert_user(org,'admin','admin',pw)
client=application.app.test_client()
login=client.post('/api/auth/login',json={'username':'admin','password':pw})
assert login.status_code==200, login.status_code
assert login.json['identity']==org+':admin'
headers={'Authorization':'Bearer '+login.json['access_token']}
assert client.get('/api/v1/users',headers=headers).status_code==200
with application.app.app_context():
    other=create_access_token(identity='other:admin',additional_claims={'org':'other','user':'admin','role':'admin'})
    otc=create_access_token(identity='other:admin',additional_claims={'typ':'otc','org':'other','user':'admin','role':'admin'})
assert client.get('/api/v1/users',headers={'Authorization':'Bearer '+other}).status_code==401
assert client.post('/api/auth/exchange',json={'code':otc}).status_code==401
assert client.post('/api/auth/login',json={'username':'other:admin','password':pw}).status_code==401
auth_users.upsert_user(org,'admin','admin',disabled=True)
assert client.get('/api/v1/users',headers=headers).status_code==401
print('MMH real-app isolation passed')
'''
class AppBoundaryTests(unittest.TestCase):
    def test_real_routes_enforce_org_and_disabled_tokens(self):
        with tempfile.TemporaryDirectory(prefix='mmh-app-') as tmp:
            env={k:v for k,v in os.environ.items() if not k.startswith(('WOUNDAI_','GOOGLE_','GCLOUD_','CLOUDSDK_'))
                 and k not in ('ADMIN_PASSWORD','JWT_SECRET_KEY','FLASK_SECRET_KEY','PYTHONPATH')}
            env.update(WOUNDAI_INSTITUTION_ORG='mmhps20261007', WOUNDAI_STORE='local',
                       WOUNDAI_FLYWHEEL_DIR=tmp+'/flywheel',WOUNDAI_RUNTIME_DIR=tmp,
                       PYTHONPATH=str(ROOT/'Backend'/'Flask'),PYTHONDONTWRITEBYTECODE='1',
                       GOOGLE_APPLICATION_CREDENTIALS=tmp+'/absent.json',CLOUDSDK_CONFIG=tmp+'/absent-gcloud',
                       FLASK_SECRET_KEY=secrets.token_urlsafe(32),JWT_SECRET_KEY=secrets.token_urlsafe(32))
            result=subprocess.run([sys.executable,'-B','-c',CHILD],cwd=tmp,env=env,
                                  capture_output=True,text=True,timeout=60)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn('MMH real-app isolation passed',result.stdout)

if __name__=='__main__':unittest.main(verbosity=2)
