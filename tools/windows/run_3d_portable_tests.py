"""Run the portable 3D pytest suite and reject zero executed/passing tests."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET


def run(test, out):
    test, out = Path(test).resolve(), Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    report = out / 'pytest.xml'
    report.unlink(missing_ok=True)  # Never accept evidence from an earlier run.
    result = dict(passed=False, tests=0, failures=0, skipped=0, exit_code=2)
    try:
        if not test.is_file():
            raise ValueError('3D test file missing')
        env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
        with (out / 'pytest.log').open('w', encoding='utf-8') as log:
            proc = subprocess.run([sys.executable, '-B', '-m', 'pytest', str(test), '-q',
                                   '-p', 'no:cacheprovider', '--junitxml', str(report)],
                                  cwd=test.parent, env=env, stdout=log, stderr=subprocess.STDOUT,
                                  timeout=300)
        result['exit_code'] = proc.returncode
        root = ET.parse(report).getroot()
        suites = [root] if root.tag == 'testsuite' else list(root.iter('testsuite'))
        for suite in suites:
            result['tests'] += int(suite.get('tests', '0'))
            result['failures'] += int(suite.get('failures', '0')) + int(suite.get('errors', '0'))
            result['skipped'] += int(suite.get('skipped', '0'))
        result['passed'] = (proc.returncode == 0 and result['failures'] == 0
                            and result['tests'] > result['skipped'])
    except (OSError, ValueError, ET.ParseError, subprocess.TimeoutExpired) as error:
        result['error'] = type(error).__name__
    (out / 'summary.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--test', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    sys.exit(run(args.test, args.out))
