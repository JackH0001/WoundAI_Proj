"""Generated service result with real GCS-state adapter and local version store.

The same script can run against ca72302: the old seal survives, but historical
masks remain. No network, credentials, cloud object or personal capture is used.
"""
import argparse,base64,copy,hashlib,json,unittest
from pathlib import Path
from types import SimpleNamespace
from google.api_core.exceptions import BadRequest,NotFound,PreconditionFailed
import google_crc32c
from lite_attest_state import GCSStateStore
from test_lite_attest_state import FakeBucket,FakeBlob
import test_lite_service_disposition as disposition_fixture
import test_lite_native_purchase as native


class HistoryBucket(FakeBucket):
    def __init__(self):
        super().__init__();self.name='synthetic-lite-history';self.soft={};self._properties={}
        self.list_calls=[];self.delete_calls=[];self.soft_unavailable=False;self.page_cap=None
        self.force_more=False;self.mutate=lambda items:items;self.delete_hook=lambda b:None
        self.lost_reply=False;self.refuse_delete=False
    def reload(self,**kw):self.calls.append(('bucket_reload',self.name,kw))
    def blob(self,name,generation=None):return HistoryBlob(self,name,generation)
    def list_blobs(self,**kw):
        self.list_calls.append(kw)
        if kw.get('soft_deleted') and self.soft_unavailable:raise BadRequest('soft policy disabled')
        source=self.soft if kw.get('soft_deleted') else self.versions
        items=[]
        for (name,generation),raw in sorted(source.items()):
            if name.startswith(kw['prefix']):
                b=self.blob(name,generation);b.metadata(raw);items.append(b)
        total=len(items);cap=min(kw['max_results'],self.page_cap or kw['max_results'])
        items=self.mutate(items[:cap]);more=total>cap or self.force_more
        return SimpleNamespace(pages=iter([items]),next_page_token='continuation' if more else None)


class HistoryBlob(FakeBlob):
    def metadata(self,raw):
        self.size=len(raw);self.crc32c=base64.b64encode(google_crc32c.Checksum(raw).digest()).decode()
        self._properties={}
    def reload(self,**kw):
        b=self.bucket
        with b.lock:
            b.calls.append(('reload',self.name,dict(kw,selected_generation=self.generation)))
            if self.generation is None:
                if self.name not in b.live:raise NotFound('absent')
                self.generation,raw=b.live[self.name]
            else:
                if (self.name,self.generation) not in b.versions:raise NotFound('absent')
                raw=b.versions[(self.name,self.generation)]
            if kw.get('if_generation_match',self.generation)!=self.generation:raise PreconditionFailed('wrong version')
            self.metadata(raw)
    def delete(self,**kw):
        b=self.bucket;b.delete_calls.append(dict(name=self.name,generation=self.generation,**kw))
        b.delete_hook(self)
        if b.refuse_delete:raise TimeoutError('not committed')
        with b.lock:
            assert self.generation is not None and kw['if_generation_match']==self.generation
            k=(self.name,self.generation)
            if k not in b.versions:raise NotFound('already removed')
            if int((b._properties.get('softDeletePolicy') or {}).get('retentionDurationSeconds',0)):
                b.soft[k]=b.versions[k]
            del b.versions[k]
            if b.live.get(self.name,(None,))[0]==self.generation:del b.live[self.name]
        if b.lost_reply:raise TimeoutError('committed reply lost')


def run(*,soft=False):
    native.legacy.LinksTests.setUpClass();f=disposition_fixture.ServiceDispositionTests();f.setUp()
    try:
        b=HistoryBucket();b._properties['versioning']={'enabled':True}
        b.soft_unavailable=not soft
        f.ops.store=GCSStateStore(b,prefix='synthetic/results');f.d=f.worker()
        if soft:b._properties['softDeletePolicy']={'retentionDurationSeconds':'604800'}
        f.complete();f.close();f.advance()
        before=f.unchanged();raw_hash=hashlib.sha256(f.raw).hexdigest()
        name=f.ops.store._name(f.ops.key(f.subject,f.op));seal=b.live[name]
        old_before=len(b.versions)-1
        masks_before=sum(bool(json.loads(v).get('result')) for v in b.versions.values())
        try:from lite_service_history import ServiceResultHistory
        except ModuleNotFoundError as exc:
            if exc.name!='lite_service_history':raise
            ServiceResultHistory=None
        result=None
        if ServiceResultHistory is not None:
            audit=f.store('history-audit');jobs=f.store('history-jobs')
            policy=dict(v=1,id='synthetic-v1',bucket=b.name,prefix=f.ops.store.prefix)
            for _ in range(20):
                worker=ServiceResultHistory(disposition=f.d,store=jobs,audit_store=audit,policy=policy)
                result=worker.advance(f.person,f.op,limit=2,apply=True)
                if result['active_history_absent_at_check']:break
            assert result['active_history_absent_at_check'] and not result['audit_batch_pending']
        old_after=len(b.versions)-1
        assert b.live[name]==seal and f.calls==1 and before==f.unchanged()
        assert hashlib.sha256(f.raw).hexdigest()==raw_hash
        return dict(schema='lite-service-history/1',synthetic_only=True,history_worker_available=ServiceResultHistory is not None,
            old_active_versions_before=old_before,old_active_versions_after=old_after,
            old_mask_versions_before=masks_before,soft_versions_after=len(b.soft),result=result,
            model_calls=f.calls,quota_identity_unchanged=before==f.unchanged(),original_capture_sha256=raw_hash,
            current_seal_preserved=b.live[name]==seal,physical_backups_erased=False,account_deleted=False,
            real_gcs_calls=0,real_data_deleted=False)
    finally:f.doCleanups()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True,type=Path);a=p.parse_args()
    a.output.write_text(json.dumps(dict(active=run(),soft=run(soft=True)),indent=2)+'\n')
