"""Synthetic before/after capture replay. No cloud, credentials or real images."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'Backend/Flask'), str(ROOT/'engineering/phase2'), str(ROOT/'tools')]
import test_lite_fenced_http as legacy
import test_lite_research_ingest_flow as current
from lite_raw_depth_store import unpack


def run():
    old=legacy.FencedHTTPTests(); old.setUp()
    try:
        module=legacy.raw_fixture.http_fixture.media_fixture.lite
        with patch.object(module,'_SEGMENT',wraps=module._SEGMENT) as model:
            responses=[old.upload(),old.upload()]
        assert [r.status_code for r in responses]==[200,200]
        before=dict(research_segment_calls=2, model_calls=model.call_count,
                    index_rows=len(old.rows('lite_index.jsonl')))
        assert before['model_calls']==2
    finally: old.doCleanups()
    current.ResearchIngestFlowTests.setUpClass()
    f=current.ResearchIngestFlowTests(); f.setUp()
    try:
        n,r=f.n,f.r
        n.login(); original=n.complete(); assert original.status_code==200
        usage=n.quota(); assert usage['used']==1
        ticket=n.post('service/research-source',**f.fields); assert ticket.status_code==200
        first=f.upload(ticket.json); assert first.status_code==200
        frozen=dict(f.bucket.live); again,lookup_body=f.receipt_request(ticket.json)
        assert again.status_code == 200 and n.f.jpeg not in lookup_body and r.f.raw not in lookup_body
        assert first.json==again.json and frozen==f.bucket.live
        iid=first.json['image_id']; receipts=[]
        contours={1:[[[2,2],[20,2],[20,20]]],2:[[[2,2],[24,2],[20,20]]]}
        for revision,area in [(1,4),(1,4),(2,5),(2,5)]:
            payload=dict(polygons=contours[revision],image_w=64,image_h=64,
                surface_cm2=area,projected_cm2=area,source='manual',consent_version='2026-10-16.1')
            body=dict(anon_id=r.h.owner,consent_epoch='legacy',research_consent=True,
                image_id=iid,revision=revision,payload_json=json.dumps(payload))
            reply=r.h.post_signed('/api/v1/lite/annotation/revision',json.dumps(body).encode())
            assert reply.status_code==200, reply.json
            receipts.append(reply.json)
        media=f.privacy.media(r.h.owner)
        labels = [json.loads(line) for line in media.read_lines_fresh('lite_labels.jsonl')]
        assert len(labels) == 2 and all(row['polygons'] == contours[row['revision']] for row in labels)
        original_polys = media.get_json(f'lite/{r.h.owner}/{iid}.json')['ai_polygons']
        assert original_polys == original.json['result']['wound_polygons']
        packet=unpack(media.get_blob('lite_raw/'+r.h.owner+'/'+iid+'.rgbd'))
        after=dict(native_model_calls=n.h.model_calls, personal_used=n.quota()['used'],
            receipt_lookup_body_bytes=len(lookup_body), retry_media_bytes=0,
            original_ai_contours_preserved=True, revised_contours_readback=contours,
            research_index_rows=len(media.read_lines_fresh('lite_index.jsonl')),
            annotation_rows=len(media.read_lines_fresh('lite_labels.jsonl')),
            revisions=[x['revision'] for x in receipts],same_revision_receipts=receipts[0]==receipts[1] and receipts[2]==receipts[3],
            original_media_versions_unchanged=frozen=={k:v for k,v in f.bucket.live.items() if k in frozen},
            rgb_bytes_equal=packet['jpeg']==n.f.jpeg,depth_bytes_equal=packet['raw_depth']==r.f.raw,
            rgb_sha256=hashlib.sha256(packet['jpeg']).hexdigest(),
            depth_sha256=hashlib.sha256(packet['raw_depth']).hexdigest(),
            original_service_receipt_unchanged=n.json_request('status').json==original.json)
        assert (after['native_model_calls'],after['personal_used'],after['research_index_rows'],after['annotation_rows'])==(1,1,1,2)
        assert all(after[k] for k in ('same_revision_receipts','original_media_versions_unchanged',
            'rgb_bytes_equal','depth_bytes_equal','original_service_receipt_unchanged'))
        deletion=r.delete(); assert deletion.status_code==200
        late=f.upload(ticket.json); assert late.status_code==410
        withdrawal=dict(status=deletion.json['status'],late_upload_status=late.status_code,
            all_live_payloads_sealed=all(v[1]==b'' and v[2]['state']=='sealed' for v in f.bucket.live.values()),
            native_receipt_unchanged=n.json_request('status').json==original.json,
            personal_quota_unchanged=n.quota()==usage)
        assert all(withdrawal[k] for k in ('all_live_payloads_sealed','native_receipt_unchanged','personal_quota_unchanged'))
        return dict(schema='lite.research-ingest-probe/1',before=before,after=after,withdrawal=withdrawal,
                    scope='synthetic HTTP, SQLite and generation-aware object fake; no deployment or real GCS')
    finally: f.doCleanups()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists(): raise SystemExit('new evidence path required')
    result=run(); args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
    print(json.dumps(result,indent=2,sort_keys=True))
