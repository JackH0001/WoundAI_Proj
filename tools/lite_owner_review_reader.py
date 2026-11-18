"""Private first-owner receipt from five exact Cloud Storage objects.

The caller supplies an already authenticated, separately approved GET-only
client. Preparing the request requires only the public native configuration.
There is no service route, credential discovery, bucket listing or activation.
"""
import copy
import hashlib
import time

from audit_chain_contract import loads_json_object_strict
from lite_account_identity import IdentityRejected, owner_review_keys
from lite_attest_state import GCSStateStore, StateUnavailable
from lite_native_schema import exact, text
from lite_usage_ledger import _hex, _subject, digest


def read_owner_review_snapshot(*, config, installation, operation, until, store, audit, accounts, clock):
    """Read generation-bound evidence. Activation still requires a fresh check."""
    config_fields = {'v', 'namespace', 'issuer', 'audience', 'identity_key_sha256', 'session_seconds'}
    if (type(config) is not dict or set(config) != config_fields
            or type(config['v']) is not int or config['v'] != 1
            or config['issuer'] != 'https://appleid.apple.com' or config['audience'] != 'com.woundai.lite'
            or type(config['session_seconds']) is not int or config['session_seconds'] != 900):
        raise IdentityRejected('review identity configuration differs')
    _hex(config['identity_key_sha256'])
    keys = owner_review_keys(config['namespace'], installation, operation)
    sources = {'config': store, 'session': store, 'proposal': audit, 'marker': audit, 'registration': accounts}

    def snapshot():
        values = {}
        for name, source in sources.items():
            row = source.load(keys[name])
            if row is None:
                if name != 'registration':
                    raise StateUnavailable('owner review unconfirmed')
            elif (type(row) not in (tuple, list) or len(row) != 2 or type(row[0]) is not int
                  or row[0] < 1 or type(row[1]) is not dict):
                raise StateUnavailable('owner review state corrupt')
            values[name] = copy.deepcopy(row)
        return values

    started = clock()
    if type(started) is not int or type(until) is not int or not 0 <= started < until <= started + 3600:
        raise IdentityRejected('owner review expired')
    first = snapshot()
    if first['registration'] is not None:
        raise IdentityRejected('existing participant must use original account')

    session = first['session'][1]
    proposal = first['proposal'][1]
    expected_session = {
        'v': 1, 'installation_hash': digest(installation), 'operation': operation,
        'phase': 'challenge', 'account': None, 'token_sha256': None, 'session_expires_at': 0,
    }
    created = session.get('created_at')
    if type(created) is not int or not 0 <= created <= started:
        raise IdentityRejected('review challenge time differs')
    expected_session.update(created_at=created, expires_at=created + 300,
                            nonce=_hex(session.get('nonce')), state=_hex(session.get('state')))
    config_digest = digest(config)
    expected_proposal = {
        'v': 1, 'config_sha256': config_digest, 'operation': operation,
        'installation_sha256': hashlib.sha256(installation.encode()).hexdigest(),
        'account_sha256': _hex(proposal.get('account_sha256')),
        'identity_token_sha256': _hex(proposal.get('identity_token_sha256')),
        'challenge_sha256': digest(session), 'expires_at': until,
    }
    expected_marker = {'v': 1, 'config_sha256': config_digest, 'expires_at': until}
    if (digest(first['config'][1]) != config_digest or digest(session) != digest(expected_session)
            or digest(first['marker'][1]) != digest(expected_marker)
            or digest(proposal) != digest(expected_proposal)):
        raise IdentityRejected('owner review binding changed')

    valid_until = min(until, started + 60)
    midpoint = clock()
    if type(midpoint) is not int or not started <= midpoint < valid_until:
        raise IdentityRejected('owner review read window expired')
    second = snapshot()
    observed = clock()
    if type(observed) is not int or not midpoint <= observed < valid_until:
        raise IdentityRejected('owner review read window expired')
    fingerprint = digest(first)
    if fingerprint != digest(second):
        raise StateUnavailable('owner review changed during readback')
    return proposal, {
        'observed_at': observed, 'valid_until': valid_until, 'snapshot_sha256': fingerprint,
        'generations': {name: row[0] if row is not None else None for name, row in first.items()},
    }


