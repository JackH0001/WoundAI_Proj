#!/usr/bin/env python3
"""Sign a previously verified MMH AAB locally; passwords stay out of argv/files.

Does not build, generate/alter keys, upload, change version or install an App.
Run interactively on the owner's computer. --check only validates the unsigned input.
"""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile


class SigningError(Exception):
    pass


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def check_input(bundle, expected):
    if not re.fullmatch(r'[0-9a-f]{64}', expected):
        raise SigningError('Expected SHA-256 must be 64 lowercase hex characters.')
    if not bundle.is_file() or bundle.suffix != '.aab' or digest(bundle) != expected:
        raise SigningError('AAB missing or content differs from the verified build; stop.')
    with zipfile.ZipFile(bundle) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise SigningError('Duplicate ZIP entries are not allowed.')
        if not {'BundleConfig.pb', 'base/manifest/AndroidManifest.xml'} <= set(names):
            raise SigningError('Expected Android App Bundle entries are missing.')
        if any(n.upper().startswith('META-INF/') and
               (n.upper().endswith(('.SF', '.RSA', '.DSA', '.EC')) or
                n.upper() == 'META-INF/MANIFEST.MF') for n in names):
            raise SigningError('Input already contains signing metadata; never replace its signature.')
        if archive.testzip() is not None:
            raise SigningError('AAB ZIP integrity failed.')


def tool(java_home, name):
    result = java_home / 'bin' / (name + ('.exe' if sys.platform == 'win32' else ''))
    if not result.is_file():
        raise SigningError('Missing JDK tool: ' + name)
    return str(result)


