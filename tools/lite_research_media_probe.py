"""Same synthetic current-media experiment on parent and candidate source.

The parent account worker only retires admission. Its existing withdrawal helper
is measured separately, not misrepresented as automatic account deletion.
No credentials, production objects, model or provider calls.
"""
import argparse
import copy
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from lite_account_research_closure import AccountResearchClosure
from lite_disposition_progress_probe import ProgressFixture
from lite_fenced_objects import ObjectRef, KINDS, GenerationConflict
from lite_fenced_store import FencedPrivacy
from lite_reconsent import Reconsent
import test_lite_fenced_objects as objects
import test_lite_native_purchase as native


class ProbeBucket(objects.Bucket):
    def __init__(self):super().__init__();self.list_requests=[]
    def list_blobs(self,*,prefix,page_token=None,page_size=None,max_results=None,timeout=10,retry=None):
        with self.lock:names=sorted(n for n in self.live if n.startswith(prefix))
        offset=int(page_token or 0);page=names[offset:offset+max_results] if max_results is not None else names
        self.list_requests.append(dict(limit=max_results,returned=len(page),paged=page_size is not None))
        values=[SimpleNamespace(name=n) for n in page]
        if page_size is None:return values
        end=offset+len(page)
        return SimpleNamespace(pages=iter([values]),next_page_token=str(end) if end<len(names) else None)


def run():
    try:from lite_research_media_disposition import ResearchMediaDisposition
    except ModuleNotFoundError as error:
        if error.name!='lite_research_media_disposition':raise
        ResearchMediaDisposition=None
    native.legacy.LinksTests.setUpClass();host=unittest.TestCase()
    try:
        f=ProgressFixture(host);owner='a'*32;f.f.login(owner);bucket=ProbeBucket()
        research=Reconsent(FencedPrivacy(f.store('probe-research'),bucket));privacy=research.privacy
        for n in range(73):
            t=privacy.begin_write(owner);ref=ObjectRef(owner,sorted(KINDS)[n%7],f'{n+1:016x}')
            privacy.manifest.write_registered(t,privacy.objects,ref,b'generated-only-'+str(n).encode());privacy.finish_write(t)
        other=ObjectRef('e'*32,'image','c'*16)
        privacy.objects.write(privacy.objects.pin(other),b'unrelated-synthetic-payload')
        first=ObjectRef(owner,sorted(KINDS)[0],f'{1:016x}');pending=privacy.objects.pin(first)
        original=copy.deepcopy(bucket.history);other_before=copy.deepcopy(bucket.live[other.name])
        f.close();identity=f.preserved();providers=copy.deepcopy((f.f.g.calls,f.f.kms.calls))
        closure=AccountResearchClosure(deletion=f.d,research=research,
            store=f.store('probe-closure'),audit_store=f.store('probe-closure-audit'))
        while not closure.advance(f.person)['scan_finished']:pass
        admitted_live=sum(v[2]['state']=='live' for k,v in bucket.live.items() if '/'+owner+'/' in k)
        host.assertEqual(admitted_live,73)
        # A pre-pinned write from the original image is retained to verify that
        # replay cannot replace a seal after cleanup; it is not an untracked new write.
        uploads=lambda:sum(x[0]=='upload' for x in bucket.calls)
        counts=[];lists=[]
        if ResearchMediaDisposition is None:
            a,z=uploads(),len(bucket.list_requests);receipt=privacy.complete_withdrawal(owner)
            counts.append(uploads()-a);lists.append(len(bucket.list_requests)-z)
            complete=False;mode='existing_withdrawal_helper_separately_invoked'
        else:
            jobs,audit=f.store('probe-media-jobs'),f.store('probe-media-audit')
            for _ in range(100):
                worker=ResearchMediaDisposition(closure=closure,store=jobs,audit_store=audit)
                a,z=uploads(),len(bucket.list_requests)
                receipt=worker.advance(f.person,limit=4,apply=True)
                counts.append(uploads()-a);lists.append(len(bucket.list_requests)-z)
                if receipt['scan_finished']:break
            host.assertTrue(receipt['scan_finished']);host.assertLessEqual(max(counts),4);host.assertLessEqual(max(lists),1)
            before=copy.deepcopy(bucket.history);host.assertEqual(receipt,worker.advance(f.person,limit=4,apply=True))
            host.assertEqual(before,bucket.history);complete=receipt['account_deleted'];mode='bounded_private_worker_restarted_each_call'
        with host.assertRaises(GenerationConflict):privacy.objects.write(pending,b'late-old-replacement')
        host.assertEqual(other_before,bucket.live[other.name]);host.assertEqual(identity,f.preserved())
        host.assertEqual(providers,(f.f.g.calls,f.f.kms.calls));host.assertEqual(f.calls,0)
        host.assertTrue(all(bucket.history[k]==v for k,v in original.items()))
        host.assertTrue(all(v[1]==b'' and v[2]['state']=='sealed' for k,v in bucket.live.items() if '/'+owner+'/' in k))
        return dict(schema='lite-research-media-probe/1',synthetic_only=True,implementation_present=ResearchMediaDisposition is not None,
            mode=mode,original_current_objects=73,current_objects_after_admission_only=admitted_live,
            calls=len(counts),max_seals_per_call=max(counts),max_list_requests_per_call=max(lists),
            max_returned_page=max(r['returned'] for r in bucket.list_requests),list_requests=bucket.list_requests,
            current_payloads_remaining=0,original_generations_preserved=True,unrelated_owner_unchanged=True,
            identity_and_quota_unchanged=True,late_old_write_rejected=True,account_deleted=complete,
            real_apple_kms_gcs_calls=0,model_calls=f.calls,receipt=receipt)
    finally:host.doCleanups()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True,type=Path);a=p.parse_args()
    a.output.write_text(json.dumps(run(),indent=2)+'\n')