class ExactReader:
    def __init__(self, bucket, prefix, allowed):
        self.store = GCSStateStore(bucket, prefix=prefix)
        self.allowed = frozenset(allowed)

    def load(self, key):
        if key not in self.allowed:
            raise StateUnavailable('object outside reviewed readback')
        return self.store.load(key)


def prepare(native_raw, plan, expected_plan, *, installation, operation, now):
    """Return reviewed object names for IAM preparation without provider calls."""
    _hex(expected_plan)
    exact(plan, 'schema native_sha256 source image project_number installation_sha256 operation starts until')
    if digest(plan) != expected_plan or plan['schema'] != 'lite-owner-readback/1':
        raise ValueError('reviewed readback plan required')
    if type(native_raw) is not bytes or not 0 < len(native_raw) <= 256 * 1024:
        raise ValueError('bounded native configuration required')
    if hashlib.sha256(native_raw).hexdigest() != _hex(plan['native_sha256']):
        raise ValueError('native configuration differs')
    if (type(now) is not int or type(plan['starts']) is not int or type(plan['until']) is not int
            or not 0 <= plan['starts'] <= now < plan['until'] <= plan['starts'] + 3600):
        raise ValueError('outside owner review window')
    if (_hex(operation, 32) != plan['operation']
            or hashlib.sha256(_subject(installation).encode()).hexdigest() != _hex(plan['installation_sha256'])):
        raise ValueError('review request differs')
    native = loads_json_object_strict(native_raw.decode('utf-8'))
    dep = native['deployment']
    number = text(plan['project_number'], r'[1-9][0-9]{5,19}')
    if (native['schema'] not in ('lite-native-startup/2', 'lite-native-startup/3')
            or native['enabled'] is not True or native['app']['bundle'] != 'com.woundai.lite'
            or native['apple']['environment'] != 'Sandbox' or dep['project_number'] != number
            or dep['source'] != _hex(plan['source'], 40) or dep['image'] != _hex(plan['image'])):
        raise ValueError('native deployment differs')
    keys = owner_review_keys(native['namespace'], installation, operation)
    config = dict(v=1, namespace=native['namespace'], issuer='https://appleid.apple.com',
                  audience=native['app']['bundle'], session_seconds=900,
                  identity_key_sha256=_hex(native['secrets']['identity']['sha256']))
    roles = {'config':'identity', 'session':'identity', 'proposal':'identity_audit',
             'marker':'identity_audit', 'registration':'accounts'}
    objects = {}
    state_bucket = 'woundai-lite-native-state-' + number
    for name, role in roles.items():
        location = native['stores'][role]
        bucket = location['bucket']
        allowed = {state_bucket}
        if role == 'identity_audit':
            allowed.add('woundai-lite-release-audit-' + number)
        if bucket not in allowed or bucket not in native['buckets']:
            raise ValueError('unreviewed owner state location')
        prefix = text(location['prefix'], r'lite-native/[a-z0-9_-]{1,64}/' + role)
        objects[name] = (bucket, prefix, keys[name])
    return config, objects


def observe(native_raw, plan, expected_plan, *, installation, operation, client, clock=None):
    """Return an ephemeral receipt to the private caller. No credentials in output."""
    if clock is None:
        clock = lambda: int(time.time())
    plan = copy.deepcopy(plan)
    started = clock()
    config, objects = prepare(native_raw, plan, expected_plan, installation=installation,
                              operation=operation, now=started)

    def current_time():
        value = clock()
        if type(value) is not int or value < started:
            raise StateUnavailable('readback clock moved backwards')
        return value

    def reader(*names):
        bucket, prefix, _ = objects[names[0]]
        return ExactReader(client.bucket(bucket), prefix, [objects[name][2] for name in names])

    proposal, evidence = read_owner_review_snapshot(
        config=config, installation=installation, operation=operation, until=plan['until'],
        store=reader('config', 'session'), audit=reader('proposal', 'marker'),
        accounts=reader('registration'), clock=current_time,
    )
    return dict(schema='lite-owner-readback-receipt/1', plan=expected_plan,
                account_sha256=proposal['account_sha256'], **evidence)
