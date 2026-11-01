"""Synthetic refresh/authorization read benchmark; no Apple, GCS or user data.
Run with tools, Backend/Flask and engineering/phase2 on PYTHONPATH.
"""
import json
import math
import statistics
import time
import unittest
from unittest.mock import patch
from lite_attest_state import SQLiteStateStore
from lite_apple_grants import GrantCipher
from lite_service_release import ServiceReleaseReader
from test_lite_account_sessions import RenewalFixture
from test_lite_service_release import Fixture
from test_lite_subscription_grace import rows
import test_lite_native_purchase as native


def run(samples=100):
    host=unittest.TestCase();native.NativePurchaseTests.setUpClass()
    try:
        f=Fixture(host);f.active();before=rows(f.store),rows(f.n.accounts.store),rows(f.ledger.store)
        baseline=f.reader
        candidate=ServiceReleaseReader(store=f.store,audit_store=f.audit,manifest=f.manifest,immutable_proof_cache_size=8)
        candidate.require_active()  # cold call separately verified by unit tests
        real=SQLiteStateStore.load;read_count=0
        def count(store,key):
            nonlocal read_count
            read_count+=1;return real(store,key)
        data={k:dict(reads=[],milliseconds=[]) for k in ('default','warm_opt_in')}
        for i in range(samples):
            modes=[('default',baseline),('warm_opt_in',candidate)]
            if i%2:modes.reverse()
            for name,reader in modes:
                f.n.r.service_release=reader;read_count=0
                started=time.perf_counter()
                with patch.object(SQLiteStateStore,'load',count):assert f.grant().limit==2
                data[name]['milliseconds'].append((time.perf_counter()-started)*1000)
                data[name]['reads'].append(read_count)
        assert before==(rows(f.store),rows(f.n.accounts.store),rows(f.ledger.store))
        for d in data.values():
            d['read_counts']=sorted(set(d.pop('reads')))
            d['p50_ms']=statistics.median(d['milliseconds'])
            d['p95_ms']=sorted(d['milliseconds'])[math.ceil(samples*.95)-1]
        assert data['default']['read_counts']==[19] and data['warm_opt_in']['read_counts']==[17]
        f.controller.apply(f.approval('pause'))
        assert not f.n.r.allowed(f.n.accounts.registration('first-device'))
        r=RenewalFixture(host);r.advance(86401);original=r.g.provider.post;attempts=[]
        untouched=rows(r.f.h.accounts.store),rows(r.f.h.f.ledger.store)
        def flaky(url,fields):
            attempts.append(fields['grant_type'])
            if len(attempts)==1:raise TimeoutError('synthetic confidential message')
            return original(url,fields)
        with patch.object(r.g.provider,'post',side_effect=flaky),patch('lite_apple_grants.time.sleep'):
            receipt=r.renew();assert receipt==r.renew()
            r.advance(901);r.renew(op='b'*32,generation=1)
        assert attempts==['refresh_token','refresh_token']
        assert untouched==(rows(r.f.h.accounts.store),rows(r.f.h.f.ledger.store))
        # A current-row deletion cannot cryptographically erase a retained copy.
        cipher=GrantCipher(keys={'synthetic':b'x'*32},current='synthetic')
        context={'account':'synthetic-A'};backup=cipher.seal({'refresh_token':'fake'},context)
        current=None
        old_backup_decrypts=cipher.open(backup,context)['refresh_token']=='fake'
        assert current is None and old_backup_decrypts
        return dict(schema='lite-refresh-review/1',synthetic_only=True,samples=samples,
            authorization=data,p95_scope='local SQLite + Python instrumentation, not cloud latency',
            refresh=dict(attempts=2,confirmed_daily_validations=1,renewal_generations=2,
                         account_unchanged=True,quota_unchanged=True,explicit_signin_needed=False),
            pause_immediately_denied=True,current_row_deletion_erases_backup=False,
            cache_default_enabled=False,cloud_calls=0,actual_apple=False)
    finally:host.doCleanups()

if __name__=='__main__':print(json.dumps(run(),indent=2))
