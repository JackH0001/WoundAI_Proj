"""Portable, offline regression cases for the release validation entry points."""
from pathlib import Path
import json
import shutil
import tempfile
import unittest

from mobile_release_contract import backend_profiles, version_exception, version_pair

ROOT = Path(__file__).resolve().parents[1]


class MobileReleaseContractTests(unittest.TestCase):
    def setUp(self):
        # Fixed parser fixtures: a real build-number bump must not turn a
        # mutation into a no-op. The live repo has a separate integration case.
        self.android = 'versionCode=22\n'
        self.project = ('settings:\n  base:\n    CURRENT_PROJECT_VERSION: "25"\n'
                        'targets:\n  WoundMeasurementApp:\n    type: application\n'
                        '  WoundLite:\n    settings:\n      base:\n        CURRENT_PROJECT_VERSION: "28"\n')
        self.doc = '<!-- release-version-exception -->\n```json\n' + json.dumps({
            'android':22, 'ios_medical':25, 'reason':'Independent release schedules',
            'alignment_trigger':'Recheck both exact versions at next release'}) + '\n```'

    def test_current_repository_has_a_valid_explicit_pair(self):
        pair = version_pair((ROOT/'Android/version.properties').read_text(),
                            (ROOT/'iOS/project.yml').read_text())
        version_exception((ROOT/'docs/PARITY.md').read_text(), *pair)

    def test_exact_declared_pair_is_accepted(self):
        pair = version_pair(self.android, self.project)
        self.assertEqual(pair, (22, 25))
        self.assertIsNotNone(version_exception(self.doc, *pair))

    def test_lite_build_does_not_change_medical_pair(self):
        suffix = self.project.index('  WoundLite:')
        changed = self.project[:suffix] + self.project[suffix:].replace('CURRENT_PROJECT_VERSION: "28"', 'CURRENT_PROJECT_VERSION: "999"')
        self.assertEqual(version_pair(self.android, changed), (22, 25))

    def test_upgrade_of_either_platform_expires_exception(self):
        for pair in [(23, 25), (22, 26), (25, 25)]:
            with self.subTest(pair=pair), self.assertRaises(ValueError):
                version_exception(self.doc, *pair)

    def test_undeclared_mismatch_and_bad_declarations_fail(self):
        with self.assertRaises(ValueError): version_exception('', 22, 25)
        self.assertIsNone(version_exception('', 25, 25))
        for doc in [self.doc.replace('"reason":', '"wrong":'), self.doc + self.doc,
                    self.doc.replace('"android": 22', '"android": true'),
                    self.doc.replace('"alignment_trigger":', '"missing_trigger":'),
                    '<!-- release-version-exception -->\n```json\n{}\n```']:
            with self.subTest(doc=doc[:40]), self.assertRaises(ValueError):
                version_exception(doc, 22, 25)

    def test_unreadable_or_target_overridden_versions_fail(self):
        for android, project in [('', self.project), (self.android + '\nversionCode=22\n', self.project),
                                 (self.android, self.project.replace('CURRENT_PROJECT_VERSION: "25"', 'CURRENT_PROJECT_VERSION: "$(OTHER)"')),
                                 (self.android, self.project.replace('targets:\n', '  configs:\n    Release:\n      CURRENT_PROJECT_VERSION: \"26\"\ntargets:\n')),
                                 (self.android, self.project.replace('  WoundMeasurementApp:\n', '  WoundMeasurementApp:\n    CURRENT_PROJECT_VERSION: "26"\n'))]:
            with self.subTest(android=android[:20]), self.assertRaises(ValueError):
                version_pair(android, project)

    def test_profile_mutations_fail_closed(self):
        paths = ['engineering/phase2/test_backend_url_parity.py',
                 'iOS/WoundMeasurementApp/Core/AppSettings.swift', 'Android/app/build.gradle',
                 'docs/admin_operations.md', '.github/workflows/p0-4-audit.yml']
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for path in paths:
                target = root / path; target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / path, target)
            self.assertTrue(backend_profiles(root)[0])
            changes = [
                (paths[1], 'return demoURL', 'return legacyURL'),
                (paths[1], '#elseif WOUND_LITE', '#elseif OTHER'),
                (paths[1], 'static let demoURL', 'static let removedURL'),
                (paths[1], 'https://woundai-backend-demo-z4kgfkob4a-de.a.run.app', 'http://localhost:5000'),
                (paths[1], '#if DEBUG && targetEnvironment(simulator)', '#if DEBUG'),
                (paths[2], 'DEFAULT_BACKEND_URL', 'WRONG_BACKEND_URL'),
                (paths[2], 'http://10.0.2.2:5000', 'http://localhost:5000'),
                (paths[3], '| `ios-medical-testflight` |', '| `unknown-profile` |'),
                (paths[4], 'python -B engineering/phase2/test_backend_url_parity.py', 'echo not-tested'),
            ]
            for path, old, new in changes:
                with self.subTest(path=path, replacement=new):
                    target = root / path; original = target.read_text(encoding='utf-8')
                    self.assertIn(old, original)
                    target.write_text(original.replace(old, new), encoding='utf-8')
                    try: self.assertFalse(backend_profiles(root)[0])
                    finally: target.write_text(original, encoding='utf-8')
            (root / paths[0]).write_text("pass\n", encoding='utf-8')
            self.assertFalse(backend_profiles(root)[0], "zero executed checks must not be green")
            (root / paths[0]).unlink()
            self.assertFalse(backend_profiles(root)[0])


if __name__ == '__main__':
    unittest.main()
