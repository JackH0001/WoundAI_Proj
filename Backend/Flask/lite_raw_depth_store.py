"""Draft single-object RGB-D persistence. NOT an upload route or authorization gate.

Caller must enforce device ownership, consent, revocation fencing and retention.
No automatic invocation from production API; this namespace is not yet connected
into the deployed deletion lifecycle. Immutable means no overwrite, NOT WORM.
"""
import hashlib
import re
import struct
from lite_raw_depth_contract import validate, MAX_PIXELS, MAX_JPEG_BYTES

_HEADER = struct.Struct('>4sIII')
_MAX_BUNDLE = _HEADER.size + 16384 + MAX_JPEG_BYTES + MAX_PIXELS*4


def capture_key(installation_id, capture_id):
    for value in (installation_id,capture_id):
        if not isinstance(value,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}',value):
            raise ValueError('invalid scoped capture key')
    return 'lite_raw/%s/%s.rgbd' % (installation_id,capture_id)


def pack(metadata, raw_depth, jpeg):
    validate(metadata,raw_depth,jpeg)
    # Keep exact submitted metadata bytes, so the sender can verify the whole bundle.
    return _HEADER.pack(b'LRD1',len(metadata),len(jpeg),len(raw_depth))+metadata+jpeg+raw_depth


def unpack(bundle):
    if not isinstance(bundle,bytes) or not _HEADER.size <= len(bundle) <= _MAX_BUNDLE:
        raise ValueError('invalid RGB-D bundle size')
    magic,nm,nj,nd=_HEADER.unpack_from(bundle)
    if magic!=b'LRD1' or nm>16384 or nj>MAX_JPEG_BYTES or nd>MAX_PIXELS*4 or len(bundle)!=_HEADER.size+nm+nj+nd:
        raise ValueError('invalid RGB-D bundle framing')
    start=_HEADER.size
    metadata=bundle[start:start+nm];start+=nm
    jpeg=bundle[start:start+nj];start+=nj
    result=validate(metadata,bundle[start:],jpeg)
    result['jpeg']=jpeg
    result['metadata_bytes']=metadata
    return result


def save_capture(store, installation_id, capture_id, metadata, raw_depth, jpeg):
    key=capture_key(installation_id,capture_id)
    bundle=pack(metadata,raw_depth,jpeg)
    store.put_blob_immutable(key,bundle,'application/octet-stream')
    readback=store.get_blob(key)
    if readback!=bundle:
        raise IOError('raw capture readback mismatch')
    decoded=unpack(readback)
    return dict(schema='lite.rgbd.receipt/1',installation_id=installation_id,capture_id=capture_id,
                bundle_sha256=hashlib.sha256(readback).hexdigest(),bundle_bytes=len(readback),
                metadata_sha256=hashlib.sha256(decoded['metadata_bytes']).hexdigest(),
                rgb_sha256=decoded['metadata']['rgb_sha256'],depth_sha256=decoded['metadata']['depth_sha256'],
                status='stored',registration='not_verified',training_admission='not_evaluated')


def erase_installation_objects(store, installation_id):
    """Erase current raw objects; NOT a complete withdrawal or concurrency fence.

    Caller must authenticate ownership, persist revocation, drain/fence writers,
    and keep the fence after this returns. A final empty listing only proves that
    snapshot; it cannot prevent a later write. No consent/withdrawal receipt here.
    Unknown keys fail closed before ANY deletion; do not trust a listing to scope
    destructive actions. Store errors propagate, so retries can resume safely.
    """
    prefix = capture_key(installation_id, 'scope').rsplit('/', 1)[0] + '/'
    keys = list(store.list_keys(prefix))
    for key in keys:
        if not isinstance(key, str) or not key.startswith(prefix):
            raise ValueError('raw listing escaped installation scope')
        name = key[len(prefix):]
        if not name.endswith('.rgbd') or capture_key(installation_id, name[:-5]) != key:
            raise ValueError('unexpected raw capture object')
    removed = 0
    for key in sorted(set(keys)):
        if store.delete(key):
            removed += 1
        if store.exists(key):
            raise IOError('raw object remains after deletion')
    if list(store.list_keys(prefix)):
        raise IOError('raw namespace not empty after deletion')
    return removed
