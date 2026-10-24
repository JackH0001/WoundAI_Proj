"""Explicit Cloud Run entry points. No import-time ADC or local demo fallback."""
import hashlib
import os
import re
import time
from pathlib import Path
import requests
from .auth import IapVerifier,ReaderIdentity
from .contracts import MEDIA,STATE,AUDIT,PORTAL_SA,READER_URLS,BOOTSTRAP_READER_URL,Catalogue,ReviewError,require,decode
from .web import portal_app,reader_app

def config(role):
    service='woundai-lite-review'+('-reader' if role=='reader' else '')
    require(os.environ.get('K_SERVICE')==service and os.environ.get('GOOGLE_CLOUD_PROJECT')=='woundai-jackh001','review_runtime_scope_mismatch')
    require(not any(k.startswith(('WOUNDAI_', 'GOOGLE_APPLICATION_CREDENTIALS')) for k in os.environ),'legacy_runtime_configuration_forbidden')
    http=requests.Session();http.trust_env=False
    response=http.get('http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/email',
        headers={'Metadata-Flavor':'Google'},timeout=5,allow_redirects=False)
    require(response.status_code==200 and response.text.strip()==service+'@woundai-jackh001.iam.gserviceaccount.com','review_runtime_identity_mismatch')
    path=Path(os.environ['LITE_REVIEW_CONFIG'])
    require(path.stat().st_size<=65536)
    value=decode(path.read_bytes())
    require(set(value)=={'roles','reader_url','portal_subject','catalogue_path','catalogue_sha256','cursor_key_path'})
    require(value['reader_url'] in READER_URLS or
        (value['reader_url']==BOOTSTRAP_READER_URL and value['roles']=={}), 'review_reader_url_unverified')
    return value,http

class Backend:
    def __init__(self,http,url,token):self.http,self.url,self.token=http,url,token
    def __call__(self,path,params,iap,image):
        require(path=='/v1/records' or re.fullmatch(r'/v1/records/[0-9a-f]{64}(?:/image)?',path) is not None)
        try:
            bearer='Bearer '+self.token()
            # Cloud Run consumes the platform header; keep the full signed token
            # in Authorization for independent verification inside the reader.
            with self.http.get(self.url+path,params=params,headers={'Authorization':bearer,'X-Serverless-Authorization':bearer,
                'X-Lite-Review-IAP':iap},timeout=(5,90),allow_redirects=False,stream=True) as response:
                limit=12*1024*1024 if image else 4*1024*1024
                body=bytearray()
                for chunk in response.iter_content(65536):
                    require(len(body)+len(chunk)<=limit,'review_response_too_large')
                    body.extend(chunk)
                if response.status_code!=200:
                    raise ReviewError('review_reader_request_failed',response.status_code if response.status_code in (400,401,403,404,409,410,429) else 503)
                require(response.headers.get('Content-Type','').split(';')[0]==('image/jpeg' if image else 'application/json'))
                return bytes(body) if image else decode(bytes(body))
        except ReviewError:raise
        except Exception:raise ReviewError('review_reader_unavailable') from None

def portal():
    c,http=config('portal')
    from google.oauth2.id_token import fetch_id_token
    from google.auth.transport.requests import Request
    transport=Request(session=http)
    return portal_app(IapVerifier(http,c['roles']),Backend(http,c['reader_url'],lambda:fetch_id_token(transport,c['reader_url'])))

def reader():
    c,http=config('reader')
    from google.cloud import storage
    from google.oauth2.id_token import verify_oauth2_token
    from google.auth.transport.requests import Request
    from .storage import Source,AccessAudit
    from .reader import Reader
    path=Path(c['catalogue_path']);require(path.stat().st_size<=16*1024*1024)
    data=path.read_bytes();require(hashlib.sha256(data).hexdigest()==c['catalogue_sha256'])
    catalogue=Catalogue(decode(data))
    require(0<=time.time()-catalogue.captured_at<=86400,'review_catalogue_refresh_required')
    key=Path(c['cursor_key_path']).read_bytes();require(len(key)==32)
    client=storage.Client(project='woundai-jackh001')
    service=Reader(catalogue,Source(client.bucket(MEDIA),client.bucket(STATE)),AccessAudit(client.bucket(AUDIT)),key)
    transport=Request(session=http)
    auth=ReaderIdentity(lambda token,aud:verify_oauth2_token(token,transport,aud),c['reader_url'],c['portal_subject'])
    app=reader_app(service,IapVerifier(http,c['roles']),auth)
    @app.before_request
    def fresh_catalogue():
        require(0<=time.time()-catalogue.captured_at<=86400,'review_catalogue_refresh_required')
    return app
