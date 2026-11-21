"""Owner-operated, first-version staging of the THREE runtime custody payloads.

Default mode is offline verification. This tool cannot provision secrets, change
IAM, enable versions, read payloads, deploy, or export operator signing seeds.
An uncertain addVersion is never retried: recovery only tries to disable the
attempted first version, and reports uncertainty rather than claiming import.
"""
import argparse
import base64
import fcntl
import http.client
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

import google_crc32c
import lite_operational_materials as custody

ROLES = ('identity', 'native_issuer', 'research_issuer')
NAMES = {r: 'woundai-lite-native-sandbox-' + r.replace('_', '-') for r in ROLES}
MAX_CALLS = 240
FORWARD_CALLS = 160


class Stop(ValueError):
    """Fixed messages only; never include provider bodies or private inputs."""


class OwnerInputExpired(Stop):
    """The interactive approval expired before this process claimed a journal."""


def check_owner_input_deadline(approval):
    if int(time.time()) >= approval['expires_at']:
        raise OwnerInputExpired('authorization expired before import')


def need(ok, message):
    if not ok:
        raise Stop(message)


def digest(value):
    return custody.sha(custody.canonical(value))


def hexpin(value, length=64):
    need(type(value) is str and re.fullmatch('[a-f0-9]{%d}' % length, value), 'invalid reviewed digest')


def validate(plan):
    fields = {'schema', 'source', 'tool_sha256', 'custody_sha256', 'project', 'project_number',
              'region', 'owner', 'scope', 'manifest_sha256', 'backup_sha256', 'receipt_sha256',
              'project_policy_sha256', 'iam_review_sha256', 'created_at', 'expires_at', 'targets'}
    need(type(plan) is dict and set(plan) == fields and plan['schema'] == 'lite-runtime-custody-plan/1',
         'exact runtime custody plan required')
    hexpin(plan['source'], 40)
    for name in ('tool_sha256', 'custody_sha256', 'manifest_sha256', 'backup_sha256', 'receipt_sha256',
                 'project_policy_sha256', 'iam_review_sha256'):
        hexpin(plan[name])
    custody.validate_scope(plan['scope'])
    need(plan['scope']['project'] == plan['project'] and plan['scope']['environment'] == 'owner-sandbox'
         and plan['scope']['audience'] == 'com.woundai.lite' and plan['region'] == 'asia-east1',
         'owner-only Lite environment required')
    need(type(plan['project_number']) is str and re.fullmatch('[1-9][0-9]{5,19}', plan['project_number']),
         'project number required')
    need(type(plan['owner']) is str and re.fullmatch('[A-Za-z0-9._+%-]+@[A-Za-z0-9.-]+', plan['owner']),
         'reviewed owner account required')
    need(type(plan['created_at']) is int and type(plan['expires_at']) is int
         and 0 < plan['expires_at'] - plan['created_at'] <= 86400, 'bounded review window required')
    need(type(plan['targets']) is dict and set(plan['targets']) == set(ROLES), 'exact three runtime roles required')
    for role, target in plan['targets'].items():
        need(type(target) is dict and set(target) == {'name', 'metadata_sha256', 'policy_sha256'},
             'exact secret metadata pins required')
        need(target['name'] == 'projects/' + plan['project_number'] + '/secrets/' + NAMES[role],
             'runtime secret role differs')
        hexpin(target['metadata_sha256']); hexpin(target['policy_sha256'])
    return plan


def verify_backup(plan, backup, receipt):
    validate(plan)
    need(custody.sha(backup) == plan['backup_sha256'] and custody.sha(receipt) == plan['receipt_sha256'],
         'original backup or receipt differs')
    need(custody.check_receipt(backup, receipt) == plan['manifest_sha256'], 'independent custody pin differs')
    manifest = custody.envelope(backup)[0]['manifest']
    need(manifest['scope'] == plan['scope'] and manifest['schema'] == 'lite-operational-materials/2',
         'original seven-role owner material required')
    return custody.configuration_pins(manifest, plan['manifest_sha256'])


