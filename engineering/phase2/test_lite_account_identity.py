"""Real signature checks with generated keys, synthetic identities and local CAS."""
import base64,json,tempfile,unittest,uuid
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa,ec
from lite_account_identity import AppleIdentityVerifier,AccountIdentityBridge,IdentityRejected,PREFIX
from lite_attest_state import SQLiteStateStore,StateUnavailable
from lite_usage_ledger import UsageConflict,UsageLimit,digest
from lite_subscription_executor import key
from lite_service_operations import ServiceAccounts
from test_lite_subscription_grace import rows
import test_lite_native_purchase as native


class IdentityFixture:
    def __init__(self,host,runtime_fixture=None):
        self.h=runtime_fixture or native.Fixture(host);self.now=lambda:self.h.a.now
        self.private=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        k=json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.private.public_key()))
        k.update(kid='synthetic-rsa',alg='RS256',use='sig')
        self.jwks={'keys':[k]};self.n=0;self.days=set()
        self.verifier=AppleIdentityVerifier(audience='com.woundai.lite',jwks=self.jwks,
            fetched_at=self.now()-1,expires_at=self.now()+3599,clock=self.now)
        def store(n):return SQLiteStateStore(self.h.f.root/(n+'.sqlite'))
        self.b=AccountIdentityBridge(runtime=self.h.r,verifier=self.verifier,store=store('identity'),
            audit_store=store('identity-audit'),namespace='sandbox-candidate',identity_key=b'x'*32)
    def use(self,n,device):
        a=self.h.accounts.resolve(device);f=self.h.f
        if a['subject'] not in self.days:
            f.ledger.initialize_day(a['subject'],f.day,baseline_successes=0,baseline_attempts=0,manifest_sha256='f'*64)
            self.days.add(a['subject'])
        t=f.ledger.reserve(a['subject'],f.day,f'{n:032x}','c'*64,self.h.accounts.grant(a,self.now()))
        assert f.ledger.claim(t,f'{n:032x}')
        return f.ledger.settle(t,f'{n:032x}','succeeded','d'*64)
    def challenge(self,device='new-device'):
        self.n+=1;return self.b.challenge(device,f'{self.n:032x}')
    def token(self,c,sub='apple-human-1',**changes):
        p=dict(iss=self.verifier.issuer,aud=self.verifier.audience,sub=sub,iat=self.now(),exp=self.now()+600,nonce=c['nonce'])
        p.update(changes)
        return jwt.encode(p,self.private,algorithm='RS256',headers={'kid':'synthetic-rsa'})
    def confirm(self,c,token=None,sub='apple-human-1'):
        return self.b.confirm(c['installation'],c['operation'],token or self.token(c,sub),c['state'])


class IdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):native.legacy.LinksTests.setUpClass()
    def setUp(self):self.f=IdentityFixture(self);self.b=self.f.b
    def test_verified_subject_creates_free_stable_account_not_client_identity(self):
        c=self.f.challenge();r=self.f.confirm(c)
        a=self.f.h.accounts.resolve(c['installation'])
        self.assertEqual(self.f.h.accounts.grant(a,self.f.now()).limit,2)
        self.assertEqual(r['app_account_token'],a['token']);self.assertNotIn('sub',r)
    def test_two_devices_share_identity_token_and_daily_quota(self):
        f=self.f;a=f.confirm(f.challenge('new-a'));b=f.confirm(f.challenge('new-b'))
        self.assertEqual(a['account_sha256'],b['account_sha256']);self.assertEqual(a['app_account_token'],b['app_account_token'])
        f.use(701,'new-a');f.use(702,'new-b')
        with self.assertRaises(UsageLimit):f.use(703,'new-b')
    def test_new_login_same_person_keeps_registration_and_usage(self):
        f=self.f;c=f.challenge();f.confirm(c);f.use(704,'new-device')
        before=rows(f.h.accounts.store),rows(f.h.f.ledger.store)
        f.confirm(f.challenge());self.assertEqual(before,(rows(f.h.accounts.store),rows(f.h.f.ledger.store)))
    def test_different_person_cannot_replace_installation(self):
        f=self.f;f.confirm(f.challenge());before=rows(f.h.accounts.store)
        with self.assertRaises(IdentityRejected):f.confirm(f.challenge(),sub='another-human')
        self.assertEqual(before,rows(f.h.accounts.store))
        with self.assertRaises(IdentityRejected):self.b.status('new-device')
    def test_email_is_not_identity_and_not_stored(self):
        f=self.f;c=f.challenge();f.confirm(c,f.token(c,email='one@example.invalid'))
        r=f.confirm(f.challenge('new-other'),sub='apple-human-2')
        self.assertNotEqual(r['account_sha256'],self.b.status('new-device')['account_sha256'])
        self.assertNotIn('example.invalid',repr(rows(self.b.store)+rows(self.b.audit)))
        self.assertNotIn('apple-human-1',repr(rows(self.b.store)+rows(self.b.audit)))
    def test_wrong_nonce_issuer_audience_times_or_subject_rejected_without_binding(self):
        c=self.f.challenge()
        for change in [dict(nonce='wrong'),dict(iss='https://evil.invalid'),dict(aud='other'),dict(aud=['com.woundai.lite']),
                       dict(exp=self.f.now()),dict(iat=True),dict(iat=self.f.now()+31),dict(sub=''),dict(nbf=True)]:
            with self.subTest(change=change),self.assertRaises(IdentityRejected):self.f.confirm(c,self.f.token(c,**change))
        self.assertIsNone(self.f.h.accounts.store.load(self.f.h.accounts.key('new-device')))
    def test_untrusted_signature_key_or_algorithm_rejected(self):
        c=self.f.challenge();parts=self.f.token(c).split('.')
        wrong=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        p=jwt.decode(self.f.token(c),options={'verify_signature':False})
        for token in [jwt.encode(p,wrong,algorithm='RS256',headers={'kid':'synthetic-rsa'}),
                      jwt.encode(p,b'never-a-public-key',algorithm='HS256',headers={'kid':'synthetic-rsa'}),
                      parts[0]+'.'+parts[1]+'.bad']:
            with self.assertRaises(IdentityRejected):self.f.confirm(c,token)
    def test_duplicate_claims_and_remote_key_header_rejected(self):
        c=self.f.challenge();p=jwt.decode(self.f.token(c),options={'verify_signature':False})
        t=jwt.encode(p,self.f.private,algorithm='RS256',headers={'kid':'synthetic-rsa','jku':'https://evil.invalid'})
        with self.assertRaises(IdentityRejected):self.f.confirm(c,t)
        parts=self.f.token(c).split('.');parts[1]=base64.urlsafe_b64encode(b'{"sub":"a","sub":"b"}').decode().rstrip('=')
        with self.assertRaises(IdentityRejected):self.f.confirm(c,'.'.join(parts))
    def test_es256_key_verification(self):
        f=self.f;c=f.challenge();private=ec.generate_private_key(ec.SECP256R1())
        k=json.loads(jwt.algorithms.ECAlgorithm.to_jwk(private.public_key()));k.update(alg='ES256',kid='ec',use='sig')
        v=AppleIdentityVerifier(audience='com.woundai.lite',jwks={'keys':[k]},fetched_at=f.now(),expires_at=f.now()+100,clock=f.now)
        p=jwt.decode(f.token(c),options={'verify_signature':False})
        self.assertEqual(v.verify(jwt.encode(p,private,algorithm='ES256',headers={'kid':'ec'}),c['nonce'])['sub'],'apple-human-1')
    def test_stale_key_snapshot_fails_closed_without_binding(self):
        c=self.f.challenge();t=self.f.token(c);self.f.verifier.expires_at=self.f.now()
        with self.assertRaises(StateUnavailable):self.f.confirm(c,t)
    def test_challenge_expiry_requires_explicit_new_operation(self):
        c=self.f.challenge();self.f.h.a.now+=301
        with self.assertRaises(IdentityRejected):self.f.confirm(c)
        with self.assertRaises(IdentityRejected):self.b.challenge('new-device',c['operation'])
    def test_new_challenge_invalidates_old_token_even_same_person(self):
        c=self.f.challenge();t=self.f.token(c);self.f.challenge()
        with self.assertRaises(IdentityRejected):self.f.confirm(c,t)
    def test_state_and_device_binding(self):
        c=self.f.challenge();t=self.f.token(c)
        with self.assertRaises(IdentityRejected):self.b.confirm('new-device',c['operation'],t,'bad')
        with self.assertRaises(IdentityRejected):self.b.confirm('different',c['operation'],t,c['state'])
    def test_repeat_confirmation_lost_reply_keeps_receipt_and_usage(self):
        c=self.f.challenge();t=self.f.token(c);r=self.f.confirm(c,t)
        before=rows(self.b.store),rows(self.b.audit),rows(self.f.h.f.ledger.store)
        with patch.object(self.f.verifier,'verify',side_effect=AssertionError('do not reauthenticate replay')):
            self.assertEqual(self.f.confirm(c,t),r)
        self.assertEqual(before,(rows(self.b.store),rows(self.b.audit),rows(self.f.h.f.ledger.store)))
    def test_audit_write_failure_blocks_and_same_token_resumes(self):
        c=self.f.challenge();t=self.f.token(c);real=self.b.audit.compare_exchange
        with patch.object(self.b.audit,'compare_exchange',side_effect=StateUnavailable('offline')),self.assertRaises(StateUnavailable):self.f.confirm(c,t)
        with self.assertRaises(IdentityRejected):self.b.status('new-device')
        self.assertEqual(self.f.confirm(c,t)['state'],'signed_in')
    def test_bootstrap_failure_resumes_without_replacing_registration(self):
        c=self.f.challenge();t=self.f.token(c)
        with patch.object(self.f.h.accounts,'bootstrap',side_effect=StateUnavailable('offline')),self.assertRaises(StateUnavailable):self.f.confirm(c,t)
        old=rows(self.f.h.accounts.store);self.f.confirm(c,t);self.assertEqual(old,rows(self.f.h.accounts.store))
    def test_logout_blocks_delayed_confirmation_and_keeps_data(self):
        c=self.f.challenge();t=self.f.token(c);self.f.confirm(c,t)
        before=rows(self.f.h.accounts.store),rows(self.f.h.f.ledger.store)
        r=self.b.logout('new-device',c['operation']);self.assertEqual(self.b.logout('new-device',c['operation']),r)
        with self.assertRaises(IdentityRejected):self.f.confirm(c,t)
        with self.assertRaises(IdentityRejected):self.b.status('new-device')
        self.assertEqual(before,(rows(self.f.h.accounts.store),rows(self.f.h.f.ledger.store)))
    def test_logout_during_verification_never_activates(self):
        c=self.f.challenge();t=self.f.token(c);real=self.f.verifier.verify
        def verify(*a):
            result=real(*a);self.b.logout('new-device',c['operation']);return result
        with patch.object(self.f.verifier,'verify',side_effect=verify),self.assertRaises(IdentityRejected):self.f.confirm(c,t)
        with self.assertRaises(IdentityRejected):self.b.status('new-device')
    def test_logout_during_provision_never_reopens(self):
        c=self.f.challenge();t=self.f.token(c);real=self.f.h.accounts.provision
        def provision(*a):real(*a);self.b.logout('new-device',c['operation'])
        with patch.object(self.f.h.accounts,'provision',side_effect=provision),self.assertRaises(IdentityRejected):self.f.confirm(c,t)
        with self.assertRaises(IdentityRejected):self.b.status('new-device')
    def test_late_old_logout_does_not_end_new_session(self):
        f=self.f;old=f.challenge();f.confirm(old);self.b.logout('new-device',old['operation'])
        new=f.challenge();f.confirm(new);self.b.logout('new-device',old['operation'])
        self.assertEqual(self.b.status('new-device')['operation'],new['operation'])
    def test_logout_before_challenge_cannot_be_reused(self):
        self.b.logout('new-device','f'*32)
        with self.assertRaises(IdentityRejected):self.b.challenge('new-device','f'*32)
    def test_expired_session_does_not_extend_when_replaying(self):
        c=self.f.challenge();t=self.f.token(c);self.f.confirm(c,t);self.f.h.a.now+=601
        with self.assertRaises(IdentityRejected):self.f.confirm(c,t)
        with self.assertRaises(IdentityRejected):self.b.status('new-device')
    def test_config_key_change_is_not_silent_new_account(self):
        with self.assertRaises(StateUnavailable):AccountIdentityBridge(runtime=self.f.h.r,verifier=self.f.verifier,
            store=self.b.store,audit_store=self.b.audit,namespace=self.b.namespace,identity_key=b'y'*32)
    def test_corrupt_control_never_resets_on_new_challenge(self):
        self.f.challenge();row=self.b._read('new-device');self.b.store.compare_exchange(self.b._key('new-device'),row[0],dict(row[1],v=True))
        before=rows(self.b.store)
        with self.assertRaises(StateUnavailable):self.f.challenge()
        self.assertEqual(before,rows(self.b.store))
    def test_two_devices_concurrent_same_subject_keep_one_token(self):
        cs=[self.f.challenge('thread-'+str(n)) for n in range(2)]
        with ThreadPoolExecutor(max_workers=2) as pool:rs=list(pool.map(self.f.confirm,cs))
        self.assertEqual(rs[0]['app_account_token'],rs[1]['app_account_token'])
    def test_challenge_audit_lost_reply_can_resume(self):
        real=self.b.audit.compare_exchange
        def lost(*a):real(*a);raise StateUnavailable('reply lost')
        with patch.object(self.b.audit,'compare_exchange',side_effect=lost),self.assertRaises(StateUnavailable):self.f.challenge()
        c=self.b.challenge('new-device',f'{self.f.n:032x}');self.f.confirm(c)


class IdentityHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):native.legacy.LinksTests.setUpClass()
    def setUp(self):
        import test_lite_service_http as http
        from lite_attest_http import install_lite_attest
        self.h=http.ServiceHTTPTests();temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        def install(app,**kw):
            kw['commerce'].accounts=ServiceAccounts(SQLiteStateStore(Path(temp.name)/'accounts.sqlite'))
            p=native.Fixture(self,kw['commerce'].accounts);self.f=IdentityFixture(self,p)
            return install_lite_attest(app,**kw,purchases=p.bridge,identity=self.f.b)
        with patch.object(http,'install_lite_attest',side_effect=install):self.h.setUp()
        self.addCleanup(self.h.doCleanups)
    def post(self,path,body):return self.h.post_signed(path,json.dumps({'anon_id':self.h.owner,**body}).encode())
    def login(self):
        r=self.post(PREFIX+'challenge',dict(operation='1'*32));self.assertEqual(r.status_code,200,r.json);c=r.json
        r=self.post(PREFIX+'confirm',dict(operation=c['operation'],state=c['state'],identity_token=self.f.token(c)))
        self.assertEqual(r.status_code,200,r.json);return r.json
    def test_attested_apple_login_and_purchase_context_keep_research_unchanged(self):
        before=rows(self.h.privacy.store),rows(self.h.ledger.store);r=self.login()
        x=self.post('/api/v1/lite/purchase/context',dict(operation='2'*32));self.assertEqual(x.status_code,200,x.json)
        self.assertEqual(x.json['app_account_token'],r['app_account_token'])
        self.assertEqual(before,(rows(self.h.privacy.store),rows(self.h.ledger.store)));self.assertEqual(self.h.model_calls,0)
    def test_attestation_alone_does_not_unlock_purchase_or_service(self):
        self.assertEqual(self.post('/api/v1/lite/purchase/context',dict(operation='2'*32)).status_code,401)
        self.assertEqual(self.post('/api/v1/lite/service/prepare',{}).status_code,401)
    def test_unsigned_owner_spoof_and_extra_client_identity_fields_rejected(self):
        self.assertEqual(self.h.client.post(PREFIX+'challenge',json={}).status_code,401)
        self.assertEqual(self.post(PREFIX+'challenge',dict(anon_id='another',operation='1'*32)).status_code,403)
        self.assertEqual(self.post(PREFIX+'challenge',dict(operation='1'*32,subject='chosen')).status_code,401)
    def test_logout_blocks_new_work_and_preserves_withdrawal_route(self):
        r=self.login();self.assertEqual(self.post(PREFIX+'logout',dict(operation=r['operation'])).status_code,200)
        self.assertEqual(self.post('/api/v1/lite/purchase/context',dict(operation='2'*32)).status_code,401)
        path='/api/v1/lite/data/'+self.h.owner
        x=self.h.client.delete(path,headers=self.h.headers(path,b'','',method='DELETE',purpose='withdraw'))
        self.assertEqual(x.status_code,200,x.json)
    def test_status_is_no_store_and_has_no_paid_or_quota_flag(self):
        self.login();r=self.post(PREFIX+'status',{})
        self.assertEqual(r.status_code,200);self.assertEqual(r.headers['Cache-Control'],'no-store')
        self.assertNotIn('limit',r.json);self.assertNotIn('paid',r.json)

if __name__=='__main__':unittest.main()
