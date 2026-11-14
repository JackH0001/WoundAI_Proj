"""Synthetic offline custody, real crypto and existing identity/quota contracts."""
import base64
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import warnings

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import lite_operational_materials as custody
from lite_account_identity import AccountIdentityBridge
from lite_apple_account_events import AppleAccountEvents
from lite_attest_state import SQLiteStateStore, StateUnavailable
from lite_research_issuer import settings as issuer_settings
from lite_disposition_startup import association_keys
from lite_subscription_executor import SignedEvidence, canonical
from lite_usage_ledger import UsageLedger, DailyGrant, UsageLimit
import test_lite_account_identity as identity_fixture
import test_lite_shared_runtime as shared

SCOPE = dict(project='synthetic-project', environment='owner-sandbox', namespace='synthetic-native',
             audience='com.example.synthetic', issuer='https://appleid.apple.com', media_bucket='synthetic-media')
PASSWORD = secrets.token_urlsafe(32)


def material():
    return custody.Material(dict(SCOPE), 'a' * 32, 1793810000,
        {role: hashlib.sha256(('fixture-' + role).encode()).digest() for role in custody.ROLES})


class CustodyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = material()
        cls.raw = custody.seal(cls.original, PASSWORD)
        cls.pin = custody.sha(custody.canonical(cls.original.manifest()))

    def restore(self, raw=None, password=PASSWORD, pin=None):
        return custody.restore(self.raw if raw is None else raw, password,
            expected_manifest_sha256=self.pin if pin is None else pin)

    def mutated(self, mutate):
        obj = json.loads(self.raw)
        mutate(obj)
        return custody.canonical(obj)

    def test_restore_preserves_all_seven_materials_and_runtime_payloads(self):
        restored = self.restore()
        self.assertEqual(restored.keys, self.original.keys)
        self.assertEqual(restored.runtime_payloads(), self.original.runtime_payloads())

    def test_backup_and_manifest_do_not_contain_private_bytes(self):
        manifest = custody.canonical(self.original.manifest())
        for secret in self.original.keys.values():
            for representation in (secret, custody.b64(secret).encode(), secret.hex().encode()):
                self.assertNotIn(representation, self.raw)
                self.assertNotIn(representation, manifest)
                self.assertNotIn(representation.decode('ascii', errors='replace'), repr(self.original))

    def test_each_seal_uses_a_different_salt_nonce_and_ciphertext(self):
        other = json.loads(custody.seal(self.original, PASSWORD))
        first = json.loads(self.raw)
        for k in ('salt', 'nonce', 'ciphertext'):
            self.assertNotEqual(other[k], first[k])
        self.assertEqual(other['manifest'], first['manifest'])

    def test_wrong_password_never_returns_material(self):
        with self.assertRaisesRegex(ValueError, 'authentication failed'):
            self.restore(password=PASSWORD + '-wrong')

    def test_ciphertext_corruption_rejected(self):
        def change(obj):
            raw = bytearray(base64.b64decode(obj['ciphertext']))
            raw[40] ^= 1
            obj['ciphertext'] = custody.b64(bytes(raw))
        with self.assertRaisesRegex(ValueError, 'authentication failed'):
            self.restore(self.mutated(change))

    def test_wrong_external_pin_rejected_before_password_derivation(self):
        with patch.object(custody, 'derive', side_effect=AssertionError('must not derive')):
            with self.assertRaisesRegex(ValueError, 'manifest differs'):
                self.restore(pin='b' * 64)

    def test_cross_environment_swap_rejected(self):
        changed = self.mutated(lambda o: o['manifest']['scope'].update(environment='production'))
        with self.assertRaisesRegex(ValueError, 'manifest differs'):
            self.restore(changed)

    def test_changing_manifest_and_its_pin_still_fails_authentication(self):
        raw = self.mutated(lambda o: o['manifest']['scope'].update(namespace='other-account-space'))
        pin = custody.sha(custody.canonical(json.loads(raw)['manifest']))
        with self.assertRaisesRegex(ValueError, 'authentication failed'):
            self.restore(raw, pin=pin)

    def test_unknown_role_or_plaintext_in_public_manifest_rejected(self):
        raw = self.mutated(lambda o: o['manifest']['roles']['identity'].update(seed='not-public'))
        with self.assertRaisesRegex(ValueError, 'role differs'):
            self.restore(raw)

    def test_extra_top_level_field_rejected(self):
        with self.assertRaisesRegex(ValueError, 'format differs'):
            self.restore(self.mutated(lambda o: o.update(keys={'identity': 'unsafe'})))

    def test_duplicate_json_fields_rejected(self):
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            custody.restore(b'{"schema":1,"schema":2}', PASSWORD, expected_manifest_sha256=self.pin)

    def test_huge_kdf_work_factor_rejected_without_deriving(self):
        with patch.object(custody, 'derive', side_effect=AssertionError('must not derive')):
            with self.assertRaisesRegex(ValueError, 'bounded KDF'):
                self.restore(self.mutated(lambda o: o['kdf'].update(n=2**40)))

    def test_kdf_boolean_is_not_an_integer_parameter(self):
        with self.assertRaisesRegex(ValueError, 'bounded KDF'):
            self.restore(self.mutated(lambda o: o['kdf'].update(p=True)))

    def test_short_and_unbounded_passwords_rejected(self):
        for password in ('short', 'z' * 1025, b'not-a-string'):
            with self.subTest(length=len(password)), self.assertRaises(ValueError):
                self.restore(password=password)

    def test_size_limit_precedes_json_parse(self):
        with patch.object(custody.json, 'loads', side_effect=AssertionError('must not parse')):
            with self.assertRaisesRegex(ValueError, 'bounded backup'):
                self.restore(b' ' * (custody.MAX_BACKUP + 1))

    def test_wrong_nonce_length_rejected(self):
        with self.assertRaises(ValueError):
            self.restore(self.mutated(lambda o: o.update(nonce=custody.b64(b'x' * 13))))

    def test_missing_private_role_even_with_valid_encryption_rejected(self):
        obj = json.loads(self.raw)
        salt, nonce = custody.decode(obj['salt'], 32), custody.decode(obj['nonce'], 12)
        key = custody.derive(PASSWORD, salt)
        header = {k: v for k, v in obj.items() if k != 'ciphertext'}
        plain = json.loads(AESGCM(key).decrypt(nonce, base64.b64decode(obj['ciphertext']), canonical(header)))
        del plain['keys']['operator']
        # New nonce for this crafted malformed plaintext; never reuse the prior encryption nonce.
        nonce = os.urandom(12)
        header['nonce'] = custody.b64(nonce)
        raw = canonical(dict(header, ciphertext=custody.b64(AESGCM(key).encrypt(nonce, canonical(plain), canonical(header)))))
        with self.assertRaisesRegex(ValueError, 'roles differ'):
            self.restore(raw)

    def test_shared_key_between_issuer_roles_rejected(self):
        other = material()
        other.keys['native_issuer'] = other.keys['research_issuer']
        with self.assertRaisesRegex(ValueError, 'key reuse'):
            custody.seal(other, PASSWORD)

    def test_zero_material_and_bad_scope_rejected(self):
        other = material()
        other.keys['identity'] = bytes(32)
        with self.assertRaises(ValueError):
            custody.seal(other, PASSWORD)
        for update in ({'issuer': 'https://not-apple.example'}, {'namespace': '../test'}, {'environment': 'guess'}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                custody.generate(dict(SCOPE, **update))

    def test_new_generation_does_not_serve_as_restore(self):
        new = custody.generate(SCOPE)
        self.assertNotEqual(new.manifest(), self.original.manifest())
        with self.assertRaisesRegex(ValueError, 'manifest differs'):
            self.restore(custody.seal(new, PASSWORD))

    def test_runtime_payloads_exclude_operator_and_observer_and_match_issuers(self):
        restored = self.restore()
        payloads = restored.runtime_payloads()
        self.assertEqual(set(payloads), {'identity', 'native_issuer', 'research_issuer'})
        pins = custody.configuration_pins(restored.manifest(), self.pin)
        self.assertEqual(association_keys(pins['disposition']['research_associations']),
            (restored.public_keys()['native_issuer'], restored.public_keys()['research_issuer']))
        with tempfile.TemporaryDirectory() as tmp:
            for role in ('native', 'research'):
                path = Path(tmp).resolve() / (role + '.json')
                path.write_bytes(payloads[role + '_issuer'])
                env = dict(LITE_RESEARCH_LINK_FILE=str(path), LITE_RESEARCH_LINK_SHA256=pins[role]['issuer_sha256'])
                signer = issuer_settings(env, role)
                message = b'synthetic-restore-proof'
                self.assertEqual(signer.signer.sign(message),
                    Ed25519PrivateKey.from_private_bytes(self.original.keys[role + '_issuer']).sign(message))
                with self.assertRaises(ValueError):
                    issuer_settings(env, 'research' if role == 'native' else 'native')

    def test_private_disposition_authorities_are_separate_from_all_runtime_and_activation_signers(self):
        pins = custody.configuration_pins(self.original.manifest(), self.pin)
        keysets = [*pins['native']['controls'].values(), *pins['disposition']['signers'].values()]
        controls = {value for keys in keysets for value in keys.values()}
        self.assertEqual(len(controls), 4)
        research = pins['disposition']['research_associations']
        self.assertFalse(controls & {research['native_key'], research['research_key']})
        self.assertEqual(pins['disposition']['binding'], self.original.binding())
        for secret in self.original.keys.values():
            self.assertNotIn(custody.b64(secret).encode(), canonical(pins))

    def test_five_role_archive_remains_recoverable_without_generating_replacement_keys(self):
        old = custody.Material(dict(SCOPE), 'e'*32, 1793810000,
                {r: self.original.keys[r] for r in custody.FIRST_INSTALL_ROLES}, schema='lite-operational-materials/1')
        pin = custody.sha(canonical(old.manifest()))
        with patch.object(custody, 'generate', side_effect=AssertionError('unexpected regeneration')):
            recovered = custody.restore(custody.seal(old, PASSWORD), PASSWORD, expected_manifest_sha256=pin)
        self.assertEqual(recovered, old)
        self.assertEqual(recovered.runtime_payloads(), self.original.runtime_payloads())
        with self.assertRaisesRegex(ValueError, 'separate disposition signers'):
            custody.configuration_pins(recovered.manifest(), pin)

    def test_wrong_pin_or_duplicate_public_authority_refuses_configuration(self):
        with self.assertRaisesRegex(ValueError, 'manifest pin differs'):
            custody.configuration_pins(self.original.manifest(), 'a'*64)
        m = self.original.manifest()
        m['roles']['disposition_drain']['public_key'] = m['roles']['observer']['public_key']
        with self.assertRaisesRegex(ValueError, 'distinct public keys'):
            custody.configuration_pins(m, custody.sha(canonical(m)))

    def test_predeployment_check_rejects_cross_service_misbindings(self):
        original = material()
        original.scope['audience'] = 'com.woundai.lite'
        m = original.manifest()
        pin = custody.sha(canonical(m))
        pins = custody.configuration_pins(m, pin)
        native = dict(schema='lite-native-startup/3', deployment={'project': SCOPE['project']},
            namespace=SCOPE['namespace'], app={'bundle': 'com.woundai.lite'}, apple={'environment': 'Sandbox'},
            secrets={'identity': {'sha256': original.binding()['identity_key_sha256']}},
            controls=pins['native']['controls'])
        worker = dict(schema='lite-disposition-startup/3', deployment={'project': SCOPE['project']},
            media={'bucket': SCOPE['media_bucket']}, **pins['disposition'])
        def check(n, w, nsha=pins['native']['issuer_sha256'], rsha=pins['research']['issuer_sha256']):
            return custody.check_configuration(m, pin, n, w, nsha, rsha)
        self.assertTrue(check(native, worker)['custody_fields_verified'])
        changes = (
            lambda n,w: n['deployment'].update(project='different-project'),
            lambda n,w: n.update(namespace='other-scope'),
            lambda n,w: n['secrets']['identity'].update(sha256='0'*64),
            lambda n,w: n['controls'].update(observer_keys=n['controls']['operator_keys']),
            lambda n,w: w['binding'].update(identity_key_sha256='0'*64),
            lambda n,w: w['signers'].update(drain=n['controls']['observer_keys']),
            lambda n,w: w['research_associations'].update(research_key=w['research_associations']['native_key']),
            lambda n,w: w['media'].update(bucket='other-media'))
        for change in changes:
            n, w = copy.deepcopy(native), copy.deepcopy(worker)
            change(n, w)
            with self.assertRaises(ValueError):
                check(n, w)
        with self.assertRaisesRegex(ValueError, 'issuer mount differs'):
            check(native, worker, pins['research']['issuer_sha256'], pins['native']['issuer_sha256'])

    def test_restored_control_keys_verify_original_evidence_and_reject_role_swap(self):
        restored = self.restore()
        public = restored.public_keys()
        evidence = SignedEvidence({'operator': public['operator']}, {'observer': public['observer']}, clock=lambda: 100)
        for role, key_id in (('approval', 'operator'), ('observation', 'observer')):
            payload = dict(kind='lite-migration-' + role + '/1', issued_at=99, expires_at=110)
            signature = Ed25519PrivateKey.from_private_bytes(self.original.keys[key_id]).sign(canonical(payload))
            envelope = dict(key_id=key_id, payload=payload, signature=custody.b64(signature))
            self.assertEqual(evidence.verify(envelope, role), payload)
            with self.assertRaises(ValueError):
                SignedEvidence({'operator': public['operator']}, {'observer': public['operator']})

    def test_restore_uses_same_identity_and_spent_quota_without_reinitialization(self):
        def identity(m):
            # Invoke the existing production identity derivation, not a copied formula.
            events = object.__new__(AppleAccountEvents)
            events.secret, events.namespace = m.keys['identity'], m.scope['namespace']
            events.verifier = SimpleNamespace(issuer=m.scope['issuer'])
            return 'apple-' + events.identity('synthetic-apple-subject')
        original, restored = identity(self.original), identity(self.restore())
        self.assertEqual(original, restored)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'quota.sqlite'
            now = 1793810000
            ledger = UsageLedger(SQLiteStateStore(path), namespace='synthetic-quota', clock=lambda: now)
            ledger.initialize_day(original, now//86400, baseline_successes=0, baseline_attempts=0, manifest_sha256='a'*64)
            grant = DailyGrant(original, 2, 'b'*64, now, now+30)
            ticket = ledger.reserve(original, now//86400, 'c'*32, 'd'*64, grant)
            self.assertTrue(ledger.claim(ticket, 'e'*32))
            ledger.settle(ticket, 'e'*32, 'succeeded', 'f'*64)
            before = ledger.snapshot(original, now//86400, grant)
            self.assertEqual((before['used'], before['pending'], before['remaining']), (1, 0, 1))
            restarted = UsageLedger(SQLiteStateStore(path), namespace='synthetic-quota', clock=lambda: now)
            self.assertEqual(restarted.snapshot(restored, now//86400, grant), before)
            self.assertEqual(restarted.receipt(ticket), ledger.receipt(ticket))


class SignedLoginRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        identity_fixture.native.legacy.LinksTests.setUpClass()

    def test_second_device_login_after_restore_reuses_purchase_token_and_daily_balance(self):
        fixture = identity_fixture.IdentityFixture(self)
        original = material()
        original.scope['audience'] = fixture.verifier.audience
        store = SQLiteStateStore(fixture.h.f.root / 'custody-login.sqlite')
        audit = SQLiteStateStore(fixture.h.f.root / 'custody-audit.sqlite')

        def rebuild(key):
            return AccountIdentityBridge(runtime=fixture.h.r, verifier=fixture.verifier, store=store,
                audit_store=audit, namespace=original.scope['namespace'], identity_key=key)

        fixture.b = rebuild(original.keys['identity'])
        first = fixture.confirm(fixture.challenge('first-installation'))
        fixture.use(1, 'first-installation')
        ledger = fixture.h.f.ledger
        prior_usage = identity_fixture.rows(ledger.store)
        raw = custody.seal(original, PASSWORD)
        restored = custody.restore(raw, PASSWORD, expected_manifest_sha256=custody.sha(canonical(original.manifest())))
        fixture.b = rebuild(restored.keys['identity'])
        second = fixture.confirm(fixture.challenge('second-installation'))
        for field in ('account_sha256', 'app_account_token'):
            self.assertEqual(first[field], second[field])
        self.assertEqual(identity_fixture.rows(ledger.store), prior_usage)
        account = fixture.h.accounts.resolve('second-installation')
        balance = ledger.current_balance(account['subject'], fixture.h.accounts.grant(account, fixture.now()))
        self.assertEqual((balance['used'], balance['remaining'], balance['limit']), (1, 1, 2))
        with self.assertRaises(StateUnavailable):
            rebuild(custody.generate(original.scope).keys['identity'])
        self.assertEqual(identity_fixture.rows(ledger.store), prior_usage)
        fixture.use(2, 'second-installation')
        with self.assertRaises(UsageLimit):
            fixture.use(3, 'first-installation')


@unittest.skipUnless(os.name == 'posix', 'owner CLI requires POSIX file controls')
class StartupConfigurationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        shared.native.StartupPreflightTests.setUpClass()
        shared.disposition.StartupTests.setUpClass()

    def test_restored_pins_pass_both_startup_validators_and_the_public_cli_check(self):
        nf = shared.SharedNativeFixture(self)
        df = shared.SharedDispositionFixture(self)
        n, d = copy.deepcopy(nf.config), copy.deepcopy(df.c)
        project = n['deployment']['project']
        scope = dict(SCOPE, project=project, audience=n['app']['bundle'], media_bucket=d['media']['bucket'])
        m = custody.generate(scope)
        backup = custody.seal(m, PASSWORD)
        digest = custody.sha(canonical(m.manifest()))
        recovered = custody.restore(backup, PASSWORD, expected_manifest_sha256=digest)
        pins = custody.configuration_pins(recovered.manifest(), digest)
        n['namespace'] = pins['native']['namespace']
        n['controls'].update(pins['native']['controls'])
        n['secrets']['identity']['sha256'] = pins['native']['identity_sha256']
        identity_file = nf.secret_dir / 'identity' / n['secrets']['identity']['file']
        identity_file.write_bytes(recovered.runtime_payloads()['identity'])
        native_args = nf.arguments(n)
        native_args.pop('providers')
        native_args.pop('package')
        shared.native.startup.preflight(**native_args)
        d['deployment'].update(project=project, service_account='disposition@' + project + '.iam.gserviceaccount.com',
                               keyring=n['deployment']['keyring'])
        d['grant_keys'] = copy.deepcopy(n['grant_keys'])
        d.update(pins['disposition'])
        research = d['stores']['research']
        origin = custody.sha(canonical(['lite-research-origin/1', project, d['deployment']['project_number'],
                                        scope['media_bucket'], research['bucket'], research['prefix']]))
        raw = canonical(d)
        shared.disposition.startup.preflight(raw, expected_config=custody.sha(raw),
            expected_source=d['deployment']['source'], expected_image=d['deployment']['image'],
            expected_research_origin=origin, secrets=df.root, clock=lambda: df.now)
        self.assertEqual(nf.providers.calls, [])
        self.assertEqual(df.providers.calls, [])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            receipt_dir = root / 'receipt'
            receipt_dir.mkdir(mode=0o700)
            path, receipt = root / 'backup', receipt_dir / 'receipt'
            custody.save_pair(backup, path, receipt)
            (root / 'native.json').write_bytes(canonical(n))
            (root / 'worker.json').write_bytes(raw)
            command = [sys.executable, custody.__file__, 'check-config', '--backup', str(path),
                '--receipt', str(receipt), '--native', str(root/'native.json'), '--disposition', str(root/'worker.json'),
                '--native-issuer-sha256', pins['native']['issuer_sha256'],
                '--research-issuer-sha256', pins['research']['issuer_sha256']]
            run = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, timeout=15, check=True)
            decision = json.loads(run.stdout)
            self.assertEqual((decision['credential_roles'], decision['runtime_mounts']), (7, 3))
            self.assertTrue(decision['custody_fields_verified'])
            self.assertEqual(decision['manifest_sha256'], digest)


@unittest.skipUnless(os.name == 'posix', 'owner file custody requires POSIX permissions; no Windows ACL claim')
class CustodyFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = custody.seal(material(), PASSWORD)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.root.chmod(0o700)
        self.path = self.root / 'backup.json'
        self.receipt_dir = self.root / 'receipts'
        self.receipt_dir.mkdir(mode=0o700)
        self.receipt = self.receipt_dir / 'recovery.json'

    def test_exclusive_file_permissions_and_exact_readback(self):
        custody.write_new(self.path, self.raw)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(custody.read_private(self.path), self.raw)

    def test_existing_backup_is_never_overwritten(self):
        custody.write_new(self.path, self.raw)
        with self.assertRaises(FileExistsError):
            custody.write_new(self.path, self.raw)
        self.assertEqual(self.path.read_bytes(), self.raw)

    def test_receipt_can_verify_a_pair_after_create_process_exits(self):
        saved, pin = custody.save_pair(self.raw, self.path, self.receipt)
        self.assertEqual(saved, self.raw)
        recovered_pin = custody.check_receipt(custody.read_private(self.path), custody.read_private(self.receipt))
        self.assertEqual(recovered_pin, pin)
        self.assertEqual(custody.restore(saved, PASSWORD, expected_manifest_sha256=recovered_pin).keys, material().keys)

    def test_interruption_after_receipt_preserves_pins_and_blocks_overwrite_on_retry(self):
        with patch.object(custody, 'write_new', side_effect=OSError('injected before backup write')):
            with self.assertRaises(OSError):
                custody.save_pair(self.raw, self.path, self.receipt)
        receipt = self.receipt.read_bytes()
        self.assertEqual(json.loads(receipt), custody.recovery_receipt(self.raw))
        self.assertFalse(self.path.exists())
        with self.assertRaisesRegex(ValueError, 'destination already exists'):
            custody.save_pair(self.raw, self.path, self.receipt)
        self.assertEqual(receipt, self.receipt.read_bytes())

    def test_receipt_from_another_seal_cannot_verify_the_file(self):
        another = custody.seal(material(), PASSWORD)
        with self.assertRaisesRegex(ValueError, 'receipt differ'):
            custody.check_receipt(another, canonical(custody.recovery_receipt(self.raw)))

    def test_receipt_alias_or_same_directory_does_not_create_files(self):
        for target in (self.path, self.root / 'receipt'):
            with self.assertRaisesRegex(ValueError, 'separate directories'):
                custody.save_pair(self.raw, self.path, target)
            self.assertFalse(self.path.exists())

    def test_password_input_stops_if_terminal_would_echo(self):
        def echo_fallback(*args):
            warnings.warn('terminal unavailable', custody.getpass.GetPassWarning)
            self.fail('continued past an echoed password warning')
        with patch.object(custody.getpass, 'getpass', side_effect=echo_fallback):
            with self.assertRaisesRegex(ValueError, 'could not hide'):
                custody.password_prompt('Synthetic test')

    def test_scope_fifo_and_production_generation_refused(self):
        fifo = self.root / 'scope'
        os.mkfifo(fifo, 0o600)
        with self.assertRaisesRegex(ValueError, 'regular configuration'):
            custody.read_scope(fifo)
        with self.assertRaisesRegex(ValueError, 'owner sandbox'):
            custody.generate(dict(SCOPE, environment='production'))

    def test_plaintext_cannot_be_written_as_backup(self):
        with self.assertRaises(ValueError):
            custody.write_new(self.path, b'{"keys":"private"}')
        self.assertFalse(self.path.exists())

    def test_world_readable_directory_or_backup_rejected(self):
        self.root.chmod(0o755)
        with self.assertRaises(ValueError):
            custody.write_new(self.path, self.raw)
        self.root.chmod(0o700)
        custody.write_new(self.path, self.raw)
        self.path.chmod(0o644)
        with self.assertRaises(ValueError):
            custody.read_private(self.path)

    def test_symlink_or_hardlink_backup_rejected(self):
        custody.write_new(self.path, self.raw)
        link = self.root / 'link'
        link.symlink_to(self.path)
        with self.assertRaises(ValueError):
            custody.read_private(link)
        link.unlink()
        os.link(self.path, link)
        with self.assertRaises(ValueError):
            custody.read_private(self.path)

    def test_relative_path_and_symlink_parent_rejected(self):
        with self.assertRaises(ValueError):
            custody.write_new(Path('relative-backup'), self.raw)
        link = self.root / 'alias'
        actual = self.root / 'actual'
        actual.mkdir(mode=0o700)
        link.symlink_to(actual, target_is_directory=True)
        with self.assertRaises(ValueError):
            custody.write_new(link / 'backup', self.raw)

    def test_fifo_is_rejected_without_blocking(self):
        os.mkfifo(self.path, 0o600)
        with self.assertRaises(ValueError):
            custody.read_private(self.path)

    def test_disk_sync_failure_retains_only_encrypted_output(self):
        with patch.object(custody.os, 'fsync', side_effect=OSError('synthetic disk failure')):
            with self.assertRaises(OSError):
                custody.write_new(self.path, self.raw)
        self.assertEqual(self.path.read_bytes(), self.raw)

    def test_cli_rejects_piped_passwords_without_creating_backup(self):
        with patch.object(custody.sys, 'argv', ['custody', 'verify', '--backup', str(self.path),
                          '--expected-manifest-sha256', 'a'*64]), patch.object(custody.sys, 'stdin', io.StringIO('secret')):
            with self.assertRaisesRegex(ValueError, 'interactive owner'):
                custody.main()


if __name__ == '__main__':
    unittest.main()
