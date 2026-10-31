"""Durable journal + real Google SDK, with synthetic HTTP and no credentials."""
import argparse
import base64
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
import google_crc32c
from google.auth.credentials import AnonymousCredentials
from google.auth.transport.requests import AuthorizedSession
from google.cloud.storage import Client
from lite_attest_state import GCSStateStore
from lite_provider_budget import BudgetUnavailable
from lite_provider_transport import BudgetedSession
from test_lite_provider_budget import Fixture
from test_lite_provider_transport import Adapter, reply


def run():
    host = unittest.TestCase()
    try:
        host.enterContext(patch.dict(os.environ,DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA='true'))
        f = Fixture(host)
        f.p.update(max_allocations=2,requests=2,send_bytes=4096,receive_bytes=131072)
        def transport(allocation, responses):
            session = AuthorizedSession(AnonymousCredentials(),max_refresh_attempts=0)
            session.trust_env = False
            adapter = Adapter(responses);session.mount('https://',adapter);host.addCleanup(session.close)
            return BudgetedSession(session,allocation,provider='gcs',authorized=True),adapter
        first = f.build().allocate()
        t,a = transport(first,[TimeoutError('synthetic lost response'),reply({})])
        try:t.request('GET','https://storage.googleapis.com/synthetic')
        except BudgetUnavailable:pass
        else:raise AssertionError('lost response incorrectly accepted')
        t.request('GET','https://storage.googleapis.com/synthetic')
        try:t.request('GET','https://storage.googleapis.com/synthetic')
        except BudgetUnavailable:pass
        else:raise AssertionError('extra request allowed')
        assert len(a.calls)==2 and first.used['requests']==2
        second = f.build().allocate()
        data=b'{"v":1}';crc=base64.b64encode(google_crc32c.value(data).to_bytes(4,'big')).decode()
        t,b = transport(second,[reply(dict(generation='7',size=str(len(data)),crc32c=crc)),
                                reply(data,headers={'x-goog-hash':'crc32c='+crc})])
        client=Client(project='synthetic-project',credentials=AnonymousCredentials(),_http=t)
        host.addCleanup(client.close)
        store=GCSStateStore(client.bucket('synthetic-bucket'),prefix='lite-native/probe/state')
        assert store.load(b'x'*32)==(7,{'v':1}) and len(b.calls)==2
        assert 'ifGenerationMatch=7' in b.calls[1][0].url
        try:f.build().allocate()
        except BudgetUnavailable:pass
        else:raise AssertionError('restart replenished durable budget')
        status=f.build().status()
        assert status['allocated_attempts']==2 and status['remaining_attempts']==0
        return dict(schema='lite-provider-budget-probe/1',synthetic_only=True,
            durable_allocations=status['allocated_attempts'],remaining_allocations=0,
            actual_sdk_http_attempts=len(a.calls)+len(b.calls),lost_response_charged=True,
            extra_attempts_after_exhaustion=0,restart_refill=False,
            generation_guard_preserved=True,metadata_background_disabled=True,
            first_attempt=first.snapshot(),second_attempt=second.snapshot(),
            budget_status=status,control_plane_included=False,hard_cancellation_proven=False,
            live_gcs_apple_kms_calls=0,real_data_or_keys_deleted=False)
    finally:host.doCleanups()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True,type=Path);args=p.parse_args()
    args.output.write_text(json.dumps(run(),indent=2)+'\n')
