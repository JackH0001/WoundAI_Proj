"""Shared configuration vocabulary; imports no HTTP app, models or credentials."""
import re

STORE_NAMES = tuple(name for lane in ('events', 'lifecycle', 'keys', 'grants',
    'identity', 'purchases', 'deletion', 'results', 'progress') for name in (lane, lane+'_audit')) + (
    'operations', 'recovery')
PEER_NAMES = ('security','budget','privacy','accounts','links','links_audit',
    'subscription','subscription_audit','observer','release','release_audit',
    'quota','rollout','rollout_audit','cutover','apple_proofs')
ALL_STORES = PEER_NAMES + STORE_NAMES


def exact(value, fields):
    if type(value) is not dict or set(value) != set(fields.split()):
        raise ValueError('native configuration fields differ')
    return value


def text(value, pattern):
    if type(value) is not str or re.fullmatch(pattern, value) is None:
        raise ValueError('native configuration value invalid')
    return value
