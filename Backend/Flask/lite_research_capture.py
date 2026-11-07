"""Opt-in research ingestion of an existing capture, without model or billing.

The HTTP guard supplies independently attested ownership, consent epoch and a
registered writer. Every payload uses the existing withdrawal fence. An index
row is the completion marker, published only after exact media readback. This
receipt does not claim final annotation, calibration or training eligibility.
"""
import hashlib
import io
import math
import re
from datetime import datetime, timezone

from cryptography.exceptions import InvalidSignature
from flask import Blueprint, g, jsonify, request
from PIL import Image
import numpy as np

from audit_chain_contract import loads_json_object_strict
from lite_attest_budget import BudgetExceeded
from lite_attest_state import CAS_ATTEMPTS, StateUnavailable, _revision
from lite_fenced_store import FencedPrivacy
from lite_privacy_state import OwnerWithdrawn
from lite_raster_tools import _has_face
from lite_research_image_privacy import ResearchPhotoMetadata, check_research_jpeg
from lite_raw_depth_contract import MAX_JPEG_BYTES, MAX_PIXELS, validate
from lite_raw_depth_store import pack, save_capture
from lite_research_source import ResearchSourceVerifier, SourceRejected, SourceExpired, encoded, sha, hexid
from store_errors import ImmutableConflict

PATH = '/api/v1/lite/research/capture'
STATUS_PATH = PATH + '/status'


class CapturePrivacyRejected(ValueError): pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


class CaptureAdmissionBudget:
    """Bound new research captures per installation; never personal usage credit.

    One bounded current-day row per owner. Same-day retries reuse their slot;
    a persisted media intent also reuses its slot across midnight. A failed
    request before intent persistence can consume admission capacity, not money.
    """
    def __init__(self, store, *, day_limit, clock):
        if type(day_limit) is not int or not 1 <= day_limit <= 1000 or not callable(clock):
            raise ValueError('bounded trusted capture admission required')
        self.store, self.limit, self.clock = store, day_limit, clock

    def reserve(self, owner, identifier):
        hexid(owner, 32); hexid(identifier, 16)
        key = hashlib.sha256(b'lite/research-capture-budget/1\0' + owner.encode()).digest()
        for _ in range(CAS_ATTEMPTS):
            now = self.clock()
            if type(now) not in (int, float) or not math.isfinite(now) or not 0 <= now < 2**53:
                raise StateUnavailable('invalid capture clock')
            now = int(now); day = now // 86400
            row = self.store.load(key)
            revision, state = (0, dict(v=1, day=day, ids=[])) if row is None else row
            _revision(revision, creating=row is None)
            if (type(state) is not dict or set(state) != {'v', 'day', 'ids'} or state['v'] != 1
                    or type(state['day']) is not int or not 0 <= state['day'] <= day
                    or type(state['ids']) is not list or len(state['ids']) > 1000
                    or any(type(i) is not str or re.fullmatch('[0-9a-f]{16}', i) is None for i in state['ids'])
                    or len(set(state['ids'])) != len(state['ids'])):
                raise StateUnavailable('invalid capture admission state')
            if state['day'] != day: state = dict(v=1, day=day, ids=[])
            if identifier in state['ids']: return
            if len(state['ids']) >= self.limit: raise BudgetExceeded((day+1)*86400-now)
            state = dict(v=1, day=day, ids=state['ids']+[identifier])
            if self.store.compare_exchange(key, revision, state): return
        raise StateUnavailable('capture admission contention')


