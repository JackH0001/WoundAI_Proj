"""Concurrent account CAS tests: real local files and fake GCS generations."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import secrets
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/"Backend"/"Flask"))
from account_versions import AccountConflict, EMPTY_REVISION, resolve, revision
from store import LocalStore, GcsStore
import auth_users
import api_users
import institution_context
from flask import Flask
from flask_jwt_extended import JWTManager, create_access_token
from google.api_core.exceptions import PreconditionFailed

ORG="mmhps20261007"


def row(parent=EMPTY_REVISION, user="dr01", role="physician", disabled=False):
    return dict(org=ORG,user=user,role=role,disabled=disabled,
                _account_cas=dict(parent=parent,nonce=secrets.token_hex(16)))


class Bucket:
    def __init__(self): self.data={}; self.lock=threading.Lock(); self.conditions=[]; self.barrier=None
    def blob(self,name): return Blob(self,name)


class Blob:
    def __init__(self,bucket,name): self.bucket=bucket;self.name=name
    def exists(self):
        with self.bucket.lock:return self.name in self.bucket.data
    def download_as_bytes(self):
        with self.bucket.lock:return self.bucket.data[self.name]
    def upload_from_string(self,data,*,content_type,if_generation_match):
        if self.bucket.barrier:self.bucket.barrier.wait(timeout=5)
        with self.bucket.lock:
            self.bucket.conditions.append(if_generation_match)
            if if_generation_match != 0: raise AssertionError("unsafe write")
            if self.name in self.bucket.data: raise PreconditionFailed("synthetic conflict")
            self.bucket.data[self.name]=data


def gcs(bucket):
    s=object.__new__(GcsStore);s.prefix="test";s._line_cache={};s._CACHE_MAX_LINES=10000
    s._bucket=Bucket();s._bucket_name="media";s._audit_bucket=Bucket();s._audit_bucket_name="audit"
    s._security_bucket=bucket;s._security_bucket_name="security"
    class Client:
        def list_blobs(self,name,*,prefix):
            assert name=="security"
            with bucket.lock:return [bucket.blob(k) for k in bucket.data if k.startswith(prefix)]
    s._client=Client()
    return s


class AccountVersionTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.local=LocalStore(tmp.name)

    def test_replay_is_independent_of_cas_object_order(self):
        a=row();b=row(revision(a),role="nurse");c=row(revision(b),disabled=True)
        result=resolve([json.dumps(x) for x in (c,a,b)])
        self.assertEqual(result[ORG+":dr01"],c)

    def test_legacy_root_preserved_and_transitioned(self):
        legacy=dict(org=ORG,user="dr01",role="physician")
        new=row(revision(legacy),disabled=True)
        self.assertEqual(resolve([json.dumps(legacy),json.dumps(new)])[ORG+":dr01"],new)

    def test_fork_orphan_and_mixed_old_writer_fail_closed(self):
        a=row();b=row();legacy=dict(org=ORG,user="dr01",role="nurse")
        for records in ((a,b),(row("f"*64),),(a,legacy)):
            with self.assertRaises(ValueError):resolve([json.dumps(x) for x in records])

    def race(self, stores):
        barrier=threading.Barrier(len(stores))
        def write(store):
            data=row();barrier.wait(timeout=5)
            try:store.append_user_version(data);return "saved"
            except AccountConflict:return "conflict"
        with ThreadPoolExecutor(max_workers=len(stores)) as pool:
            results=list(pool.map(write,stores))
        self.assertEqual(results.count("saved"),1)
        self.assertEqual(results.count("conflict"),len(stores)-1)
        self.assertEqual(len(resolve(stores[0].read_lines_fresh("users.jsonl"))),1)

    def test_local_eight_writers_one_winner(self): self.race([LocalStore(self.local.root) for _ in range(8)])

    def test_gcs_eight_writers_one_winner_security_bucket_only(self):
        bucket=Bucket();stores=[gcs(bucket) for _ in range(8)]
        bucket.barrier=threading.Barrier(8)
        self.race(stores)
        self.assertEqual(len(bucket.data),1)
        self.assertEqual(len(bucket.conditions),8)
        self.assertTrue(all(v==0 for v in bucket.conditions))
        self.assertFalse(stores[0]._bucket.data)

    def test_local_and_gcs_stale_cannot_reenable_disabled(self):
        for store in (self.local,gcs(Bucket())):
            a=row();store.append_user_version(a)
            disabled=row(revision(a),disabled=True);store.append_user_version(disabled)
            with self.assertRaises(AccountConflict):store.append_user_version(row(revision(a),role="nurse"))
            current=resolve(store.read_lines_fresh("users.jsonl"))[ORG+":dr01"]
            self.assertTrue(current["disabled"])
            self.assertEqual(len(store.read_lines_fresh("users.jsonl")),2)

    def test_new_revision_prevents_aba(self):
        a=row();b=row(revision(a),disabled=True);c=row(revision(b))
        self.assertNotEqual(revision(a),revision(c))

    def test_api_missing_or_stale_version_returns_409_without_write(self):
        with patch.object(auth_users,"BOUND_ORG",ORG),patch.object(institution_context,"BOUND_ORG",ORG),patch.object(auth_users,"_store",return_value=self.local):
            auth_users.upsert_user(ORG,"dr01","physician","synthetic-password")
            first=auth_users.list_users()[0]["revision"]
            app=Flask(__name__);app.config["JWT_SECRET_KEY"]="synthetic-"+"x"*40;JWTManager(app);app.register_blueprint(api_users.users_bp)
            with app.app_context():token=create_access_token(identity=ORG+":admin",additional_claims={"org":ORG,"role":"admin","user":"admin"})
            client=app.test_client();headers={"Authorization":"Bearer "+token}
            with patch.object(api_users.fw,"audit_intent"),patch.object(api_users.fw,"audit"):
                body=dict(org=ORG,user="dr01",role="nurse")
                self.assertEqual(client.post('/api/v1/users',json=body,headers=headers).status_code,409)
                body["expected_revision"]=first
                self.assertEqual(client.post('/api/v1/users',json=body,headers=headers).status_code,200)
                self.assertEqual(client.post('/api/v1/users',json=body,headers=headers).status_code,409)
            self.assertEqual(len(self.local.read_lines('users.jsonl')),2)

    def test_bridge_with_real_backend_account_create_disable_and_receipt(self):
        from Backend.AccessManager.accounts import AccountAdapter, account_snapshot
        with patch.object(auth_users,"BOUND_ORG",ORG),patch.object(institution_context,"BOUND_ORG",ORG),patch.object(auth_users,"_store",return_value=self.local):
            auth_users.upsert_user(ORG,"admin","admin","synthetic-password")
            app=Flask(__name__);app.config["JWT_SECRET_KEY"]="synthetic-"+"x"*40
            jwt_manager=JWTManager(app)
            jwt_manager.token_verification_loader(lambda h,c:institution_context.token_matches_institution(c,auth_users.get_user))
            app.register_blueprint(api_users.users_bp)
            with app.app_context():token=create_access_token(identity=ORG+":admin",additional_claims={"org":ORG,"role":"admin","user":"admin"})
            client=app.test_client();headers={"Authorization":"Bearer "+token}
            class Transport:
                def get(self,url,**kwargs):return self.wrap(client.get('/api/v1/users',headers=headers))
                def post(self,url,*,json,**kwargs):return self.wrap(client.post('/api/v1/users',headers=headers,json=json))
                @staticmethod
                def wrap(response):
                    class Reply:
                        status_code=response.status_code
                        def json(self):return response.get_json()
                    return Reply()
            adapter=AccountAdapter(Transport())
            with patch.object(api_users.fw,"audit_intent"),patch.object(api_users.fw,"audit"):
                payload=dict(action="create",user="dr01",role="physician",display_name="synthetic",disabled=False,
                             account_snapshot=account_snapshot(adapter.list_accounts()))
                saved=adapter.change(payload,actor=ORG+":admin")
                self.assertEqual(saved["status"],"account_readback_verified")
                self.assertEqual(auth_users.authenticate(ORG,"dr01",saved["generated_password"])[1],"ok")
                payload.update(action="update",disabled=True,account_snapshot=account_snapshot(adapter.list_accounts()))
                self.assertTrue(adapter.change(payload,actor=ORG+":admin")["changed"])
                self.assertEqual(auth_users.authenticate(ORG,"dr01",saved["generated_password"])[1],"disabled")


if __name__=="__main__":unittest.main()
