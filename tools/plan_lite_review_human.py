#!/usr/bin/env python3
"""Offline B1 review: owner login and an empty catalogue, never research access.

Consumes hashed, read-only A7/identity evidence; does not obtain credentials or
make cloud requests. This is a review document, not an executable deployment.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

PROJECT = 'woundai-jackh001'
NUMBER = '421209514056'
REGION = 'asia-east1'
PORTAL = 'woundai-lite-review'
READER = PORTAL + '-reader'
AUDIT = PORTAL + '-audit-' + NUMBER
IAP = f'projects/{NUMBER}/iap_web/cloud_run-{REGION}/services/{PORTAL}'
MMH_IAP = f'projects/{NUMBER}/iap_web/cloud_run-{REGION}/services/woundai-backend-mmhps20261007'
DEMO_IAP = f'projects/{NUMBER}/iap_web/cloud_run-{REGION}/services/woundai-mmh-review-demo'
APP_COMMIT = '065c3289819f905cd7581680a42e3c686678c23a'
IMAGE = 'sha256:23a38262ac509da954bc3b313ab8ffb34fade9fb39770d6c2ad7dc91b7f80d07'


def canonical(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def make_plan(commit, files, a7, boundary, now):
    require(isinstance(commit, str) and re.fullmatch('[0-9a-f]{40}', commit), 'clean fixed source required')
    require(files and all(isinstance(k, str) and isinstance(v, str) and
            re.fullmatch('[0-9a-f]{64}', v) for k, v in files.items()), 'source hashes required')
    require(a7.get('status') == 'phase A closed negative-access acceptance passed' and
            a7.get('source_commit') == APP_COMMIT and a7.get('image_digest') == IMAGE and
            a7.get('http_rejections') == 11 and a7.get('actual_identity_bucket_permission_checks') == 16 and
            a7.get('final_readonly_assertions') == 86 and a7.get('temporary_grants_removed') == 3,
            'complete matching A7 receipt required; do not retry A7')
    require(type(now) is int and boundary.get('cloud_mutations') == 0 and boundary.get('research_object_reads') == 0,
            'read-only boundary evidence required')
    at = datetime.fromisoformat(boundary['at']).timestamp()
    require(0 <= now - at <= 86400, 'boundary evidence older than 24 hours or from future')
    require('project_parent' in boundary and boundary['project_parent'] is None,
            'organization eligibility unknown or changed; re-review OAuth plan')
    settings = boundary['settings']
    require(set(settings) == {f'projects/{NUMBER}', f'projects/{NUMBER}/iap_web', IAP, MMH_IAP, DEMO_IAP} and
            set(boundary['policies']) == {IAP, MMH_IAP, DEMO_IAP}, 'complete per-environment baseline required')
    for scope in (f'projects/{NUMBER}', f'projects/{NUMBER}/iap_web', IAP):
        require(settings[scope].get('http') == 200 and settings[scope].get('has_custom_client') is False,
                'unexpected inherited or existing Lite OAuth client; stop without adoption')
    for item in list(settings.values()) + list(boundary['policies'].values()):
        require(item.get('http') == 200 and re.fullmatch('[0-9a-f]{64}', item.get('digest', '')),
                'incomplete settings/policy baseline')
    require(boundary['policies'][IAP].get('binding_count') == 0, 'existing Lite human grant needs review')
    require(boundary['access_audit_bucket'].get('http') == 404, 'audit bucket collision; never silently adopt')
    owner = boundary['owner_discovery']
    require(owner.get('http') == 200 and owner.get('email_matches_approved_owner') is True and
            owner.get('verified_email') is True and re.fullmatch('[0-9a-f]{64}', owner.get('subject_sha256', '')),
            'verified owner discovery required')
    return {
        'schema': 'lite-review-human-acceptance-plan/1', 'phase': 'B1_empty_owner_login',
        'execution_authorized': False, 'deployable': False, 'source_commit': commit, 'files': files,
        'application_commit': APP_COMMIT, 'image_digest': IMAGE,
        'project': PROJECT, 'region': REGION,
        'evidence': {'a7_sha256': digest(canonical(a7)), 'boundary_sha256': digest(canonical(boundary)),
                     'owner_subject_sha256': owner['subject_sha256'], 'boundary_at': boundary['at'],
                     'actual_iap_login_verified': False},
        'oauth': {'new_clients': 1, 'display_name': 'WoundAILite Review Internal Acceptance',
                 'type': 'web', 'settings_resource': IAP,
                 'redirect': 'https://iap.googleapis.com/v1/oauth/clientIds/<new-client-id>:handleRedirect',
                 'reuse_mmh_client': False, 'project_branding_or_test_users_change': False,
                 'shared_branding_limitation': 'existing project consent screen is WoundAI MMHPS20261007; preserve it for owner-only acceptance, not independent public branding',
                 'prerequisite': 'read existing branding/audience eligibility in Console; stop if any shared setting must change',
                 'secret_handling': 'protected file/memory only; never logs, Git or review packet'},
        'human': {'email': 'jack.hou@gmail.com', 'roles': ['lite.review.metadata'],
                  'subject': 'verified discovery in protected local file; bind hash above; never email fallback',
                  'iap_role': 'roles/iap.httpsResourceAccessor', 'iap_resource': IAP,
                  'signed_iap_assertion': 'must pass both existing validators on actual owner login'},
        'services': {'existing': [PORTAL, READER], 'new': 0, 'cloud_build': 0,
                     'application_image_changes': 0, 'max_new_configuration_revisions': 2,
                     'allUsers': False, 'allAuthenticatedUsers': False, 'iam_required': True,
                     'direct_iap_only_on': PORTAL, 'min': 0, 'max': 1, 'concurrency': 1,
                     'iap_service_agent': f'service-{NUMBER}@gcp-sa-iap.iam.gserviceaccount.com',
                     'iap_agent_invoker_scope': PORTAL, 'reader_invoker': 'existing portal SA only'},
        'configuration': {'new_secret_versions': 3, 'new_secret_resources': 0,
                          'items': ['portal config: owner metadata role', 'reader config: same role and catalogue digest',
                                    'catalogue: records=[] and execution timestamp'],
                          'numeric_version_pins': True, 'latest_forbidden': True, 'reuse_cursor_key': True,
                          'catalogue_records': [], 'catalogue_valid_seconds': 86400,
                          'refresh_job': False, 'changed_catalogue_payload_requires_new_review': True},
        'audit': {'new_bucket': AUDIT, 'location': REGION, 'uniform_access': True,
                  'public_access_prevention': 'enforced', 'soft_delete_seconds': 604800,
                  'retention_lock': False, 'lifecycle_rules': [], 'physical_erasure_claim': False,
                  'runtime': READER + '@' + PROJECT + '.iam.gserviceaccount.com',
                  'new_custom_role': f'projects/{PROJECT}/roles/liteReviewAuditCreate',
                  'runtime_permissions': ['storage.objects.create'],
                  'runtime_prefix': 'lite_review_access/v1/', 'prefix_enforcement': 'application; IAM grants create only in the dedicated new bucket',
                  'if_generation_match': 0,
                  'operator_readback': 'only this new audit bucket; list/get receipt metadata and bytes, never delete'},
        'source_storage': {'new_grants': [], 'list': False, 'get': False, 'write': False, 'delete': False,
                           'existing_lite_mmh_demo_buckets_unchanged': True},
        'limits': {'single_attempt': True, 'operator_http_probes': 60, 'owner_page_views': 5,
                   'acceptance_minutes': 30, 'temporary_grant_minutes': 55,
                   'normal_cloud_costs': True, 'automatic_retry': False},
        'acceptance': ['owner browser login -> empty page via real signed IAP and portal service token',
                       'create-only audit receipt matches owner hash, action=list and records=[]',
                       'missing/foreign/forged identity still denied; no media capability',
                       'portal and reader have no effective source storage permissions',
                       'audit failure blocks response; execute locally, live injection separately reviewed',
                       'compare protected service, project/MMH/Demo IAP and bucket policies with baseline'],
        'containment': ['on completion, failure or user delay, close human entry and restore closed role configurations',
                        'remove only new IAP human/agent grants and audit write/readback grants; keep A7 grants',
                        'preserve OAuth client, all Secret versions, audit bucket and all receipts; no deletion',
                        'an expired old catalogue may return 503: rollback promises closed access, not a working review page',
                        'verify effective denial; if network fails, prioritize removal on reconnect; no automatic rerun'],
        'pending_separate_scopes': {
            'B2': 'finite synthetic fixture, exact media/state GET and bounded fixture writes; positive media/withdrawal/reconsent/race evidence',
            'C': 'MMH/Demo additive medical index migration only after exact source manifest and writer drain; never Lite epochs',
            'D': 'cleanup dry-run, live failures/natural expiry and approved retention before bounded deletion or scheduler'},
        'execution_gates': ['new explicit authorization of fixed plan', 'exact-source CI passes',
                            'fresh baseline and protected subject hash match',
                            'fresh custom-role absence check; no silent adoption',
                            'service-scoped OAuth Console preflight without shared settings change',
                            'reviewed bounded executor and rollback rehearsal'],
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--a7', type=Path, required=True)
    p.add_argument('--boundary', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    root = Path(__file__).resolve().parents[1]
    def git(*args):
        return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()
    require(not git('status', '--porcelain'), 'clean committed source required')
    require(not git('diff', APP_COMMIT, '--', 'Backend/LiteReview', ':(exclude)Backend/LiteReview/README.md'),
            'runtime differs from retained image; new build review needed')
    paths = git('ls-files', 'tools/plan_lite_review_human.py', 'tools/test_plan_lite_review_human.py',
                'engineering/phase2/test_lite_review_human.py', 'Backend/LiteReview',
                'docs/claude_lite_review_human_20261025.md', '.github/workflows/lite-review.yml').splitlines()
    # Only documentation may differ from the retained image's application tree.
    result = make_plan(git('rev-parse', 'HEAD'), {n: digest((root / n).read_bytes()) for n in paths},
                       json.loads(a.a7.read_bytes()), json.loads(a.boundary.read_bytes()),
                       int(datetime.now(timezone.utc).timestamp()))
    raw = canonical(result)
    fd = os.open(a.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
    print(json.dumps({'path': str(a.output.resolve()), 'sha256': digest(raw),
                      'execution_authorized': False, 'cloud_mutations': 0}))


if __name__ == '__main__':
    main()
