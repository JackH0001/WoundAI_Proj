"""Explicit production configuration; no cloud client before complete validation."""
import re
from google.cloud import storage
from lite_attest_assertion import AssertionPolicy
from lite_attest_enrollment import RegistrationService
from lite_attest_state import GCSStateStore
from lite_attest_budget import RequestBudget
from lite_privacy_state import PrivacyState
from lite_fenced_store import FencedPrivacy
from store import _refuse_cloud_in_test_process
from lite_bucket_policy import validate_bucket_policy


def build_lite_security(environment):
    from lite_service_profile import service_profile
    profile = service_profile(environment)
    def setting(name, pattern):
        value = environment.get(name)
        if type(value) is not str or re.fullmatch(pattern, value) is None:
            raise ValueError('invalid or missing ' + name)
        return value
    app_id = setting('WOUNDAI_LITE_ATTEST_APP_ID', r'[A-Z0-9]{10}\.[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+')
    versions = setting('WOUNDAI_LITE_ATTEST_VERSIONS', r'[0-9]{1,10}(?:\.[0-9]{1,10}){0,2}(?:,[0-9]{1,10}(?:\.[0-9]{1,10}){0,2}){0,15}')
    if len(set(versions.split(','))) != len(versions.split(',')):
        raise ValueError('duplicate bundle version policy')
    audience = setting('WOUNDAI_LITE_ATTEST_AUDIENCE', r'[a-z0-9-]{1,64}')
    project = setting('WOUNDAI_LITE_SECURITY_PROJECT', r'[a-z][a-z0-9-]{4,28}[a-z0-9]')
    project_number = setting('WOUNDAI_LITE_SECURITY_PROJECT_NUMBER', r'[1-9][0-9]{5,19}')
    bucket_name = setting('WOUNDAI_LITE_SECURITY_BUCKET', r'[a-z0-9][a-z0-9-]{1,61}[a-z0-9]')
    if bucket_name in (environment.get('WOUNDAI_GCS_BUCKET'), environment.get('WOUNDAI_AUDIT_BUCKET')):
        raise ValueError('dedicated mutable Lite security bucket required')
    minute = int(setting('WOUNDAI_LITE_BUDGET_MINUTE', r'[1-9][0-9]{0,5}'))
    day = int(setting('WOUNDAI_LITE_BUDGET_DAY', r'[1-9][0-9]{0,7}'))
    if minute > day:
        raise ValueError('minute budget exceeds daily budget')
    policy = AssertionPolicy(app_id, frozenset({2, 4}), frozenset(versions.split(',')))
    # Public server accepts production App Attest only. Development must remain
    # an explicitly isolated test harness, not a client-selectable downgrade.
    _refuse_cloud_in_test_process()
    client = storage.Client(project=project)
    bucket = client.bucket(bucket_name)
    bucket.reload(timeout=10, retry=None)
    validate_bucket_policy(bucket._properties, name=bucket_name, project_number=project_number, role='security',
                           location='ASIA-EAST1' if profile == 'lite' else None)
    if profile == 'lite':
        media_name = environment['WOUNDAI_GCS_BUCKET']
        media = client.bucket(media_name)
        media.reload(timeout=10, retry=None)
        validate_bucket_policy(media._properties, name=media_name, project_number=project_number, role='media', location='ASIA-EAST1')
        # Never silently switch an existing legacy dataset to a namespace whose
        # withdrawal receipt cannot cover it. Old rate-only ledgers are separate.
        for legacy in ('lite/', 'lite_raw/', 'lite_index.jsonl/', 'lite_labels.jsonl/'):
            old_prefix = environment['WOUNDAI_GCS_PREFIX'] + '/' + legacy
            if list(media.list_blobs(prefix=old_prefix, max_results=1, timeout=10, retry=None)):
                raise ValueError('legacy Lite research data requires explicit migration')

    prefix = 'lite_security/v1/' + audience
    state = GCSStateStore(bucket, prefix=prefix+'/keys')
    budget_state = GCSStateStore(bucket, prefix=prefix+'/budget')
    privacy_state = GCSStateStore(bucket, prefix=prefix+('/privacy-fenced' if profile == 'lite' else '/privacy'))
    privacy = FencedPrivacy(privacy_state, media) if profile == 'lite' else PrivacyState(privacy_state)
    return (RegistrationService(state, policy=policy, audience=audience, environment='production'),
            RequestBudget(budget_state, minute_limit=minute, day_limit=day), privacy)
