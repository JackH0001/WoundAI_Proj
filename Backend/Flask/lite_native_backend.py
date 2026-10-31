"""Explicit Sandbox service composition; no environment discovery or activation.

Trusted startup supplies reviewed providers, isolated stores and previously
activated subscription/usage controls. The returned WSGI app contains only
device, account, purchase and service routes. It cannot migrate, provision KMS,
advance deletion workers, or reach the research/medical API. Legacy app:app is
unchanged. This is a candidate entry, not a production deployment factory.
"""
from dataclasses import dataclass
from itertools import combinations
import hashlib
from flask import Flask, jsonify, request
from lite_account_identity import AccountIdentityBridge, AppleIdentityVerifier, ACCOUNT_PATHS
from lite_account_lifecycle import AccountLifecycle
from lite_account_deletion import AccountDeletion, DELETION_PATHS
from lite_apple_account_events import AppleAccountEvents, EVENT_PATH
from lite_apple_grants import AppleGrantAuthority
from lite_apple_oauth import AppleOAuthProvider
from lite_attest_budget import RequestBudget
from lite_attest_enrollment import RegistrationService
from lite_attest_http import install_lite_attest, CHALLENGE_PATH, REGISTER_PATH
from lite_fenced_store import FencedPrivacy
from lite_privacy_state import PrivacyState
from lite_grant_key_custody import AccountGrantCipher, GoogleGrantKeyProvider
from lite_native_purchase import NativePurchaseBridge, PURCHASE_PATHS
from lite_service_http import SERVICE_PATHS
from lite_service_operations import ServiceOperations, RasterInference
from lite_service_release import ServiceReleaseReader, make_manifest
from lite_service_disposition import ServiceResultDisposition
from lite_disposition_progress import DispositionProgress
from lite_subscription_runtime import SubscriptionRuntime
from lite_usage_rollout import UsageRollout
from lite_usage_cutover import CutoverPolicy
from lite_usage_ledger import digest, _subject, _hex


STORE_NAMES = tuple(name for lane in ('events', 'lifecycle', 'keys', 'grants',
    'identity', 'purchases', 'deletion', 'results', 'progress') for name in (lane, lane+'_audit')) + (
    'operations', 'recovery')


from lite_store_locations import location, overlaps


@dataclass(frozen=True)
class NativeBackend:
    security: RegistrationService
    budget: RequestBudget
    privacy: object
    identity: AccountIdentityBridge
    purchases: NativePurchaseBridge
    operations: ServiceOperations
    deletion: AccountDeletion
    progress: DispositionProgress

    def create_app(self):
        app = Flask('woundailite-native-candidate', static_folder=None)
        # Reject unsupported research/admin routes BEFORE the general guard
        # can reserve capacity or create a research writer ticket.
        paths = {CHALLENGE_PATH, REGISTER_PATH, EVENT_PATH} | set(ACCOUNT_PATHS) | set(
            PURCHASE_PATHS) | set(SERVICE_PATHS) | set(DELETION_PATHS)

        @app.before_request
        def service_routes_only():
            if request.method != 'POST' or request.path not in paths:
                return jsonify(error='lite_candidate_route_not_supported'), 404

        @app.after_request
        def private_response(response):
            response.headers['Cache-Control'] = 'no-store'
            return response

        install_lite_attest(app, service=self.security, budget=self.budget, privacy=self.privacy,
            commerce=self.operations, purchases=self.purchases, identity=self.identity, deletion=self.deletion)
        return app


