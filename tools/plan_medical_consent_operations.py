#!/usr/bin/env python3
"""Emit a review-only GCS/scheduler plan. No SDK, credentials or cloud mutation."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

FILES = (
    'Backend/Flask/medical_consent_order.py', 'Backend/Flask/medical_consent_migration.py',
    'Backend/Flask/medical_media_cleanup.py', 'Backend/Flask/medical_consent_maintenance.py',
    'Backend/Flask/store.py', 'Backend/Flask/consent_staging.py',
    'engineering/phase2/test_medical_consent_operations.py', 'engineering/phase2/medical_fake_gcs.py',
    'tools/plan_medical_consent_operations.py',
    'engineering/contracts/medical-consent-operations-acceptance-v1.md',
)


def plan(commit, files, clean):
    if not isinstance(commit, str) or not re.fullmatch(r'[0-9a-f]{40}', commit): raise ValueError('exact commit required')
    if type(clean) is not bool or set(files) != set(FILES): raise ValueError('complete source manifest required')
    if any(not isinstance(h, str) or not re.fullmatch(r'[0-9a-f]{64}', h) for h in files.values()):
        raise ValueError('invalid source digest')
    return dict(
        schema='medical-consent-operations-review/1', state='review_only', execution_authorized=False,
        source_commit=commit, source_clean=clean, files=files,
        project='woundai-jackh001', region='asia-east1',
        synthetic_gcs=dict(
            media_bucket='woundai-consent-smoke-20261023-media-421209514056',
            audit_bucket='woundai-consent-smoke-20261023-audit-421209514056',
            prefix='consent-ops-synthetic/20261023',
            require_new_empty_dedicated_buckets=True, max_minutes=20,
            max_objects=1000, max_uploaded_bytes=16*1024*1024,
            data='generated synthetic bytes only; no copying MMH/Demo/Lite objects',
            deletes='only explicitly inventoried synthetic media generations; retain all audit evidence',
            audit_retention='no irreversible lock; separate smoke-only guarded adapter must be reviewed',
            cases=['migration-readonly-and-resume', 'withdraw-restore-CAS', 'overlapping-purge-workers',
                   'restore-before-purge', 'purge-before-reconsent-new-media', 'noncurrent-generation-delete',
                   'late-generation-write', 'soft-delete-recoverable', 'policy-disabled-old-soft-version',
                   'missing-permission-and-object-hold', 'immutable-audit-readback'],
        ),
        scheduler_candidate=dict(
            enabled=False, initial_mode='dry_run', timezone='Asia/Taipei', proposed_cron='30 3 * * *',
            tasks=1, parallelism=1, max_retries=0, task_timeout_seconds=600,
            max_subjects=2000, cpu=1, memory='512Mi',
            scheduler_auth='OAuth service account to Cloud Run Jobs API; not OIDC',
            execution_identity='separate worker SA; Scheduler SA only job invocation',
            overlap='parallelism=1 is per execution, not an inter-execution lock; rely on CAS/pinned generations',
            retry='new execution after pending outcome; same completed run ID is read-only replay',
            production_policy=None, image_digest=None,
        ),
        unchanged=['MMH', 'Demo', 'Lite candidate', 'existing buckets and IAM', 'store distribution'],
        gates=['clean reviewed source and CI', 'separate cloud creation/write/delete/cost authorization',
               'read back isolated bucket IAM/versioning/soft-delete/holds before writes',
               'smoke-only adapter scope review; never spoof MMH/Demo K_SERVICE',
               'soft-version LIST capability before any purge; current policy-zero GCS returns 400, no automatic policy change',
               'real GCS evidence, including natural soft expiry or status kept pending',
               'approved production retention and retained-metadata disclosures',
               'pause and drain all writers, reviewed migration digest and zero unresolved owners',
               'worker audit identity valid for selected deployment; no validation-scope bypass',
               'fixed container digest, private job/IAM and budget review',
               'dry-run observation and alert delivery acceptance before schedule activation'],
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', required=True); a = p.parse_args()
    root = Path(__file__).resolve().parents[1]
    def git(*args): return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()
    files = {name: hashlib.sha256((root/name).read_bytes()).hexdigest() for name in FILES}
    body = plan(git('rev-parse', 'HEAD'), files, not bool(git('status', '--porcelain')))
    payload = json.dumps(body, ensure_ascii=False, sort_keys=True, indent=2)+'\n'
    with open(a.output, 'x', encoding='utf-8') as out: out.write(payload)
    print(json.dumps(dict(path=str(Path(a.output).resolve()), sha256=hashlib.sha256(payload.encode()).hexdigest(),
                          source_clean=body['source_clean'], execution_authorized=False)))


if __name__ == '__main__': main()
