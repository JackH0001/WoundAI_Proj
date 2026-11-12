"""Whole selected-source retirement guard with real AES and versioned storage.

Actual application validators; only Google/Apple transport and time are doubles.
"""
import base64
import copy
import hashlib
import json
import os
import socket
import unittest
from unittest.mock import patch
import requests
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from lite_attest_state import SQLiteStateStore, StateUnavailable
from lite_key_lifecycle_dispatch import PinnedKeyLifecycle
from lite_shared_key_retirement import SharedKeyRetirement, RetirementRejected
from lite_store_locations import location
from lite_subscription_executor import canonical,key
from lite_usage_ledger import digest
from test_lite_key_lifecycle_dispatch import LifecycleAdapter
from test_lite_shared_key_inventory import InventoryFixture
from test_lite_rotating_grant_keys import V1,V2,V3
from test_lite_subscription_grace import rows
import test_lite_native_purchase as native


class RetirementFixture:
    def __init__(self,host,*,backup=True,third=False):
        self.x=InventoryFixture(host);self.f=self.x.f;self.a=self.x.person
        self.a_snapshot=self.f.snapshot();self.original_a=copy.deepcopy(self.x.before_key)
        self.x.add_mirror();self.b=self.x.close_and_rotate();self.b_snapshot=self.f.snapshot()
        self.third=None
        if third:
            self.f.login('third-device','apple-human-3');self.third=self.f.person;self.third_snapshot=self.f.snapshot()
        if backup:self.refresh_backup()
        self.f.cipher.fence_write_version(V1,target=V2,rotation='1'*64)
        root=self.f.f.h.f.root
        self.reviews=SQLiteStateStore(root/'retirement-reviews.sqlite')
        self.state=SQLiteStateStore(root/'retirement-dispatch.sqlite')
        self.audit=SQLiteStateStore(root/'retirement-dispatch-audit.sqlite')
        self.session=requests.Session();self.session.trust_env=False;host.addCleanup(self.session.close)
        self.wire=LifecycleAdapter(self.f.kms);self.session.mount('https://',self.wire)
        self.provider=PinnedKeyLifecycle(session=self.session,version=V1,protected_version=V2)
        self.op,self.dr,self.reviewer=[Ed25519PrivateKey.generate() for _ in range(3)]
        self.now=1000;self.counter=0
        self.repin()
    def refresh_backup(self):
        b=self.x.buckets['synthetic-custody-mirror']
        for n,(g,raw) in self.x.bucket.live.items():
            old=b.live.get(n)
            if old is not None and old[1]==raw:continue
            b.blob(n).upload_from_string(raw,if_generation_match=0 if old is None else old[0])
    def repin(self):
        self.keys=[k.public_key().public_bytes_raw() for k in (self.op,self.dr,self.reviewer)]
        pins=dict(custody=location(self.f.cipher.store),custody_audit=location(self.f.cipher.audit),
            lifecycle=location(self.f.lifecycle.store),lifecycle_audit=location(self.f.lifecycle.audit),
            reviews=location(self.reviews),dispatch=location(self.state),dispatch_audit=location(self.audit))
        self.m=dict(v=1,source_commit='a'*40,scope=self.f.cipher.scope,sources=self.x.sources.copy(),recovery_sources=['mirror'],
            retiring_version=V1,protected_version=V2,locations_sha256={k:digest(v) for k,v in pins.items()},
            signers_sha256=[hashlib.sha256(k).hexdigest() for k in self.keys])
        self.g=self.restart()
    def restart(self,**kw):
        args=dict(enabled=True,manifest=self.m,expected_manifest=digest(self.m),expected_source='a'*40,
            cipher=self.f.cipher,get=self.x.get,provider=self.provider,reviews=self.reviews,
            dispatch_store=self.state,dispatch_audit=self.audit,operator_key=self.keys[0],drain_key=self.keys[1],
            reviewer_key=self.keys[2],clock=lambda:self.now)
        args.update(kw);return SharedKeyRetirement(**args)
    @staticmethod
    def sign(p,sk):return dict(payload=p,signature=base64.b64encode(sk.sign(canonical(p))).decode())
    def command(self,plan):
        self.counter+=1;d=self.g._dispatch(plan)
        return self.sign(dict(kind='key-lifecycle-command/1',version=V1,manifest=digest(plan),command=f'{self.counter:064x}',
            expected_state=d.inspect()['state_sha256'],issued_at=self.now,expires_at=self.now+300),self.op)
    def scope(self,plan):
        return self.sign(dict(kind='shared-key-retirement-scope/1',manifest=digest(self.m),plan=digest(plan),
            coverage_evidence='c'*64,drain_evidence='d'*64,recovery_evidence='e'*64,
            all_custody_sources_declared=True,all_writers_drained=True,recovery_locations_reviewed=True,
            issued_at=self.now,expires_at=self.now+120),self.reviewer)
    def drain(self,plan,c):
        d=self.g._dispatch(plan);r=d._read(allow_missing_for_reconcile=True)
        return self.sign(dict(kind='key-lifecycle-drain/1',version=V1,manifest=digest(plan),command=digest(c),
            expected_state=d._state(r),issued_at=self.now,expires_at=self.now+60,prior_dispatch_drained=True),self.dr)
    def perform(self,review,action):
        plan=self.g.plan(review,action);c=self.command(plan);result=self.g.execute(plan,c,scope=self.scope(plan));return plan,c,result
    def mutations(self):return [c for c in self.wire.calls if c[0]!='get']
    def original_rows(self):
        return (copy.deepcopy([(b.name,b.live,b.versions) for b in self.x.buckets.values()]),rows(self.f.key_audit),
            rows(self.f.lifecycle.store),rows(self.f.lifecycle.audit),rows(self.f.g.store),rows(self.f.f.h.f.ledger.store))


class RetirementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):native.legacy.LinksTests.setUpClass()
    def setUp(self):
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden'));guard.start();self.addCleanup(guard.stop)
        self.x=RetirementFixture(self);self.f=self.x.f
    def prepared(self):return self.x.g.prepare()['review_sha256']
    def test_real_selected_recovery_preserves_all_original_rows(self):
        before=self.x.original_rows();r=self.x.g.prepare()
        self.assertEqual((r['accounts'],r['active_accounts'],r['closed_accounts']),(2,1,1))
        self.assertTrue(r['selected_recovery_verified']);self.assertFalse(r['global_coverage_attested'])
        self.assertEqual(before,self.x.original_rows());self.assertEqual(self.x.mutations(),[])
        self.assertNotIn(self.x.original_a['wrapped_dek'],repr(rows(self.x.reviews)))
        self.assertNotIn('private-refresh-token',repr(rows(self.x.reviews)))
        decoded=json.dumps(self.x.g._review(r['review_sha256']))
        self.assertNotIn(self.x.original_a['wrapped_dek'],decoded)
        self.assertNotIn('wrapped_dek',decoded)
    def test_lifecycle_full_flow_after_actual_inventory_and_recovery(self):
        before=self.x.original_rows();review=self.prepared()
        _,_,d=self.x.perform(review,'disable');self.assertEqual(d['provider_state'],'DISABLED')
        self.assertEqual(self.f.cipher.open(*self.x.b_snapshot)['refresh_token'],'private-refresh-token')
        _,_,s=self.x.perform(review,'schedule');self.assertEqual(s['provider_state'],'DESTROY_SCHEDULED')
        _,_,r=self.x.perform(review,'restore');self.assertEqual(r['provider_state'],'DISABLED')
        p,c,_=self.x.perform(review,'schedule');self.x.wire.finish_destruction();self.x.now+=31*86400
        r=self.x.g.reconcile(p,self.x.drain(p,c));self.assertEqual(r['provider_state'],'DESTROYED')
        self.assertFalse(r['account_deleted']);self.assertFalse(r['physical_backups_erased'])
        self.assertEqual(before,self.x.original_rows());self.assertEqual(len(self.x.mutations()),4)
        self.assertEqual(self.f.cipher.open(*self.x.b_snapshot)['refresh_token'],'private-refresh-token')
    def test_default_off_wrong_signer_source_and_locations_rejected(self):
        for change in (dict(enabled=False),dict(reviewer_key=self.x.keys[0]),dict(expected_source='f'*40),dict(dispatch_store=self.x.reviews)):
            with self.subTest(change=repr(change)),self.assertRaises(ValueError):self.x.restart(**change)
    def test_old_only_backup_blocks_before_disable(self):
        x=RetirementFixture(self,backup=False)
        with self.assertRaises(RetirementRejected):x.g.prepare()
        self.assertEqual(x.mutations(),[])
    def test_each_recovery_source_must_have_current_copy(self):
        mirror=self.x.x.buckets['synthetic-custody-mirror'];name=self.x.x.store._name(self.f.cipher._key(self.x.b))
        del mirror.live[name]  # Versions remain, but no current recovery copy.
        with self.assertRaises(RetirementRejected):self.prepared()
        self.assertEqual(self.x.mutations(),[])
    def test_backup_matching_identifier_but_different_dek_is_rejected(self):
        row=copy.deepcopy(self.f.cipher._read(self.x.b)[1])
        row['wrapped_dek']=self.f.cipher.provider.wrap(V2,os.urandom(32),self.f.cipher._aad_for(row))
        self.f.cipher.immutable(self.f.key_audit,self.f.cipher._proof_key(row),self.f.cipher._proof(row))
        mirror=self.x.x.buckets['synthetic-custody-mirror'];name=self.x.x.store._name(self.f.cipher._key(self.x.b))
        mirror.blob(name).upload_from_string(canonical(row),if_generation_match=mirror.live[name][0])
        with self.assertRaisesRegex(RetirementRejected,'recovery data key differs'):self.prepared()
        self.assertEqual(self.x.mutations(),[])
    def test_corrupt_protected_wrapping_cannot_be_called_recoverable(self):
        unwrap=self.f.cipher.provider.unwrap
        def bad(v,*a):return bytes(32) if v==V2 else unwrap(v,*a)
        with patch.object(self.f.cipher.provider,'unwrap',side_effect=bad),self.assertRaises(RetirementRejected):self.prepared()
    def test_account_not_in_old_rewrap_batch_still_required(self):
        self.f.rotate_to(V1);load=self.f.key_audit.load;fence=self.f.cipher._write_fence_key(V1)
        # Model an old process without the new fence; the drain proof must cover it.
        with patch.object(self.f.key_audit,'load',side_effect=lambda k:None if k==fence else load(k)):
            self.f.login('late','apple-human-3')
        self.f.rotate_to(V2);self.x.g=self.x.restart()
        with self.assertRaisesRegex(RetirementRejected,'every active account'):self.prepared()
        self.assertEqual(self.x.mutations(),[])
    def test_current_source_omission_cannot_hide_person_with_historical_copy(self):
        name=self.x.x.store._name(self.f.cipher._key(self.x.b));del self.x.x.bucket.live[name]
        with self.assertRaises(RetirementRejected):self.prepared()
    def test_current_closed_key_requires_original_closure_receipt(self):
        load=self.f.key_audit.load
        def omit(k):
            r=load(k)
            return None if r and r[1].get('scheme')=='shared-kms-envelope-1' else r
        with patch.object(self.f.key_audit,'load',side_effect=omit),self.assertRaises(StateUnavailable):self.prepared()
    def test_unreadable_soft_deleted_state_never_accepted(self):
        self.x.x.soft_status=400
        with self.assertRaisesRegex(RetirementRejected,'unknown is not zero'):self.prepared()
    def test_secondary_source_forbidden_never_accepted(self):
        self.x.x.mutate=lambda p,q,m,c,b:(403,None) if 'mirror' in p else (c,b)
        with self.assertRaises(RetirementRejected):self.prepared()
    def test_old_version_fence_required(self):
        load=self.f.key_audit.load;fk=self.f.cipher._write_fence_key(V1)
        with patch.object(self.f.key_audit,'load',side_effect=lambda k:None if k==fk else load(k)),self.assertRaises(RetirementRejected):self.prepared()
    def test_target_disabled_prevents_preparation(self):
        self.f.kms.states[V2]='DISABLED'
        with self.assertRaises(RetirementRejected):self.prepared()
    def test_closed_then_new_account_or_roster_change_invalidates_old_review(self):
        r=self.prepared();plan=self.x.g.plan(r,'disable');c=self.x.command(plan)
        self.f.login('third','apple-human-3');self.x.refresh_backup()
        with self.assertRaises(RetirementRejected):self.x.g.execute(plan,c,scope=self.x.scope(plan))
        self.assertEqual(self.x.mutations(),[])
    def test_scope_proof_is_independent_explicit_and_fresh(self):
        r=self.prepared();p=self.x.g.plan(r,'disable');c=self.x.command(p);scope=self.x.scope(p)
        wrong=self.x.sign(scope['payload'],self.x.op)
        with self.assertRaises(RetirementRejected):self.x.g.execute(p,c,scope=wrong)
        for field in ('all_custody_sources_declared','all_writers_drained','recovery_locations_reviewed'):
            payload=dict(scope['payload'],**{field:False})
            with self.assertRaises(RetirementRejected):self.x.g.execute(p,c,scope=self.x.sign(payload,self.x.reviewer))
        self.x.now+=121
        with self.assertRaises(RetirementRejected):self.x.g.execute(p,c,scope=scope)
        self.assertEqual(self.x.mutations(),[])
    def test_wrong_plan_or_review_scope_attestation_rejected(self):
        r=self.prepared();p=self.x.g.plan(r,'disable');c=self.x.command(p);scope=self.x.scope(p)
        payload=dict(scope['payload'],plan='f'*64)
        with self.assertRaises(RetirementRejected):self.x.g.execute(p,c,scope=self.x.sign(payload,self.x.reviewer))
        with self.assertRaises(RetirementRejected):self.x.g.plan('f'*64,'disable')
    def test_replay_does_not_repeat_kms_get_or_crypto(self):
        r=self.prepared();p,c,result=self.x.perform(r,'disable');calls=self.x.wire.calls.copy();crypto=self.f.kms.calls.copy()
        self.assertEqual(self.x.g.execute(p,c,scope=self.x.scope(p)),result)
        self.assertEqual(calls,self.x.wire.calls);self.assertEqual(crypto,self.f.kms.calls)
    def test_lost_disable_ack_requires_separate_dispatch_drain(self):
        r=self.prepared();p=self.x.g.plan(r,'disable');c=self.x.command(p)
        self.x.wire.after=lambda a:(_ for _ in ()).throw(TimeoutError()) if a=='disable' else None
        with self.assertRaises(StateUnavailable):self.x.g.execute(p,c,scope=self.x.scope(p))
        self.x.wire.after=lambda a:None;self.x.g=self.x.restart()
        read=self.x.g.reconcile(p,self.x.drain(p,c));self.assertEqual(read['provider_state'],'DISABLED')
        self.assertEqual(self.x.g.execute(p,c,scope=self.x.scope(p)),read);self.assertEqual(len(self.x.mutations()),1)
    def test_reconcile_needs_no_recovery_reads_or_key_decryption(self):
        r=self.prepared();p,c,_=self.x.perform(r,'disable')
        with patch.object(self.x.g,'_binding',side_effect=AssertionError('no crypto')),patch.object(self.f.cipher.provider,'unwrap',side_effect=AssertionError('no unwrap')):
            self.assertEqual(self.x.g.reconcile(p,self.x.drain(p,c))['provider_state'],'DISABLED')
    def test_schedule_revalidates_recovery_even_though_old_version_is_disabled(self):
        r=self.prepared();self.x.perform(r,'disable');self.f.kms.states[V2]='DISABLED'
        p=self.x.g.plan(r,'schedule');c=self.x.command(p)
        with self.assertRaises(RetirementRejected):self.x.g.execute(p,c,scope=self.x.scope(p))
        self.assertEqual(len(self.x.mutations()),1)
    def test_unexpected_new_physical_version_blocks_schedule(self):
        r=self.prepared();self.x.perform(r,'disable')
        mirror=self.x.x.buckets['synthetic-custody-mirror'];n,(g,raw)=next(iter(mirror.live.items()))
        mirror.blob(n).upload_from_string(raw,if_generation_match=g)
        p=self.x.g.plan(r,'schedule');c=self.x.command(p)
        with self.assertRaises(RetirementRejected):self.x.g.execute(p,c,scope=self.x.scope(p))
    def test_restore_remains_available_when_recovery_copy_breaks(self):
        r=self.prepared();self.x.perform(r,'disable');self.x.perform(r,'schedule');self.x.x.soft_status=400
        _,_,result=self.x.perform(r,'restore');self.assertEqual(result['provider_state'],'DISABLED')
        self.assertEqual(self.f.kms.states[V1],'DISABLED')
    def test_scope_expiring_during_fresh_read_blocks_provider_mutation(self):
        r=self.prepared();p=self.x.g.plan(r,'disable');c=self.x.command(p);s=self.x.scope(p);bind=self.x.g._binding
        def slow(**kw):
            value=bind(**kw);self.x.now+=121;return value
        with patch.object(self.x.g,'_binding',side_effect=slow),self.assertRaises(RetirementRejected):self.x.g.execute(p,c,scope=s)
        self.assertEqual(self.x.mutations(),[])
    def test_recovery_crypto_fault_preserves_original_records(self):
        before=self.x.original_rows()
        with patch.object(self.f.cipher.provider,'unwrap',side_effect=RuntimeError('private raw token')),self.assertRaises(RetirementRejected) as e:self.prepared()
        self.assertNotIn('raw token',str(e.exception));self.assertEqual(before,self.x.original_rows())
    def test_recovery_store_may_not_alias_other_state(self):
        with self.assertRaises(ValueError):self.x.restart(reviews=self.x.state)
    def test_missing_declared_recovery_scope_is_rejected(self):
        m=dict(self.x.m,recovery_sources=[])
        with self.assertRaises(ValueError):self.x.restart(manifest=m,expected_manifest=digest(m))
    def test_same_bucket_not_independent_recovery_location(self):
        m=copy.deepcopy(self.x.m);m['sources'][1]['bucket']=m['sources'][0]['bucket'];m['sources'][1]['prefix']='other/keys'
        with self.assertRaises(ValueError):self.x.restart(manifest=m,expected_manifest=digest(m))
    def test_physical_copy_changes_during_crypto_are_detected(self):
        unwrap=self.f.cipher.provider.unwrap;done=[]
        def changed(*args):
            result=unwrap(*args)
            if not done:
                done.append(1);b=self.x.x.buckets['synthetic-custody-mirror'];n,(g,raw)=next(iter(b.live.items()))
                b.blob(n).upload_from_string(raw,if_generation_match=g)
            return result
        with patch.object(self.f.cipher.provider,'unwrap',side_effect=changed),self.assertRaises(RetirementRejected):self.prepared()
    def test_matching_ciphertext_at_wrong_generation_not_accepted(self):
        get=self.x.x.get
        def corrupt(p,q,*,media=False):
            status,raw=get(p,q,media=media)
            if media and len(self.x.x.calls)>100:return 412,None
            return status,raw
        # An explicit pinned-read error is never treated as an empty source.
        self.x.g.get=corrupt
        self.x.x.mutate=lambda p,q,m,c,b:(412,None) if m else (c,b)
        with self.assertRaises(RetirementRejected):self.prepared()
    def test_review_is_chunked_and_every_original_part_is_required(self):
        r=self.prepared();root=self.x.reviews.load(key('shared-key-retirement-review',r))[1]
        self.assertGreater(len(root['parts']),1)
        self.assertEqual(digest(self.x.g._review(r)),r)
        load=self.x.reviews.load;missing=key('shared-key-retirement-part',root['parts'][0])
        with patch.object(self.x.reviews,'load',side_effect=lambda k:None if k==missing else load(k)),self.assertRaises(RetirementRejected):
            self.x.g.plan(r,'disable')
    def test_review_chunk_reordering_is_rejected(self):
        r=self.prepared();rk=key('shared-key-retirement-review',r);load=self.x.reviews.load
        def reorder(k):
            row=load(k)
            return (row[0],dict(row[1],parts=list(reversed(row[1]['parts'])))) if k==rk else row
        with patch.object(self.x.reviews,'load',side_effect=reorder),self.assertRaises(RetirementRejected):self.x.g._review(r)
    def test_failed_review_root_publication_cannot_authorize_dispatch(self):
        cas=self.x.reviews.compare_exchange
        def fail(k,g,v):
            if v.get('schema')=='shared-key-retirement-root/1':raise StateUnavailable('synthetic root write failure')
            return cas(k,g,v)
        with patch.object(self.x.reviews,'compare_exchange',side_effect=fail),self.assertRaises(StateUnavailable):self.prepared()
        self.assertEqual(self.x.mutations(),[])
        review=self.prepared();self.assertEqual(digest(self.x.g._review(review)),review)
    def test_combined_review_time_bound_keeps_original_state(self):
        with patch('lite_shared_key_retirement.time.monotonic',side_effect=[100,401]),self.assertRaises(RetirementRejected):self.prepared()
        self.assertEqual(self.x.mutations(),[])
    def test_held_old_dependency_blocks_retirement_even_when_backup_is_good(self):
        def hold(p,q,m,c,b):
            if not m and p.endswith('/o'):
                for item in b.get('items',[]):item['temporaryHold']=True
            return c,b
        self.x.x.mutate=hold
        with self.assertRaisesRegex(RetirementRejected,'held dependency'):self.prepared()
        self.assertEqual(self.x.mutations(),[])
    def test_bucket_retention_blocks_retirement(self):
        self.x.x.mutate=lambda p,q,m,c,b:(c,dict(b,retentionPolicy={'retentionPeriod':'604800','isLocked':True})) if '/o' not in p else (c,b)
        with self.assertRaisesRegex(RetirementRejected,'retention'):self.prepared()
        self.assertEqual(self.x.mutations(),[])
