"""Independent encrypted-role, authenticity and file-failure boundaries (synthetic)."""
import json
from pathlib import Path
import secrets
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import lite_operational_materials as custody
import lite_role_custody as roles
from test_lite_operational_materials import material


class RoleCustodyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original=material();cls.password=secrets.token_urlsafe(32)
        cls.raw=roles.seal(cls.original,'operator',cls.password)
        cls.receipt=custody.canonical(roles.receipt(cls.raw))

    def restore(self,raw=None,receipt=None,role='operator',password=None):
        return roles.restore(self.raw if raw is None else raw,self.password if password is None else password,
            receipt_raw=self.receipt if receipt is None else receipt,required_role=role)

    def test_all_seven_exports_preserve_original_seeds_and_manifest(self):
        for role in custody.ROLES:
            with self.subTest(role=role):
                raw=roles.seal(self.original,role,self.password)
                restored=self.restore(raw,custody.canonical(roles.receipt(raw)),role)
                self.assertEqual(restored.keys,{role:self.original.keys[role]})
                self.assertEqual(restored.manifest(),self.original.manifest())
                self.assertEqual(restored.scope,self.original.scope)

    def test_plaintext_really_contains_only_selected_seed_not_seven_key_bundle(self):
        obj,encrypted=roles.envelope(self.raw);header={k:v for k,v in obj.items() if k!='ciphertext'}
        plain=AESGCM(custody.derive(self.password,custody.decode(obj['salt'],32))).decrypt(
            custody.decode(obj['nonce'],12),encrypted,custody.canonical(header))
        data=json.loads(plain)
        self.assertEqual(set(data),{'schema','role','manifest_sha256','seed'})
        self.assertEqual(data['seed'],custody.b64(self.original.keys['operator']))
        for role,seed in self.original.keys.items():
            if role!='operator':self.assertNotIn(custody.b64(seed).encode(),plain)

    def test_separate_password_and_random_envelope_do_not_change_authority(self):
        other=roles.seal(self.original,'operator',secrets.token_urlsafe(32))
        for key in ('salt','nonce','ciphertext'):self.assertNotEqual(json.loads(other)[key],json.loads(self.raw)[key])
        self.assertEqual(roles.receipt(other)['manifest_sha256'],roles.receipt(self.raw)['manifest_sha256'])

    def test_output_and_repr_contain_no_private_seed(self):
        restored=self.restore()
        for seed in self.original.keys.values():
            for value in (seed.hex(),custody.b64(seed)):
                self.assertNotIn(value,self.raw.decode());self.assertNotIn(value,self.receipt.decode())
                self.assertNotIn(value,repr(restored))

    def test_wrong_role_refused_before_kdf(self):
        with patch.object(custody,'derive',side_effect=AssertionError('KDF should not run')):
            for role in ('observer','disposition_operator','unknown'):
                with self.subTest(role=role),self.assertRaises(ValueError):self.restore(role=role)

    def test_wrong_password_refused(self):
        with self.assertRaisesRegex(ValueError,'authentication failed'):self.restore(password=secrets.token_urlsafe(32))

    def test_modified_receipt_and_resealed_receipt_rejected_before_kdf(self):
        other=custody.canonical(roles.receipt(roles.seal(self.original,'operator',self.password)))
        with patch.object(custody,'derive',side_effect=AssertionError('KDF should not run')):
            for value in (b'{}',other,self.receipt+b' invalid'):
                with self.subTest(receipt=value[:20]),self.assertRaises(ValueError):self.restore(receipt=value)

    def test_header_changes_cannot_rebind_role_even_with_recomputed_receipt(self):
        obj=json.loads(self.raw);obj['role']='observer';raw=custody.canonical(obj)
        with self.assertRaisesRegex(ValueError,'authentication failed'):
            self.restore(raw,custody.canonical(roles.receipt(raw)),'observer')

    def test_manifest_changes_cannot_rebind_scope_even_with_recomputed_receipt(self):
        obj=json.loads(self.raw);obj['manifest']['scope']['project']='other-project';raw=custody.canonical(obj)
        with self.assertRaises(ValueError):self.restore(raw,custody.canonical(roles.receipt(raw)))

    def test_kdf_escalation_and_ciphertext_bounds_rejected_before_kdf(self):
        variants=[]
        for key,value in [('n',2**30),('r',True)]:
            obj=json.loads(self.raw);obj['kdf'][key]=value;variants.append(custody.canonical(obj))
        obj=json.loads(self.raw);obj['ciphertext']='A'*2052;variants.append(custody.canonical(obj))
        with patch.object(custody,'derive',side_effect=AssertionError('unbounded work')):
            for raw in variants:
                with self.subTest(raw=raw[:30]),self.assertRaises(ValueError):self.restore(raw)

    def test_duplicate_json_and_additional_envelope_fields_rejected(self):
        obj=json.loads(self.raw);obj['extra']='unexpected'
        for raw in (custody.canonical(obj),self.raw[:-1]+b',"role":"operator"}'):
            with self.subTest(raw=raw[:20]),self.assertRaises(ValueError):self.restore(raw)

    def test_corrupt_ciphertext_with_matching_receipt_still_fails_authentication(self):
        obj=json.loads(self.raw);value=obj['ciphertext'];obj['ciphertext']=('B' if value[0]=='A' else 'A')+value[1:]
        raw=custody.canonical(obj)
        with self.assertRaisesRegex(ValueError,'authentication failed'):self.restore(raw,custody.canonical(roles.receipt(raw)))

    def test_encrypted_plaintext_seed_must_match_original_authority(self):
        obj,_=roles.envelope(self.raw);header={k:v for k,v in obj.items() if k!='ciphertext'}
        plain=custody.canonical(dict(schema='lite-role-secret/1',role='operator',
            manifest_sha256=roles.receipt(self.raw)['manifest_sha256'],seed=custody.b64(self.original.keys['observer'])))
        obj['ciphertext']=custody.b64(AESGCM(custody.derive(self.password,custody.decode(obj['salt'],32))).encrypt(
            custody.decode(obj['nonce'],12),plain,custody.canonical(header)))
        raw=custody.canonical(obj)
        with self.assertRaisesRegex(ValueError,'fingerprint differs'):self.restore(raw,custody.canonical(roles.receipt(raw)))

    def test_role_material_cannot_export_other_roles_or_new_complete_backup(self):
        restored=self.restore()
        for role in ('operator','observer'):
            with self.subTest(role=role),self.assertRaises(ValueError):roles.seal(restored,role,self.password)

    def test_compatibility_restores_combined_owner_source_without_new_identity(self):
        raw=custody.seal(self.original,self.password);receipt=custody.canonical(custody.recovery_receipt(raw))
        restored,pin=roles.restore_for_role(raw,self.password,receipt,'operator')
        self.assertEqual(restored.keys,self.original.keys)
        self.assertEqual(pin,roles.check_role_receipt(raw,receipt,'operator'))

    def test_bad_role_format_never_falls_back_to_combined_restore(self):
        with patch.object(custody,'restore',side_effect=AssertionError('unsafe fallback')):
            with self.assertRaises(ValueError):roles.restore_for_role(self.raw,self.password,b'{}','operator')

    def test_private_create_only_pair_and_wrong_parent_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();a=root/'backup';b=root/'receipt';a.mkdir(mode=0o700);b.mkdir(mode=0o700)
            backup=a/'operator.json';receipt=b/'operator.json'
            with self.assertRaises(ValueError):roles.save_pair(self.raw,backup,a/'receipt.json')
            self.assertFalse(backup.exists())
            roles.save_pair(self.raw,backup,receipt)
            self.assertEqual(backup.stat().st_mode&0o777,0o600);self.assertEqual(receipt.stat().st_mode&0o777,0o600)
            before=(backup.read_bytes(),receipt.read_bytes())
            with self.assertRaises(ValueError):roles.save_pair(self.raw,backup,receipt)
            self.assertEqual(before,(backup.read_bytes(),receipt.read_bytes()))

    def test_second_file_failure_preserves_receipt_without_false_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();a=root/'backup';b=root/'receipt';a.mkdir(mode=0o700);b.mkdir(mode=0o700)
            backup=a/'operator.json';receipt=b/'operator.json';write=custody._write_exclusive
            def fail(path,data):
                if path==backup:raise OSError('synthetic disk unavailable')
                return write(path,data)
            with patch.object(custody,'_write_exclusive',side_effect=fail),self.assertRaises(OSError):roles.save_pair(self.raw,backup,receipt)
            self.assertFalse(backup.exists());self.assertEqual(receipt.read_bytes(),self.receipt)
            with self.assertRaises(ValueError):roles.save_pair(self.raw,backup,receipt)

    def test_symlink_source_and_unsafe_permissions_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();target=root/'target.json';target.write_bytes(self.raw);target.chmod(0o600)
            link=root/'link.json';link.symlink_to(target)
            with self.assertRaises(ValueError):custody.read_private(link)
            target.chmod(0o644)
            with self.assertRaises(ValueError):custody.read_private(target)

    def test_invalid_role_seed_and_source_manifest_rejected(self):
        for role,seed in [('operator',self.original.keys['observer']),('unknown',b'x'*32),('operator',b'x')]:
            with self.subTest(role=role),self.assertRaises(ValueError):roles.RoleMaterial(self.original.manifest(),role,seed).validate()


if __name__=='__main__':unittest.main()
