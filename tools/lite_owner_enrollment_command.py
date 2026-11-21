"""Prepare an owner-only registration plan and its storage scope on the operator's Mac."""
import argparse
import hashlib
import os
import plistlib
import re
import sys
import time
import zipfile
from pathlib import Path

from audit_chain_contract import loads_json_object_strict
from lite_attest_http import CHALLENGE_PATH, REGISTER_PATH
from lite_native_schema import exact, text
from lite_operational_materials import _write_exclusive, canonical, read_private, sha
from lite_owner_enrollment import checked_config

BUNDLE = 'com.woundai.lite'
INFO_PATH = 'Payload/WoundLite.app/Info.plist'


class PreparationError(ValueError):
    """A fixed operator-facing message containing no device or file identifiers."""


def checked_ipa(path, expected):
    # Pin the open file and keep using the same handle if its path is replaced.
    with path.open('rb') as stream:
        before = os.fstat(stream.fileno())
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
        if digest.hexdigest() != text(expected, r'[0-9a-f]{64}'):
            raise PreparationError('The IPA differs from the reviewed export digest.')
        stream.seek(0)
        with zipfile.ZipFile(stream) as archive:
            entries = [e for e in archive.infolist() if e.filename == INFO_PATH]
            if len(entries) != 1 or not 0 < entries[0].file_size <= 256 * 1024:
                raise PreparationError('The IPA does not contain a single bounded owner app Info.plist.')
            info = plistlib.loads(archive.read(entries[0]))
        after = os.fstat(stream.fileno())
        if (before.st_size, before.st_ctime_ns) != (after.st_size, after.st_ctime_ns):
            raise PreparationError('The IPA changed during verification.')
        return info


def prepare(native, template, expected, shared_text, ipa, now, *, review_existing=False):
    if sha(template) != text(expected, r'[0-9a-f]{64}'):
        raise PreparationError('The template differs from its reviewed SHA-256.')
    cfg = exact(loads_json_object_strict(template.decode()),
                'schema native_sha256 source image ipa_sha256 app_id key_id_sha256 service_account starts until')
    if cfg['schema'] != 'lite-owner-enrollment-template/1':
        raise PreparationError('Select the owner enrollment template.')
    # ShareLink exports lowercase hex as text. A saved text file can add one trailing line ending.
    key = re.fullmatch(rb'([0-9a-f]{64})(?:\r?\n)?', shared_text)
    if key is None:
        raise PreparationError('Select the text shared by the owner app, containing 64 lowercase hex characters.')
    if sha(bytes.fromhex(key[1].decode())) != text(cfg['key_id_sha256'], r'[0-9a-f]{64}'):
        raise PreparationError('The device registration ID differs from the one reviewed for this window.')
    plan = dict(schema='lite-owner-enrollment/1', key_id=key[1].decode(),
                **{k:cfg[k] for k in ('native_sha256','service_account','starts','until')})
    raw = canonical(plan)
    _, deployment, location, policy = checked_config(native, raw, config_sha=cfg['native_sha256'],
        plan_sha=sha(raw), source=cfg['source'], image=cfg['image'])
    if not review_existing and now >= plan['until']:
        raise PreparationError('The registration window has ended.')
    info = checked_ipa(ipa, cfg['ipa_sha256'])
    origin = 'https://woundai-lite-native-sandbox-' + deployment['project_number'] + '.asia-east1.run.app'
    if (info.get('CFBundleIdentifier') != BUNDLE or info.get('DTPlatformName') != 'iphoneos'
            or info.get('TFInternalTestingOnly') is not True or info.get('LiteOwnerReviewEnabled') != 'YES'):
        raise PreparationError('Select the internal-only device IPA with owner enrollment enabled.')
    if (info.get('LiteNativeSandboxOrigin') != origin or policy.app_id != cfg['app_id']
            or policy.allowed_bundle_versions != frozenset({info.get('CFBundleVersion')})):
        raise PreparationError('The owner IPA and the reviewed service origin, team or build differ.')
    bucket, generation, prefix, budget = location
    budget_key = sha(b'woundlite/global-request-budget/1')
    scope = dict(schema='lite-owner-enrollment-scope/1', plan_sha256=sha(raw), template_sha256=expected,
                 native_sha256=cfg['native_sha256'], source=cfg['source'], image=cfg['image'],
                 ipa_sha256=cfg['ipa_sha256'], build=info['CFBundleVersion'], application_id=policy.app_id,
                 project=deployment['project'], project_number=deployment['project_number'],
                 region='asia-east1', service_account=deployment['service_account'], bucket=bucket,
                 metageneration=generation, starts=plan['starts'], until=plan['until'],
                 objects=[prefix+'/'+plan['key_id']+'.json', budget+'/'+budget_key+'.json'],
                 bucket_permissions=['storage.buckets.get'],
                 object_permissions=['storage.objects.get','storage.objects.create','storage.objects.delete'],
                 routes=[CHALLENGE_PATH, REGISTER_PATH])
    return raw, scope


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare','scope'))
    for name in ('template','native-config','reference','ipa','output'):
        parser.add_argument('--'+name, required=True, type=Path)
    parser.add_argument('--template-sha256', required=True)
    parser.add_argument('--plan', type=Path, help='previously prepared private plan; required for scope')
    args = parser.parse_args(argv)
    if (args.action == 'scope') != (args.plan is not None):
        parser.error('--plan is required only for scope')
    try:
        with args.native_config.open('rb') as source:
            native = source.read(256 * 1024 + 1)
        if len(native) > 256 * 1024:
            raise ValueError('native configuration too large')
        raw, scope = prepare(native, read_private(args.template), args.template_sha256,
                             read_private(args.reference), args.ipa, int(time.time()),
                             review_existing=args.action == 'scope')
        if args.action == 'scope':
            if raw != read_private(args.plan):
                raise PreparationError('The saved plan differs from the reconstructed mount bytes.')
            now = int(time.time())
            scope['window_state'] = ('expired' if now >= scope['until'] else
                                     'scheduled' if now < scope['starts'] else 'open')
            raw = canonical(scope)
        elif int(time.time()) >= scope['until']:
            raise PreparationError('The registration window has ended.')
        _write_exclusive(args.output, raw)
        if read_private(args.output) != raw:
            raise ValueError('private output readback differs')
    except PreparationError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except FileExistsError:
        print('Output already exists. Preserve it and use scope to verify the saved plan.', file=sys.stderr)
        return 1
    except Exception:
        print('Preparation stopped. Check the fixed inputs, owner IPA, time window and unused output path.', file=sys.stderr)
        return 1
    print('Private file saved. SHA-256', sha(raw))
    return 0


if __name__ == '__main__':
    sys.exit(main())
