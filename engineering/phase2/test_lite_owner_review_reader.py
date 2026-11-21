"""Offline readback using rows written by the actual first-owner confirmation."""
import copy
import json
import unittest
from unittest.mock import Mock

import test_lite_owner_review as web
from test_lite_attest_state import FakeBucket
from lite_attest_state import GCSStateStore, StateUnavailable
from lite_owner_review_reader import ExactReader, observe, prepare
from lite_usage_ledger import digest

class ReadbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        web.OwnerReviewTests.setUpClass()

    def setUp(self):
        fixture = web.OwnerReviewTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        fixture.challenge()
        self.assertEqual(fixture.submit().status_code, 202)
        bridge = fixture.backend.identity
        self.now = fixture.h.clock()
        self.installation, self.op = fixture.owner, fixture.op
        number = '12345678910'
        self.bucket_name = 'woundai-lite-native-state-' + number
        self.native = dict(schema='lite-native-startup/3', enabled=True, namespace=bridge.namespace,
            app={'bundle':'com.woundai.lite'}, apple={'environment':'Sandbox'},
            deployment={'source':'a'*40,'image':'b'*64,'project_number':number},
            buckets={self.bucket_name:{}},
            stores={role:dict(bucket=self.bucket_name, prefix='lite-native/demo/'+role)
                    for role in ('identity','identity_audit','accounts')},
            secrets={'identity':{'sha256':bridge.config['identity_key_sha256']}})
        self.plan = dict(schema='lite-owner-readback/1', native_sha256=digest(self.native),
            source='a'*40,image='b'*64,project_number=number,installation_sha256=fixture.pin,
            operation=self.op,starts=self.now,until=fixture.until)
        self.proposal = fixture.read()
        self.bucket = FakeBucket()
        self.client = Mock()
        self.client.bucket.return_value = self.bucket
        _, self.objects = prepare(self.raw(),self.plan,digest(self.plan),installation=self.installation,
                                  operation=self.op,now=self.now)
        for name, backing in [('config',bridge.store),('session',bridge.store),
                              ('proposal',bridge.audit),('marker',bridge.audit)]:
            _, prefix, k = self.objects[name]
            GCSStateStore(self.bucket,prefix=prefix).compare_exchange(k,0,backing.load(k)[1])
        self.bucket.calls.clear()

    def raw(self):
        return json.dumps(self.native,sort_keys=True,separators=(',',':')).encode()

    def read(self, *, nonce=None, pin=None, clock=None):
        return observe(self.raw(),self.plan,pin or digest(self.plan),installation=self.installation,
                       operation=nonce or self.op, client=self.client,clock=clock or (lambda:self.now))

    def write(self, name, mutate):
        _,prefix,k = self.objects[name]
        store=GCSStateStore(self.bucket,prefix=prefix)
        row=store.load(k)
        store.compare_exchange(k, row[0] if row else 0, mutate(copy.deepcopy(row[1]) if row else {}))

    def test_verified_account_only_and_five_exact_objects(self):
        receipt=self.read()
        self.assertEqual(receipt['account_sha256'],self.proposal['account_sha256'])
        self.assertEqual(set(receipt),{'schema','plan','account_sha256','observed_at','valid_until','snapshot_sha256','generations'})
        self.assertIsNone(receipt['generations']['registration'])
        self.assertEqual(receipt['valid_until'],self.now+60)
        permitted={prefix+'/'+k.hex()+'.json' for _,prefix,k in self.objects.values()}
        self.assertEqual({c[1] for c in self.bucket.calls},permitted)
        self.assertEqual([c[0] for c in self.bucket.calls].count('reload'),10)
        self.assertEqual([c[0] for c in self.bucket.calls].count('download'),8)
        self.assertFalse(any(c[0]=='upload' for c in self.bucket.calls))
        self.assertEqual(self.read(),receipt)

    def test_wrong_plan_native_pin_or_operation_precedes_any_bucket_access(self):
        for kwargs in ({'pin':'0'*64},{'nonce':'0'*32}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):self.read(**kwargs)
        self.native['enabled']=False
        with self.assertRaises(ValueError):self.read()
        self.client.bucket.assert_not_called()

    def test_wrong_bucket_in_rehashed_config_rejected(self):
        self.native['stores']['accounts']['bucket']='other-media'
        self.native['buckets']['other-media']={}
        self.plan['native_sha256']=digest(self.native)
        with self.assertRaises(ValueError):self.read()
        self.client.bucket.assert_not_called()

    def test_outside_window_never_reaches_provider(self):
        for n in [self.now-1,self.plan['until']]:
            with self.subTest(n=n), self.assertRaises(ValueError):self.read(clock=lambda:n)
        self.client.bucket.assert_not_called()

    def test_even_unchanged_content_under_new_generation_invalidates_receipt(self):
        times=iter((self.now, self.now, self.now+1, self.now+1))
        def clock():
            now=next(times)
            if now>self.now and not getattr(clock,'changed',False):
                clock.changed=True
                self.write('proposal',lambda p:p)
            return now
        with self.assertRaises(StateUnavailable):self.read(clock=clock)

    def test_clock_moves_back_or_too_much_time_passes(self):
        for sequence in ((0,-1),(0,0,60),(0,0,30,29),(0,0,30,60)):
            times=iter(self.now+n for n in sequence)
            with self.subTest(sequence=sequence), self.assertRaises((ValueError,StateUnavailable)):
                self.read(clock=lambda:next(times))

    def test_session_changed_or_installation_registered(self):
        self.write('session',lambda s:dict(s,phase='ended'))
        with self.assertRaises(ValueError):self.read()
        self.write('registration',lambda s:{'v':1})
        with self.assertRaises(ValueError):self.read()

    def test_boolean_marker_version_cannot_impersonate_integer(self):
        self.write('marker',lambda p:dict(p,v=True))
        with self.assertRaises(ValueError):self.read()

    def test_expected_missing_registration_does_not_mask_storage_error(self):
        self.bucket.fail_reload=PermissionError('unavailable')
        with self.assertRaises(StateUnavailable):self.read()

    def test_exact_reader_has_no_write_and_rejects_other_key(self):
        _,prefix,k=self.objects['session']
        lane=ExactReader(self.bucket,prefix,{k})
        with self.assertRaises(StateUnavailable):lane.load(bytes(32))
        self.assertFalse(hasattr(lane,'compare_exchange'))
        self.assertEqual(self.bucket.calls,[])


    def test_registration_appearing_between_passes_preserves_original_proposal(self):
        times=iter((self.now,self.now,self.now+1,self.now+1))
        def clock():
            value=next(times)
            if value>self.now and not getattr(clock,'registered',False):
                clock.registered=True
                self.write('registration',lambda row:{'v':1})
            return value
        with self.assertRaises(StateUnavailable):
            self.read(clock=clock)
        _, prefix, key=self.objects['proposal']
        self.assertEqual(GCSStateStore(self.bucket,prefix=prefix).load(key)[1],self.proposal)

    def test_wrong_installation_cannot_open_any_bucket(self):
        with self.assertRaises(ValueError):
            observe(self.raw(),self.plan,digest(self.plan),installation='different-installation',
                    operation=self.op,client=self.client,clock=lambda:self.now)
        self.client.bucket.assert_not_called()

    def test_registration_missing_and_registration_unreadable_are_distinct(self):
        from google.api_core.exceptions import Forbidden
        original=self.bucket.blob
        _,prefix,key=self.objects['registration']
        name=prefix+'/'+key.hex()+'.json'
        def guarded(object_name,generation=None):
            blob=original(object_name,generation=generation)
            if object_name==name:
                blob.reload=Mock(side_effect=Forbidden('permission denied'))
            return blob
        self.bucket.blob=guarded
        with self.assertRaises(StateUnavailable):
            self.read()

    def test_valid_signed_proposal_still_inspectable_after_login_challenge_expires(self):
        result=self.read(clock=lambda:self.now+310)
        self.assertEqual(result['account_sha256'],self.proposal['account_sha256'])
        self.assertLessEqual(result['valid_until'],self.plan['until'])
