"""Owner-operated Mac command for a scoped first-login readback."""
import argparse
import datetime
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlsplit

from google.cloud.storage import Client

from audit_chain_contract import loads_json_object_strict
from lite_attest_state import StateUnavailable
from lite_disposition_bootstrap import StartupCredentials, google_session
from lite_native_schema import exact, text
from lite_operational_materials import _write_exclusive, available_destination, canonical, read_private
from lite_owner_review_reader import observe, prepare
from lite_provider_budget import Allocation
from lite_provider_transport import BudgetedSession
from lite_runtime_custody import GoogleTransport
from lite_usage_ledger import _hex, digest

READ_SCOPE = 'https://www.googleapis.com/auth/devstorage.read_only'
OBJECT_LIMIT = 32768
GET_LIMIT = 18


def review_binding(raw, request, expected, now, *, planning=False):
    exact(request, 'schema plan installation operator project reader')
    if request['schema'] != 'lite-owner-readback-command/1' or digest(request) != _hex(expected):
        raise ValueError('reviewed operator request required')
    plan = request['plan']
    # Offline IAM preparation may precede the window. Each real read validates the actual time.
    when = max(now, plan['starts']) if planning and type(plan['starts']) is int else now
    _, objects = prepare(raw, plan, digest(plan), installation=request['installation'],
                         operation=plan['operation'], now=when)
    native = loads_json_object_strict(raw.decode())
    project = text(request['project'], r'[a-z][a-z0-9-]{4,28}[a-z0-9]')
    reader_name = r'[a-z][a-z0-9-]{4,28}[a-z0-9]@' + re.escape(project) + r'\.iam\.gserviceaccount\.com'
    reader = text(request['reader'], reader_name)
    operator = text(request['operator'], r'[a-zA-Z0-9][A-Za-z0-9._+%-]{0,127}@[A-Za-z0-9.-]{1,190}')
    if (native['deployment']['project'] != project or reader == native['deployment']['service_account']
            or operator.lower().endswith('.gserviceaccount.com')):
        raise ValueError('separate operator and reader required')
    return {name: (bucket, prefix + '/' + key.hex() + '.json')
            for name, (bucket, prefix, key) in objects.items()}


def compose_request(native, template, expected, reference, now):
    """Fill only the installation and operation from the App's pending-review export."""
    exact(template, 'schema plan operator project reader')
    if template['schema'] != 'lite-owner-readback-template/1' or digest(template) != _hex(expected):
        raise ValueError('reviewed operator template required')
    plan = exact(template['plan'], 'schema native_sha256 source image project_number installation_sha256 starts until')
    exact(reference, 'schema origin installation_sha256 installation operation')
    number = text(plan['project_number'], r'[1-9][0-9]{5,19}')
    if (reference['schema'] != 'lite-owner-reference/1'
            or reference['origin'] != f'https://woundai-lite-native-sandbox-{number}.asia-east1.run.app'
            or _hex(reference['installation_sha256']) != plan['installation_sha256']):
        raise ValueError('reference does not match the reviewed service and installation')
    request = dict(template, schema='lite-owner-readback-command/1',
                   installation=_hex(reference['installation'], 32),
                   plan=dict(plan, operation=_hex(reference['operation'], 32)))
    # Also verifies the raw installation against the independently pinned hash.
    review_binding(native, request, digest(request), now, planning=True)
    return request