def run(args, env, timeout=180):
    # Do not print subprocess output: keytool/jarsigner diagnostics may contain
    # user-provided values. A failure preserves no purported release artifact.
    r = subprocess.run(args, env=env, stdin=subprocess.DEVNULL,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if r.returncode:
        raise SigningError(Path(args[0]).name + ' failed; check the local key/password and retry.')
    return r.stdout


def private_aliases(keytool, keystore, env):
    listing = run([keytool, '-J-Duser.language=en', '-J-Duser.country=US', '-list', '-v',
                   '-keystore', str(keystore), '-storepass:env', 'MMH_SIGN_STORE_PASS'], env).decode()
    # Only PrivateKeyEntry can sign. Do not offer a trusted certificate entry.
    return [m.group(1).strip() for m in re.finditer(
        r'^Alias name: (.+)\r?\n(?:(?!^Alias name: ).*\n)*?Entry type: PrivateKeyEntry',
        listing, re.M)]


def verify_signature(bundle, cert_sha256, java_home):
    # Read every payload entry, triggering JDK JarVerifier. Each entry must be
    # covered by exactly the selected upload certificate, not merely a valid
    # signature elsewhere in the archive. Android will re-sign with Play's key.
    source = Path(__file__).with_name('VerifyMmhBundle.java')
    result = run([tool(java_home, 'java'), str(source), str(bundle), cert_sha256], os.environ.copy())
    report = json.loads(result)
    if report.get('verified') is not True or report.get('payload_entries', 0) < 2:
        raise SigningError('Incomplete signature verification.')
    return report


def sign_bundle(bundle, expected, keystore, alias, store_pass, key_pass, output, java_home):
    check_input(bundle, expected)
    if not keystore.is_file() or keystore.is_symlink():
        raise SigningError('Existing regular keystore required; no key is created.')
    if output.exists() or output.with_suffix('.verification.json').exists():
        raise SigningError('Output already exists; choose a new path, never overwrite.')
    if not output.parent.is_dir() or output.suffix != '.aab':
        raise SigningError('Existing output directory and .aab filename required.')
    keytool = tool(java_home, 'keytool')
    env = os.environ.copy()
    env.update(MMH_SIGN_STORE_PASS=store_pass, MMH_SIGN_KEY_PASS=key_pass)
    try:
        with tempfile.TemporaryDirectory(prefix='mmh-sign-', dir=output.parent) as scratch:
            scratch = Path(scratch)
            staged = scratch / 'signed.aab'
            shutil.copyfile(bundle, staged)
            staged.chmod(0o600)
            # Re-check the copied snapshot, avoiding a changed source during copy.
            check_input(staged, expected)
            cert = run([keytool, '-exportcert', '-alias', alias, '-keystore', str(keystore),
                        '-storepass:env', 'MMH_SIGN_STORE_PASS'], env)
            fingerprint = hashlib.sha256(cert).hexdigest()
            run([tool(java_home, 'jarsigner'), '-keystore', str(keystore),
                 '-storepass:env', 'MMH_SIGN_STORE_PASS', '-keypass:env', 'MMH_SIGN_KEY_PASS',
                 str(staged), alias], env)
            # jarsigner replaces the file, resetting its mode on some JDKs.
            staged.chmod(0o600)
            verified = verify_signature(staged, fingerprint, java_home)
            report = dict(verified, input_sha256=expected, output_sha256=digest(staged),
                          upload_certificate_sha256=fingerprint,
                          play_app_signing_certificate=False, uploaded=False)
            # Atomic no-clobber publication on the same filesystem, only after verification.
            os.link(staged, output)
            receipt = output.with_suffix('.verification.json')
            fd = os.open(receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as stream:
                json.dump(report, stream, indent=2)
                stream.write('\n')
            return report
    finally:
        env.pop('MMH_SIGN_STORE_PASS', None)
        env.pop('MMH_SIGN_KEY_PASS', None)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--sha256', required=True)
    p.add_argument('--keystore', type=Path)
    p.add_argument('--output', type=Path)
    p.add_argument('--java-home', type=Path, default=Path(os.environ.get('JAVA_HOME', '/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home')))
    p.add_argument('--check', action='store_true')
    args = p.parse_args()
    check_input(args.input, args.sha256)
    if args.check:
        print('Unsigned AAB content and ZIP integrity match; no credentials read, no signing/upload.')
        return
    if not args.keystore or not args.output:
        p.error('--keystore and --output required for signing')
    if not sys.stdin.isatty():
        raise SigningError('Run in your local interactive Terminal; never paste passwords into chat.')
    store_pass = getpass.getpass('既有 upload keystore 密碼（不顯示）：')
    env = os.environ.copy()
    env['MMH_SIGN_STORE_PASS'] = store_pass
    try:
        aliases = private_aliases(tool(args.java_home, 'keytool'), args.keystore, env)
    finally:
        env.pop('MMH_SIGN_STORE_PASS', None)
    if not aliases:
        raise SigningError('No private-key entry found in this keystore.')
    for index, name in enumerate(aliases, 1):
        print(str(index) + ': ' + name)
    choice = input('選擇既有上傳金鑰編號：').strip()
    if not choice.isdigit() or not 1 <= int(choice) <= len(aliases):
        raise SigningError('Invalid key selection; nothing signed.')
    key_pass = getpass.getpass('既有 key 密碼（留白＝沿用 keystore 密碼）：') or store_pass
    try:
        report = sign_bundle(args.input, args.sha256, args.keystore, aliases[int(choice)-1],
                             store_pass, key_pass, args.output, args.java_home)
    finally:
        store_pass = key_pass = None
    print('簽署與全部檔案驗證完成：' + str(args.output))
    print('Upload certificate SHA-256: ' + report['upload_certificate_sha256'])
    print('尚未上傳 Play；此指紋不是 Play App Signing SHA-1。')


if __name__ == '__main__':
    try:
        main()
    except SigningError as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError, zipfile.BadZipFile, subprocess.TimeoutExpired):
        # Suppress raw subprocess/environment or secret-bearing user input.
        print('簽署未完成；請核對輸入檔、金鑰選項與本機密碼。未上傳商店。', file=sys.stderr)
        sys.exit(1)
