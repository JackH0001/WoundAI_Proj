"""Versioned final binary wound mask, independent of unknown tissue (class 0)."""
import base64
import hashlib
import io
from PIL import Image
from tissue_mask_contract import validate_tissue_mask

CONTRACT = 'woundai.annotation-masks/1'
FIELDS = ('mask_contract', 'wound_mask_png', 'wound_mask_sha256')


def decode_wound(data):
    if not any(k in data for k in FIELDS):
        return None
    if data.get('mask_contract') != CONTRACT or not all(k in data for k in FIELDS):
        raise ValueError('incomplete or unsupported wound mask contract')
    encoded = data['wound_mask_png']
    if not isinstance(encoded,str) or len(encoded)>6*1024*1024:
        raise ValueError('wound PNG encoding/size')
    raw=base64.b64decode(encoded,validate=True)
    if hashlib.sha256(raw).hexdigest()!=data['wound_mask_sha256']:
        raise ValueError('wound mask digest mismatch')
    validate_wound_bytes(raw,data)
    if not data.get('tissue_mask_png'):
        raise ValueError('paired tissue mask required')
    tissue=base64.b64decode(data['tissue_mask_png'],validate=True)
    validate_tissue_mask(tissue,data.get('tissue_raster'),data.get('image_w'),data.get('image_h'))
    validate_pair(raw, tissue)
    return raw


def validate_pair(raw, tissue):
    if tissue is None:
        raise ValueError('paired tissue mask missing')
    with Image.open(io.BytesIO(raw)) as wi, Image.open(io.BytesIO(tissue)) as ti:
        if wi.size != ti.size:
            raise ValueError('paired mask dimensions mismatch')
        wound = wi.getchannel('R') if wi.mode != 'L' else wi
        labels = ti.getchannel('R') if ti.mode != 'L' else ti
        if any(w == 0 and t != 0 for w, t in zip(wound.tobytes(), labels.tobytes())):
            raise ValueError('tissue label outside final wound mask')


def validate_wound_bytes(raw,data):
    validate_tissue_mask(raw,data.get('tissue_raster'),data.get('image_w'),data.get('image_h'))
    with Image.open(io.BytesIO(raw)) as im:
        labels=im.getchannel('R') if im.mode!='L' else im
        low,high=labels.getextrema()
        if high!=1:
            raise ValueError('wound mask must be nonempty binary 0/1')


def receipt_fields(rec):
    if rec.get('mask_contract')==CONTRACT:
        return {k:rec[k] for k in ('mask_contract','wound_mask_sha256','tissue_mask_sha256')}
    return {}
