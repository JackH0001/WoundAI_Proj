"""Synthetic identity-to-purchase rehearsal; no Apple account or cloud access."""
import argparse,json,unittest
from pathlib import Path
from unittest.mock import patch
from test_lite_account_identity import IdentityFixture,IdentityTests
from test_lite_subscription_grace import rows
from lite_account_identity import IdentityRejected
from lite_usage_ledger import UsageLimit

def run():
    IdentityTests.setUpClass();host=unittest.TestCase();f=IdentityFixture(host)
    try:
        ca=f.challenge('identity-a');a=f.confirm(ca)
        cb=f.challenge('identity-b');b=f.confirm(cb)
        f.use(801,'identity-a');f.use(802,'identity-b');free_limited=False
        try:f.use(803,'identity-b')
        except UsageLimit:free_limited=True
        before=rows(f.h.f.ledger.store)
        f.h.a.payload['appAccountToken']=a['app_account_token']
        f.h.confirm(device='identity-a')
        upgrade_keeps=before==rows(f.h.f.ledger.store)
        with patch.object(f.h.a.client,'get_all_subscription_statuses',wraps=f.h.a.fetch) as api:
            f.h.confirm(device='identity-b');restore_queries=api.call_count
        for n in range(804,812):f.use(n,'identity-a' if n%2 else 'identity-b')
        limited=False
        try:f.use(812,'identity-a')
        except UsageLimit:limited=True
        account=f.h.accounts.resolve('identity-a')
        used=f.h.f.ledger.snapshot(account['subject'],f.h.f.day,f.h.accounts.grant(account,f.now()))['used']
        f.b.logout('identity-a',ca['operation']);late=False
        try:f.confirm(ca)
        except IdentityRejected:late=True
        other_device=f.b.status('identity-b')['state']=='signed_in'
        audit_before=len(rows(f.b.audit));f.confirm(f.challenge('identity-a'))
        after=f.h.f.ledger.snapshot(account['subject'],f.h.f.day,f.h.accounts.grant(account,f.now()))['used']
        return dict(schema='woundailite-account-identity-probe/1',synthetic_only=True,
            cross_device_same_account=a['account_sha256']==b['account_sha256'],
            cross_device_same_app_account_token=a['app_account_token']==b['app_account_token'],
            free_third_denied=free_limited,upgrade_keeps_used=upgrade_keeps,restore_apple_queries=restore_queries,
            paid_total_used=used,eleventh_denied=limited,late_login_after_logout_denied=late,
            other_device_session_preserved=other_device,relogin_keeps_used=after==used,
            audit_preserved=len(rows(f.b.audit))>audit_before,
            real_apple_login=False,real_apple_sandbox=False,cloud_calls=0,default_startup_enabled=False)
    finally:host.doCleanups()

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);a=p.parse_args();r=run()
    Path(a.output).write_text(json.dumps(r,indent=2)+'\n')
    assert all(r[k] for k in ('cross_device_same_account','cross_device_same_app_account_token','free_third_denied',
        'upgrade_keeps_used','eleventh_denied','late_login_after_logout_denied','other_device_session_preserved','relogin_keeps_used','audit_preserved'))
    assert r['paid_total_used']==10 and r['restore_apple_queries']==0
