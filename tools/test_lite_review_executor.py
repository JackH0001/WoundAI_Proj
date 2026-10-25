#!/usr/bin/env python3
"""Offline injected-failure rehearsal, not live Google acceptance."""
import copy
import fcntl
import json
import os
from pathlib import Path
import tempfile
import unittest
from lite_review_executor import *


def plan():
    return dict(schema='lite-b1-controller/1', source='a'*40, ci_head='a'*40, app=APP,
        image=IMAGE, project=PROJECT, region=REGION, phase='B1_empty_owner_login',
        catalogue_records=[], source_grants=[], owner_hash='b'*64, expires=2000,
        baseline_hash='c'*64, oauth_receipt_hash='d'*64, steps=list(STEPS))


class Fake:
    def __init__(self, fail=None, lost=False, preflight=True):
        self.fail = fail; self.lost = lost; self.valid = preflight
        self.calls = []; self.applied = set(); self.retained = set()
        self.owner_ok = True; self.closed_ok = True; self.close_fail = None
    def preflight(self, p):
        self.calls.append(('preflight', None)); return self.valid
    def apply(self, step):
        self.calls.append(('apply', step))
        if step != self.fail or self.lost:
            self.applied.add(step)
            if 'version' in step or step.startswith('create_'):
                self.retained.add(step)
        if step == self.fail: raise TimeoutError('SECRET MUST NOT REACH JOURNAL')
    def observe(self, step):
        self.calls.append(('observe', step))
        return 'confirmed' if step in self.applied else 'absent'
    def close(self, step):
        self.calls.append(('close', step))
        if step == self.close_fail: raise TimeoutError()
        return 'confirmed'
    def verify_closed(self):
        self.calls.append(('verify_closed', None)); return self.closed_ok
    def verify_owner(self):
        self.calls.append(('owner', None)); return self.owner_ok


