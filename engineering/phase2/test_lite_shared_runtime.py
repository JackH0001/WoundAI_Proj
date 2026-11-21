"""Opt-in shared startup, signed HTTP and private worker; synthetic transports."""
import copy
import re
import unittest
from pathlib import Path
from unittest.mock import patch
from lite_attest_state import StateUnavailable
from lite_disposition_progress import CATALOG, SHARED_CATALOG, SHARED_PENDING_LANES
from lite_disposition_executor import PATH
from lite_rotating_grant_keys import RotatingGrantCipher, RotatingGrantKeyProvider, SHARED_SCHEME
from lite_subscription_executor import key
from lite_usage_ledger import digest, free_grant
from test_lite_rotating_grant_keys import MultiVersionKMS
from test_lite_grant_key_custody import KeyFixture
from test_lite_subscription_grace import rows
import test_lite_native_startup as native
import test_lite_disposition_startup as disposition


def spec(ring):
    resource=ring+'/cryptoKeys/shared-runtime'
    return dict(scheme=SHARED_SCHEME,crypto_key=resource,write_version=resource+'/cryptoKeyVersions/1',
                read_versions=[resource+'/cryptoKeyVersions/1',resource+'/cryptoKeyVersions/2'])


class SharedNativeFixture(native.Fixture):
    def __init__(self,host):
        super().__init__(host)
        self.isolate_secret_mounts();self.config['schema']='lite-native-startup/3'
        self.config['grant_keys']=spec(self.config['deployment']['keyring'])
        self.kms=MultiVersionKMS()
        for v in self.config['grant_keys']['read_versions']:self.kms.provision(v)
        self.providers.kms=native.startup.RuntimeKMSSession(self.kms)


class SharedNativeHTTPTests(native.StartupHTTPTests):
    """Run the same nine signed service/usage scenarios against shared startup."""
    def setUp(self):
        with patch.object(native,'Fixture',SharedNativeFixture):super().setUp()

    def test_explicit_shared_graph_and_readonly_deletion_progress(self):
        self.assertIs(type(self.backend.identity.grants.cipher),RotatingGrantCipher)
        self.assertEqual(self.backend.progress.catalog,SHARED_CATALOG)
        self.login();self.complete(1);original=self.f.jpeg;quota=self.quota()
        subject=self.h.release.n.accounts.resolve(self.owner)['subject']
        r=self.post('account/deletion/request',operation='d'*32,confirmation='delete_account')
        self.assertEqual(r.status_code,202,r.json)
        result=self.backend.progress.advance(self.person)
        self.assertEqual(result['disposition']['grant_key_state'],'backup_pending')
        self.assertEqual(result['disposition']['pending_lanes'],SHARED_PENDING_LANES)
        self.assertFalse(result['account_deleted']);self.assertEqual(self.f.jpeg,original)
        # Closure intentionally blocks account resolution. Inspect the original
        # ledger directly instead of trying to use that closed service account.
        self.assertEqual(self.h.release.ledger.snapshot(subject,self.h.clock()//86400,
            free_grant(subject,self.h.clock())),quota)
        before=self.h.kms.calls.copy(),self.h.grant.calls.copy(),rows(self.backend.progress.audit)
        for _ in range(3):
            self.assertEqual(self.backend.deletion.status(self.owner,'d'*32),result)
        self.assertEqual(before,(self.h.kms.calls,self.h.grant.calls,rows(self.backend.progress.audit)))
        self.assertNotIn('destroy',[a for a,_ in self.h.kms.calls])
        self.assertNotIn('cryptoKeys',repr(result))

    def test_shared_receipt_cannot_claim_complete_erasure_or_change_types(self):
        self.login();self.post('account/deletion/request',operation='d'*32,confirmation='delete_account')
        self.backend.progress.advance(self.person)
        p=self.backend.progress._plan(self.person)
        r=self.backend.progress._read(p)[1]['grant_key']
        for change in ({'grant_key_unrecoverable':True},{'retirement_inventory_verified':True},
                       {'account_deleted':True},{'grant_key_unrecoverable':0},{'versions':[]}):
            with self.subTest(change=change),self.assertRaises(StateUnavailable):
                self.backend.progress._validate_key(p,dict(r,**change))

    def test_lost_shared_progress_observation_never_reconstructed_by_status(self):
        self.login();self.post('account/deletion/request',operation='d'*32,confirmation='delete_account')
        self.backend.progress.advance(self.person)
        p=self.backend.progress._plan(self.person);r=self.backend.progress._read(p)[1]['grant_key']
        c=self.backend.progress.cipher;target=key('shared-grant-key-closure-observed',digest(r));load=c.audit.load
        before=rows(c.audit),self.h.kms.calls.copy()
        with patch.object(c.audit,'load',side_effect=lambda k:None if k==target else load(k)),self.assertRaises(StateUnavailable):
            self.backend.deletion.status(self.owner,'d'*32)
        self.assertEqual((rows(c.audit),self.h.kms.calls),before)


class SharedPreflightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):native.StartupPreflightTests.setUpClass()
    def setUp(self):self.f=SharedNativeFixture(self)
    def reject(self,config):
        before=self.f.all_rows()
        with self.assertRaises((ValueError,StateUnavailable)):
            native.startup.build_native_runtime(**self.f.arguments(config))
        self.assertEqual(self.f.all_rows(),before);self.assertEqual(self.f.providers.calls,[])
    def test_shared_mode_requires_new_schema_and_pinned_versions(self):
        c=self.f.config
        for change in ({'schema':'lite-native-startup/2'}, {'grant_keys':None},
                       {'key_preparation':{'origin':'https://synthetic.a.run.app'}}):
            bad=copy.deepcopy(c);bad.update(change);self.reject(bad)
        bad=copy.deepcopy(c);bad.pop('grant_keys');self.reject(bad)
    def test_cross_key_duplicate_extra_and_unlisted_write_pins_rejected(self):
        for change in ({'write_version':'unlisted'}, {'read_versions':[]}, {'extra':True},
                       {'scheme':'account-kms-envelope-1'}, {'crypto_key':'projects/other/cryptoKeys/other'},
                       {'read_versions':[self.f.config['grant_keys']['write_version']]*2}):
            bad=copy.deepcopy(self.f.config);bad['grant_keys'].update(change);self.reject(bad)
    def test_shared_schema_requires_separate_secret_mounts(self):
        ref=self.f.config['secrets']['oauth'];original=self.f.secret_dir/'oauth'/ref['file']
        original.rename(self.f.secret_dir/'oauth.p8');ref['file']='oauth.p8'
        self.reject(self.f.config)
    def test_legacy_vault_cannot_change_scheme_by_changing_configuration(self):
        from lite_grant_key_custody import AccountGrantCipher,GoogleGrantKeyProvider
        b=self.f.compose();c=b.identity.grants.cipher
        with self.assertRaises(StateUnavailable):AccountGrantCipher(
            provider=GoogleGrantKeyProvider(session=self.f.kms,keyring=c.provider.keyring),
            lifecycle=c.lifecycle,store=c.store,audit_store=c.audit)
        self.assertEqual(c.mode['scheme'],SHARED_SCHEME)
    def test_swift_catalog_pins_and_pending_language_match_backend(self):
        swift=(Path(__file__).resolve().parents[2]/'iOS/WoundLite/LiteAccountDeletion.swift').read_text()
        self.assertIn('static let sharedCatalog="'+SHARED_CATALOG+'"',swift)
        self.assertIn('static let catalog="'+CATALOG+'"',swift)
        self.assertIn('sharedPending=pending+["shared_key_retirement"]',swift)
        self.assertIn('目前存取已停止；歷史加密副本仍待處理',swift)


class SharedWorkerKeyFixture(KeyFixture):
    def make_cipher(self):
        if type(self.provider) is not RotatingGrantKeyProvider:
            ring=self.provider.keyring;settings=spec(ring);self.kms=MultiVersionKMS()
            self.provider=RotatingGrantKeyProvider(session=self.kms,keyring=ring,
                **{k:v for k,v in settings.items() if k!='scheme'})
        return RotatingGrantCipher(provider=self.provider,lifecycle=self.lifecycle,
            store=self.key_store,audit_store=self.key_audit)


