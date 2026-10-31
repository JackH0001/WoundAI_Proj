"""Synthetic retention byte/operation probe using real GCS CAS adapter.

Transport is in-memory FakeBucket, never live GCS. Historical generations are
deliberately preserved to show why compacting the current row is not erasure.
"""
from pathlib import Path
import argparse,json,math,sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'Backend/Flask'),str(ROOT/'engineering/phase2'),str(ROOT/'tools')]
import test_lite_service_http as fixture
from test_lite_attest_state import FakeBucket
from lite_attest_state import GCSStateStore
from lite_usage_ledger import UsageLedger
from lite_usage_probe import calls
from lite_service_operations import RESULT_TTL,validate_result
from lite_service_retention import ServiceResultRetention


def scenario(dense=False,unstarted=False):
    h=fixture.ServiceHTTPTests();h.setUp()
    buckets={n:FakeBucket() for n in ('result','usage','audit')}
    try:
        h.ops.store=GCSStateStore(buckets['result'],prefix='synthetic-retention/results')
        h.ledger=h.ops.ledger=UsageLedger(GCSStateStore(buckets['usage'],prefix='synthetic-retention/usage'),
            namespace='retention-probe',clock=lambda:h.e.now)
        h.initialize_day();audit=GCSStateStore(buckets['audit'],prefix='synthetic-retention/audit')
        job=ServiceResultRetention(h.ops,audit_store=audit,scope='b'*64);operation=f'{1:032x}'
        if dense:
            def known_result(rgb):
                h.model_calls+=1;height,width=rgb.shape[:2]
                points=[[round(width*(.5+.3*math.cos(i*2*math.pi/256)),2),
                         round(height*(.5+.3*math.sin(i*2*math.pi/256)),2)] for i in range(256)]
                return validate_result(dict(image_w=width,image_h=height,wound_polygons=[points],confidence=.25,route='synthetic-contour'))
            h.inference.infer=known_result
        h.ops.prepare(h.owner,operation,h.image_hash)
        if not unstarted:h.ops.run(h.owner,operation,h.f.jpeg)
        name=h.ops.store._name(h.ops.key(h.subject,operation))
        generation,before=buckets['result'].live[name]
        h.e.now+=RESULT_TTL+1
        starts={n:len(b.calls) for n,b in buckets.items()}
        plan=job.plan(h.subject,operation);done=job.execute(plan)
        counts={n:calls(b,starts[n]) for n,b in buckets.items()}
        starts={n:len(b.calls) for n,b in buckets.items()};same=job.execute(plan)
        replay={n:calls(b,starts[n]) for n,b in buckets.items()}
        assert done==same and buckets['result'].versions[(name,generation)]==before
        assert h.ops.load(h.subject,operation)[1]['result'] is None
        assert not any(x['write_attempts'] for x in replay.values())
        return dict(action=plan['action'],before_current_bytes=len(before),
            after_current_bytes=len(buckets['result'].live[name][1]),
            audit_current_bytes=sum(len(data) for _,data in buckets['audit'].live.values()),
            prior_generation_preserved=True,prior_generation_contained_result=json.loads(before)['result'] is not None,
            measured=dict(stores=counts,write_attempts=sum(x['write_attempts'] for x in counts.values()),
                          reads=sum(x['metadata_reads']+x['content_reads'] for x in counts.values())),
            replay=dict(stores=replay,write_attempts=sum(x['write_attempts'] for x in replay.values()),
                        reads=sum(x['metadata_reads']+x['content_reads'] for x in replay.values())),
            model_calls=h.model_calls,terminal_outcome=done['receipt']['outcome'],
            conditional_uploads=all('if_generation_match' in c[2] for b in buckets.values() for c in b.calls if c[0]=='upload'))
    finally:h.doCleanups()


def probe():
    return dict(schema='lite-service-retention-probe/1',synthetic_only=True,real_gcs_adapter=True,live_cloud=False,
        physical_generation_delete=False,profit_claim=False,
        cases={'small_completed':scenario(),'dense_completed':scenario(dense=True),'unstarted':scenario(unstarted=True)},
        exclusions=['Cloud IAM/listing/version purge','App Attest and model cost','network latency and billing',
                    'unknown-worker resolution','research media and audit retention'],
        note='Per-job metadata and two audit objects add overhead; do not infer storage savings or physical erasure.')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    result=probe();args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:{f:v[f] for f in ('before_current_bytes','after_current_bytes','audit_current_bytes','measured')}
                      for k,v in result['cases'].items()}))
