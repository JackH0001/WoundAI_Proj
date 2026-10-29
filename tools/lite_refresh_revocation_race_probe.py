"""Same synthetic probe can run against 8e36672 and the protected candidate."""
import argparse,json,unittest
from pathlib import Path
from unittest.mock import patch
from lite_account_identity import IdentityRejected
from lite_apple_oauth import GrantUncertain
from test_lite_account_sessions import RenewalFixture
import test_lite_native_purchase as native


def run():
    native.legacy.LinksTests.setUpClass();host=unittest.TestCase();f=RenewalFixture(host)
    try:
        f.advance(86401);refresh=f.g.provider.refresh;revoker=f.g.provider.revoke
        outcomes=[];revoked=[]
        def revoke(token):
            revoked.append('rotated' if token=='synthetic-late-rotated' else 'original')
            return revoker(token)
        def late(token):
            result=refresh(token)
            try:f.g.revoke();outcomes.append('reported_revoked')
            except GrantUncertain:outcomes.append('deferred')
            return dict(result,refresh_token='synthetic-late-rotated')
        with patch.object(f.g.provider,'revoke',side_effect=revoke):
            with patch.object(f.g.provider,'refresh',side_effect=late):
                try:f.renew();renewed=True
                except (IdentityRejected,GrantUncertain):renewed=False
            row=f.g.a._row(f.g.a._id(f.c['installation'],f.c['operation']))[1]
            preserved=(row['envelope'] is not None and
                f.g.cipher.open(row['envelope'],row['context'])['refresh_token']=='synthetic-late-rotated')
            f.g.revoke()
        return dict(schema='lite-refresh-revoke-race/1',synthetic_only=True,actual_apple=False,
            revoke_during_refresh=outcomes[0],late_token_preserved=preserved,
            revoke_token_generations=revoked,renewed=renewed,
            final_grant_phase=f.g.a._row(f.g.a._id(f.c['installation'],f.c['operation']))[1]['phase'])
    finally:host.doCleanups()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True)
    p.add_argument('--expect',choices=['protected','baseline'],default='protected');a=p.parse_args();r=run()
    if a.expect=='protected':
        assert r['revoke_during_refresh']=='deferred' and r['late_token_preserved']
        assert r['revoke_token_generations']==['rotated']
    else:
        assert r['revoke_during_refresh']=='reported_revoked' and not r['late_token_preserved']
        assert r['revoke_token_generations']==['original']
    assert not r['renewed'] and r['final_grant_phase']=='revoked'
    a.output.write_text(json.dumps(r,indent=2)+'\n')