class SharedDispositionFixture(disposition.Fixture):
    def __init__(self,host):
        with patch('lite_disposition_progress_probe.KeyFixture',SharedWorkerKeyFixture):super().__init__(host)
        self.c['schema']='lite-disposition-startup/3'
        self.c['grant_keys']=spec(self.c['deployment']['keyring'])
        self.providers.kms=disposition.startup.DispositionKMSSession(self.f.f.f.kms,
            self.c['deployment']['keyring'],digest(self.c['binding']),[self.person],shared_keys=self.c['grant_keys'])
    def finish_shared(self):
        for _ in range(12):
            e=self.build();response=e.create_app().test_client().post(PATH,json=dict(command=self.command(),drain=None))
            assert response.status_code==200,response.json
            if all(s=='observed' for s in e.w.status(self.person)['states'].values()):return response.json
        raise AssertionError('shared account work did not finish')


class SharedDispositionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):disposition.StartupTests.setUpClass()
    def setUp(self):self.f=SharedDispositionFixture(self)
    def test_private_startup_has_shared_catalog_and_no_provider_side_effects(self):
        before=self.f.snap();e=self.f.build()
        self.assertEqual(before,self.f.snap());self.assertEqual(e.w.p.catalog,SHARED_CATALOG)
        self.assertIs(type(e.w.p.cipher),RotatingGrantCipher)
    def test_signed_worker_preserves_records_and_never_destroys_shared_kek(self):
        preserved=self.f.f.f.preserved();capture=self.f.f.f.capture
        result=self.f.finish_shared();status=self.f.e.w.status(self.f.person)
        self.assertTrue(all(s=='observed' for s in result['worker']['states'].values()))
        self.assertIn('shared_key_retirement',result['worker']['pending_lanes'])
        self.assertIn('shared_key_retirement',status['pending_lanes'])
        self.assertFalse(result['account_deleted'])
        receipt=status['observations']['grant_key']
        self.assertFalse(receipt['grant_key_unrecoverable']);self.assertFalse(receipt['retirement_inventory_verified'])
        self.assertEqual(self.f.f.f.preserved(),preserved);self.assertEqual(self.f.f.f.capture,capture)
        self.assertEqual(self.f.f.f.calls,1)
        self.assertNotIn('destroy',[a for a,_ in self.f.f.f.f.kms.calls])
    def test_shared_worker_cannot_encrypt_create_disable_restore_or_destroy(self):
        s=self.f.providers.kms;version=next(iter(s.versions));resource=next(iter(s.keys))
        before=self.f.snap()
        for method,url in [('POST',version+':destroy'),('POST',version+':disable'),('POST',version+':restore'),
                           ('POST',version+':encrypt'),('POST',resource+':encrypt'),('POST',resource+'/cryptoKeyVersions')]:
            with self.subTest(url=url),self.assertRaises(StateUnavailable):s.request(method,url)
        self.assertEqual(before,self.f.snap())
    def test_worker_schema_and_vault_mode_must_agree(self):
        self.f.c['schema']='lite-disposition-startup/2'
        with self.assertRaises(ValueError):self.f.build()
        self.assertEqual(self.f.providers.calls,[])
        self.f.c.pop('grant_keys')
        with self.assertRaises(StateUnavailable):self.f.build()
    def test_missing_shared_pins_rejected_before_provider(self):
        self.f.c.pop('grant_keys')
        with self.assertRaises(ValueError):self.f.build()
        self.assertEqual(self.f.providers.calls,[])
    def test_same_completed_command_replays_without_repeat_apple_or_kms(self):
        e=self.f.build();command=self.f.command();first=e.execute(command);before=self.f.snap()
        self.assertEqual(self.f.build().execute(command),first);self.assertEqual(before,self.f.snap())
    def test_missing_closed_key_evidence_does_not_claim_shared_lane_observed(self):
        self.f.finish_shared();e=self.f.e;p=e.w.p;plan=p._plan(self.f.person)
        receipt=p._read(plan)[1]['grant_key'];cipher=p.cipher
        target=key('shared-grant-key-closure-observed',digest(receipt));load=cipher.audit.load
        with patch.object(cipher.audit,'load',side_effect=lambda k:None if k==target else load(k)),self.assertRaises(StateUnavailable):
            e.w.status(self.f.person)


if __name__=='__main__':unittest.main()
