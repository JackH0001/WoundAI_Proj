"""Submission ownership boundaries after Jack's explicit Mac authorization."""
import contextlib
import io
import unittest
from unittest.mock import patch
import owner_guard as guard


class OwnershipTests(unittest.TestCase):
    def check(self, platform, paths, restored=False):
        with patch.object(guard, 'changed_paths', return_value=paths), patch.object(
                guard, 'matches_authority', return_value=restored), contextlib.redirect_stdout(io.StringIO()):
            return guard.check_ownership(platform)

    def test_backend_and_engineering_shared_on_mac_and_windows(self):
        paths = ['Backend/Flask/app.py', 'Backend/Flask/deploy_cloudrun.ps1',
                 'Backend/AccessManager/accounts.py', 'engineering/phase2/test_medical_revision_assets.py']
        for platform in ('mac', 'windows'):
            with self.subTest(platform=platform):
                self.assertEqual(self.check(platform, paths), 0)

    def test_windows_application_and_workflow_still_require_windows(self):
        for path in ('Windows/App/Main.cs', '.github/workflows/android.yml'):
            with self.subTest(path=path):
                self.assertEqual(self.check('mac', [path]), 1)
                self.assertEqual(self.check('windows', [path]), 0)

    def test_ios_source_and_workflow_still_require_mac(self):
        for path in ('iOS/WoundMeasurementApp/UI/ReviewView.swift', '.github/workflows/ios.yml'):
            with self.subTest(path=path):
                self.assertEqual(self.check('windows', [path]), 1)
                self.assertEqual(self.check('linux', [path]), 1)
                self.assertEqual(self.check('mac', [path]), 0)

    def test_android_tools_docs_remain_shared(self):
        for platform in ('mac', 'windows'):
            self.assertEqual(self.check(platform, ['Android/app/build.gradle', 'tools/verify_logic.py', 'docs/PARITY.md']), 0)

    def test_mixed_changes_do_not_hide_forbidden_path(self):
        self.assertEqual(self.check('mac', ['Backend/Flask/app.py', 'Windows/App/Main.cs']), 1)
        self.assertEqual(self.check('windows', ['engineering/phase2/test_app.py', 'iOS/App.swift']), 1)

    def test_authority_restoration_exception_preserved(self):
        self.assertEqual(self.check('windows', ['iOS/App.swift'], restored=True), 0)

    def test_unreadable_git_status_fails_closed(self):
        self.assertEqual(self.check('mac', None), 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
