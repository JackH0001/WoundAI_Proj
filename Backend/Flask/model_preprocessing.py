"""Load the authoritative model preprocessing in a checkout or packaged image.

The checkout wins when present; an invalid checkout must not fall through to a
stale vendor copy. Missing or invalid configuration is an error, not RGB [0,1].
"""
import json
import math
from pathlib import Path


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate preprocessing field')
        result[key] = value
    return result


def load_preprocessing(base_dir):
    base = Path(base_dir)
    source = base / '../../engineering/phase0/preprocessing.json'
    if not source.exists():
        source = base / 'vendor/preprocessing.json'
    with source.open(encoding='utf-8') as handle:
        config = json.load(handle, object_pairs_hook=_unique)
    if not isinstance(config, dict) or not isinstance(config.get('models'), dict) or not config['models']:
        raise ValueError('missing model preprocessing configuration')
    for name, model in config['models'].items():
        if not isinstance(model, dict):
            raise ValueError('invalid model preprocessing: ' + name)
        size = model.get('input_size')
        if (not isinstance(size, list) or len(size) != 2 or
                not all(type(n) is int and 8 <= n <= 4096 for n in size) or
                model.get('layout') not in ('NCHW', 'NHWC') or
                model.get('channel_order') not in ('RGB', 'BGR') or
                model.get('normalize') not in ('imagenet', '[0,1]', '[-1,1]')):
            raise ValueError('invalid model preprocessing: ' + name)
        if model['normalize'] == 'imagenet':
            for field in ('imagenet_mean', 'imagenet_std'):
                values = config.get(field)
                if (not isinstance(values, list) or len(values) != 3 or
                        not all(type(n) in (int, float) and math.isfinite(n) for n in values) or
                        (field == 'imagenet_std' and not all(n > 0 for n in values))):
                    raise ValueError('invalid ' + field)
    return config
