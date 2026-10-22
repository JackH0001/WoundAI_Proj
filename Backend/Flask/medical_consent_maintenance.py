#!/usr/bin/env python3
"""Operator-run candidate: offline index backfill or policy media cleanup.

Defaults to read-only. No cloud resources, policies or schedules are changed.
Use only with medical writes paused for backfill. Never use --apply on research
media without a separately approved retention/deployment plan.
"""
import argparse,json,sys
from pathlib import Path
from medical_consent_order import ConsentOrder, ROOT, INDEX_MARKER, INDEX_SCHEMA
from medical_media_cleanup import cleanup_subject
from store import get_store


def backfill(order, *, apply=False, writers_paused=False):
    if apply and not writers_paused: raise ValueError('backfill requires all old/new writers paused')
    keys=order._keys(ROOT)
    plans=[]
    for key in keys:
        root=order.store.get_json(key)
        if not isinstance(root,dict): raise ValueError('corrupt root requires investigation')
        state=order.state(root['code'],root['org'])
        plans.append(state)
    if apply:
        for state in plans: order.index_images(state['code'],state['org'],state['image_ids'])
        if order._keys(ROOT)!=keys: raise ValueError('subjects changed during backfill')
        for state in plans:
            if order.state(state['code'],state['org'])!=state: raise ValueError('history changed during backfill')
        order.store.put_json_immutable(INDEX_MARKER,{'schema':INDEX_SCHEMA})
    return dict(subjects=len(plans),status='completed' if apply else 'dry_run')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('operation',choices=['index-backfill','media-cleanup'])
    p.add_argument('--root');p.add_argument('--apply',action='store_true')
    p.add_argument('--writers-paused',action='store_true')
    p.add_argument('--policy',help='JSON file with approved id and retention_seconds')
    a=p.parse_args();store=get_store(a.root)
    legacy=lambda:[json.loads(x) for x in store.read_lines_fresh('withdrawn.jsonl') if x.strip()]
    order=ConsentOrder(store,legacy)
    if a.operation=='index-backfill': result=backfill(order,apply=a.apply,writers_paused=a.writers_paused)
    else:
        if not a.policy: p.error('--policy is required (no implicit retention period)')
        policy=json.loads(Path(a.policy).read_text())
        result=[]
        for state in order.all_states():
            if state['status']=='fault':
                result.append(dict(status='blocked_fault',subject_hash=state.get('subject_hash'),code=state.get('code')));continue
            try: result.append(cleanup_subject(order,state['code'],state['org'],policy,apply=a.apply))
            except Exception as exc: result.append(dict(code=state['code'],status='pending',error=type(exc).__name__))
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if isinstance(result,list) and any(r['status'] in {'pending','blocked_fault','recoverable_media_pending','blocked_missing_timestamp'} for r in result): return 2
    return 0
if __name__=='__main__': raise SystemExit(main())
