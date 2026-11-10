"""Private one-shot GCS bootstrap runner. No HTTP route, signer, IAM or deletion.

Use only in an independently authorized, supervised job with attached identity.
Public inputs and exact command are hash pinned. An invocation advances one
already-signed core step; it cannot invent an observer or retry an uncertain step.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import time

from audit_chain_contract import loads_json_object_strict
from lite_attest_state import GCSStateStore, StateUnavailable
from lite_disposition_bootstrap import acquire, google_session, validate_limits
from lite_fresh_bootstrap_factory import build, GCSFactory, NAMES
from lite_store_locations import overlaps
from lite_provider_budget import ProviderBudget, validate_policy
from lite_provider_transport import BudgetedSession
from lite_usage_ledger import digest, _hex


class NoState:
    def load(self, key): return None
    def compare_exchange(self, *args): raise StateUnavailable('preflight cannot write')


def checked(raw, expected, limit):
    _hex(expected)
    if type(raw) is not bytes or not 0 < len(raw) <= limit or hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('pinned bounded bytes required')
    return loads_json_object_strict(raw.decode('utf-8'))


def preflight(raw, native_raw, request_raw, *, expected_config, expected_native,
              expected_request, expected_source, expected_image, clock):
    c = checked(raw, expected_config, 256*1024)
    n = checked(native_raw, expected_native, 256*1024)
    request = checked(request_raw, expected_request, 32768)
    fields = {'schema','enabled','deployment','native_config','plan','sources','writers',
              'locations','buckets','budget_stores','provider_budget','bootstrap_budget'}
    if set(c) != fields or c['schema'] != 'lite-bootstrap-runner/1' or c['enabled'] is not True:
        raise ValueError('explicit private bootstrap runner required')
    _hex(expected_source,40); _hex(expected_image)
    d = c['deployment']
    if (type(d) is not dict or set(d) != {'project','project_number','service_account','source','image'}
            or d['source'] != expected_source or d['image'] != expected_image
            or not re.fullmatch(r'[a-z][a-z0-9-]{4,61}[a-z0-9]',d['project'])
            or not re.fullmatch(r'[1-9][0-9]{5,19}',d['project_number'])
            or not re.fullmatch(r'[a-z][a-z0-9-]{4,28}[a-z0-9]@'+re.escape(d['project'])+r'\.iam\.gserviceaccount\.com',d['service_account'])
            or c['native_config'] != expected_native
            or any(n['deployment'][k] != d[k] for k in ('project','project_number'))
            or n['deployment']['service_account'] == d['service_account']):
        raise ValueError('separate pinned bootstrap identity and source required')
    if type(c['locations']) is not dict or set(c['locations']) != NAMES:
        raise ValueError('exact operational lane set required')
    if type(c['budget_stores']) is not dict or set(c['budget_stores']) != {'control','audit'}:
        raise ValueError('separate budget lanes required')
    pairs = [('gcs',v['bucket'],v['prefix']) for v in n['stores'].values()]
    pairs += [('gcs',v['bucket'],v['prefix']) for v in c['locations'].values()]
    for loc in c['budget_stores'].values():
        if (type(loc) is not dict or set(loc) != {'bucket','prefix'}
                or not re.fullmatch(r'[a-z0-9][a-z0-9-]{1,61}[a-z0-9]',loc['bucket'])
                or not re.fullmatch(r'[a-z0-9_-]+(?:/[a-z0-9_-]+)+',loc['prefix'])
                or not loc['prefix'].startswith('lite-bootstrap/'+c['plan']['namespace']+'/')):
            raise ValueError('explicit dedicated budget location required')
        pair = ('gcs',loc['bucket'],loc['prefix'])
        if any(overlaps(pair,other) for other in pairs): raise ValueError('budget aliases data/control')
        pairs.append(pair)
    buckets = {v['bucket'] for v in (*c['locations'].values(),*c['budget_stores'].values())}
    if (type(c['buckets']) is not dict or set(c['buckets']) != buckets
            or any(type(v) is not dict or set(v) != {'metageneration'}
                   or type(v['metageneration']) is not str or not re.fullmatch(r'[1-9][0-9]*',v['metageneration'])
                   for v in c['buckets'].values())):
        raise ValueError('exact reviewed bucket generations required')
    limits = validate_policy(c['provider_budget'],digest(c['provider_budget']))
    validate_limits(c['bootstrap_budget'])
    if (limits['source'] != expected_source or limits['image'] != expected_image
            or limits['manifest'] != digest(c['plan']) or limits['namespace'] != c['plan']['namespace']
            or not limits['not_before'] <= clock() < limits['expires_at']):
        raise ValueError('budget does not bind this bootstrap plan/window')
    if (set(request) != {'schema','plan','mode','command','inner'}
            or request['schema'] != 'lite-bootstrap-request/1' or request['plan'] != digest(c['plan'])
            or request['mode'] not in ('inspect','execute')
            or request['mode'] == 'inspect' and (request['command'] is not None or request['inner'] is not None)):
        raise ValueError('exact single bootstrap request required')
    args = dict(raw=native_raw,expected_config=expected_native,plan=c['plan'],expected_plan=digest(c['plan']),
                sources=c['sources'],writers=c['writers'],locations=c['locations'],expected_locations=digest(c['locations']),clock=clock)
    # Validate ALL core manifests before even metadata credentials are obtained.
    x = build(**args,state_factory=lambda *_:NoState())
    if request['mode'] == 'execute':
        signed = x.evidence.verify(request['command'],'approval')
        if (signed.get('manifest') != digest(c['plan']) or signed.get('inner_sha256') != digest(request['inner'])
                or signed.get('action') not in ('bootstrap-step','bootstrap-recover')
                or signed.get('stage') not in ('subscription','quota','release')):
            raise ValueError('signed command differs from exact requested step')
    return c, request, args


class GCSProviders:
    """Cloud Run job identity only; no local ADC, secret, Apple or KMS access."""
    def __init__(self): self.transports = []; self.started = False

    def connect(self, c, *, clock):
        if self.started: raise StateUnavailable('one-shot provider already started')
        self.started = True
        if os.environ.get('DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA') != 'true':
            raise ValueError('background metadata disabled required')
        from google.cloud.storage import Client
        self.config = c
        self.credentials, self.control_meter = acquire(c,clock=clock)
        if self.credentials.service_account_email != c['deployment']['service_account']:
            raise ValueError('attached bootstrap identity differs')
        self.control = BudgetedSession(google_session(self.credentials),self.control_meter,provider='gcs',authorized=True)
        self.transports.append(self.control)
        self.client = Client(project=c['deployment']['project'],credentials=self.credentials,_http=self.control)

    def activate(self, allocation):
        from google.cloud.storage import Client
        self.control.close()
        transport = BudgetedSession(google_session(self.credentials),allocation,provider='gcs',authorized=True)
        self.transports.append(transport)
        self.client = Client(project=self.config['deployment']['project'],credentials=self.credentials,_http=transport)

    def factory(self, c):
        return GCSFactory(client=self.client,project_number=c['deployment']['project_number'],buckets=c['buckets'])

    def close(self):
        for transport in self.transports: transport.close()


def run(raw, native_raw, request_raw, *, expected_config, expected_native,
        expected_request, expected_source, expected_image, providers=None, clock=lambda:int(time.time())):
    c, request, args = preflight(raw,native_raw,request_raw,expected_config=expected_config,
        expected_native=expected_native,expected_request=expected_request,expected_source=expected_source,
        expected_image=expected_image,clock=clock)
    p = GCSProviders() if providers is None else providers
    allocation = None
    try:
        p.connect(c,clock=clock)
        factory = p.factory(c)
        if request['mode'] == 'execute':
            # Separate durable allocation journal; failure never refunds/reuses a
            # slice. Its control-plane calls are bounded by the startup meter.
            states = {k:factory('budget-'+k,v) for k,v in c['budget_stores'].items()}
            budget = ProviderBudget(policy=c['provider_budget'],expected=digest(c['provider_budget']),
                store=states['control'],audit_store=states['audit'],clock=clock)
            allocation = budget.allocate()
            p.activate(allocation)
            factory = p.factory(c)
        # Bucket policy is freshly checked with the active transport; all core
        # mutations are now inside the reserved allocation. Inspect never allocates.
        x = build(**args,state_factory=factory)
        inspection = None
        if request['mode'] == 'inspect':
            result = x.inspect()
            quota, release = x.rollout._read(), x.reader.snapshot()
            if x.inspect() != result:
                raise StateUnavailable('bootstrap changed during inspection; inspect again')
            inspection = dict(observed_at=int(clock()), quota=quota[1] if quota else None,
                              release=release[1] if release else None)
        else:
            result = x.execute(request['command'],inner=request['inner'])
        return dict(schema='lite-bootstrap-runner-receipt/2',mode=request['mode'],plan=digest(c['plan']),
                    request=expected_request,source=expected_source,image=expected_image,result=result,
                    inspection=inspection,
                    allocation=None if allocation is None else allocation.name,
                    provider_mutation_requested=request['mode']=='execute',customer_ingress_changed=False,
                    audit_preserved=True,automatic_recovery=False)
    finally:
        p.close()


def read_regular(name, maximum):
    # This is public configuration, never a private-key loader. O_NOFOLLOW plus
    # fstat binds size/type to the opened file; caller supplies its independent hash.
    import stat
    path = Path(name)
    if not path.is_absolute(): raise ValueError('absolute configuration path required')
    fd = os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
    try:
        s = os.fstat(fd)
        if not stat.S_ISREG(s.st_mode) or not 0 < s.st_size <= maximum: raise ValueError('bounded regular input required')
        with os.fdopen(fd,'rb',closefd=False) as f: return f.read(maximum+1)
    finally: os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('config','native','request','source','image'):
        parser.add_argument('--'+name,required=True)
    for name in ('config','native','request'):
        parser.add_argument('--'+name+'-sha256',required=True)
    a = parser.parse_args()
    try:
        receipt = run(read_regular(a.config,256*1024),read_regular(a.native,256*1024),read_regular(a.request,32768),
            expected_config=a.config_sha256,expected_native=a.native_sha256,expected_request=a.request_sha256,
            expected_source=a.source,expected_image=a.image)
    except Exception as error:
        # Do not dump provider response bodies, credentials, config or traceback.
        print(json.dumps(dict(status='stopped',error_type=type(error).__name__,
            action='preserve evidence; inspect original state; no automatic retry')))
        return 1
    print(json.dumps(receipt,sort_keys=True)); return 0


if __name__ == '__main__': raise SystemExit(main())
