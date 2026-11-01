"""Issue a withdrawal association only after two independent device admissions."""
import base64
import hashlib
import re
import time
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from flask import Blueprint, g, jsonify, request

from audit_chain_contract import loads_json_object_strict
from lite_attest_request import RESEARCH_OFFER_PATH as OFFER, RESEARCH_ACCEPT_PATH as ACCEPT
from lite_attest_state import StateUnavailable
from lite_research_association import ResearchAssociations, digest, validate_pair

CONFIRM = "withdraw_this_research_installation_for_this_account_deletion"
TTL = 600


class LinkRejected(ValueError):
    pass


class TicketExpired(LinkRejected):
    pass


def raw_key(value, length):
    if type(value) is not str or len(value) != 4 * ((length + 2) // 3):
        raise LinkRejected("incorrect key or signature size")
    raw = base64.b64decode(value, validate=True)
    if len(raw) != length or base64.b64encode(raw).decode() != value:
        raise LinkRejected("noncanonical key or signature")
    return raw


def opaque(value, length):
    if type(value) is not str or not re.fullmatch(f"[0-9a-f]{{{length}}}", value):
        raise LinkRejected("invalid identifier")


def ticket_bytes(packet, issued, expires):
    body = dict(packet=packet, issued=issued, expires=expires)
    return ("woundailite-research-ticket/1:" + digest(body)).encode("ascii")


def object_key(kind, fields):
    return bytes.fromhex(digest(["research-withdrawal-issuer/1", kind, fields]))


def save_once(store, identity, record):
    row = store.load(identity)
    if row is None:
        try:
            store.compare_exchange(identity, 0, record)
        except StateUnavailable:
            # A commit can survive a lost acknowledgement. Read its exact bytes.
            pass
        row = store.load(identity)
    if row is None:
        raise StateUnavailable("research proof not confirmed")
    if row[1] != record:
        raise LinkRejected("another disposition already owns this association")


class LinkSigner:
    def __init__(self, *, role, scope, bucket, private_key, native_key, research_key, clock=time.time):
        if role not in ("native", "research") or not callable(clock):
            raise ValueError("explicit research issuer required")
        opaque(scope, 64)
        if type(bucket) is not str or not 1 <= len(bucket) <= 222:
            raise ValueError("explicit research destination required")
        self.role, self.scope, self.bucket, self.clock = role, scope, bucket, clock
        self.native_key, self.research_key = native_key, research_key
        ResearchAssociations(native_key, research_key, [])
        self.signer = Ed25519PrivateKey.from_private_bytes(private_key)
        expected = native_key if role == "native" else research_key
        if self.signer.public_key().public_bytes_raw() != expected:
            raise ValueError("mounted signing key belongs to a different issuer")

    def offer(self, deletion, owner, operation, research):
        if self.role != "native" or deletion.scope != self.scope:
            raise StateUnavailable("mismatched service authority")
        for value in (owner, operation, research):
            opaque(value, 32)
        person = deletion._owner(owner, operation)
        if not deletion.research_confirmation(person, owner, operation):
            raise LinkRejected("original research withdrawal confirmation required")
        registration = deletion.bridge.registry.registration(owner)
        pair = dict(action="withdraw-linked-research/1", scope=self.scope, bucket=self.bucket, person=person,
                    native=owner, research=research, operation=operation, registration=registration["proof"])
        validate_pair(pair)
        signature = self.signer.sign(ResearchAssociations.message("native", pair))
        packet = dict(pair=pair, native=base64.b64encode(signature).decode())
        save_once(deletion.audit, object_key("native", [self.scope, person, owner, operation]), packet)
        now = int(self.clock())
        expires = now + TTL
        endorsement = self.signer.sign(ticket_bytes(packet, now, expires))
        return dict(packet=packet, issued=now, expires=expires, signature=base64.b64encode(endorsement).decode())

    def accept(self, privacy, owner, ticket):
        if self.role != "research" or privacy.objects.bucket.name != self.bucket:
            raise StateUnavailable("mismatched research destination")
        if type(ticket) is not dict or set(ticket) != {"packet", "issued", "expires", "signature"}:
            raise LinkRejected("invalid offer")
        packet = ticket["packet"]
        if type(packet) is not dict or set(packet) != {"pair", "native"}:
            raise LinkRejected("invalid native proof")
        pair = packet["pair"]
        validate_pair(pair)
        if (pair["scope"], pair["bucket"], pair["research"]) != (self.scope, self.bucket, owner):
            raise LinkRejected("wrong research installation or environment")
        issued, expires = ticket["issued"], ticket["expires"]
        now = int(self.clock())
        if type(issued) is not int or type(expires) is not int or expires != issued + TTL or not 0 <= issued <= now:
            raise LinkRejected("invalid offer interval")
        native = Ed25519PublicKey.from_public_bytes(self.native_key)
        native.verify(raw_key(packet["native"], 64), ResearchAssociations.message("native", pair))
        native.verify(raw_key(ticket["signature"], 64), ticket_bytes(packet, issued, expires))

        key = object_key("research", [self.scope, self.bucket, owner])
        store = privacy.manifest.store
        saved = store.load(key)
        if saved is None and now >= expires:
            raise TicketExpired("original offer expired")
        if saved is not None and (type(saved[1]) is not dict or saved[1].get("pair") != pair):
            raise LinkRejected("research installation already bound to another deletion")
        signature = self.signer.sign(ResearchAssociations.message("research", pair))
        proof = dict(**packet, research=base64.b64encode(signature).decode())
        verified = ResearchAssociations(self.native_key, self.research_key, [proof])
        _, receipt = verified.resolve(self.scope, self.bucket, pair["person"], pair["native"],
                                      pair["operation"], pair["registration"])
        save_once(store, key, proof)
        return dict(state="association_recorded", operation=pair["operation"], native=pair["native"],
                    research=owner, receipt_sha256=receipt, cleanup_confirmed=False)


def issuer_blueprint(signer, *, deletion=None, privacy=None):
    native = signer.role == "native"
    if native != (deletion is not None) or native == (privacy is not None):
        raise ValueError("only the designated issuer may serve this route")
    bp = Blueprint("lite_research_issuer", __name__)

    @bp.post(OFFER if native else ACCEPT)
    def issue():
        admission = getattr(g, "lite_admission", None)
        if admission is None:
            return jsonify(error="lite_owner_mismatch"), 403
        try:
            body = loads_json_object_strict(request.get_data(cache=True).decode())
            expected = {"anon_id", "confirmation"} | ({"operation", "research"} if native else {"ticket"})
            if (type(body) is not dict or set(body) != expected
                    or body["anon_id"] != admission.installation or body["confirmation"] != CONFIRM):
                raise LinkRejected("explicit device confirmation required")
            if native:
                result = signer.offer(deletion, admission.installation, body["operation"], body["research"])
            else:
                result = signer.accept(privacy, admission.installation, body["ticket"])
            return jsonify(result), 200
        except TicketExpired:
            return jsonify(error="lite_research_link_offer_expired", keep_local=True), 410
        except (ValueError, InvalidSignature, TypeError):
            return jsonify(error="lite_research_link_conflict", keep_local=True), 409
        except StateUnavailable:
            return jsonify(error="lite_research_link_pending", keep_local=True), 503

    return bp


def settings(env, role):
    """Use only the explicitly pinned bytes of this service's own mounted key."""
    path = env.get("LITE_RESEARCH_LINK_FILE")
    pin = env.get("LITE_RESEARCH_LINK_SHA256")
    if path is None and pin is None:
        return None
    if type(path) is not str or not path or type(pin) is not str or not re.fullmatch("[0-9a-f]{64}", pin):
        raise ValueError("complete research issuer pins required")
    mount = Path(path)
    if not mount.is_absolute() or mount.resolve() != mount or not mount.is_file() or mount.stat().st_size > 4096:
        raise ValueError("bounded regular research issuer mount required")
    with mount.open("rb") as handle:
        raw = handle.read(4097)
    if len(raw) > 4096 or hashlib.sha256(raw).hexdigest() != pin:
        raise ValueError("research issuer mount digest differs")
    cfg = loads_json_object_strict(raw.decode())
    fields = {"schema", "role", "scope", "bucket", "native", "research", "seed"}
    if (type(cfg) is not dict or set(cfg) != fields or cfg["schema"] != "lite-research-issuer/1"
            or cfg["role"] != role):
        raise ValueError("research issuer settings differ")
    return LinkSigner(role=role, scope=cfg["scope"], bucket=cfg["bucket"], native_key=raw_key(cfg["native"], 32),
                      research_key=raw_key(cfg["research"], 32), private_key=raw_key(cfg["seed"], 32))
