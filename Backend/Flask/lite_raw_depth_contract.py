"""Offline draft lite.rgbd/1 validation. No route, storage or research admission.

Preserves raw IEEE-754 bits, including invalid samples. Structural validation
cannot prove camera provenance, calibration, consent or authorization.
"""
import hashlib
import io
import json
import math
import struct
from PIL import Image

MIN_DEPTH_M = struct.unpack("<f", struct.pack("<f", .05))[0]  # Match Swift Float comparison.
MAX_PIXELS = 1024 * 1024
MAX_JPEG_BYTES = 16 * 1024 * 1024
_FIELDS = frozenset(('schema', 'format', 'unit', 'width', 'height', 'rgb_width',
    'rgb_height', 'rgb_sha256', 'depth_sha256', 'intrinsics', 'source_exif_orientation',
    'accuracy', 'filtered', 'confidence_kind', 'pose', 'capture_time', 'registration'))


def _unique(pairs):
    d = {}
    for k, v in pairs:
        if k in d: raise ValueError('duplicate metadata key')
        d[k] = v
    return d


def validate(metadata, raw_depth, jpeg):
    if not isinstance(metadata, bytes) or len(metadata) > 16384:
        raise ValueError('metadata size/type')
    if not isinstance(raw_depth, bytes) or len(raw_depth) > MAX_PIXELS * 4:
        raise ValueError('depth size/type')
    if not isinstance(jpeg, bytes) or not 0 < len(jpeg) <= MAX_JPEG_BYTES:
        raise ValueError('JPEG size/type')
    m = json.loads(metadata, object_pairs_hook=_unique)
    if not isinstance(m, dict) or set(m) != _FIELDS:
        raise ValueError('metadata fields')
    fixed = dict(schema='lite.rgbd/1', format='float32_le', unit='m',
                 confidence_kind='validity_only', pose='unavailable',
                 capture_time='unavailable', registration='not_verified')
    if any(m[k] != v for k, v in fixed.items()):
        raise ValueError('unsupported semantic contract')
    w, h, rw, rh = (m[k] for k in ('width', 'height', 'rgb_width', 'rgb_height'))
    if any(type(v) is not int or not 8 <= v <= 1024 for v in (w,h)) or w*h > MAX_PIXELS:
        raise ValueError('depth dimensions')
    if any(type(v) is not int or not 1 <= v <= 4096 for v in (rw,rh)):
        raise ValueError('RGB dimensions')
    if len(raw_depth) != w*h*4:
        raise ValueError('sample length')
    if abs((rw/rh)/(w/h)-1) > .01:
        raise ValueError('RGB-depth aspect')
    if type(m['source_exif_orientation']) is not int or not 1 <= m['source_exif_orientation'] <= 8:
        raise ValueError('orientation provenance')
    if m['accuracy'] not in ('absolute', 'relative') or type(m['filtered']) is not bool:
        raise ValueError('capture metadata')
    k = m['intrinsics']
    keys = ('fx','fy','cx','cy','reference_width','reference_height')
    if not isinstance(k,dict) or set(k) != set(keys): raise ValueError('intrinsics fields')
    if any(type(k[n]) not in (int,float) or not math.isfinite(k[n]) for n in keys):
        raise ValueError('nonfinite intrinsics')
    if not (k['fx'] > 0 and k['fy'] > 0 and 0 < k['reference_width'] <= 16384 and
            0 < k['reference_height'] <= 16384 and 0 <= k['cx'] < k['reference_width'] and
            0 <= k['cy'] < k['reference_height']):
        raise ValueError('intrinsics range')
    if abs((k['reference_width']/k['reference_height'])/(w/h)-1) > .01:
        raise ValueError('intrinsics reference aspect')
    for field, data in [('rgb_sha256',jpeg),('depth_sha256',raw_depth)]:
        if m[field] != hashlib.sha256(data).hexdigest(): raise ValueError('asset digest mismatch')
    with Image.open(io.BytesIO(jpeg)) as image:
        if image.format != 'JPEG' or image.size != (rw,rh) or image.getexif().get(274,1) != 1:
            raise ValueError('JPEG metadata mismatch')
        image.verify()
    with Image.open(io.BytesIO(jpeg)) as image: image.load()
    # A derived mask only. Never turn this into ARKit sensor confidence.
    validity = bytes(255 if math.isfinite(v) and MIN_DEPTH_M < v < 60 else 0
                     for (v,) in struct.iter_unpack('<f',raw_depth))
    return dict(metadata=m, raw_depth=raw_depth, validity_mask=validity,
                training_admission='not_evaluated')


def parse_multipart(form, files):
    """Strict bounded extraction from a parsed Werkzeug request. No storage.

    A route MUST also set a total request limit before multipart parsing,
    authenticate/authorize the caller, and check current consent/withdrawal.
    """
    if set(form) != {"raw_depth_metadata"} or set(files) != {"image", "raw_depth"}:
        raise ValueError("multipart fields")
    if any(len(form.getlist(k)) != 1 for k in form) or any(len(files.getlist(k)) != 1 for k in files):
        raise ValueError("duplicate multipart fields")
    text = form["raw_depth_metadata"]
    if not isinstance(text, str) or len(text) > 16384:
        raise ValueError("metadata size/type")
    metadata = text.encode("utf-8")
    raw = files["raw_depth"].stream.read(MAX_PIXELS * 4 + 1)
    jpeg = files["image"].stream.read(MAX_JPEG_BYTES + 1)
    return validate(metadata, raw, jpeg)
