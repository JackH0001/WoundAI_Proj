"""Reproducible synthetic custody/identity probe. Never contacts Apple or GCS."""
import argparse
import json
import unittest
from pathlib import Path
from lite_account_identity import IdentityRejected
from lite_apple_oauth import GrantUncertain, TOKEN_URL, REVOKE_URL
from test_lite_apple_grants import GrantFixture
from test_lite_subscription_grace import rows
import test_lite_native_purchase as native


def run():
    native.legacy.LinksTests.setUpClass()
    host=unittest.TestCase()
    try:
        g=GrantFixture(host);g.prepare();b=g.bridge()
        g.signed=g.f.token(g.c,exp=g.f.now()+3600);g.token=g.signed
        before=rows(g.f.h.f.ledger.store)
        first=g.confirm();retry=g.confirm()
        captured=g.capture(g.clone())
        vault=repr(rows(g.store)+rows(g.audit))
        no_plaintext=all(secret not in vault for secret in (g.signed,g.code,'private-refresh-token','private-access-token','apple-human-1'))
        g.f.h.a.now+=900
        try:b.status(g.c['installation']);expired=False
        except IdentityRejected:expired=True
        g.revoke_error=True
        try:g.revoke();pending=False
        except GrantUncertain:pending=True
        try:g.a.require(g.c['installation'],g.c['operation'],captured['identity_sha256']);blocked=False
        except IdentityRejected:blocked=True
        ciphertext_retained=g.store.load(g.grant_key())[1]['envelope'] is not None
        g.revoke_error=False;revoked=g.revoke();repeated=g.revoke()
        result=dict(schema='lite-apple-grant-probe/1',synthetic_only=True,
            exchange_calls=g.calls.count(TOKEN_URL),revocation_attempts=g.calls.count(REVOKE_URL),
            confirm_retry_identical=first==retry,encrypted_vault_no_plaintext=no_plaintext,
            short_session_still_expires=expired,revocation_failure_pending=pending,
            pending_revocation_blocks_grant=blocked,pending_ciphertext_retained=ciphertext_retained,
            revoked_current_ciphertext_removed=g.store.load(g.grant_key())[1]['envelope'] is None,
            revocation_retry_identical=revoked==repeated,quota_unchanged=before==rows(g.f.h.f.ledger.store),
            actual_apple=False,actual_gcs=False,native_enabled=False,account_deletion_complete=False)
        assert result['exchange_calls']==1 and result['revocation_attempts']==2
        assert all(result[k] for k in ('confirm_retry_identical','encrypted_vault_no_plaintext',
            'short_session_still_expires','revocation_failure_pending','pending_revocation_blocks_grant',
            'pending_ciphertext_retained','revoked_current_ciphertext_removed','revocation_retry_identical','quota_unchanged'))
        return result
    finally:host.doCleanups()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();result=run();args.output.write_text(json.dumps(result,indent=2)+'\n')