class ResearchCaptureReceiver:
    def __init__(self, *, verifier, budget):
        if not isinstance(verifier, ResearchSourceVerifier) or not isinstance(budget, CaptureAdmissionBudget):
            raise ValueError('pinned provenance verifier and durable budget required')
        self.verifier, self.budget = verifier, budget

    def lookup(self, privacy, owner, body):
        """Read the published receipt for an exact submission. Never start a write."""
        fields = {'anon_id', 'consent_epoch', 'consent_version', 'research_consent',
                  'source_ticket', 'image_bytes', 'bundle_sha256'}
        if (set(body) != fields or body['research_consent'] is not True or body['anon_id'] != owner
                or type(body['source_ticket']) is not str or len(body['source_ticket'].encode()) > 16384
                or type(body['image_bytes']) is not int or not 1 <= body['image_bytes'] <= MAX_JPEG_BYTES):
            raise ValueError('invalid capture lookup')
        if body['bundle_sha256'] is not None:
            hexid(body['bundle_sha256'], 64)
        if privacy.objects.bucket.name != self.verifier.bucket:
            raise StateUnavailable('research destination differs')
        if privacy.is_withdrawn(owner):
            raise OwnerWithdrawn()
        ticket = loads_json_object_strict(body['source_ticket'])
        claim = self.verifier.verify(ticket, owner=owner, consent_epoch=privacy.manifest.epoch, allow_expired=True)
        if claim['consent_version'] != body['consent_version']:
            raise SourceRejected('consent version differs')
        identifier = capture_identifier(owner, privacy.manifest.epoch, claim['capture_id'])
        intent = dict(source=claim, signature=ticket['signature'], image_bytes=body['image_bytes'],
                      bundle_sha256=body['bundle_sha256'])
        media = privacy.media(owner)
        # Index is published last. A missing index is a possible interrupted
        # write; the matching intent allows the same bytes to resume safely.
        try:
            index = media.capture_index(identifier)
            original = media.capture_intent(identifier)
        except (UnicodeError, ValueError) as exc:
            raise StateUnavailable('unreadable capture evidence') from exc
        if ((index is not None and type(index) is not dict)
                or (original is not None and type(original) is not dict)):
            raise StateUnavailable('invalid capture evidence')
        if original is not None and (original.get('source_provenance') != intent
                                     or original.get('capture_intent_sha256') != sha(intent)):
            raise ImmutableConflict('capture content differs')
        if index is None:
            # A complete historical receipt is recoverable after expiry. For
            # missing or partial data, renew the ticket before sending media.
            self.verifier.verify(ticket, owner=owner, consent_epoch=privacy.manifest.epoch)
            reply = dict(schema='lite.research-capture.lookup/1', status='upload_required',
                         capture_id=claim['capture_id'], image_sha256=claim['image_sha256'],
                         bundle_sha256=body['bundle_sha256'])
        else:
            try:
                metadata = media.get_json('lite/'+owner+'/'+identifier+'.json')
            except (UnicodeError, ValueError) as exc:
                raise StateUnavailable('unreadable published metadata') from exc
            receipt = index.get('capture_receipt')
            if (type(original) is not dict or type(metadata) is not dict or type(receipt) is not dict
                    or index.get('anon_id') != owner or index.get('image_id') != identifier
                    or index.get('raw_depth_receipt') != receipt.get('raw_depth_receipt')
                    or metadata != dict(original, capture_receipt=receipt, raw_depth_receipt=receipt.get('raw_depth_receipt'))
                    or receipt.get('schema') != 'lite.research-capture.receipt/1' or receipt.get('status') != 'stored'
                    or receipt.get('capture_id') != claim['capture_id'] or receipt.get('image_id') != identifier
                    or receipt.get('image_sha256') != claim['image_sha256']
                    or receipt.get('capture_intent_sha256') != sha(intent)):
                raise StateUnavailable('publication evidence differs')
            raw = receipt.get('raw_depth_receipt')
            if ((raw is None) != (body['bundle_sha256'] is None) or (raw is not None and
                    (type(raw) is not dict or raw.get('bundle_sha256') != body['bundle_sha256']))):
                raise StateUnavailable('published depth evidence differs')
            reply = receipt
        if privacy.is_withdrawn(owner):
            raise OwnerWithdrawn()
        return reply

    def receive(self, *, privacy, writer, owner, consent_version, ticket, jpeg, metadata=None, depth=None):
        if (not isinstance(privacy, FencedPrivacy) or privacy.objects.bucket.name != self.verifier.bucket
                or writer is None or writer.owner != owner):
            raise StateUnavailable('research writer destination differs')
        if privacy.is_withdrawn(owner): raise OwnerWithdrawn()
        # Expired signatures may read a COMPLETE original receipt. Freshness is
        # required below before any new or resumed write, never bypassed by a draft.
        claim = self.verifier.verify(ticket, owner=owner, consent_epoch=privacy.manifest.epoch, allow_expired=True)
        if claim['consent_version'] != consent_version: raise SourceRejected('consent version differs')
        if type(jpeg) is not bytes or not 1 <= len(jpeg) <= MAX_JPEG_BYTES or digest(jpeg) != claim['image_sha256']:
            raise SourceRejected('original photo differs')
        with Image.open(io.BytesIO(jpeg)) as image:
            w, h = image.size
            if image.format != 'JPEG' or image.getexif().get(274, 1) != 1 or not (1 <= w <= 4096 and 1 <= h <= 4096):
                raise SourceRejected('canonical bounded JPEG required')
            image.load()
            bgr = np.asarray(image.convert('RGB'))[:, :, ::-1].copy()
        if claim['source'] == 'service' and (claim['result']['image_w'], claim['result']['image_h']) != (w, h):
            raise SourceRejected('result dimensions differ')
        raw = None; bundle = None
        if metadata is not None or depth is not None:
            if type(metadata) is not bytes or type(depth) is not bytes: raise SourceRejected('complete RGB-D pair required')
            raw = validate(metadata, depth, jpeg)
            if raw['metadata']['schema'] != 'lite.rgbd/2' or raw['metadata']['capture_id'] != claim['capture_id']:
                raise SourceRejected('RGB-D capture identity differs')
            bundle = pack(metadata, depth, jpeg)
        identifier = capture_identifier(owner, privacy.manifest.epoch, claim['capture_id'])
        intent = dict(source=claim, signature=ticket['signature'], image_bytes=len(jpeg),
                      bundle_sha256=None if bundle is None else digest(bundle))
        intent_hash = sha(intent)
        store = privacy.media(owner, writer); prefix = 'lite/'+owner+'/'+identifier
        existing = store.capture_intent(identifier)
        completed = store.capture_index(identifier)
        if completed is None:
            self.verifier.verify(ticket, owner=owner, consent_epoch=privacy.manifest.epoch)
            check_research_jpeg(jpeg)
            found, _ = _has_face(bgr)
            if found is True: raise CapturePrivacyRejected('face detected')
            if found is not False: raise StateUnavailable('privacy check unavailable')
        elif existing is None:
            raise StateUnavailable('published capture metadata missing')

        now = self.verifier.clock()
        received_at = datetime.fromtimestamp(now, timezone.utc).isoformat()
        if existing is not None:
            received_at = existing.get('received_at')
            if type(received_at) is not str or re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?\+00:00', received_at) is None:
                raise StateUnavailable('invalid capture timestamp')
        result = claim['result'] or {}
        record = dict(anon_id=owner, client='ios', image_id=identifier, image_w=w, image_h=h,
                      received_at=received_at, research_consent=True, consent_version=consent_version,
                      consent_epoch=privacy.manifest.epoch, capture_id=claim['capture_id'],
                      capture_intent_sha256=intent_hash, source_provenance=intent,
                      route=result.get('route'), ai_polygons=result.get('wound_polygons', []),
                      student_output_contract=result.get('student_output_contract'),
                      depth='raw_stored' if raw else 'not_provided',
                      camera_intrinsics=raw['metadata']['intrinsics'] if raw else None)
        if existing is not None and existing != record: raise ImmutableConflict('capture already bound to different content')
        if completed is None and existing is None:
            self.budget.reserve(owner, identifier)
            try:
                store.claim_capture_once(identifier, record)
            except ImmutableConflict:
                # Concurrent identical requests may have different receive times.
                # Only reuse an exact first intent with its original timestamp.
                old = store.capture_intent(identifier)
                if type(old) is not dict or type(old.get('received_at')) is not str: raise
                candidate = dict(record, received_at=old['received_at'])
                if old != candidate: raise
                record = candidate
        if store.capture_intent(identifier) != record: raise StateUnavailable('capture intent readback differs')

        if completed is None:
            store.put_blob_immutable(prefix+'.jpg', jpeg, 'image/jpeg')
            raw_receipt = None if raw is None else save_capture(store, owner, identifier, metadata, depth, jpeg)
        else:
            raw_receipt = completed.get('raw_depth_receipt')
        if store.get_blob(prefix+'.jpg') != jpeg: raise StateUnavailable('image readback differs')
        if raw is not None:
            if store.get_blob('lite_raw/'+owner+'/'+identifier+'.rgbd') != bundle:
                raise StateUnavailable('RGB-D readback differs')
            # Exact expected receipt, checked even on expired-ticket replays.
            expected = dict(schema='lite.rgbd.receipt/1', installation_id=owner, capture_id=identifier,
                bundle_sha256=digest(bundle), bundle_bytes=len(bundle), metadata_sha256=digest(metadata),
                rgb_sha256=digest(jpeg), depth_sha256=digest(depth), status='stored',
                registration='not_verified', training_admission='not_evaluated')
            if raw_receipt != expected: raise StateUnavailable('RGB-D receipt differs')
        elif raw_receipt is not None: raise StateUnavailable('unexpected depth receipt')
        receipt = dict(schema='lite.research-capture.receipt/1', status='stored', image_id=identifier,
                       capture_id=claim['capture_id'], capture_intent_sha256=intent_hash,
                       image_sha256=digest(jpeg), raw_depth_receipt=raw_receipt,
                       annotation_status='not_confirmed', registration='not_verified',
                       training_admission='not_evaluated')
        index = dict(anon_id=owner, image_id=identifier, client='ios', received_at=record['received_at'],
                     bytes=len(jpeg), polygons=len(record['ai_polygons']), depth=record['depth'],
                     raw_depth_receipt=raw_receipt, route=record['route'],
                     student_output_contract=record['student_output_contract'], consent_version=consent_version,
                     capture_receipt=receipt)
        final_metadata = dict(record, raw_depth_receipt=raw_receipt, capture_receipt=receipt)
        if completed is None:
            store.put_blob_immutable(prefix+'.json', encoded(final_metadata))
        if store.get_json(prefix+'.json') != final_metadata:
            raise StateUnavailable('capture metadata readback differs')
        if completed is None:
            store.publish_capture_once(identifier, index)
        if store.capture_index(identifier) != index: raise StateUnavailable('capture index readback differs')
        if privacy.is_withdrawn(owner): raise OwnerWithdrawn()
        return receipt


