"""Run opt-in iOS simulator <-> Python loopback contract tests.

Requires Xcode, XcodeGen, PyYAML and the existing backend test dependencies.
Creates a separate QA App/project and keeps logs/results. No device/cloud access.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import yaml
from lite_native_wire_fixture import WireFixture, ROOT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--simulator', required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    os.chmod(out, 0o700)
    qa = out / 'qa'; ios = qa / 'iOS'
    tracked = subprocess.check_output(['git', 'ls-files', '-z', 'iOS', 'site'], cwd=ROOT).decode().split('\0')
    binding = {}
    for name in filter(None, tracked):
        source = ROOT / name
        if not source.is_file(): continue
        dest = qa / name; dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
        binding[name] = hashlib.sha256(source.read_bytes()).hexdigest()
    for name in ('tools/run_lite_native_wire.py', 'tools/lite_native_wire_fixture.py',
                 'tools/fixtures/LiteNativeWireTests.swift'):
        binding[name] = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
    tests = ios / 'WireTests'; tests.mkdir()
    shutil.copyfile(ROOT / 'tools/fixtures/LiteNativeWireTests.swift', tests / 'LiteNativeWireTests.swift')
    project = yaml.safe_load((ios / 'project.yml').read_text())
    project['name'] = 'LiteNativeWireQA'
    project['targets'] = {k: v for k, v in project['targets'].items() if k in ('WoundLite', 'WoundLiteTests')}
    project['targets']['WoundLite']['settings']['base']['PRODUCT_BUNDLE_IDENTIFIER'] = 'com.woundai.nativewireqa20261113'
    project['targets']['WoundLite']['settings']['base']['CODE_SIGN_ENTITLEMENTS'] = ''
    project['targets']['WoundLiteTests']['sources'] = [{'path': 'WireTests'}]
    project['schemes'] = {'NativeWire': {'build': {'targets': {'WoundLite': 'all', 'WoundLiteTests': ['test']}},
                         'test': {'targets': ['WoundLiteTests'], 'config': 'Release'}}}
    (ios / 'project.wire.yml').write_text(yaml.safe_dump(project, sort_keys=False))
    # ATS exception is QA-only and accepts no remote target in the test adapter.
    import plistlib
    info = ios / 'WoundLite/Info.plist'
    payload = plistlib.loads(info.read_bytes())
    payload['NSAppTransportSecurity'] = {'NSAllowsLocalNetworking': True, 'NSAllowsArbitraryLoads': True}
    info.write_bytes(plistlib.dumps(payload))
    (out / 'source-binding.json').write_text(json.dumps(dict(
        commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT).decode().strip(),
        files=binding, qa_changes=['bundle ID', 'test source only', 'loopback ATS exception'],
        physical_device_accessed=False), indent=2) + '\n')
    fixture = WireFixture()
    config = fixture.start()
    config_path = tests / 'native-wire.json'
    config_path.write_text(json.dumps(config)); os.chmod(config_path, 0o600)
    try:
        subprocess.run(['xcodegen', 'generate', '--spec', 'project.wire.yml'], cwd=ios, check=True,
                       stdout=subprocess.DEVNULL)
        command = ['xcodebuild', 'test', '-project', 'LiteNativeWireQA.xcodeproj', '-scheme', 'NativeWire',
                   '-configuration', 'Release', '-destination', f'platform=iOS Simulator,id={args.simulator}',
                   '-parallel-testing-enabled', 'NO', 'CODE_SIGN_IDENTITY=-', 'ENABLE_TESTABILITY=YES',
                   'ONLY_ACTIVE_ARCH=YES', '-derivedDataPath', str(out / 'derived'),
                   '-resultBundlePath', str(out / 'wire.xcresult')]
        with (out / 'xcodebuild.log').open('x') as log:
            result = subprocess.run(command, cwd=ios, stdout=log, stderr=subprocess.STDOUT, timeout=900)
        for kind in ('summary', 'tests'):
            with (out / (kind + '.json')).open('w') as file:
                subprocess.run(['xcrun', 'xcresulttool', 'get', 'test-results', kind, '--path',
                                str(out / 'wire.xcresult')], stdout=file, check=True)
        (out / 'backend-observations.json').write_text(json.dumps(dict(
            schema='lite-native-wire/1', completed=fixture.completed, last=fixture.snapshot() if fixture.h else None,
            apple_sandbox=False, live_cloud=False, inference='synthetic',
            security='synthetic App Attest signatures; loopback QA forwarding'), indent=2) + '\n')
        if result.returncode: raise SystemExit(result.returncode)
        summary = json.loads((out / 'summary.json').read_text())
        if summary.get('passedTests') != 7 or summary.get('failedTests') != 0 or summary.get('skippedTests') != 0:
            raise RuntimeError('expected exactly seven tests without skips')
        if len(fixture.completed) != 7: raise RuntimeError('missing backend evidence')
        print(json.dumps(dict(passed=True, ios_tests=7, backend_scenarios=7, output=str(out))))
    finally:
        fixture.close()
        # Ephemeral fixture bearer, never an operational or Apple key. Erase it
        # from the project and derived App after the server is stopped.
        config_path.unlink(missing_ok=True)
        for path in (out / 'derived').rglob('native-wire.json'): path.unlink()


if __name__ == '__main__': main()
