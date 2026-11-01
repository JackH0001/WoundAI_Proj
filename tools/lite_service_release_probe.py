"""Reproducible local before/after service-release rehearsal. Synthetic data only."""
import argparse
import hashlib
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
import numpy as np
from lite_attest_state import SQLiteStateStore, StateUnavailable
from lite_service_operations import ServiceOperations, RasterInference
from test_lite_service_release import Fixture, ReleaseTests


def coordinator(f):
    state={'model_calls':0}
    def model(rgb):
        state['model_calls']+=1
        mask=np.zeros(rgb.shape[:2],dtype=np.uint8);mask[8:24,8:24]=1
        return mask,{'route':'synthetic-release-test'}
    buf=io.BytesIO();Image.new('RGB',(32,32),(160,70,70)).save(buf,format='JPEG');raw=buf.getvalue()
    ops=ServiceOperations(ledger=f.ledger,accounts=f.n.accounts,
        store=SQLiteStateStore(f.n.f.root/'release-results.sqlite'),
        inference=RasterInference(model,lambda _:(False,{})),model_version='a'*64,clock=lambda:f.n.a.now)
    return ops,raw,state


def run():
    ReleaseTests.setUpClass();host=unittest.TestCase();before=Fixture(host);after=Fixture(host)
    try:
        # Unpublished opt-in is not the comparison: reproduce the prior deployed
        # code path with no marker/reader, then let its signed observation expire.
        before.n.r.service_release=None;before.rollout.service_release=None;before.n.a.now+=31
        old_blocked=False
        try:before.grant()
        except StateUnavailable:old_blocked=True
        after.active();after.n.a.now+=172800
        ops,raw,stats=coordinator(after);op='1'*32;image_hash=hashlib.sha256(raw).hexdigest()
        with (patch.object(after.n.observer,'load',side_effect=AssertionError('daily observer read')),
              patch.object(after.provider,'observe',side_effect=AssertionError('daily provider read'))):
            prepared=ops.prepare('first-device',op,image_hash)
            first=ops.run('first-device',op,raw);repeat=ops.run('first-device',op,raw)
            other,registry=after.restart();cross_instance_before=other.allowed(registry.resolve('first-device'))
        result_before=ops.load(after.n.f.subject,op)
        pause=after.controller.apply(after.approval('pause'))
        cross_instance_paused=not other.allowed(registry.resolve('first-device'))
        old_result=ops.status('first-device',op)
        new_denied=False
        try:ops.prepare('first-device','2'*32,image_hash)
        except StateUnavailable:new_denied=True
        after.fresh();after.controller.apply(after.approval('resume'))
        return dict(schema='lite-service-release-probe/1',synthetic_only=True,
            before_observer_31s_new_work_denied=old_blocked,
            after_observer_48h_prepared=prepared['state'],model_calls=stats['model_calls'],
            used=after.snapshot()['used'],attempts=after.snapshot()['attempts'],same_result_on_retry=repeat==first,
            cross_instance_before=cross_instance_before,cross_instance_pause_blocks=cross_instance_paused,
            paused_new_denied=new_denied,paused_old_receipt_readable=old_result==first,
            original_result_preserved=result_before==ops.load(after.n.f.subject,op),
            resume_epoch=after.reader.snapshot()[1]['epoch'],paused_epoch=pause['epoch'],
            research_stored=first['research_stored'],actual_cloud=False,actual_apple=False,startup_enabled=False)
    finally:host.doCleanups()

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);a=p.parse_args()
    result=run();Path(a.output).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    assert result['before_observer_31s_new_work_denied'] and result['after_observer_48h_prepared']=='ready'
    assert (result['model_calls'],result['used'],result['attempts'])==(1,1,1)
    assert all(result[k] for k in ('same_result_on_retry','cross_instance_before','cross_instance_pause_blocks',
                                  'paused_new_denied','paused_old_receipt_readable','original_result_preserved'))
    assert result['resume_epoch']==3 and result['research_stored'] is False
