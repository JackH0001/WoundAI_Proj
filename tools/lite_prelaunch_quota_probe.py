"""Offline characterization of CURRENT Lite inner-route admission, not a fix.

Each Flask client calls production api_lite with a synthetic image/model. A barrier
holds inference until all concurrent requests passed rate_check. A locked in-memory
ledger removes append loss as a confounder. No network, GCS, App Attest or real model.
This does not prove the deployed outer admission permits this interleaving: it proves
the quota implementation alone cannot safely authorize future paid concurrency.
"""
from concurrent.futures import ThreadPoolExecutor
import argparse
import copy
import io
import json
from pathlib import Path
import sys
import threading
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'engineering/phase2'))
import test_lite_quota as fixture


def characterize(parallel=8, limit=2):
    if type(parallel) is not int or not 1 <= parallel <= 32 or limit not in (2, 5, 10):
        raise ValueError('bounded probe')
    f = fixture.LiteQuotaTests(); f.setUp()
    rows = {}; lock = threading.Lock(); barrier = threading.Barrier(parallel)
    calls = 0
    def read(path, with_bad=False, **kw):
        with lock:
            data = copy.deepcopy(rows.get(path, []))
        return (data, 0) if with_bad else data
    def append(path, entry):
        with lock:
            rows.setdefault(path, []).append(copy.deepcopy(entry))
    def model(rgb):
        nonlocal calls
        with lock:
            calls += 1
        if parallel > 1:
            barrier.wait(timeout=10)
        return f.segment(rgb)
    def request():
        with f.client.application.test_client() as client:
            r = client.post('/api/v1/lite/segment', data={
                'anon_id': 'synthetic-prelaunch', 'research_consent': 'false',
                'image': (io.BytesIO(f.jpeg), 'synthetic.jpg')})
            return dict(status=r.status_code, stored=r.json.get('stored'),
                        polygons=bool(r.json.get('wound_polygons')))
    try:
        with patch.object(fixture.lite, 'LIMIT_PER_ANON', limit), \
                patch.object(fixture.lite, '_SEGMENT', model), \
                patch.object(fixture.fw, 'read_jsonl', read), \
                patch.object(fixture.fw, 'append_jsonl', append), \
                patch.object(fixture.lite.time, 'time', return_value=1792886400):
            if parallel == 1:
                responses = [request() for _ in range(limit+1)]
            else:
                with ThreadPoolExecutor(max_workers=parallel) as pool:
                    responses = list(pool.map(lambda _: request(), range(parallel)))
            usage = fixture.lite.quota_snapshot('synthetic-prelaunch', '127.0.0.1')[0]['used']
            return dict(configured_limit=limit, parallel=parallel, responses=responses,
                        model_calls=calls, recorded_successes=usage,
                        quota_within_limit=usage <= limit,
                        research_media_created=any(Path(f.tmp.name).rglob('*.jpg')))
    finally:
        f.doCleanups()


def report():
    rows = [characterize(1, n) for n in (2, 5, 10)] + [characterize(8, 2), characterize(8, 5)]
    return dict(schema='lite-prelaunch-quota-probe/1', scope='LOCAL_INNER_ROUTE_ONLY',
                simulated_configuration_not_deployed=True,
                cases=rows, parallel_quota_gate=all(x['quota_within_limit'] for x in rows),
                recommendation='Atomic reserve/settle with idempotency before model work; do not use this JSONL check/append as a paid ledger.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    with args.output.open('x') as f:
        json.dump(report(), f, ensure_ascii=False, indent=2)
        f.write('\n')