def utc(seconds):
    return datetime.datetime.fromtimestamp(seconds, datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def scope(request, names):
    plan = request['plan']
    resources_by_bucket = {}
    for bucket, path in names.values():
        resources_by_bucket.setdefault(bucket, []).append(f'projects/_/buckets/{bucket}/objects/{path}')
    bindings = []
    for bucket, resources in sorted(resources_by_bucket.items()):
        matches = ' || '.join(f'resource.name == "{resource}"' for resource in sorted(resources))
        expression = (f'request.time >= timestamp("{utc(plan["starts"])}") && '
                      f'request.time < timestamp("{utc(plan["until"])}") && '
                      f'resource.type == "storage.googleapis.com/Object" && ({matches})')
        bindings.append(dict(bucket=bucket, member='serviceAccount:' + request['reader'],
                             permissions=['storage.objects.get'], expression=expression))
    return dict(schema='lite-owner-readback-scope/1', request=digest(request), bindings=bindings,
                token_permission='iam.serviceAccounts.getAccessToken', token_target=request['reader'],
                token_caller=request['operator'], token_scope=READ_SCOPE,
                max_token_seconds=120, max_storage_gets=GET_LIMIT)


def limited_credentials(request, *, clock, monotonic, operator_transport):
    operator = operator_transport(request['operator'])
    issued, start = clock(), monotonic()
    until = request['plan']['until']
    if not request['plan']['starts'] <= issued < until:
        raise ValueError('operator window closed')
    lifetime = min(120, until - issued)
    path = '/v1/projects/-/serviceAccounts/' + request['reader'] + ':generateAccessToken'
    response = operator.request('POST', 'iamcredentials.googleapis.com', path,
                                {'scope': [READ_SCOPE], 'lifetime': str(lifetime) + 's'})
    exact(response, 'accessToken expireTime')
    token = text(response['accessToken'], r'[A-Za-z0-9._~+/=-]{1,8192}')
    expiry_text = text(response['expireTime'], r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]{8}(?:\.[0-9]{1,9})?Z')
    expiry = datetime.datetime.fromisoformat(expiry_text.replace('Z', '+00:00')).timestamp()
    if not issued < expiry <= issued + lifetime + 5:
        raise ValueError('unexpected reader credential expiry')
    end = min(issued + lifetime, expiry, until)
    return StartupCredentials(request['reader'], token, wall_end=end, mono_end=start + (end - issued),
                              clock=clock, monotonic=monotonic)


class ExactTransport(BudgetedSession):
    def __init__(self, session, allocation, names):
        super().__init__(session, allocation, provider='gcs', authorized=True, response_limit=OBJECT_LIMIT)
        self.paths = set()
        for kind, (bucket, path) in names.items():
            target = 'storage/v1/b/' + bucket + '/o/' + quote(path, safe='')
            self.paths.add('/' + target)
            if kind != 'registration':
                self.paths.add('/download/' + target)

    def request(self, method, url, **kwargs):
        target = urlsplit(url)
        if (method != 'GET' or target.scheme != 'https' or target.netloc != 'storage.googleapis.com'
                or target.path not in self.paths or target.fragment or kwargs.get('params')
                or kwargs.get('data') is not None or kwargs.get('json') is not None):
            raise StateUnavailable('outside approved storage read')

        # Metadata always describes the current object. Content uses the generation returned by reload().
        query = parse_qsl(target.query, keep_blank_values=True)
        fields = dict(query)
        if len(fields) != len(query):
            raise StateUnavailable('ambiguous storage query')
        if target.path.startswith('/download/'):
            generation = fields.get('generation', '')
            if (re.fullmatch(r'[1-9][0-9]{0,19}', generation) is None
                    or fields != {'alt': 'media', 'generation': generation, 'ifGenerationMatch': generation}):
                raise StateUnavailable('expected a pinned content generation')
        elif any(pair not in {('projection', 'noAcl'), ('prettyPrint', 'false')} for pair in query):
            raise StateUnavailable('expected current object metadata')
        return super().request(method, url, **kwargs)


def execute(raw, request, expected, *, mode, output, reference=None, clock=lambda: int(time.time()),
            monotonic=time.monotonic, operator_transport=GoogleTransport, session_factory=google_session):
    output = available_destination(output)
    if mode != 'compose' and reference is not None:
        raise ValueError('a reference is only used during offline preparation')
    if mode == 'compose':
        result = compose_request(raw, request, expected, reference, clock())
    elif mode == 'scope':
        names = review_binding(raw, request, expected, clock(), planning=True)
        result = scope(request, names)
    elif mode == 'inspect':
        names = review_binding(raw, request, expected, clock())
        if (os.environ.get('STORAGE_EMULATOR_HOST') or os.environ.get('GOOGLE_CLOUD_API_ENDPOINT_OVERRIDES_STORAGE')
                or os.environ.get('DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA') != 'true'):
            raise ValueError('fixed Google endpoint and disabled background metadata required')
        creds = limited_credentials(request, clock=clock, monotonic=monotonic, operator_transport=operator_transport)
        plan = request['plan']
        limits = dict(requests=GET_LIMIT, send_bytes=0, receive_bytes=GET_LIMIT * OBJECT_LIMIT)
        allocation = Allocation(limits, 'owner-readback', dict(expires_at=min(clock() + 60, plan['until'])),
                                clock=clock, monotonic=monotonic)
        transport = ExactTransport(session_factory(creds), allocation, names)
        try:
            client = Client(project=request['project'], credentials=creds, _http=transport,
                            client_options={'api_endpoint': 'https://storage.googleapis.com'})
            receipt = observe(raw, plan, digest(plan), installation=request['installation'],
                              operation=plan['operation'], client=client, clock=clock)
            result = dict(schema='lite-owner-readback-output/1', request=expected, receipt=receipt,
                          storage_gets=allocation.snapshot()['used']['requests'])
        finally:
            transport.close()
        if clock() >= receipt['valid_until']:
            raise StateUnavailable('observation expired before local delivery')
    else:
        raise ValueError('unknown readback operation')
    record = canonical(result)
    _write_exclusive(output, record)
    if read_private(output) != record:
        raise StateUnavailable('private output readback differs')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('compose', 'scope', 'inspect'))
    parser.add_argument('--request', required=True, type=Path)
    parser.add_argument('--native-config', required=True, type=Path)
    parser.add_argument('--expected-request', required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--reference', type=Path, help='private iPhone export, required only for compose')
    args = parser.parse_args()
    if (args.mode == 'compose') != (args.reference is not None):
        parser.error('--reference is required only for compose')
    try:
        request = loads_json_object_strict(read_private(args.request).decode())
        with args.native_config.open('rb') as config:
            native = config.read(256 * 1024 + 1)
        reference = None
        if args.reference:
            data = read_private(args.reference)
            if len(data) > 2048:
                raise ValueError('unexpected reference size')
            reference = loads_json_object_strict(data.decode())
        execute(native, request, args.expected_request, mode=args.mode, output=args.output, reference=reference)
    except Exception:
        print('Readback stopped. Confirm the private inputs, window, permissions and output.', file=sys.stderr)
        return 1
    print({'compose': 'Private request saved.', 'scope': 'Private scope saved.',
           'inspect': 'Private readback receipt saved.'}[args.mode])
    return 0


if __name__ == '__main__':
    sys.exit(main())
