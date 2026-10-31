"""Offline synthetic signature/key outage/revocation rehearsal. No cloud calls."""
import argparse
import json
import unittest
import jwt
from lite_account_identity import AccountIdentityBridge,IdentityRejected
from lite_apple_identity_provider import RefreshingAppleIdentityVerifier
from lite_apple_account_events import AppleAccountEvents
from lite_attest_state import SQLiteStateStore,StateUnavailable
from test_lite_account_identity import IdentityFixture
from test_lite_subscription_grace import rows
import test_lite_native_purchase as native


def run():
    host=unittest.TestCase();native.legacy.LinksTests.setUpClass()
    try:
        f=IdentityFixture(host);old=f.b;calls=[0];offline=[False];mono=[1000]
        def fetch():
            calls[0]+=1
            if offline[0]:raise TimeoutError('synthetic outage')
            return json.dumps(f.jwks).encode()
        verifier=RefreshingAppleIdentityVerifier(audience=f.verifier.audience,fetch=fetch,
            clock=f.now,monotonic=lambda:mono[0],ttl=60)
        events=AppleAccountEvents(verifier=verifier,store=SQLiteStateStore(f.h.f.root/'events.sqlite'),
            audit_store=SQLiteStateStore(f.h.f.root/'events-audit.sqlite'),namespace=old.namespace,identity_key=old.secret)
        f.b=AccountIdentityBridge(runtime=old.runtime,verifier=verifier,store=old.store,audit_store=old.audit,
            namespace=old.namespace,identity_key=old.secret,events=events)
        first=f.confirm(f.challenge('one'));second=f.confirm(f.challenge('two'))
        f.use(1,'one');f.use(2,'two');before=rows(f.h.f.ledger.store),rows(f.b.registry.store)
        event=jwt.encode(dict(iss=verifier.issuer,aud=verifier.audience,iat=f.now(),jti='synthetic-revoke',
            events=dict(type='consent-revoked',sub='apple-human-1',event_time=f.now())),f.private,
            algorithm='RS256',headers={'kid':'synthetic-rsa'})
        receipt=events.accept(event);audit=rows(events.audit)
        assert events.accept(event)==receipt and rows(events.audit)==audit
        blocked=0
        for device in ('one','two'):
            try:f.b.require_session(device)
            except IdentityRejected:blocked+=1
        assert blocked==2 and (rows(f.h.f.ledger.store),rows(f.b.registry.store))==before
        f.h.a.now+=2;mono[0]+=2;again=f.confirm(f.challenge('one'))
        assert again['app_account_token']==first['app_account_token']==second['app_account_token']
        assert rows(f.h.f.ledger.store)==before[0]
        cached_calls=calls[0];assert cached_calls==1
        f.h.a.now+=60;mono[0]+=60;offline[0]=True
        challenge=f.challenge('three');token=f.token(challenge)
        try:f.confirm(challenge,token)
        except StateUnavailable:pass
        else:raise AssertionError('expired signing keys admitted sign-in')
        try:f.confirm(challenge,token)
        except StateUnavailable:pass
        else:raise AssertionError('outage retry admitted sign-in')
        assert calls[0]==2
        f.h.a.now+=30;mono[0]+=30;offline[0]=False
        f.h.publish()  # refresh the separate synthetic rollout observer too
        resumed=f.confirm(challenge,token)
        assert resumed['app_account_token']==first['app_account_token'] and calls[0]==3
        assert rows(f.h.f.ledger.store)==before[0]
        return dict(schema='woundailite-apple-provider-probe/1',synthetic_only=True,
            two_devices_blocked=blocked,revocation_receipt_replay_identical=True,
            original_ledger_and_registry_preserved=True,explicit_relogin_same_account=True,
            cached_fetches=cached_calls,outage_retry_fetches=1,total_fetches_after_recovery=calls[0],
            stale_keys_refused=True,same_pending_signin_resumed=True,used_quota_preserved=True,
            cloud_calls=0,real_apple_login=False,real_sandbox=False)
    finally:host.doCleanups()

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);a=p.parse_args()
    from pathlib import Path
    result=run();Path(a.output).write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