def compose_native_backend(*, enabled=False, runtime, rollout, security, budget, privacy,
        verifier, oauth, key_provider, stores, namespace, identity_key, inference, model_version, key_readiness=None):
    """Validate the complete graph before any component writes config markers.

    Never calls activate_fresh(), rollout.execute(), a provider, or a cleanup
    worker. Paused/unpublished releases may start so account deletion remains
    accessible; the existing durable guards still deny login and new work.
    """
    if enabled is not True:
        raise ValueError('native Sandbox candidate requires explicit enablement')
    if (not isinstance(runtime, SubscriptionRuntime) or not isinstance(rollout, UsageRollout)
            or not isinstance(security, RegistrationService) or not isinstance(budget, RequestBudget)
            or not isinstance(privacy, (PrivacyState, FencedPrivacy))
            or not isinstance(verifier, AppleIdentityVerifier) or not isinstance(oauth, AppleOAuthProvider)
            or not isinstance(key_provider, GoogleGrantKeyProvider) or not isinstance(inference, RasterInference)):
        raise ValueError('complete trusted native candidate dependencies required')
    _subject(namespace); _hex(model_version)
    if type(identity_key) is not bytes or len(identity_key) != 32:
        raise ValueError('dedicated 32-byte identity key required')
    reader = runtime.service_release
    if not isinstance(reader, ServiceReleaseReader) or rollout.service_release is not reader:
        raise ValueError('one shared durable service release required')
    if make_manifest(runtime, rollout, namespace=reader.manifest['namespace']) != reader.manifest:
        raise ValueError('runtime, quota and release pins differ')
    cutover = rollout.ledger.cutover
    if (not isinstance(cutover, CutoverPolicy) or cutover.store is not rollout.control
            or cutover.namespace != rollout.ledger.namespace or cutover.expected_sha256 != digest(rollout.manifest)
            or cutover.verify_writer_fence != rollout.admission_allowed):
        raise ValueError('usage ledger must use this rollout admission guard')
    if runtime.registry.subscription_runtime is not runtime or runtime.registry.subscription_links is not runtime.links:
        raise ValueError('one stable account registry required')
    bundle = verifier.audience
    if (bundle != 'com.woundai.lite' or oauth.client_id != bundle
            or security.admission.policy.app_id.split('.', 1)[-1] != bundle
            or security.admission.environment != 'production'
            or any(a.policy.bundle != bundle or a.policy.environment != 'Sandbox' for a in runtime.links.catalog.values())):
        raise ValueError('matching Lite app and Sandbox subscription policy required')
    if type(stores) is not dict or set(stores) != set(STORE_NAMES):
        raise ValueError('complete explicit native store map required')
    peers = dict(security=security.store, budget=budget.store,
        privacy=privacy.manifest.store if isinstance(privacy,FencedPrivacy) else privacy.store,
        accounts=runtime.registry.store, links=runtime.links.store, links_audit=runtime.links.audit,
        subscription=runtime.store, subscription_audit=runtime.audit, observer=runtime.observations,
        release=reader.store, release_audit=reader.audit, quota=rollout.ledger.store,
        rollout=rollout.store, rollout_audit=rollout.audit, cutover=rollout.control)
    # Catalog entries may deliberately share immutable Apple proof storage.
    proof_stores = []
    for authority in runtime.links.catalog.values():
        if not any(authority.proofs is s for s in proof_stores):
            proof_stores.append(authority.proofs)
            peers['apple_proof_'+str(len(proof_stores))] = authority.proofs
    named = [('native_'+k, location(s)) for k,s in stores.items()] + [(k, location(s)) for k,s in peers.items()]
    for (an,a), (bn,b) in combinations(named,2):
        if overlaps(a,b): raise ValueError('native storage scopes overlap: '+an+' / '+bn)
    if isinstance(privacy,FencedPrivacy) and any(
            loc[0]=='gcs' and loc[1]==privacy.objects.bucket.name for name,loc in named if name!='privacy'):
        raise ValueError('native state cannot share the research media bucket')
    binding = dict(namespace=namespace, issuer=verifier.issuer, audience=bundle,
        identity_key_sha256=hashlib.sha256(identity_key).hexdigest())
    life = AccountLifecycle(store=stores['lifecycle'], audit_store=stores['lifecycle_audit'],
        binding=binding, clock=verifier.clock, service_catalog=True)
    cipher = AccountGrantCipher(provider=key_provider, lifecycle=life,
        store=stores['keys'], audit_store=stores['keys_audit'], readiness=key_readiness)
    events = AppleAccountEvents(verifier=verifier, store=stores['events'], audit_store=stores['events_audit'],
        namespace=namespace, identity_key=identity_key)
    grants = AppleGrantAuthority(provider=oauth, verifier=verifier, store=stores['grants'],
        audit_store=stores['grants_audit'], cipher=cipher, namespace=namespace,
        identity_key=identity_key, lifecycle=life)
    identity = AccountIdentityBridge(runtime=runtime, verifier=verifier, store=stores['identity'],
        audit_store=stores['identity_audit'], namespace=namespace, identity_key=identity_key,
        events=events, grants=grants, renewal=True, lifecycle=life)
    purchases = NativePurchaseBridge(runtime=runtime, store=stores['purchases'], audit_store=stores['purchases_audit'])
    operations = ServiceOperations(ledger=rollout.ledger, accounts=runtime.registry, store=stores['operations'],
        inference=inference, model_version=model_version, clock=verifier.clock, recovery_store=stores['recovery'])
    deletion = AccountDeletion(bridge=identity, store=stores['deletion'], audit_store=stores['deletion_audit'])
    results = ServiceResultDisposition(operations=operations, lifecycle=life,
        store=stores['results'], audit_store=stores['results_audit'])
    progress = DispositionProgress(deletion=deletion, results=results, cipher=cipher,
        store=stores['progress'], audit_store=stores['progress_audit'])
    deletion.attach_disposition(progress)
    return NativeBackend(security,budget,privacy,identity,purchases,operations,deletion,progress)
