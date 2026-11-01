"""Per-start control traffic, separate from the durable operational budget."""
import re
import time

from google.auth.credentials import Credentials
from google.auth.transport.requests import AuthorizedSession

from audit_chain_contract import loads_json_object_strict
from lite_provider_budget import Allocation, BudgetUnavailable, stamp
from lite_provider_transport import BudgetedSession, METADATA_ACCOUNT, NoRedirectPreparation, PlainProviderSession


def validate_limits(value):
    bounds = {'requests': (2, 128), 'send_bytes': (1, 1024**2),
              'receive_bytes': (2 * 16384, 8 * 1024**2), 'seconds': (1, 120)}
    if (type(value) is not dict or set(value) != set(bounds)
            or any(type(value[k]) is not int or not lo <= value[k] <= hi
                   for k, (lo, hi) in bounds.items())):
        raise ValueError('finite per-start control budget required')
    return value


class GoogleSession(NoRedirectPreparation, AuthorizedSession):
    pass


def refuse_refresh(*args, **kwargs):
    raise BudgetUnavailable('explicit fresh worker startup required')


class StartupCredentials(Credentials):
    """One in-memory bearer. No metadata or other network call on expiry/401."""
    def __init__(self, email, token, *, wall_end, mono_end, clock, monotonic):
        super().__init__()
        self.service_account_email = email
        self.token = token
        self._ends = (wall_end, mono_end)
        self._clocks = (clock, monotonic)

    refresh = refuse_refresh

    def before_request(self, request, method, url, headers):
        if any(stamp(clock) >= end for clock, end in zip(self._clocks, self._ends)):
            refuse_refresh()
        self.apply(headers)


def google_session(credentials):
    s = GoogleSession(credentials, auth_request=refuse_refresh, max_refresh_attempts=0)
    s.trust_env = False
    return s


def acquire(config, *, clock, monotonic=time.monotonic):
    """Verify the attached identity before obtaining a token or opening GCS."""
    limits = validate_limits(config['bootstrap_budget'])
    work = config['provider_budget']
    wall, mono = stamp(clock), stamp(monotonic)
    if not work['not_before'] <= wall < work['expires_at']:
        raise BudgetUnavailable('private provider window closed')
    meter = Allocation(limits, 'bootstrap', {'expires_at': min(wall + limits['seconds'], work['expires_at'])},
                       clock=clock, monotonic=monotonic)
    session = PlainProviderSession()
    session.trust_env = False
    transport = BudgetedSession(session, meter, provider='metadata', response_limit=16384)
    try:
        email = transport.request('GET', METADATA_ACCOUNT + 'email').content.decode('ascii').rstrip('\r\n')
        if email != config['deployment']['service_account']:
            raise BudgetUnavailable('attached identity differs')
        reply = transport.request('GET', METADATA_ACCOUNT + 'token')
        data = loads_json_object_strict(reply.content.decode('utf-8'))
        if (set(data) != {'access_token', 'token_type', 'expires_in'} or data['token_type'] != 'Bearer'
                or type(data['access_token']) is not str or len(data['access_token']) > 8192
                or not re.fullmatch(r'[A-Za-z0-9._~+/=-]+', data['access_token'])
                or type(data['expires_in']) is not int
                or not limits['seconds'] + work['seconds'] + 10 <= data['expires_in'] <= 3600):
            raise ValueError('unusable token response')
        # Count token age from before acquisition; never print or persist it.
        lifetime = data['expires_in'] - 10
        return StartupCredentials(email, data['access_token'], wall_end=wall + lifetime,
                                  mono_end=mono + lifetime, clock=clock, monotonic=monotonic), meter
    except BudgetUnavailable:
        raise
    except Exception:
        raise BudgetUnavailable('attached credential unavailable') from None
    finally:
        transport.close()
