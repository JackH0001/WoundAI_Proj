"""Strict in-memory HTTP transport for real Storage SDK tests; never network.

This models the small API surface used by the isolated consent harness, not
Google's server semantics or evidence of actual cloud acceptance.
"""
import base64
from email.parser import BytesParser
from email import policy
import json
import io
from urllib3.response import HTTPResponse
from urllib.parse import urlparse, parse_qs, unquote
import google_crc32c
from google.api_core.exceptions import GoogleAPICallError
from google.auth.credentials import AnonymousCredentials
from google.cloud import storage
from requests import Response, Request


class Transport:
    is_mtls=False
    def __init__(self, raw):
        self.raw=raw; self.calls=[]; self.before=None

    def client(self, project):
        return storage.Client(project=project,credentials=AnonymousCredentials(),_http=self)

    def request(self, method, url, **kwargs):
        self.calls.append((method,url))
        if self.before:self.before(method,url,kwargs)
        parts=urlparse(url);q=parse_qs(parts.query);path=parts.path
        headers=kwargs.get('headers',{})
        def number(key):return int(q[key][0]) if key in q else None
        def crc(data):return base64.b64encode(google_crc32c.value(data).to_bytes(4,'big')).decode()
        def metadata(blob):
            key=blob._key();data=self.raw.rows[key]['data']
            return dict(bucket=key[0],name=key[1],generation=str(key[2]),size=str(len(data)),crc32c=crc(data))
        status=200;body=None;binary=False
        try:
            if path.startswith('/upload/storage/v1/b/'):
                assert method=='POST' and q['uploadType']==['multipart']
                bucket=path.removeprefix('/upload/storage/v1/b/').removesuffix('/o')
                content_type=headers.get('content-type',headers.get('Content-Type'))
                if isinstance(content_type,bytes):content_type=content_type.decode()
                message=BytesParser(policy=policy.default).parsebytes(
                    b'Content-Type: '+content_type.encode()+b'\r\n\r\n'+kwargs['data'])
                items=list(message.iter_parts());assert len(items)==2
                meta=json.loads(items[0].get_payload(decode=True));data=items[1].get_payload(decode=True)
                blob=self.raw.bucket(bucket).blob(meta['name'])
                blob.upload_from_string(data,if_generation_match=number('ifGenerationMatch'))
                body=metadata(blob)
            else:
                path=path.removeprefix('/download')
                assert path.startswith('/storage/v1/b/'),path
                bucket,_,suffix=path.removeprefix('/storage/v1/b/').partition('/')
                b=self.raw.bucket(bucket)
                if suffix.startswith('o/'):
                    blob=b.blob(unquote(suffix[2:]),generation=number('generation'))
                    if method=='GET':
                        blob.reload(if_generation_match=number('ifGenerationMatch'))
                        if q.get('alt')==['media']:
                            body=blob.download_as_bytes(if_generation_match=number('ifGenerationMatch'));binary=True
                        else:body=metadata(blob)
                    elif method=='DELETE':
                        blob.delete(if_generation_match=number('ifGenerationMatch'));status=204;body={}
                    else:raise AssertionError('unsupported object mutation')
                else:
                    assert method=='GET',method
                    if suffix=='':body=b._properties
                    elif suffix=='iam':body=dict(version=1,etag='synthetic',bindings=b.bindings)
                    elif suffix=='iam/testPermissions':body={'permissions':sorted(b.test_iam_permissions(q.get('permissions',[])))}
                    elif suffix=='o':
                        rows=self.raw.list_blobs(bucket,prefix=q.get('prefix',[''])[0],
                            versions=q.get('versions')==['True'],soft_deleted=q.get('softDeleted')==['True'])
                        start=int(q.get('pageToken',['0'])[0]);limit=min(2,number('maxResults') or 2)
                        chosen=rows[start:start+limit]
                        body={'items':[dict(name=r.name,generation=str(r.generation),size=str(r.size)) for r in chosen]}
                        if start+limit<len(rows):body['nextPageToken']=str(start+limit)
                    else:raise AssertionError('unsupported route '+suffix)
        except GoogleAPICallError as exc:
            status=int(exc.code);body={'error':{'code':status,'message':'injected synthetic API error'}}
        response=Response();response.status_code=status;response.url=url
        response.request=Request(method,url).prepare()
        response._content=body if binary else json.dumps(body).encode()
        response.headers['content-type']='application/octet-stream' if binary else 'application/json'
        response.headers['content-length']=str(len(response._content))
        if binary:response.headers['x-goog-hash']='crc32c='+crc(body)
        response.raw=HTTPResponse(body=io.BytesIO(response._content),headers=response.headers,preload_content=False)
        response._content_consumed=True
        return response
