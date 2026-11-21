"""Read the two existing issuer receipts by exact key as the private worker.

No account discovery, callbacks, receipt writes or signing keys. Each resolve
revalidates the original operation, target and signatures before returning an ID.
"""
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from lite_attest_state import StateUnavailable
from lite_research_association import (
    ResearchAssociations, ResearchLinkPending, object_key, validate_pair, verify_signature,
)
from lite_store_locations import location, overlaps


class StoredResearchAssociations(ResearchAssociations):
    def __init__(self, native_key, research_key, *, native_audit, research_state):
        super().__init__(native_key, research_key, [])
        if overlaps(location(native_audit), location(research_state)):
            raise ValueError("separate issuer stores required")
        self.keys = (native_key, research_key)
        self.native_public = Ed25519PublicKey.from_public_bytes(native_key)
        self.load_native = native_audit.load
        self.load_research = research_state.load

    def resolve(self, scope, bucket, person, native, operation, registration):
        row = self.load_native(object_key("native", [scope, person, native, operation]))
        if row is None:
            raise ResearchLinkPending("original native offer has not been recorded")
        packet = row[1]
        try:
            if type(packet) is not dict or set(packet) != {"pair", "native"}:
                raise ValueError("invalid native offer")
            pair = packet["pair"]
            validate_pair(pair)
            bindings = (pair["scope"], pair["bucket"], pair["person"], pair["native"],
                        pair["operation"], pair["registration"])
            if bindings != (scope, bucket, person, native, operation, registration):
                raise ValueError("native offer does not match closure")
            verify_signature(self.native_public, "native", pair, packet["native"])
        except ValueError as exc:
            # Never use an unverified/exchanged target as a research lookup key.
            raise StateUnavailable("invalid original native research offer") from exc

        owner = pair["research"]
        row = self.load_research(object_key("research", [scope, bucket, owner]))
        if row is None:
            raise ResearchLinkPending("research registration has not confirmed the offer")
        proof = row[1]
        try:
            if type(proof) is not dict or proof.get("pair") != pair or proof.get("native") != packet["native"]:
                raise ValueError("stored receipts disagree")
            verified = ResearchAssociations(*self.keys, [proof])
            return verified.resolve(scope, bucket, person, native, operation, registration)
        except ValueError as exc:
            raise StateUnavailable("invalid research acceptance for the original offer") from exc