class Rehearsal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'journal.jsonl'; self.now = 1000; self.p = plan()
    def run_for(self, fake=None, **kw):
        r = Executor(self.p, sha(self.p), fake or Fake(), self.path,
                     wall=lambda:self.now, clock=lambda:self.now, **kw)
        self.addCleanup(lambda: os.close(r.journal.lock))
        return r
    def test_success_retains_created_resources_and_closes(self):
        f=Fake(); r=self.run_for(f)
        self.assertEqual(r.start(),'ready_for_owner'); r.owner_view()
        self.assertTrue(r.contain()); self.assertEqual(r.forward,28)
        self.assertEqual(r.closure,9)
        self.assertEqual(len(f.retained),5)
        self.assertEqual([s for c,s in f.calls if c=='close'],list(CLOSE))
    def test_preflight_failure_has_no_mutation_including_rollback(self):
        f=Fake(preflight=False); r=self.run_for(f)
        with self.assertRaises(Stop):r.start()
        self.assertEqual(f.calls,[('preflight',None)])
    def test_every_partial_failure_with_or_without_lost_response_is_contained(self):
        # 26 injection points; none repeats a write or deletes a retained resource.
        for step in STEPS:
            for lost in (False,True):
                with self.subTest(step=step,lost=lost), tempfile.TemporaryDirectory() as td:
                    f=Fake(step,lost); r=Executor(self.p,sha(self.p),f,Path(td)/'j',wall=lambda:1000)
                    try:
                        with self.assertRaises(Stop):r.start()
                        self.assertEqual([s for c,s in f.calls if c=='apply'].count(step),1)
                        self.assertEqual([s for c,s in f.calls if c=='close'],list(CLOSE))
                        self.assertEqual(r.journal.events[-1]['event'],'closed')
                        self.assertNotIn('SECRET',r.journal.path.read_text())
                        self.assertEqual(r.journal.path.stat().st_mode&0o777,0o600)
                    finally:os.close(r.journal.lock)
    def test_second_start_never_repeats_mutations(self):
        f=Fake();r=self.run_for(f);r.start();count=len(f.calls)
        with self.assertRaises(Stop):r.start()
        self.assertEqual(len(f.calls),count)
    def test_wrong_source_ci_prevents_start(self):
        self.p['ci_head']='e'*40
        with self.assertRaises(Stop):self.run_for()
        self.assertFalse(self.path.exists())
    def test_plan_hash_mismatch(self):
        with self.assertRaises(Stop):Executor(self.p,'0'*64,Fake(),self.path,wall=lambda:1000)
    def test_wrong_app_image_region_project_phase_denied(self):
        for key in ('image','app','region','project','phase'):
            p=plan();p[key]='other'
            with self.subTest(key=key),self.assertRaises(Stop):validate_plan(p,sha(p),1000)
    def test_positive_catalogue_or_source_permission_denied(self):
        for key in ('catalogue_records','source_grants'):
            p=plan();p[key]=['one']
            with self.subTest(key=key),self.assertRaises(Stop):validate_plan(p,sha(p),1000)
    def test_unknown_actions_and_parameters_denied(self):
        for p in (dict(plan(),secret='x'),dict(plan(),steps=list(STEPS)+['delete_source'])):
            with self.assertRaises(Stop):validate_plan(p,sha(p),1000)
    def test_expired_authorization_denies_forward(self):
        self.now=3000
        with self.assertRaises(Stop):self.run_for()
    def test_missing_oauth_receipt_denied(self):
        self.p['oauth_receipt_hash']=''
        with self.assertRaises(Stop):self.run_for()
    def test_deadline_closes_without_suppressing_recovery(self):
        r=self.run_for();r.start();self.now=4000
        with self.assertRaises(Stop):r.owner_view()
        self.assertEqual(r.journal.events[-1]['event'],'closed')
    def test_five_probes_then_close(self):
        r=self.run_for();r.start()
        for _ in range(5):r.owner_view()
        with self.assertRaises(Stop):r.owner_view()
        self.assertEqual(r.journal.events[-1]['event'],'closed')
    def test_forged_owner_or_audit_failure_closes(self):
        f=Fake();r=self.run_for(f);r.start();f.owner_ok=False
        with self.assertRaises(Stop):r.owner_view()
        self.assertFalse(r.ready)
        self.assertEqual(r.journal.events[-1]['event'],'closed')
    def test_cleanup_failure_does_not_skip_other_closures(self):
        f=Fake();r=self.run_for(f);r.start();f.close_fail='revoke_owner'
        self.assertFalse(r.contain())
        self.assertIn(('close','revoke_audit_readback'),f.calls)
        self.assertNotEqual(r.journal.events[-1]['event'],'closed')
    def test_effective_denial_must_be_verified(self):
        f=Fake();r=self.run_for(f);r.start();f.closed_ok=False
        self.assertFalse(r.contain())
    def test_no_automatic_forward_after_restart_even_if_not_expired(self):
        r=self.run_for();r.start()
        fcntl.flock(r.journal.lock,fcntl.LOCK_UN)
        other=self.run_for(resume=True)
        with self.assertRaises(Stop):other.start()
        self.assertTrue(other.contain())
    def test_expired_restart_can_still_contain(self):
        r=self.run_for();r.start();fcntl.flock(r.journal.lock,fcntl.LOCK_UN);self.now=9000
        other=self.run_for(resume=True)
        self.assertTrue(other.contain())
        with self.assertRaises(Stop):other.start()
    def test_concurrent_operator_rejected(self):
        r=self.run_for()
        with self.assertRaises(Stop):self.run_for(resume=True)
    def test_journal_tamper_rejected(self):
        r=self.run_for();r.start();fcntl.flock(r.journal.lock,fcntl.LOCK_UN)
        data=self.path.read_text().replace('"ready"','"closed"');self.path.write_text(data)
        with self.assertRaises(Stop):self.run_for(resume=True)
    def test_truncated_journal_requires_manual_containment(self):
        r=self.run_for();fcntl.flock(r.journal.lock,fcntl.LOCK_UN)
        self.path.write_bytes(self.path.read_bytes()[:-1])
        with self.assertRaises(Stop):self.run_for(resume=True)
    def test_no_secret_or_exception_text_permitted_in_events(self):
        r=self.run_for()
        with self.assertRaises(Stop):r.journal.append('stop',result='secret-token')
    def test_limits_cannot_widen(self):
        with self.assertRaises(Stop):self.run_for(limits=Limits(seconds=9999))


class PolicyDelta(unittest.TestCase):
    def test_preserves_third_party_grants_etag_and_normalizes_user_case(self):
        original=dict(etag='fresh',version=3,bindings=[
            dict(role='roles/iap.httpsResourceAccessor',members=['user:Jack.Hou@gmail.com','user:other@example.com']),
            dict(role='other',members=['serviceAccount:keep@example.com'])])
        result=remove_owned_grant(original,{'bindings':[]},'roles/iap.httpsResourceAccessor','user:jack.hou@gmail.com',None)
        self.assertEqual(result['etag'],'fresh')
        self.assertEqual(result['bindings'][0]['members'],['user:other@example.com'])
        self.assertEqual(len(original['bindings'][0]['members']),2)
    def test_cannot_remove_preexisting_grant(self):
        b=dict(role='role',members=['user:a@example.com'])
        with self.assertRaises(Stop):remove_owned_grant(dict(etag='e',bindings=[b]),dict(bindings=[b]),'role','user:A@example.com',None)
    def test_conditions_are_exact_and_not_normalized(self):
        b=dict(role='role',members=['user:a@example.com'],condition={'expression':'x'})
        p=dict(etag='e',bindings=[b])
        self.assertEqual(remove_owned_grant(p,{},'role','user:a@example.com',{'expression':'y'}),p)
    def test_no_etag_refused(self):
        with self.assertRaises(Stop):remove_owned_grant({}, {},'r','user:a',None)
    def test_service_identity_case_is_not_changed(self):
        self.assertNotEqual(normalized_member('serviceAccount:A'),normalized_member('serviceAccount:a'))


if __name__=='__main__':unittest.main(verbosity=2)
