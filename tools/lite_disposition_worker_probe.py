"""Whole-component rehearsal with generated captures and provider doubles only."""
import argparse,copy,hashlib,json,unittest
from pathlib import Path
from lite_attest_state import GCSStateStore
from lite_service_history import ServiceResultHistory
from lite_service_history_probe import HistoryBucket
from test_lite_research_history import HistoryFixture
from test_lite_subscription_grace import rows
import test_lite_native_purchase as native
try:from lite_disposition_worker import AccountDispositionWorker
except ModuleNotFoundError as e:
    if e.name!='lite_disposition_worker':raise
    AccountDispositionWorker=None


class WorkerFixture:
    def __init__(self,host):
        self.h=HistoryFixture(host);self.f=self.h.m.f;self.person=self.f.person
        self.service_bucket=HistoryBucket();self.f.ops.store=GCSStateStore(self.service_bucket,prefix='synthetic/results')
        self.service=ServiceResultHistory(disposition=self.f.results,store=self.f.store('worker-service-jobs'),
            audit_store=self.f.store('worker-service-audit'),
            policy=dict(v=1,id='synthetic-v1',bucket=self.service_bucket.name,prefix=self.f.ops.store.prefix))
        self.store=self.f.store('worker-jobs');self.audit=self.f.store('worker-audit');self.w=self.worker()
    def worker(self,**kw):
        return None if AccountDispositionWorker is None else AccountDispositionWorker(progress=self.f.progress,
            service_history=self.service,research_history=self.h.h,store=self.store,audit_store=self.audit,**kw)
    def ready(self):
        self.f.complete();self.h.seed(3);self.f.close()
    def finish(self,*,limit=2,max_calls=100):
        for i in range(max_calls):
            r=self.w.run(self.person,apply=True,max_steps=12,limit=limit,max_seconds=300)
            k=r['observations']['grant_key']
            if k and k['kms_state']=='DESTROY_SCHEDULED':self.f.f.kms.destroy_now(k['version'])
            self.w=self.worker()
            if all(x=='observed' for x in r['states'].values()):return r,i+1
        raise AssertionError('component observations did not finish')


def run():
    native.legacy.LinksTests.setUpClass();host=unittest.TestCase()
    try:
        f=WorkerFixture(host);f.ready();before=f.f.preserved();capture=hashlib.sha256(f.f.capture).hexdigest()
        old_research=len(f.h.bucket.history);old_service=len(f.service_bucket.versions)
        if f.w is None:
            f.f.progress.attach_research(f.h.m.w)
            for _ in range(24):
                result=f.f.advance(limit=2)
                if result['disposition']['grant_key_state']=='destroy_scheduled':
                    f.f.f.kms.destroy_now(f.f.f.provider.version(f.f.f.cipher.scope,f.person))
            state=None;calls=24
        else:
            state,calls=f.finish();result=f.f.status()
        # Both implementations keep originals and quota; only the new composed
        # worker drives the two previously detached history components.
        assert f.f.preserved()==before and hashlib.sha256(f.f.capture).hexdigest()==capture and f.f.calls==1
        n=len(f.f.f.g.calls),len(f.f.f.kms.calls),len(f.service_bucket.list_calls),len(f.h.bucket.version_lists)
        if f.w:
            f.w.status(f.person);assert n==(len(f.f.f.g.calls),len(f.f.f.kms.calls),len(f.service_bucket.list_calls),len(f.h.bucket.version_lists))
        return dict(schema='lite-disposition-worker/1',synthetic_only=True,composition_available=f.w is not None,
            original_research_versions=old_research,original_service_versions=old_service,
            research_old_active_remaining=len(f.h.bucket.history)-len(f.h.bucket.live),
            service_old_active_remaining=len(f.service_bucket.versions)-len(f.service_bucket.live),
            calls=calls,worker_status=state,public_receipt=result,model_calls=f.f.calls,
            original_capture_sha256=capture,quota_and_identity_unchanged=f.f.preserved()==before,
            status_provider_calls=0,live_gcs_apple_kms_calls=0,account_deleted=False,real_data_deleted=False)
    finally:host.doCleanups()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True,type=Path);a=p.parse_args()
    a.output.write_text(json.dumps(run(),indent=2)+'\n')
