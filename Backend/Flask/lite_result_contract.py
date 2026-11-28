"""Bounded inference result structure; no model, billing or source authority."""
import json
import math
import re
from lite_attest_state import StateUnavailable


class RasterResultTooComplex(StateUnavailable):
    """A valid, newly computed raster cannot be represented by the wire contract."""


def validate_result(result, *, fresh_raster=False):
    if (type(result) is not dict or set(result) - {'student_output_contract'} != {'image_w','image_h','wound_polygons','confidence','route'}
            or any(type(result[k]) is not int or not 1<=result[k]<=8192 for k in ('image_w','image_h'))
            or result['image_w']*result['image_h']>16000000
            or type(result['confidence']) not in (int,float) or not math.isfinite(result['confidence'])
            or not 0<=result['confidence']<=1
            or type(result['route']) is not str or not 1<=len(result['route'])<=64):
        raise StateUnavailable('invalid bounded inference result')
    if 'student_output_contract' in result:
        version = result['student_output_contract']
        if not isinstance(version, str) or not re.fullmatch(r'[a-z0-9][a-z0-9._/-]{0,127}', version):
            raise StateUnavailable('invalid inference output version')
    polys=result['wound_polygons']
    if type(polys) is not list or (not fresh_raster and len(polys)>32):
        raise StateUnavailable('polygon bound')
    total=0
    for poly in polys:
        if type(poly) is not list or len(poly)<3 or (not fresh_raster and len(poly)>512):
            raise StateUnavailable('polygon points')
        total+=len(poly)
        for point in poly:
            if (type(point) is not list or len(point)!=2 or
                    any(type(v) not in (int,float) or not math.isfinite(v) for v in point)
                    or not 0<=point[0]<result['image_w'] or not 0<=point[1]<result['image_h']):
                raise StateUnavailable('polygon coordinate')
    # For new rasters, first rule out malformed geometry. Never turn bad data
    # from storage or provider errors into an attestation of a known failure.
    bound_error = RasterResultTooComplex if fresh_raster else StateUnavailable
    if len(polys)>32 or total>512:
        raise bound_error('polygon representation bound')
    encoded = json.dumps(result, sort_keys=True, separators=(',', ':'), allow_nan=False)
    if len(encoded.encode('utf-8'))>8192:
        raise bound_error('result size')
    return result
