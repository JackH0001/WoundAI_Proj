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
from lite_reconsent import Reconsent, ReconsentRejected, epoch

PREFIX = '/api/v1/lite/'
CHALLENGE_PATH = PREFIX + 'attest/challenge'
REGISTER_PATH = PREFIX + 'attest/register'
REJOIN_PATH = PREFIX+'research/rejoin'
POST_PATHS = {PREFIX+'segment', PREFIX+'annotation', PREFIX+'annotation/revision', REJOIN_PATH}
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
    # Gunicorn marks the stream terminated even for Content-Length: 0.
    # Werkzeug's zero limit then raises 413 before reading an empty DELETE.
    # Allow reading one byte, while keeping the exact zero-body checks below.
    request.max_content_length = max(1, maximum)
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


def install_lite_attest(app, *, service=None, budget=None, privacy=None, commerce=None):
    if 'lite_attest_http' in app.extensions:
        raise ValueError('Lite security gate already installed')
    if service is not None and not isinstance(service, RegistrationService):
        raise ValueError('trusted registration service required')
    if budget is not None and not isinstance(budget, RequestBudget):
        raise ValueError('durable request budget required')
    if privacy is not None and not isinstance(privacy, (PrivacyState, FencedPrivacy)):
        raise ValueError('durable privacy coordinator required')
    service_paths = frozenset()
    # The deployed legacy image deliberately excludes commerce and its Apple
    # subscription dependencies. Only explicit trusted injection may load them.
    if commerce is not None:
        from lite_service_operations import ServiceOperations
        from lite_service_http import SERVICE_PATHS, blueprint as commerce_blueprint
        if not isinstance(commerce, ServiceOperations):
            raise ValueError('trusted service operations required')
        service_paths = frozenset(SERVICE_PATHS)
    app.extensions['lite_attest_http'] = dict(service=service, budget=budget, privacy=privacy, commerce=commerce)
    if commerce is not None:app.register_blueprint(commerce_blueprint(commerce))
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
            service_only = (request.path in service_paths and configuration['commerce'] is not None
                            and request.endpoint in {'lite_service_http.prepare','lite_service_http.run','lite_service_http.status','lite_service_http.recover'})
            if not ((request.method == 'POST' and (control or request.path in POST_PATHS or service_only)) or
                    (request.method == 'DELETE' and re.fullmatch(PREFIX+r'data/[A-Za-z0-9_-]{1,128}(?:/[0-9a-f]{32})?', request.path))):
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
            limit = 0 if request.method == 'DELETE' else 4096 if request.path == REJOIN_PATH or (service_only and not request.path.endswith('/run')) else 96*1024 if request.path == REGISTER_PATH else MAX_BODY
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
                parts = request.path.removeprefix(PREFIX+'data/').split('/')
                owner, requested_epoch = parts[0], parts[1] if len(parts) == 2 else 'legacy'
            elif request.path == PREFIX+'segment' or (service_only and request.path.endswith('/run')):
                if request.mimetype != 'multipart/form-data':
                    raise AdmissionRejected('multipart required')
                owners = request.form.getlist('anon_id')
                if len(owners) != 1:
                    raise AdmissionRejected('exactly one owner required')
                owner = owners[0]
                epochs = request.form.getlist('consent_epoch')
                if len(epochs) > 1: raise ReconsentRejected('ambiguous epoch')
                requested_epoch = epochs[0] if epochs else 'legacy'
            else:
                data = _json(raw)
                owner = data.get('anon_id')
                requested_epoch = data.get('consent_epoch', 'legacy')
            if owner != admitted.installation:
                return jsonify(error='lite_owner_mismatch'), 403
            g.lite_admission = admitted
            if service_only:
                # Explicitly registered, authenticated service work has no research
                # writer ticket and cannot reach research media through this route.
                # The service account/usage coordinator separately authorizes work.
                return None
            if request.path == REJOIN_PATH:
                if not isinstance(privacy, FencedPrivacy): raise ReconsentRejected('renewal unavailable')
                return jsonify(Reconsent(privacy).resume(owner, data))
            if isinstance(privacy, FencedPrivacy):
                if request.method == 'DELETE':
                    # A delayed deletion targets only the signed epoch, never
                    # the latest participation. This protects renewed data.
                    privacy = privacy.scoped(epoch(requested_epoch))
                else:
                    privacy = Reconsent(privacy).resolve(owner, requested_epoch)
            elif requested_epoch != 'legacy':
                raise ReconsentRejected('renewal unavailable')
            g.lite_privacy = privacy
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
        except ReconsentRejected:
            return jsonify(error='lite_consent_epoch_conflict'), 409
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
                if data.get('purpose') == 'recover_register':
                    if set(data) != {'key_id', 'purpose', 'previous_challenge_id'}:
                        raise AdmissionRejected('invalid recovery request')
                    challenge = service.recover_challenge(_b64(data['key_id'], 32, 32), data['previous_challenge_id'])
                    return jsonify(challenge_id=challenge.identifier, challenge=base64.b64encode(challenge.nonce).decode('ascii'),
                                   expires_at=challenge.expires_at, audience=service.admission.audience,
                                   replaces_challenge_id=data['previous_challenge_id'])
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
            getattr(g, 'lite_privacy', current_app.extensions['lite_attest_http']['privacy']).finish_write(ticket)
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
            scoped = getattr(g, 'lite_privacy', None)
            if isinstance(scoped, FencedPrivacy) and response.is_json:
                payload = response.get_json(silent=True)
                if isinstance(payload, dict):
                    payload['consent_epoch'] = scoped.manifest.epoch
                    response.set_data(current_app.json.dumps(payload))
            # Persisted clients can distinguish backpressure from bad data.
            # Keep the exact response/error (503 can mean unknown write outcome).
            if response.status_code in (429, 503):
                response.headers.setdefault('Retry-After', '5')
                payload = response.get_json(silent=True)
                if isinstance(payload, dict):
                    payload.setdefault('retry_after', int(response.headers['Retry-After']))
                    payload.setdefault('retryable', True)
                    payload.setdefault('message', '服務忙碌或暫時無法確認保存，請保留待傳資料，等待後核對同一筆送件；請勿重新拍攝或重複建立送件。')
                    response.set_data(current_app.json.dumps(payload))
            response.headers['Cache-Control'] = 'no-store'
        return response
