"""Offline replay from empty commercial state; synthetic Apple/KMS/GCS only."""
import argparse
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from PIL import Image
from lite_attest_state import StateUnavailable
from lite_usage_ledger import digest
from test_lite_fresh_bootstrap import BootstrapTests, rows, b64


def run():
    BootstrapTests.setUpClass()
    host = BootstrapTests()
    try:
        host.setUp(); f = host.f; original = {k:rows(v) for k,v in f.original_stores.items()}
        old = f.untouched(); activate = f.runtime.activate_fresh
        def lost(*args):
            activate(*args)
            raise StateUnavailable('synthetic acknowledgement loss')
        with patch.object(f.runtime, 'activate_fresh', side_effect=lost):
            try: f.step('subscription')
            except StateUnavailable: pass
            else: raise AssertionError('fault was not exercised')
        assert f.x.inspect()['status'] == 'running'
        committed = rows(f.stores['subscription']), rows(f.stores['subscription_audit'])
        f.tick()
        with patch.object(f.runtime, 'activate_fresh', side_effect=AssertionError('duplicate activation')):
            recovered = f.step('subscription', recover=True)[2]
        assert committed == (rows(f.stores['subscription']), rows(f.stores['subscription_audit']))
        quota = f.step('quota')[2]; release = f.step('release')[2]
        assert old == f.untouched()
        assert original == {k:rows(v) for k,v in f.original_stores.items()}
        host.h = h = f.h; host.backend = h.compose(); host.client = host.backend.create_app().test_client()
        host.e = h.e; host.key = b64(h.e.key_id); host.counter = 0; host.login_serial = 300
        host.register(); host.login()
        photo = io.BytesIO(); Image.new('RGB', (64,64), (210,120,130)).save(photo, format='JPEG')
        host.f = SimpleNamespace(jpeg=photo.getvalue()); host.image_hash = hashlib.sha256(host.f.jpeg).hexdigest()
        first = host.complete(1); again = host.run_request(1)
        assert first.json == again.json
        assert (h.model_calls, host.quota()['used']) == (1,1)
        return dict(schema='lite-fresh-bootstrap-probe/1', synthetic_only=True,
            network_connections=0, real_provider_calls=0, initialized_by_existing_core=True,
            initial_commercial_rows=0, synthetic_rows_copied_to_target=0,
            stages=[dict(stage=v['stage'],status=v['status'],attempt=v['attempt']) for v in (recovered,quota,release)],
            subscription_reply_lost=True, repeated_subscription_activation=0,
            prior_records_and_audits_preserved=True, bootstrap_data_rows_created=0,
            plan_sha256=digest(f.plan), model_calls=h.model_calls, quota_used=host.quota()['used'],
            repeated_result_equal=True, deployment_performed=False)
    finally: host.doCleanups()


if __name__ == '__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--output', type=Path, required=True)
    args=parser.parse_args()
    with args.output.open('x') as stream: json.dump(run(),stream,indent=2); stream.write('\n')
