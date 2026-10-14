"""Lossless tissue label contract, shared by ingestion and export/readback.

Accept 8-bit grayscale labels or mobile RGB(A) red-channel codes. Never
quantize, resize, clip or guess missing geometry for a training label.
"""
import io
import math
from PIL import Image

MAX_BYTES = 4 * 1024 * 1024
MAX_PIXELS = 4096 * 4096


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def validate_tissue_mask(raw, raster, image_w, image_h):
    if not isinstance(raw, bytes) or len(raw) > MAX_BYTES or raw[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('invalid tissue PNG or size exceeds 4MB')
    if not isinstance(raster, dict):
        raise ValueError('missing tissue raster geometry')
    mw, mh = raster.get('mw'), raster.get('mh')
    if any(type(v) is not int or v <= 0 for v in (mw, mh, image_w, image_h)) or mw * mh > MAX_PIXELS:
        raise ValueError('invalid tissue/image dimensions')
    if not (2 <= mw <= 4096 and 2 <= mh <= 4096):
        raise ValueError('invalid tissue raster dimensions')
    if len(raw) < 33 or raw[12:16] != b'IHDR' or raw[24] != 8:
        raise ValueError('invalid tissue PNG header/bit depth')
    pw, ph = int.from_bytes(raw[16:20], 'big'), int.from_bytes(raw[20:24], 'big')
    if not (2 <= pw <= 4096 and 2 <= ph <= 4096) or (pw, ph) != (mw, mh):
        raise ValueError('tissue PNG header dimensions mismatch')
    if all(k in raster for k in ('rx0', 'ry0', 'm_scale')):
        x, y, scale = (raster[k] for k in ('rx0', 'ry0', 'm_scale'))
        if not all(_number(v) for v in (x, y, scale)) or scale <= 0:
            raise ValueError('invalid tissue placement/scale')
        width, height = mw / scale, mh / scale
    else:
        raise ValueError('missing or ambiguous tissue placement')
    if (not all(_number(v) for v in (width, height)) or width <= 0 or height <= 0
            or min(image_w, x + width) - max(0, x) < 8
            or min(image_h, y + height) - max(0, y) < 8):
        raise ValueError('tissue raster image intersection below 8x8')
    try:
        with Image.open(io.BytesIO(raw)) as im:
            if im.format != 'PNG' or im.size != (mw, mh) or im.mode not in ('L', 'RGB', 'RGBA') or getattr(im, 'n_frames', 1) != 1:
                raise ValueError('tissue PNG dimensions/mode mismatch')
            im.verify()
        with Image.open(io.BytesIO(raw)) as im:
            im.load()
            channels = im.split()
            if channels[0].getextrema()[1] > 5:
                raise ValueError('tissue class outside 0..5')
            if im.mode != 'L' and any(ch.getextrema() != (0, 0) for ch in channels[1:3]):
                raise ValueError('tissue labels must use red channel only')
            if im.mode == 'RGBA' and channels[3].getextrema() != (255, 255):
                raise ValueError('tissue labels must be opaque')
    except (OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise ValueError('corrupt tissue PNG') from exc


def validate_tissue_payload(data):
    if 'tissue_mask_png' not in data:
        return True, []
    try:
        import base64
        encoded = data['tissue_mask_png']
        if not isinstance(encoded, str) or len(encoded) > 6 * 1024 * 1024:
            raise ValueError('tissue PNG encoding/size')
        validate_tissue_mask(base64.b64decode(encoded, validate=True), data.get('tissue_raster'),
                             data.get('image_w'), data.get('image_h'))
        r = data['tissue_raster']
        if 'tissue_edited' in data and type(data['tissue_edited']) is not bool:
            raise ValueError('tissue_edited must be bool')
        if 'tissue_edit_px' in data and (type(data['tissue_edit_px']) is not int or not 0 <= data['tissue_edit_px'] <= r['mw']*r['mh']):
            raise ValueError('tissue_edit_px range/type')
        if 'tissue_edit_ratio' in data and (not _number(data['tissue_edit_ratio']) or not 0 <= data['tissue_edit_ratio'] <= 1):
            raise ValueError('tissue_edit_ratio range/type')
    except (ValueError, TypeError) as exc:
        return False, [str(exc)]
    return True, []
