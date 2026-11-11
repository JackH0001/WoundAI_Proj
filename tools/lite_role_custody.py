"""Offline single-role custody derived from an existing verified backup.

Never generates replacement identity/signing seeds, exports plaintext, contacts
providers, or retires the original combined backup. Separation of actual people
and the combined-backup recovery path still require an operational handoff.
"""
import argparse
from dataclasses import dataclass, field
import json
from pathlib import Path
import secrets
import sys

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import lite_operational_materials as custody

require=custody.require
SCHEMA='lite-role-backup/1'


@dataclass(frozen=True)
class RoleMaterial:
    public_manifest: dict
    role: str
    seed: bytes = field(repr=False)

    @property
    def scope(self):return dict(self.public_manifest['scope'])

    @property
    def keys(self):return {self.role:self.seed}

    def validate(self):
        custody.validate_manifest(self.public_manifest)
        require(self.public_manifest['schema']=='lite-operational-materials/2', 'seven-role source required')
        require(self.role in custody.ROLES and type(self.seed) is bytes and len(self.seed)==32,
                'one exact role required')
        expected=self.public_manifest['roles'][self.role]
        require(custody.sha(self.seed)==expected['sha256'], 'role fingerprint differs')
        if self.role!='identity':
            public=Ed25519PrivateKey.from_private_bytes(self.seed).public_key().public_bytes_raw()
            require(custody.b64(public)==expected['public_key'], 'role authority differs')

    def manifest(self):
        self.validate()
        return json.loads(custody.canonical(self.public_manifest))


def envelope(raw):
    obj=custody.parse(raw)
    require(set(obj)=={'schema','manifest','role','kdf','salt','nonce','cipher','ciphertext'}
            and obj['schema']==SCHEMA and obj['cipher']=='AES-256-GCM', 'role backup format differs')
    custody.validate_manifest(obj['manifest'])
    require(obj['manifest']['schema']=='lite-operational-materials/2' and obj['role'] in custody.ROLES,
            'seven-role source and exact role required')
    require(custody.canonical(obj['kdf'])==custody.canonical(custody.KDF), 'fixed bounded KDF required')
    custody.decode(obj['salt'],32);custody.decode(obj['nonce'],12)
    text=obj['ciphertext']
    require(type(text) is str and 24<=len(text)<=2048,'bounded role ciphertext required')
    import base64
    encrypted=base64.b64decode(text,validate=True)
    require(custody.b64(encrypted)==text,'noncanonical role ciphertext')
    return obj,encrypted


def seal(material, role, password):
    material.validate()
    require(type(material) is custody.Material and material.schema=='lite-operational-materials/2',
            'existing complete source required for role export')
    require(role in custody.ROLES,'unknown role')
    manifest=material.manifest()
    salt,nonce=secrets.token_bytes(32),secrets.token_bytes(12)
    header=dict(schema=SCHEMA,manifest=manifest,role=role,kdf=dict(custody.KDF),
                salt=custody.b64(salt),nonce=custody.b64(nonce),cipher='AES-256-GCM')
    plain=custody.canonical(dict(schema='lite-role-secret/1',role=role,
        manifest_sha256=custody.sha(custody.canonical(manifest)),seed=custody.b64(material.keys[role])))
    encrypted=AESGCM(custody.derive(password,salt)).encrypt(nonce,plain,custody.canonical(header))
    return custody.canonical(dict(header,ciphertext=custody.b64(encrypted)))


def receipt(raw):
    obj,_=envelope(raw)
    return dict(schema='lite-role-recovery-receipt/1',role=obj['role'],scope=obj['manifest']['scope'],
        manifest_sha256=custody.sha(custody.canonical(obj['manifest'])),backup_sha256=custody.sha(raw),bytes=len(raw))


def check_receipt(raw, receipt_raw, required_role):
    require(required_role in custody.ROLES,'required role missing')
    expected=receipt(raw)
    require(expected['role']==required_role,'backup cannot supply required role')
    require(custody.canonical(custody.parse(receipt_raw))==custody.canonical(expected),
            'role backup and independent receipt differ')
    return expected['manifest_sha256']


def restore(raw, password, *, receipt_raw, required_role):
    pin=check_receipt(raw,receipt_raw,required_role)
    obj,encrypted=envelope(raw)
    header={k:v for k,v in obj.items() if k!='ciphertext'}
    try:
        plain=AESGCM(custody.derive(password,custody.decode(obj['salt'],32))).decrypt(
            custody.decode(obj['nonce'],12),encrypted,custody.canonical(header))
    except InvalidTag:
        raise custody.CustodyError('role backup authentication failed') from None
    data=custody.parse(plain)
    require(set(data)=={'schema','role','manifest_sha256','seed'} and data['schema']=='lite-role-secret/1'
            and data['role']==required_role and data['manifest_sha256']==pin,'decrypted role scope differs')
    material=RoleMaterial(obj['manifest'],required_role,custody.decode(data['seed'],32))
    material.validate()
    return material


