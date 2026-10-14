"""Pixel-grid validation; equal aspect does not establish RGB-D registration."""
import io
import math
from PIL import Image


def finite(value):
    try:
        return type(value) in (int,float) and math.isfinite(value)
    except OverflowError:
        return False


def aspect(a, b, label):
    if any(type(v) is not int or v <= 0 for v in (*a,*b)):
        raise ValueError(label + ' dimensions')
    try:
        difference = abs((a[0]/a[1])/(b[0]/b[1])-1)
    except (OverflowError, ZeroDivisionError) as exc:
        raise ValueError(label + ' aspect out of range') from exc
    if not math.isfinite(difference) or difference > .01:
        raise ValueError(label + ' aspect mismatch (>1%)')


def intrinsics(ci, size):
    if not isinstance(ci,dict) or any(not finite(ci.get(k)) for k in ('fx','fy','cx','cy')):
        raise ValueError('intrinsics must be finite numbers')
    if ci['fx'] <= 0 or ci['fy'] <= 0 or not 0 <= ci['cx'] < size[0] or not 0 <= ci['cy'] < size[1]:
        raise ValueError('intrinsics focal length/principal point range')


def validate_rgb_depth(meta, image_w, image_h):
    aspect((image_w,image_h),(meta['width'],meta['height']), 'RGB-depth')
    for keys in (('rgb_w','rgb_h'),('rgb_width','rgb_height')):
        if any(k in meta for k in keys):
            if any(type(meta.get(k)) is not int for k in keys) or tuple(meta[k] for k in keys) != (image_w,image_h):
                raise ValueError('declared RGB dimensions do not match annotation')


def png_gray(raw, bits, limit):
    if (not isinstance(raw,bytes) or len(raw)>limit or len(raw)<33 or raw[:8]!=b'\x89PNG\r\n\x1a\n'
            or raw[12:16]!=b'IHDR' or raw[24] not in bits or raw[25]!=0):
        raise ValueError('invalid grayscale PNG; depth requires 16-bit, confidence 8/16-bit')
    size=(int.from_bytes(raw[16:20],'big'),int.from_bytes(raw[20:24],'big'))
    if any(not 1<=v<=4096 for v in size): raise ValueError('depth PNG dimensions')
    try:
        with Image.open(io.BytesIO(raw)) as im:
            if im.size!=size or getattr(im,'n_frames',1)!=1: raise ValueError('PNG dimensions/frames')
            im.verify()
        with Image.open(io.BytesIO(raw)) as im: im.load()
    except (OSError,SyntaxError,Image.DecompressionBombError) as exc:
        raise ValueError('corrupt depth PNG') from exc
    return size


def validate_png_depth(raw, ci, image_w, image_h, confidence=None):
    size=png_gray(raw,{16},16*1024*1024)
    aspect((image_w,image_h),size,'RGB-PNG-depth')
    if not isinstance(ci,dict): raise ValueError('missing intrinsics')
    grid='depth_png'
    ref=size
    if 'ref_width' in ci or 'ref_height' in ci:
        ref=(ci.get('ref_width'),ci.get('ref_height'))
        aspect(ref,size,'intrinsics-depth')
        grid='reference'
    intrinsics(ci,ref)
    if confidence is not None and png_gray(confidence,{8,16},16*1024*1024)!=size:
        raise ValueError('confidence dimensions mismatch')
    return grid