def capture_identifier(owner, epoch, capture_id):
    return sha(['lite.research-capture/1', owner, epoch, capture_id])[:16]


def blueprint(receiver):
    bp = Blueprint('lite_research_capture', __name__)

    @bp.post(STATUS_PATH)
    def status():
        try:
            body = loads_json_object_strict(request.get_data(cache=True).decode())
            return jsonify(receiver.lookup(g.lite_privacy, g.lite_admission.installation, body))
        except SourceExpired:
            return jsonify(error='research_source_expired', renew_source_only=True, keep_local=True), 409
        except (SourceRejected, InvalidSignature):
            return jsonify(error='research_source_mismatch', keep_local=True), 403
        except ImmutableConflict:
            return jsonify(error='research_capture_conflict', keep_local=True), 409
        except OwnerWithdrawn:
            return jsonify(error='withdrawn', keep_local=True), 410
        except (StateUnavailable, OSError):
            return jsonify(error='research_capture_unconfirmed', keep_local=True), 503
        except (ValueError, TypeError, RecursionError):
            return jsonify(error='invalid_research_lookup', keep_local=True), 400

    @bp.post(PATH)
    def capture():
        try:
            required = {'anon_id', 'research_consent', 'consent_version', 'consent_epoch', 'source_ticket'}
            raw = 'raw_depth' in request.files or 'raw_depth_metadata' in request.form
            if (set(request.form) != required | ({'raw_depth_metadata'} if raw else set())
                    or set(request.files) != {'image'} | ({'raw_depth'} if raw else set())
                    or any(len(request.form.getlist(k)) != 1 for k in request.form)
                    or any(len(request.files.getlist(k)) != 1 for k in request.files)):
                raise ValueError('exact capture parts required')
            if request.form['research_consent'] != 'true':
                return jsonify(error='research_consent_required'), 403
            if len(request.form['source_ticket'].encode()) > 16384: raise ValueError('source ticket too large')
            ticket = loads_json_object_strict(request.form['source_ticket'])
            receipt = receiver.receive(privacy=g.lite_privacy, writer=request.environ.get('woundlite.writer_ticket'),
                owner=g.lite_admission.installation, consent_version=request.form['consent_version'], ticket=ticket,
                jpeg=request.files['image'].read(MAX_JPEG_BYTES+1),
                metadata=request.form['raw_depth_metadata'].encode() if raw else None,
                depth=request.files['raw_depth'].read(MAX_PIXELS*4+1) if raw else None)
            return jsonify(receipt)
        except SourceExpired:
            return jsonify(error='research_source_expired', renew_source_only=True, keep_local=True), 409
        except ResearchPhotoMetadata:
            return jsonify(error='research_photo_metadata', keep_local=True), 400
        except CapturePrivacyRejected:
            return jsonify(error='face_detected', keep_local=True), 400
        except (SourceRejected, InvalidSignature):
            return jsonify(error='research_source_mismatch', keep_local=True), 403
        except ImmutableConflict:
            return jsonify(error='research_capture_conflict', keep_local=True), 409
        except OwnerWithdrawn:
            return jsonify(error='withdrawn', keep_local=True), 410
        except BudgetExceeded as exc:
            response = jsonify(error='research_capture_capacity', retry_after=exc.retry_after, keep_local=True)
            response.headers['Retry-After'] = str(exc.retry_after)
            return response, 429
        except (StateUnavailable, OSError):
            return jsonify(error='research_capture_unconfirmed', keep_local=True), 503
        except (ValueError, TypeError, RecursionError):
            return jsonify(error='invalid_research_capture', keep_local=True), 400

    return bp


def configured_receiver(env, *, signer, privacy, request_budget):
    """Explicit default-off switch; reuse pinned issuer public key, not consent.

    Private issuer configuration remains independently mounted and hash-pinned.
    The admission budget shares the security-budget store with a distinct key.
    """
    setting = env.get('LITE_RESEARCH_CAPTURE_ENABLED', '0')
    if setting == '0': return None
    if setting != '1': raise ValueError('explicit research capture setting required')
    from lite_research_issuer import LinkSigner
    from lite_attest_budget import RequestBudget
    if (not isinstance(signer, LinkSigner) or signer.role != 'research'
            or not isinstance(privacy, FencedPrivacy) or privacy.objects.bucket.name != signer.bucket
            or not isinstance(request_budget, RequestBudget)):
        raise ValueError('pinned research issuer and fenced storage required')
    return ResearchCaptureReceiver(
        verifier=ResearchSourceVerifier(scope=signer.scope, bucket=signer.bucket,
            public_key=signer.native_key, clock=request_budget.clock),
        budget=CaptureAdmissionBudget(request_budget.store, day_limit=30, clock=request_budget.clock))
