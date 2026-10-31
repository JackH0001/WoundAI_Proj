"""Narrow worker dependencies using the same persisted-record validators.

No login, registration, inference, quota mutation or Apple code exchange. These
views are trusted startup objects, not deserialized client authority. They use
existing native records and refuse missing bindings instead of initializing them.
"""
from lite_account_lifecycle import AccountLifecycle
from lite_apple_grants import AppleGrantAuthority
from lite_apple_oauth import AppleOAuthProvider
from lite_attest_state import StateUnavailable
from lite_grant_key_custody import AccountGrantCipher
from lite_service_operations import ServiceAccounts, ServiceOperations
from lite_store_locations import LimitedStateStore
from lite_subscription_executor import key
from lite_subscription_runtime import SubscriptionRuntime


def existing(store, name, value):
    row = store.load(name)
    if row is None or row[1] != value:
        raise StateUnavailable('existing native binding required; no implicit initialization')


class GrantRevocationContext:
    """Reuse exact grant/refresh decoders and revoke CAS; expose no capture API."""
    immutable = staticmethod(SubscriptionRuntime.immutable)
    _id = AppleGrantAuthority._id
    _row = AppleGrantAuthority._row
    revoke = AppleGrantAuthority.revoke

    def __init__(self, *, lifecycle, provider, cipher, store, audit_store):
        if (not isinstance(lifecycle, AccountLifecycle) or not isinstance(provider, AppleOAuthProvider)
                or not isinstance(cipher, AccountGrantCipher) or cipher.lifecycle is not lifecycle
                or provider.client_id != lifecycle.binding['audience']):
            raise ValueError('matching revocation-only dependencies required')
        self.lifecycle, self.provider, self.cipher = lifecycle, provider, cipher
        self.store, self.audit = store, audit_store
        self.binding = lifecycle.binding
        self.namespace = self.binding['namespace']
        for name, value in (
                ('apple-grant-config', dict(v=1, binding=self.binding)),
                ('apple-grant-crypto-mode', cipher.mode),
                ('apple-grant-inventory-required', dict(v=1, binding=self.binding))):
            existing(store, key(name, self.namespace), value)

    def _validation(self, identity):
        from lite_apple_grants import read_validation
        return read_validation(self.store, identity, self.lifecycle.now())


class RegistryReadContext:
    key = staticmethod(ServiceAccounts.key)
    registration = ServiceAccounts.registration

    def __init__(self, store, lifecycle):
        if not isinstance(store, LimitedStateStore) or store.mode != 'read':
            raise ValueError('read-only registry required')
        self.store, self.lifecycle = store, lifecycle


class ClosedAccountContext:
    """Only durable associations needed by an already-accepted deletion."""
    def __init__(self, *, lifecycle, grants, store, audit_store, registry):
        if (not isinstance(lifecycle, AccountLifecycle) or not lifecycle.service_catalog
                or not isinstance(grants, GrantRevocationContext) or grants.lifecycle is not lifecycle
                or not isinstance(registry, RegistryReadContext) or registry.lifecycle is not lifecycle
                or any(not isinstance(s, LimitedStateStore) or s.mode != 'read' for s in (store, audit_store))):
            raise ValueError('closed-account read context required')
        self.lifecycle, self.grants, self.store, self.audit, self.registry = lifecycle, grants, store, audit_store, registry
        self.namespace = lifecycle.binding['namespace']
        existing(store, key('human-identity-config', self.namespace), dict(v=1, **lifecycle.binding, session_seconds=900))
        for name in ('human-lifecycle-required', 'human-grants-required'):
            existing(store, key(name, self.namespace), dict(v=1, binding=lifecycle.binding))


class ResultReadContext:
    """Validate existing results; there is deliberately no inference/claim API."""
    key = staticmethod(ServiceOperations.key)
    now = ServiceOperations.now
    load = ServiceOperations.load

    def __init__(self, *, store, accounts, ledger, clock):
        if (not isinstance(accounts, RegistryReadContext) or not callable(clock)
                or not isinstance(ledger, LimitedStateStore) or ledger.mode != 'read'):
            raise ValueError('read-only account/quota context required')
        self.store, self.accounts, self.clock = store, accounts, clock
        # Existing isolation checks inspect ledger.store, never mutate the ledger.
        self.ledger = LedgerReadContext(ledger)


class LedgerReadContext:
    def __init__(self, store): self.store = store
