"""Shared fail-closed release checks used by both portable validation commands."""
from pathlib import Path
import json
import re
import subprocess
import sys


def backend_profiles(root):
    guard = Path(root) / 'engineering/phase2/test_backend_url_parity.py'
    if not guard.is_file():
        return False, 'missing backend profile guard: ' + str(guard)
    try:
        result = subprocess.run([sys.executable, '-B', str(guard)], cwd=root,
                                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, 'backend profile guard did not complete: ' + type(exc).__name__
    report = result.stdout + result.stderr
    ran = re.search(r'^Ran ([1-9][0-9]*) tests? in ', report, re.M)
    complete = re.search(r'^OK\s*$', report, re.M)
    return result.returncode == 0 and ran is not None and complete is not None, report


def version_pair(android, project):
    """Check the medical target's effective version, not the independent Lite target."""
    android_values = re.findall(r'^versionCode=(\d+)\s*$', android, re.M)
    base = re.search(r'^settings:\s*\n(.*?)(?=^\S|\Z)', project, re.M | re.S)
    global_values = re.findall(r'^    CURRENT_PROJECT_VERSION:\s*"?(\d+)"?\s*$',
                               base.group(1) if base else '', re.M)
    medical = re.search(r'^  WoundMeasurementApp:\s*\n(.*?)(?=^  [A-Za-z]\w*:|^\S|\Z)', project, re.M | re.S)
    all_global_versions = re.findall(r'^\s*CURRENT_PROJECT_VERSION:', base.group(1) if base else '', re.M)
    if len(android_values) != 1 or len(global_values) != 1 or len(all_global_versions) != 1 or not medical:
        raise ValueError('cannot resolve unique Android and medical iOS build numbers')
    # Do not silently ignore a target/configuration override introduced later.
    if re.search(r'^\s*CURRENT_PROJECT_VERSION:', medical.group(1), re.M):
        raise ValueError('medical target version override requires explicit parser review')
    return int(android_values[0]), int(global_values[0])


def version_exception(markdown, android, ios):
    """Return an explicit matching exception; malformed or stale registrations reject."""
    tag = '<!-- release-version-exception -->'
    if tag not in markdown:
        if android != ios:
            raise ValueError(f'undeclared version mismatch: Android={android}, iOS medical={ios}')
        return None
    blocks = re.findall(re.escape(tag) + r'\s*```json\s*\n(.*?)\n```', markdown, re.S)
    if markdown.count(tag) != 1 or len(blocks) != 1:
        raise ValueError('invalid/duplicate version exception declaration')
    try:
        value = json.loads(blocks[0])
    except ValueError as exc:
        raise ValueError('version exception is not valid JSON') from exc
    keys = {'android', 'ios_medical', 'reason', 'alignment_trigger'}
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError('version exception requires exact versions, reason and alignment_trigger')
    if any(type(value[k]) is not int or value[k] <= 0 for k in ('android', 'ios_medical')):
        raise ValueError('exception versions must be positive integers')
    if any(not isinstance(value[k], str) or not value[k].strip() for k in ('reason', 'alignment_trigger')):
        raise ValueError('exception reason/alignment_trigger cannot be empty')
    if android == ios or (value['android'], value['ios_medical']) != (android, ios):
        raise ValueError('stale version exception; update or remove it before release')
    return value
