"""P0-4 Phase A: care-authorized staging and receipt-first promotion.

No Flask dependency. Callers supply storage, audit and a fresh withdrawal check.
All data in this module is PHI, including content-derived image identifiers.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time

from auth_users import can
from image_canonical import CANONICALIZATION_VERSION
from store import ImmutableConflict

ID = re.compile(r"[0-9a-f]{16}\Z")
CODE = re.compile(r"WD-[A-Za-z0-9_-]{1,32}\Z")
ORG = re.compile(r"[a-z0-9][a-z0-9-]{1,20}\Z")
CARE_SCHEMA = "woundai.care/1"
BIND_SCHEMA = "woundai.staging-bind/1"
PROMOTION_SCHEMA = "woundai.promotion/1"
RATIFICATION_SCHEMA = "woundai.legacy-ratification/1"
CARE_TTL_SECONDS = 12 * 3600
KEY_RETENTION_SECONDS = 37 * 86400  # Phase A: staging 30d + retry margin 7d


class ConsentError(ValueError):
    def __init__(self, code, status=400):
        super().__init__(code)
        self.code, self.status = code, status


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("invalid base64url")
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


class CareKeys:
    """Separate keyring; never infer a care secret from JWT configuration.

    CARE_RECEIPT_SECRET JSON: {active_kid, keys:{kid:{secret_b64,
    retired_at?, verify_until?}}}. Inactive keys must carry both timestamps and
    remain verifiable at least 37 days after retirement in Phase A.
    """
    def __init__(self, config=None, now=None):
        self.now = int(time.time() if now is None else now)
        try:
            config = config if config is not None else json.loads(os.environ["CARE_RECEIPT_SECRET"])
            self.active = config["active_kid"]
            self.keys = {}
            for kid, entry in config["keys"].items():
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", kid):
                    raise ValueError("invalid kid")
                secret = _unb64(entry["secret_b64"])
                if len(secret) < 32:
                    raise ValueError("short secret")
                if kid != self.active:
                    retired, until = entry["retired_at"], entry["verify_until"]
                    if type(retired) is not int or type(until) is not int:
                        raise ValueError("key retention timestamps required")
                    if until < retired + KEY_RETENTION_SECONDS:
                        raise ValueError("key retention too short")
                    if self.now >= until:
                        continue
                elif "retired_at" in entry:
                    raise ValueError("cannot sign with retired key")
                self.keys[kid] = secret
            if self.active not in self.keys:
                raise ValueError("no signing key")
        except (KeyError, TypeError, ValueError) as exc:
            raise ConsentError("care_receipt_not_configured", 503) from exc

    def mac(self, kid, data):
        if kid not in self.keys:
            raise ConsentError("care_receipt_unknown_kid")
        return hmac.new(self.keys[kid], data, hashlib.sha256).digest()

    def subject(self, kid, code, org):
        return self.mac(kid, ("subject-v1|" + code + "|" + org).encode()).hex()

    def status(self):
        return {"configured": True, "signing_kid": self.active,
                "verification_kids": sorted(self.keys)}


def care_key_status():
    try:
        return CareKeys().status()
    except ConsentError:
        return {"configured": False, "signing_kid": None, "verification_kids": []}


def _identity(code, actor, org):
    if not CODE.fullmatch(str(code or "")) or not ORG.fullmatch(str(org or "")):
        raise ConsentError("invalid_care_identity")
    if not isinstance(actor, str) or not actor.startswith(org + ":"):
        raise ConsentError("invalid_care_identity")


def issue_care(store, keys, code, actor, role, org, audit, now=None):
    if not can(role, "measure.clinical"):
        raise ConsentError("care_permission_denied", 403)
    _identity(code, actor, org)
    now = int(time.time() if now is None else now)
    payload = {"v": 1, "kid": keys.active, "jti": secrets.token_hex(16),
               "code": code, "org": org, "attested_by": actor,
               "iat": now, "exp": now + CARE_TTL_SECONDS}
    raw = _json(payload)
    token = _b64(raw) + "." + _b64(keys.mac(keys.active, raw))
    # Protected issuance identity is not copied into the short-lived bind.
    store.put_json_immutable("receipts/care/" + payload["jti"] + ".json",
                             {"schema": CARE_SCHEMA, **payload})
    audit("care_receipt_issued", code, {"actor": actor, "role": role,
                                       "org": org, "jti": payload["jti"]})
    return token, payload["exp"]


def verify_care(token, keys, role, org, now=None):
    if not can(role, "measure.clinical"):
        raise ConsentError("care_permission_denied", 403)
    now = int(time.time() if now is None else now)
    try:
        if not isinstance(token, str) or len(token) > 4096:
            raise ValueError("invalid envelope")
        p, s = token.split(".")
        raw, sig = _unb64(p), _unb64(s)
        payload = json.loads(raw)
        expected = {"v", "kid", "jti", "code", "org", "attested_by", "iat", "exp"}
        if not isinstance(payload, dict) or set(payload) != expected or payload["v"] != 1:
            raise ValueError("invalid payload")
        if raw != _json(payload) or not hmac.compare_digest(keys.mac(payload["kid"], raw), sig):
            raise ValueError("bad signature")
        if not re.fullmatch(r"[0-9a-f]{32}", payload["jti"]):
            raise ValueError("invalid jti")
        if type(payload["iat"]) is not int or type(payload["exp"]) is not int:
            raise ValueError("invalid times")
        if payload["iat"] > now or payload["exp"] <= now or payload["exp"] - payload["iat"] != CARE_TTL_SECONDS:
            raise ValueError("expired/future receipt")
        _identity(payload["code"], payload["attested_by"], payload["org"])
        if payload["org"] != org:
            raise ValueError("organization mismatch")
        return payload
    except (ValueError, KeyError, TypeError) as exc:
        raise ConsentError("invalid_care_receipt") from exc


def annotation_receipt(code, actor, image_id, poly_signature, tissue_signature):
    # Preserve the approved wire-independent identifier definition.
    return hashlib.sha1("|".join((code, actor, image_id, poly_signature,
                                  tissue_signature or "")).encode()).hexdigest()[:16]


def promotion_key(image_id):
    if not ID.fullmatch(str(image_id)):
        raise ConsentError("invalid_image_id")
    return "receipts/promotion/" + image_id + ".json"


def valid_promotion(p, image_id):
    if not isinstance(p, dict) or p.get("schema") != PROMOTION_SCHEMA:
        return False
    if p.get("image_id") != image_id or not ID.fullmatch(str(image_id)):
        return False
    if not CODE.fullmatch(str(p.get("code", ""))) or not ORG.fullmatch(str(p.get("org", ""))):
        return False
    if any(p.get(k) is not True for k in ("consent_train", "doctor_verified", "deidentified")):
        return False
    if not can(p.get("role"), "annotation.submit") or not str(p.get("actor", "")).startswith(p["org"] + ":"):
        return False
    if p.get("promotion_id") != hashlib.sha1(("promo|" + image_id).encode()).hexdigest()[:16]:
        return False
    if any(type(p.get(k)) is not int or p[k] <= 0 for k in ("width", "height")):
        return False
    return (bool(ID.fullmatch(str(p.get("triggering_annotation_receipt_id", ""))))
            and isinstance(p.get("canonicalization_version"), str)
            and bool(p["canonicalization_version"])
            and bool(re.fullmatch(r"[0-9a-f]{64}", str(p.get("sha256", ""))))
            and type(p.get("ts")) is int)


def image_is_ratified(store, image_id, code=None, org=None):
    """Missing/invalid evidence is legacy, regardless of legacy_image flags."""
    p = store.get_json(promotion_key(image_id))
    if p is not None:
        return valid_promotion(p, image_id) and p["code"] == code and p["org"] == org
    rat = store.get_json("receipts/legacy_ratification.json")
    return (isinstance(rat, dict) and rat.get("schema") == RATIFICATION_SCHEMA
            and isinstance(rat.get("approved_by"), str) and bool(rat["approved_by"].strip())
            and isinstance(rat.get("image_ids"), list) and image_id in rat["image_ids"])


class Promotion:
    def __init__(self, store, audit, blocked, rows, keys=None, now=None):
        self.store, self.audit, self.blocked, self.rows = store, audit, blocked, rows
        self.keys = keys
        self.now = time.time if now is None else now

    def _guard(self, code, image_id):
        if self.blocked(code, image_id):
            raise ConsentError("training_consent_withdrawn")

    def _p(self, image_id):
        p = self.store.get_json(promotion_key(image_id))
        if p is not None and not valid_promotion(p, image_id):
            raise ConsentError("invalid_promotion_receipt", 503)
        return p

    def _binding(self, image_id, code, org):
        b = self.store.get_json("staging_meta/bind/" + image_id + ".json")
        if not isinstance(b, dict) or b.get("schema") != BIND_SCHEMA or b.get("image_id") != image_id:
            raise ConsentError("staging_binding_missing", 409)
        if any(k in b for k in ("code", "org", "actor", "attested_by")):
            raise ConsentError("invalid_staging_binding", 503)
        keys = self.keys or CareKeys()
        expected = keys.subject(b.get("kid"), code, org)
        if not hmac.compare_digest(expected, str(b.get("subject_binding", ""))):
            raise ConsentError("promotion_code_mismatch")
        return b

    def _bytes(self, key, image_id, evidence):
        data = self.store.get_blob(key)
        if data is None:
            raise ConsentError("promotion_lost", 409)
        if (hashlib.sha1(data).hexdigest()[:16] != image_id
                or hashlib.sha256(data).hexdigest() != evidence.get("sha256")):
            raise ConsentError("canonical_bytes_mismatch", 503)
        return data

    def stage(self, image, token, actor, role, org):
        """No valid receipt -> analysis only, with no object writes or image ID."""
        if not token:
            return {"persisted": False, "persistence_reason": "care_receipt_required", "image_id": None}
        keys = self.keys or CareKeys()
        care = verify_care(token, keys, role, org, int(self.now()))
        _identity(care["code"], actor, org)
        iid = image.image_id
        self._guard(care["code"], iid)
        p = self._p(iid)
        if p is not None:
            if (p["code"], p["org"]) != (care["code"], org):
                raise ConsentError("promotion_code_mismatch")
            if self.store.exists("images/" + iid + ".jpg"):
                self._bytes("images/" + iid + ".jpg", iid, p)
                return {"persisted": True, "persistence_reason": "already_promoted",
                        "image_id": iid, "image_reused": True}
        key = "staging_meta/bind/" + iid + ".json"
        old = self.store.get_json(key)
        bind = {"schema": BIND_SCHEMA, "image_id": iid, "ts": int(self.now()),
                "bytes": len(image.data), "sha256": image.sha256,
                "width": int(image.pixels.shape[1]), "height": int(image.pixels.shape[0]),
                "had_metadata": image.had_metadata,
                "canonicalization_version": CANONICALIZATION_VERSION,
                "jti": care["jti"], "kid": care["kid"],
                "subject_binding": keys.subject(care["kid"], care["code"], org),
                "care_receipt_fp": hashlib.sha256(token.encode()).hexdigest()}
        if old is not None:
            old = self._binding(iid, care["code"], org)
            if old.get("sha256") != image.sha256:
                raise ConsentError("canonical_bytes_mismatch", 503)
        else:
            try:
                self.store.put_json_immutable(key, bind)
            except ImmutableConflict:
                self._binding(iid, care["code"], org)  # racing bind must match subject
        self._guard(care["code"], iid)
        created = self.store.put_blob_immutable("staging/" + iid + ".jpg", image.data)
        self.audit("care_receipt_consumed", care["code"],
                   {"actor": actor, "attested_by": care["attested_by"], "role": role,
                    "org": org, "jti": care["jti"], "image_id": iid, "created": created})
        return {"persisted": True, "persistence_reason": "staged", "image_id": iid,
                "image_reused": not created}

    def _identity_gate(self, p, code, org, image_id):
        if (p["code"], p["org"], p["image_id"]) != (code, org, image_id):
            self.audit("promotion_code_mismatch", code, {"image_id": image_id})
            raise ConsentError("promotion_code_mismatch")

    def _copy(self, p):
        iid = p["image_id"]
        self._guard(p["code"], iid)
        self._bytes("staging/" + iid + ".jpg", iid, p)
        self.store.copy_immutable("staging/" + iid + ".jpg", "images/" + iid + ".jpg")
        self._guard(p["code"], iid)

    def prepare(self, d, actor, role, org, rid):
        """p1/p2/p3 only. Caller writes sidecars, then the idempotent queue last."""
        iid, code = d["image_id"], d["code"]
        self._guard(code, iid)
        if not can(role, "annotation.submit") or not all(d.get(k) is True for k in
                ("consent_train", "doctor_verified", "deidentified")):
            raise ConsentError("training_consent_required", 403)
        p = self._p(iid)
        if p is not None:
            self._identity_gate(p, code, org, iid)
        image, stage = self.store.exists("images/" + iid + ".jpg"), self.store.exists("staging/" + iid + ".jpg")
        queued = any(r.get("annotation_receipt_id") == rid for r in self.rows())
        if p is None:
            if image:
                return {"legacy_image": True, "annotation_receipt_id": rid}
            if not stage:
                raise ConsentError("image_not_staged")
            b = self._binding(iid, code, org)
            if b.get("canonicalization_version") != CANONICALIZATION_VERSION:
                raise ConsentError("canonicalization_version_mismatch", 409)
            self._bytes("staging/" + iid + ".jpg", iid, b)
            if (d["image_w"], d["image_h"]) != (b.get("width"), b.get("height")):
                raise ConsentError("image_dimensions_mismatch")
            p = {"schema": PROMOTION_SCHEMA, "promotion_id": hashlib.sha1(("promo|" + iid).encode()).hexdigest()[:16],
                 "image_id": iid, "code": code, "org": org, "actor": actor, "role": role,
                 "triggering_annotation_receipt_id": rid, "consent_train": True,
                 "doctor_verified": True, "deidentified": True,
                 "jti": b["jti"], "kid": b["kid"], "care_receipt_fp": b["care_receipt_fp"],
                 "canonicalization_version": b["canonicalization_version"], "sha256": b["sha256"],
                 "width": b["width"], "height": b["height"], "ts": int(self.now())}
            self._guard(code, iid)
            try:
                self.store.put_json_immutable(promotion_key(iid), p)
            except ImmutableConflict:
                p = self._p(iid)  # another actor won p1; dispatch using that receipt
                self._identity_gate(p, code, org, iid)
        if (d["image_w"], d["image_h"]) != (p.get("width"), p.get("height")):
            raise ConsentError("image_dimensions_mismatch")
        trigger = p["triggering_annotation_receipt_id"] == rid
        if not trigger:
            if not image:
                raise ConsentError("promotion_pending_original" if stage else "promotion_lost", 409)
        else:
            if not image and queued and stage:
                raise ConsentError("impossible_promotion_state", 503)
            if not image and not stage:
                raise ConsentError("promotion_lost_with_queue" if queued else "promotion_lost", 409)
            if not image:
                self._copy(p)
            if not queued:
                self.audit("staging_swept_early" if image and not stage else "image_promoted",
                           code, {"image_id": iid, "annotation_receipt_id": rid})
        self._bytes("images/" + iid + ".jpg", iid, p)
        self._guard(code, iid)
        return {"legacy_image": False, "annotation_receipt_id": rid,
                "promotion_id": p["promotion_id"], "canonicalization_version": p["canonicalization_version"]}

    def finish(self, image_id, rid):
        """p5 only after the triggering queue receipt is observable; best effort."""
        p = self._p(image_id)
        if p and p["triggering_annotation_receipt_id"] == rid:
            return self.sweep(image_id, apply=True)
        return False

    def sweep(self, image_id, apply=False):
        p = self._p(image_id)
        if p is None or not self.store.exists("images/" + image_id + ".jpg"):
            return False
        if not any(r.get("annotation_receipt_id") == p["triggering_annotation_receipt_id"] for r in self.rows()):
            return False
        self._guard(p["code"], image_id)
        self._bytes("images/" + image_id + ".jpg", image_id, p)
        if not self.store.exists("staging/" + image_id + ".jpg"):
            return False
        if apply:
            self._guard(p["code"], image_id)
            self.store.delete("staging/" + image_id + ".jpg")
        return True

    def repair(self, image_id, operator, grace_seconds=86400, apply=False):
        """Only p2; never fabricate R_P and never remove staging."""
        if grace_seconds < 0 or not operator.strip():
            raise ConsentError("invalid_repair_parameters")
        p = self._p(image_id)
        if p is None or self.store.exists("images/" + image_id + ".jpg"):
            return False
        if not self.store.exists("staging/" + image_id + ".jpg") or self.now() - p["ts"] < grace_seconds:
            return False
        b = self._binding(image_id, p["code"], p["org"])
        if b.get("canonicalization_version") != p["canonicalization_version"] or p["canonicalization_version"] != CANONICALIZATION_VERSION:
            raise ConsentError("canonicalization_version_mismatch", 409)
        self._bytes("staging/" + image_id + ".jpg", image_id, b)
        self._bytes("staging/" + image_id + ".jpg", image_id, p)
        self._guard(p["code"], image_id)
        if apply:
            self._copy(p)
            self.audit("promotion_completed_by_repair", p["code"],
                       {"operator": operator, "image_id": image_id,
                        "annotation_receipt_id": p["triggering_annotation_receipt_id"],
                        "grace_seconds": grace_seconds})
        return True
