"""Native source-ticket route under actual synthetic App Attest + Apple login."""
import unittest
from unittest.mock import patch
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from lite_attest_http import install_lite_attest
from lite_research_source_issuer import ResearchSourceIssuer, PATH
import test_lite_account_identity as identity_fixture
import test_lite_service_http as native_fixture


class ResearchSourceHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): identity_fixture.IdentityHTTPTests.setUpClass()

    def setUp(self):
        self.host=identity_fixture.IdentityHTTPTests()
        self.key=Ed25519PrivateKey.generate()
        def install(app, **kw):
            self.issuer=ResearchSourceIssuer(scope='d'*64,bucket='synthetic-research',
                private_key=self.key.private_bytes_raw(),operations=kw['commerce'])
            return install_lite_attest(app,**kw,research_source=self.issuer)
        with patch('lite_attest_http.install_lite_attest',side_effect=install): self.host.setUp()
        self.addCleanup(self.host.doCleanups)
        self.h=self.host.h
        self.fields=dict(research='b'*32,consent_epoch='legacy',consent_version='2026-10-16.1',
            capture_id='fe000000-0000-4000-8000-000000000001',image_sha256=self.h.image_hash,operation=None)

    def issue(self,**changes): return self.host.post(PATH,self.fields | changes)

    def test_attestation_without_personal_login_cannot_issue_ticket(self):
        r=self.issue(); self.assertEqual(r.status_code,401,r.json)
        self.assertEqual(r.json['error'],'lite_sign_in_required')

    def test_explicit_login_manual_ticket_requires_no_model_or_purchase(self):
        self.host.login()
        with patch.object(self.issuer.operations.accounts,'grant',side_effect=AssertionError('no quota lookup')):
            r=self.issue(); self.assertEqual(r.status_code,200,r.json)
        p=self.issuer.verifier.verify(r.json,owner='b'*32,consent_epoch='legacy')
        self.assertEqual(p['source'],'manual'); self.assertEqual(p['native_installation'],self.h.owner)
        self.assertEqual(self.h.model_calls,0)

    def test_logout_immediately_blocks_source_route_but_keeps_original_receipts(self):
        login=self.host.login(); before=self.issue(); self.assertEqual(before.status_code,200,before.json)
        r=self.host.post('/api/v1/lite/account/logout',dict(operation=login['operation']))
        self.assertEqual(r.status_code,200,r.json)
        self.assertEqual(self.issue().status_code,401); self.assertEqual(self.h.model_calls,0)

    def test_client_cannot_supply_ai_result_or_other_native_owner(self):
        self.host.login()
        self.assertEqual(self.issue(result={}).status_code,400)
        self.assertEqual(self.issue(anon_id='e'*32).status_code,403)
        self.assertEqual(self.issue(operation='f'*32).status_code,409)
        self.assertEqual(self.h.model_calls,0)

    def test_source_route_cannot_bypass_guard_with_unsigned_request(self):
        self.host.login()
        r=self.h.client.post(PATH,json={'anon_id':self.h.owner,**self.fields})
        self.assertEqual(r.status_code,401)

    def test_source_route_unavailable_without_explicit_startup_injection(self):
        h=native_fixture.ServiceHTTPTests(); h.setUp(); self.addCleanup(h.doCleanups)
        r=h.post_signed(PATH,b'{"anon_id":"'+h.owner.encode()+b'"}')
        self.assertEqual(r.status_code,404,r.json)


if __name__=='__main__': unittest.main()
