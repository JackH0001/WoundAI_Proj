"""Exact isolated review deployment; does not relax MMH or formal audit scopes."""

MODE = 'synthetic-review-validation'
ENVIRONMENT = 'synthetic-review'
BUCKETS = tuple('woundai-mmh-review-%s-421209514056' % name
                for name in ('media', 'security', 'audit'))
EXPECTED = {
    'WOUNDAI_DEPLOYMENT_ENVIRONMENT': ENVIRONMENT,
    'WOUNDAI_AUDIT_MODE': MODE,
    'WOUNDAI_STORE': 'gcs',
    'WOUNDAI_SERVICE_PROFILE': 'medical',
    'WOUNDAI_INSTITUTION_ORG': 'mmh-review-demo',
    'WOUNDAI_ENABLE_LITE_API': '0',
    'GOOGLE_CLOUD_PROJECT': 'woundai-jackh001',
    'K_SERVICE': 'woundai-mmh-review-demo',
    'WOUNDAI_GCS_PREFIX': 'flywheel',
    'WOUNDAI_GCS_BUCKET': BUCKETS[0],
    'WOUNDAI_SECURITY_BUCKET': BUCKETS[1],
    'WOUNDAI_AUDIT_BUCKET': BUCKETS[2],
}


def review_environment(environment):
    # A partial profile must never quietly fall back to another service mode.
    signals = ('WOUNDAI_DEPLOYMENT_ENVIRONMENT', 'WOUNDAI_AUDIT_MODE',
               'WOUNDAI_INSTITUTION_ORG', 'K_SERVICE', 'WOUNDAI_GCS_BUCKET',
               'WOUNDAI_SECURITY_BUCKET', 'WOUNDAI_AUDIT_BUCKET')
    requested = any(environment.get(key) == EXPECTED[key] for key in signals)
    if not requested:
        return False
    for key, value in EXPECTED.items():
        if environment.get(key) != value:
            raise PermissionError('review service requires exact ' + key)
    if any(value for key, value in environment.items() if key.startswith('WOUNDAI_DEMO_SEED_')):
        raise PermissionError('review service must not reuse the legacy demo seed')
    return True


def validate_review_scope(environment, bucket, prefix, audit_bucket, security_bucket):
    if not review_environment(environment):
        raise PermissionError('review service profile is required')
    if (bucket, security_bucket, audit_bucket) != BUCKETS or prefix != 'flywheel':
        raise PermissionError('review service requires its isolated bucket tuple')
