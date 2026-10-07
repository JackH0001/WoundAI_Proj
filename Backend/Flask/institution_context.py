"""Dedicated medical-service institution binding; unset preserves legacy behavior."""
import os
import re


def configured_org(env):
    value = env.get('WOUNDAI_INSTITUTION_ORG')
    if value is None:
        return None
    if not isinstance(value, str) or re.fullmatch(r'[a-z0-9][a-z0-9-]{1,20}', value) is None:
        raise ValueError('WOUNDAI_INSTITUTION_ORG must be an exact lowercase institution code')
    if value == 'default':
        raise ValueError('Dedicated institution cannot use legacy default org')
    if env.get('WOUNDAI_SERVICE_PROFILE', 'medical') == 'lite':
        raise ValueError('Institution binding is only for medical services')
    return value


BOUND_ORG = configured_org(os.environ)


def token_matches_institution(claims, lookup):
    if BOUND_ORG is None:
        return True
    if not isinstance(claims, dict) or claims.get('org') != BOUND_ORG:
        return False
    user = claims.get('user')
    if not isinstance(user, str) or re.fullmatch(r'[a-z0-9][a-z0-9._-]{1,30}', user) is None:
        return False
    if claims.get('sub') != BOUND_ORG + ':' + user:
        return False
    try:
        row = lookup(BOUND_ORG, user)
    except Exception:
        return False  # Unavailable account state must not authorize a stale token.
    return (isinstance(row, dict) and row.get('org') == BOUND_ORG and row.get('user') == user
            and not row.get('disabled') and row.get('role') == claims.get('role')
            and isinstance(row.get('role'), str))
