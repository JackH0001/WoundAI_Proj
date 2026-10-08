"""Offline integrity preflight for a scoped Lite audit snapshot.

Does not authorize research use, classify consent, or export training data.
Input hashes must come from separately reviewed evidence. A matching aspect
ratio is not proof of RGB/depth registration. No network or cloud writes.
"""
import argparse
import hashlib
import json
import io
import math
from pathlib import Path
import re
from PIL import Image


def inspect_record(record, assets_dir):
    image_id = record.get('image_id', '')
    if not isinstance(image_id, str) or not re.fullmatch(r'[0-9a-f]{16}', image_id):
        raise ValueError('Invalid scoped image_id')
    root = Path(assets_dir).resolve(strict=True)
    errors, sizes, verified = [], {}, {}
    for suffix in ('jpg', 'json', 'depth.png', 'conf.png'):
        path = (root / (image_id + '.' + suffix)).resolve(strict=True)
        if path.parent != root:
            raise ValueError('Asset escapes scoped directory')
        raw = path.read_bytes()
        expected = record.get('assets', {}).get(suffix, {}).get('sha256')
        digest = hashlib.sha256(raw).hexdigest()
        if not isinstance(expected, str) or digest != expected:
            errors.append('hash_mismatch:' + suffix)
        verified[suffix] = digest
        if suffix == 'json':
            meta = json.loads(raw)
            if not isinstance(meta, dict):
                raise ValueError('Metadata must be an object')
            continue
        with Image.open(io.BytesIO(raw)) as im:
            im.verify()
        with Image.open(io.BytesIO(raw)) as im:
            im.load()
            sizes[suffix] = im.size
            if suffix == 'jpg':
                if im.format != 'JPEG':
                    errors.append('rgb_not_jpeg')
                if hashlib.sha1(raw).hexdigest()[:16] != image_id:
                    errors.append('rgb_image_identity_mismatch')
            if suffix == 'conf.png' and im.mode == 'L':
                if any(count for value, count in enumerate(im.histogram()) if value not in (0, 255)):
                    errors.append('validity_mask_not_binary')
            if suffix == 'depth.png' and (raw[:8] != b'\x89PNG\r\n\x1a\n' or raw[24:26] != bytes((16, 0))):
                errors.append('depth_not_png16_gray')
            if suffix == 'conf.png' and (im.format != 'PNG' or im.mode != 'L'):
                errors.append('validity_mask_not_png8_gray')
    rgb, depth, mask = sizes['jpg'], sizes['depth.png'], sizes['conf.png']
    if depth != mask:
        errors.append('depth_validity_size_mismatch')
    if abs((rgb[0] / rgb[1]) / (depth[0] / depth[1]) - 1) > .01:
        errors.append('rgb_depth_aspect_mismatch')
    k = meta.get('camera_intrinsics', {})
    if (meta.get('image_w'), meta.get('image_h')) != rgb:
        errors.append('metadata_rgb_size_mismatch')
    if meta.get('image_id') != image_id:
        errors.append('metadata_image_identity_mismatch')
    if meta.get('depth_format') != 'png16_mm' or meta.get('depth_scale') not in (0.001, '0.001'):
        errors.append('unknown_depth_encoding')
    values = [k.get(name) for name in ('fx', 'fy', 'cx', 'cy')]
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
        errors.append('invalid_intrinsics')
    elif not (values[0] > 0 and values[1] > 0 and 0 <= values[2] < depth[0] and 0 <= values[3] < depth[1]):
        errors.append('implausible_intrinsics_in_depth_grid')
    pending, ack = record.get('pending_revision'), record.get('acknowledged_revision')
    if type(pending) is not int or type(ack) is not int or pending < 1 or ack < 1 or pending != ack:
        errors.append('latest_annotation_not_confirmed')
    return dict(image_id=image_id, file_sha256=verified, dimensions=sizes,
                issues=errors, structural_checks_passed=not errors,
                training_admission='not_evaluated', registration='not_verified',
                confidence_kind='validity_only', pose='unavailable',
                depth_representation='derived_uint16_millimetres',
                limitations=['Snapshot cannot prove current consent or withdrawal state.',
                             'These checks do not prove sensor accuracy or image-depth registration.',
                             'No pose, native confidence or exposure timestamp is inferred.'])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--audit', type=Path, required=True)
    p.add_argument('--assets', type=Path, required=True)
    args = p.parse_args()
    audit = json.loads(args.audit.read_text())
    output = [inspect_record(record, args.assets) for record in audit['records']]
    print(json.dumps(dict(schema_version=1, purpose='offline_preflight_only', records=output), indent=2))
    return 2 if any(record['issues'] for record in output) else 0

if __name__ == '__main__':
    raise SystemExit(main())
