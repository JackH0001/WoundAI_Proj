#!/usr/bin/env python3
"""Local review plan only; no cloud client, credentials, deploy or delete command."""
import hashlib,json,subprocess,os,argparse,re
from pathlib import Path

def plan(commit,files):
    if not re.fullmatch('[0-9a-f]{40}',commit) or not files or any(not re.fullmatch('[0-9a-f]{64}',h) for h in files.values()):raise ValueError('fixed source required')
    return dict(schema='lite-review-rollout/1',state='review_only',execution_authorized=False,
        source_commit=commit,files=files,project='woundai-jackh001',region='asia-east1',
        review=dict(services=['woundai-lite-review','woundai-lite-review-reader'],image_digest=None,
            entry='IAP signed ES256 assertion; separate reader independently verifies both human assertion and portal service token',
            allowed_subjects=None,owner_only_initially=True,allUsers=False,min_instances=0,max_instances=1,concurrency=1,
            media_bucket='woundai-lite-media-421209514056',state_bucket='woundai-lite-security-421209514056',
            access_audit_bucket='woundai-lite-review-audit-421209514056',catalogue='reviewed local mounted snapshot <=2000 records; 24h freshness; 50/page',
            discovery='bounded read-only inventory/export under separate authorization; no full bucket scans in HTTP',
            permissions={'portal':['invoke dedicated reader only; no GCS'],
                'reader':['Lite media get only','Lite security get only','dedicated review audit create only'],
                'catalogue_operator':['reviewed Lite media list/get only; no mutations']},
            residual='security GET covers hashed state kinds at IAM layer; API only derives consent/fence keys',
            denied=['MMH/Demo buckets','source writes/deletes','security list','training','raw export','App reconsent','impersonation'],
            activation_gates=['reviewed image digest and exact CI','verified owner Google subject and portal SA numeric subject',
                'independent IAP client eligibility without changing MMH project OAuth settings',
                'effective IAM incl inherited denial and source bucket policy readback',
                'create-only access audit retention/disclosure approved','synthetic two-hop auth and withdrawal-race live acceptance']),
        medical_migration=dict(applies_to=['MMH medical consent indexes','Demo medical consent indexes'],
            excludes=['Lite fenced enrollment data','consent-smoke originals'],mode='plan only',
            source_plan_sha256=None,writer_drain_evidence=None,retention_policy=None,
            sequence=['read-only source generations and ownership inventory','resolve unknown owners without guessing',
                'review exact manifest and policy','pause and drain all old/new writers',
                'additive backfill both ownership directions with fixed manifest','read back sources unchanged and outputs',
                'publish completion marker last','restore only purge-aware readers/writers'],
            no_media_delete=True,no_audit_rewrite=True),
        lite_catalogue=dict(immutable_epochs=True,never_run_medical_backfill=True,
            original_payloads_and_fences='read only; no rewritten labels/consent',
            invalid_or_unknown='block selected record; retain source; no identity guessing'),
        cleanup=dict(created=False,enabled=False,initial_mode='dry_run',targets=None,policy=None,image_digest=None,
            applies_to='medical consent worker only; never point it at Lite fenced storage',
            lite='current payload withdrawal remains epoch-scoped; permanent fence markers are never scheduled for deletion',
            schedule='30 3 * * *',timezone='Asia/Taipei',tasks=1,parallelism=1,max_retries=0,task_timeout_seconds=600,
            scheduler_auth='OAuth to Cloud Run Jobs API',worker_profile='exact job identity required; never spoof K_SERVICE',
            audit='create-only receipts retained; no audit delete',overlap='immutable CAS and generation preconditions, not scheduler exactly-once',
            activation_gates=['approved per-environment retention policy','migration complete and drained writers',
                'soft-version LIST works without changing policy','remaining live failure-injection and natural expiry evidence',
                'dry-run readback and alert delivery','separate apply/scheduler permission'],
            rollback='pause scheduler and writers; preserve claims and audit; use purge-aware image only',
            alerts=['nonzero exit','missing result','blocked subject','recoverable pending overdue','no successful check >26h']),
        observed_synthetic_evidence=dict(source='28c350e8e38a6de49924b5a82cbe146b80e5c14d',passed=12,
            pending=['natural seven-day soft expiry','live crash/resume','live late replacement','live hold/audit failure'],
            not_production_acceptance=True),
        unchanged=['MMH','Demo','Lite candidate','all existing buckets/IAM','scheduler','store distribution'])

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True);a=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    def git(*args):return subprocess.check_output(['git','-C',str(root),*args],text=True).strip()
    if git('status','--porcelain'):raise SystemExit('clean committed source required')
    names=git('ls-files','Backend/LiteReview','tools/plan_lite_review_rollout.py','engineering/phase2/test_lite_review_portal.py',
        'engineering/contracts/lite-review-portal-v1.md','engineering/contracts/medical-consent-operations-acceptance-v1.md',
        'Backend/Flask/medical_consent_order.py','Backend/Flask/medical_consent_migration.py',
        'Backend/Flask/medical_consent_maintenance.py','Backend/Flask/medical_media_cleanup.py','Backend/Flask/store.py',
        'tools/run_medical_consent_maintenance.py','tools/build_lite_review_catalogue.py').splitlines()
    value=plan(git('rev-parse','HEAD'),{n:hashlib.sha256((root/n).read_bytes()).hexdigest() for n in names})
    data=(json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode()
    fd=os.open(a.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as f:f.write(data)
    print(json.dumps(dict(path=str(Path(a.output).resolve()),sha256=hashlib.sha256(data).hexdigest(),execution_authorized=False)))

if __name__=='__main__':main()
