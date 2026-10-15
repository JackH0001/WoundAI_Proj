import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class PortableRunnerTests(unittest.TestCase):
    def test_real_pytest_success_failure_empty_skipped_and_missing(self):
        runner = Path(__file__).parent / 'windows/run_3d_portable_tests.py'
        for source, expected, count in [
            ('def test_ok(): assert True', 0, 1),
            ('def test_bad(): assert False', 1, 1),
            ('value = 1', 1, 0),
            ('import pytest\n@pytest.mark.skip(reason="fixture")\ndef test_skip(): pass', 1, 1),
            (None, 1, 0),
        ]:
            with self.subTest(source=source), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); test = root/'test_fixture.py'; out = root/'out'; out.mkdir()
                (out/'pytest.xml').write_text('<testsuite tests="100" failures="0" errors="0" skipped="0"/>')
                if source is not None:
                    test.write_text(source, encoding='utf-8')
                result = subprocess.run([sys.executable, '-B', str(runner), '--test', str(test), '--out', str(out)], capture_output=True, timeout=30)
                self.assertEqual(result.returncode, expected, result.stdout.decode())
                summary = json.loads((out/'summary.json').read_text())
                self.assertEqual(summary['tests'], count)
                self.assertEqual(summary['passed'], expected == 0)


if __name__ == '__main__':
    unittest.main()
