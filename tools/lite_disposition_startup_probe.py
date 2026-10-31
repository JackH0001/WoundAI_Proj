"""Real factory + signed HTTP with generated transport fixtures; no cloud calls."""
import argparse, hashlib, json, unittest
from pathlib import Path
from unittest.mock import patch
from lite_disposition_executor import PATH
from test_lite_disposition_startup import Fixture
import test_lite_native_purchase as native


def run():
    native.legacy.LinksTests.setUpClass()
    host = unittest.TestCase()
    try:
        h = Fixture(host)
        preserved = h.f.f.preserved()
        capture = hashlib.sha256(h.f.f.capture).hexdigest()
        before = h.snap()
        executor = h.build()
        assert before == h.snap()
        client = executor.create_app().test_client()
        refused = client.post(PATH, json={'command': {}, 'drain': None})
        assert refused.status_code == 409
        rejected = {}
        for path in ('/api/v1/lite/account/challenge', '/api/v1/lite/service/run',
                     '/api/v1/lite/account/deletion/request', '/admin'):
            rejected[path] = client.post(path, json={}).status_code
        assert all(status == 404 for status in rejected.values())
        command = h.command()
        first = client.post(PATH, json={'command': command, 'drain': None})
        assert first.status_code == 200
        before_replay = h.snap()
        executor = h.build()
        budget = executor.inspect(h.person)['used_dispatch_budget']
        with patch.object(executor.w, 'run', side_effect=AssertionError('duplicate work')):
            replay = executor.create_app().test_client().post(PATH, json={'command': command, 'drain': None})
        assert replay.status_code == 200 and replay.json == first.json
        assert h.snap() == before_replay
        assert executor.inspect(h.person)['used_dispatch_budget'] == budget
        h.finish()
        view = h.e.inspect(h.person)
        assert all(value == 'observed' for value in view['worker']['states'].values())
        assert preserved == h.f.f.preserved()
        assert capture == hashlib.sha256(h.f.f.capture).hexdigest()
        assert h.f.f.calls == 1
        return dict(schema='lite-disposition-startup-probe/1', synthetic_only=True,
            real_factory=True, provider_transports='generated fixtures',
            startup_provider_mutations=0, unsigned_http=refused.status_code,
            unavailable_public_routes=rejected, replay_same_receipt=True,
            replay_extra_dispatches=0, model_calls=h.f.f.calls,
            quota_identity_audit_unchanged=True, original_capture_sha256=capture,
            commands=len(view['commands']), used_dispatch_budget=view['used_dispatch_budget'],
            worker=view['worker'], factory_reconnections=len(h.providers.calls)//2,
            service_old_active_remaining=len(h.f.service_bucket.versions)-len(h.f.service_bucket.live),
            research_old_active_remaining=len(h.f.h.bucket.history)-len(h.f.h.bucket.live),
            account_deleted=False, live_gcs_apple_kms_calls=0, real_data_or_keys_deleted=False)
    finally:
        host.doCleanups()


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--output', required=True, type=Path)
    args = p.parse_args()
    args.output.write_text(json.dumps(run(), indent=2)+'\n')
