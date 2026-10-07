"""Explicit public Lite profile: no clinical bootstrap or public admin routes.

The route fence is installed before any other request hook, and rejects before
body parsing, JWT handling, storage or inference. It does not replace App Attest.
"""
import os
import re
from flask import jsonify, request

POST_ROUTES = frozenset({
    '/api/v1/lite/attest/challenge', '/api/v1/lite/attest/register',
    '/api/v1/lite/segment', '/api/v1/lite/annotation', '/api/v1/lite/annotation/revision',
})


def service_profile(environment):
    value = environment.get('WOUNDAI_SERVICE_PROFILE', 'medical')
    if value not in ('medical', 'lite'):
        raise ValueError('unknown WoundAI service profile')
    if value == 'lite':
        validate_lite_environment(environment)
    return value


def validate_lite_environment(environment):
    if environment.get("LITE_FACE_REJECT", "1") != "1":
        raise ValueError("Lite service must enable image privacy checks")
    for key, expected in [('WOUNDAI_ENABLE_LITE_API', '1'), ('WOUNDAI_STORE', 'gcs'),
                          ('WOUNDAI_GCS_PREFIX', 'lite-public-v1')]:
        if environment.get(key) != expected:
            raise ValueError('invalid or missing ' + key + ' for Lite service')
    media, security = environment.get('WOUNDAI_GCS_BUCKET'), environment.get('WOUNDAI_LITE_SECURITY_BUCKET')
    for value in (media, security):
        if type(value) is not str or re.fullmatch(r'[a-z0-9][a-z0-9-]{1,61}[a-z0-9]', value) is None:
            raise ValueError('Lite service requires explicit bucket identities')
    if media == security:
        raise ValueError('Lite media and security buckets must be distinct')
    runtime = environment.get('WOUNDAI_RUNTIME_DIR')
    if type(runtime) is not str or not os.path.isabs(runtime) or os.path.dirname(runtime) == runtime:
        raise ValueError('Lite service requires a dedicated absolute runtime directory')
    salt = environment.get('LITE_IP_SALT')
    if type(salt) is not str or len(salt) < 32 or salt.strip() != salt:
        raise ValueError('Lite service requires its own IP hashing secret')
    for key, value in environment.items():
        if value and (key in ('ADMIN_PASSWORD', 'JWT_SECRET_KEY', 'FLASK_SECRET_KEY',
                              'CARE_RECEIPT_SECRET', 'WOUNDAI_AUDIT_BUCKET') or
                      key.startswith('WOUNDAI_DEMO_SEED_')):
            raise ValueError('clinical/demo configuration is forbidden in Lite service: ' + key)


def install_lite_perimeter(app):
    if app.before_request_funcs.get(None):
        raise ValueError('Lite perimeter must be the first request hook')

    @app.before_request
    def lite_surface():
        path, method = request.path, request.method
        raw = request.environ.get('RAW_URI', request.environ.get('REQUEST_URI', path))
        canonical = (not request.query_string and not request.script_root and raw == path
                     and '%' not in path and '\\' not in path)
        allowed = ((path == '/api/health' and method in ('GET', 'HEAD')) or
                   (path in POST_ROUTES and method == 'POST') or
                   (method == 'DELETE' and re.fullmatch(r'/api/v1/lite/data/[0-9a-f]{32}', path)))
        if not canonical or not allowed:
            return jsonify(error='lite_route_not_supported'), 404

    @app.after_request
    def no_cache(response):
        response.headers['Cache-Control'] = 'no-store'
        return response


def health_response(app, *, model_ready):
    from lite_attest_enrollment import RegistrationService
    from lite_attest_budget import RequestBudget
    from lite_privacy_state import PrivacyState
    from lite_fenced_store import FencedPrivacy
    state = app.extensions.get('lite_attest_http', {})
    configured = (isinstance(state.get('service'), RegistrationService) and
                  isinstance(state.get('budget'), RequestBudget) and
                  isinstance(state.get('privacy'), (PrivacyState, FencedPrivacy)))
    ready = bool(model_ready and configured)
    # This is local readiness, not proof of GCS availability or Apple E2E.
    return jsonify(profile='lite', status='healthy' if ready else 'degraded',
                   services=dict(segmentation_model=bool(model_ready), lite_attest_configured=configured),
                   build=dict(git_commit=os.environ.get('GIT_COMMIT'), revision=os.environ.get('K_REVISION'))), 200 if ready else 503