def save_pair(raw, backup_path, receipt_path):
    backup,out=custody.available_destination(backup_path),custody.available_destination(receipt_path)
    require(not backup.parent.samefile(out.parent),'choose separate directories for role backup and receipt')
    pins=custody.canonical(receipt(raw))
    custody._write_exclusive(out,pins)
    require(custody.read_private(out)==pins,'role receipt readback differs')
    custody._write_exclusive(backup,raw)
    saved=custody.read_private(backup)
    check_receipt(saved,custody.read_private(out),receipt(raw)['role'])
    return saved


def restore_for_role(raw, password, receipt_raw, required_role):
    """Compatibility for owner-only combined backups; no format fallback on errors."""
    if custody.parse(raw).get('schema')==SCHEMA:
        m=restore(raw,password,receipt_raw=receipt_raw,required_role=required_role)
    else:
        pin=custody.check_receipt(raw,receipt_raw)
        m=custody.restore(raw,password,expected_manifest_sha256=pin)
        require(required_role in m.keys,'required role missing')
    return m,custody.sha(custody.canonical(m.manifest()))


def check_role_receipt(raw, receipt_raw, required_role):
    """Reject a wrong role/receipt before requesting any recovery passphrase."""
    if custody.parse(raw).get('schema')==SCHEMA:
        return check_receipt(raw,receipt_raw,required_role)
    pin=custody.check_receipt(raw,receipt_raw)
    require(required_role in custody.envelope(raw)[0]['manifest']['roles'],'required role missing')
    return pin


def main():
    p=argparse.ArgumentParser(description=__doc__)
    subs=p.add_subparsers(dest='action',required=True)
    for action in ('export-role','verify-role'):
        q=subs.add_parser(action)
        q.add_argument('--backup',required=True,type=Path)
        q.add_argument('--receipt',required=True,type=Path)
        q.add_argument('--role',required=True,choices=sorted(custody.ROLES))
        if action=='export-role':
            q.add_argument('--output',required=True,type=Path)
            q.add_argument('--role-receipt',required=True,type=Path)
    a=p.parse_args()
    require(sys.stdin.isatty(),'interactive owner terminal required; no piped passwords')
    raw=custody.read_private(a.backup);original_receipt=custody.read_private(a.receipt)
    if a.action=='export-role':
        pin=custody.check_receipt(raw,original_receipt)
        manifest=custody.envelope(raw)[0]['manifest']
        require(manifest['schema']=='lite-operational-materials/2','seven-role source required')
        backup=custody.available_destination(a.output);output_receipt=custody.available_destination(a.role_receipt)
        require(not backup.parent.samefile(output_receipt.parent),'choose separate backup and receipt directories')
        print(json.dumps(dict(scope=manifest['scope'],role=a.role,manifest_sha256=pin,
            combined_backup_retained=True,actual_holder_separation_verified=False)),flush=True)
        require(input('Type EXPORT_ONE_ROLE to preserve the original and export this role only: ')==
                'EXPORT_ONE_ROLE','export cancelled')
        source_password=custody.password_prompt('Original backup recovery passphrase: ')
        material=custody.restore(raw,source_password,expected_manifest_sha256=pin)
        password=custody.password_prompt('Independent role recovery passphrase: ')
        require(password!=source_password,'role passphrase must differ from the original backup')
        require(password==custody.password_prompt('Repeat role recovery passphrase: '),'passphrases differ')
        output=seal(material,a.role,password)
        out_receipt=custody.canonical(receipt(output))
        restore(output,password,receipt_raw=out_receipt,required_role=a.role)
        saved=save_pair(output,backup,output_receipt)
        restored=restore(saved,password,receipt_raw=custody.read_private(output_receipt),required_role=a.role)
        require(custody.read_private(a.backup)==raw and custody.read_private(a.receipt)==original_receipt,
                'source changed during export; preserve all files')
    else:
        check_receipt(raw,original_receipt,a.role)
        restored=restore(raw,custody.password_prompt('Role recovery passphrase: '),
                         receipt_raw=original_receipt,required_role=a.role)
    print(json.dumps(dict(verified=True,role=restored.role,
        manifest_sha256=custody.sha(custody.canonical(restored.manifest())),
        restored_roles=1,cloud_calls=0,original_retired=False,actual_holder_separation_verified=False)))


if __name__=='__main__':
    try:main()
    except Exception as error:
        print(json.dumps(dict(status='stopped',error_type=type(error).__name__,
            action='preserve all files; no automatic export or execution')),file=sys.stderr)
        raise SystemExit(1)
