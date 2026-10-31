"""Replay the same closed-account/research cases on parent or candidate source."""
import argparse
import copy
import json
from pathlib import Path
import unittest
from lite_disposition_progress_probe import ProgressFixture
from lite_fenced_store import FencedPrivacy
from lite_reconsent import Reconsent, ReconsentRejected, CONSENT, DISCLOSURE
import test_lite_fenced_objects as objects
import test_lite_native_purchase as native


def run():
    try:from lite_account_research_closure import AccountResearchClosure
    except ImportError:AccountResearchClosure=None
    native.legacy.LinksTests.setUpClass();results={}
    for case in ('legacy_media','renewed_media','closed_rejoin','ordinary_rejoin'):
        host=unittest.TestCase()
        try:
            f=ProgressFixture(host);owner='a'*32;f.f.login(owner);bucket=objects.Bucket()
            research=Reconsent(FencedPrivacy(f.store('research'),bucket));epoch='legacy'
            def rejoin(previous,new):
                return research.resume(owner,dict(anon_id=owner,research_consent=True,previous_epoch=previous,
                    new_epoch=new,consent_version=CONSENT,disclosure_version=DISCLOSURE))
            if case=='renewed_media':
                research.privacy.manifest.withdraw(owner);rejoin('legacy','b'*32);epoch='b'*32
            if case.endswith('media'):
                p=research.privacy.scoped(epoch);ticket=p.begin_write(owner);media=p.media(owner,ticket)
                for suffix,data in (('jpg',f.capture),('depth.png',b'generated-depth-bytes'),
                                    ('conf.png',b'generated-confidence-bytes'),('json',b'{"synthetic":true}')):
                    media.put_blob('lite/'+owner+'/'+'f'*16+'.'+suffix,data)
                media.put_blob('lite_raw/'+owner+'/'+'f'*16+'.rgbd',b'generated-rgbd-packet')
                media.append_record_once('lite_labels.jsonl','c'*16,
                    dict(anon_id=owner,annotation_receipt_id='c'*16,synthetic=True,revision=1))
                p.finish_write(ticket)
            else:research.privacy.manifest.withdraw(owner)
            original=copy.deepcopy((bucket.live,bucket.history))
            old_consents=research.load(owner)[1:];worker_result=None
            if case!='ordinary_rejoin':f.close()
            native_before=f.preserved();calls=(list(f.f.g.calls),list(f.f.kms.calls),f.calls)
            if case!='ordinary_rejoin':
                if AccountResearchClosure is not None:
                    worker=AccountResearchClosure(deletion=f.d,research=research,
                        store=f.store('research-closure'),audit_store=f.store('research-closure-audit'))
                    worker_result=worker.advance(f.person)
            if case.endswith('media'):
                try:research.resolve(owner,epoch);accepted=True
                except ReconsentRejected:accepted=False
            else:
                try:rejoin('legacy','b'*32);accepted=True
                except ReconsentRejected:accepted=False
            host.assertEqual(original,(bucket.live,bucket.history))
            host.assertEqual(native_before,f.preserved())
            host.assertEqual(calls,(f.f.g.calls,f.f.kms.calls,f.calls))
            if case.endswith('media'):host.assertEqual(old_consents,research.load(owner)[1:])
            if AccountResearchClosure is not None:
                host.assertEqual(accepted,case=='ordinary_rejoin')
                if worker_result:host.assertFalse(worker_result['account_deleted'])
            results[case]=dict(research_admission_or_rejoin_allowed=accepted,
                current_epoch=research.load(owner)[1],media_objects=len(bucket.live),
                media_and_history_unchanged=True,original_consent_preserved=True,
                native_identity_and_quota_unchanged=True,provider_or_model_calls=0,
                checkpoint=worker_result)
        finally:host.doCleanups()
    return dict(schema='lite-account-research-closure-probe/1',implementation_present=AccountResearchClosure is not None,
        synthetic_only=True,scenarios=results,real_data_or_keys_deleted=False)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    value=run();a.output.write_text(json.dumps(value,indent=2)+'\n');print(json.dumps(value))
