"""Pinned shared startup and private HTTP disposition with synthetic providers."""
import argparse
import hashlib
import json
import unittest
from pathlib import Path
from lite_disposition_executor import PATH
from lite_disposition_progress import SHARED_CATALOG
from test_lite_shared_runtime import SharedDispositionFixture
import test_lite_native_purchase as native


def run():
    native.legacy.LinksTests.setUpClass()
    host=unittest.TestCase()
    try:
        h=SharedDispositionFixture(host)
        preserved=h.f.f.preserved();capture=hashlib.sha256(h.f.f.capture).hexdigest()
        before=h.snap();e=h.build()
        assert before==h.snap()
        client=e.create_app().test_client()
        refused=client.post(PATH,json=dict(command={},drain=None))
        assert refused.status_code==409
        command=h.command();first=e.execute(command);before=h.snap()
        assert h.build().execute(command)==first and before==h.snap()
        h.finish_shared();view=h.e.inspect(h.person)
        assert all(s=='observed' for s in view['worker']['states'].values())
        assert 'shared_key_retirement' in view['worker']['pending_lanes']
        public=h.f.f.status()
        assert public['disposition']['catalog_sha256']==SHARED_CATALOG
        assert public['disposition']['grant_key_state']=='backup_pending'
        assert public['account_deleted'] is False
        observed=h.e.w.status(h.person);before=h.snap()
        for _ in range(3):assert h.e.w.status(h.person)==observed
        assert before==h.snap()
        assert preserved==h.f.f.preserved() and capture==hashlib.sha256(h.f.f.capture).hexdigest()
        assert h.f.f.calls==1
        assert 'destroy' not in [a for a,_ in h.f.f.f.kms.calls]
        return dict(schema='lite-shared-runtime-probe/1',synthetic_only=True,
            real_factory=True,provider_transports='synthetic Apple/KMS/GCS',unsigned_http=refused.status_code,
            startup_provider_mutations=0,replay_extra_dispatches=0,model_calls=h.f.f.calls,
            quota_identity_audit_unchanged=True,original_capture_sha256=capture,
            worker=view['worker'],public_progress=public['disposition'],
            readonly_status_extra_provider_calls=0,shared_key_destroy_calls=0,
            account_deleted=False,live_provider_calls=0,real_data_or_keys_deleted=False)
    finally:host.doCleanups()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output.write_text(json.dumps(run(),indent=2)+'\n')
