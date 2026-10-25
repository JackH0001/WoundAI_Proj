#!/usr/bin/env python3
"""Offline verification mounted outside the image; never part of runtime COPY.

Run with Python -I and an explicit application root. An external script's
directory is not /app, even when the container WORKDIR is /app.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import Mock, patch


def main(application_root):
    root=application_root.resolve(strict=True)
    if not (root/'Backend/LiteReview/runtime.py').is_file():
        raise ValueError('isolated application root missing')
    sys.path.insert(0,str(root))
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    import jwt
    from Backend.LiteReview import runtime
    from Backend.LiteReview.contracts import IAP_AUDIENCE, PORTAL_SA, READER_URLS, MEDIA, STATE, AUDIT

    assert importlib.util.find_spec('Backend.Flask') is None
    key=ec.generate_private_key(ec.SECP256R1())
    public=key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    now=int(time.time());subject='123456789012345678901';reader_url=sorted(READER_URLS)[0]
    token=jwt.encode(dict(iss='https://cloud.google.com/iap',aud=IAP_AUDIENCE,
        sub='accounts.google.com:123',iat=now-1,exp=now+599),key,algorithm='ES256',headers={'kid':'probe'})
    buckets={name:Mock() for name in (MEDIA,STATE,AUDIT)}
    for name,bucket in buckets.items():
        bucket.name=name
        bucket.blob.side_effect=AssertionError('closed runtime accessed source/audit objects')
    client=Mock();client.bucket.side_effect=lambda name:buckets[name]
    claims=dict(aud=reader_url,sub=subject,email=PORTAL_SA,email_verified=True,
        iss='https://accounts.google.com',iat=now-1,exp=now+599)
    with tempfile.TemporaryDirectory(prefix='lite-review-closed-') as temporary:
        d=Path(temporary);catalogue=json.dumps(dict(schema='lite-review-catalogue/1',captured_at=now,records=[])).encode()
        (d/'catalogue.json').write_bytes(catalogue);(d/'cursor.bin').write_bytes(b'x'*32)
        config=dict(roles={},reader_url=reader_url,portal_subject=subject,
            catalogue_path=str(d/'catalogue.json'),catalogue_sha256=hashlib.sha256(catalogue).hexdigest(),cursor_key_path=str(d/'cursor.bin'))
        (d/'config.json').write_text(json.dumps(config))
        for role in ('portal','reader'):
            service='woundai-lite-review'+('-reader' if role=='reader' else '')
            http=Mock()
            def get(url,**kwargs):
                response=Mock();response.status_code=200
                if url=='http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/email':
                    response.text=service+'@woundai-jackh001.iam.gserviceaccount.com'
                elif url=='https://www.gstatic.com/iap/verify/public_key':
                    response.json.return_value={'probe':public}
                else:raise AssertionError('unexpected network request '+url)
                return response
            http.get.side_effect=get
            environment={'K_SERVICE':service,'GOOGLE_CLOUD_PROJECT':'woundai-jackh001','LITE_REVIEW_CONFIG':str(d/'config.json')}
            with patch.dict(os.environ,environment,clear=True),patch.object(runtime.requests,'Session',return_value=http),patch('google.cloud.storage.Client',return_value=client),patch('google.oauth2.id_token.verify_oauth2_token',return_value=claims),patch('google.oauth2.id_token.fetch_id_token',side_effect=AssertionError('closed portal called reader')):
                app=getattr(runtime,role)()
                headers={'X-Goog-IAP-JWT-Assertion':token} if role=='portal' else {'Authorization':'Bearer offline-service-fixture','X-Lite-Review-IAP':token}
                path='/' if role=='portal' else '/v1/records'
                response=app.test_client().get(path,headers=headers)
                assert response.status_code==401 and response.json=={'error':'review_sign_in_required'}
        for bucket in buckets.values():bucket.blob.assert_not_called()
    print('PASS: standalone isolated import; real portal/reader factories reject signed human fixture with empty roles; no source/audit access')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--application-root',type=Path,default=Path('/app'))
    main(parser.parse_args().application_root)
