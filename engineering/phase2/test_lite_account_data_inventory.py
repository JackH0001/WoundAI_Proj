"""Real SQLite account authorities, signed synthetic identities, no remote access."""
import copy
import unittest
from unittest.mock import patch
from lite_account_data_inventory import AccountDataInventory, POLICY
from lite_account_identity import IdentityRejected
from lite_attest_state import StateUnavailable
from lite_disposition_progress_probe import ProgressFixture
from lite_fenced_store import FencedPrivacy
from lite_reconsent import Reconsent, CONSENT, DISCLOSURE
from lite_subscription_executor import key
from lite_usage_ledger import digest
from test_lite_subscription_grace import rows
import test_lite_fenced_objects as objects
import test_lite_native_purchase as native


class InventoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): native.legacy.LinksTests.setUpClass()
    def setUp(self):
        self.f=ProgressFixture(self)
        self.audit=self.f.store('inventory-audit')
        self.i=AccountDataInventory(deletion=self.f.d,audit_store=self.audit)
    def inspect(self, **kw):return self.i.inspect(self.f.person, **kw)
    def source_rows(self):return {n:rows(s) for n,s in self.i.stores.items()}
    def research(self):
        self.owner='a'*32;self.f.f.login(self.owner)
        self.bucket=objects.Bucket()
        self.r=Reconsent(FencedPrivacy(self.f.store('research'),self.bucket),clock=self.f.f.f.now)
        self.i=AccountDataInventory(deletion=self.f.d,audit_store=self.audit,research=self.r)
    def rejoin(self, previous, new):
        self.r.privacy.scoped(previous).manifest.withdraw(self.owner)
        return self.r.resume(self.owner,dict(anon_id=self.owner,research_consent=True,
            previous_epoch=previous,new_epoch=new,consent_version=CONSENT,disclosure_version=DISCLOSURE))
    def test_active_account_cannot_be_inventoried(self):
        with self.assertRaises(IdentityRejected):self.inspect()
        self.assertEqual(rows(self.audit),[])
    def test_missing_closure_receipt_is_never_repaired_by_observer(self):
        self.f.close();life=self.f.f.lifecycle;life.audit=self.f.store('empty-lifecycle-audit')
        before=rows(life.audit)
        with self.assertRaises(StateUnavailable):self.inspect()
        self.assertEqual(rows(life.audit),before);self.assertEqual(rows(self.audit),[])
    def test_inventory_has_real_references_but_no_account_erasure_claim(self):
        self.f.close();r=self.inspect()
        self.assertEqual(r['total_grants'],1)
        self.assertEqual(r['grants'][0]['position'],1)
        self.assertFalse(r['account_deleted']);self.assertFalse(r['cleanup_authorized'])
        self.assertFalse(r['whole_account_inventory_complete'])
        self.assertIn('metadata_writer_drain',r['pending'])
        self.assertTrue(r['subscription']['stable_present'])
        self.assertTrue(self.i.verify_unchanged(r['report_sha256'])['sources_unchanged'])
    def test_replay_same_clock_same_hash_and_no_duplicate_audit(self):
        self.f.close();a=self.inspect();before=rows(self.audit)
        self.assertEqual(a,self.inspect());self.assertEqual(before,rows(self.audit))
    def test_restart_can_verify_original_observation(self):
        self.f.close();r=self.inspect()
        other=AccountDataInventory(deletion=self.f.d,audit_store=self.audit)
        self.assertFalse(other.verify_unchanged(r['report_sha256'])['cleanup_authorized'])
    def test_no_original_state_media_quota_or_provider_side_effects(self):
        self.f.complete();self.f.close();before=self.source_rows(),self.f.preserved(),bytes(self.f.capture)
        calls=self.f.f.kms.calls.copy(),self.f.f.g.calls.copy(),self.f.calls
        r=self.inspect();self.i.verify_unchanged(r['report_sha256'])
        self.assertEqual(before,(self.source_rows(),self.f.preserved(),self.f.capture))
        self.assertEqual(calls,(self.f.f.kms.calls,self.f.f.g.calls,self.f.calls))
    def test_credentials_and_account_values_never_copied_into_inventory(self):
        self.f.close();r=self.inspect();raw=repr(r)+repr(rows(self.audit))
        account=self.f.f.b._read('device-one')[1]['account']
        for secret in (account['token'],account['subject'],'device-one','private-refresh-token',
                       'private-access-token','apple-human-1',self.f.f.g.signed):
            self.assertNotIn(secret,raw)
    def test_same_account_all_devices_and_prior_logins(self):
        self.f.f.login('device-two');self.f.f.login('device-one');self.f.close()
        r=self.inspect();self.assertEqual(len(r['grants']),3)
        self.assertEqual(len({x['installation_sha256'] for x in r['grants']}),2)
        self.assertEqual(len({x['grant_id'] for x in r['grants']}),3)
    def test_historical_family_not_just_latest_session(self):
        first=self.f.f.b._read('device-one')[1];self.f.f.login()
        k=key('human-session-family',digest([self.f.f.b.namespace,'device-one',first['operation']]))
        self.f.f.b.store.compare_exchange(k,0,dict(v=1,operation=first['operation'],account_sha256=digest(first['account']),
            renewal_generation=0,renewed_at=first['created_at'],expires_at=first['session_expires_at'],
            family_expires_at=first['created_at']+30*86400,renewal_operation=None))
        self.f.close();r=self.inspect()
        self.assertTrue(any(x['key']==k.hex() and x['generation']>0 for x in self.i.references(r['report_sha256'])))
    def test_foreign_family_is_rejected(self):
        c=self.f.f.b._read('device-one')[1]
        k=key('human-session-family',digest([self.f.f.b.namespace,'device-one',c['operation']]))
        self.f.f.b.store.compare_exchange(k,0,dict(operation=c['operation'],account_sha256='f'*64))
        self.f.close()
        with self.assertRaises(StateUnavailable):self.inspect()
    def test_foreign_registration_is_rejected(self):
        self.f.close();reg=self.f.f.b.registry;k=reg.key('device-one');v,r=reg.store.load(k)
        reg.store.compare_exchange(k,v,dict(r,subject='apple-'+'f'*64))
        with self.assertRaises(StateUnavailable):self.inspect()
    def test_corrupt_account_is_rejected_without_reset(self):
        self.f.close();b=self.f.f.b;k=key('human-account',self.f.person);v,r=b.store.load(k)
        b.store.compare_exchange(k,v,dict(r,token='bad'))
        with self.assertRaises(StateUnavailable):self.inspect()
        self.assertEqual(b.store.load(k)[1]['token'],'bad')
    def test_foreign_grant_is_rejected(self):
        self.f.close();n=self.f.f.lifecycle.page(self.f.person)['items'][0]
        k=key('apple-grant',n['grant_id']);s=self.f.f.g.store;v,r=s.load(k)
        s.compare_exchange(k,v,dict(r,context=dict(r['context'],identity_sha256='f'*64)))
        with self.assertRaises(StateUnavailable):self.inspect()
    def test_another_account_not_listed_or_changed(self):
        self.f.f.login('other','apple-human-2');other=self.f.f.person;self.f.close()
        before=self.source_rows();r=self.inspect()
        self.assertEqual(r['total_grants'],1);self.assertNotIn(other,repr(r))
        self.assertEqual(before,self.source_rows())
    def test_unknown_prior_installations_never_guessed(self):
        self.f.close();r=self.inspect()
        self.assertEqual(r['grants'][0]['research']['state'],'authority_not_configured')
        self.assertFalse(r['grants'][0]['research']['complete'])
    def test_invalid_limits_before_audit(self):
        self.f.close()
        for n in (0,6,True,'1',1.5):
            with self.assertRaises(ValueError):self.inspect(limit=n)
        self.assertEqual(rows(self.audit),[])
    def test_pagination_more_than_one_hundred_grants_no_full_snapshot_claim(self):
        for n in range(105):self.f.f.lifecycle.reserve_grant(self.f.person,'not-finished',f'{n+100:032x}')
        self.f.close();cursor=None;found=[]
        while True:
            r=self.inspect(cursor=cursor);found+=r['grants'];cursor=r['next_cursor']
            self.assertLessEqual(len(r['grants']),5)
            self.assertFalse(r['whole_account_inventory_complete'])
            if cursor is None:break
        self.assertEqual([x['position'] for x in found],list(range(106,0,-1)))
        self.assertTrue(any(not x['registration_present'] for x in found))
    def test_missing_or_tampered_report_not_authorized(self):
        self.f.close();r=self.inspect();k=key('account-data-inventory-page',r['report_sha256']);v,j=self.audit.load(k)
        self.audit.compare_exchange(k,v,dict(j,account_deleted=True))
        for h in (r['report_sha256'],'f'*64):
            with self.assertRaises(StateUnavailable):self.i.verify_unchanged(h)
    def test_changed_generation_even_same_content_invalidates_report(self):
        self.f.close();r=self.inspect();k=self.f.f.b._key('device-one');v,j=self.f.f.b.store.load(k)
        self.f.f.b.store.compare_exchange(k,v,j)
        with self.assertRaises(StateUnavailable):self.i.verify_unchanged(r['report_sha256'])
    def test_absent_family_created_after_snapshot_invalidates_report(self):
        self.f.close();r=self.inspect();c=self.f.f.b._read('device-one')[1]
        k=key('human-session-family',digest([self.f.f.b.namespace,'device-one',c['operation']]))
        self.f.f.b.store.compare_exchange(k,0,dict(operation=c['operation'],account_sha256=digest(c['account'])))
        with self.assertRaises(StateUnavailable):self.i.verify_unchanged(r['report_sha256'])
    def test_drift_while_observing_prevents_audit_commit(self):
        self.f.close();real=self.i._subscription
        def change(a,refs):
            result=real(a,refs);k=key('human-account',self.f.person);v,j=self.f.f.b.store.load(k)
            self.f.f.b.store.compare_exchange(k,v,j);return result
        with patch.object(self.i,'_subscription',side_effect=change),self.assertRaises(StateUnavailable):self.inspect()
        self.assertEqual(rows(self.audit),[])
    def test_audit_failure_does_not_change_original_data(self):
        self.f.close();before=self.source_rows()
        with patch.object(self.audit,'compare_exchange',side_effect=StateUnavailable('offline')):
            with self.assertRaises(StateUnavailable):self.inspect()
        self.assertEqual(before,self.source_rows())
        self.assertTrue(self.inspect()['report_sha256'])
    def test_alias_sources_or_wrong_authority_refused(self):
        for s in self.i.stores.values():
            with self.assertRaises(ValueError):AccountDataInventory(deletion=self.f.d,audit_store=s)
        with self.assertRaises(ValueError):AccountDataInventory(deletion=object(),audit_store=self.audit)
        with self.assertRaises(ValueError):AccountDataInventory(deletion=self.f.d,audit_store=self.audit,research=object())
    def test_research_current_and_prior_epochs_observed_without_media_access(self):
        self.research();self.rejoin('legacy','b'*32);self.rejoin('b'*32,'c'*32)
        self.f.close();before=self.source_rows(),copy.deepcopy(self.bucket.live)
        calls=self.bucket.calls.copy();r=self.inspect()
        record=next(x for x in r['grants'] if x['installation_sha256']==digest(self.owner))['research']
        self.assertEqual(record['epochs'],3);self.assertFalse(record['complete'])
        self.assertFalse(record['research_withdrawn_by_inventory'])
        self.assertEqual(before,(self.source_rows(),self.bucket.live));self.assertEqual(calls,self.bucket.calls)
    def test_consent_version_change_does_not_hide_historical_epochs(self):
        self.research();self.rejoin('legacy','b'*32);self.f.close()
        with patch('lite_reconsent.CONSENT','future'),patch('lite_reconsent.DISCLOSURE','future'):
            self.assertEqual(next(x for x in self.inspect()['grants'] if x['installation_sha256']==digest(self.owner))['research']['epochs'],2)
    def test_research_history_hash_corruption_fails_without_repair(self):
        self.research();self.rejoin('legacy','b'*32);self.rejoin('b'*32,'c'*32)
        k=self.r.key(self.owner,'b'*32);v,j=self.r.store.load(k)
        self.r.store.compare_exchange(k,v,dict(j,owner='f'*32));self.f.close()
        with self.assertRaises(StateUnavailable):self.inspect()
        self.assertEqual(rows(self.audit),[])
    def test_research_epoch_bound_is_reported_incomplete(self):
        self.research();previous='legacy'
        for n in range(18):
            new=f'{n+100:032x}';self.rejoin(previous,new);previous=new
        self.f.close();r=self.inspect()
        research=next(x for x in r['grants'] if x['installation_sha256']==digest(self.owner))['research']
        self.assertEqual(research['state'],'epoch_limit_requires_followup')
        self.assertEqual(research['epochs'],16);self.assertFalse(research['complete'])
    def test_reference_chunk_tampering_fails_readback(self):
        self.f.close();r=self.inspect();k=key('account-data-inventory-refs',r['reference_chunks'][0])
        v,j=self.audit.load(k);self.audit.compare_exchange(k,v,dict(j,references=[]))
        with self.assertRaises(StateUnavailable):self.i.verify_unchanged(r['report_sha256'])
    def test_profile_cannot_silently_switch_research_store_binding(self):
        self.f.close();r=self.inspect()
        privacy=Reconsent(FencedPrivacy(self.f.store('research'),objects.Bucket()))
        other=AccountDataInventory(deletion=self.f.d,audit_store=self.audit,research=privacy)
        with self.assertRaises(StateUnavailable):other.verify_unchanged(r['report_sha256'])
    def test_missing_reference_chunk_not_repaired(self):
        self.f.close();r=self.inspect();original=self.audit.load
        def missing(k):
            if k==key('account-data-inventory-refs',r['reference_chunks'][0]):return None
            return original(k)
        with patch.object(self.audit,'load',side_effect=missing),self.assertRaises(StateUnavailable):
            self.i.verify_unchanged(r['report_sha256'])
    def test_lost_audit_acknowledgment_replays_identical_snapshot(self):
        self.f.close();original=self.audit.compare_exchange
        def lost(*args):original(*args);raise StateUnavailable('lost acknowledgment')
        with patch.object(self.audit,'compare_exchange',side_effect=lost),self.assertRaises(StateUnavailable):self.inspect()
        r=self.inspect();self.assertTrue(self.i.verify_unchanged(r['report_sha256'])['sources_unchanged'])
    def test_new_research_epoch_after_scan_invalidates_original_observation(self):
        self.research();self.f.close();r=self.inspect();self.rejoin('legacy','b'*32)
        with self.assertRaises(StateUnavailable):self.i.verify_unchanged(r['report_sha256'])
    def test_five_devices_sixteen_epochs_each_fit_security_state_bound(self):
        self.research()
        for i in range(5):
            self.owner=f'{i+200:032x}';self.f.f.login(self.owner);previous='legacy'
            for n in range(16):
                new=f'{n+100:032x}';self.rejoin(previous,new);previous=new
        self.f.close();r=self.inspect()
        self.assertEqual(len(r['grants']),5);self.assertGreater(len(r['reference_chunks']),1)
        self.assertTrue(self.i.verify_unchanged(r['report_sha256'])['sources_unchanged'])
    def test_subscription_guard_corruption_cannot_be_reported_as_free(self):
        from lite_service_subscription_links import stable_subscription_key
        self.f.close();a=self.f.f.b._read('device-one')[1]['account'];s=self.i.links
        k=stable_subscription_key(s.policy,a['subject'],'guard');v,j=s.store.load(k)
        s.store.compare_exchange(k,v,dict(j,token_hash='f'*64))
        with self.assertRaises(StateUnavailable):self.inspect()
        self.assertEqual(rows(self.audit),[])
    def test_foreign_inventory_cursor_rejected(self):
        self.f.f.login('other','apple-human-2');other=self.f.f.person
        self.f.f.close('other');cursor=self.f.f.lifecycle._read(other)[1]['head'];self.f.close()
        with self.assertRaises(StateUnavailable):self.inspect(cursor=cursor)


if __name__=='__main__':unittest.main()
