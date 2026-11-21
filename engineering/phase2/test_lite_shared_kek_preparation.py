"""Actual grant custody/AES/SQLite; synthetic signed operator and KMS transport."""
import base64
import copy
import socket
import threading
import unittest
from unittest.mock import patch
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from lite_attest_state import SQLiteStateStore, StateUnavailable
from lite_shared_kek_preparation import SharedKEKPreparation, PreparationRejected
from lite_subscription_executor import canonical, key
from lite_usage_ledger import digest
from test_lite_rotating_grant_keys import SharedFixture, V1, V2, V3
from test_lite_subscription_grace import rows
from lite_rotating_grant_keys_probe import raw_backup_open
import test_lite_native_purchase as native


class PreparationFixture:
    def __init__(self,host,*,accounts=2,steps=1):
        self.f=SharedFixture(host); self.snapshots={}; self.sources={}
        for i in range(accounts):
            self.f.login('device-one' if i==0 else 'device-'+str(i), 'apple-human-'+str(i+1))
            p=self.f.person; self.snapshots[p]=self.f.snapshot()
            self.sources[p]=copy.deepcopy(self.f.cipher._read(p)[1])
        self.old=self.f.cipher; self.f.rotate_to(V2)
        self.op=Ed25519PrivateKey.generate(); self.dr=Ed25519PrivateKey.generate(); self.now=1000
        self.m=dict(v=1,scope=self.f.cipher.scope,source_commit='a'*40,rotation='b'*64,
            source_version=V1,target_version=V2,accounts=[dict(person=p,source_sha256=digest(v))
            for p,v in sorted(self.sources.items())],not_before=1000,expires_at=2000,max_steps=steps,max_dispatches=32)
        root=self.f.f.h.f.root
        self.store=SQLiteStateStore(root/'prep-state.sqlite'); self.audit=SQLiteStateStore(root/'prep-audit.sqlite')
        self.n=0; self.executor=self.restart()
    def restart(self,**overrides):
        args=dict(enabled=True,manifest=self.m,expected_manifest=digest(self.m),expected_source='a'*40,cipher=self.f.cipher,
            store=self.store,audit_store=self.audit,operator_key=self.op.public_key().public_bytes_raw(),
            drain_key=self.dr.public_key().public_bytes_raw(),clock=lambda:self.now)
        args.update(overrides); return SharedKEKPreparation(**args)
    @staticmethod
    def sign(p,k): return dict(payload=p,signature=base64.b64encode(k.sign(canonical(p))).decode())
    def command(self,action='advance'):
        self.n+=1
        p=dict(kind='shared-kek-prepare/1',manifest=digest(self.m),command=f'{self.n:032x}',
            expected_state=self.executor.inspect()['state_sha256'],issued_at=self.now,expires_at=self.now+300,action=action)
        return self.sign(p,self.op)
    def drain(self,command):
        p=dict(command['payload']); p.pop('action')
        p.update(kind='shared-kek-dispatch-drain/1',expires_at=self.now+60,dispatch_drained=True)
        return self.sign(p,self.dr)
    def complete(self):
        while not self.executor.inspect()['selected_batch_complete']:
            self.executor.execute(self.command())
        return self.executor.inspect()


class SharedPreparationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): native.legacy.LinksTests.setUpClass()
    def setUp(self):
        p=patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden'))
        p.start(); self.addCleanup(p.stop)
        self.x=PreparationFixture(self); self.e=self.x.executor; self.f=self.x.f
    def calls(self,action): return sum(a==action for a,_ in self.f.kms.calls)
    def recover(self):
        c=self.x.command('resume'); return self.e.execute(c,drain=self.x.drain(c))
    def test_default_disabled_and_same_signer_rejected(self):
        with self.assertRaises(ValueError): self.x.restart(enabled=False)
        with self.assertRaises(ValueError): self.x.restart(drain_key=self.x.op.public_key().public_bytes_raw())
    def test_scope_source_and_manifest_pins_are_checked(self):
        for field,value in [('scope','c'*64),('source_commit','not-a-commit'),('target_version',V1),('accounts',[])]:
            m=dict(self.x.m,**{field:value})
            with self.assertRaises(ValueError): self.x.restart(manifest=m,expected_manifest=digest(m))
        with self.assertRaises(ValueError): self.x.restart(expected_manifest='f'*64)
        with self.assertRaises(ValueError): self.x.restart(expected_source='f'*40)
    def test_duplicate_accounts_and_overbound_batch_rejected(self):
        for accounts in [self.x.m['accounts']*2,self.x.m['accounts']*26]:
            m=dict(self.x.m,accounts=accounts)
            with self.assertRaises(ValueError): self.x.restart(manifest=m,expected_manifest=digest(m))
    def test_store_overlap_rejected(self):
        with self.assertRaises(ValueError): self.x.restart(store=self.f.key_store)
        with self.assertRaises(ValueError): self.x.restart(audit_store=self.x.store)
    def test_bad_operator_and_expired_command_do_not_write_or_wrap(self):
        before=rows(self.x.store),rows(self.x.audit),self.f.kms.calls.copy()
        c=self.x.command(); c['signature']=self.x.sign(c['payload'],self.x.dr)['signature']
        with self.assertRaises(PreparationRejected): self.e.execute(c)
        c=self.x.command(); self.x.now+=301
        with self.assertRaises(PreparationRejected): self.e.execute(c)
        self.assertEqual(before,(rows(self.x.store),rows(self.x.audit),self.f.kms.calls))
    def test_unavailable_target_installs_no_fence(self):
        self.f.kms.states[V2]='DISABLED'
        with self.assertRaises(StateUnavailable): self.e.execute(self.x.command())
        self.assertIsNone(self.f.key_audit.load(self.f.cipher._write_fence_key(V1)))
        self.assertEqual(self.e.inspect()['phase'],'not_started')
    def test_any_stale_selected_source_refuses_entire_batch_before_fence(self):
        m=copy.deepcopy(self.x.m); m['accounts'][-1]['source_sha256']='f'*64
        self.x.m=m; self.x.executor=self.e=self.x.restart()
        before=self.calls('encrypt')
        with self.assertRaises(StateUnavailable): self.e.execute(self.x.command())
        self.assertEqual(self.calls('encrypt'),before)
        self.assertIsNone(self.f.key_audit.load(self.f.cipher._write_fence_key(V1)))
    def test_two_batches_preserve_all_original_envelopes_and_quota(self):
        baseline=(rows(self.f.b.registry.store),rows(self.f.f.h.f.ledger.store),rows(self.f.g.store))
        self.assertEqual(self.e.execute(self.x.command())['phase'],'waiting')
        self.x.executor=self.e=self.x.restart()
        status=self.x.complete()
        self.assertTrue(status['selected_batch_complete']); self.assertEqual(status['dispatches'],2)
        self.f.kms.states[V1]='DISABLED'
        for p,(envelope,ctx) in self.x.snapshots.items():
            self.assertEqual(self.f.cipher.open(envelope,ctx)['refresh_token'],'private-refresh-token')
            self.assertEqual(self.f.cipher._read(p)[1]['dek_id'],self.x.sources[p]['dek_id'])
        self.assertEqual(baseline,(rows(self.f.b.registry.store),rows(self.f.f.h.f.ledger.store),rows(self.f.g.store)))
        self.assertEqual(self.calls('destroy'),0)
        for flag in ('complete_inventory_verified','all_writers_drained','kms_destruction_authorized','account_deleted'):
            self.assertIs(status[flag],False)
    def test_replay_completed_and_older_commands_never_rewrap(self):
        c=self.x.command(); self.e.execute(c); self.x.complete()
        before=self.f.kms.calls.copy(),rows(self.x.store),rows(self.x.audit)
        self.e.execute(c)
        self.assertEqual(before,(self.f.kms.calls,rows(self.x.store),rows(self.x.audit)))
    def test_new_command_with_stale_checkpoint_rejected(self):
        a=self.x.command(); b=self.x.command(); self.e.execute(a)
        with self.assertRaises(PreparationRejected): self.e.execute(b)
    def test_reused_command_id_with_different_body_rejected(self):
        a=self.x.command(); self.e.execute(a); b=self.x.command()
        b['payload']['command']=a['payload']['command']; b=self.x.sign(b['payload'],self.x.op)
        with self.assertRaises(StateUnavailable): self.e.execute(b)
    def test_lost_claim_ack_requires_independent_drain_not_automatic_retry(self):
        cas=self.x.store.compare_exchange; calls=self.calls('encrypt'); c=self.x.command()
        def lost(k,g,v):
            result=cas(k,g,v)
            if k==self.e._key(): raise StateUnavailable('lost acknowledgement')
            return result
        with patch.object(self.x.store,'compare_exchange',side_effect=lost),self.assertRaises(StateUnavailable): self.e.execute(c)
        self.assertEqual(self.calls('encrypt'),calls)
        with self.assertRaises(StateUnavailable): self.e.execute(c)
        with self.assertRaises(PreparationRejected): self.e.execute(self.x.command())
        self.recover(); self.x.complete()
    def test_uncommitted_claim_keeps_audit_and_never_resets_missing_budget(self):
        with patch.object(self.x.store,'compare_exchange',return_value=False),self.assertRaises(StateUnavailable):
            self.e.execute(self.x.command())
        with self.assertRaisesRegex(StateUnavailable,'never reset'): self.x.restart().inspect()
    def test_lost_batch_checkpoint_ack_is_read_back(self):
        cas=self.x.store.compare_exchange
        def lost(k,g,v):
            result=cas(k,g,v)
            if v.get('cursor')==1: raise StateUnavailable('lost checkpoint acknowledgement')
            return result
        with patch.object(self.x.store,'compare_exchange',side_effect=lost): self.e.execute(self.x.command())
        self.assertEqual(self.e.inspect()['cursor'],1); self.x.complete()
    def test_receipt_failure_resumes_original_proposal_without_new_wrap(self):
        audit=self.f.key_audit.compare_exchange
        def fail(k,g,v):
            if set(v)=={'v','operation','source_sha256','target_sha256'}: raise StateUnavailable('receipt unavailable')
            return audit(k,g,v)
        with patch.object(self.f.key_audit,'compare_exchange',side_effect=fail),self.assertRaises(StateUnavailable):
            self.e.execute(self.x.command())
        before=self.calls('encrypt'); self.assertEqual(self.e.inspect()['phase'],'uncertain')
        self.recover(); self.assertEqual(self.calls('encrypt'),before)
        self.x.complete()
    def test_wrong_or_old_drain_cannot_resume(self):
        with patch.object(self.f.cipher,'rewrap',side_effect=StateUnavailable('interrupted')),self.assertRaises(StateUnavailable):
            self.e.execute(self.x.command())
        c=self.x.command('resume'); d=self.x.drain(c)
        d['signature']=self.x.sign(d['payload'],self.x.op)['signature']
        with self.assertRaises(PreparationRejected): self.e.execute(c,drain=d)
        d=self.x.drain(c); self.x.now+=61
        with self.assertRaises(PreparationRejected): self.e.execute(c,drain=d)
        self.recover()
    def test_audit_failure_cannot_advance_account_cursor(self):
        cas=self.x.audit.compare_exchange
        def fail(k,g,v):
            if set(v)=={'v','operation','source_sha256','target_sha256'}: raise StateUnavailable('audit unavailable')
            return cas(k,g,v)
        with patch.object(self.x.audit,'compare_exchange',side_effect=fail),self.assertRaises(StateUnavailable):
            self.e.execute(self.x.command())
        self.assertEqual(self.e.inspect()['cursor'],0)
        before=self.calls('encrypt'); self.recover(); self.assertEqual(self.calls('encrypt'),before)
    def test_fenced_old_runtime_reads_existing_accounts_but_cannot_create_new_keys(self):
        self.e.execute(self.x.command())
        p=self.x.m['accounts'][1]['person']; envelope,ctx=self.x.snapshots[p]
        self.assertEqual(self.x.old.open(envelope,ctx)['refresh_token'],'private-refresh-token')
        before=self.calls('encrypt'); self.f.cipher=self.x.old
        self.f.g.cipher=self.x.old; self.f.g.a.cipher=self.x.old
        with self.assertRaises(StateUnavailable): self.f.login('late-device','new-after-fence')
        self.assertEqual(self.calls('encrypt'),before)
    def test_new_target_account_outside_batch_remains_usable_and_not_claimed_as_inventory(self):
        self.e.execute(self.x.command()); self.f.login('other','new-after-fence')
        p=self.f.person; envelope,ctx=self.f.snapshot(); self.x.complete()
        self.assertEqual(self.f.cipher._read(p)[1]['version'],V2)
        self.assertEqual(self.f.cipher.open(envelope,ctx)['refresh_token'],'private-refresh-token')
        self.assertEqual(self.e.inspect()['selected_accounts'],2)
    def test_closed_account_is_skipped_without_resurrection_or_erasure_claim(self):
        a=self.f.who('apple-human-1'); self.f.close_revoke()
        closure=self.f.cipher.destroy_after_revocation(self.f.d,a)
        before=self.calls('encrypt'); self.x.complete()
        self.assertEqual(self.calls('encrypt')-before,1)
        self.assertEqual(self.f.cipher.closure_receipt(a,closure['closure']),closure)
        e,c=self.x.snapshots[a]
        self.assertEqual(raw_backup_open(self.f.kms,self.x.sources[a],e,c)['refresh_token'],'private-refresh-token')
        self.assertIs(self.e.inspect()['account_deleted'],False)
    def test_closure_during_rewrap_never_overwrites_closed_row(self):
        a=self.x.m['accounts'][0]['person']
        # Close the selected account's lifecycle immediately before rewrap.
        rewrap=self.f.cipher.rewrap
        def close(p,**kw):
            device='device-one' if p==self.f.who('apple-human-1') else 'device-1'
            self.f.d.begin(device,'e'*32); self.f.d.advance(p)
            self.f.cipher.destroy_after_revocation(self.f.d,p)
            return rewrap(p,**kw)
        with patch.object(self.f.cipher,'rewrap',side_effect=close),self.assertRaises(Exception): self.e.execute(self.x.command())
        self.assertEqual(self.f.cipher._read(a)[1]['phase'],'closed')
        self.recover(); self.x.complete()
    def test_late_wrap_response_is_stopped_before_initial_proposal_persistence(self):
        # Hooks run inside the synthetic KMS RPC, beyond the initial fence check.
        self.f.cipher=self.x.old; self.f.g.cipher=self.x.old; self.f.g.a.cipher=self.x.old
        triggered=[]
        def hook(action,name):
            if action=='encrypt' and name==V1 and not triggered:
                triggered.append(True)
                self.e.cipher.fence_write_version(V1,target=V2,rotation=self.x.m['rotation'])
        self.f.kms.hook=hook
        with self.assertRaises(StateUnavailable): self.f.login('late','late-writer')
        p=self.f.who('late-writer'); self.assertIsNone(self.x.old._read(p))
        self.assertIsNone(self.f.key_store.load(key('shared-grant-key-initial-proposal',digest([self.x.old.scope,p]))))
    def test_fence_after_check_before_cas_is_not_misreported_as_global_drain(self):
        # Deliberate negative control: a cooperative check isn't atomic with an
        # independent CAS. Demonstrate why this tool never authorizes destruction.
        self.f.cipher=self.x.old; self.f.g.cipher=self.x.old; self.f.g.a.cipher=self.x.old
        cas=self.f.key_store.compare_exchange; p=self.f.who('between-check-and-cas')
        def late(k,g,v):
            if k==self.x.old._key(p):
                self.e.cipher.fence_write_version(V1,target=V2,rotation=self.x.m['rotation'])
            return cas(k,g,v)
        with patch.object(self.f.key_store,'compare_exchange',side_effect=late): self.f.login('late','between-check-and-cas')
        self.assertEqual(self.x.old._read(p)[1]['version'],V1)
        self.assertIs(self.e.inspect()['all_writers_drained'],False)
        self.assertIs(self.e.inspect()['kms_destruction_authorized'],False)
    def test_corrupt_fence_blocks_wrap_without_resetting_it(self):
        self.f.key_audit.compare_exchange(self.f.cipher._write_fence_key(V2),0,dict(corrupt=True))
        before=self.calls('encrypt')
        with self.assertRaises(StateUnavailable): self.f.login('new','never-wrap')
        self.assertEqual(self.calls('encrypt'),before)
    def test_dispatch_budget_exhaustion_does_not_change_keys(self):
        m=dict(self.x.m,max_dispatches=1); self.x.m=m; self.x.executor=self.e=self.x.restart()
        self.e.execute(self.x.command()); before=self.f.kms.calls.copy()
        with self.assertRaises(PreparationRejected): self.e.execute(self.x.command())
        self.assertEqual(self.f.kms.calls,before)
    def test_expiry_during_dispatch_stops_and_preserves_original_operation(self):
        original=self.f.cipher.rewrap
        def expire(*a,**kw):
            r=original(*a,**kw); self.x.now+=301; return r
        with patch.object(self.f.cipher,'rewrap',side_effect=expire),self.assertRaises(PreparationRejected):
            self.e.execute(self.x.command())
        self.assertEqual(self.e.inspect()['phase'],'uncertain')
        before=self.calls('encrypt'); self.recover(); self.assertEqual(self.calls('encrypt'),before)
    def test_two_independent_callers_cannot_both_claim_same_checkpoint(self):
        a=self.x.command(); b=self.x.command(); barrier=threading.Barrier(2)
        original=self.f.provider.describe; outcomes=[]; local=threading.local()
        def race(version):
            result=original(version)
            # Both have observed the unclaimed checkpoint before either writes
            # the durable start marker. Do not rely on scheduler luck.
            if version==V2 and not getattr(local,'observed',False):
                local.observed=True; barrier.wait(timeout=5)
            return result
        def worker(c):
            try: outcomes.append(self.x.restart().execute(c)['phase'])
            except (StateUnavailable,PreparationRejected): outcomes.append('refused')
        before=self.calls('encrypt')
        with patch.object(self.f.provider,'describe',side_effect=race):
            threads=[threading.Thread(target=worker,args=(c,)) for c in (a,b)]
            for t in threads:t.start()
            for t in threads:t.join(timeout=10)
        self.assertEqual(sorted(outcomes),['refused','waiting'])
        self.assertEqual(self.calls('encrypt')-before,1)
    def test_old_staged_initial_proposal_is_preserved_but_not_published_after_fence(self):
        # A prior lost current-row write must not silently resurrect an old KEK.
        old=self.x.old; p=self.f.who('orphan-proposal'); original=self.f.key_store.compare_exchange
        self.f.cipher=old; self.f.g.cipher=old; self.f.g.a.cipher=old
        def block(k,g,v):
            if k==old._key(p): raise StateUnavailable('current key write unavailable')
            return original(k,g,v)
        with patch.object(self.f.key_store,'compare_exchange',side_effect=block),self.assertRaises(StateUnavailable):
            self.f.login('orphan','orphan-proposal')
        pk=key('shared-grant-key-initial-proposal',digest([old.scope,p])); proposal=self.f.key_store.load(pk)
        self.assertIsNotNone(proposal)
        self.e.execute(self.x.command()); before=self.calls('encrypt')
        with self.assertRaisesRegex(StateUnavailable,'fenced'): old._dek(p,create=True)
        self.assertIsNone(old._read(p)); self.assertEqual(self.f.key_store.load(pk),proposal)
        self.assertEqual(self.calls('encrypt'),before)
    def test_corrupt_checkpoint_and_missing_audit_fail_closed(self):
        self.e.execute(self.x.command()); row=self.e._read()
        self.x.store.compare_exchange(self.e._key(),row[0],dict(row[1],cursor=2))
        with self.assertRaises(StateUnavailable): self.x.restart().inspect()
    def test_fifty_account_batch_fits_actual_sqlite_state_bound(self):
        x=PreparationFixture(self,accounts=50,steps=5); status=x.complete()
        self.assertEqual(status['cursor'],50); self.assertEqual(status['dispatches'],10)
        self.assertIs(status['complete_inventory_verified'],False)
    def test_no_secrets_or_wrapped_key_material_in_preparation_audit(self):
        self.x.complete(); text=repr(rows(self.x.audit))
        for p,c in self.x.sources.items():
            self.assertNotIn(c['wrapped_dek'],text)
            self.assertNotIn(self.f.cipher._read(p)[1]['wrapped_dek'],text)
        self.assertNotIn('private-refresh-token',text)
    def test_conflicting_rotation_cannot_replace_existing_fence(self):
        self.e.execute(self.x.command())
        before=self.f.key_audit.load(self.f.cipher._write_fence_key(V1))
        with self.assertRaises(StateUnavailable): self.f.cipher.fence_write_version(V1,target=V2,rotation='d'*64)
        self.assertEqual(self.f.key_audit.load(self.f.cipher._write_fence_key(V1)),before)


if __name__=='__main__': unittest.main()
