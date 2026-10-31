"""Explicit, default-off quota cutover control. No HTTP or cloud discovery.

The operator must independently verify drained legacy writers, their frozen
baseline and account mapping. The callback is a trusted adapter, not a client
boolean. No live adapter or deployment is provided by this module.
"""
import copy
import hashlib
import re
from lite_attest_state import StateUnavailable
from lite_usage_ledger import digest, _hex, _integer, UsageConflict


def _key(namespace):
    if type(namespace) is not str or re.fullmatch(r'[a-z0-9_-]{1,64}', namespace) is None:
        raise UsageConflict('dedicated usage namespace required')
    return hashlib.sha256(('lite/usage-cutover/1\0'+namespace).encode()).digest()


def validate(manifest):
    if (type(manifest) is not dict or set(manifest) != {'schema', 'namespace', 'day',
            'sealed_at', 'writer_fence_sha256', 'baseline_sha256', 'account_map_sha256',
            'policy', 'reservation_seconds'} or manifest['schema'] != 'lite-usage-cutover/1'
            or manifest['policy'] != 'free2-plus10-attempt30/v1'
            or type(manifest['reservation_seconds']) is not int
            or manifest['reservation_seconds'] != 900):
        raise UsageConflict('unsupported quota cutover')
    _key(manifest['namespace'])
    if _integer(manifest['sealed_at']) // 86400 != _integer(manifest['day']):
        raise UsageConflict('invalid cutover day')
    for k in ('writer_fence_sha256', 'baseline_sha256', 'account_map_sha256'):
        _hex(manifest[k])
    return copy.deepcopy(manifest)


def seal_cutover(store, manifest, *, verify_frozen_inputs):
    """Create once, read back exactly. Never clears a legacy daily row."""
    value = validate(manifest)
    if not callable(verify_frozen_inputs) or verify_frozen_inputs(copy.deepcopy(value)) is not True:
        raise StateUnavailable('legacy writer fence/baseline/account mapping not verified')
    store.compare_exchange(_key(value['namespace']), 0, value)
    row = store.load(_key(value['namespace']))
    if row is None: raise StateUnavailable('cutover write unconfirmed')
    if row[1] != value: raise UsageConflict('cutover receipt cannot change')
    return digest(value)


class CutoverPolicy:
    def __init__(self, store, *, namespace, expected_sha256, verify_writer_fence):
        _key(namespace); _hex(expected_sha256)
        if not callable(verify_writer_fence): raise ValueError('trusted live fence check required')
        self.store, self.namespace = store, namespace
        self.expected_sha256, self.verify_writer_fence = expected_sha256, verify_writer_fence

    def read(self, namespace, now, *, admission=False):
        if namespace != self.namespace: raise StateUnavailable('cutover namespace mismatch')
        row = self.store.load(_key(namespace))
        try:
            value = validate(row[1])
            if value['namespace'] != namespace or digest(value) != self.expected_sha256 or value['sealed_at'] > now:
                raise ValueError('cutover mismatch')
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            raise StateUnavailable('cutover receipt missing or changed') from exc
        if admission and self.verify_writer_fence(copy.deepcopy(value)) is not True:
            raise StateUnavailable('legacy writer fence unconfirmed; new work blocked')
        return value
