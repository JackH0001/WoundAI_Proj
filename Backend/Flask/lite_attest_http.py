"""Fail-closed Lite HTTP gate. Install before registering business blueprints.

Trusted startup supplies RegistrationService and RequestBudget objects. Missing
configuration blocks Lite, never falls back to anonymous access. Request bodies
remain exactly the bytes signed; only verified ownership reaches business code.
"""
import base64
import re
from flask import Blueprint, current_app, g, jsonify, request
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge
from audit_chain_contract import loads_json_object_strict
from lite_attest_assertion import AssertionRejected
from lite_attest_enrollment import RegistrationService
from lite_attest_budget import RequestBudget, BudgetExceeded
from lite_attest_state import AdmissionRejected, ChallengeLimit, StateUnavailable
from lite_privacy_state import PrivacyState, OwnerWithdrawn, WriterLimit
from lite_fenced_store import FencedPrivacy

PREFIX = '/api/v1/lite/'
CHALLENGE_PATH = PREFIX + 'attest/challenge'
REGISTER_PATH = PREFIX + 'attest/register'
POST_PATHS = {PREFIX+'segment', PREFIX+'annotation', PREFIX+'annotation/revision'}
MAX_BODY = 32 * 1024 * 1024


def _b64(value, maximum, exact=None):
    if type(value) is not str or len(value) > ((maximum + 2) // 3) * 4:
        raise AdmissionRejected('invalid base64 size')
    try:
        raw = base64.b64decode(value, validate=True)
    except ValueError as exc:
        raise AdmissionRejected('invalid base64') from exc
    if (not 1 <= len(raw) <= maximum or (exact is not None and len(raw) != exact) or
            base64.b64encode(raw).decode('ascii') != value):
        raise AdmissionRejected('noncanonical base64')
    return raw


def _body(maximum):
    if request.content_length is None and request.method != 'DELETE':
        raise AdmissionRejected('Content-Length required')
    request.max_content_length = 1 if request.content_length is None else maximum
    request.max_form_memory_size = maximum
    request.max_form_parts = 64
    if request.content_length is not None and request.content_length > maximum:
        raise RequestEntityTooLarge()
    raw = request.get_data(cache=True, parse_form_data=False)
    if (request.content_length is None and raw) or (request.content_length is not None and len(raw) != request.content_length):
        raise AdmissionRejected('incomplete body')
    return raw


def _json(raw):
    if request.mimetype != 'application/json':
        raise AdmissionRejected('JSON required')
    try:
        return loads_json_object_strict(raw.decode('utf-8'))
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise AdmissionRejected('invalid JSON') from exc


def install_lite_attest(app, *, service=None, budget=None, privacy=None):
    if 'lite_attest_http' in app.extensions:
        raise ValueError('Lite security gate already installed')
    if service is not None and not isinstance(service, RegistrationService):
        raise ValueError('trusted registration service required')
    if budget is not None and not isinstance(budget, RequestBudget):
        raise ValueError('durable request budget required')
    if privacy is not None and not isinstance(privacy, (PrivacyState, FencedPrivacy)):
        raise ValueError('durable privacy coordinator required')
    app.extensions['lite_attest_http'] = dict(service=service, budget=budget, privacy=privacy)
    bp = Blueprint('lite_attest_http', __name__)

    @app.before_request
    def guard():
        if not request.path.startswith(PREFIX):
            return None
        try:
            # The console's three reviewer reads use existing JWT + audit.read,
            # not a consumer device key. Check that permission here as well.
            if request.method == 'GET' and request.endpoint in {
                    'lite.lite_records', 'lite.lite_record_image', 'lite.lite_record_preview'}:
                from api_lite import _lite_reviewer_ok
                allowed, error = _lite_reviewer_ok()
                return None if allowed else error
            configuration = current_app.extensions['lite_attest_http']
            service, budget, privacy = configuration['service'], configuration['budget'], configuration['privacy']
            if (not isinstance(service, RegistrationService) or not isinstance(budget, RequestBudget)
                    or not isinstance(privacy, (PrivacyState, FencedPrivacy))):
                raise StateUnavailable('Lite device verification is not configured')
            # Reject alternate encodings before matching Flask's decoded path.
            raw_uri = request.environ.get('RAW_URI', request.environ.get('REQUEST_URI', request.path))
            if (request.query_string or '%' in raw_uri or '\\' in raw_uri or
                    request.headers.get('Content-Encoding', 'identity') != 'identity'):
                raise AdmissionRejected('noncanonical request target or encoding')
            control = request.path in (CHALLENGE_PATH, REGISTER_PATH)
            if not ((request.method == 'POST' and (control or request.path in POST_PATHS)) or
                    (request.method == 'DELETE' and re.fullmatch(PREFIX+r'data/[A-Za-z0-9_-]{1,128}', request.path))):
                return jsonify(error='lite_route_not_supported'), 404
            # A bounded 4 KiB challenge body selects the privacy lane. Media
            # bodies remain unread until their general capacity is reserved.
            if request.path == CHALLENGE_PATH:
                try:
                    data = _json(_body(4096))
                except (AdmissionRejected, RequestEntityTooLarge, BadRequest):
                    budget.reserve()
                    raise
                withdrawal = set(data) == {'key_id', 'purpose'} and data.get('purpose') == 'withdraw'
                budget.reserve(scope='withdraw' if withdrawal else 'general')
                g.lite_attest_control = data
                return None
            budget.reserve(scope='withdraw' if request.method == 'DELETE' else 'general')
            limit = 0 if request.method == 'DELETE' else 96*1024 if request.path == REGISTER_PATH else MAX_BODY
            raw = _body(limit)
            if control:
                g.lite_attest_control = _json(raw)
                return None
            key_id = _b64(request.headers.get('X-Lite-Key-ID'), 32, 32)
            assertion = _b64(request.headers.get('X-Lite-Assertion'), 4096)
            admitted = service.admission.admit(key_id, request.headers.get('X-Lite-Challenge-ID'), assertion,
                request_id=request.headers.get('X-Lite-Request-ID'), method=request.method, path=request.path,
                content_type=request.headers.get('Content-Type', ''), body=raw)
            if request.method == 'DELETE':
                # client_data also binds the DELETE path to this installation.
                owner = request.path.removeprefix(PREFIX+'data/')
            elif request.path == PREFIX+'segment':
                if request.mimetype != 'multipart/form-data':
                    raise AdmissionRejected('multipart required')
                owners = request.form.getlist('anon_id')
                if len(owners) != 1:
                    raise AdmissionRejected('exactly one owner required')
                owner = owners[0]
            else:
                owner = _json(raw).get('anon_id')
            if owner != admitted.installation:
                return jsonify(error='lite_owner_mismatch'), 403
            g.lite_admission = admitted
            if request.method == 'DELETE':
                if privacy.withdraw(owner):
                    response = jsonify(status='withdrawal_pending', anon_id=owner, reason='writers_in_flight')
                    response.headers['Retry-After'] = '5'
                    return response, 202
                if isinstance(privacy, FencedPrivacy):
                    request.environ['woundlite.write_fence'] = 'gcs-generation-v1'
                else:
                    request.environ['woundlite.writers_drained'] = True
            else:
                request.environ['woundlite.writer_ticket'] = privacy.begin_write(owner)
        except (RequestEntityTooLarge, BadRequest) as exc:
            return jsonify(error='lite_request_too_large' if isinstance(exc, RequestEntityTooLarge) else 'lite_bad_request'), exc.code
        except BudgetExceeded as exc:
            response = jsonify(error='lite_service_budget', retry_after=exc.retry_after)
            response.headers['Retry-After'] = str(exc.retry_after)
            return response, 429
        except OwnerWithdrawn:
            return jsonify(error='withdrawn'), 410
        except WriterLimit:
            return jsonify(error='lite_writers_busy'), 429
        except ChallengeLimit:
            return jsonify(error='lite_challenge_limit'), 429
        except (AdmissionRejected, AssertionRejected, ValueError):
            return jsonify(error='lite_attestation_required'), 401
        except StateUnavailable:
            return jsonify(error='lite_security_unavailable'), 503

    def control_response(operation):
        try:
            data = g.lite_attest_control
            service = current_app.extensions['lite_attest_http']['service']
            if operation == 'challenge':
                if set(data) != {'key_id', 'purpose'} or data['purpose'] not in ('register', 'assert', 'withdraw'):
                    raise AdmissionRejected('invalid challenge request')
                key = _b64(data['key_id'], 32, 32)
                challenge = (service.issue_challenge(key) if data['purpose'] == 'register'
                             else service.admission.issue_challenge(key, purpose=data['purpose']))
                return jsonify(challenge_id=challenge.identifier, challenge=base64.b64encode(challenge.nonce).decode('ascii'),
                               expires_at=challenge.expires_at, audience=service.admission.audience)
            if set(data) != {'key_id', 'challenge_id', 'attestation'}:
                raise AdmissionRejected('invalid registration request')
            result = service.register(_b64(data['key_id'], 32, 32), data['challenge_id'], _b64(data['attestation'], 65536))
            return jsonify(installation=result.installation, key_id=base64.b64encode(result.key_id).decode('ascii'))
        except ChallengeLimit:
            return jsonify(error='lite_challenge_limit'), 429
        except (AdmissionRejected, AssertionRejected, ValueError):
            return jsonify(error='lite_attestation_rejected'), 401
        except StateUnavailable:
            return jsonify(error='lite_security_unavailable'), 503

    bp.add_url_rule(CHALLENGE_PATH, 'challenge', lambda:control_response('challenge'), methods=['POST'])
    bp.add_url_rule(REGISTER_PATH, 'register', lambda:control_response('register'), methods=['POST'])
    app.register_blueprint(bp)

    def release_writer():
        ticket = request.environ.get('woundlite.writer_ticket')
        if ticket is not None:
            current_app.extensions['lite_attest_http']['privacy'].finish_write(ticket)
            del request.environ['woundlite.writer_ticket']

    @app.teardown_request
    def release_after_exception(error):
        # Also runs when Flask propagates an exception without after_request.
        # A failure leaves the durable ticket pending; never expire it by time.
        try:
            release_writer()
        except Exception:
            current_app.logger.error('Lite writer drain remains unconfirmed')

    @app.after_request
    def no_cache(response):
        try:
            release_writer()
        except Exception:
            response = jsonify(error='lite_writer_completion_unconfirmed')
            response.status_code = 503
        if request.path.startswith(PREFIX):
            response.headers['Cache-Control'] = 'no-store'
        return response
