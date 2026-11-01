"""Actual indexed grants, AES/KMS double, service operations and worker restart.

Generated captures only. Can replay unchanged against d6f5bdb's modules; no
network, live key deletion, cloud resources, quotas or personal data are used.
"""
import argparse
import hashlib
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from PIL import Image
from lite_attest_state import SQLiteStateStore, StateUnavailable
from lite_service_operations import ServiceOperations, RasterInference
from lite_service_disposition import ServiceResultDisposition
from test_lite_account_lifecycle import Fixture
from test_lite_grant_key_custody import KeyFixture
from test_lite_subscription_grace import rows
import test_lite_native_purchase as native


class ProgressFixture:
    def __init__(self, host, *, attach=True):
        original=Fixture.__init__
        with patch.object(Fixture, '__init__', lambda s,h:original(s,h,service_catalog=True)):
            self.f=KeyFixture(host)
        self.f.login();self.person=self.f.person;self.d=self.f.d
        self.root=self.f.f.h.f.root;self.ledger=self.f.f.h.f.ledger;self.calls=0;self.op='1'*32
        out=io.BytesIO();Image.new('RGB',(32,32),(180,30,30)).save(out,format='JPEG')
        self.capture=out.getvalue();self.image_hash=hashlib.sha256(self.capture).hexdigest()
        def infer(rgb):
            self.calls+=1;mask=np.zeros(rgb.shape[:2],dtype=np.uint8);mask[5:25,5:25]=1;return mask
        self.ops=ServiceOperations(ledger=self.ledger,accounts=self.f.b.registry,store=self.store('results'),
            inference=RasterInference(infer,lambda _: (False,None)),model_version='a'*64,clock=self.f.f.now)
        self.ledger.initialize_day('apple-'+self.person,self.f.f.now()//86400,
            baseline_successes=0,baseline_attempts=0,manifest_sha256='b'*64)
        self.results=ServiceResultDisposition(operations=self.ops,lifecycle=self.f.lifecycle,
            store=self.store('result-disposition'),audit_store=self.store('result-disposition-audit'))
        self.progress=None
        if attach:
            from lite_disposition_progress import DispositionProgress
            self.progress=DispositionProgress(deletion=self.d,results=self.results,cipher=self.f.cipher,
                store=self.store('progress'),audit_store=self.store('progress-audit'))
            self.d.attach_disposition(self.progress)
    def store(self,name):return SQLiteStateStore(self.root/(name+'.sqlite'))
    def complete(self):
        self.ops.prepare('device-one',self.op,self.image_hash)
        return self.ops.run('device-one',self.op,self.capture)
    def close(self):return self.d.begin('device-one','d'*32)
    def status(self):return self.d.status('device-one','d'*32)
    def advance(self,**kwargs):return self.progress.advance(self.person,**kwargs)
    def preserved(self):return [rows(s) for s in (self.ledger.store,self.f.b.registry.store,self.f.b.store)]


def run():
    native.legacy.LinksTests.setUpClass();host=unittest.TestCase()
    try:
        try:from lite_disposition_progress import DispositionProgress
        except ImportError:DispositionProgress=None
        f=ProgressFixture(host,attach=DispositionProgress is not None)
        f.complete();before=f.preserved();first=f.close()
        if f.progress is None:
            f.results.advance(f.person);f.d.advance(f.person)
            f.f.cipher.destroy_after_revocation(f.d,f.person)
        else:f.advance()
        scheduled=f.status()
        version=f.f.provider.version(f.f.cipher.scope,f.person)
        f.f.kms.destroy_now(version)
        if f.progress is None:f.f.cipher.destroy_after_revocation(f.d,f.person)
        else:f.advance()
        final=f.status();calls=len(f.f.kms.calls);again=f.status()
        assert before==f.preserved() and final==again and len(f.f.kms.calls)==calls
        assert not final['account_deleted'] and f.calls==1
        result=dict(schema='lite-disposition-progress/1',synthetic_only=True,
            coordinated=f.progress is not None,first=first,scheduled=scheduled,final=final,
            model_calls=f.calls,usage_identity_unchanged=before==f.preserved(),
            status_performs_no_provider_calls=len(f.f.kms.calls)==calls,account_deleted=False,
            real_kms_calls=0,real_apple_calls=0,real_cloud_cleanup=False)
        if f.progress is not None:
            assert scheduled['disposition']['grant_key_state']=='destroy_scheduled'
            assert final['disposition']['grant_key_state']=='unrecoverable'
            assert final['disposition']['service_state']=='current_sealed'
            assert len(final['disposition']['pending_lanes'])==4
        else:assert final['data_disposition']=='not_started'
        return result
    finally:host.doCleanups()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True,type=Path);a=p.parse_args()
    a.output.write_text(json.dumps(run(),indent=2)+'\n')
