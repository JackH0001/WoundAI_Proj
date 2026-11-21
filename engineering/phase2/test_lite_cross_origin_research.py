"""Two real-format, independently enrolled synthetic installation domains."""
from base64 import b64encode
from copy import deepcopy
from unittest import TestCase
from unittest.mock import patch

from lite_attest_state import StateUnavailable
from lite_account_research_closure import AccountResearchClosure
from lite_account_data_inventory import AccountDataInventory
from lite_reconsent import ReconsentRejected
from lite_disposition_startup import research_associations
from lite_research_association import ResearchAssociations
from research_pair_fixture import pair_config
from test_lite_research_history import HistoryFixture
import test_lite_native_purchase as native


class SplitOwnerTests(TestCase):
    @classmethod
    def setUpClass(cls):native.legacy.LinksTests.setUpClass()
    def setUp(self):
        self.h=HistoryFixture(self)
        self.m=self.h.m
        self.f=self.m.f
        self.native=self.m.owner
        self.research='b'*32
        self.unrelated='c'*32
        self.cfg=pair_config(self.f,self.m.r,[(self.native,self.research)])
        self.links=research_associations(self.cfg)
        self.m.closure.associations=self.links
        self.f.close()

    def run_closure(self):
        return self.m.closure.advance(self.f.person)

    def test_two_ids_only_verified_research_subject_is_retired(self):
        r=self.run_closure()
        self.assertEqual(r['linked_grants'],1)
        self.assertTrue(self.m.r.privacy.is_withdrawn(self.research))
        self.assertIsNone(self.m.r.retirement(self.native))
        self.assertIsNone(self.m.r.retirement(self.unrelated))
        with self.assertRaises(ReconsentRejected):self.m.r.resolve(self.research,'legacy')

    def test_missing_proof_can_resume_with_same_audit_without_retiring_native_id(self):
        self.m.closure.associations=None
        self.assertEqual(self.run_closure()['waiting_for'],'verified_research_link_required')
        self.assertEqual(self.m.closure.status(self.f.person)['research_admission'],'pending')
        self.assertEqual(self.m.closure.status(self.f.person)['waiting_for'],'verified_research_link_required')
        self.assertIsNone(self.m.r.retirement(self.native))
        self.assertIsNone(self.m.r.retirement(self.research))
        self.m.closure.associations=self.links
        self.assertEqual(self.run_closure()['linked_grants'],1)
        self.assertIsNotNone(self.m.r.retirement(self.research))

    def test_equal_identifier_bytes_alone_cannot_authorize(self):
        self.m.closure.associations=research_associations(dict(self.cfg,proofs=[]))
        self.assertEqual(self.run_closure()['research_admission'],'pending')
        self.assertIsNone(self.m.r.retirement(self.native))

    def test_different_account_or_operation_or_registration_cannot_read_owner(self):
        pair=self.cfg['proofs'][0]['pair']
        args=[pair[k] for k in ('scope','bucket','person','native','operation','registration')]
        for i in range(len(args)):
            changed=args.copy();changed[i]='foreign'
            with self.subTest(field=i),self.assertRaises(StateUnavailable):self.links.resolve(*changed)

    def test_pair_cannot_change_after_worker_observed_it_even_if_first_retirement_failed(self):
        with patch.object(self.m.r,'retire',side_effect=StateUnavailable('lost')):
            with self.assertRaises(StateUnavailable):self.run_closure()
        alt=pair_config(self.f,self.m.r,[(self.native,self.unrelated)])
        self.m.closure.associations=research_associations(alt)
        with self.assertRaises(StateUnavailable):self.run_closure()
        self.assertIsNone(self.m.r.retirement(self.unrelated))
        self.m.closure.associations=self.links
        self.run_closure()
        self.assertIsNotNone(self.m.r.retirement(self.research))

    def test_privacy_inventory_does_not_read_an_assumed_owner(self):
        inv=AccountDataInventory(deletion=self.f.d,audit_store=self.f.store('new-inventory'),research=self.m.r)
        refs=[]
        with patch.object(self.m.r,'load',side_effect=AssertionError('cross-domain lookup')):
            val=inv._research(self.native,refs,person=self.f.person,operation='d'*32,registration='f'*64)
        self.assertEqual(val['state'],'research_association_pending')
        self.assertEqual(refs,[])

    def test_signatures_are_not_interchangeable_or_inferred(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        original=self.cfg['proofs'][0]
        corrupted=deepcopy(original);corrupted['pair']['research']=self.unrelated
        alternate_authority=b64encode(Ed25519PrivateKey.generate().public_key().public_bytes_raw()).decode()
        cases=[dict(self.cfg,research_key=self.cfg['native_key']),dict(self.cfg,research_key=alternate_authority),
               dict(self.cfg,proofs=[corrupted]),dict(self.cfg,proofs=[dict(original,research=original['native'])]),
               dict(self.cfg,proofs=[original]*2),dict(self.cfg,proofs=[{'pair':original['pair'],'native':original['native']}])]
        for i,spec in enumerate(cases):
            with self.subTest(i=i),self.assertRaises(ValueError):research_associations(spec)

    def test_prior_v1_incorrect_retirement_cannot_authorize_new_target(self):
        old=self.m.r.retire(self.native,closure_sha256='3'*64,association_sha256='4'*64)
        self.m.closure.associations=None
        self.assertEqual(self.run_closure()['waiting_for'],'verified_research_link_required')
        self.assertEqual(self.m.r.retirement(self.native),old)
        self.assertIsNone(self.m.r.retirement(self.research))
        self.m.closure.associations=self.links
        self.run_closure()
        self.assertEqual(self.m.r.retirement(self.native),old)
        self.assertIsNotNone(self.m.r.retirement(self.research))

    def test_read_only_inventory_uses_verified_source_and_retains_audit(self):
        # A native-ID namespace can contain an unrelated record; never read it.
        inv=AccountDataInventory(deletion=self.f.d,audit_store=self.f.store('mapped-inventory'),
                                 research=self.m.r,associations=self.links)
        self.run_closure()
        original=self.m.r._state
        def check_owner(owner):
            self.assertEqual(owner,self.research)
            return original(owner)
        with patch.object(self.m.r,'_state',side_effect=check_owner):
            ref=[];observation=inv._research(self.native,ref,person=self.f.person,operation='d'*32,
                registration=self.f.d.bridge.registry.registration(self.native)['proof'])
        self.assertEqual(observation['state'],'linked_epoch_references_observed')
        self.assertTrue(ref)
        self.assertEqual(observation['association_sha256'],self.m.r.retirement(self.research)['association_sha256'])

    def test_corrupt_pending_receipt_fails_closed(self):
        self.m.closure.associations=None
        self.run_closure()
        c=self.m.closure;p=c._plan(self.f.person);node=c.d.lifecycle._node(self.f.person,c._read(p)[1]['cursor'])
        from lite_subscription_executor import key
        from lite_usage_ledger import digest
        k=key('account-research-awaiting-link',digest([p,node['grant_id']]))
        rev,val=c.audit.load(k);c.audit.compare_exchange(k,rev,dict(val,reason='changed'))
        with self.assertRaisesRegex(StateUnavailable,'audit'):c.status(self.f.person)
        self.assertIsNone(self.m.r.retirement(self.research))

    def test_current_and_versioned_media_use_research_subject(self):
        from lite_fenced_objects import ObjectRef
        other=self.m.r.privacy
        ref=ObjectRef(self.research,'image','1'*16)
        twin=ObjectRef(self.native,'image','1'*16)
        for item in (ref,twin):
            ticket=other.begin_write(item.owner)
            other.manifest.write_registered(ticket,other.objects,item,b'synthetic unaffected history')
            other.finish_write(ticket)
        original=deepcopy(self.h.bucket.live[other.objects.name(twin)])
        self.m.finish()
        for _ in range(100):
            receipt=self.h.h.advance_account(self.f.person,versions=3,apply=True)
            if receipt['scan_finished']:break
        else:self.fail('research history scan did not finish')
        self.assertEqual(self.h.bucket.live[other.objects.name(twin)],original)
        self.assertEqual(sum(k[0]==other.objects.name(ref) for k in self.h.bucket.history),1)  # current sealed marker only
        self.assertTrue(any(k[0]==other.objects.name(twin) for k in self.h.bucket.history))
