"""Fixed-resource IAP REST adapter; caller supplies its management-only session.

There is deliberately no ADC discovery or CLI entry point. This is not wired
into the measurement service. The caller must configure zero automatic retries
for policy writes and supply a separately approved management identity.
"""
from .iap_policy import PolicyError, RESOURCE, validate_policy


class TransportError(RuntimeError):
    pass


class IapPolicyAdapter:
    def __init__(self, authorized_session):
        self._session = authorized_session

    def _post(self, resource, method, body):
        if resource != RESOURCE:
            raise PolicyError("resource_outside_mmh_scope")
        try:
            response = self._session.post(
                f"https://iap.googleapis.com/v1/{RESOURCE}:{method}",
                json=body, timeout=20, allow_redirects=False)
        except Exception:
            raise TransportError("iap_transport_unconfirmed") from None
        if response.status_code != 200:
            raise TransportError(f"iap_http_{response.status_code}")
        try:
            policy = response.json()
            validate_policy(policy)
        except Exception:
            raise TransportError("iap_invalid_policy_response") from None
        return policy

    def get_policy(self, resource):
        # Version 3 is essential: older reads can hide conditional bindings.
        return self._post(resource, "getIamPolicy", {"options": {"requestedPolicyVersion": 3}})

    def set_policy(self, resource, policy):
        validate_policy(policy)
        self._post(resource, "setIamPolicy", {"policy": policy,
                                              "updateMask": "bindings,etag"})
