"""First-install App Attest enrollment for one owner key, ahead of account review."""
import base64
import hashlib
import hmac
import os
import re
import time

from flask import Flask, jsonify, request
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge

from audit_chain_contract import loads_json_object_strict
from lite_attest_assertion import AssertionPolicy, AssertionRejected
from lite_attest_budget import BudgetExceeded, RequestBudget
from lite_attest_enrollment import RegistrationService
from lite_attest_http import CHALLENGE_PATH, REGISTER_PATH, _b64, _body, _budget_limit_response, _json
from lite_attest_state import AdmissionRejected, GCSStateStore, StateUnavailable
from lite_bucket_policy import validate_bucket_policy
from lite_native_schema import exact, text
from lite_run_identity import verify_attached_identity

AUDIENCE = 'woundlite-native-sandbox-v1'


def checked_config(raw, plan_raw, *, config_sha, plan_sha, source, image):
    for data, pin in ((raw, config_sha), (plan_raw, plan_sha)):
        if hashlib.sha256(data).hexdigest() != text(pin, r'[0-9a-f]{64}'):
            raise ValueError('owner enrollment configuration differs')
    native = loads_json_object_strict(raw.decode())
    plan = exact(loads_json_object_strict(plan_raw.decode()),
                 'schema native_sha256 key_id service_account starts until')
    dep = native['deployment']
    number = text(dep['project_number'], r'[1-9][0-9]{5,19}')
    project = text(dep['project'], r'[a-z][a-z0-9-]{4,28}[a-z0-9]')
    bucket = 'woundai-lite-native-state-' + number
    loc = exact(native['stores']['security'], 'bucket prefix')
    prefix = text(loc['prefix'], r'lite-native/[a-z0-9_-]{1,64}/security')
    budget_prefix = prefix.rsplit('/', 1)[0] + '/owner-enrollment-budget'
    for name, other in native['stores'].items():
        if name == 'security' or other['bucket'] != bucket:
            continue
        other_prefix = text(other['prefix'], r'lite-native/[a-z0-9_-]{1,64}/[a-z0-9_-]{1,64}')
        if other_prefix in (prefix, budget_prefix):
            raise ValueError('enrollment storage overlaps another lane')
    if (plan['schema'] != 'lite-owner-enrollment/1' or plan['native_sha256'] != config_sha
            or native['schema'] not in ('lite-native-startup/2', 'lite-native-startup/3')
            or native['enabled'] is not True or native['apple']['environment'] != 'Sandbox'
            or dep['source'] != text(source, r'[0-9a-f]{40}') or dep['image'] != text(image, r'[0-9a-f]{64}')
            or loc['bucket'] != bucket
            or native['app']['bundle'] != 'com.woundai.lite' or native['app']['attest_audience'] != AUDIENCE):
        raise ValueError('owner enrollment native binding differs')
    text(plan['key_id'], r'[0-9a-f]{64}')
    text(plan['service_account'], r'[a-z][a-z0-9-]{4,28}[a-z0-9]@'+re.escape(project)+r'\.iam\.gserviceaccount\.com')
    if plan['service_account'] == dep['service_account']:
        raise ValueError('dedicated enrollment service identity required')
    start, end = plan['starts'], plan['until']
    if (type(start) is not int or type(end) is not int or not 0 <= start < end <= start + 900
            or start // 86400 != (end - 1) // 86400):
        raise ValueError('bounded same-day enrollment window required')
    team = text(native['app']['team'], r'[A-Z0-9]{10}')
    version = text(native['app']['version'], r'[1-9][0-9]{0,9}')
    generation = text(native['buckets'][bucket]['metageneration'], r'[1-9][0-9]*')
    return plan, dict(project=project, project_number=number, service_account=plan['service_account']), (
        bucket, generation, prefix, budget_prefix), AssertionPolicy(team+'.com.woundai.lite', frozenset({2,4}), frozenset({version}))


def cloud_stores(deployment, location):
    from google.auth.compute_engine import Credentials
    from google.cloud.storage import Client
    if os.environ.get('STORAGE_EMULATOR_HOST') or os.environ.get('GOOGLE_CLOUD_API_ENDPOINT_OVERRIDES_STORAGE'):
        raise ValueError('fixed Cloud Storage endpoint required')
    verify_attached_identity(deployment)
    name, generation, security, budget = location
    client = Client(project=deployment['project'], credentials=Credentials(service_account_email=deployment['service_account']),
                    client_options={'api_endpoint': 'https://storage.googleapis.com'})
    bucket = client.bucket(name)
    bucket.reload(timeout=10, retry=None)
    checked = validate_bucket_policy(bucket._properties, name=name, project_number=deployment['project_number'],
                                     role='security', location='ASIA-EAST1')
    if checked['metageneration'] != generation:
        raise ValueError('enrollment bucket has changed')
    return GCSStateStore(bucket, prefix=security), GCSStateStore(bucket, prefix=budget)


def build(raw, plan_raw, *, config_sha, plan_sha, source, image, stores=cloud_stores, clock=lambda: int(time.time())):
    plan, deployment, location, policy = checked_config(raw, plan_raw, config_sha=config_sha,
        plan_sha=plan_sha, source=source, image=image)
    start, end = plan['starts'], plan['until']

    def active_time():
        now = clock()
        if type(now) is not int or not start <= now < end:
            raise StateUnavailable('outside enrollment window')
        return now

    if clock() >= end:
        raise ValueError('expired enrollment revision')
    security, counters = stores(deployment, location)
    service = RegistrationService(security, policy=policy, audience=AUDIENCE, environment='production', clock=active_time)
    budget = RequestBudget(counters, minute_limit=6, day_limit=12, clock=active_time)
    pinned_key = bytes.fromhex(plan['key_id'])
    app = Flask(__name__, static_folder=None)

    @app.post(CHALLENGE_PATH)
    @app.post(REGISTER_PATH)
    def enroll():
        try:
            active_time()
            target = request.environ.get('RAW_URI', request.environ.get('REQUEST_URI', request.path))
            if (target != request.path or request.query_string or request.mimetype != 'application/json'
                    or request.headers.get('Content-Encoding', 'identity') != 'identity'):
                raise AdmissionRejected('canonical enrollment request required')
            challenge = request.path == CHALLENGE_PATH
            data = _json(_body(4096 if challenge else 96*1024))
            exact(data, 'key_id purpose' if challenge else 'key_id challenge_id attestation')
            key = _b64(data['key_id'], 32, 32)
            if not hmac.compare_digest(key, pinned_key) or (challenge and data['purpose'] != 'register'):
                return jsonify(error='lite_owner_enrollment_closed'), 403
            budget.reserve()
            if challenge:
                c = service.issue_challenge(key)
                result = dict(challenge_id=c.identifier, challenge=base64.b64encode(c.nonce).decode('ascii'),
                              expires_at=c.expires_at, audience=AUDIENCE)
            else:
                record = service.register(key, data['challenge_id'], _b64(data['attestation'], 65536))
                result = dict(installation=record.installation, key_id=data['key_id'])
            active_time()
            return jsonify(result)
        except BudgetExceeded as exc:
            return _budget_limit_response(exc)
        except RequestEntityTooLarge:
            return jsonify(error='lite_body_too_large'), 413
        except (AdmissionRejected, AssertionRejected, BadRequest, ValueError):
            return jsonify(error='lite_attestation_rejected'), 401
        except StateUnavailable:
            return jsonify(error='lite_security_unavailable'), 503

    @app.after_request
    def no_cache(response):
        response.headers['Cache-Control'] = 'no-store'
        return response
    return app