def authorization(plan, approval, mode, now):
    validate(plan)
    need(mode in ('import', 'reconcile'), 'invalid operation')
    need(type(approval) is dict and set(approval) == {'schema', 'plan_sha256', 'mode', 'approved', 'issued_at', 'expires_at'}
         and approval['schema'] == 'lite-runtime-custody-approval/1'
         and approval['plan_sha256'] == digest(plan) and approval['mode'] == mode
         and approval['approved'] is True, 'separate explicit import or recovery approval required')
    need(type(approval['issued_at']) is int and type(approval['expires_at']) is int and type(now) is int
         and plan['created_at'] <= approval['issued_at'] <= now < approval['expires_at']
         and 0 < approval['expires_at'] - approval['issued_at'] <= 3600, 'authorization expired')
    if mode == 'import':
        need(approval['expires_at'] <= plan['expires_at'], 'original import window expired')


class Journal:
    """A single owner-only file, locked across processes; fsync before mutations."""
    def __init__(self, path, plan, *, create):
        self.path, parent = custody.private_parent(Path(path))
        self.fd = None
        try:
            flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
            if create: flags |= os.O_CREAT | os.O_EXCL
            self.fd = os.open(self.path.name, flags, 0o600, dir_fd=parent)
            info = os.fstat(self.fd)
            need(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1
                 and not stat.S_IMODE(info.st_mode) & 0o077 and info.st_size <= 65536, 'private bounded journal required')
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            raw = os.read(self.fd, 65537)
            need(not raw or raw.endswith(b'\n'), 'partial audit record; inspect before recovery')
            self.rows = [custody.parse(line) for line in raw.splitlines()]
            if create:
                self.emit('claimed', plan=digest(plan)); os.fsync(parent)
            need(self.rows and self.rows[0] == dict(event='claimed', plan=digest(plan)), 'journal belongs to another plan')
            self.calls = sum(row.get('event') == 'call' for row in self.rows)
            need(self.calls <= MAX_CALLS, 'durable call budget exhausted')
        except BaseException:
            if self.fd is not None: os.close(self.fd)
            self.fd = None
            raise
        finally:
            os.close(parent)

    def emit(self, event, **fields):
        value = dict(event=event, **fields)
        raw = custody.canonical(value) + b'\n'
        need(os.fstat(self.fd).st_size + len(raw) <= 65536, 'audit capacity exhausted')
        os.lseek(self.fd, 0, os.SEEK_END)
        with os.fdopen(os.dup(self.fd), 'ab') as out:
            out.write(raw); out.flush(); os.fsync(out.fileno())
        self.rows.append(value)

    def reserve(self, method, path, *, recovery):
        need(self.calls < (MAX_CALLS if recovery else FORWARD_CALLS), 'durable call budget exhausted')
        self.emit('call', method=method, path=path, recovery=recovery)
        self.calls += 1

    def close(self):
        if self.fd is not None: os.close(self.fd); self.fd = None


class GoogleTransport:
    """No SDK retries or arbitrary URL. Responses never enter logs/exceptions."""
    def __init__(self, owner, *, gcloud='/opt/homebrew/bin/gcloud'):
        def run(args):
            p = subprocess.run([gcloud, '--quiet', *args], capture_output=True, timeout=25)
            need(p.returncode == 0, 'owner cloud session unavailable')
            return p.stdout.decode().strip()
        need(run(['config', 'get-value', 'auth/impersonate_service_account']) in ('', '(unset)'),
             'impersonation is outside this plan')
        need(run(['auth', 'list', '--filter=status:ACTIVE', '--format=value(account)']).lower() == owner.lower(),
             'expected owner cloud account required')
        self._token = run(['auth', 'print-access-token', owner])
        need(self._token and not any(c.isspace() for c in self._token), 'invalid cloud session')

    def request(self, method, host, path, body=None):
        connection = http.client.HTTPSConnection(host, timeout=15)
        try:
            connection.request(method, path, None if body is None else custody.canonical(body),
                               {'Authorization': 'Bearer ' + self._token, 'Content-Type': 'application/json'})
            response = connection.getresponse(); raw = response.read(2 * 1024 * 1024 + 1)
            need(len(raw) <= 2 * 1024 * 1024, 'provider response too large')
            if method == 'GET' and response.status == 404: return None
            need(200 <= response.status < 300, 'provider request not confirmed')
            return custody.parse(raw, 2 * 1024 * 1024)
        except (OSError, http.client.HTTPException):
            raise Stop('provider outcome unknown; do not repeat import') from None
        finally:
            connection.close()


