"""Native-service provenance issuer; no research consent or media writes."""
import base64
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from lite_research_source import (ResearchSourceVerifier, SourceRejected, validate_claim,
                                  sha, message, ticket_message, TTL)

PATH = '/api/v1/lite/service/research-source'


class ResearchSourceIssuer:
    def __init__(self, *, scope, bucket, private_key, operations):
        from lite_service_operations import ServiceOperations
        if not isinstance(operations,ServiceOperations): raise ValueError('trusted service operations required')
        self.key=Ed25519PrivateKey.from_private_bytes(private_key)
        self.verifier=ResearchSourceVerifier(scope=scope,bucket=bucket,
            public_key=self.key.public_key().public_bytes_raw(),clock=operations.now)
        self.operations=operations

    def issue(self, owner, *, research, consent_epoch, consent_version, capture_id, image_sha256, operation=None):
        ops=self.operations
        account=ops.accounts.registration(owner); ops.accounts.require_active(account)
        p=dict(schema='lite.research-source/1',scope=self.verifier.scope,bucket=self.verifier.bucket,
            native_installation=owner,account_sha256=sha(account['subject']),registration_sha256=account['proof'],
            research_installation=research,consent_epoch=consent_epoch,consent_version=consent_version,
            capture_id=capture_id,image_sha256=image_sha256,source='manual' if operation is None else 'service',
            operation=operation,model=None,result=None,result_sha256=None)
        if operation is not None:
            _,record=ops._require(account['subject'],operation)
            receipt=ops.ledger.active_receipt(ops.ticket(account['subject'],record))
            if (record['image_sha256']!=image_sha256 or record['result'] is None
                    or receipt['outcome'] not in ('succeeded','empty')
                    or receipt['result_sha256']!=record['result_sha256']):
                raise SourceRejected('completed original service receipt required')
            p.update(model=record['model'],result=record['result'],result_sha256=record['result_sha256'])
        validate_claim(p)
        ops.accounts.require_active(account)
        now=ops.now()
        return dict(claim=p,signature=base64.b64encode(self.key.sign(message(p))).decode(),issued=now,expires=now+TTL,
            ticket_signature=base64.b64encode(self.key.sign(ticket_message(p,now,now+TTL))).decode())


def blueprint(issuer):
    from flask import Blueprint, g, jsonify, request
    from audit_chain_contract import loads_json_object_strict
    from lite_attest_state import StateUnavailable
    from lite_service_operations import OperationExpired, OperationUnknown
    bp = Blueprint('lite_research_source', __name__)

    @bp.post(PATH)
    def issue():
        try:
            body = loads_json_object_strict(request.get_data(cache=True).decode())
            fields = {'anon_id', 'research', 'consent_epoch', 'consent_version', 'capture_id', 'image_sha256', 'operation'}
            if set(body) != fields or body['anon_id'] != g.lite_admission.installation:
                return jsonify(error='research_source_request_invalid'), 400
            body.pop('anon_id')
            return jsonify(issuer.issue(g.lite_admission.installation, **body))
        except OperationExpired:
            return jsonify(error='research_source_receipt_expired', keep_local=True), 410
        except (SourceRejected, OperationUnknown, ValueError):
            return jsonify(error='research_source_unavailable', keep_local=True), 409
        except StateUnavailable:
            return jsonify(error='research_source_unconfirmed', keep_local=True), 503

    return bp
