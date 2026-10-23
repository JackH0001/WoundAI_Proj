#!/usr/bin/env python3
"""Operator candidate: manifest-bound index migration or audited cleanup.

Read-only by default. This tool never creates jobs or changes IAM/policies.
Plans/results contain subject mappings: keep them in protected evidence storage.
"""
import argparse
import json
import os
import re
from pathlib import Path
from medical_consent_order import ConsentOrder, digest, subject
from medical_consent_migration import plan_migration, apply_migration
from medical_media_cleanup import cleanup_subject, validate_policy
from store import get_store

RUNS = 'receipts/medical_cleanup_runs'
PENDING = {'pending', 'blocked_fault', 'recoverable_media_pending', 'blocked_missing_timestamp'}


def backfill(order, *, apply=False, writers_paused=False, plan=None,
             expected_sha256=None, care_keys=None):
    if not apply:
        return plan_migration(order, care_keys)
    return apply_migration(order, plan, expected_sha256,
                           writers_paused=writers_paused, care_keys=care_keys)


def cleanup_run(order, policy, *, apply=False, run_id=None, max_subjects=2000, now=None):
    """One bounded subject batch; same finished execution is read-only replay.

    A different execution rechecks every old purge claim for late orphan media.
    Overlapping executions are allowed: consent CAS and pinned deletion, not a
    process lock or Scheduler delivery, provide the safety boundary.
    """
    validate_policy(policy)
    if type(max_subjects) is not int or not 1 <= max_subjects <= 10000:
        raise ValueError('max_subjects must be 1..10000')
    if apply and (not isinstance(run_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,120}', run_id)):
        raise ValueError('explicit unique execution run_id required')
    key = RUNS+'/'+str(run_id)
    binding = dict(schema='medical-cleanup-run/1', run_id=run_id,
                   policy=policy, max_subjects=max_subjects)
    if apply:
        old = order.store.get_json(key+'.intent.json')
        if old is not None and old != binding: raise ValueError('execution id reused with different policy/bounds')
        done = order.store.get_json(key+'.outcome.json')
        if done is not None:
            if (not isinstance(done, dict) or old != binding or done.get('binding_sha256') != digest(binding)
                    or done.get('schema') != 'medical-cleanup-run-result/1' or done.get('mode') != 'apply'
                    or done.get('status') not in {'checked', 'needs_attention'}
                    or not isinstance(done.get('results'), list)
                    or any(not isinstance(r, dict) or r.get('status') not in PENDING | {'not_due', 'media_versions_absent_at_check'}
                           for r in done['results'])
                    or (done['status'] == 'checked' and any(r['status'] in PENDING for r in done['results']))):
                raise ValueError('execution receipt mismatch')
            return dict(done, replayed=True)
    states = order.all_states()
    # Refuse rather than silently dropping later subjects or starving the same
    # tail on every schedule. Larger deployments need a reviewed shard plan.
    if len(states) > max_subjects: raise ValueError('subject bound exceeded; no cleanup started')
    if apply: order.store.put_json_immutable(key+'.intent.json', binding)
    results = []
    for state in states:
        if state['status'] == 'fault':
            results.append(dict(status='blocked_fault', subject_hash=state.get('subject_hash') or subject(state['code'])))
            continue
        try:
            results.append(cleanup_subject(order, state['code'], state['org'], policy, apply=apply, now=now))
        except Exception as exc:
            # Retain details only in protected state, never treat exception as
            # absence/deletion success. Do not include raw SDK error/credentials.
            results.append(dict(subject_hash=subject(state['code']), status='pending', error=type(exc).__name__))
    result = dict(schema='medical-cleanup-run-result/1', binding_sha256=digest(binding),
                  status='needs_attention' if any(r['status'] in PENDING for r in results) else 'checked',
                  mode='apply' if apply else 'dry_run', results=results)
    if apply: order.store.put_json_immutable(key+'.outcome.json', result)
    return result


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('operation', choices=['index-backfill', 'media-cleanup'])
    p.add_argument('--root'); p.add_argument('--apply', action='store_true')
    p.add_argument('--writers-paused', action='store_true')
    p.add_argument('--plan', help='previous protected migration JSON (required for apply)')
    p.add_argument('--expected-sha256', help='approved migration plan digest')
    p.add_argument('--output', help='create-only local evidence JSON, mode 0600')
    p.add_argument('--policy', help='approved JSON id and retention_seconds; no default')
    p.add_argument('--run-id', default=os.environ.get('CLOUD_RUN_EXECUTION'))
    p.add_argument('--max-subjects', type=int, default=2000)
    a = p.parse_args(argv)
    if a.output and Path(a.output).exists(): p.error('output already exists')
    if a.operation == 'index-backfill' and a.apply and not (a.plan and a.expected_sha256 and a.writers_paused):
        p.error('apply requires --plan, --expected-sha256 and --writers-paused')
    if a.operation == 'media-cleanup' and (not a.policy or (a.apply and not a.run_id)):
        p.error('cleanup requires --policy; apply also requires --run-id or CLOUD_RUN_EXECUTION')
    store = get_store(a.root)
    legacy = lambda: [json.loads(x) for x in store.read_lines_fresh('withdrawn.jsonl') if x.strip()]
    order = ConsentOrder(store, legacy)
    if a.operation == 'index-backfill':
        from consent_staging import CareKeys, ConsentError
        try: keys = CareKeys()
        except ConsentError: keys = None  # staging evidence stays explicitly blocked
        result = backfill(order, apply=a.apply, writers_paused=a.writers_paused,
                          plan=json.loads(Path(a.plan).read_text()) if a.plan else None,
                          expected_sha256=a.expected_sha256, care_keys=keys)
    else:
        result = cleanup_run(order, json.loads(Path(a.policy).read_text()), apply=a.apply,
                             run_id=a.run_id, max_subjects=a.max_subjects)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)+'\n'
    if a.output:
        fd = os.open(a.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as handle: handle.write(rendered)
        print(json.dumps(dict(evidence=a.output, sha256=digest(result), status=result.get('status', 'planned'))))
    else: print(rendered)
    return 2 if result.get('status') == 'needs_attention' or result.get('apply_allowed') is False else 0


if __name__ == '__main__': raise SystemExit(main())
