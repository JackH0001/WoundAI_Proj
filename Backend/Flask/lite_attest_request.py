"""App Attest request binding v1. Encoding only; NOT authentication.

The server must construct these fields from its trusted deployment, challenge and
registered key state plus the actual HTTP request. Never hash a client-supplied
body digest instead of the received body. Certificate/assertion verification,
one-time challenge consumption, counter CAS and ownership remain required.
"""
import hashlib
import re
import struct

DOMAIN = b'woundlite.appattest.request/1\x00'
MAX_BODY = 32 * 1024 * 1024


def client_data(*, audience, installation, key_id, challenge, request_id,
                method, path, content_type, body):
    def token(value, pattern):
        if not isinstance(value, str) or re.fullmatch(pattern, value) is None:
            raise ValueError('invalid request binding')
        return value.encode('ascii')
    aud = token(audience, r'[a-z0-9-]{1,64}')
    owner = token(installation, r'[A-Za-z0-9_-]{1,128}')
    rid = token(request_id, r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
    allowed = {'POST': {'/api/v1/lite/segment', '/api/v1/lite/annotation',
                        '/api/v1/lite/annotation/revision', '/api/v1/lite/research/rejoin',
                        '/api/v1/lite/service/prepare', '/api/v1/lite/service/run', '/api/v1/lite/service/status', '/api/v1/lite/service/recover', '/api/v1/lite/service/resubmit',
                        '/api/v1/lite/purchase/context','/api/v1/lite/purchase/confirm',
                        '/api/v1/lite/account/challenge','/api/v1/lite/account/confirm','/api/v1/lite/account/status','/api/v1/lite/account/logout','/api/v1/lite/account/renew',
                        '/api/v1/lite/account/deletion/request','/api/v1/lite/account/deletion/status', '/api/v1/lite/account/deletion/reauth/challenge', '/api/v1/lite/account/deletion/reauth/confirm'},
               'DELETE': {'/api/v1/lite/data/' + installation}}
    epoch_delete = (method == 'DELETE' and isinstance(path, str) and
                    re.fullmatch(re.escape('/api/v1/lite/data/' + installation) + r'/[0-9a-f]{32}', path) is not None)
    if not isinstance(method, str) or not isinstance(path, str) or (path not in allowed.get(method, set()) and not epoch_delete):
        raise ValueError('unsupported method or noncanonical path')
    ct = token(content_type, r'[\x20-\x7e]{0,200}')
    if type(key_id) is not bytes or len(key_id) != 32 or type(challenge) is not bytes or len(challenge) != 32:
        raise ValueError('invalid key or challenge')
    if type(body) is not bytes or len(body) > MAX_BODY or (method == 'DELETE' and body):
        raise ValueError('invalid request body')
    fields = (aud, owner, key_id, challenge, rid, method.encode('ascii'), path.encode('ascii'), ct, hashlib.sha256(body).digest())
    return DOMAIN + b''.join(struct.pack('>I', len(field)) + field for field in fields)


def client_data_hash(**request):
    return hashlib.sha256(client_data(**request)).digest()
