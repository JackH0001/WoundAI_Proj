"""Offline custody for first-install identity/signing material; no cloud access.

Only an encrypted backup is written. Restoring requires an independently saved
manifest digest; verification never prints private bytes or resets an identity.
This is not a KMS grant-key backup, Apple .p8 importer, or activation authority.
"""
import argparse
import base64
from dataclasses import dataclass, field
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import time
import warnings

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

FIRST_INSTALL_ROLES = frozenset(('identity', 'native_issuer', 'research_issuer', 'operator', 'observer'))
ROLES = FIRST_INSTALL_ROLES | {'disposition_operator', 'disposition_drain'}
SCHEMA_ROLES = {'lite-operational-materials/1': FIRST_INSTALL_ROLES, 'lite-operational-materials/2': ROLES}
KDF = dict(name='scrypt', n=131072, r=8, p=1, length=32)
MAX_BACKUP = 32768


class CustodyError(ValueError):
    """Only fixed, non-secret operator messages may use this type."""


def require(condition, message):
    if not condition:
        raise CustodyError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def b64(raw):
    return base64.b64encode(raw).decode('ascii')


def decode(value, length):
    require(type(value) is str and len(value) == 4 * ((length + 2) // 3), 'invalid encoded length')
    raw = base64.b64decode(value, validate=True)
    require(len(raw) == length and b64(raw) == value, 'noncanonical encoded bytes')
    return raw


def parse(raw, maximum=MAX_BACKUP):
    require(type(raw) is bytes and 0 < len(raw) <= maximum, 'bounded backup required')
    def unique(pairs):
        out = {}
        for k, v in pairs:
            require(k not in out, 'duplicate field')
            out[k] = v
        return out
    value = json.loads(raw, object_pairs_hook=unique)
    require(type(value) is dict, 'object required')
    return value


def validate_scope(scope, *, legacy=False):
    fields = {'project', 'environment', 'namespace', 'audience', 'issuer', 'media_bucket'}
    require(type(scope) is dict and set(scope) == fields, 'exact custody scope required')
    require(all(type(v) is str for v in scope.values()), 'text scope required')
    project_pattern = r'[a-z][a-z0-9-]{4,61}[a-z0-9]' if legacy else r'[a-z][a-z0-9-]{4,28}[a-z0-9]'
    require(re.fullmatch(project_pattern, scope['project']) is not None, 'invalid project')
    require(scope['environment'] in ('owner-sandbox', 'production'), 'explicit environment required')
    require(re.fullmatch(r'[A-Za-z0-9_-]{1,128}', scope['namespace']) is not None, 'invalid namespace')
    require(scope['issuer'] == 'https://appleid.apple.com', 'wrong identity issuer')
    require(re.fullmatch(r'[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+', scope['audience']) is not None
            and len(scope['audience']) <= 128, 'invalid bundle audience')
    bucket_pattern = r'[a-z0-9][a-z0-9-]{1,220}[a-z0-9]' if legacy else r'[a-z0-9][a-z0-9-]{1,61}[a-z0-9]'
    require(re.fullmatch(bucket_pattern, scope['media_bucket']) is not None,
            'invalid research destination')


@dataclass(frozen=True)
class Material:
    scope: dict
    identifier: str
    created_at: int
    keys: dict = field(repr=False)
    schema: str = 'lite-operational-materials/2'

    def validate(self):
        validate_scope(self.scope, legacy=self.schema == 'lite-operational-materials/1')
        require(type(self.identifier) is str and re.fullmatch(r'[a-f0-9]{32}', self.identifier) is not None,
                'invalid material identifier')
        require(type(self.created_at) is int and 0 <= self.created_at < 2**53, 'invalid creation time')
        require(self.schema in SCHEMA_ROLES and type(self.keys) is dict and set(self.keys) == SCHEMA_ROLES[self.schema],
                'exact independent roles required')
        require(all(type(k) is bytes and len(k) == 32 and k != bytes(32) for k in self.keys.values()),
                '32-byte nonzero material required')
        require(len(set(self.keys.values())) == len(self.keys), 'role key reuse forbidden')

    def public_keys(self):
        self.validate()
        return {role: Ed25519PrivateKey.from_private_bytes(raw).public_key().public_bytes_raw()
                for role, raw in self.keys.items() if role != 'identity'}

    def binding(self):
        self.validate()
        return dict(namespace=self.scope['namespace'], issuer=self.scope['issuer'],
                    audience=self.scope['audience'], identity_key_sha256=sha(self.keys['identity']))

    def runtime_payloads(self):
        """Private in-memory bytes for a future separately approved importer.

        No operator/observer seed is included in App runtime payloads. Nothing
        here writes files, changes Secret Manager, or declares a deployment.
        """
        public = self.public_keys()
        result = {'identity': self.keys['identity']}
        for role in ('native', 'research'):
            result[role + '_issuer'] = canonical(dict(schema='lite-research-issuer/1', role=role,
                scope=sha(canonical(self.binding())), bucket=self.scope['media_bucket'],
                native=b64(public['native_issuer']), research=b64(public['research_issuer']),
                seed=b64(self.keys[role + '_issuer'])))
        return result

    def manifest(self):
        public = self.public_keys()
        entries = {}
        for role in sorted(self.keys):
            entries[role] = dict(format='hmac-sha256' if role == 'identity' else 'ed25519-seed',
                                 sha256=sha(self.keys[role]))
            if role in public:
                entries[role]['public_key'] = b64(public[role])
        return dict(schema=self.schema, scope=dict(self.scope),
                    identifier=self.identifier, created_at=self.created_at, roles=entries,
                    runtime_payloads={r: sha(v) for r, v in self.runtime_payloads().items()})


def generate(scope):
    """Generate a NEW set only. Never call this to recover an existing identity."""
    validate_scope(scope)
    require(scope['environment'] == 'owner-sandbox', 'new material creation is limited to the owner sandbox')
    keys = {role: secrets.token_bytes(32) for role in ROLES}
    material = Material(dict(scope), secrets.token_hex(16), int(time.time()), keys)
    material.validate()
    return material


def validate_manifest(m):
    require(type(m) is dict and set(m) == {'schema', 'scope', 'identifier', 'created_at', 'roles', 'runtime_payloads'}
            and type(m['schema']) is str and m['schema'] in SCHEMA_ROLES, 'manifest schema differs')
    validate_scope(m['scope'], legacy=m['schema'] == 'lite-operational-materials/1')
    require(type(m['identifier']) is str and re.fullmatch('[a-f0-9]{32}', m['identifier']) is not None
            and type(m['created_at']) is int and 0 <= m['created_at'] < 2**53, 'manifest identity differs')
    require(type(m['roles']) is dict and set(m['roles']) == SCHEMA_ROLES[m['schema']], 'manifest roles differ')
    for role, value in m['roles'].items():
        fields = {'format', 'sha256'} | (set() if role == 'identity' else {'public_key'})
        require(type(value) is dict and set(value) == fields
                and value['format'] == ('hmac-sha256' if role == 'identity' else 'ed25519-seed'), 'manifest role differs')
        require(type(value['sha256']) is str and re.fullmatch('[a-f0-9]{64}', value['sha256']) is not None,
                'manifest fingerprint differs')
        if role != 'identity':
            decode(value['public_key'], 32)
    require(len({v['sha256'] for v in m['roles'].values()}) == len(m['roles']), 'role fingerprint reuse forbidden')
    public = [v['public_key'] for r, v in m['roles'].items() if r != 'identity']
    require(len(set(public)) == len(public), 'signing authorities must have distinct public keys')
    payloads = m['runtime_payloads']
    require(type(payloads) is dict and set(payloads) == {'identity', 'native_issuer', 'research_issuer'}
            and all(type(v) is str and re.fullmatch('[a-f0-9]{64}', v) is not None for v in payloads.values()),
            'runtime payload fingerprints differ')
    require(payloads['identity'] == m['roles']['identity']['sha256'], 'inconsistent identity key fingerprint')


def configuration_pins(manifest, expected_manifest_sha256):
    """Public fields consumed by native startup, research issuer settings and private disposition."""
    validate_manifest(manifest)
    require(sha(canonical(manifest)) == expected_manifest_sha256, 'independent manifest pin differs')
    require(manifest['schema'] == 'lite-operational-materials/2', 'separate disposition signers required')
    roles, scope, mounts = manifest['roles'], manifest['scope'], manifest['runtime_payloads']
    binding = dict(namespace=scope['namespace'], issuer=scope['issuer'], audience=scope['audience'],
                   identity_key_sha256=roles['identity']['sha256'])
    return dict(schema='lite-operational-config-pins/1', manifest_sha256=expected_manifest_sha256,
                project=scope['project'], environment=scope['environment'], media_bucket=scope['media_bucket'],
                native=dict(namespace=scope['namespace'], identity_sha256=mounts['identity'],
                            controls=dict(operator_keys={'operator-v1': roles['operator']['public_key']},
                                          observer_keys={'observer-v1': roles['observer']['public_key']}),
                            issuer_sha256=mounts['native_issuer']),
                research=dict(issuer_sha256=mounts['research_issuer'], scope=sha(canonical(binding))),
                disposition=dict(binding=binding,
                    signers=dict(operator={'disposition-operator-v1': roles['disposition_operator']['public_key']},
                                 drain={'disposition-drain-v1': roles['disposition_drain']['public_key']}),
                    research_associations=dict(source='issuer-records/1', native_key=roles['native_issuer']['public_key'],
                                               research_key=roles['research_issuer']['public_key'])))


def check_configuration(manifest, expected_pin, native, disposition, native_issuer_sha256, research_issuer_sha256):
    pins = configuration_pins(manifest, expected_pin)
    require(pins['environment'] == 'owner-sandbox' and native.get('schema') == 'lite-native-startup/3'
            and disposition.get('schema') == 'lite-disposition-startup/3', 'shared owner sandbox configuration required')
    try:
        require(native['deployment']['project'] == disposition['deployment']['project'] == pins['project'],
                'service project differs from custody')
        require(native['namespace'] == pins['native']['namespace']
                and native['app']['bundle'] == pins['disposition']['binding']['audience'] == 'com.woundai.lite'
                and native['apple']['environment'] == 'Sandbox', 'service identity scope differs')
        require(native['secrets']['identity']['sha256'] == pins['native']['identity_sha256'],
                'native identity mount differs')
        for name, key_map in pins['native']['controls'].items():
            require(native['controls'][name] == key_map, 'first-install signer map differs')
        require(disposition['media']['bucket'] == pins['media_bucket']
                and disposition['binding'] == pins['disposition']['binding'], 'disposition account or media scope differs')
        require(disposition['signers'] == pins['disposition']['signers'], 'disposition signer map differs')
        require(disposition['research_associations'] == pins['disposition']['research_associations'],
                'research association verifier keys differ')
        require(native_issuer_sha256 == pins['native']['issuer_sha256']
                and research_issuer_sha256 == pins['research']['issuer_sha256'], 'research issuer mount differs')
    except (TypeError, KeyError):
        raise CustodyError('required service configuration field missing') from None
    return dict(schema='lite-operational-config-check/1', custody_fields_verified=True,
                manifest_sha256=expected_pin, credential_roles=len(ROLES), runtime_mounts=3)


def envelope(raw):
    obj = parse(raw)
    require(set(obj) == {'schema', 'manifest', 'kdf', 'salt', 'nonce', 'cipher', 'ciphertext'}
            and obj['schema'] == 'lite-operational-backup/1' and obj['cipher'] == 'AES-256-GCM', 'backup format differs')
    require(canonical(obj['kdf']) == canonical(KDF), 'fixed bounded KDF required')
    validate_manifest(obj['manifest'])
    decode(obj['salt'], 32)
    decode(obj['nonce'], 12)
    text = obj['ciphertext']
    require(type(text) is str and 24 <= len(text) <= 8192, 'bounded ciphertext required')
    encrypted = base64.b64decode(text, validate=True)
    require(b64(encrypted) == text, 'noncanonical ciphertext')
    return obj, encrypted


def derive(password, salt):
    require(type(password) is str and 20 <= len(password.encode('utf-8')) <= 1024,
            'a long independent recovery passphrase is required')
    return Scrypt(salt=salt, length=32, n=KDF['n'], r=KDF['r'], p=KDF['p']).derive(password.encode('utf-8'))


def seal(material, password):
    manifest = material.manifest()
    salt, nonce = secrets.token_bytes(32), secrets.token_bytes(12)
    header = dict(schema='lite-operational-backup/1', manifest=manifest, kdf=dict(KDF),
                  salt=b64(salt), nonce=b64(nonce), cipher='AES-256-GCM')
    plain = canonical(dict(schema='lite-operational-secrets/1', manifest_sha256=sha(canonical(manifest)),
                           keys={r: b64(v) for r, v in material.keys.items()}))
    encrypted = AESGCM(derive(password, salt)).encrypt(nonce, plain, canonical(header))
    return canonical(dict(header, ciphertext=b64(encrypted)))


def restore(raw, password, *, expected_manifest_sha256):
    """Fail closed if the external pin, environment, role, KDF or ciphertext differs."""
    require(type(expected_manifest_sha256) is str and
            re.fullmatch(r'[a-f0-9]{64}', expected_manifest_sha256) is not None, 'independent manifest pin required')
    obj, encrypted = envelope(raw)
    # Never accept attacker-selected KDF work factors (including bools as ints).
    require(sha(canonical(obj['manifest'])) == expected_manifest_sha256, 'backup manifest differs')
    salt, nonce = decode(obj['salt'], 32), decode(obj['nonce'], 12)
    header = {k: v for k, v in obj.items() if k != 'ciphertext'}
    try:
        plain = AESGCM(derive(password, salt)).decrypt(nonce, encrypted, canonical(header))
    except InvalidTag:
        raise CustodyError('backup authentication failed') from None
    data = parse(plain)
    require(set(data) == {'schema', 'manifest_sha256', 'keys'}
            and data['schema'] == 'lite-operational-secrets/1'
            and data['manifest_sha256'] == expected_manifest_sha256
            and type(data['keys']) is dict and set(data['keys']) == set(obj['manifest']['roles']), 'secret roles differ')
    m = obj['manifest']
    material = Material(m['scope'], m['identifier'], m['created_at'], {r: decode(v, 32) for r, v in data['keys'].items()},
                        schema=m['schema'])
    require(material.manifest() == m, 'restored fingerprints or purpose differ')
    return material


def private_parent(path):
    """Owner-operated Mac/Linux file boundary. No Windows ACL claims."""
    path = Path(path)
    require(os.name == 'posix' and path.is_absolute() and path.resolve() == path,
            'absolute nonsymlink POSIX path required')
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    s = os.fstat(fd)
    if s.st_uid != os.getuid() or stat.S_IMODE(s.st_mode) & 0o077:
        os.close(fd)
        raise CustodyError('owner-only parent directory required')
    return path, fd


def _write_exclusive(path, raw):
    path, parent = private_parent(path)
    try:
        fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        with os.fdopen(fd, 'wb') as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.fsync(parent)
    finally:
        os.close(parent)


def write_new(path, raw):
    envelope(raw)
    _write_exclusive(path, raw)


def recovery_receipt(raw):
    """Public pins for the intended backup. This receipt can precede file completion."""
    obj, _ = envelope(raw)
    return dict(schema='lite-operational-recovery-receipt/1', scope=obj['manifest']['scope'],
                manifest_sha256=sha(canonical(obj['manifest'])), backup_sha256=sha(raw), bytes=len(raw))


def check_receipt(raw, receipt_bytes):
    expected = recovery_receipt(raw)
    require(canonical(parse(receipt_bytes)) == canonical(expected), 'backup and recovery receipt differ')
    return expected['manifest_sha256']


def available_destination(path):
    path, parent = private_parent(path)
    os.close(parent)
    require(not path.exists(), 'destination already exists; preserve it and inspect the recovery receipt')
    return path


def save_pair(raw, backup_path, receipt_path):
    backup, receipt = available_destination(backup_path), available_destination(receipt_path)
    require(not backup.parent.samefile(receipt.parent), 'choose separate directories for backup and receipt')
    pins = canonical(recovery_receipt(raw))
    _write_exclusive(receipt, pins)
    require(read_private(receipt) == pins, 'receipt readback differs')
    write_new(backup, raw)
    saved = read_private(backup)
    return saved, check_receipt(saved, read_private(receipt))


def read_private(path):
    path, parent = private_parent(path)
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, 'rb') as handle:
            s = os.fstat(handle.fileno())
            require(stat.S_ISREG(s.st_mode) and s.st_nlink == 1 and s.st_uid == os.getuid()
                    and not stat.S_IMODE(s.st_mode) & 0o077 and 0 < s.st_size <= MAX_BACKUP,
                    'owner-only bounded regular backup required')
            return handle.read(MAX_BACKUP + 1)
    finally:
        os.close(parent)


def password_prompt(message):
    # getpass otherwise warns and reads an echoed secret if terminal control fails.
    with warnings.catch_warnings():
        warnings.simplefilter('error', getpass.GetPassWarning)
        try:
            return getpass.getpass(message)
        except getpass.GetPassWarning:
            raise CustodyError('terminal could not hide the passphrase') from None


def read_public_object(path, maximum):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as handle:
        st = os.fstat(handle.fileno())
        require(stat.S_ISREG(st.st_mode) and 0 < st.st_size <= maximum, 'bounded regular configuration required')
        return parse(handle.read(maximum + 1), maximum)


def read_scope(path):
    scope = read_public_object(path, MAX_BACKUP)
    validate_scope(scope)
    require(scope['environment'] == 'owner-sandbox', 'new material creation is limited to the owner sandbox')
    return scope


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    create = commands.add_parser('create-new', help='new installation only; never recovery or rotation')
    create.add_argument('--scope', required=True, type=Path)
    create.add_argument('--output', required=True, type=Path)
    create.add_argument('--receipt', required=True, type=Path)
    verify = commands.add_parser('verify', help='read encrypted backup; never export plaintext')
    verify.add_argument('--backup', required=True, type=Path)
    pins = verify.add_mutually_exclusive_group(required=True)
    pins.add_argument('--expected-manifest-sha256')
    pins.add_argument('--receipt', type=Path)
    check = commands.add_parser('check-config', help='compare public service configuration with the independent receipt')
    for arg in ('backup', 'receipt', 'native', 'disposition'):
        check.add_argument('--' + arg, required=True, type=Path)
    check.add_argument('--native-issuer-sha256', required=True)
    check.add_argument('--research-issuer-sha256', required=True)
    args = parser.parse_args()
    if args.action == 'check-config':
        raw = read_private(args.backup)
        pin = check_receipt(raw, read_private(args.receipt))
        manifest = envelope(raw)[0]['manifest']
        result = check_configuration(manifest, pin, read_public_object(args.native, 256*1024),
            read_public_object(args.disposition, 65536), args.native_issuer_sha256, args.research_issuer_sha256)
        print(json.dumps(result, indent=2))
        return
    require(sys.stdin.isatty(), 'interactive owner terminal required; no piped passwords')
    if args.action == 'create-new':
        scope = read_scope(args.scope)
        backup, receipt = available_destination(args.output), available_destination(args.receipt)
        require(not backup.parent.samefile(receipt.parent), 'choose separate directories for backup and receipt')
        print('New owner sandbox scope ' + canonical(scope).decode(), flush=True)
        require(input('Type CREATE_NEW_KEYS for a new installation only: ') == 'CREATE_NEW_KEYS', 'creation cancelled')
        password = password_prompt('Recovery passphrase (store separately): ')
        require(password == password_prompt('Repeat recovery passphrase: '), 'passphrases differ')
        material = generate(scope)
        pin = sha(canonical(material.manifest()))
        raw = seal(material, password)
        restore(raw, password, expected_manifest_sha256=pin)
        raw, pin = save_pair(raw, backup, receipt)
    else:
        raw = read_private(args.backup)
        pin = check_receipt(raw, read_private(args.receipt)) if args.receipt else args.expected_manifest_sha256
        password = password_prompt('Recovery passphrase: ')
    restored = restore(raw, password, expected_manifest_sha256=pin)
    manifest = restored.manifest()
    print(json.dumps(dict(verified=True, manifest_sha256=pin, backup_sha256=sha(raw),
                         manifest=manifest, configuration_pins=configuration_pins(manifest, pin)
                         if manifest['schema'] == 'lite-operational-materials/2' else None, cloud_mutations=0), indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # No traceback/path/password/key content in operator logs.
        reason = str(error) if isinstance(error, CustodyError) else type(error).__name__
        print('Custody operation stopped: ' + reason + '; preserve existing files.', file=sys.stderr)
        sys.exit(1)