class Importer:
    def __init__(self, plan, approval, journal, transport, *, mode, clock=lambda: int(time.time()), monotonic=time.monotonic):
        authorization(plan, approval, mode, clock())
        self.plan, self.approval, self.journal, self.transport = plan, approval, journal, transport
        self.clock, self.monotonic, self.started = clock, monotonic, monotonic()
        self.recovery = mode == 'reconcile'
        self.role_paths = set()

    def call(self, method, path, body=None):
        need(self.monotonic() - self.started < 900, 'single process deadline exhausted')
        need(self.clock() < self.approval['expires_at'], 'authorization expired')
        project_path = '/v1/projects/' + self.plan['project'] + ':getIamPolicy'
        project_meta = '/v1/projects/' + self.plan['project']
        allowed = {('POST', project_path), ('GET', project_meta)} | {('GET', p) for p in self.role_paths}
        for target in self.plan['targets'].values():
            leaf = '/v1/' + target['name']
            allowed.update({('GET', leaf), ('GET', leaf + ':getIamPolicy?options.requestedPolicyVersion=3'),
                            ('GET', leaf + '/versions?pageSize=4'), ('GET', leaf + '/versions/1'),
                            ('POST', leaf + '/versions/1:disable')})
            if not self.recovery: allowed.add(('POST', leaf + ':addVersion'))
        need((method, path) in allowed, 'request outside runtime custody scope')
        self.journal.reserve(method, path, recovery=self.recovery)
        host = ('cloudresourcemanager.googleapis.com' if path in (project_path, project_meta)
                else 'iam.googleapis.com' if path in self.role_paths else 'secretmanager.googleapis.com')
        return self.transport.request(method, host, path, body)

    def policies(self):
        raw = self.call('POST', '/v1/projects/' + self.plan['project'] + ':getIamPolicy',
                        {'options': {'requestedPolicyVersion': 3}})
        need(digest(raw) == self.plan['project_policy_sha256'], 'inherited project policy changed')
        project = self.call('GET', '/v1/projects/' + self.plan['project'])
        need(type(project) is dict and project.get('projectId') == self.plan['project']
             and str(project.get('projectNumber')) == self.plan['project_number']
             and not project.get('parent'), 'standalone reviewed project required; ancestor review otherwise needed')
        bindings = raw.get('bindings', [])
        roles = {b['role'] for b in bindings}
        need(1 <= len(roles) <= 16, 'bounded explicit inherited role inventory required')
        paths = {}
        for role in roles:
            need(type(role) is str and (re.fullmatch(r'roles/[A-Za-z][A-Za-z0-9.]+', role)
                 or re.fullmatch(r'projects/(?:' + re.escape(self.plan['project']) + '|' + self.plan['project_number']
                                 + r')/roles/[A-Za-z][A-Za-z0-9_.]+', role)), 'unreviewed role scope')
            paths[role] = '/v1/' + role
        self.role_paths = set(paths.values())
        owner = 'user:' + self.plan['owner'].lower()
        sensitive = {'secretmanager.versions.access', 'secretmanager.versions.add', 'secretmanager.versions.enable',
                     'secretmanager.versions.disable', 'secretmanager.versions.destroy', 'secretmanager.secrets.setIamPolicy',
                     'resourcemanager.projects.setIamPolicy', 'iam.roles.update'}
        definitions = {}
        for role, path in paths.items():
            definition = self.call('GET', path)
            need(type(definition) is dict and definition.get('name') == role
                 and type(definition.get('includedPermissions')) is list, 'inherited role definition unavailable')
            definitions[role] = definition
            if sensitive.intersection(definition['includedPermissions']):
                for b in bindings:
                    if b['role'] == role:
                        need(not b.get('condition') and {m.lower() for m in b.get('members', [])} == {owner},
                             'inherited secret or policy access is not owner-only')
        need(digest(definitions) == self.plan['iam_review_sha256'], 'reviewed role definitions changed')
        # Definitions and bindings are separate provider reads. Catch observed
        # binding drift before proceeding; this is not a cross-resource lock.
        observed = self.call('POST', '/v1/projects/' + self.plan['project'] + ':getIamPolicy',
                             {'options': {'requestedPolicyVersion': 3}})
        need(digest(observed) == self.plan['project_policy_sha256'], 'project policy changed during role inspection')

    def metadata(self, role):
        target = self.plan['targets'][role]; leaf = '/v1/' + target['name']
        meta = self.call('GET', leaf)
        need(type(meta) is dict and meta.get('name') == target['name'] and meta.get('etag'), 'secret identity unavailable')
        need(meta.get('replication') == {'userManaged': {'replicas': [{'location': self.plan['region']}]}}
             and meta.get('labels') == dict(app='woundailite', stage='owner-sandbox', purpose=role),
             'separate reviewed regional runtime secret required')
        projected = {k: meta.get(k) for k in ('name', 'replication', 'labels', 'etag', 'createTime')}
        need(digest(projected) == target['metadata_sha256'], 'secret metadata changed')
        policy = self.call('GET', leaf + ':getIamPolicy?options.requestedPolicyVersion=3')
        need(type(policy) is dict and not policy.get('bindings') and digest(policy) == target['policy_sha256'],
             'empty reviewed secret policy required')
        page = self.call('GET', leaf + '/versions?pageSize=4')
        need(type(page) is dict and not page.get('nextPageToken') and type(page.get('versions', [])) is list,
             'complete bounded version inventory required')
        versions = page.get('versions', [])
        need(len(versions) <= 1 and all(v.get('name') == target['name'] + '/versions/1' for v in versions),
             'foreign or additional version; stop')
        return versions

    def version(self, role, *, checksum=False):
        name = self.plan['targets'][role]['name'] + '/versions/1'
        value = self.call('GET', '/v1/' + name)
        need(type(value) is dict and value.get('name') == name and value.get('etag')
             and value.get('state') in ('ENABLED', 'DISABLED'), 'first version not confirmed')
        if checksum:
            need(value.get('clientSpecifiedPayloadChecksum') is True, 'payload checksum not confirmed')
        return value

    def disable(self, role):
        value = self.version(role)
        if value['state'] == 'ENABLED':
            self.call('POST', '/v1/' + value['name'] + ':disable', {'etag': value['etag']})
        need(self.version(role)['state'] == 'DISABLED', 'disabled version not confirmed')
        self.journal.emit('disabled', role=role, version=1)

    def reconcile(self):
        self.recovery = True
        attempted = {r['role'] for r in self.journal.rows if r['event'] == 'add-intent'}
        need(attempted <= set(ROLES), 'invalid attempted role')
        unresolved = []
        for role in ROLES:
            if role not in attempted: continue
            try:
                self.policies()
                need(len(self.metadata(role)) == 1, 'missing version may still arrive later')
                self.disable(role)
            except Exception:
                unresolved.append(role)
                self.journal.emit('reconciliation-required', role=role)
        # No payload read: a lost add reply cannot be upgraded to verified import.
        verified = {r['role'] for r in self.journal.rows if r['event'] == 'add-confirmed'}
        result = dict(schema='lite-runtime-custody-result/1', plan=digest(self.plan),
                      attempted=len(attempted), verified_payloads=len(verified),
                      unresolved=unresolved, all_three_imported=False, service_enabled=False)
        self.journal.emit('recovery-result', result=result)
        return result

    def stage(self, material):
        need(not self.recovery and self.journal.rows == [dict(event='claimed', plan=digest(self.plan))],
             'single fresh import only; use reconciliation after interruption')
        need(digest(material.manifest()) == self.plan['manifest_sha256'] and material.scope == self.plan['scope'],
             'restored material differs')
        payloads = material.runtime_payloads()
        need(set(payloads) == set(ROLES), 'runtime-only payloads required')
        try:
            self.policies()
            for role in ROLES: need(not self.metadata(role), 'all three targets must start empty')
            for role in ROLES:
                self.policies()
                need(not self.metadata(role), 'target changed before upload')
                payload = payloads[role]
                self.journal.emit('add-intent', role=role, sha256=custody.sha(payload))
                response = self.call('POST', '/v1/' + self.plan['targets'][role]['name'] + ':addVersion',
                                     {'payload': {'data': base64.b64encode(payload).decode(),
                                                  'dataCrc32c': str(google_crc32c.value(payload))}})
                observed = self.version(role, checksum=True)
                need(response == observed and observed['state'] == 'ENABLED', 'add reply and version differ')
                self.journal.emit('add-confirmed', role=role, sha256=custody.sha(payload), version=1)
                self.disable(role)
            self.policies()
            for role in ROLES:
                need(len(self.metadata(role)) == 1 and self.version(role)['state'] == 'DISABLED',
                     'final disabled set differs')
            result = dict(schema='lite-runtime-custody-result/1', plan=digest(self.plan), attempted=3,
                          verified_payloads=3, unresolved=[], all_three_imported=True, service_enabled=False)
            self.journal.emit('completed', result=result)
            return result
        except BaseException:
            # Reserve 80 calls for containment. If the process is killed, the
            # persisted intents support a separately approved reconcile only.
            self.reconcile()
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('verify', 'owner-import', 'reconcile'))
    for name in ('plan', 'plan-sha256', 'backup', 'receipt'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--approval'); parser.add_argument('--journal')
    args = parser.parse_args(argv)
    raw = custody.read_public_object(args.plan, 65536); validate(raw)
    need(digest(raw) == args.plan_sha256, 'reviewed plan digest differs')
    need(custody.sha(Path(__file__).read_bytes()) == raw['tool_sha256']
         and custody.sha(Path(custody.__file__).read_bytes()) == raw['custody_sha256'], 'reviewed tool source differs')
    backup, receipt = custody.read_private(args.backup), custody.read_private(args.receipt)
    verify_backup(raw, backup, receipt)
    if args.mode == 'verify':
        print(json.dumps(dict(plan=digest(raw), runtime_roles=list(ROLES), cloud_calls=0, decrypted=False)))
        return
    need(args.approval is not None and args.journal is not None, 'approval and durable journal required')
    mode = 'import' if args.mode == 'owner-import' else 'reconcile'
    approval = custody.read_public_object(args.approval, 4096)
    authorization(raw, approval, mode, int(time.time()))
    material = None
    if mode == 'import':
        need(sys.stdin.isatty(), 'interactive owner terminal required')
        expires = time.strftime('%Y-%m-%d %H:%M:%S %z', time.localtime(approval['expires_at']))
        print(f'本人匯入授權截止時間 / Import approval expires at {expires}', file=sys.stderr)
        confirmed = input('Type STAGE_THREE_RUNTIME_KEYS; services stay disabled: ')
        check_owner_input_deadline(approval)
        need(confirmed == 'STAGE_THREE_RUNTIME_KEYS', 'cancelled')
        password = custody.password_prompt('Recovery passphrase (hidden): ')
        try:
            check_owner_input_deadline(approval)
            material = custody.restore(backup, password, expected_manifest_sha256=raw['manifest_sha256'])
        finally:
            del password
        check_owner_input_deadline(approval)
    journal = Journal(args.journal, raw, create=mode == 'import')
    try:
        # Connect only after local pins, approval, password and durable claim.
        importer = Importer(raw, approval, journal, GoogleTransport(raw['owner']), mode=mode)
        result = importer.stage(material) if material is not None else importer.reconcile()
        print(json.dumps(result))
    finally:
        journal.close()


if __name__ == '__main__':
    try:
        main()
    except OwnerInputExpired:
        print('本人匯入時段已結束。本次操作尚未建立日誌或連線。請關閉視窗。\n'
              'Import approval expired before staging. Close this window.', file=sys.stderr)
        sys.exit(2)
    except (Exception, KeyboardInterrupt):
        print('Stopped. Preserve backup and journal; do not repeat import. Review reconciliation.', file=sys.stderr)
        sys.exit(2)
